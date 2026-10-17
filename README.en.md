# Play Heroes of Might and Magic III with Claude

[中文（正本）](README.md)

A patch and toolkit for [VCMI](https://vcmi.eu/), the open-source Heroes of Might and Magic III engine, that lets an AI agent (we use Claude) join your game: it chats with you in-game, follows your turns live, plays a color in hotseat as your ally or rival, changes the game on request without flagging you as a cheater, and writes complete maps with their own story events.

Built by Sibyl and Claude for our own games and shared as-is. It works for us every day, but it is young and rough in places. The Chinese README is the primary version; the in-game UI that the patch adds is currently in Chinese only.

<!-- screenshot: the in-game Claude window -->

## Features

**In-game chat.** A "Claude" tab on the adventure map opens a chat window. Unread replies blink on the tab and scroll through the chat bar; important messages can pop up as dialogs. Text typed there goes only to the agent, never to the game's cheat parser. The normal Tab chat is forwarded too.

**Live turn reports.** VCMI autosaves at the start of every turn. A watcher reads each new save and reports what changed: where heroes moved, what they picked up, which story objects fired, battle results, level-ups, new buildings. The patched client also reports visits, event texts and battles as they happen. Reports only cover what the player has seen, so the agent doesn't spoil the map. Everything is logged locally, so a fresh agent session can catch up on the whole game.

**The agent as a local player.** In the lobby, hand any computer color to Claude (or name a hotseat seat `Claude`). On its turn, the agent gets a text briefing and a numbered list of legal actions, and sends commands such as `move H1 12 14`, `build T1 marketplace`, `recruit T1 pixie max`, `hire T1 1`, `end`. Each command is validated, and failures come back with a reason. Pathfinding and combat use VCMI's own pathfinder and auto-battle. If the agent goes quiet for three minutes, the Nullkiller AI finishes the turn.

**Host commands that don't count as cheating.** While the game runs, the agent can add creatures, resources, artifacts, experience, skills, spells, mana, movement, buildings and recruits; reveal the map; or teleport a hero. It uses the same server calls as the cheat codes but never sets the cheater flag. A separate save editor does the same offline, and can clear an existing cheater flag.

**Map authoring toolkit** (pure Python 3.12, standard library only):
- Lossless reading and writing of `.h3m` maps (RoE/AB/SoD) and `.h3c` campaigns.
- ASCII-art layout compiled into terrain, transitions, roads, rivers and obstacles; terrain transitions match the original editor on 99.9% of tiles.
- A high-level placement API for towns, heroes, monsters, artifacts, signs, events, Pandora's boxes, seer huts, quest guards, timed events, rumors and victory/loss conditions, with overlap and reachability checks.
- Full-map PNG rendering with the original sprites, plus an automated VCMI load test.
- Output is a standard SoD `.h3m`: it plays in stock VCMI (mobile included) and opens in the original map editor.

**Small extras.** Swap how any two player colors are displayed (UI, flags, minimap, color names; display only). Pick any color in a new game, including ones the map marks computer-only.

**Example map.** [`examples/love_letter.py`](examples/love_letter.py) builds a 36×36 map with a start sign, a hidden event, a seer hut quest, a Pandora's box gift and a timed event, as a template for agents writing personal maps.

## Digging deeper into VCMI
VCMI can do much more without recompiling: mods can add artifacts, heroes, creatures, spells and town buildings, replace menus and music, and define new configurable map objects. [`AGENTS.md`](AGENTS.md) gives agents a map of where to look in the VCMI source and docs, and what each hook makes possible. Point your agent at it and let it explore.

## Platforms

| Platform | Status |
|---|---|
| Windows | Works, all features |
| macOS | In progress |
| iPhone / iPad | We currently stream the PC game to the iPad. The patch already includes the iOS-specific changes for a native build, but the native build hasn't been compiled yet |
| Android | Stock VCMI plays the maps; no agent features |

## Requirements

1. **A legal copy of Heroes III Complete.** VCMI is only an engine and needs the original game data.
   - **Steam**: [Heroes Of Might And Magic III](https://store.steampowered.com/app/4921760/Heroes_Of_Might_And_Magic_III/) (released August 2026, includes Armageddon's Blade and The Shadow of Death).
   - **Don't buy the 2015 "HD Edition"**: it contains only Restoration of Erathia, and VCMI cannot run on it alone.
   - **GOG**: Heroes of Might and Magic 3: Complete, the copy VCMI's site recommends.
   - **Ubisoft Store**: also sells Complete.
2. **An agent that can run commands on your PC.** We use [Claude Code](https://claude.com/claude-code) (a paid Claude plan). The tools are plain command-line programs that read and write local files, so other agents that read `AGENTS.md` should work too, but only Claude has been tested.
3. **Windows with Visual Studio 2022**, to build the patched client (about 15 minutes the first time). A prebuilt Windows package is planned.

## Install
1. Install the game and [official VCMI](https://vcmi.eu/download/), and import the game data as VCMI's instructions describe.
2. Clone this repository, open it in Claude Code and say "set this up following AGENTS.md". The agent writes your paths into `h3mod.local.json` (see [`h3mod.example.json`](h3mod.example.json)) and builds the client following [`tools/vcmi_src/README.md`](tools/vcmi_src/README.md).
3. Start the game with `tools/vcmi_play.bat` and tell the agent to start following your game.

Everything stays on your machine. The optional relay for iPad/iPhone listens only on your home network and requires a token.

## Repository layout

| Path | Contents |
|---|---|
| `AGENTS.md` | Instructions for agents: setup, live play, map writing, where to explore |
| `tools/vcmi_src/` | VCMI 1.7.5 patch and the save reader/editor (GPL) |
| `tools/vcmi_*.py` | Save reader, live watcher, chat bridge, agent-as-player, relay |
| `tools/h3tools/` | Map/campaign I/O, layout compiler, placement API, renderer |
| `examples/` | Example map |
| `docs/authoring.md` | Map-writing workflow (Chinese) |

## License
- This mod: [MIT](LICENSE).
- `tools/vcmi_src/` is a modification of VCMI and, like VCMI, is licensed [GPL-2.0-or-later](tools/vcmi_src/COPYING).

No original game files are included. Heroes of Might and Magic is a trademark of Ubisoft. This project is not affiliated with Ubisoft or the VCMI team.

## Credits
- **[VCMI](https://github.com/vcmi/vcmi)**: the open-source engine this is built on (version 1.7.5). Site: [vcmi.eu](https://vcmi.eu/).
- **VCMI Chinese translation mod** by YueR.
- **[vcmi-llm-opponent](https://github.com/SergiiMytakii/vcmi-llm-opponent)**: an earlier LLM opponent for VCMI that informed our agent-as-player design.

Keywords: Heroes of Might and Magic III, HoMM3, Heroes 3, H3, VCMI, Shadow of Death, mod, map editor, h3m, AI companion, LLM agent, Claude, Claude Code
