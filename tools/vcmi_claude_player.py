"""Play a colour as Claude in the patched VCMI 1.7.5 (路线 2：Claude 当本地玩家).

In the game: in the new-game (or load-game) lobby, open 「颜色和 Claude」 (top right) and press
「Claude 来打」 on any computer colour; or start a hotseat game (多人游戏 → 热座) and call one of the
players "Claude" (or "回声"). The patched client hands that colour's turns to Claude
through <claude-bridge>/player/: a briefing of what the colour can see, and commands back, each
checked before it runs. Walking uses the game's pathfinding, battles are auto battles, and if
Claude sends nothing for a few minutes the computer (the stand-in AI) plays the rest of the turn.

Usage: python -X utf8 tools/vcmi_claude_player.py <command> ...
  status                  whose turn it is, how many seconds Claude has left
  wait [--timeout S]      block until it is Claude's turn, then print the briefing and what
                          the player said in the game since the last wait
  brief                   the current briefing (same layout, fresh numbers)
  actions                 a numbered list of what Claude can do now; `do 3` runs item 3
  do CMD [CMD ...]        run commands in order, stop at the first one that fails:
                            move H1 <x> <y> [z]     walk there (an object's entrance tile visits it,
                                                    a monster's tile attacks it)
                            build T1 <building>     key in [brackets] in the briefing, or the name
                            recruit T1 <creature> [n|max]   into the visiting hero, else the garrison
                            hire T1 [1|2]           hero from the town's tavern
                            end                     end the turn
                            say <text>              chat line in the game (does not use up the turn)
                            <n>                     item n of the last `actions` list
  end                     end the turn and print what changed during it
  say TEXT                say something in the game
  log [-n N]              the last messages from the game (turns, replies, hand-overs)
Options: --bridge DIR (default: $VCMI_CLAUDE_BRIDGE or <vcmi_data>/claude-bridge, see
                      tools/h3tools/config.py),
         --color red|blue|... (only needed if Claude has more than one seat)

Every run touches player/listener.json. The client gives Claude its full answer time (default
180 s, player/config.json "timeout") only when that file is fresh (15 minutes); otherwise
nobody is listening and the AI takes the turn after "idleTimeout" (20 s), so the player isn't kept
waiting. File formats: client/ClaudePlayer.cpp in the patched source (tools/vcmi_src/README.md).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h3tools import config  # noqa: E402


def bridge_dir(arg: str | None) -> Path:
    return Path(arg) if arg else config.bridge_dir()


class Player:
    def __init__(self, bridge: Path, color: str | None):
        self.bridge = bridge
        self.dir = bridge / "player"
        self.color = color
        self.dir.mkdir(parents=True, exist_ok=True)
        self.touch()

    # -- files ---------------------------------------------------------------
    def touch(self) -> None:
        """Tell the client someone is listening (see the module docstring)."""
        (self.dir / "listener.json").write_text(
            json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "pid": os.getpid()}), encoding="utf-8")

    def outbox_size(self) -> int:
        path = self.dir / "outbox.jsonl"
        return path.stat().st_size if path.exists() else 0

    def read_outbox(self, offset: int) -> tuple[list[dict], int]:
        path = self.dir / "outbox.jsonl"
        if not path.exists():
            return [], offset
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        end = data.rfind(b"\n")
        if end < 0:
            return [], offset
        messages = []
        for raw in data[:end].splitlines():
            try:
                messages.append(json.loads(raw.decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                pass
        return messages, offset + end + 1

    def state(self) -> list[dict]:
        path = self.dir / "state.json"
        try:
            seats = json.loads(path.read_text(encoding="utf-8")).get("seats", [])
        except (OSError, ValueError):
            return []
        return [s for s in seats if not self.color or s.get("color") == self.color]

    # -- talking to the game -------------------------------------------------
    def request(self, lines: list[str], timeout: float) -> dict:
        rid = uuid.uuid4().hex[:12]
        offset = self.outbox_size()
        entry = {"id": rid, "lines": lines, "ts": datetime.now().isoformat(timespec="seconds")}
        if self.color:
            entry["color"] = self.color
        with open(self.dir / "inbox.jsonl", "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            messages, offset = self.read_outbox(offset)
            for m in messages:
                if m.get("kind") == "reply" and m.get("id") == rid:
                    return m
            time.sleep(0.2)
        return {"ok": False, "text": f"游戏 {timeout:.0f} 秒没有回应：游戏没开着、这局没有叫 Claude 的座位，"
                                     "或者开的不是打补丁的客户端（tools/vcmi_play.py）。"}

    def wait_turn(self, timeout: float | None) -> dict | None:
        """The briefing of Claude's current turn, waiting for one if needed."""
        offset = self.outbox_size()
        for seat in self.state():
            if seat.get("turn") and not seat.get("handedOver"):
                briefing = self.dir / "briefing.md"
                # state.json outlives a game that was killed: ask the game whether the turn is really on
                if briefing.exists() and "轮到你了" in self.request(["status"], 5).get("text", ""):
                    return {"kind": "turn", "color": seat.get("color"), "text": briefing.read_text(encoding="utf-8")}
        deadline = None if timeout is None else time.monotonic() + timeout
        last_touch = time.monotonic()
        while deadline is None or time.monotonic() < deadline:
            messages, offset = self.read_outbox(offset)
            for m in messages:
                if m.get("kind") == "turn" and (not self.color or m.get("color") == self.color):
                    return m
            if time.monotonic() - last_touch > 30:
                self.touch()
                last_touch = time.monotonic()
            time.sleep(0.5)
        return None

    def new_chat(self) -> list[dict]:
        """What the player said in the game (Claude window / Tab chat) since the last call."""
        path = self.bridge / "from-game.jsonl"
        mark = self.dir / "cli-chat-offset.txt"
        if not path.exists():
            return []
        size = path.stat().st_size
        try:
            offset = int(mark.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            offset = max(0, size - 4000)  # first time: only the last few lines
        if offset > size:
            offset = 0
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        mark.write_text(str(size), encoding="utf-8")
        lines = []
        for raw in data.splitlines():
            try:
                m = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if m.get("kind", "chat") == "chat" and m.get("text"):
                lines.append(m)
        return lines


def show(message: dict) -> int:
    print(message.get("text", "").rstrip())
    return 0 if message.get("ok", True) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bridge", help="claude-bridge folder (default: $VCMI_CLAUDE_BRIDGE or <vcmi_data>/claude-bridge)")
    ap.add_argument("--color", help="which Claude seat, if there are several (red, blue, ...)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    w = sub.add_parser("wait")
    w.add_argument("--timeout", type=float, default=None, help="give up after this many seconds")
    sub.add_parser("brief")
    sub.add_parser("actions")
    d = sub.add_parser("do")
    d.add_argument("commands", nargs="+")
    d.add_argument("--timeout", type=float, default=900, help="seconds to wait for the game (battles take a while)")
    sub.add_parser("end")
    s = sub.add_parser("say")
    s.add_argument("text", nargs="+")
    lg = sub.add_parser("log")
    lg.add_argument("-n", type=int, default=10)
    args = ap.parse_args()

    player = Player(bridge_dir(args.bridge), args.color)

    if args.command == "status":
        seats = player.state()
        if not seats:
            print("没有 Claude 的座位（游戏没开，或者这局热座里没有叫 Claude 的玩家）")
            return 1
        reply = player.request(["status"], 15)
        if reply.get("ok") is False and "没有回应" in reply.get("text", ""):
            for seat in seats:
                print(f"{seat['color']} {seat['name']}：{'轮到了' if seat['turn'] else '没轮到'}（state.json，{seat.get('day')} 天）")
            return 1
        return show(reply)

    if args.command == "wait":
        message = player.wait_turn(args.timeout)
        if message is None:
            print("等到超时，还没轮到 Claude")
            return 1
        print(message["text"].rstrip())
        chat = player.new_chat()
        if chat:
            print("\n## 玩家在游戏里说的话（上次 wait 以来）")
            for m in chat:
                print(f"- {m.get('ts', '')[11:16]} {m.get('from', '')}：{m['text']}")
        return 0

    if args.command in ("brief", "actions"):
        return show(player.request([args.command], 60))

    if args.command == "do":
        return show(player.request(args.commands, args.timeout))

    if args.command == "end":
        return show(player.request(["end"], 120))

    if args.command == "say":
        return show(player.request(["say " + " ".join(args.text)], 15))

    if args.command == "log":
        path = player.dir / "outbox.jsonl"
        if not path.exists():
            print("还没有消息")
            return 0
        lines = path.read_text(encoding="utf-8").splitlines()[-args.n:]
        for raw in lines:
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            text = m.get("text", "").strip().replace("\n", " ⏎ ")
            print(f"[{m.get('ts', '')[11:19]} {m.get('kind')} {m.get('color')} 第{m.get('day')}天] {text[:300]}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
