// SPDX-License-Identifier: GPL-2.0-or-later
// vcmi_save_reader: load a VCMI 1.7.5 save (.vsgm1) and print the game state as JSON.
//
// Must be built from the same VCMI tag as the game that wrote the save (the
// binary format changes between versions). Run with the working directory set
// to a VCMI install (its config/ and Mods/vcmi are the game data) and a
// config/dirs.json next to this exe that points at the user data. See README.md.
//
// Usage: vcmi_save_reader <save.vsgm1> [-o out.json] [--full-text] [--log <file>]

#include "Global.h"

#include "GameLibrary.h"
#include "GameConstants.h"
#include "StartInfo.h"
#include "VCMIDirs.h"
#include "CPlayerState.h"
#include "callback/EditorCallback.h"
#include "constants/StringConstants.h"
#include "campaign/CampaignState.h"
#include "entities/artifact/CArtifact.h"
#include "entities/artifact/CArtifactInstance.h"
#include "entities/building/CBuilding.h"
#include "entities/faction/CTown.h"
#include "entities/hero/CHero.h"
#include "entities/hero/CHeroClass.h"
#include "gameState/QuestInfo.h"
#include "gameState/CGameState.h"
#include "json/JsonNode.h"
#include "logging/CLogger.h"
#include "mapObjects/CGCreature.h"
#include "mapObjects/CGHeroInstance.h"
#include "mapObjects/CGPandoraBox.h"
#include "mapObjects/CGTownInstance.h"
#include "mapObjects/CQuest.h"
#include "mapObjects/MiscObjects.h"
#include "mapObjects/army/CStackInstance.h"
#include "mapping/CMap.h"
#include "mapping/CMapService.h"
#include "serializer/CLoadFile.h"
#include "CCreatureHandler.h"
#include "spells/CSpellHandler.h"
#include "CSkillHandler.h"

#include <vcmi/Creature.h>
#include <vcmi/Faction.h>
#include <vcmi/HeroType.h>
#include <vcmi/Skill.h>
#include <vcmi/spells/Spell.h>

#include <windows.h>
#include <fcntl.h>
#include <io.h>

VCMI_LIB_USING_NAMESPACE

namespace
{

bool fullText = false;

JsonNode posNode(const int3 & p)
{
	JsonNode n;
	n.Vector().emplace_back(static_cast<int32_t>(p.x));
	n.Vector().emplace_back(static_cast<int32_t>(p.y));
	n.Vector().emplace_back(static_cast<int32_t>(p.z));
	return n;
}

JsonNode textNode(const std::string & text)
{
	if(fullText || text.size() <= 180)
		return JsonNode(text);
	// cut on a UTF-8 character boundary
	size_t cut = 180;
	while(cut > 0 && (static_cast<unsigned char>(text[cut]) & 0xC0) == 0x80)
		--cut;
	return JsonNode(text.substr(0, cut) + "…");
}

JsonNode stringList(const std::vector<std::string> & items)
{
	JsonNode n;
	n.Vector(); // make it an empty list rather than null
	for(const auto & s : items)
		n.Vector().emplace_back(s);
	return n;
}

std::string statusName(EPlayerStatus s)
{
	switch(s)
	{
	case EPlayerStatus::INGAME: return "ingame";
	case EPlayerStatus::LOSER: return "lost";
	case EPlayerStatus::WINNER: return "won";
	default: return "unknown";
	}
}

JsonNode armyNode(const CCreatureSet & army)
{
	JsonNode n;
	n.Vector();
	for(const auto & [slot, stack] : army.Slots())
	{
		JsonNode s;
		s["slot"].Integer() = slot.getNum();
		const CCreature * c = stack->getCreature();
		s["creature"].String() = c ? c->getJsonKey() : "?";
		s["name"].String() = c ? c->getNamePluralTranslated() : "?";
		s["count"].Integer() = stack->getCount();
		n.Vector().push_back(s);
	}
	return n;
}

JsonNode artifactNode(const CArtifactInstance * art)
{
	JsonNode a;
	a["artifact"].String() = art->getType()->getJsonKey();
	a["name"].String() = art->getType()->getNameTranslated();
	if(art->getScrollSpellID().hasValue())
		a["spell"].String() = art->getScrollSpellID().toEntity(LIBRARY)->getJsonKey();
	return a;
}

JsonNode heroNode(const CGHeroInstance * h)
{
	JsonNode n;
	n["id"].Integer() = h->id.getNum();
	n["name"].String() = h->getNameTranslated();
	n["type"].String() = h->getHeroType()->getJsonKey();
	n["class"].String() = h->getHeroClass()->getNameTranslated();
	n["pos"] = posNode(h->visitablePos());
	n["level"].Integer() = h->level;
	n["experience"].Integer() = h->exp;
	n["mana"].Integer() = h->mana;
	n["movement"].Integer() = h->movementPointsRemaining();
	if(h->getVisitedTown())
		n["in_town"].String() = h->getVisitedTown()->getNameTranslated();
	n["garrisoned"].Bool() = h->isGarrisoned();

	for(const auto & skill : PrimarySkill::ALL_SKILLS())
	{
		const auto key = PrimarySkill::encode(skill.getNum());
		n["primary"][key].Integer() = h->getPrimSkillLevel(skill);
		n["primary_base"][key].Integer() = h->getBasePrimarySkillValue(skill);
	}

	n["secondary"].Vector();
	for(const auto & [skill, level] : h->secSkills)
	{
		if(!skill.hasValue())
			continue;
		JsonNode s;
		s["skill"].String() = skill.toEntity(LIBRARY)->getJsonKey();
		s["name"].String() = skill.toEntity(LIBRARY)->getNameTranslated();
		s["level"].Integer() = level;
		n["secondary"].Vector().push_back(s);
	}

	n["army"] = armyNode(*h);

	n["artifacts"].Struct();
	for(const auto & [slot, info] : h->artifactsWorn)
	{
		if(info.locked || !info.getArt())
			continue;
		// ArtifactPosition::encode() indexes its own slot list, not ArtifactPosition values
		const int index = slot.getNum();
		const std::string name = index >= 0 && index < static_cast<int>(NArtifactPosition::namesHero.size())
			? NArtifactPosition::namesHero[index] : std::to_string(index);
		n["artifacts"][name] = artifactNode(info.getArt());
	}
	n["backpack"].Vector();
	for(const auto & info : h->artifactsInBackpack)
		if(info.getArt())
			n["backpack"].Vector().push_back(artifactNode(info.getArt()));

	std::vector<std::string> spells;
	for(const auto & spell : h->getSpellsInSpellbook())
		spells.push_back(spell.toEntity(LIBRARY)->getJsonKey());
	n["spells"] = stringList(spells);
	return n;
}

JsonNode townNode(const CGTownInstance * t)
{
	JsonNode n;
	n["id"].Integer() = t->id.getNum();
	n["name"].String() = t->getNameTranslated();
	n["faction"].String() = t->getFactionID().toEntity(LIBRARY)->getJsonKey();
	n["pos"] = posNode(t->visitablePos());
	n["buildings"].Vector();
	for(const auto & bid : t->getBuildings())
	{
		JsonNode b;
		const auto & buildings = t->getTown()->buildings;
		auto it = buildings.find(bid);
		if(it != buildings.end())
		{
			b["building"].String() = it->second->getJsonKey();
			b["name"].String() = it->second->getNameTranslated();
		}
		else
			b["building"].String() = std::to_string(bid.getNum());
		n["buildings"].Vector().push_back(b);
	}
	n["garrison"] = armyNode(*t);
	if(t->getGarrisonHero())
		n["garrison_hero"].String() = t->getGarrisonHero()->getNameTranslated();
	if(t->getVisitingHero())
		n["visiting_hero"].String() = t->getVisitingHero()->getNameTranslated();
	return n;
}

/// Short type tag for objects that carry story: messages, quests, scripted rewards.
std::string storyKind(MapObjectID id)
{
	switch(id.toEnum())
	{
	case Obj::EVENT: return "event";
	case Obj::SEER_HUT: return "seer_hut";
	case Obj::QUEST_GUARD: return "quest_guard";
	case Obj::SIGN: return "sign";
	case Obj::OCEAN_BOTTLE: return "bottle";
	case Obj::PANDORAS_BOX: return "pandora";
	case Obj::BORDERGUARD: return "border_guard";
	case Obj::BORDER_GATE: return "border_gate";
	case Obj::KEYMASTER: return "keymaster";
	case Obj::ARTIFACT: return "artifact";
	case Obj::PRISON: return "prison";
	case Obj::GRAIL: return "grail";
	default: return "";
	}
}

/// Story text carried by an object (as of the given instance).
std::string storyText(const CGObjectInstance * obj)
{
	if(const auto * box = dynamic_cast<const CGPandoraBox *>(obj))
		return box->message.toString();
	if(const auto * sign = dynamic_cast<const CGSignBottle *>(obj))
		return sign->message.toString();
	if(const auto * art = dynamic_cast<const CGArtifact *>(obj))
		return art->message.toString();
	if(const auto * monster = dynamic_cast<const CGCreature *>(obj))
		return monster->message.toString();
	return {};
}

bool isStoryObject(const CGObjectInstance * obj)
{
	if(!storyKind(obj->ID).empty())
		return true;
	// wandering monsters only count when the map author gave them a message
	if(obj->ID == Obj::MONSTER)
		return !storyText(obj).empty();
	return false;
}

JsonNode questNode(const CQuest & q)
{
	JsonNode n;
	n["completed"].Bool() = q.isCompleted;
	std::vector<std::string> active;
	for(const auto & c : q.activeForPlayers)
		active.push_back(c.toString());
	n["active_for"] = stringList(active);
	if(q.lastDay >= 0)
		n["last_day"].Integer() = q.lastDay + 1;
	if(!q.firstVisitText.empty())
		n["first_visit_text"] = textNode(q.firstVisitText.toString());
	if(!q.completedText.empty())
		n["completed_text"] = textNode(q.completedText.toString());
	return n;
}

std::unique_ptr<CMap> loadOriginalMap(const CGameState & gs, IGameInfoCallback * cb)
{
	const StartInfo * si = gs.getStartInfo();
	if(si->campState)
	{
		auto scenario = si->campState->currentScenario();
		if(scenario)
			return si->campState->getMap(*scenario, cb);
		return nullptr;
	}
	if(si->mapname.empty())
		return nullptr; // random map
	CMapService service;
	return service.loadMap(ResourcePath(si->mapname, EResType::MAP), cb);
}

JsonNode objectSummary(const CGObjectInstance * obj)
{
	JsonNode n;
	n["id"].Integer() = obj->id.getNum();
	std::string kind = storyKind(obj->ID);
	n["type"].String() = kind.empty() ? obj->getTypeName() : kind;
	n["subtype"].String() = obj->getSubtypeName();
	n["name"].String() = obj->getObjectName();
	n["pos"] = posNode(obj->visitablePos());
	if(!obj->instanceName.empty())
		n["instance"].String() = obj->instanceName;
	return n;
}

JsonNode buildReport(CGameState & gs, IGameInfoCallback * mapCb)
{
	const CMap & map = gs.getMap();
	const StartInfo * si = gs.getStartInfo();
	JsonNode out;

	out["vcmi_version"].String() = GameConstants::VCMI_VERSION;
	out["map"]["name"].String() = map.name.toString();
	out["map"]["file"].String() = si->mapname;
	out["map"]["size"].Vector().emplace_back(static_cast<int32_t>(map.width));
	out["map"]["size"].Vector().emplace_back(static_cast<int32_t>(map.height));
	out["map"]["size"].Vector().emplace_back(static_cast<int32_t>(map.levels()));
	// Which game this is: the server stamps the start time when a game or campaign chapter
	// starts, and every save of that game keeps it (autosave folders are named after it too).
	// Local time, the same format as the claude-bridge "ts" fields.
	const StartInfo * initial = gs.getInitialStartInfo() ? gs.getInitialStartInfo() : si;
	out["game"]["started"].String() = vstd::getFormattedDateTime(initial->startTime, "%Y-%m-%dT%H:%M:%S");
	if(si->campState)
	{
		out["map"]["campaign"].String() = si->campState->getNameTranslated();
		if(auto scenario = si->campState->currentScenario())
			out["map"]["scenario"].Integer() = scenario->getNum();
		// high-score records of finished chapters, each with its own cheater mark
		out["map"]["finished_chapters"].Vector();
		for(const auto & param : si->campState->highscoreParameters)
		{
			JsonNode c;
			c["name"].String() = param.scenarioName;
			c["day"].Integer() = param.day;
			c["cheated"].Bool() = param.usedCheat;
			out["map"]["finished_chapters"].Vector().push_back(c);
		}
	}

	out["day"].Integer() = gs.getDate(Date::DAY);
	out["month"].Integer() = gs.getDate(Date::MONTH);
	out["week"].Integer() = gs.getDate(Date::WEEK);
	out["day_of_week"].Integer() = gs.getDate(Date::DAY_OF_WEEK);
	std::vector<std::string> acting;
	for(const auto & c : gs.actingPlayers)
		acting.push_back(c.toString());
	out["acting_players"] = stringList(acting);

	out["players"].Vector();
	for(const auto & [color, ps] : gs.players)
	{
		JsonNode p;
		p["color"].String() = color.toString();
		p["human"].Bool() = ps.human;
		p["status"].String() = statusName(ps.status);
		p["cheated"].Bool() = ps.cheated; // shown as 作弊者 with 0 points on the high-score screen
		p["team"].Integer() = ps.team.getNum();
		for(int r = 0; r < GameResID::COUNT; ++r)
			p["resources"][GameResID::encode(r)].Integer() = ps.resources[r];
		if(ps.daysWithoutCastle)
			p["days_without_town"].Integer() = *ps.daysWithoutCastle;

		p["heroes"].Vector();
		for(const auto * h : ps.getHeroes())
			p["heroes"].Vector().push_back(heroNode(h));
		p["towns"].Vector();
		for(const auto * t : ps.getTowns())
			p["towns"].Vector().push_back(townNode(t));

		p["visited_objects"].Vector();
		for(const auto & id : ps.visitedObjects)
			p["visited_objects"].Vector().emplace_back(id.getNum());
		p["destroyed_objects"].Vector();
		for(const auto & id : ps.destroyedObjects)
			p["destroyed_objects"].Vector().emplace_back(id.getNum());
		p["quest_log"].Vector();
		for(const auto & q : ps.quests)
			p["quest_log"].Vector().emplace_back(q.obj.getNum());
		out["players"].Vector().push_back(p);
	}

	// Story objects: compare the scenario's original map with the current one.
	// In-game removal (RemoveObject) leaves a null slot, so ids stay stable.
	auto original = loadOriginalMap(gs, mapCb);
	out["story_objects"].Vector();
	out["removed_objects"].Vector();
	auto currentAt = [&](ObjectInstanceID id) -> const CGObjectInstance *
	{
		if(id.getNum() < 0 || id.getNum() >= static_cast<int>(map.objects.size()))
			return nullptr;
		return map.objects[id.getNum()].get();
	};
	auto addVisitors = [&](JsonNode & n, const CGObjectInstance * obj)
	{
		std::vector<std::string> visitors;
		for(const auto & [color, ps] : gs.players)
			if(ps.visitedObjects.count(obj->id) || ps.destroyedObjects.count(obj->id) || obj->wasVisited(color))
				visitors.push_back(color.toString());
		n["visited_by"] = stringList(visitors);
	};

	if(original)
	{
		out["original_objects_loaded"].Bool() = true;
		for(const auto & origPtr : original->objects)
		{
			if(!origPtr)
				continue;
			const CGObjectInstance * now = currentAt(origPtr->id);
			bool present = now && now->ID == origPtr->ID;
			const CGObjectInstance * obj = present ? now : origPtr.get();

			if(!present && origPtr->ID != Obj::HERO && origPtr->ID != Obj::RANDOM_HERO && origPtr->ID != Obj::HERO_PLACEHOLDER)
			{
				JsonNode r = objectSummary(origPtr.get());
				out["removed_objects"].Vector().push_back(r);
			}
			if(!isStoryObject(origPtr.get()))
				continue;

			JsonNode n = objectSummary(obj);
			n["present"].Bool() = present;
			if(present)
				addVisitors(n, now);
			std::string text = storyText(origPtr.get());
			if(!text.empty())
				n["text"] = textNode(text);
			if(const auto * questObj = dynamic_cast<const IQuestObject *>(obj))
				n["quest"] = questNode(questObj->getQuest());
			if(const auto * ev = dynamic_cast<const CGEvent *>(obj))
			{
				n["remove_after_visit"].Bool() = ev->removeAfterVisit;
				std::vector<std::string> avail;
				for(const auto & c : ev->availableFor)
					avail.push_back(c.toString());
				n["available_for"] = stringList(avail);
			}
			out["story_objects"].Vector().push_back(n);
		}
	}
	else
	{
		// Random map or unreadable original: report only what is still on the map.
		out["original_objects_loaded"].Bool() = false;
		for(const auto & ptr : map.objects)
		{
			if(!ptr || !isStoryObject(ptr.get()))
				continue;
			JsonNode n = objectSummary(ptr.get());
			n["present"].Bool() = true;
			addVisitors(n, ptr.get());
			std::string text = storyText(ptr.get());
			if(!text.empty())
				n["text"] = textNode(text);
			if(const auto * questObj = dynamic_cast<const IQuestObject *>(ptr.get()))
				n["quest"] = questNode(questObj->getQuest());
			out["story_objects"].Vector().push_back(n);
		}
	}
	return out;
}

std::string narrow(const std::wstring & w)
{
	if(w.empty())
		return {};
	int size = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
	std::string s(size, '\0');
	WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), s.data(), size, nullptr, nullptr);
	return s;
}

} // namespace

int wmain(int argc, wchar_t * argv[])
{
	boost::filesystem::path savePath;
	boost::filesystem::path outPath;
	boost::filesystem::path logPath;
	for(int i = 1; i < argc; ++i)
	{
		std::wstring arg = argv[i];
		if(arg == L"-o" && i + 1 < argc)
			outPath = argv[++i];
		else if(arg == L"--log" && i + 1 < argc)
			logPath = argv[++i];
		else if(arg == L"--full-text")
			fullText = true;
		else if(savePath.empty())
			savePath = arg;
		else
		{
			std::cerr << "unexpected argument: " << narrow(arg) << "\n";
			return 2;
		}
	}
	if(savePath.empty())
	{
		std::cerr << "usage: vcmi_save_reader <save.vsgm1> [-o out.json] [--full-text] [--log <file>]\n";
		return 2;
	}

	if(logPath.empty())
		logPath = VCMIDirs::get().userLogsPath() / "VCMI_SaveReader_log.txt";
	// File-only logging: stdout is reserved for the JSON.
	CLogger::getGlobalLogger()->addTarget(std::make_unique<CLogFileTarget>(logPath, false));
	CLogger::getGlobalLogger()->setLevel(ELogLevel::INFO);
	logGlobal->info("Starting %s save reader", GameConstants::VCMI_VERSION);
	logGlobal->info("%s", VCMIDirs::get().genHelpString());

	try
	{
		LIBRARY = new GameLibrary;
		LIBRARY->initializeFilesystem(false);
		LIBRARY->initializeLibrary();

		auto gs = std::make_shared<CGameState>();
		{
			CLoadFile lf(savePath, gs.get());
			gs->loadGame(lf);
		}
		gs->preInit(LIBRARY);

		EditorCallback mapCb(nullptr);
		JsonNode report = buildReport(*gs, &mapCb);
		report["save"].String() = narrow(savePath.wstring());
		std::string json = report.toString();

		if(outPath.empty())
		{
			_setmode(_fileno(stdout), _O_BINARY);
			std::cout << json << std::endl;
		}
		else
		{
			std::ofstream f(outPath.wstring(), std::ios::binary);
			f << json;
		}
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
