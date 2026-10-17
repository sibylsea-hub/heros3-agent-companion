"""Dump every original campaign to out/campaigns/<file>/.

For each campaign (Data/H3bitmap.lod, Data/H3ab_bmp.lod, Maps/C1..C8-*.h3c):

* ``campaign.json`` - the full header in h3c.to_dir() form (rebuildable with
  ``h3c.from_dir``), annotated with ``_``-prefixed human-readable fields
  (Chinese names of heroes/creatures/artifacts/spells, media names, ...);
* ``<scenario map>.h3m`` - every embedded map, gzip-compressed like normal maps;
* ``story.md`` - the campaign's Chinese text: description, then per scenario
  its region text, map title/description, prolog and epilog.

Also writes ``out/campaigns/index.md``.

Run:  PYTHONIOENCODING=utf-8 python -X utf8 tools/dump_campaigns.py [--game DIR] [--out DIR]
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from h3tools import config, gamedb, h3c  # noqa: E402
from h3tools.binio import Reader  # noqa: E402

H3M_VERSIONS = {0x0E: "RoE", 0x15: "AB", 0x1C: "SoD"}
SKILL_LEVELS = {1: "初级", 2: "中级", 3: "高级"}
RESOURCE_SPECIAL = {-3: "木材和矿石", -2: "水银、硫磺、水晶和宝石"}


def h3m_info(data: bytes) -> dict:
    """Title/description/size from an (uncompressed) RoE/AB/SoD H3M header."""
    try:
        r = Reader(data)
        ver = r.u32()
        if ver not in H3M_VERSIONS:
            return {"version": ver}
        r.u8()  # any players
        size = r.u32()
        two_level = r.u8()
        return {"version": ver, "format": H3M_VERSIONS[ver], "size": size,
                "underground": bool(two_level), "title": r.str(), "description": r.str()}
    except Exception:  # noqa: BLE001 - best effort, never fail a dump on it
        return {}


class Names:
    """Chinese names for ids, from the game database."""

    def __init__(self, db: dict):
        self.db = db
        self.misc = db.get("misc", {})
        self.colors = self.misc.get("player_colors", [])
        self.difficulties = self.misc.get("campaign_difficulties", [])
        self.special_heroes = self.misc.get("campaign_special_heroes", {})
        self.camp_maps = {m["id"]: m for m in self.misc.get("campaign_maps", [])}
        self.common_buildings = {}
        towns = db.get("towns", [])
        if towns:
            for b in towns[0].get("h3m_buildings", []):
                self.common_buildings[b["h3m_id"]] = (b["vcmi"], b["name"])

    def _get(self, kind, i):
        for r in self.db.get(kind, []):
            if r["id"] == i:
                return r
        return None

    def name(self, kind, i, default=None):
        r = self._get(kind, i)
        return r["name"] if r else (default if default is not None else f"#{i}")

    def hero(self, i):
        if str(i) in self.special_heroes and i < 0:
            return self.special_heroes[str(i)]
        return self.name("heroes", i)

    def color(self, i):
        return self.colors[i] if 0 <= i < len(self.colors) else f"玩家{i}"

    def difficulty(self, i):
        return self.difficulties[i] if 0 <= i < len(self.difficulties) else str(i)

    def building(self, i):
        vcmi, zh = self.common_buildings.get(i, (None, None))
        if vcmi is None:
            return f"建筑#{i}"
        if vcmi.startswith("dwelling"):
            lvl = vcmi[-1]
            return f"{lvl}级兵营{'（升级）' if 'Up' in vcmi else ''}"
        if vcmi.startswith("horde"):
            return f"{vcmi[5]}级兵种产量建筑"
        if vcmi.startswith("special"):
            return f"特殊建筑{vcmi[-1]}"
        return zh or vcmi

    def bonus(self, b: dict) -> str:
        t = b["type"]
        if t == "spell":
            return f"{self.hero(b['hero'])}学会魔法「{self.name('spells', b['spell'])}」"
        if t == "creature":
            return f"{self.hero(b['hero'])}获得{b['amount']}个{self.name('creatures', b['creature'])}"
        if t == "building":
            return f"主城建有{self.building(b['building'])}"
        if t == "artifact":
            return f"{self.hero(b['hero'])}获得宝物「{self.name('artifacts', b['artifact'])}」"
        if t == "scroll":
            return f"{self.hero(b['hero'])}获得「{self.name('spells', b['spell'])}」魔法卷轴"
        if t == "primarySkill":
            prim = self.misc.get("primary_skills") or ["攻击力", "防御力", "力量", "知识"]
            parts = [f"{prim[k]}+{b[key]}" for k, key in enumerate(h3c.PRIMARY) if b[key]]
            return f"{self.hero(b['hero'])}{'、'.join(parts)}"
        if t == "secondarySkill":
            return (f"{self.hero(b['hero'])}掌握{SKILL_LEVELS.get(b['mastery'], b['mastery'])}"
                    f"{self.name('skills', b['skill'])}")
        if t == "resource":
            res = RESOURCE_SPECIAL.get(b["resource"]) or self.name("resources", b["resource"])
            return f"额外获得{b['amount']}{res}" + ("（各）" if b["resource"] in (-3, -2) else "")
        if t == "prevHero":
            return f"使用第{b['scenario'] + 1}关的英雄（{self.color(b['player'])}）"
        if t == "hero":
            return f"起始英雄：{self.hero(b['hero'])}（{self.color(b['player'])}）"
        return str(b)


def annotate(c: dict, names: Names, label: str, also: list[str]) -> dict:
    """Deep copy of the campaign with ``_``-prefixed readable annotations."""
    c = copy.deepcopy(c)
    h = c["header"]
    h["_source"] = label
    if also:
        h["_identical_copies"] = also
    reg = h3c.REGION_MAPS.get(h["region_map"])
    cm = names.camp_maps.get(h["region_map"], {})
    if reg:
        h["_region_map_info"] = {"vcmi": reg[0], "image_prefix": reg[1], "background": f"{reg[1]}_BG.BMP",
                                 "regions": reg[2], "title_en": reg[3], "title_zh": cm.get("name"),
                                 "region_labels": cm.get("regions")}
    h["_music_name"] = h3c.media_name("music", h["music"])
    for i, s in enumerate(h["scenarios"]):
        s["_index"] = i
        if not s["map_name"]:
            s["_void"] = True
            continue
        m = h3c.get_map(c, i)
        if m:
            info = h3m_info(m)
            if info.get("title") is not None:
                s["_map_title"] = info["title"]
                s["_map_format"] = info["format"]
                s["_map_size"] = info["size"]
        s["_region_color_name"] = names.color(s["region_color"])
        s["_difficulty_name"] = names.difficulty(s["difficulty"])
        labels = cm.get("regions") or []
        if i < len(labels) and not labels[i].isascii():
            s["_region_label"] = labels[i]
        for key in ("prolog", "prolog2", "prolog3", "epilog", "epilog2", "epilog3"):
            p = s.get(key)
            if p:
                p["_video_name"] = h3c.media_name("video", p["video"])
                p["_music_name"] = h3c.media_name("music", p["music"])
        t = s["travel"]
        if t.get("keep_creatures"):
            t["_keep_creatures_names"] = [names.name("creatures", x) for x in t["keep_creatures"]]
        if t.get("keep_artifacts"):
            t["_keep_artifacts_names"] = [names.name("artifacts", x) for x in t["keep_artifacts"]]
        if "player_color" in t:
            t["_player_color_name"] = names.color(t["player_color"])
        for b in t.get("bonuses", []):
            b["_desc"] = names.bonus(b)
    return c


def md_text(text: str) -> str:
    """H3 text -> Markdown, keeping its line breaks."""
    text = text.replace("\r\n", "\n").strip("\n")
    return "  \n".join(text.split("\n")) if text else "（无）"


def story_md(c: dict, file_name: str, maps_info: dict) -> str:
    h = c["header"]
    reg = h.get("_region_map_info", {})
    out = [f"# {h['name']}", ""]
    meta = [f"文件 `{file_name}`（{h['_source']}）",
            f"格式 {h['_version_name']}（版本 {h['version']}）",
            f"战役地图 {reg.get('vcmi', h['region_map'])}（#{h['region_map']}）",
            f"{sum(1 for s in h['scenarios'] if s['map_name'])} 个关卡"]
    if h.get("difficulty_choosable"):
        meta.append("可选难度")
    out += ["> " + " · ".join(meta), "", "## 战役简介", "", md_text(h["description"]), ""]
    n = 0
    for i, s in enumerate(h["scenarios"]):
        if not s["map_name"]:
            continue
        n += 1
        title = s.get("_map_title") or s["map_name"]
        out += [f"## 第{n}关　{title}", ""]
        info = [f"地图 `{s['map_name']}`"]
        if s.get("_region_label"):
            info.append(f"区域「{s['_region_label']}」")
        info.append(f"{s['_region_color_name']}")
        info.append(f"默认难度 {s['_difficulty_name']}")
        if s["preconditions"]:
            pre = [str(sum(1 for x in h["scenarios"][:p + 1] if x["map_name"])) for p in s["preconditions"]]
            info.append("前置：第" + "、".join(pre) + "关")
        out += ["*" + " · ".join(info) + "*", ""]
        if s["region_text"]:
            out += ["**区域说明**　" + md_text(s["region_text"]), ""]
        desc = maps_info.get(i, {}).get("description")
        if desc:
            out += ["**地图简介**", "", md_text(desc), ""]
        for key, label in (("prolog", "序幕"), ("prolog2", "序幕（二）"), ("prolog3", "序幕（三）"),
                           ("epilog", "尾声"), ("epilog2", "尾声（二）"), ("epilog3", "尾声（三）")):
            p = s.get(key)
            if p:
                out += [f"### {label}", "", md_text(p["text"]), ""]
        t = s["travel"]
        keeps = {"experience": "经验", "primarySkills": "主属性", "secondarySkills": "辅助技能",
                 "spells": "魔法", "artifacts": "宝物"}
        kept = [keeps[k] for k in t.get("hero_keeps", []) if k in keeps]
        extra = []
        if kept:
            extra.append("英雄保留：" + "、".join(kept))
        if t.get("_keep_creatures_names"):
            extra.append("带走生物：" + "、".join(t["_keep_creatures_names"][:12])
                         + ("等" if len(t["_keep_creatures_names"]) > 12 else ""))
        if t.get("_keep_artifacts_names"):
            extra.append("带走宝物：" + "、".join(t["_keep_artifacts_names"]))
        if t.get("bonuses"):
            extra.append("开局奖励（任选其一）：" + "；".join(b["_desc"] for b in t["bonuses"]))
        if extra:
            out += ["*" + "　".join(extra) + "*", ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game", help="original game folder (default: h3_game_dir, see tools/h3tools/config.py)")
    ap.add_argument("--out", default=str(ROOT / "out" / "campaigns"))
    a = ap.parse_args(argv)
    out_root = Path(a.out)
    names = Names(gamedb.load())

    # collect, de-duplicating identical copies (SoD LOD first, like the game)
    seen: dict[str, tuple[str, str, bytes, list[str]]] = {}
    order = []
    for label, file_name, raw in h3c.game_campaigns(a.game or config.game_dir()):
        stem = Path(file_name).stem
        key = stem.lower()
        if key in seen:
            if seen[key][2] == raw:
                seen[key][3].append(label)
                continue
            key = f"{key}_{label.split(':')[0].split('.')[0].lower()}"
            stem = f"{stem}_{label.split(':')[0].split('.')[0]}"
        seen[key] = (label, stem, raw, [])
        order.append(key)

    index = ["# 原版战役一览", "", "| 目录 | 战役 | 格式 | 关卡 | 来源 |", "|---|---|---|---|---|"]
    for key in order:
        label, stem, raw, also = seen[key]
        c = h3c.parse(raw)
        if c["header"]["name"].isascii():  # untranslated English file (RoE Neutral.h3c)
            c = h3c.parse(raw, "cp1252")
        ca = annotate(c, names, label, also)
        d = out_root / stem
        d.mkdir(parents=True, exist_ok=True)
        h3c.to_dir(ca, d)
        maps_info = {i: h3m_info(h3c.get_map(c, i)) for i, s in enumerate(c["header"]["scenarios"])
                     if s["map_name"]}
        (d / "story.md").write_text(story_md(ca, Path(label.split(":")[-1]).name, maps_info),
                                    encoding="utf-8", newline="\n")
        h = c["header"]
        nmaps = sum(1 for s in h["scenarios"] if s["map_name"])
        src = label + ("（另见 " + "、".join(also) + "）" if also else "")
        index.append(f"| [{stem}]({stem}/story.md) | {h['name']} | {h['_version_name']} | {nmaps} | {src} |")
        print(f"{stem:22} {h['_version_name']:4} {nmaps:2} maps  {h['name']}")
    (out_root / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(order)} campaigns -> {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
