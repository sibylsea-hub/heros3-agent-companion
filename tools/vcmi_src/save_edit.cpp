// SPDX-License-Identifier: GPL-2.0-or-later
// vcmi_save_edit: load a VCMI 1.7.5 save (.vsgm1), apply edits, write a new save.
//
// Loads the whole file the way VCMI_server does (game state, then server state), makes the
// edits through the same CGameHandler calls the cheat codes use -- but without the
// PlayerCheated pack, so nobody is marked as a cheater -- and saves it again the way
// CGameHandler::save does. The result is a normal save that the official 1.7.5 client lists
// and loads. Same build and run requirements as vcmi_save_reader (see README.md): built from
// the same VCMI tag as the game, run with the working directory set to that VCMI install.
// (First version written by the 战役自动存档 session as tools/vcmi_save_edit; adopted here.)
//
// Usage: vcmi_save_edit <in.vsgm1> <out.vsgm1> [--log <file>] <edits...>
// Edits run in order. Hero edits go to the current hero: the first hero of the first human
// player, until --hero picks another (object id or name). Player edits go to that hero's
// owner unless --player <colour> is given. Names can be VCMI identifiers (titan, core:titan)
// or the in-game (Chinese) names.
//   --hero <id|name>               --player <red|blue|...>
//   --give <creature> <count>      add creatures: grow a stack of the same type, else a free slot
//   --resource <name> <n|+n|-n>    set, or add with a sign (gold wood ore mercury sulfur crystal gems)
//   --artifact <artifact> [slot]   slot: head..misc5 (see README), backpack; default first free slot
//   --exp <n>                      add experience; level-ups pick the first offered skill
//   --levels <n>                   add levels (same level-up rule)
//   --primary <attack|defence|spellpower|knowledge> <n|+n|-n>
//   --skill <secondary skill> <0-3>
//   --spell <spell>                learn a spell (adds a spellbook if needed)
//   --mana <n|+n|-n>
//   --clear-cheated                clear the cheater marks of the player (and of finished campaign chapters)

#include "Global.h"

#include "GameLibrary.h"
#include "GameConstants.h"
#include "StartInfo.h"
#include "VCMIDirs.h"
#include "CPlayerState.h"
#include "CCreatureHandler.h"
#include "CSkillHandler.h"
#include "callback/GameRandomizer.h"
#include "campaign/CampaignState.h"
#include "constants/StringConstants.h"
#include "entities/artifact/CArtHandler.h"
#include "entities/artifact/CArtifact.h"
#include "entities/hero/CHeroHandler.h"
#include "gameState/CGameState.h"
#include "gameState/GameStatistics.h"
#include "logging/CLogger.h"
#include "mapObjects/CGHeroInstance.h"
#include "modding/IdentifierStorage.h"
#include "modding/ModScope.h"
#include "networkPacks/PacksForClient.h"
#include "serializer/CLoadFile.h"
#include "serializer/CSaveFile.h"
#include "spells/CSpellHandler.h"
#include "server/CGameHandler.h"
#include "server/IGameServer.h"
#include "server/TurnTimerHandler.h"
#include "server/battles/BattleProcessor.h"
#include "server/processors/HeroPoolProcessor.h"
#include "server/processors/PlayerMessageProcessor.h"
#include "server/processors/TurnOrderProcessor.h"
#include "server/queries/MapQueries.h"
#include "server/queries/QueriesProcessor.h"

#include <windows.h>

VCMI_LIB_USING_NAMESPACE

namespace
{

/// No clients: packs only go to the game state, as CVCMIServer::applyPack does after sending.
class OfflineServer final : public IGameServer
{
public:
	CGameHandler * gh = nullptr;

	void setState(EServerState) override {}
	EServerState getState() const override { return EServerState::GAMEPLAY; }
	bool isPlayerHost(const PlayerColor &) const override { return false; }
	bool hasPlayerAt(PlayerColor, GameConnectionID) const override { return false; }
	bool hasBothPlayersAtSameConnection(PlayerColor, PlayerColor) const override { return false; }
	void applyPack(CPackForClient & pack) override { gh->gs->apply(pack); }
	void sendPack(CPackForClient &, GameConnectionID) override {}
};

std::string narrow(const std::wstring & s)
{
	if(s.empty())
		return {};
	int n = WideCharToMultiByte(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0, nullptr, nullptr);
	std::string out(n, '\0');
	WideCharToMultiByte(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), out.data(), n, nullptr, nullptr);
	return out;
}

/// "+5" / "-5" add, "5" sets
struct Amount
{
	int64_t value = 0;
	bool relative = false;

	static Amount parse(const std::string & s)
	{
		Amount a;
		a.relative = !s.empty() && (s[0] == '+' || s[0] == '-');
		a.value = std::stoll(s);
		return a;
	}
	int64_t applyTo(int64_t current) const { return relative ? current + value : value; }
};

/// VCMI identifier (case-insensitive, any mod scope) or the translated in-game name.
template<typename Objects, typename NameFn>
int32_t resolve(const std::string & type, const std::string & text, const Objects & objects, NameFn names)
{
	if(auto id = LIBRARY->identifiers()->getIdentifierCaseInsensitive(ModScope::scopeGame(), type, text, true))
		return *id;
	for(const auto & object : objects)
		if(object)
			for(const auto & name : names(*object))
				if(name == text)
					return object->getIndex();
	throw std::runtime_error("unknown " + type + ": " + text);
}

CreatureID findCreature(const std::string & text)
{
	return CreatureID(resolve("creature", text, LIBRARY->creh->objects, [](const CCreature & c)
		{ return std::vector<std::string>{c.getNameSingularTranslated(), c.getNamePluralTranslated()}; }));
}

ArtifactID findArtifact(const std::string & text)
{
	return ArtifactID(resolve("artifact", text, LIBRARY->arth->objects, [](const CArtifact & a)
		{ return std::vector<std::string>{a.getNameTranslated()}; }));
}

SpellID findSpell(const std::string & text)
{
	return SpellID(resolve("spell", text, LIBRARY->spellh->objects, [](const CSpell & s)
		{ return std::vector<std::string>{s.getNameTranslated()}; }));
}

SecondarySkill findSkill(const std::string & text)
{
	return SecondarySkill(resolve("secondarySkill", text, LIBRARY->skillh->objects, [](const CSkill & s)
		{ return std::vector<std::string>{s.getNameTranslated()}; }));
}

GameResID findResource(const std::string & text)
{
	for(int r = 0; r < GameResID::COUNT; ++r)
		if(GameResID::encode(r) == text)
			return GameResID(r);
	throw std::runtime_error("unknown resource: " + text);
}

PrimarySkill findPrimary(const std::string & text)
{
	for(const auto & skill : PrimarySkill::ALL_SKILLS())
		if(PrimarySkill::encode(skill.getNum()) == text)
			return skill;
	if(text == "defense")
		return PrimarySkill::DEFENSE;
	throw std::runtime_error("unknown primary skill: " + text + " (attack, defence, spellpower, knowledge)");
}

ArtifactPosition findSlot(const std::string & text)
{
	if(text.empty())
		return ArtifactPosition::FIRST_AVAILABLE;
	if(text == "backpack")
		return ArtifactPosition::BACKPACK_START;
	for(size_t i = 0; i < NArtifactPosition::namesHero.size(); ++i)
		if(text == NArtifactPosition::namesHero[i])
			return ArtifactPosition(static_cast<int32_t>(i));
	throw std::runtime_error("unknown artifact slot: " + text);
}

CGHeroInstance * findHero(CGameState & gs, const std::string & idOrName)
{
	for(auto & [color, player] : gs.players)
	{
		if(idOrName.empty() && !player.human)
			continue;
		for(auto * h : player.getHeroes())
			if(idOrName.empty() || std::to_string(h->id.getNum()) == idOrName || h->getNameTranslated() == idOrName)
				return h;
	}
	throw std::runtime_error(idOrName.empty() ? "no hero of a human player in this save" : "no hero " + idOrName);
}

/// Level-up dialogs are server queries that wait for the player; answer them with the first offer.
/// The dialog applies HeroLevelUp (the actual level increase) only once the player's UI has
/// reported ready, which a client does with AdvInterfaceReady; without that every answer
/// would roll another level-up at the same level.
void answerLevelUps(CGameHandler & gh, const CGHeroInstance & hero)
{
	while(auto query = gh.queries->topQuery(hero.getOwner()))
	{
		auto levelUp = std::dynamic_pointer_cast<CHeroLevelUpDialogQuery>(query);
		if(!levelUp)
			throw std::runtime_error("unexpected pending query after giving experience");
		gh.queryReply(query->queryID, 0, hero.getOwner());
	}
}

class Editor
{
	CGameHandler & gh;
	CGameState & gs;
	CGHeroInstance * hero = nullptr;
	std::optional<PlayerColor> player;

	CGHeroInstance & currentHero()
	{
		if(!hero)
			hero = findHero(gs, "");
		return *hero;
	}
	PlayerColor currentPlayer()
	{
		return player ? *player : currentHero().getOwner();
	}
	std::string heroLabel() { return currentHero().getNameTranslated() + " (#" + std::to_string(currentHero().id.getNum()) + ")"; }

public:
	Editor(CGameHandler & gh) : gh(gh), gs(*gh.gs) {}

	void selectHero(const std::string & idOrName)
	{
		hero = findHero(gs, idOrName);
		std::cout << "hero " << heroLabel() << ", owner " << hero->getOwner().toString() << "\n";
	}

	void selectPlayer(const std::string & color)
	{
		for(auto & [c, ps] : gs.players)
			if(c.toString() == color)
				player = c;
		if(!player)
			throw std::runtime_error("no player " + color + " in this save");
	}

	void give(const std::string & creatureName, int count)
	{
		CGHeroInstance & h = currentHero();
		const CreatureID creature = findCreature(creatureName);
		const SlotID slot = h.getSlotFor(creature);
		if(!slot.validSlot())
			throw std::runtime_error("no free slot for " + creatureName + " in the army of " + heroLabel());

		if(h.hasStackAtSlot(slot))
		{
			ChangeStackCount pack;
			pack.army = h.id;
			pack.slot = slot;
			pack.count = count;
			pack.mode = ChangeValueMode::RELATIVE;
			gh.sendAndApply(pack);
		}
		else
		{
			InsertNewStack pack;
			pack.army = h.id;
			pack.slot = slot;
			pack.type = creature;
			pack.count = count;
			gh.sendAndApply(pack);
		}
		std::cout << "  +" << count << " " << creature.toCreature()->getNamePluralTranslated() << " -> " << heroLabel()
			<< " slot " << slot.getNum() << " (now " << h.getStackCount(slot) << ")\n";
	}

	void resource(const std::string & name, const Amount & amount)
	{
		const GameResID res = findResource(name);
		const PlayerColor color = currentPlayer();
		const int64_t current = gs.players.at(color).resources[res];
		ResourceSet delta;
		delta[res] = static_cast<TResource>(amount.applyTo(current) - current);
		gh.giveResources(color, delta);
		std::cout << "  " << color.toString() << " " << name << ": " << current << " -> " << gs.players.at(color).resources[res] << "\n";
	}

	void artifact(const std::string & name, const std::string & slotName)
	{
		const ArtifactID art = findArtifact(name);
		if(!gh.giveHeroNewArtifact(&currentHero(), art, findSlot(slotName)))
			throw std::runtime_error("could not put " + name + " on " + heroLabel() + (slotName.empty() ? "" : " at " + slotName));
		std::cout << "  " << art.toArtifact()->getNameTranslated() << " -> " << heroLabel() << "\n";
	}

	void experience(int64_t amount)
	{
		CGHeroInstance & h = currentHero();
		const auto levelBefore = h.level;
		gh.uiReadyForDialogs.insert(h.getOwner()); // see answerLevelUps
		gh.giveExperience(&h, amount);
		answerLevelUps(gh, h);
		std::cout << "  " << heroLabel() << " experience +" << amount << ": level " << levelBefore << " -> " << h.level
			<< " (exp " << h.exp << ")\n";
	}

	void levels(int count)
	{
		CGHeroInstance & h = currentHero();
		experience(LIBRARY->heroh->reqExp(h.level + count) - h.exp);
	}

	void primary(const std::string & name, const Amount & amount)
	{
		CGHeroInstance & h = currentHero();
		const PrimarySkill skill = findPrimary(name);
		const int before = h.getBasePrimarySkillValue(skill);
		gh.changePrimSkill(&h, skill, amount.applyTo(before), ChangeValueMode::ABSOLUTE);
		std::cout << "  " << heroLabel() << " " << name << ": " << before << " -> " << h.getBasePrimarySkillValue(skill) << "\n";
	}

	void skill(const std::string & name, int level)
	{
		CGHeroInstance & h = currentHero();
		const SecondarySkill skill = findSkill(name);
		gh.changeSecSkill(&h, skill, level, ChangeValueMode::ABSOLUTE);
		std::cout << "  " << heroLabel() << " " << skill.toEntity(LIBRARY)->getNameTranslated() << " -> " << int(h.getSecSkillLevel(skill)) << "\n";
	}

	void spell(const std::string & name)
	{
		CGHeroInstance & h = currentHero();
		const SpellID spell = findSpell(name);
		if(!h.hasSpellbook())
			gh.giveHeroNewArtifact(&h, ArtifactID::SPELLBOOK, ArtifactPosition::SPELLBOOK);
		gh.changeSpells(&h, true, {spell});
		std::cout << "  " << heroLabel() << " learns " << spell.toEntity(LIBRARY)->getNameTranslated() << "\n";
	}

	void mana(const Amount & amount)
	{
		CGHeroInstance & h = currentHero();
		const int before = h.mana;
		SetMana pack(h.id, static_cast<si32>(amount.applyTo(before)), ChangeValueMode::ABSOLUTE);
		gh.sendAndApply(pack);
		std::cout << "  " << heroLabel() << " mana: " << before << " -> " << h.mana << "\n";
	}

	void clearCheated()
	{
		const PlayerColor color = currentPlayer();
		PlayerState & ps = gs.players.at(color);
		std::cout << "  " << color.toString() << " cheated: " << ps.cheated << " -> 0\n";
		ps.cheated = false;
		ps.enteredWinningCheatCode = false;
		ps.enteredLosingCheatCode = false;
		// finished campaign chapters keep their own high-score record
		for(const StartInfo * si : std::array<const StartInfo *, 2>{gs.getStartInfo(), gs.getInitialStartInfo()})
		{
			if(!si || !si->campState)
				continue;
			for(auto & param : si->campState->highscoreParameters)
			{
				if(param.usedCheat)
					std::cout << "  campaign chapter \"" << param.scenarioName << "\": cleared cheater mark\n";
				param.usedCheat = false;
			}
		}
	}
};

void usage()
{
	std::cerr << "usage: vcmi_save_edit <in.vsgm1> <out.vsgm1> [--log <file>] <edits...>\n"
				 "  --hero <id|name>  --player <colour>  --give <creature> <count>  --resource <name> <n|+n>\n"
				 "  --artifact <artifact> [slot|backpack]  --exp <n>  --levels <n>  --primary <skill> <n|+n>\n"
				 "  --skill <skill> <0-3>  --spell <spell>  --mana <n|+n>  --clear-cheated\n";
}

} // namespace

int wmain(int argc, wchar_t * argv[])
{
	std::vector<std::string> args;
	boost::filesystem::path inPath;
	boost::filesystem::path outPath;
	boost::filesystem::path logPath;
	for(int i = 1; i < argc; ++i)
	{
		std::wstring arg = argv[i];
		if(arg == L"--log" && i + 1 < argc)
			logPath = argv[++i];
		else if(inPath.empty())
			inPath = arg;
		else if(outPath.empty() && arg.rfind(L"--", 0) != 0)
			outPath = arg;
		else
			args.push_back(narrow(arg));
	}
	if(inPath.empty() || outPath.empty() || args.empty())
	{
		usage();
		return 2;
	}
	if(boost::filesystem::exists(outPath) && boost::filesystem::equivalent(inPath, outPath))
	{
		std::cerr << "refusing to overwrite the input save\n";
		return 2;
	}

	if(logPath.empty())
		logPath = VCMIDirs::get().userLogsPath() / "VCMI_SaveEdit_log.txt";
	CLogger::getGlobalLogger()->addTarget(std::make_unique<CLogFileTarget>(logPath, false));
	CLogger::getGlobalLogger()->setLevel(ELogLevel::INFO);
	logGlobal->info("Starting %s save editor", GameConstants::VCMI_VERSION);

	try
	{
		LIBRARY = new GameLibrary;
		LIBRARY->initializeFilesystem(false);
		LIBRARY->initializeLibrary();

		// CGameHandler::load without the filesystem lookup and StartInfo refresh
		OfflineServer server;
		CGameHandler gh(server);
		server.gh = &gh;
		gh.gs = std::make_shared<CGameState>();
		gh.randomizer = std::make_unique<GameRandomizer>(*gh.gs);
		{
			CLoadFile lf(inPath, gh.gs.get());
			gh.gs->loadGame(lf);
			lf.load(gh);
		}
		gh.gs->preInit(LIBRARY);

		Editor editor(gh);
		auto next = [&](size_t & i) -> const std::string &
		{
			if(i + 1 >= args.size())
				throw std::runtime_error("missing value after " + args[i]);
			return args[++i];
		};
		for(size_t i = 0; i < args.size(); ++i)
		{
			const std::string & a = args[i];
			if(a == "--hero")
				editor.selectHero(next(i));
			else if(a == "--player")
				editor.selectPlayer(next(i));
			else if(a == "--give")
			{
				const std::string creature = next(i);
				editor.give(creature, std::stoi(next(i)));
			}
			else if(a == "--resource")
			{
				const std::string name = next(i);
				editor.resource(name, Amount::parse(next(i)));
			}
			else if(a == "--artifact")
			{
				const std::string name = next(i);
				std::string slot;
				if(i + 1 < args.size() && args[i + 1].rfind("--", 0) != 0)
					slot = args[++i];
				editor.artifact(name, slot);
			}
			else if(a == "--exp")
				editor.experience(std::stoll(next(i)));
			else if(a == "--levels")
				editor.levels(std::stoi(next(i)));
			else if(a == "--primary")
			{
				const std::string name = next(i);
				editor.primary(name, Amount::parse(next(i)));
			}
			else if(a == "--skill")
			{
				const std::string name = next(i);
				editor.skill(name, std::stoi(next(i)));
			}
			else if(a == "--spell")
				editor.spell(next(i));
			else if(a == "--mana")
				editor.mana(Amount::parse(next(i)));
			else if(a == "--clear-cheated")
				editor.clearCheated();
			else
				throw std::runtime_error("unknown option " + a);
		}

		// CGameHandler::save
		CSaveFile save;
		gh.gs->saveGame(save);
		save.save(gh);
		save.write(outPath);
		std::cout << "wrote " << narrow(outPath.wstring()) << "\n";
		logGlobal->info("Done");
	}
	catch(const std::exception & e)
	{
		logGlobal->error("Failed: %s", e.what());
		std::cerr << "error: " << e.what() << "\n(see " << narrow(logPath.wstring()) << ")\n";
		return 1;
	}
	return 0;
}
