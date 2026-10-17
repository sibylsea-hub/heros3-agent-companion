"""Live play-along: watch VCMI saves and print what changed each turn.

VCMI autosaves at the start of every turn. This polls the save folders and, for
every new save, reads it (tools/vcmi_save.py) and prints a short Chinese digest
of what happened since the previous save of the same map: moves, fights,
pick-ups, story events triggered, level-ups, buildings. A Claude Code session runs
it under Monitor, so each turn wakes Claude up (see docs/play-together.md).
Lines the player types in the in-game chat are printed too, when they play with
the patched client (tools/vcmi_play.py); answer into the game with tools/vcmi_say.py.
The patched client also reports what happens to the player (visits, messages and
story texts they see, questions, battles); those are kept and added to the next digest.

Usage:
  python -X utf8 -u tools/vcmi_watch.py                    # watch until killed
  python -X utf8 -u tools/vcmi_watch.py --diff OLD NEW     # print one digest and exit
  python -X utf8 tools/vcmi_watch.py --recap [N]           # catch up: last N turns + what was said

Saves are only read. Each digest also goes to out/play/latest.md, with the full
state (vcmi_save --brief) underneath, and the JSON of every save read is kept in
out/play/saves/. Saves come from <vcmi_data>/Saves and <claude-bridge>/remote-saves
(tools/h3tools/config.py).

Memory is kept for one game at a time (tools/vcmi_memory.py): when a save of another game
turns up, the old game's turns, chat and notes are put away first, and the digest says so
(【新的一局】, or 【回到旧局】 when an archived game is loaded again).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcmi_local  # noqa: E402
import vcmi_memory  # noqa: E402
import vcmi_save  # noqa: E402
import vcmi_say  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "play"
FROM_GAME = vcmi_say.bridge_dir() / "from-game.jsonl"   # written by the patched client
TURN_LOG = OUT / "turn-log.md"                           # every digest, oldest first
BIBLE = ROOT / "docs" / "story" / "bible.md"             # story notes, mentioned by --recap if present

RES_NAMES = {"gold": "金币", "wood": "木材", "ore": "矿石", "mercury": "水银",
             "sulfur": "硫磺", "crystal": "水晶", "gems": "宝石"}
MAX_EVENTS = 30  # per digest; the rest is counted

TYPE_NAMES = {"sign": "告示牌", "event": "事件", "seerHut": "先知小屋", "questGuard": "任务守卫",
              "pandora": "潘多拉魔盒", "artifact": "宝物", "prison": "监狱", "monster": "怪物"}


def short(text: str | None, n: int = 60) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[:n] + "…"


def save_key(d: dict) -> tuple:
    m = d["map"]
    return ((d.get("game") or {}).get("started"), m.get("campaign"), m.get("scenario"), m["name"])


def where(m: dict) -> str:
    return f"{m['name']}" + (f"（战役「{m['campaign']}」第 {m['scenario'] + 1} 章）" if "campaign" in m else "")


def human_players(d: dict) -> list[dict]:
    return [p for p in d["players"] if p["human"]]


def items(h: dict) -> list[str]:
    return [a["name"] for a in h["artifacts"].values()] + [a["name"] for a in h["backpack"]]


def diff_hero(old: dict | None, new: dict, label: str = "新英雄") -> list[str]:
    name = new["name"]
    if old is None:
        army = "、".join(f"{s['name']}×{s['count']}" for s in new["army"])
        return [f"{label} {name} Lv{new['level']} @ {tuple(new['pos'])}，部队 {army or '无'}"]
    out = []
    bits = []
    if old["pos"] != new["pos"]:
        bits.append(f"{tuple(old['pos'])} → {tuple(new['pos'])}")
    if new["level"] != old["level"]:
        bits.append(f"升到 Lv{new['level']}")
    if new["experience"] != old["experience"]:
        bits.append(f"经验 {new['experience'] - old['experience']:+d}")
    prim = [f"{k} {new['primary_base'][k] - old['primary_base'][k]:+d}" for k in new["primary_base"]
            if new["primary_base"][k] != old["primary_base"].get(k)]
    if prim:
        bits.append("属性 " + " ".join(prim).replace("attack", "攻").replace("defence", "防")
                    .replace("spellpower", "力").replace("knowledge", "知"))
    out.append(f"{name}：" + ("，".join(bits) if bits else "没动"))

    def counts(h):
        c: dict[str, list] = {}
        for s in h["army"]:
            c.setdefault(s["creature"], [s["name"], 0])[1] += s["count"]
        return c
    a, b = counts(old), counts(new)
    army = []
    for key in list(a) + [k for k in b if k not in a]:
        n0 = a.get(key, [None, 0])[1]
        n1 = b.get(key, [None, 0])[1]
        if n0 != n1:
            army.append(f"{(b.get(key) or a.get(key))[0]} {n0}→{n1}")
    if army:
        out.append("  部队 " + "，".join(army))
    gained = [x for x in items(new) if x not in items(old)]
    lost = [x for x in items(old) if x not in items(new)]
    if gained:
        out.append("  得到宝物 " + "、".join(gained))
    if lost:
        out.append("  失去宝物 " + "、".join(lost))
    old_sec = {s["skill"]: s["level"] for s in old["secondary"]}
    sec = [f"{s['name']}{s['level']}" for s in new["secondary"] if old_sec.get(s["skill"]) != s["level"]]
    if sec:
        out.append("  技能 " + "、".join(sec))
    spells = [s for s in new["spells"] if s not in old["spells"]]
    if spells:
        out.append(f"  学会魔法 {len(spells)} 个：" + "、".join(s.split(":")[-1] for s in spells))
    return out


def digest(old: dict | None, new: dict, path: Path, first: str | None = None) -> list[str]:
    """Short lines describing new (and what changed since old, a save of the same map)."""
    head = f"【第 {new['day']} 天】{where(new['map'])}  存档 {path.parent.name}/{path.name}"
    if old is None:
        lines = [head + (first or "（开始跟踪，之前的回合看不到）")]
    elif old["day"] == new["day"] and old == new:
        return [head + "（和上一个存档一样）"]
    else:
        gap = new["day"] - old["day"]
        lines = [head + ("" if gap == 1 else "，和上一个存档同一天" if gap == 0 else f"，上一个存档是第 {old['day']} 天")]

    old_players = {p["color"]: p for p in (old or {}).get("players", [])}
    for p in human_players(new):
        op = old_players.get(p["color"])
        if op and op["status"] != p["status"]:
            lines.append(f"{p['color']} 状态：{op['status']} → {p['status']}")
        if op:
            res = [f"{RES_NAMES.get(k, k)} {p['resources'][k] - op['resources'].get(k, 0):+d}"
                   for k in p["resources"] if p["resources"][k] != op["resources"].get(k, 0)]
            if res:
                lines.append("资源 " + "，".join(res) + f"（金币现有 {p['resources'].get('gold', 0)}）")
        old_heroes = {h["id"]: h for h in (op or {}).get("heroes", [])}
        for h in p["heroes"]:
            lines += diff_hero(old_heroes.get(h["id"]), h, "新英雄" if op else "英雄")
        if op:
            for hid, h in old_heroes.items():
                if hid not in {x["id"] for x in p["heroes"]}:
                    lines.append(f"英雄 {h['name']} 不在了（战败、逃跑或被解雇）")
            old_towns = {t["id"]: t for t in op["towns"]}
            for t in p["towns"]:
                ot = old_towns.get(t["id"])
                if ot is None:
                    lines.append(f"拿下城镇 {t['name']} @ {tuple(t['pos'])}")
                else:
                    built = [b.get("name", b["building"]) for b in t["buildings"] if b not in ot["buildings"]]
                    if built:
                        lines.append(f"{t['name']} 新建 " + "、".join(built))
            for tid, t in old_towns.items():
                if tid not in {x["id"] for x in p["towns"]}:
                    lines.append(f"丢了城镇 {t['name']}")
        elif old is None:
            for t in p["towns"]:
                lines.append(f"城镇 {t['name']}：{len(t['buildings'])} 座建筑")

    if old is not None:
        # Only what the human players did: the computer players' moves are none of the narrator's
        # business (spoilers).
        humans = {p["color"] for p in human_players(new)}
        mine: set[int] = set()
        for p in human_players(new):
            op = old_players.get(p["color"], {})
            mine |= set(p["destroyed_objects"]) | set(p["visited_objects"])
            mine -= set(op.get("destroyed_objects", [])) | set(op.get("visited_objects", []))
        old_story = {o["id"]: o for o in old["story_objects"]}
        for o in new["story_objects"]:
            oo = old_story.get(o["id"])
            if oo is None:
                continue
            kind = TYPE_NAMES.get(o["type"], o["type"])
            q, oq = o.get("quest"), oo.get("quest")
            if oo["present"] and not o["present"]:
                if o["id"] in mine:
                    lines.append(f"触发/清掉 {kind} #{o['id']} @ {tuple(o['pos'])}：{short(o.get('text'))}")
            elif q and oq and q["completed"] and not oq["completed"]:
                if humans & set(oq["active_for"]) or o["id"] in mine:
                    lines.append(f"完成任务 {kind} #{o['id']}：{short(q.get('completed_text') or o.get('text'))}")
            elif q and oq and humans & set(q["active_for"]) and not humans & set(oq["active_for"]):
                lines.append(f"接了任务 {kind} #{o['id']}：{short(q.get('first_visit_text') or o.get('text'))}")
            elif humans & (set(o.get("visited_by") or []) - set(oo.get("visited_by") or [])):
                lines.append(f"拜访 {kind} #{o['id']} @ {tuple(o['pos'])}：{short(o.get('text'))}")
        story_ids = set(old_story)
        old_removed = {o["id"] for o in old["removed_objects"]}
        gone = [o for o in new["removed_objects"]
                if o["id"] not in old_removed and o["id"] not in story_ids and o["id"] in mine]
        if gone:
            lines.append(f"地图上没了 {len(gone)} 样：" + "、".join(f"{o['name']}{tuple(o['pos'])}" for o in gone[:12])
                         + (" …" if len(gone) > 12 else ""))
    return lines


class Watcher:
    """Remembers what it has reported in out/play/watch-state.json, so a restart
    (Monitor runs at most 30 minutes at a time) picks up where it left off."""

    def __init__(self, folders: list[Path], state: Path):
        self.folders = folders
        self.state = state
        self.seen: dict[Path, float] = {}
        self.last: dict[tuple, Path] = {}   # newest save read per map
        self.failures: dict[Path, int] = {}
        self.skipped = 0                    # saves passed over while nobody watched
        self.events: list[str] = []         # what the client reported since the last digest
        self.chat_pos = FROM_GAME.stat().st_size if FROM_GAME.exists() else 0

    def saves(self) -> dict[Path, float]:
        found = {}
        for folder in self.folders:
            if folder.exists():
                for p in folder.rglob("*.vsgm1"):
                    if any(part.startswith("_") for part in p.relative_to(folder).parts):
                        continue  # Claude's test saves (_claude_check..., _check/...) passing through
                    try:
                        found[p] = p.stat().st_mtime
                    except OSError:
                        pass  # deleted while scanning
        return found

    def read(self, path: Path) -> dict | None:
        try:
            d = vcmi_save.read_save(path)
        except SystemExit as e:  # vcmi_save reports reader failures this way
            n = self.failures[path] = self.failures.get(path, 0) + 1
            if n == 3:
                emit([f"【读档失败】{path}：{short(str(e), 200)}"])
            return None
        self.failures.pop(path, None)
        keep = kept_json(path)
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return d

    def previous(self, key: tuple) -> dict | None:
        path = self.last.get(key)
        try:
            return json.loads(kept_json(path).read_text(encoding="utf-8")) if path else None
        except (OSError, ValueError):
            return None

    def resume(self) -> bool:
        try:
            state = json.loads(self.state.read_text(encoding="utf-8"))
            self.seen = {Path(p): m for p, m in state["seen"].items()}
            self.last = {tuple(k): Path(p) for k, p in state["last"]}
            self.chat_pos = state["chat_pos"]
            self.events = state.get("events", [])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        return True

    def save_state(self) -> None:
        self.state.parent.mkdir(parents=True, exist_ok=True)
        state = {"seen": {str(p): m for p, m in self.seen.items()},
                 "last": [[list(k), str(p)] for k, p in self.last.items()],
                 "chat_pos": self.chat_pos, "events": self.events}
        self.state.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def start(self, fresh: bool) -> None:
        if not fresh and self.resume():
            # Saves made while nobody watched: report only the newest, against the last one reported.
            pending = sorted((m, p) for p, m in self.saves().items() if self.seen.get(p) != m)
            for m, p in pending[:-1]:
                self.seen[p] = m
            self.skipped = max(0, len(pending) - 1)
            return
        current = self.saves()
        self.seen = dict(current)
        if not current:
            emit(["【陪玩就绪】还没有存档；开一局后每回合的自动存档会推送过来"])
        else:
            newest = max(current, key=current.get)
            d = None if vcmi_memory.stale(newest) else self.read(newest)
            if vcmi_memory.stale(newest):
                emit(["【陪玩就绪】记忆是空的（上一局已经收起来了）；玩家开新局或读档以后，每回合的存档会推送过来"])
            elif d is None:
                emit([f"【陪玩就绪】最新存档 {newest} 读不出来"])
            else:
                self.switch_game(d, newest)
                self.last[save_key(d)] = newest
                publish(digest(None, d, newest), d, newest, "【陪玩就绪】")
        self.save_state()

    def poll(self) -> None:
        changed = self.poll_chat()  # first, so events from before a save land in its digest
        now = time.time()
        for path, mtime in sorted(self.saves().items(), key=lambda kv: kv[1]):
            if self.seen.get(path) == mtime or now - mtime < 1.5:  # unchanged, or VCMI may still be writing
                continue
            d = self.read(path)
            if d is None:
                if self.failures.get(path, 0) >= 3:
                    self.seen[path] = mtime
                    changed = True
                continue
            self.seen[path] = mtime
            switched = self.switch_game(d, path)
            key = save_key(d)
            lines = digest(self.previous(key), d, path, "（换了一局，这是它的第一个存档）" if switched else None)
            if self.skipped > 0:
                lines[0] += f"（没在看的时候还存了 {self.skipped} 次，合在这一条里）"
                self.skipped = 0
            lines += event_lines(self.events)
            self.events = []
            publish(lines, d, path)
            self.last[key] = path
            changed = True
        if changed:
            self.save_state()

    def switch_game(self, d: dict, path: Path) -> bool:
        """Every game is a new game: put the old one's memory away before this save is reported."""
        lines = vcmi_memory.switch(vcmi_memory.game_of(d, path))
        if lines:
            self.events = []  # the end of the old game
            emit(lines)
        return bool(lines)

    def poll_chat(self) -> bool:
        if not FROM_GAME.exists():
            return False
        size = FROM_GAME.stat().st_size
        if size < self.chat_pos:  # file was recreated
            self.chat_pos = 0
        if size == self.chat_pos:
            return False
        with FROM_GAME.open("rb") as f:
            f.seek(self.chat_pos)
            data = f.read()
        complete = data[: data.rfind(b"\n") + 1]  # leave a half-written last line for next time
        self.chat_pos += len(complete)
        lines = []
        for raw in complete.decode("utf-8", "replace").splitlines():
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if m.get("kind") == "command":  # Claude's own vcmi_say --command, echoed back
                continue
            if m.get("kind") == "gm-result":  # the server's answer to a vcmi_say --gm
                lines.append(f"【修改结果】{m.get('text', '')}")
                continue
            if m.get("kind") == "event":  # something that happened in the game: goes into the next digest
                text = " ".join(str(m.get("text", "")).replace("{", "").replace("}", "").split())
                self.events.append(short(text, 300))
                continue
            lines.append(f"【游戏内聊天】{m.get('from', '?')}：{m.get('text', '')}")
        if lines:
            emit(lines)
        return True


def event_lines(events: list[str]) -> list[str]:
    if not events:
        return []
    lines = ["这期间游戏里："] + [f"· {e}" for e in events[:MAX_EVENTS]]
    if len(events) > MAX_EVENTS:
        lines.append(f"· …还有 {len(events) - MAX_EVENTS} 条")
    return lines


def kept_json(path: Path) -> Path:
    return OUT / "saves" / f"{path.parent.name}__{path.stem}.json"


def emit(lines: list[str]) -> None:
    print("\n".join(lines), flush=True)


def publish(lines: list[str], d: dict, path: Path, tag: str = "") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    detail = OUT / "latest.md"
    detail.write_text("\n".join(lines) + "\n\n---\n\n" + vcmi_save.brief(d, path) + "\n", encoding="utf-8")
    with TURN_LOG.open("a", encoding="utf-8") as f:  # every turn, for --recap
        f.write("\n".join(lines) + "\n\n")
    if tag:
        lines = [tag + lines[0]] + lines[1:]
    emit(lines + [f"（详情 {detail.relative_to(ROOT).as_posix()}）"])


def recap(turns: int, chats: int) -> list[str]:
    """What a fresh Claude session needs to pick up the game: the last turns and the last
    things said in the Claude window, in both directions. Only the game of the newest save:
    other games are put away first (tools/vcmi_memory.py)."""
    lines = vcmi_memory.sync()
    game = vcmi_memory.current()
    if game:
        lines.append(f"【这一局】{vcmi_memory.describe(game)}。每一局都是新的：只说这局的事，以前的局不提。")
    elif vcmi_memory.load():
        lines.append("【这一局】还没开始：以前的局都收起来了，记忆是空的。等玩家开新局，以前的局不提。")
    blocks = [b for b in TURN_LOG.read_text(encoding="utf-8").split("\n\n")
              if b.strip() and not b.startswith("【补记】")] if TURN_LOG.exists() else []
    if BIBLE.exists() and game and "<!-- game: " in BIBLE.read_text(encoding="utf-8"):
        # private story notes; public copies of the tools don't have them
        lines.append("【恢复记忆】这局（和同一战役前几章）玩家让存的经过和选择见 docs/story/bible.md「游玩记录」。")
    lines.append(f"最近 {min(turns, len(blocks))} 个回合（共记了 {len(blocks)} 个）：")
    lines += blocks[-turns:] or ["（还没有回合记录）"]
    talk = [m for m in vcmi_memory.talk() if m.get("kind", "chat") in ("chat", "popup") and m.get("text")]
    lines.append(f"Claude 窗口里最近 {min(chats, len(talk))} 句（共 {len(talk)} 句）：")
    lines += [f"[{m.get('ts', '?')}] {m.get('from', '?')}：{m['text']}" for m in talk[-chats:]] or ["（还没聊过）"]
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recap", nargs="?", const=10, type=int, metavar="TURNS",
                    help="print the last TURNS turns (default 10) and the last 30 lines said, then exit")
    ap.add_argument("--saves", type=Path, action="append",
                    help="save folder to watch (repeatable; default: <vcmi_data>/Saves and <claude-bridge>/remote-saves)")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--fresh", action="store_true", help="forget what was reported before and start over")
    ap.add_argument("--state", type=Path, default=OUT / "watch-state.json", help=argparse.SUPPRESS)
    ap.add_argument("--diff", nargs=2, type=Path, metavar=("OLD", "NEW"))
    a = ap.parse_args()

    if a.recap is not None:
        emit(recap(a.recap, 30))
        return 0
    if a.diff:
        old, new = (vcmi_save.read_save(p.resolve()) for p in a.diff)  # the reader runs in the VCMI folder
        emit(digest(old if save_key(old) == save_key(new) else None, new, a.diff[1]))
        return 0

    # saves made on this PC, and those an iPad/iPhone sent through tools/vcmi_relay.py
    w = Watcher(a.saves or [vcmi_local.GAME_DATA / "Saves", vcmi_say.bridge_dir() / "remote-saves"], a.state)
    w.start(a.fresh)
    while True:
        time.sleep(a.interval)
        try:
            w.poll()
        except Exception as e:  # keep watching; a crash here would look like a quiet game
            emit([f"【监视出错】{type(e).__name__}: {short(str(e), 200)}"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
