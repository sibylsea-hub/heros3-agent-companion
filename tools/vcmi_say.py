"""Send something into the player's running game (needs the patched client, see vcmi_play.py).

Usage:
  python -X utf8 tools/vcmi_say.py "前面有埋伏。"                    # chat line from Claude
  python -X utf8 tools/vcmi_say.py --popup "{第二章}\\n\\n……"         # popup window
  python -X utf8 tools/vcmi_say.py --command "vcmiarmy titan 10"     # as if the player typed it
  python -X utf8 tools/vcmi_say.py --open                            # open the player's Claude window
  python -X utf8 tools/vcmi_say.py --gm "hero=孟斐拉 give 金龙 10"     # 主持人命令: change the running game
  options: --from <name> (sender shown in chat, default Claude)

Appends one JSON line to <vcmi_data>/claude-bridge/to-game.jsonl ($VCMI_CLAUDE_BRIDGE overrides
the folder; see tools/h3tools/config.py); the client picks it up
within a second (an iPad/iPhone through tools/vcmi_relay.py) and shows it once a game is
running: chat lines go to the Claude window, with an unread badge on the adventure map's
bottom-left gem, and briefly to the in-game console. Note: --command goes through the game's
chat, so cheat codes mark the player as a cheater (作弊者 on the high-score screen); to change
their army or resources without that, use --gm (below), or edit a save with tools/vcmi_save_edit.py.

--gm sends a 主持人命令 (host command): the patched server applies it at once, through the same
calls as the cheat codes, without marking anyone as a cheater. Optional targets first
(hero=<name|id>, town=<name|id>, player=<colour>; default: the player's selected hero/town), then one of:
  give <creature> <count>      resource <gold|wood|ore|mercury|sulfur|crystal|gems> <n|+n|-n>
  artifact <artifact> [slot]   exp <n>   levels <n>   primary <attack|defence|spellpower|knowledge> <n|+n|-n>
  skill <skill> <0-3>          spell <spell>   mana <n|+n|-n>   move [points]
  build <building>             raze <building>
  army <slot 1-7> <creature> <count> | army <slot 1-7> none   (town=X without hero=: its garrison)
  recruits [level 1-7] <n|+n|-n>   (creatures waiting in the town's dwellings)
  reveal | hide | puzzle       luck | morale | fly (permanent)   teleport <x> <y> [z] (standing tile)
Names are VCMI identifiers (goldDragon, capitol) or the in-game names (金龙, 国会). The result
comes back as a 【Claude】 system message in the player's chat, and vcmi_watch.py prints it as
【修改结果】. What the player types in the in-game chat (Tab) is appended to from-game.jsonl in the same folder.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h3tools import config  # noqa: E402


def bridge_dir() -> Path:
    """$VCMI_CLAUDE_BRIDGE, else <vcmi_data>/claude-bridge."""
    return config.bridge_dir()


def say(text: str, kind: str = "chat", sender: str = "Claude") -> None:
    folder = bridge_dir()
    folder.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), "from": sender, "text": text, "kind": kind}
    # one write per line, so the client never sees half a message
    with open(folder / "to-game.jsonl", "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="")
    kind = ap.add_mutually_exclusive_group()
    kind.add_argument("--popup", action="store_true")
    kind.add_argument("--command", action="store_true")
    kind.add_argument("--open", action="store_true", help="open the player's Claude window (no text needed)")
    kind.add_argument("--gm", action="store_true", help="主持人命令: change the running game, not counted as cheating")
    ap.add_argument("--from", dest="sender", default="Claude")
    a = ap.parse_args()
    text = a.text.replace("\\n", "\n")
    if not text and not a.open:
        ap.error("text is required (except with --open)")
    say(text, "open" if a.open else "popup" if a.popup else "command" if a.command else "gm" if a.gm else "chat", a.sender)
    return 0


if __name__ == "__main__":
    sys.exit(main())
