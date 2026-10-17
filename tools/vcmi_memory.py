"""Play-along memory, one game at a time.

Everything Claude remembers about a game belongs to that game: the turn digests
(out/play/turn-log.md, latest.md), the save JSONs (out/play/saves/), what was said in the
Claude window (<claude-bridge>/to-game.jsonl, from-game.jsonl), the messages to Claude's own seat
(<claude-bridge>/player/outbox.jsonl) and the game's 游玩记录 entries in docs/story/bible.md
(private repo only). Every game is a new game: when a save of another game turns up,
tools/vcmi_watch.py calls switch(), which moves the old game's memory to
out/play/archive/<start>_<map>/, so --recap and a fresh session only see the game being played.
Loading a save of an archived game again brings its memory back.

A game is known by its start time: the server stamps it when a game or a campaign chapter starts
and every save of that game keeps it (the reader's game.started; VCMI's autosave folders are
named <start>_<map> after it too). Chapters of one campaign run keep each other's 游玩记录.

Usage:
  python -X utf8 tools/vcmi_memory.py              # which game Claude remembers, what is kept, the archive
  python -X utf8 tools/vcmi_memory.py sync         # switch to the game of the newest save now
  python -X utf8 tools/vcmi_memory.py forget       # put everything away; the next game starts empty
  python -X utf8 tools/vcmi_memory.py note TEXT    # add a line to this game's 游玩记录 entry
  python -X utf8 tools/vcmi_memory.py search WORD [WORD ...] [--all]
                                                   # every game's turns, chat and notes with all the words
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcmi_local  # noqa: E402
import vcmi_save  # noqa: E402
import vcmi_say  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "play"
ARCHIVE = OUT / "archive"
STATE = OUT / "memory.json"                       # {"game": the game remembered or null, "since": ts}
TURN_LOG = OUT / "turn-log.md"
LATEST = OUT / "latest.md"
SAVES = OUT / "saves"
BIBLE = ROOT / "docs" / "story" / "bible.md"      # story notes; public copies of the tools don't have them
NOTES = "## 游玩记录"
TALK = ["to-game.jsonl", "from-game.jsonl", "player/outbox.jsonl"]  # in the bridge folder

AUTOSAVE = re.compile(r"(\d{4}-\d\d-\d\d)_(\d\d)-(\d\d)_")          # VCMI's autosave folder name
SAVE_IN_DIGEST = re.compile(r"存档 (.+?)/(\S+?)\.vsgm1")              # vcmi_watch digest header
MARK = re.compile(r"<!-- game: (\S+)(?: campaign: (.*?))? -->")       # on a 游玩记录 entry heading


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def key_of_folder(name: str) -> str | None:
    m = AUTOSAVE.match(name)
    return f"{m[1]}T{m[2]}:{m[3]}" if m else None


def game_of(d: dict, path: Path | None = None) -> dict:
    """The game a save (vcmi_save JSON) belongs to. key = start time to the minute."""
    m = d["map"]
    started = (d.get("game") or {}).get("started") or (key_of_folder(path.parent.name) if path else None) or ""
    return {"key": started[:16] or None, "started": started, "map": m["name"], "campaign": m.get("campaign"),
            "finished": [c.get("name") for c in m.get("finished_chapters", [])]}


def describe(g: dict) -> str:
    return f"「{g['map']}」（{g['key'].replace('T', ' ')} 开局）"


def load() -> dict | None:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def store(game: dict | None, since: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"game": game, "since": since}, ensure_ascii=False, indent=1), encoding="utf-8")


def current() -> dict | None:
    return (load() or {}).get("game")


def stamp(key: str) -> str:
    return key.replace("T", "_").replace(":", "-")


def folder(key: str, name: str = "") -> Path:
    """The archive folder of a game, named like VCMI's autosave folder: <start>_<map>."""
    if ARCHIVE.exists():
        for p in sorted(ARCHIVE.glob(stamp(key) + "_*")):
            return p
    return ARCHIVE / f"{stamp(key)}_{re.sub(r'[/:*?<>|\s\\\"]', '_', name).strip('._') or 'game'}"


def archives() -> list[tuple[str, Path]]:
    if not ARCHIVE.exists():
        return []
    return sorted((key_of_folder(p.name), p) for p in ARCHIVE.iterdir() if p.is_dir() and key_of_folder(p.name))


# -- reading the pieces ------------------------------------------------------
def read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_lines(p: Path) -> list[str]:
    try:
        return p.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def line_ts(raw: str) -> str:
    try:
        return str(json.loads(raw).get("ts", ""))
    except (ValueError, AttributeError):
        return ""


def append(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(text)


def blocks(text: str) -> list[str]:
    return [b for b in text.split("\n\n") if b.strip()]


def notes_section(text: str) -> tuple[int, int]:
    """[start, end) of the 游玩记录 entries (after the section's intro) in bible.md, or (-1, -1)."""
    head = text.find("\n" + NOTES + "\n")
    if head < 0:
        return -1, -1
    body = head + len(NOTES) + 2
    end = text.find("\n## ", body)
    end = len(text) if end < 0 else end + 1
    first = text.find("### ", body)
    return (first if 0 <= first < end else end), end


def entries(text: str) -> list[str]:
    """The ### entries of a 游玩记录 section (or of an archived 游玩记录.md)."""
    return ["### " + e for e in re.split(r"(?m)^### ", text)[1:]]


def notes(text: str) -> list[str]:
    start, end = notes_section(text)
    return entries(text[start:end]) if start >= 0 else []


def write_notes(text: str, es: list[str]) -> None:
    """Write bible.md (text) with its 游玩记录 entries replaced by es."""
    start, end = notes_section(text)
    head = text[:start].rstrip("\n") + "\n\n"
    body = "".join(e.rstrip("\n") + "\n\n" for e in es)
    tail = text[end:]
    BIBLE.write_text(head + body + tail if tail.strip() else (head + body).rstrip("\n") + "\n",
                     encoding="utf-8", newline="\n")


def entry_name(e: str) -> str:
    return MARK.sub("", e.split("\n", 1)[0])[4:].split(" · ")[0].strip()


def entry_game(e: str) -> tuple[str | None, str | None]:
    m = MARK.search(e.split("\n", 1)[0])
    return (m[1], m[2] or None) if m else (None, None)


def keeps(e: str, g: dict) -> bool:
    """Does entry e belong with game g? Its own entries, and those of earlier chapters of the same
    campaign run (g has finished chapters)."""
    key, campaign = entry_game(e)
    return key == g["key"] or bool(g["campaign"] and g["finished"] and campaign == g["campaign"])


# -- putting away and bringing back -----------------------------------------
def known_games() -> dict[str, str]:
    """key -> map name of every game seen (archives, autosave folders, kept JSONs, the state)."""
    games = {k: p.name[17:] for k, p in archives()}
    for folder_ in (vcmi_local.GAME_DATA / "Saves" / "Autosave", vcmi_say.bridge_dir() / "remote-saves"):
        if folder_.exists():
            for p in folder_.rglob("*"):
                if p.is_dir() and key_of_folder(p.name):
                    games.setdefault(key_of_folder(p.name), p.name[17:])
    for p in SAVES.glob("*.json"):
        d = read_json(p)
        if d:
            g = game_of(d, Path(p.name.split("__")[0]) / "x")
            if g["key"]:
                games[g["key"]] = g["map"]
    cur = current()
    if cur:
        games[cur["key"]] = cur["map"]
    return games


def put_away(keep: dict | None, boundary: str = "", cur: dict | None = None) -> dict[str, list[str]]:
    """Move all memory that is not keep's to the archive. Lines (chat, the seat's messages) written
    at or after boundary are keep's; older ones are cur's, or with no cur, belong to the game that
    had started last when they were written. Returns {archive folder: what went there}."""
    games = known_games()
    starts = sorted(games)
    moved: dict[str, list[str]] = {}
    keep_key = keep["key"] if keep else None

    # keys: a game's start, or "" when it can't be told (goes to archive/unsorted)
    def owner(ts: str) -> str:
        if keep_key and boundary and ts >= boundary:
            return keep_key
        if cur:
            return cur["key"]
        earlier = [k for k in starts if k <= ts[:16]]
        return earlier[-1] if earlier else (starts[0] if starts else "")

    def target(key: str) -> Path:
        return folder(key, games.get(key, "")) if key else ARCHIVE / "unsorted"

    def note(key: str, what: str) -> None:
        moved.setdefault(target(key).name, []).append(what)

    # save JSONs: by their own start time; old ones without it by the autosave folder, or the map
    by_name = {}
    for k in sorted(games):
        by_name[games[k]] = k
    json_key: dict[str, str] = {}
    for p in sorted(SAVES.glob("*.json")):
        d = read_json(p) or {"map": {"name": ""}}
        g = game_of(d, Path(p.name.split("__")[0]) / "x")
        json_key[p.stem] = k = g["key"] or by_name.get(g["map"]) or (cur or {}).get("key") or ""
        if k != keep_key:
            dest = target(k) / "saves" / p.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
            note(k, "save")

    # turn digests: by the save named in each header; others follow the block before them
    if TURN_LOG.exists():
        stay, last = [], (cur or {}).get("key") or ""
        for b in blocks(TURN_LOG.read_text(encoding="utf-8")):
            m = SAVE_IN_DIGEST.search(b.split("\n", 1)[0])
            if m:
                last = key_of_folder(m[1]) or json_key.get(f"{m[1]}__{m[2]}") or last
            if last == keep_key:
                stay.append(b)
            else:
                append(target(last) / "turn-log.md", b + "\n\n")
                note(last, "turn")
        TURN_LOG.write_text("".join(b + "\n\n" for b in stay), encoding="utf-8")
    if LATEST.exists():
        m = SAVE_IN_DIGEST.search(LATEST.read_text(encoding="utf-8").split("\n", 1)[0])
        k = (key_of_folder(m[1]) or json_key.get(f"{m[1]}__{m[2]}")) if m else None
        k = k or (cur or {}).get("key") or ""
        if k != keep_key:
            dest = target(k) / "latest.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(LATEST), str(dest))

    # chat and the seat's messages: by time. The game client and the relay append to these files
    # and start over from the top when one gets shorter, so only lines that move are taken out.
    bridge = vcmi_say.bridge_dir()
    for name in TALK:
        live = bridge / name
        for _ in range(5):
            try:
                raw = live.read_bytes()
            except FileNotFoundError:
                break
            stay, gone = [], {}
            for line in raw.decode("utf-8", "replace").splitlines():
                k = owner(line_ts(line)) if line.strip() else keep_key
                (stay if k == keep_key else gone.setdefault(k, [])).append(line)
            if not gone:
                break
            if live.stat().st_size != len(raw):
                continue  # written to meanwhile: read again
            live.write_text("".join(x + "\n" for x in stay), encoding="utf-8", newline="\n")
            for k, lines in gone.items():
                append(target(k) / name, "".join(x + "\n" for x in lines))
                moved.setdefault(target(k).name, []).extend(["chat" if name != TALK[2] else "seat"] * len(lines))
            break

    # 游玩记录 entries
    if BIBLE.exists():
        text = BIBLE.read_text(encoding="utf-8")
        es = notes(text)
        stay = [e for e in es if keep and keeps(e, keep)]
        for e in es:
            if e not in stay:
                k = entry_game(e)[0] or (cur or {}).get("key") or ""
                games.setdefault(k, entry_name(e))
                append(target(k) / "游玩记录.md", e.rstrip("\n") + "\n\n")
                note(k, "note")
        if len(stay) < len(es):
            write_notes(text, stay)
    return moved


def bring_back(g: dict) -> list[str]:
    """Move g's archived turns, save JSONs and 游玩记录 back (its chat stays in the archive, where
    talk() reads it, because the live chat files are read by offset)."""
    back = []
    src = folder(g["key"]) if any(k == g["key"] for k, _ in archives()) else None
    if src:
        for p in sorted((src / "saves").glob("*.json")):
            SAVES.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(SAVES / p.name))
        if (src / "turn-log.md").exists():
            old = (src / "turn-log.md").read_text(encoding="utf-8")
            back.append(f"{len(blocks(old))} 个回合")
            OUT.mkdir(parents=True, exist_ok=True)
            TURN_LOG.write_text(old + (TURN_LOG.read_text(encoding="utf-8") if TURN_LOG.exists() else ""),
                                encoding="utf-8")
            (src / "turn-log.md").unlink()
        if (src / "latest.md").exists() and not LATEST.exists():
            shutil.move(str(src / "latest.md"), str(LATEST))
    if BIBLE.exists():
        found = []
        for _, p in archives():
            f = p / "游玩记录.md"
            if f.exists():
                es = entries(f.read_text(encoding="utf-8"))
                found += [e for e in es if keeps(e, g)]
                rest = [e for e in es if not keeps(e, g)]
                if rest:
                    f.write_text("".join(e.rstrip("\n") + "\n\n" for e in rest), encoding="utf-8")
                else:
                    f.unlink()
        text = BIBLE.read_text(encoding="utf-8")
        if found and notes_section(text)[0] >= 0:
            write_notes(text, notes(text) + found)
            back.append(f"{len(found)} 条游玩记录")
    return back


def switch(game: dict, boundary: str | None = None) -> list[str]:
    """Make game the one remembered. Lines to show if that changed anything, else []."""
    state = load()
    cur = (state or {}).get("game")
    if not game.get("key"):
        return []  # can't tell which game this is
    if cur and cur["key"] == game["key"]:
        if game != cur:
            store(game, state["since"])  # same game, newer facts (finished chapters)
        return []
    if boundary is None:
        if state is None:          # never sorted before: lines go by when each game started
            boundary = game["started"]
        elif cur is None:          # after forget: everything since belongs to this game
            boundary = state["since"]
        elif game["started"] > cur["started"]:
            boundary = game["started"]
        else:                      # an older game loaded again
            boundary = now()
    moved = put_away(game, boundary, cur)
    back = bring_back(game)
    store(game, boundary)
    where = "、".join(f"out/play/archive/{name}/" for name in moved)
    if back:
        lines = [f"【回到旧局】{describe(game)}：这局的记忆拿回来了（{'，'.join(back)}）。"]
    else:
        lines = [f"【新的一局】{describe(game)}：从头记起。"]
    if moved:
        lines.append(f"以前那局的记录收进了 {where}；那是另一局的事，别再提。")
    return lines


def forget() -> list[str]:
    state = load()
    moved = put_away(None, "", (state or {}).get("game"))
    store(None, now())
    if not moved:
        return ["记忆里本来就没有哪一局的记录。"]
    return ["收起来了：" + "；".join(f"out/play/archive/{name}/（{summary(what)}）" for name, what in moved.items()),
            "下一局从头记起。读回这些局的存档时，它们的记忆会自己回来。"]


def newest_save() -> Path | None:
    found = []
    for folder_ in (vcmi_local.GAME_DATA / "Saves", vcmi_say.bridge_dir() / "remote-saves"):
        if folder_.exists():
            for p in folder_.rglob("*.vsgm1"):
                if not any(part.startswith("_") for part in p.relative_to(folder_).parts):  # Claude's test saves
                    found.append((p.stat().st_mtime, p))
    return max(found)[1] if found else None


def stale(path: Path) -> bool:
    """Was this save made before the memory was put away (forget)? Then nobody is playing that
    game: it stays put away until she plays it again and a new save turns up."""
    state = load()
    if not state or state.get("game"):
        return False
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds") < state["since"]


def sync() -> list[str]:
    """Switch to the game of the newest save (what a fresh session does before --recap)."""
    path = newest_save()
    if path is None or stale(path):
        return []
    try:
        d = vcmi_save.read_save(path)
    except SystemExit:
        return []
    return switch(game_of(d, path))


def talk(bridge: Path | None = None) -> list[dict]:
    """Chat lines of the game remembered: the live files plus what its archive holds."""
    bridge = bridge or vcmi_say.bridge_dir()
    paths = [bridge / n for n in TALK[:2]]
    cur = current()
    if cur and any(k == cur["key"] for k, _ in archives()):
        paths += [folder(cur["key"]) / n for n in TALK[:2]]
    out = []
    for p in paths:
        for raw in read_lines(p):
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
    out.sort(key=lambda m: str(m.get("ts", "")))
    return out


def note(text: str) -> str:
    """Add one line to the current game's 游玩记录 entry in bible.md (made if missing)."""
    g = current()
    if not g:
        raise SystemExit("还不知道在玩哪一局：先跑 sync（或等监视看到这局的存档）")
    if not BIBLE.exists():
        raise SystemExit(f"没有 {BIBLE}")
    content = BIBLE.read_text(encoding="utf-8")
    if notes_section(content)[0] < 0:
        raise SystemExit(f"{BIBLE.name} 里没有「{NOTES[3:]}」一节")
    es = notes(content)
    line = f"- {datetime.now():%m-%d}：{text.strip()}\n"
    for i, e in enumerate(es):
        if entry_game(e)[0] == g["key"]:
            es[i] = e.rstrip("\n") + "\n" + line
            break
    else:
        camp = f" campaign: {g['campaign']}" if g["campaign"] else ""
        es.append(f"### {g['map']} · {g['key'].replace('T', ' ')} 开局 <!-- game: {g['key']}{camp} -->\n{line}")
    write_notes(content, es)
    return f"记进了 {BIBLE.name}「{NOTES[3:]}」·{g['map']}"


def search(terms: list[str], per_game: int = 12) -> list[str]:
    """Lines of every game's memory (this game, then the archive, newest first) that contain all
    terms: turn digests, what was said and what happened in the game, 游玩记录 entries.
    Other games are for when she asks about them; they are not brought up otherwise."""
    want = [t.casefold() for t in terms]

    def hit(s: str) -> bool:
        s = s.casefold()
        return all(t in s for t in want)

    def cut(s: str, n: int = 110) -> str:
        s = " ".join(str(s).split())
        return s if len(s) <= n else s[:n] + "…"

    def scan(turns: str, chats: list[dict], notes_text: str) -> list[str]:
        out = []
        for b in blocks(turns):
            head, *rest = b.split("\n")
            found = [x for x in rest if hit(x)]
            if hit(head) or found:
                out.append("  " + cut(head.split("  存档")[0], 60))
                out += ["    " + cut(x) for x in found]
        for m in chats:
            if hit(str(m.get("text", ""))):
                who = "游戏里" if m.get("kind") == "event" else m.get("from", "?")
                out.append(f"  [{m.get('ts', '?')}] {who}：{cut(m['text'])}")
        for e in entries(notes_text):
            out += ["  游玩记录：" + cut(x.lstrip("- ")) for x in e.split("\n")[1:] if x.strip() and hit(x)]
        return out

    def chats_in(paths: list[Path]) -> list[dict]:
        out = []
        for p in paths:
            for raw in read_lines(p):
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
        return sorted(out, key=lambda m: str(m.get("ts", "")))

    cur = current()
    found: list[tuple[str, list[str]]] = []
    bible = BIBLE.read_text(encoding="utf-8") if BIBLE.exists() else ""
    here = scan(TURN_LOG.read_text(encoding="utf-8") if TURN_LOG.exists() else "", talk(),
                "".join(notes(bible)) if bible else "")
    if here:
        found.append(("这一局" + (describe(cur) if cur else "（还没开局）"), here))
    folders = [p for k, p in reversed(archives()) if not (cur and k == cur["key"])]
    if (ARCHIVE / "unsorted").exists():
        folders.append(ARCHIVE / "unsorted")
    for p in folders:
        text = lambda name: (p / name).read_text(encoding="utf-8") if (p / name).exists() else ""
        lines = scan(text("turn-log.md"), chats_in([p / n for n in TALK[:2]]), text("游玩记录.md"))
        if lines:
            found.append((f"以前的局 {p.name}", lines))
    if not found:
        return [f"搜「{' '.join(terms)}」：没搜到。"]
    out = [f"搜「{' '.join(terms)}」：{len(found)} 局里有（以前的局是她问起才说的，平时不提）"]
    for label, lines in found:
        out.append(f"▸ {label}")
        out += lines[:per_game]
        if len(lines) > per_game:
            out.append(f"  …还有 {len(lines) - per_game} 行（--all 全列）")
    return out


def summary(what: list[str]) -> str:
    names = {"turn": "回合", "save": "存档摘要", "chat": "行聊天和事件", "seat": "条座位消息", "note": "条游玩记录"}
    return "，".join(f"{what.count(k)} {v}" if k in ("chat", "seat", "note") else f"{what.count(k)} 个{v}"
                    for k, v in names.items() if what.count(k))


def status() -> list[str]:
    state = load()
    g = (state or {}).get("game")
    if state is None:
        lines = ["还没整理过：记忆里可能混着好几局（跑 sync 只留最新一局，或 forget 全部收起来）。"]
    elif g is None:
        lines = [f"记忆是空的（{state['since'].replace('T', ' ')} 收起来以后还没开始新的一局）。"]
    else:
        lines = [f"记的是这一局：{describe(g)}" + (f"，战役「{g['campaign']}」" if g["campaign"] else "")]
    turns = blocks(TURN_LOG.read_text(encoding="utf-8")) if TURN_LOG.exists() else []
    chats = [m for m in talk() if m.get("kind", "chat") in ("chat", "popup")]
    n_notes = len(notes(BIBLE.read_text(encoding="utf-8"))) if BIBLE.exists() else 0
    lines.append(f"  回合记录 {len(turns)} 个，存档摘要 {len(list(SAVES.glob('*.json')))} 个，"
                 f"Claude 窗口 {len(chats)} 句，游玩记录 {n_notes} 条")
    arch = archives()
    lines.append(f"收起来的局（out/play/archive/）：{len(arch)} 个" + ("" if arch else "，没有"))
    for k, p in arch:
        what = []
        if (p / "turn-log.md").exists():
            what.append(f"{len(blocks((p / 'turn-log.md').read_text(encoding='utf-8')))} 个回合")
        n = sum(len(read_lines(p / n)) for n in TALK[:2])
        if n:
            what.append(f"{n} 行聊天")
        if (p / "游玩记录.md").exists():
            what.append(f"{len(entries((p / '游玩记录.md').read_text(encoding='utf-8')))} 条游玩记录")
        lines.append(f"  {p.name}" + ("（就是这一局）" if g and k == g["key"] else "") + f"：{'，'.join(what) or '空'}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="status", choices=["status", "sync", "forget", "note", "search"])
    ap.add_argument("text", nargs="*")
    ap.add_argument("--all", action="store_true", help="search: list every hit, not the first 12 per game")
    a = ap.parse_args()
    if a.command == "sync":
        lines = sync() or ["已经是最新存档那一局了。"]
        lines += [""] + status()
    elif a.command == "forget":
        lines = forget()
    elif a.command == "note":
        if not a.text:
            ap.error("note 要给一段话")
        lines = [note(" ".join(a.text))]
    elif a.command == "search":
        if not a.text:
            ap.error("search 要给要搜的词")
        lines = search(a.text, 10**9 if a.all else 12)
    else:
        lines = status()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
