"""Game database: original HoMM3 (SoD, Simplified Chinese) text tables + VCMI identifiers.

The tables are read from the game's LOD archives (``H3bitmap.lod`` = SoD data,
``H3ab_bmp.lod`` = older Armageddon's Blade data; like the game and VCMI, SoD
overrides AB) with a Python port of VCMI's ``CLegacyConfigParser``, using the
same row/field order as VCMI's ``loadLegacyData()`` loaders. Every record is
cross-referenced by numeric index with the VCMI configs in
``third_party/vcmi/config`` to get the VCMI identifier (``"vcmi"``) and data the
text files do not carry (creature level/faction, hero class, ...).

Usage::

    python -X utf8 tools/h3tools/gamedb.py build            # writes out/gamedb/*.json
    python -X utf8 tools/h3tools/gamedb.py find 天使
    python -X utf8 tools/h3tools/gamedb.py find archangel --kind creatures
    python -X utf8 tools/h3tools/gamedb.py templates 54 13      # templates of class/subclass
    python -X utf8 tools/h3tools/gamedb.py templates 134 --terrain snow --strict

From Python::

    from h3tools import gamedb
    db = gamedb.load()                       # out/gamedb/*.json (built on demand)
    gamedb.find_creature("天使")             # -> list of creature records
    gamedb.find_template(54, 13)            # monster templates for creature 13
    gamedb.get("artifacts", 128)            # record by id
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from . import config
    from .lod import Lod
except ImportError:  # run as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from h3tools import config
    from h3tools.lod import Lod

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VCMI = ROOT / "third_party" / "vcmi"
DEFAULT_OUT = ROOT / "out" / "gamedb"
ENCODING = "gbk"

KINDS = ["creatures", "artifacts", "heroes", "hero_classes", "spells", "skills", "towns",
         "resources", "terrains", "rivers", "roads", "objects", "templates", "misc"]

RESOURCE_NAMES = ["wood", "mercury", "ore", "sulfur", "crystal", "gems", "gold"]
FACTIONS = ["castle", "rampart", "tower", "inferno", "necropolis", "dungeon", "stronghold",
            "fortress", "conflux"]
PRIMARY_SKILLS = ["attack", "defence", "spellpower", "knowledge"]
SECONDARY_SKILLS = [
    "pathfinding", "archery", "logistics", "scouting", "diplomacy", "navigation", "leadership",
    "wisdom", "mysticism", "luck", "ballistics", "eagleEye", "necromancy", "estates",
    "fireMagic", "airMagic", "waterMagic", "earthMagic", "scholar", "tactics", "artillery",
    "learning", "offence", "armorer", "intelligence", "sorcery", "resistance", "firstAid"]
# ARTRAITS.TXT slot columns, in file order (VCMI ART_POS_LIST)
ART_SLOTS = ["SPELLBOOK", "MACH4", "MACH3", "MACH2", "MACH1", "MISC5", "MISC4", "MISC3",
             "MISC2", "MISC1", "FEET", "LEFT_RING", "RIGHT_RING", "TORSO", "LEFT_HAND",
             "RIGHT_HAND", "NECK", "SHOULDERS", "HEAD"]
ART_CLASSES = {"S": "SPECIAL", "T": "TREASURE", "N": "MINOR", "J": "MAJOR", "R": "RELIC"}
# legacy H3 building ids (VCMI EBuildingType::names)
BUILDING_NAMES = [
    "mageGuild1", "mageGuild2", "mageGuild3", "mageGuild4", "mageGuild5", "tavern", "shipyard",
    "fort", "citadel", "castle", "villageHall", "townHall", "cityHall", "capitol", "marketplace",
    "resourceSilo", "blacksmith", "special1", "horde1", "horde1Upgr", "ship", "special2",
    "special3", "special4", "horde2", "horde2Upgr", "grail", "extraTownHall", "extraCityHall",
    "extraCapitol", "dwellingLvl1", "dwellingLvl2", "dwellingLvl3", "dwellingLvl4",
    "dwellingLvl5", "dwellingLvl6", "dwellingLvl7", "dwellingUpLvl1", "dwellingUpLvl2",
    "dwellingUpLvl3", "dwellingUpLvl4", "dwellingUpLvl5", "dwellingUpLvl6", "dwellingUpLvl7"]
SPELL_LEVEL_NAMES = ["none", "basic", "advanced", "expert"]
# Data sizes VCMI reads from the H3 text files (config/gameConfig.json "textData")
N_CREATURES, N_ARTIFACTS, N_HEROES, N_HERO_CLASSES = 150, 144, 156, 18


# =========================================================================== parsing helpers

class LegacyParser:
    """Port of VCMI lib/texts/CLegacyConfigParser (tab separated, quoted multi-line)."""

    TAB, CR, LF, QUOTE, SPACE = 9, 13, 10, 34, 32

    def __init__(self, data: bytes, encoding: str = ENCODING):
        self.d = data
        self.p = 0
        self.end = len(data)
        self.enc = encoding

    def _eol(self, p: int) -> bool:
        # only an LF preceded by CR ends a line (some texts have bare LF inside)
        return p >= self.end or (self.d[p] == self.LF and p > 0 and self.d[p - 1] == self.CR)

    def _quoted_part(self) -> bytes:
        self.p += 1
        b = self.p
        while self.p < self.end and self.d[self.p] not in (self.QUOTE, self.TAB, self.CR):
            self.p += 1
        ret = self.d[b:self.p]
        if self.p < self.end and self.d[self.p] == self.QUOTE:
            self.p += 1
        return ret

    def _quoted(self) -> bytes:
        ret = b""
        while True:
            ret += self._quoted_part()
            if self.p < self.end and self.d[self.p] == self.QUOTE:
                ret += b'"'  # doubled quote
            elif self.p < self.end and self.d[self.p] not in (self.TAB, self.CR):
                b = self.p
                while self.p < self.end and self.d[self.p] not in (self.TAB, self.CR, self.QUOTE):
                    self.p += 1
                ret += self.d[b:self.p]
                if self.p >= self.end or self.d[self.p] != self.QUOTE:
                    return ret
            else:
                return ret

    def raw(self) -> bytes:
        if self._eol(self.p):
            return b""
        if self.d[self.p] == self.QUOTE:
            r = self._quoted()
        else:
            b = self.p
            while self.p < self.end and self.d[self.p] not in (self.TAB, self.CR):
                self.p += 1
            r = self.d[b:self.p]
        self.p += 1
        return r

    def str(self) -> str:
        return self.raw().decode(self.enc, "replace")

    def num(self):
        s = self.raw().decode("latin-1").strip().replace(",", ".")
        m = re.match(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", s)
        if not m:
            return 0
        v = float(m.group(0))
        return int(v) if v.is_integer() else v

    def nums(self, n: int) -> list:
        return [self.num() for _ in range(n)]

    def next_empty(self) -> bool:
        q = self.p
        while q < self.end and self.d[q] == self.SPACE:
            q += 1
        return self._eol(q) or self.d[q] in (self.CR, self.TAB)

    def end_line(self) -> bool:
        while not self._eol(self.p):
            self.raw()
        self.p += 1
        return self.p < self.end

    def skip(self, n: int):
        for _ in range(n):
            self.end_line()

    def at_end(self) -> bool:
        return self.p >= self.end


def read_lines(data: bytes, encoding: str = ENCODING) -> list[str]:
    """One string per line (first field), like VCMI's readToVector."""
    p = LegacyParser(data, encoding)
    out = []
    while True:
        out.append(p.str())
        if not p.end_line():
            break
    return out


def load_json5(path: str | Path):
    """json.loads for VCMI configs (// and /* */ comments, trailing commas)."""
    text = Path(path).read_text(encoding="utf-8-sig")
    bs = "\\"

    def scan(s, on_char):
        out, i, n, ins = [], 0, len(s), False
        while i < n:
            c = s[i]
            if ins:
                out.append(c)
                if c == bs and i + 1 < n:
                    out.append(s[i + 1])
                    i += 2
                    continue
                if c == '"':
                    ins = False
                i += 1
                continue
            if c == '"':
                ins = True
                out.append(c)
                i += 1
                continue
            i = on_char(s, i, out)
        return "".join(out)

    def comments(s, i, out):
        if s.startswith("//", i):
            j = s.find("\n", i)
            return len(s) if j < 0 else j
        if s.startswith("/*", i):
            j = s.find("*/", i + 2)
            return len(s) if j < 0 else j + 2
        out.append(s[i])
        return i + 1

    def commas(s, i, out):
        if s[i] == ",":
            j = i + 1
            while j < len(s) and s[j] in " \t\r\n":
                j += 1
            if j < len(s) and s[j] in "}]":
                return i + 1
        out.append(s[i])
        return i + 1

    return json.loads(scan(scan(text, comments), commas))


# =========================================================================== sources

class GameText:
    """Text files from the LODs: SoD (H3bitmap.lod) overrides AB (H3ab_bmp.lod)."""

    def __init__(self, game_dir: str | Path):
        data = Path(game_dir) / "Data"
        self.lods = []
        for name in ("H3bitmap.lod", "H3ab_bmp.lod"):  # priority order
            p = data / name
            if p.exists():
                self.lods.append((name, Lod(p)))
        if not self.lods:
            raise FileNotFoundError(f"no H3bitmap.lod / H3ab_bmp.lod under {data}")
        self.used: dict[str, str] = {}

    def get(self, name: str) -> bytes:
        for lod_name, lod in self.lods:
            if name.upper() in lod.entries:
                self.used[name.upper()] = lod_name
                return lod.read(name)
        raise KeyError(name)

    def get_from(self, lod_name: str, name: str) -> bytes | None:
        for n, lod in self.lods:
            if n == lod_name and name.upper() in lod.entries:
                self.used.setdefault(name.upper(), lod_name)
                return lod.read(name)
        return None

    def parser(self, name: str) -> LegacyParser:
        return LegacyParser(self.get(name))


class Vcmi:
    """Identifier lookups from VCMI's config directory."""

    def __init__(self, vcmi_dir: str | Path):
        self.cfg = Path(vcmi_dir) / "config"
        self._cache: dict[str, dict] = {}
        self.ok = self.cfg.exists()

    def entries(self, pattern: str) -> dict[str, dict]:
        """Merged top-level objects of config files matching ``pattern``."""
        if pattern in self._cache:
            return self._cache[pattern]
        merged: dict[str, dict] = {}
        if self.ok:
            for f in sorted(self.cfg.glob(pattern)):
                d = load_json5(f)
                for k, v in d.items():
                    if isinstance(v, dict):
                        v = dict(v)
                        v["_file"] = f.relative_to(self.cfg).as_posix()
                        merged[k] = v
        self._cache[pattern] = merged
        return merged

    def by_index(self, pattern: str) -> dict[int, tuple[str, dict]]:
        out = {}
        for k, v in self.entries(pattern).items():
            if isinstance(v.get("index"), int):
                out.setdefault(v["index"], (k, v))
        return out

    def obj_enum(self) -> dict[int, str]:
        """Obj enum (MapObjectBaseID) from lib/constants/EntityIdentifiers.h."""
        p = self.cfg.parent / "lib" / "constants" / "EntityIdentifiers.h"
        if not p.exists():
            return {}
        text = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"class MapObjectBaseID.*?enum Type\s*\{(.*?)\};", text, re.S)
        out: dict[int, str] = {}
        if m:
            for name, val in re.findall(r"(\w+)\s*=\s*(-?\d+)", m.group(1)):
                out.setdefault(int(val), name)
        return out

    def game_config(self) -> dict:
        p = self.cfg / "gameConfig.json"
        return load_json5(p) if p.exists() else {}


# =========================================================================== table builders

def _strip_title(desc: str) -> str:
    """'{Title}\\n\\nBody' -> 'Body' (H3 rich-text headers)."""
    return re.sub(r"^\{[^}]*\}\s*", "", desc).strip()


def _faction_names(gt: GameText) -> list[str]:
    return read_lines(gt.get("TOWNTYPE.TXT"))


def build_creatures(gt: GameText, vc: Vcmi, factions_zh: list[str]) -> list[dict]:
    p = gt.parser("CRTRAITS.TXT")
    p.end_line()  # header line 1
    if p.str() != "Singular" or p.str() != "Plural":
        raise ValueError("unexpected CRTRAITS.TXT header")
    three_names = p.str() == "Plural2"
    p.end_line()
    vmap = vc.by_index("creatures/*.json")
    out = []
    for i in range(N_CREATURES):
        while p.next_empty():
            if not p.end_line():
                break
        if p.at_end():
            break
        rec = {"id": i}
        rec["name"] = p.str()
        if three_names:
            p.str()
        rec["name_plural"] = p.str()
        cost = {r: p.num() for r in RESOURCE_NAMES}
        rec["fight_value"] = p.num()
        rec["ai_value"] = p.num()
        rec["growth"] = p.num()
        rec["horde_growth"] = p.num()
        rec["hp"] = p.num()
        rec["speed"] = p.num()
        rec["attack"] = p.num()
        rec["defense"] = p.num()
        rec["damage"] = [p.num(), p.num()]
        rec["shots"] = p.num()
        rec["spell_points"] = p.num()
        rec["adv_map_amount"] = [p.num(), p.num()]
        rec["ability_text"] = p.str()
        rec["attributes"] = p.str()
        rec["cost"] = {k: v for k, v in cost.items() if v}
        p.end_line()
        vid, ventry = vmap.get(i, (None, {}))
        rec["vcmi"] = vid
        rec["level"] = ventry.get("level")
        fac = ventry.get("faction")
        rec["faction"] = fac
        if fac in FACTIONS:
            rec["faction_name"] = factions_zh[FACTIONS.index(fac)]
        elif fac == "neutral":
            rec["faction_name"] = "中立"
        if ventry.get("upgrades"):
            rec["upgrades"] = ventry["upgrades"]
        if ventry.get("special"):
            rec["special"] = True
        if ventry.get("compatibilityIdentifiers"):
            rec["compat_ids"] = ventry["compatibilityIdentifiers"]
        out.append(rec)
    # upgrade-of links + numeric upgrade ids
    by_vcmi = {c["vcmi"]: c for c in out if c["vcmi"]}
    for c in out:
        ups = c.get("upgrades")
        if ups:
            c["upgrade_ids"] = [by_vcmi[u]["id"] for u in ups if u in by_vcmi]
            for u in ups:
                if u in by_vcmi:
                    by_vcmi[u].setdefault("upgrade_of", []).append(c["id"])
    return out


def build_artifacts(gt: GameText, vc: Vcmi) -> list[dict]:
    p = gt.parser("ARTRAITS.TXT")
    ev = gt.parser("ARTEVENT.TXT")
    p.end_line()
    for _ in range(7):
        p.str()
    is_roe = p.str() != "Misc 5"
    p.end_line()
    slots = [s for s in ART_SLOTS if not (is_roe and s == "MISC5")]
    vmap = vc.by_index("artifacts.json")
    out = []
    for i in range(N_ARTIFACTS):
        if p.at_end():
            break
        rec = {"id": i}
        rec["name"] = p.str()
        rec["event_text"] = ev.str()
        rec["cost"] = p.num()
        rec["slots"] = [s for s in slots if p.str() == "x"]
        cls = p.str()
        rec["class"] = ART_CLASSES.get(cls[:1], cls)
        rec["description"] = p.str()
        rec["description_plain"] = _strip_title(rec["description"])
        p.end_line()
        ev.end_line()
        vid, ventry = vmap.get(i, (None, {}))
        rec["vcmi"] = vid
        if ventry.get("type"):
            rec["type"] = ventry["type"]
        if ventry.get("components"):
            rec["components"] = ventry["components"]
        if ventry.get("warMachine"):
            rec["war_machine"] = ventry["warMachine"]
        out.append(rec)
    by_vcmi = {a["vcmi"]: a for a in out if a["vcmi"]}
    for a in out:
        if a.get("components"):
            a["component_ids"] = [by_vcmi[c]["id"] for c in a["components"] if c in by_vcmi]
    return out


def build_hero_classes(gt: GameText, vc: Vcmi, factions_zh: list[str]) -> list[dict]:
    p = gt.parser("HCTRAITS.TXT")
    p.skip(2)
    vmap = vc.by_index("heroClasses.json")
    out = []
    for i in range(N_HERO_CLASSES):
        rec = {"id": i, "name": p.str(), "aggression": p.num()}
        rec["primary_skills"] = dict(zip(PRIMARY_SKILLS, p.nums(4)))
        rec["primary_chance_low"] = dict(zip(PRIMARY_SKILLS, p.nums(4)))
        rec["primary_chance_high"] = dict(zip(PRIMARY_SKILLS, p.nums(4)))
        rec["secondary_chance"] = dict(zip(SECONDARY_SKILLS, p.nums(len(SECONDARY_SKILLS))))
        rec["tavern_chance"] = dict(zip(FACTIONS, p.nums(len(FACTIONS))))
        p.end_line()
        vid, ventry = vmap.get(i, (None, {}))
        rec["vcmi"] = vid
        fac = ventry.get("faction")
        rec["faction"] = fac
        if fac in FACTIONS:
            rec["faction_name"] = factions_zh[FACTIONS.index(fac)]
        rec["affinity"] = ventry.get("affinity")
        out.append(rec)
    return out


def build_heroes(gt: GameText, vc: Vcmi, classes: list[dict], creatures: list[dict]) -> list[dict]:
    spec = gt.parser("HEROSPEC.TXT")
    bios = gt.parser("HEROBIOS.TXT")
    p = gt.parser("HOTRAITS.TXT")
    p.skip(2)
    spec.skip(2)
    vmap = vc.by_index("heroes/*.json")
    cls_by_vcmi = {c["vcmi"]: c for c in classes if c["vcmi"]}
    cre_by_ref = {}
    for c in creatures:
        for ident in [c["vcmi"]] + c.get("compat_ids", []):
            if ident:
                cre_by_ref[ident.lower()] = c
    out = []
    for i in range(N_HEROES):
        rec = {"id": i, "name": p.str()}
        rec["biography"] = bios.str()
        rec["specialty_name"] = spec.str()
        rec["specialty_tooltip"] = spec.str()
        rec["specialty_description"] = spec.str()
        army = []
        for _ in range(3):
            lo, hi, ref = p.num(), p.num(), p.str()
            ref_id = ref.replace(" ", "")
            ref_id = ref_id[:1].lower() + ref_id[1:]
            c = cre_by_ref.get(ref_id.lower())
            slot = {"min": lo, "max": hi, "ref": ref}
            if c:
                slot.update(creature=c["vcmi"], creature_id=c["id"], creature_name=c["name"])
            army.append(slot)
        rec["army"] = army
        p.end_line()
        spec.end_line()
        bios.end_line()
        vid, ventry = vmap.get(i, (None, {}))
        rec["vcmi"] = vid
        hc = ventry.get("class")
        rec["class"] = hc
        if hc in cls_by_vcmi:
            c = cls_by_vcmi[hc]
            rec["class_id"] = c["id"]
            rec["class_name"] = c["name"]
            rec["faction"] = c.get("faction")
            rec["faction_name"] = c.get("faction_name")
        rec["female"] = bool(ventry.get("female", False))
        if ventry.get("special"):
            rec["special"] = True  # campaign-only hero
        if ventry.get("skills"):
            rec["skills"] = ventry["skills"]
        if ventry.get("spellbook"):
            rec["spellbook"] = ventry["spellbook"]
        if ventry.get("specialty"):
            rec["specialty"] = ventry["specialty"]
        out.append(rec)
    return out


def build_spells(gt: GameText, vc: Vcmi) -> list[dict]:
    p = gt.parser("SPTRAITS.TXT")
    vmap = vc.by_index("spells/*.json")
    out: list[dict] = []

    def read(kind: str):
        while True:
            rec = {"id": len(out), "type": kind, "name": p.str(), "abbr": p.str(),
                   "level": int(p.num())}
            rec["schools"] = [s for s in ("earth", "water", "fire", "air") if p.str() == "x"]
            rec["cost"] = p.nums(4)
            rec["power"] = p.num()
            rec["effect"] = p.nums(4)
            rec["gain_chance"] = dict(zip(FACTIONS, p.nums(len(FACTIONS))))
            rec["ai_value"] = p.nums(4)
            rec["descriptions"] = dict(zip(SPELL_LEVEL_NAMES, [p.str() for _ in range(4)]))
            rec["attributes"] = p.str()
            vid, _ = vmap.get(rec["id"], (None, {}))
            rec["vcmi"] = vid
            out.append(rec)
            if not (p.end_line() and not p.next_empty()):
                break

    p.skip(5)
    read("adventure")
    p.skip(3)
    read("combat")
    p.skip(3)
    read("ability")
    return out


def build_skills(gt: GameText, vc: Vcmi) -> list[dict]:
    p = gt.parser("SSTRAITS.TXT")
    p.skip(2)
    vmap = vc.by_index("skills.json")
    out = []
    while True:
        name = p.str()
        descs = [p.str() for _ in range(3)]
        i = len(out)
        vid = vmap.get(i, (SECONDARY_SKILLS[i] if i < len(SECONDARY_SKILLS) else None, {}))[0]
        out.append({"id": i, "vcmi": vid, "name": name,
                    "descriptions": dict(zip(["basic", "advanced", "expert"], descs))})
        if not p.end_line():
            break
    return [s for s in out if s["name"]]


def build_towns(gt: GameText, vc: Vcmi, factions_zh: list[str], classes: list[dict],
                creatures: list[dict]) -> list[dict]:
    n = len(FACTIONS)
    town_names = LegacyParser(gt.get("TOWNNAME.TXT"))
    buildings: list[dict[int, dict]] = [dict() for _ in range(n)]

    def setb(t, bid, name, desc):
        buildings[t][bid] = {"name": name, "description": desc}

    p = gt.parser("BLDGNEUT.TXT")
    for bid in range(15):
        name, desc = p.str(), p.str()
        p.end_line()
        for t in range(n):
            setb(t, bid, name, desc)
    p.skip(3)  # silo, blacksmith, moat (unused)
    name, desc = p.str(), p.str()
    p.end_line()
    for t in range(n):
        setb(t, 20, name, desc)  # shipyard with ship
    for t in range(n):
        setb(t, 16, p.str(), p.str())  # blacksmith
        p.end_line()
    p = gt.parser("BLDGSPEC.TXT")
    for t in range(n):
        for b in range(9):
            setb(t, 17 + b, p.str(), p.str())
            p.end_line()
        setb(t, 26, p.str(), p.str())  # grail
        p.end_line()
        setb(t, 15, p.str(), p.str())  # resource silo
        p.end_line()
    p = gt.parser("DWELLING.TXT")
    for t in range(n):
        for b in range(14):
            setb(t, 30 + b, p.str(), p.str())
            p.end_line()

    vfac = vc.entries("factions/*.json")
    gcfg = vc.game_config().get("settings", {}).get("mapFormat", {})
    common_h3m = gcfg.get("restorationOfErathia", {}).get("buildingsCommon", {})
    special_h3m = {}
    for fmt in ("restorationOfErathia", "armageddonsBlade", "shadowOfDeath"):
        for fac, bmap in gcfg.get(fmt, {}).get("buildings", {}).items():
            special_h3m.setdefault(fac, {}).update(bmap)

    out = []
    for t in range(n + 1):
        fac = FACTIONS[t] if t < n else "neutral"
        ventry = vfac.get(fac, {})
        rec = {"id": t, "vcmi": fac, "name": factions_zh[t] if t < n else "中立"}
        if t < n:
            names = []
            for _ in range(16):
                names.append(town_names.str())
                town_names.end_line()
            rec["town_names"] = names
        rec["native_terrain"] = ventry.get("nativeTerrain")
        rec["alignment"] = ventry.get("alignment")
        rec["hero_classes"] = [{"id": c["id"], "vcmi": c["vcmi"], "name": c["name"]}
                               for c in classes if c.get("faction") == fac]
        rec["creatures"] = [{"id": c["id"], "vcmi": c["vcmi"], "name": c["name"], "level": c["level"]}
                            for c in creatures if c.get("faction") == fac]
        if t < n:
            rec["buildings"] = [
                {"h3_id": bid, "vcmi": BUILDING_NAMES[bid], **buildings[t][bid]}
                for bid in sorted(buildings[t])]
            zh_by_vcmi = {b["vcmi"]: b["name"] for b in rec["buildings"]}
            h3m = {}
            for bname, idx in common_h3m.items():
                h3m[idx] = bname
            for bname, idx in special_h3m.get(fac, {}).items():
                h3m[idx] = bname
            rec["h3m_buildings"] = [{"h3m_id": idx, "vcmi": b, "name": zh_by_vcmi.get(b)}
                                    for idx, b in sorted(h3m.items())]
        out.append(rec)
    return out


def build_resources(gt: GameText, vc: Vcmi) -> list[dict]:
    names = read_lines(gt.get("RESTYPES.TXT"))
    mines = read_lines(gt.get("MINENAME.TXT"))
    vmap = vc.by_index("resources.json")
    out = []
    for i, name in enumerate(names):
        if not name:
            continue
        vid, ventry = vmap.get(i, (RESOURCE_NAMES[i] if i < 7 else None, {}))
        rec = {"id": i, "vcmi": vid, "name": name}
        if i < len(mines) and mines[i]:
            rec["mine_name"] = mines[i]
        if "price" in ventry:
            rec["price"] = ventry["price"]
        out.append(rec)
    return out


def build_terrains(gt: GameText, vc: Vcmi) -> list[dict]:
    names = read_lines(gt.get("TERRNAME.TXT"))
    vmap = vc.by_index("terrains.json")
    out = []
    for i, name in enumerate(names):
        if not name:
            continue
        vid, ventry = vmap.get(i, (None, {}))
        out.append({"id": i, "vcmi": vid, "name": name,
                    "short": ventry.get("shortIdentifier"),
                    "move_cost": ventry.get("moveCost"),
                    "tiles_def": ventry.get("tiles"),
                    "river": ventry.get("river"),
                    "type": ventry.get("type")})
    return out


def _simple_vcmi_list(vc: Vcmi, fname: str, zh: dict[int, str]) -> list[dict]:
    out = [{"id": 0, "vcmi": None, "name": zh.get(0, "无"), "text": "none"}]
    for idx, (k, v) in sorted(vc.by_index(fname).items()):
        out.append({"id": idx, "vcmi": k, "name": zh.get(idx), "text": v.get("text"),
                    "short": v.get("shortIdentifier"), "tiles_def": v.get("tilesFilename")})
    return out


# Chinese names as shown in the SoD map editor tool tips (EDITRCMD.TXT)
RIVERS_ZH = {0: "无", 1: "清澈河流", 2: "冰川河流", 3: "浑浊河流", 4: "熔岩河流"}
ROADS_ZH = {0: "无", 1: "泥土道路", 2: "砂砾道路", 3: "鹅卵石道路"}


def build_objects(gt: GameText, vc: Vcmi, creatures, artifacts, towns, resources, heroes,
                  classes) -> list[dict]:
    names = read_lines(gt.get("OBJNAMES.TXT"))
    crgen1 = read_lines(gt.get("CRGEN1.TXT"))
    crgen4 = read_lines(gt.get("CRGEN4.TXT"))
    mines = read_lines(gt.get("MINENAME.TXT"))
    enum = vc.obj_enum()
    vobjs = vc.entries("objects/*.json")
    vby_index: dict[int, tuple[str, dict]] = {}
    for k, v in vobjs.items():
        if isinstance(v.get("index"), int):
            vby_index.setdefault(v["index"], (k, v))
    out = []
    for i in range(256):
        rec = {"id": i, "name": names[i] if i < len(names) else ""}
        vid, ventry = vby_index.get(i, (None, {}))
        rec["vcmi"] = vid
        rec["enum"] = enum.get(i)
        subs = {}
        for sk, sv in (ventry.get("types") or {}).items():
            if isinstance(sv, dict) and isinstance(sv.get("index"), int):
                subs[sv["index"]] = {"vcmi": sk}
        # Chinese names of subtypes where the meaning of subclass is known
        if i == 17:
            for j, nm in enumerate(crgen1):
                subs.setdefault(j, {})["name"] = nm
        elif i == 20:
            for j, nm in enumerate(crgen4):
                subs.setdefault(j, {})["name"] = nm
        elif i in (53, 220):  # mine / abandoned mine
            for j, nm in enumerate(mines):
                if nm:
                    subs.setdefault(j, {})["name"] = nm
        elif i == 79:
            for r in resources:
                subs.setdefault(r["id"], {}).update(name=r["name"], vcmi=r["vcmi"])
        if subs:
            rec["subtypes"] = [{"id": k, **v} for k, v in sorted(subs.items())]
        rec["subtype_meaning"] = {
            5: "artifact id", 54: "creature id", 98: "faction id", 34: "hero class id",
            17: "dwelling (CRGEN1.TXT line)",
            20: "dwelling (CRGEN4.TXT line)", 53: "resource id", 79: "resource id",
            218: "faction id", 217: "creature level"}.get(i)
        if rec["subtype_meaning"] is None:
            del rec["subtype_meaning"]
        out.append(rec)
    return out


def subtype_name(db: dict, cls: int, sub: int) -> str | None:
    """Chinese name of an object's subclass, when it has a meaning."""
    def by_id(kind, i):
        for r in db.get(kind, []):
            if r["id"] == i:
                return r.get("name")
        return None

    if cls == 54:
        return by_id("creatures", sub)
    if cls == 5:
        return by_id("artifacts", sub)
    if cls in (98, 218):
        return by_id("towns", sub)
    if cls == 34:
        return by_id("hero_classes", sub)
    for o in db.get("objects", []):
        if o["id"] == cls:
            for s in o.get("subtypes", []):
                if s["id"] == sub:
                    return s.get("name")
    return None


def subtype_vcmi(db: dict, cls: int, sub: int) -> str | None:
    """VCMI identifier of an object's subclass (creature/artifact/faction/... id)."""
    kind = {54: "creatures", 5: "artifacts", 98: "towns", 218: "towns",
            34: "hero_classes"}.get(cls)
    if kind:
        for r in db.get(kind, []):
            if r["id"] == sub:
                return r.get("vcmi")
        return None
    for o in db.get("objects", []):
        if o["id"] == cls:
            for s in o.get("subtypes", []):
                if s["id"] == sub:
                    return s.get("vcmi")
    return None


def _mask_terrains(mask: str) -> list[int]:
    """9-char terrain mask: char[8 - t] == '1' -> terrain t allowed (water leftmost)."""
    return [t for t in range(9) if len(mask) == 9 and mask[8 - t] == "1"]


def _tiles(mask: str, want: str) -> list[list[int]]:
    """48-char 6x8 mask -> [dx, dy] offsets from the anchor tile.

    The mask is stored mirrored: character ``row * 8 + col`` is the tile at
    offset (-col, -row) from the object's position (its bottom-right tile),
    exactly as VCMI's ObjectTemplate::readTxt / getBlockedOffsets use it.
    """
    return [[-j, -i] for i in range(6) for j in range(8) if mask[i * 8 + j] == want]


# OBJECTS.TXT field 8: map editor palette the object is listed under
EDITOR_GROUPS = {0: "objects", 1: "towns", 2: "monsters", 3: "heroes", 4: "artifacts",
                 5: "treasure"}
# Hero masks as the SoD editor writes them into maps (binary FF FF FF FF FF BF /
# 00 00 00 00 00 40 = one tile at offset (-1, 0): an H3M hero's x is one tile
# right of the tile it stands on). Text form, same convention as OBJECTS.TXT.
HERO_PASSABILITY = "10" + "1" * 46
HERO_VISITABILITY = "01" + "0" * 46


def hero_templates() -> list[dict]:
    """Heroes (class 34, subclass = hero class) and random hero (70) are not in
    OBJECTS.TXT; the editor synthesises them. Values as found in the original
    campaign maps (def names are lower case there)."""
    lines = [f"ah{c:02d}_e.def {HERO_PASSABILITY} {HERO_VISITABILITY} 011111111 011111111 34 {c} 3 0"
             for c in range(N_HERO_CLASSES)]
    lines.append(f"ahrandom.def {HERO_PASSABILITY} {HERO_VISITABILITY} 011111111 011111111 70 0 3 0")
    data = (f"{len(lines)}\r\n" + "\r\n".join(lines) + "\r\n").encode("ascii")
    out = parse_templates(data, "synthetic: SoD editor hero template")
    for t in out:
        t["synthetic"] = True
    return out


def parse_templates(data: bytes, source: str) -> list[dict]:
    """OBJECTS.TXT / ZOBJCTS.TXT / OBJTMPLT.TXT -> template records.

    Same layout as VCMI ObjectTemplate::readTxt: first line = count, then per line
    9 space-separated fields: def, passability (6x8, 0 = blocked), visitability
    (6x8, 1 = visitable), allowed terrains (9), editor terrain group (9), class,
    subclass, editor group, overlay flag (VCMI: printPriority).
    """
    p = LegacyParser(data)
    count = int(p.num())
    p.end_line()
    out = []
    for n in range(count):
        line = p.str()
        p.end_line()
        f = line.split(" ")
        if len(f) < 9:
            continue
        rec = {
            "def": f[0], "class": int(f[5]), "subclass": int(f[6]),
            "passability": f[1], "visitability": f[2],
            "allowed_terrains_mask": f[3], "editor_terrains_mask": f[4],
            "editor_group": int(f[7]), "overlay": int(f[8]),
        }
        rec["editor_group_name"] = EDITOR_GROUPS.get(rec["editor_group"])
        rec["allowed_terrains"] = _mask_terrains(f[3])
        rec["editor_terrains"] = _mask_terrains(f[4])
        rec["blocked"] = _tiles(f[1], "0")
        rec["visitable"] = _tiles(f[2], "1")
        occupied = {tuple(t) for t in rec["blocked"]} | {tuple(t) for t in rec["visitable"]}
        if occupied:
            rec["width"] = 1 - min(t[0] for t in occupied)
            rec["height"] = 1 - min(t[1] for t in occupied)
        else:
            rec["width"] = rec["height"] = 1
        rec["source"] = source
        rec["line"] = n + 1
        out.append(rec)
    return out


def build_templates(gt: GameText) -> tuple[list[dict], dict]:
    """SoD OBJECTS.TXT, plus AB-only lines, OBJTMPLT.TXT extras and the
    synthetic hero templates; returns (templates, diff)."""
    sod_raw = gt.get_from("H3bitmap.lod", "OBJECTS.TXT")
    ab_raw = gt.get_from("H3ab_bmp.lod", "OBJECTS.TXT")
    base = parse_templates(sod_raw if sod_raw is not None else gt.get("OBJECTS.TXT"),
                           "H3bitmap.lod/OBJECTS.TXT")

    def key(t):
        return (t["def"].lower(), t["class"], t["subclass"])

    def full(t):
        return tuple(t[k] for k in ("def", "passability", "visitability", "allowed_terrains_mask",
                                    "editor_terrains_mask", "class", "subclass", "editor_group",
                                    "overlay"))

    diff = {"sod_count": len(base)}
    out = list(base)
    seen = {key(t) for t in base}
    if ab_raw is not None:
        ab = parse_templates(ab_raw, "H3ab_bmp.lod/OBJECTS.TXT")
        sod_full = {key(t): full(t) for t in base}
        ab_keys = {key(t) for t in ab}
        diff["ab_count"] = len(ab)
        diff["sod_only"] = sorted({t["def"] for t in base if key(t) not in ab_keys})
        ab_only = [t for t in ab if key(t) not in seen]
        diff["ab_only"] = sorted({t["def"] for t in ab_only})
        diff["changed"] = sorted({t["def"] for t in ab if key(t) in sod_full
                                  and sod_full[key(t)] != full(t)})
        for t in ab_only:
            t["ab_only"] = True
            out.append(t)
            seen.add(key(t))
    for lod_name in ("H3bitmap.lod", "H3ab_bmp.lod"):
        raw = gt.get_from(lod_name, "OBJTMPLT.TXT")
        if raw is None:
            continue
        extra = parse_templates(raw, f"{lod_name}/OBJTMPLT.TXT")
        diff.setdefault("objtmplt", sorted({t["def"] for t in extra}))
        for t in extra:
            if key(t) not in seen:
                out.append(t)
                seen.add(key(t))
    for t in hero_templates():
        if key(t) not in seen:
            out.append(t)
            seen.add(key(t))
    return out, diff


def build_campaign_maps(gt: GameText) -> list[dict]:
    """CAMPTEXT.TXT: campaign-map (region map) names and their region labels, ids 1..20."""
    sections: list[list[str]] = []
    for line in read_lines(gt.get("CAMPTEXT.TXT")):
        if line.startswith("//"):
            sections.append([])
        elif line and sections:
            sections[-1].append(line)
    if not sections:
        return []
    names, regions = sections[0], sections[1:]
    return [{"id": i + 1, "name": n, "regions": regions[i] if i < len(regions) else []}
            for i, n in enumerate(names)]


def build_misc(gt: GameText) -> dict:
    edit = read_lines(gt.get("CMPEDITR.TXT"))

    def pick(*words):  # campaign editor strings, located by content
        return [w if w in edit else None for w in words]

    return {
        "campaign_maps": build_campaign_maps(gt),
        "campaign_difficulties": pick("容易", "一般", "较难", "专家", "极难"),
        "campaign_special_heroes": dict(zip(["-3", "-2", "-1"],
                                            pick("最强大的英雄", "生成的英雄", "随机英雄"))),
        "player_colors": read_lines(gt.get("PLCOLORS.TXT")),
        "player_colors_vcmi": ["red", "blue", "tan", "green", "orange", "purple", "teal", "pink"],
        "primary_skills": read_lines(gt.get("PRISKILL.TXT")),
        "primary_skills_vcmi": PRIMARY_SKILLS,
        "skill_levels": read_lines(gt.get("SKILLLEV.TXT"))[:3],
        "skill_levels_vcmi": ["basic", "advanced", "expert"],
        "difficulties_vcmi": ["pawn", "knight", "rook", "queen", "king"],
    }


def table_differences(gt: GameText, names: list[str]) -> dict:
    """Per text file: which LOD supplied it and how many lines differ from the AB copy."""
    out = {}
    for n in names:
        sod = gt.get_from("H3bitmap.lod", n)
        ab = gt.get_from("H3ab_bmp.lod", n)
        rec = {"used": gt.used.get(n.upper())}
        if sod is not None and ab is not None:
            a, b = sod.split(b"\r\n"), ab.split(b"\r\n")
            rec["sod_lines"], rec["ab_lines"] = len(a), len(b)
            rec["identical"] = sod == ab
            if sod != ab:
                rec["lines_only_in_sod"] = len(set(a) - set(b))
                rec["lines_only_in_ab"] = len(set(b) - set(a))
        out[n] = rec
    return out


# =========================================================================== build all

def __getattr__(name: str):
    if name == "DEFAULT_GAME":  # older scripts: the original game folder, looked up on use
        return config.game_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_all(game_dir=None, vcmi_dir=DEFAULT_VCMI) -> dict:
    gt = GameText(game_dir or config.game_dir())
    vc = Vcmi(vcmi_dir)
    factions_zh = _faction_names(gt)
    creatures = build_creatures(gt, vc, factions_zh)
    artifacts = build_artifacts(gt, vc)
    classes = build_hero_classes(gt, vc, factions_zh)
    heroes = build_heroes(gt, vc, classes, creatures)
    spells = build_spells(gt, vc)
    skills = build_skills(gt, vc)
    towns = build_towns(gt, vc, factions_zh, classes, creatures)
    resources = build_resources(gt, vc)
    terrains = build_terrains(gt, vc)
    rivers = _simple_vcmi_list(vc, "rivers.json", RIVERS_ZH)
    roads = _simple_vcmi_list(vc, "roads.json", ROADS_ZH)
    objects = build_objects(gt, vc, creatures, artifacts, towns, resources, heroes, classes)
    templates, tdiff = build_templates(gt)
    db = {"creatures": creatures, "artifacts": artifacts, "heroes": heroes,
          "hero_classes": classes, "spells": spells, "skills": skills, "towns": towns,
          "resources": resources, "terrains": terrains, "rivers": rivers, "roads": roads,
          "objects": objects, "templates": templates, "misc": build_misc(gt)}
    # annotate templates & object template counts
    tcount: dict[int, int] = {}
    obj_name = {o["id"]: o["name"] for o in objects}
    obj_vcmi = {o["id"]: o["vcmi"] for o in objects}
    for t in templates:
        tcount[t["class"]] = tcount.get(t["class"], 0) + 1
        t["class_name"] = obj_name.get(t["class"])
        t["class_vcmi"] = obj_vcmi.get(t["class"])
        sn = subtype_name(db, t["class"], t["subclass"])
        if sn:
            t["subclass_name"] = sn
        sv = subtype_vcmi(db, t["class"], t["subclass"])
        if sv and sv != "object":
            t["subclass_vcmi"] = sv
    for o in objects:
        o["template_count"] = tcount.get(o["id"], 0)
    used_files = ["CRTRAITS.TXT", "ARTRAITS.TXT", "ARTEVENT.TXT", "HOTRAITS.TXT", "HEROBIOS.TXT",
                  "HEROSPEC.TXT", "HCTRAITS.TXT", "SPTRAITS.TXT", "SSTRAITS.TXT", "TOWNTYPE.TXT",
                  "TOWNNAME.TXT", "BLDGNEUT.TXT", "BLDGSPEC.TXT", "DWELLING.TXT", "RESTYPES.TXT",
                  "MINENAME.TXT", "TERRNAME.TXT", "OBJNAMES.TXT", "CRGEN1.TXT", "CRGEN4.TXT",
                  "OBJECTS.TXT", "OBJTMPLT.TXT", "PLCOLORS.TXT", "PRISKILL.TXT", "SKILLLEV.TXT"]
    db["_sources"] = {"game_dir": str(game_dir), "vcmi_dir": str(vcmi_dir),
                      "files": table_differences(gt, used_files), "templates": tdiff}
    return db


def write_db(db: dict, out_dir=DEFAULT_OUT) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for kind, data in db.items():
        name = "sources" if kind == "_sources" else kind
        p = out / f"{name}.json"
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        paths.append(p)
    return paths


# =========================================================================== lookup API

_DB: dict | None = None


def load(out_dir=DEFAULT_OUT, rebuild: bool = False) -> dict:
    """Load out/gamedb/*.json (building it from the game data if missing)."""
    global _DB
    if _DB is not None and not rebuild:
        return _DB
    out = Path(out_dir)
    if rebuild or not (out / "creatures.json").exists():
        db = build_all()
        write_db(db, out)
        _DB = db
        return db
    db = {}
    for kind in KINDS:
        p = out / f"{kind}.json"
        if p.exists():
            db[kind] = json.loads(p.read_text(encoding="utf-8"))
    _DB = db
    return db


def get(kind: str, ident) -> dict | None:
    """Record by numeric id or VCMI identifier."""
    for r in load().get(kind, []):
        if r.get("id") == ident or (isinstance(ident, str) and r.get("vcmi") == ident):
            return r
    return None


def _match(rec: dict, q: str) -> int:
    """0 = no match; higher = better (exact Chinese/VCMI name > prefix > substring)."""
    ql = q.lower()
    best = 0
    for field in ("name", "name_plural", "vcmi", "def", "enum", "specialty_name"):
        v = rec.get(field)
        if not isinstance(v, str) or not v:
            continue
        vl = v.lower()
        if vl == ql:
            best = max(best, 3)
        elif vl.startswith(ql):
            best = max(best, 2)
        elif ql in vl:
            best = max(best, 1)
    return best


def find(query: str, kinds: list[str] | None = None, limit: int = 50) -> list[tuple[str, dict]]:
    """Search by Chinese name, VCMI id or numeric id ("kind:id" also accepted)."""
    db = load()
    kinds = kinds or [k for k in KINDS if k not in ("templates", "misc")]
    if ":" in query and query.split(":", 1)[0] in KINDS:
        k, q = query.split(":", 1)
        kinds, query = [k], q
    hits = []
    for kind in kinds:
        for r in db.get(kind, []):
            if not isinstance(r, dict):
                continue
            score = 4 if query.isdigit() and r.get("id") == int(query) and len(kinds) == 1 else _match(r, query)
            if score:
                hits.append((score, kind, r))
    hits.sort(key=lambda h: (-h[0], KINDS.index(h[1]), h[2].get("id", 0)))
    return [(k, r) for _, k, r in hits[:limit]]


def _find_kind(kind: str, query) -> list[dict]:
    if isinstance(query, int):
        r = get(kind, query)
        return [r] if r else []
    return [r for _, r in find(query, [kind])]


def find_creature(q):
    return _find_kind("creatures", q)


def find_artifact(q):
    return _find_kind("artifacts", q)


def find_hero(q):
    return _find_kind("heroes", q)


def find_spell(q):
    return _find_kind("spells", q)


def find_skill(q):
    return _find_kind("skills", q)


def find_town(q):
    return _find_kind("towns", q)


def find_object(q):
    return _find_kind("objects", q)


def terrain_id(t) -> int | None:
    if t is None or isinstance(t, int):
        return t
    if isinstance(t, str) and t.isdigit():
        return int(t)
    for r in load().get("terrains", []):
        if t in (r.get("vcmi"), r.get("name"), r.get("short")):
            return r["id"]
    raise KeyError(f"unknown terrain {t!r}")


def find_template(class_id: int, subclass_id: int | None = None, terrain=None,
                  strict: bool = False, include_ab_only: bool = False) -> list[dict]:
    """Templates of an object class (and subclass), optionally placeable on ``terrain``.

    With ``terrain`` (id, VCMI name or Chinese name), templates whose
    allowed-terrain mask (field 4) includes it are returned, those whose
    editor terrain group (field 5, which VCMI uses as allowedTerrains) contains
    it first. ``strict=True`` keeps only the editor-terrain matches, i.e. the
    graphics drawn for that terrain (snowy mountains, lava mines, ...).
    """
    tid = terrain_id(terrain)
    res = []
    for t in load().get("templates", []):
        if t["class"] != class_id or (subclass_id is not None and t["subclass"] != subclass_id):
            continue
        if t.get("ab_only") and not include_ab_only:
            continue
        if tid is not None and tid not in (t["editor_terrains"] if strict else t["allowed_terrains"]):
            continue
        res.append(t)
    if tid is not None:
        res.sort(key=lambda t: (tid not in t["editor_terrains"], len(t["editor_terrains"])))
    return res


def find_templates_by_def(def_name: str) -> list[dict]:
    d = def_name.lower()
    if not d.endswith(".def"):
        d += ".def"
    return [t for t in load().get("templates", []) if t["def"].lower() == d]


# =========================================================================== CLI

def _brief(kind: str, r: dict) -> str:
    bits = [f"[{kind}] #{r.get('id')}", r.get("name") or "", f"({r.get('vcmi')})" if r.get("vcmi") else ""]
    if kind == "creatures":
        bits.append(f"L{r.get('level')} {r.get('faction_name') or r.get('faction') or ''} "
                    f"A{r['attack']} D{r['defense']} Dmg{r['damage'][0]}-{r['damage'][1]} "
                    f"HP{r['hp']} Spd{r['speed']} Gr{r['growth']} AI{r['ai_value']} "
                    f"cost {r.get('cost')}")
    elif kind == "artifacts":
        bits.append(f"{r.get('class')} {','.join(r.get('slots', []))} cost {r.get('cost')}")
    elif kind == "heroes":
        bits.append(f"{r.get('class_name')}/{r.get('faction_name')} 专长:{r.get('specialty_name')}"
                    + (" [campaign]" if r.get("special") else ""))
    elif kind == "spells":
        bits.append(f"{r.get('type')} L{r.get('level')} {'/'.join(r.get('schools', []))} cost {r.get('cost')}")
    elif kind == "objects":
        bits.append(f"enum={r.get('enum')} templates={r.get('template_count')}")
    elif kind == "towns":
        bits.append(" ".join(c["name"] for c in r.get("creatures", [])[:14]))
    return " ".join(b for b in bits if b)


def _tmpl_brief(t: dict) -> str:
    return (f"{t['def']:14} class {t['class']:3} ({t.get('class_name')}) sub {t['subclass']:3}"
            f"{' (' + t['subclass_name'] + ')' if t.get('subclass_name') else ''} "
            f"group {t['editor_group']} {t['width']}x{t['height']} "
            f"terr {t['allowed_terrains_mask']}/{t['editor_terrains_mask']}"
            f"{' AB-only' if t.get('ab_only') else ''}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build out/gamedb/*.json")
    b.add_argument("--game", help="original game folder (default: h3_game_dir, see tools/h3tools/config.py)")
    b.add_argument("--vcmi", default=str(DEFAULT_VCMI))
    b.add_argument("--out", default=str(DEFAULT_OUT))
    f = sub.add_parser("find", help="search by Chinese name / VCMI id / number")
    f.add_argument("query")
    f.add_argument("--kind", action="append", choices=KINDS)
    f.add_argument("--json", action="store_true", help="print full records")
    t = sub.add_parser("templates", help="list templates of an object class")
    t.add_argument("cls", help="class id, or a def name")
    t.add_argument("subclass", nargs="?", type=int)
    t.add_argument("--terrain", help="terrain id / VCMI name / Chinese name")
    t.add_argument("--strict", action="store_true", help="only graphics made for that terrain")
    t.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        db = build_all(a.game, a.vcmi)
        global _DB
        _DB = db
        for p in write_db(db, a.out):
            data = json.loads(p.read_text(encoding="utf-8"))
            print(f"{p}  ({len(data)} entries)")
        return 0
    if a.cmd == "find":
        hits = find(a.query, a.kind)
        for kind, r in hits:
            print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else _brief(kind, r))
        if not hits:
            print("no match")
        return 0 if hits else 1
    if a.cmd == "templates":
        if a.cls.isdigit():
            res = find_template(int(a.cls), a.subclass, a.terrain, a.strict)
        else:
            res = find_templates_by_def(a.cls)
        for r in res:
            print(json.dumps(r, ensure_ascii=False) if a.json else _tmpl_brief(r))
        if not res:
            print("no match")
        return 0 if res else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
