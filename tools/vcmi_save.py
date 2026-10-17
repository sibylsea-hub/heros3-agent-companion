"""Read a VCMI 1.7.5 save and print the game state (JSON, or a short digest).

Usage:
  python -X utf8 tools/vcmi_save.py                 # newest save under <vcmi_data>/Saves
  python -X utf8 tools/vcmi_save.py <file.vsgm1>    # a specific save
  options: --brief (human-readable digest), --full-text (don't shorten texts), -o out.json

Uses tools/vcmi_src (vcmi_save_reader.exe, built against VCMI 1.7.5 -- saves from
another VCMI version will fail to load). Saves are only read, never modified.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcmi_local  # noqa: E402

RES_ORDER = ["gold", "wood", "ore", "mercury", "sulfur", "crystal", "gems"]


def latest_save() -> Path:
    saves = list((vcmi_local.GAME_DATA / "Saves").rglob("*.vsgm1"))
    if not saves:
        raise SystemExit(f"no saves under {vcmi_local.GAME_DATA / 'Saves'}")
    return max(saves, key=lambda p: p.stat().st_mtime)


def read_save(path: Path, full_text: bool = False) -> dict:
    vcmi_local.prepare()
    cmd = [str(vcmi_local.SAVE_READER), str(path)]
    if full_text:
        cmd.append("--full-text")
    res = subprocess.run(cmd, cwd=vcmi_local.INSTALL, capture_output=True)
    if res.returncode != 0:
        raise SystemExit(res.stderr.decode("utf-8", "replace").strip() or f"reader failed ({res.returncode})")
    return json.loads(res.stdout.decode("utf-8"))


def brief(d: dict, path: Path) -> str:
    out = []
    when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    out.append(f"存档 {path}（{when}）")
    m = d["map"]
    camp = f"，战役「{m['campaign']}」第 {m['scenario'] + 1} 章" if "campaign" in m else ""
    out.append(f"地图 {m['name']}{camp}；第 {d['day']} 天（第 {d['month']} 月第 {d['week']} 周第 {d['day_of_week']} 天）")
    for p in d["players"]:
        who = "人类" if p["human"] else "电脑"
        res = " ".join(f"{k} {p['resources'][k]}" for k in RES_ORDER if k in p["resources"])
        out.append(f"\n{p['color']}（{who}，{p['status']}）  {res}")
        for h in p["heroes"]:
            ps = h["primary"]
            army = "、".join(f"{s['name']}×{s['count']}" for s in h["army"])
            arts = "、".join(a["name"] for a in h["artifacts"].values())
            out.append(f"  英雄 {h['name']} Lv{h['level']}（经验 {h['experience']}）@ {tuple(h['pos'])}  "
                       f"攻{ps['attack']} 防{ps['defence']} 力{ps['spellpower']} 知{ps['knowledge']}  "
                       f"魔法 {h['mana']}  行动力 {h['movement']}")
            out.append(f"    部队 {army or '无'}")
            if arts:
                out.append(f"    宝物 {arts}" + (f"；背包 {len(h['backpack'])} 件" if h["backpack"] else ""))
            if h["secondary"]:
                out.append("    技能 " + "、".join(f"{s['name']}{s['level']}" for s in h["secondary"]))
        for t in p["towns"]:
            out.append(f"  城镇 {t['name']} @ {tuple(t['pos'])}  建筑 {len(t['buildings'])} 座：" +
                       "、".join(b.get("name", b["building"]) for b in t["buildings"]))
    out.append("\n剧情物件（√ 已触发/已拜访，· 未碰过；事件、潘多拉、宝物、怪物用过就从地图上消失）：")
    for o in d["story_objects"]:
        q = o.get("quest")
        if not o["present"]:
            state = "√ 已消失"
        elif q and q["completed"]:
            state = "√ 任务完成"
        elif q and q["active_for"]:
            state = "… 任务进行中"
        elif o.get("visited_by"):
            state = "√ 拜访过"
        elif o["type"] == "sign":
            state = "? 不记录"  # VCMI keeps no trace of reading a sign
        else:
            state = "·"
        text = (o.get("text") or "").replace("\n", " ")[:40]
        out.append(f"  {state:8} {o['type']} #{o['id']} @ {tuple(o['pos'])} {text}")
    story_ids = {o["id"] for o in d["story_objects"]}
    gone = [o for o in d["removed_objects"] if o["id"] not in story_ids]
    if gone:
        out.append(f"\n已从地图上消失的物件 {len(gone)} 个：" +
                   "、".join(f"{o['name']}{tuple(o['pos'])}" for o in gone[:30]) +
                   (" …" if len(gone) > 30 else ""))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("save", nargs="?", type=Path)
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--full-text", action="store_true")
    ap.add_argument("-o", type=Path)
    a = ap.parse_args()
    path = a.save or latest_save()
    data = read_save(path, a.full_text)
    text = brief(data, path) if a.brief else json.dumps(data, ensure_ascii=False, indent=1)
    if a.o:
        a.o.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
