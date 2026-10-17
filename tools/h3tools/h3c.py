"""Lossless reader/writer for Heroes III campaign files (.h3c).

File layout
-----------
An .h3c file is a concatenation of gzip members:

* member 0 - the campaign header (described below),
* members 1..n - complete H3M maps, one per *non-void* scenario, in scenario
  order (a scenario is void when its map file name is empty).

Header layout (little endian, strings = u32 length + bytes in the game codepage),
following VCMI ``lib/campaign/CampaignHandler.cpp``::

    u32  version            4=RoE 5=AB 6=SoD 7=Chronicles 10=HotA
    [HotA only: i32 format, (format>=2: u32 major, u32 minor, u32 patch, u8 force),
                i8 unknownB, i32 unknownC, i32 scenarioCount]
    u8   region map id      selects the campaign map picture and its region count
    str  name
    str  description
    u8   difficulty choosable   (absent in RoE)
    u8   music id
    scenario * N            N = region count of the map (see REGION_MAPS)

    scenario:
      str  map file name     (empty = void scenario, no embedded map)
      u32  packed map size   (= size of the scenario's gzip member)
      u8/u16/u32 preconditions bitmask (u8 for <=8 scenarios, u16 <=16, u32 above)
      u8   region color
      u8   default difficulty
      str  region text
      prolog, [HotA: prolog2, prolog3], epilog, [HotA: epilog2, epilog3]:
           u8 has; if has: u8 video id, u8 music id, str text
      travel options:
           u8  what hero keeps (bit0 experience, 1 primary, 2 secondary, 3 spells, 4 artifacts)
           19 bytes creatures kept (bitmask)            [HotA: 24, or u32 count + bits]
           17 (RoE/AB) / 18 (SoD+) bytes artifacts kept [HotA: 21, or u32 count + bits]
           u8  start options (0 none, 1 bonus, 2 crossover, 3 hero)
           if bonus: u8 player color
           if not none: u8 count, then bonuses (layout depends on start option)

Dictionary model
----------------
``parse(raw)`` returns a JSON-serialisable dict::

    {"format": "h3c",
     "encoding": "gbk",               # text codepage (cp1252 for English files)
     "header": {...},                 # parse_header() output
     "maps":   [b64 | None, ...],     # decompressed H3M per scenario, aligned with
                                      # header["scenarios"]; None for void scenarios
     "extra_maps": [b64, ...],        # only if the file holds surplus members
     "trailing": "hex"}               # only if there are undecodable bytes at the end

Keys beginning with ``_`` are informational annotations; the builder ignores
them, so callers may add their own (``_names`` etc.) freely.

Helpers: ``get_map``/``set_map``/``clear_map`` (per-scenario H3M bytes),
``dump_maps`` (write .h3m files), ``to_dir``/``from_dir`` (editable directory
form: campaign.json + .h3m files), ``strip_maps`` (dict without payloads).

Lossless guarantees: ``build_header(parse_header(h)) == h`` for the
decompressed header bytes, and every embedded map is kept byte-identical.
``build(c)`` re-gzips everything (mtime=0) and, by default, rewrites each
scenario's ``packed_map_size`` to the size of the new gzip member (the game
uses it to locate the maps).
"""
from __future__ import annotations

import base64
import copy
import gzip
import json
import re
import zlib
from pathlib import Path

try:  # package import (tools/ on sys.path) or direct script use
    from .binio import Reader, Writer
except ImportError:  # pragma: no cover
    from binio import Reader, Writer

VERSIONS = {4: "RoE", 5: "AB", 6: "SoD", 7: "Chr", 10: "HotA"}
RoE, AB, SoD, CHR, HOTA = 4, 5, 6, 7, 10

# Campaign region maps (the campaign-map picture shown between scenarios).
# id -> (VCMI identifier in config/campaignRegions.json, image prefix, region count, title)
# ids follow VCMI config/gameConfig.json "mapFormat" -> "campaignRegions".
REGION_MAPS = {
    1: ("good1", "G1", 3, "Long Live the Queen"),
    2: ("good2", "G2", 4, "Liberation"),
    3: ("good3", "G3", 3, "Song for the Father"),
    4: ("evil1", "E1", 7, "Dungeons and Devils"),
    5: ("evil2", "E2", 4, "Long Live the King"),
    6: ("neutral1", "N1", 3, "Spoils of War"),
    7: ("secret1", "S1", 3, "Seeds of Discontent"),
    8: ("dragonSlayer", "BR", 4, "Dragon Slayer"),
    9: ("foolhardyWaywardness", "IS", 4, "Foolhardy Waywardness"),
    10: ("festivalOfLife", "KR", 4, "Festival of Life"),
    11: ("dragonsBlood", "NI", 4, "Dragon's Blood"),
    12: ("playingWithFire", "TA", 3, "Playing with Fire"),
    13: ("armageddonsBlade", "AR", 8, "Armageddon's Blade"),
    14: ("hackAndSlash", "HS", 4, "Hack and Slash"),
    15: ("birthOfBarbarian", "BB", 5, "Birth of a Barbarian"),
    16: ("newBeginning", "NB", 4, "New Beginning"),
    17: ("elixirOfLife", "EL", 4, "Elixir of Life"),
    18: ("riseOfTheNecromancer", "RN", 4, "Rise of the Necromancer"),
    19: ("unholyAlliance", "UA", 12, "Unholy Alliance"),
    20: ("spectreOfPower", "SP", 4, "Spectre of Power"),
}

HERO_KEEPS = ["experience", "primarySkills", "secondarySkills", "spells", "artifacts",
              "bit5", "bit6", "bit7"]
START_OPTIONS = {0: "none", 1: "bonus", 2: "crossover", 3: "hero"}
BONUS_TYPES = {0: "spell", 1: "creature", 2: "building", 3: "artifact", 4: "scroll",
               5: "primarySkill", 6: "secondarySkill", 7: "resource"}
PRIMARY = ["attack", "defense", "spellpower", "knowledge"]
# special hero ids used in bonuses (i16)
HERO_SPECIAL = {-1: "random", -2: "generated", -3: "strongest"}
PLAYER_COLORS = ["red", "blue", "tan", "green", "orange", "purple", "teal", "pink"]
DIFFICULTIES = ["pawn", "knight", "rook", "queen", "king"]
# resource ids in "resource" bonuses (read as i8: 0xFD = wood+ore, 0xFE = all rare ones)
RESOURCES = {0: "wood", 1: "mercury", 2: "ore", 3: "sulfur", 4: "crystal", 5: "gems",
             6: "gold", -3: "common", -2: "rare"}

# Prolog/epilog media ids (VCMI gameConfig.json "campaignVideo"/"campaignMusic").
CAMPAIGN_MUSIC = [
    "CampainMusic01", "CampainMusic02", "CampainMusic03", "CampainMusic04", "CampainMusic05",
    "CampainMusic06", "CampainMusic07", "CampainMusic08", "CampainMusic09", "AiTheme0",
    "AiTheme1", "AiTheme2", "Combat01", "Combat02", "Combat03", "Combat04", "CstleTown",
    "TowerTown", "Rampart", "InfernoTown", "NecroTown", "Dungeon", "Stronghold",
    "FortressTown", "ElemTown", "Dirt", "Sand", "Grass", "Snow", "Swamp", "Rough",
    "Underground", "Lava", "Water", "GoodTheme", "NeutralTheme", "EvilTheme",
    "SecretTheme", "LoopLepr", "MainMenu", "Win Scenario", "CampainMusic10",
    "BladeABCampaign", "BladeDBCampaign", "BladeDSCampaign", "BladeFLCampaign",
    "BladeFWCampaign", "BladePFCampaign", "CampainMusic11"]
CAMPAIGN_VIDEO = [
    "GOOD1A", "GOOD1B", "GOOD1C", "EVIL1A", "EVIL1B", "EVIL1C", "NEUTRALA", "NEUTRALB",
    "NEUTRALC", "GOOD2A", "GOOD2B", "GOOD2C", "GOOD2D", "EVIL2A", "EVIL2AP1", "EVIL2B",
    "EVIL2C", "EVIL2D", "GOOD3A", "GOOD3B", "GOOD3C", "SECRETA", "SECRETB", "SECRETC",
    "H3ABab1", "H3ABab2", "H3ABab3", "H3ABab4", "H3ABab5", "H3ABab6", "H3ABab7", "H3ABab8",
    "H3ABab9", "H3ABdb1", "H3ABdb2", "H3ABdb3", "H3ABdb4", "H3ABdb5", "H3ABds1", "H3ABds2",
    "H3ABds3", "H3ABds4", "H3ABds5", "H3ABfl1", "H3ABfl2", "H3ABfl3", "H3ABfl4", "H3ABfl5",
    "H3ABfw1", "H3ABfw2", "H3ABfw3", "H3ABfw4", "H3ABfw5", "H3ABpf1", "H3ABpf2", "H3ABpf3",
    "H3ABpf4", "H3x2_BBa", "H3x2_BBb", "H3x2_BBc", "H3x2_BBd", "H3x2_BBe", "H3x2_BBf",
    "H3x2_Ela", "H3x2_Elb", "H3x2_Elc", "H3x2_Eld", "H3x2_Ele", "H3x2_HSa", "EVIL2C",
    "H3x2_HSc", "H3x2_HSd", "H3x2_HSe", "H3x2_NBa", "H3x2_NBb", "H3x2_Nbc", "H3x2_Nbd",
    "H3x2_Nbe", "H3x2_RNa", "H3x2_RNb", "H3x2_RNc", "H3x2_RNd", "H3x2_RNe1", "H3x2_SPa",
    "H3x2_SPb", "H3x2_SPc", "H3x2_SPd", "H3x2_SPe", "H3x2_UAa", "H3x2_UAb", "H3x2_UAc",
    "H3x2_UAd", "H3x2_UAe", "H3x2_UAf", "H3x2_UAg", "H3x2_UAh", "H3x2_UAi", "H3x2_UAj",
    "H3x2_UAk", "H3x2_UAl", "H3x2_UAm"]


# --------------------------------------------------------------------------- utils

def _flag_out(v: int):
    return bool(v) if v in (0, 1) else v


def _name_or_int(table: dict, v: int):
    return table.get(v, v)


def _rev(table: dict, v) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    for k, name in table.items():
        if name == v:
            return k
    raise ValueError(f"unknown value {v!r}")


def scenario_count(version: int, region_map: int, hota_count: int | None = None) -> int | None:
    """Number of scenario records in a header, or None if it must be inferred."""
    if version == HOTA and hota_count:
        return hota_count
    if version == CHR:
        return None  # Chronicles: not tied to a region map, read until the end
    entry = REGION_MAPS.get(region_map)
    return entry[2] if entry else None


def _precondition_bytes(count: int) -> int:
    if count > 16:
        return 4
    if count > 8:
        return 2
    return 1


# --------------------------------------------------------------------------- header parse

def _read_prolog(r: Reader):
    flag = r.u8()
    if not flag:
        return None
    d = {"video": r.u8(), "music": r.u8(), "text": r.str()}
    if flag != 1:
        d["flag"] = flag
    return d


def _read_bonus(r: Reader, start: int) -> dict:
    if start == 1:
        t = r.u8()
        name = BONUS_TYPES.get(t)
        if name is None:
            raise ValueError(f"unknown campaign bonus type {t} at 0x{r.pos - 1:x}")
        b = {"type": name}
        if t in (0, 4):  # spell, scroll
            b["hero"] = r.i16()
            b["spell"] = r.u8()
        elif t == 1:
            b["hero"] = r.i16()
            b["creature"] = r.u16()
            b["amount"] = r.u16()
        elif t == 2:
            b["building"] = r.u8()
        elif t == 3:
            b["hero"] = r.i16()
            b["artifact"] = r.u16()
        elif t == 5:
            b["hero"] = r.i16()
            for k in PRIMARY:
                b[k] = r.u8()
        elif t == 6:
            b["hero"] = r.i16()
            b["skill"] = r.u8()
            b["mastery"] = r.u8()
        elif t == 7:
            b["resource"] = r.i8()
            b["amount"] = r.i32()
        return b
    if start == 2:
        return {"type": "prevHero", "player": r.u8(), "scenario": r.u8()}
    if start == 3:
        return {"type": "hero", "player": r.u8(), "hero": r.i16()}
    raise ValueError(f"unknown start option {start}")


def _read_travel(r: Reader, version: int, hota_format: int) -> dict:
    keeps = r.u8()
    t = {"hero_keeps": [HERO_KEEPS[i] for i in range(8) if keeps >> i & 1]}
    if version == HOTA and hota_format > 2:
        n = r.u32()
        t["keep_creatures_count"] = n
        t["keep_creatures"] = r.bits((n + 7) // 8)
        n = r.u32()
        t["keep_artifacts_count"] = n
        t["keep_artifacts"] = r.bits((n + 7) // 8)
    else:
        if version == HOTA:
            nc, na = 24, 21
        else:
            nc, na = 19, (17 if version < SoD else 18)
        t["keep_creatures"] = r.bits(nc)
        t["keep_artifacts"] = r.bits(na)
    start = r.u8()
    t["start_options"] = _name_or_int(START_OPTIONS, start)
    if start == 1:
        t["player_color"] = r.u8()
    if start:
        n = r.u8()
        t["bonuses"] = [_read_bonus(r, start) for _ in range(n)]
    return t


def _read_scenario(r: Reader, version: int, count_for_width: int, hota_format: int) -> dict:
    s = {"map_name": r.str(), "packed_map_size": r.u32()}
    nb = _precondition_bytes(count_for_width)
    s["preconditions"] = r.bits(nb)
    s["region_color"] = r.u8()
    s["difficulty"] = r.u8()
    s["region_text"] = r.str()
    s["prolog"] = _read_prolog(r)
    if version == HOTA:
        s["prolog2"] = _read_prolog(r)
        s["prolog3"] = _read_prolog(r)
    s["epilog"] = _read_prolog(r)
    if version == HOTA:
        s["epilog2"] = _read_prolog(r)
        s["epilog3"] = _read_prolog(r)
    s["travel"] = _read_travel(r, version, hota_format)
    return s


def parse_header(data: bytes, encoding: str = "gbk") -> dict:
    """Parse the decompressed campaign header member."""
    r = Reader(data, encoding)
    h: dict = {}
    version = r.u32()
    h["version"] = version
    h["_version_name"] = VERSIONS.get(version, "unknown")
    hota_format = 0
    hota_count = None
    if version == HOTA:
        hota = {"format": r.i32()}
        hota_format = hota["format"]
        if hota_format >= 2:
            hota["game_version"] = [r.u32(), r.u32(), r.u32()]
            hota["force_version"] = _flag_out(r.u8())
        hota["unknown_b"] = r.i8()
        hota["unknown_c"] = r.i32()
        hota["scenario_count"] = hota_count = r.i32()
        h["hota"] = hota
    elif version not in VERSIONS:
        raise ValueError(f"unsupported campaign version {version}")
    h["region_map"] = r.u8()
    reg = REGION_MAPS.get(h["region_map"])
    if reg:
        h["_region_map_name"] = reg[0]
    h["name"] = r.str()
    h["description"] = r.str()
    if version > RoE:
        h["difficulty_choosable"] = _flag_out(r.u8())
    h["music"] = r.u8()

    count = scenario_count(version, h["region_map"], hota_count)
    scenarios = []
    if count is None:  # infer: read until the member is exhausted (<=8 scenarios assumed)
        while r.remaining() > 0:
            scenarios.append(_read_scenario(r, version, 8, hota_format))
    else:
        for _ in range(count):
            scenarios.append(_read_scenario(r, version, count, hota_format))
    h["scenarios"] = scenarios
    if r.remaining():
        h["trailing"] = r.raw(r.remaining()).hex()
    return h


# --------------------------------------------------------------------------- header build

def _write_prolog(w: Writer, p):
    if not p:
        w.u8(0)
        return
    w.u8(int(p.get("flag", 1)))
    w.u8(p["video"])
    w.u8(p["music"])
    w.str(p["text"])


def _write_bonus(w: Writer, b: dict, start: int):
    if start == 1:
        t = _rev(BONUS_TYPES, b["type"])
        w.u8(t)
        if t in (0, 4):
            w.i16(b["hero"])
            w.u8(b["spell"])
        elif t == 1:
            w.i16(b["hero"])
            w.u16(b["creature"])
            w.u16(b["amount"])
        elif t == 2:
            w.u8(b["building"])
        elif t == 3:
            w.i16(b["hero"])
            w.u16(b["artifact"])
        elif t == 5:
            w.i16(b["hero"])
            for k in PRIMARY:
                w.u8(b[k])
        elif t == 6:
            w.i16(b["hero"])
            w.u8(b["skill"])
            w.u8(b["mastery"])
        elif t == 7:
            w.i8(b["resource"])
            w.i32(b["amount"])
        else:
            raise ValueError(f"unknown bonus type {b['type']!r}")
    elif start == 2:
        w.u8(b["player"])
        w.u8(b["scenario"])
    elif start == 3:
        w.u8(b["player"])
        w.i16(b["hero"])
    else:
        raise ValueError(f"bonuses not allowed for start option {start}")


def _write_travel(w: Writer, t: dict, version: int, hota_format: int):
    keeps = 0
    for k in t.get("hero_keeps", []):
        keeps |= 1 << HERO_KEEPS.index(k)
    w.u8(keeps)
    if version == HOTA and hota_format > 2:
        n = t.get("keep_creatures_count", 0)
        w.u32(n)
        w.bits((n + 7) // 8, t.get("keep_creatures", []))
        n = t.get("keep_artifacts_count", 0)
        w.u32(n)
        w.bits((n + 7) // 8, t.get("keep_artifacts", []))
    else:
        if version == HOTA:
            nc, na = 24, 21
        else:
            nc, na = 19, (17 if version < SoD else 18)
        w.bits(nc, t.get("keep_creatures", []))
        w.bits(na, t.get("keep_artifacts", []))
    start = _rev(START_OPTIONS, t.get("start_options", "none"))
    w.u8(start)
    if start == 1:
        w.u8(t.get("player_color", 0))
    if start:
        bonuses = t.get("bonuses", [])
        w.u8(len(bonuses))
        for b in bonuses:
            _write_bonus(w, b, start)


def _write_scenario(w: Writer, s: dict, version: int, count_for_width: int,
                    hota_format: int, packed_size: int | None = None):
    w.str(s["map_name"])
    w.u32(s.get("packed_map_size", 0) if packed_size is None else packed_size)
    w.bits(_precondition_bytes(count_for_width), s.get("preconditions", []))
    w.u8(s.get("region_color", 0))
    w.u8(s.get("difficulty", 1))
    w.str(s.get("region_text", ""))
    _write_prolog(w, s.get("prolog"))
    if version == HOTA:
        _write_prolog(w, s.get("prolog2"))
        _write_prolog(w, s.get("prolog3"))
    _write_prolog(w, s.get("epilog"))
    if version == HOTA:
        _write_prolog(w, s.get("epilog2"))
        _write_prolog(w, s.get("epilog3"))
    _write_travel(w, s.get("travel", {}), version, hota_format)


def build_header(h: dict, encoding: str = "gbk", packed_sizes: list | None = None) -> bytes:
    """Serialise a header dict (inverse of parse_header).

    ``packed_sizes`` optionally overrides each scenario's packed_map_size
    (list aligned with h["scenarios"], None entries keep the stored value).
    """
    w = Writer(encoding)
    version = h["version"]
    w.u32(version)
    hota_format = 0
    scenarios = h["scenarios"]
    if version == HOTA:
        hota = h["hota"]
        hota_format = hota["format"]
        w.i32(hota_format)
        if hota_format >= 2:
            for v in hota["game_version"]:
                w.u32(v)
            w.u8(int(hota["force_version"]))
        w.i8(hota.get("unknown_b", 1))
        w.i32(hota.get("unknown_c", 0))
        w.i32(hota["scenario_count"])
    w.u8(h["region_map"])
    w.str(h["name"])
    w.str(h["description"])
    if version > RoE:
        w.u8(int(h.get("difficulty_choosable", 0)))
    w.u8(h["music"])

    count = scenario_count(version, h["region_map"], h.get("hota", {}).get("scenario_count"))
    if count is not None and count != len(scenarios):
        raise ValueError(f"region map {h['region_map']} needs exactly {count} scenarios, "
                         f"got {len(scenarios)} (pad with void scenarios: map_name='')")
    width = count if count is not None else 8
    for i, s in enumerate(scenarios):
        ps = packed_sizes[i] if packed_sizes else None
        _write_scenario(w, s, version, width, hota_format, ps)
    if h.get("trailing"):
        w.raw(bytes.fromhex(h["trailing"]))
    return w.getvalue()


# --------------------------------------------------------------------------- gzip container

def split_members(raw: bytes) -> tuple[list[bytes], list[int], bytes]:
    """Split concatenated gzip members.

    Returns (decompressed members, compressed size of each member, trailing bytes
    that could not be decoded as gzip).
    """
    members, sizes = [], []
    rest = raw
    while rest:
        d = zlib.decompressobj(wbits=31)
        try:
            data = d.decompress(rest)
            data += d.flush()
        except zlib.error:
            if not members:
                raise
            break  # trailing garbage (seen in some GOG releases)
        if not d.eof:
            if not members:
                raise ValueError("truncated gzip member")
            break
        members.append(data)
        sizes.append(len(rest) - len(d.unused_data))
        rest = d.unused_data
    return members, sizes, rest


def _gz(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9, mtime=0)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def parse(raw: bytes, encoding: str = "gbk") -> dict:
    """Parse a whole .h3c file (gzip-compressed bytes) into a JSON-able dict."""
    members, sizes, trailing = split_members(raw)
    header = parse_header(members[0], encoding)
    maps_iter = iter(members[1:])
    maps = []
    for s in header["scenarios"]:
        if s["map_name"]:
            m = next(maps_iter, None)
            maps.append(_b64(m) if m is not None else None)
        else:
            maps.append(None)
    c = {"format": "h3c", "encoding": encoding, "header": header, "maps": maps}
    extra = list(maps_iter)
    if extra:
        c["extra_maps"] = [_b64(m) for m in extra]
    if trailing:
        c["trailing"] = trailing.hex()
    c["_member_sizes"] = sizes
    return c


def build(c: dict, encoding: str | None = None, fix_sizes: bool = True,
          keep_trailing: bool = False) -> bytes:
    """Serialise a campaign dict into .h3c file bytes (gzip members, mtime=0).

    With ``fix_sizes`` (default) each non-void scenario's packed_map_size is
    set to the size of the freshly compressed map member.
    """
    h = c["header"]
    maps = c.get("maps", [])
    gz_maps = []
    sizes = []
    for i, s in enumerate(h["scenarios"]):
        m = maps[i] if i < len(maps) else None
        if s["map_name"]:
            if m is None:
                raise ValueError(f"scenario {i} ({s['map_name']!r}) has no map data")
            g = _gz(_unb64(m) if isinstance(m, str) else bytes(m))
            gz_maps.append(g)
            sizes.append(len(g))
        else:
            if m is not None:
                raise ValueError(f"scenario {i} is void (empty map_name) but has map data")
            sizes.append(None)
    for m in c.get("extra_maps", []):
        gz_maps.append(_gz(_unb64(m) if isinstance(m, str) else bytes(m)))
    head = build_header(h, encoding or c.get("encoding") or "gbk", sizes if fix_sizes else None)
    out = _gz(head) + b"".join(gz_maps)
    if keep_trailing and c.get("trailing"):
        out += bytes.fromhex(c["trailing"])
    return out


def load(path: str | Path, encoding: str = "gbk") -> dict:
    return parse(Path(path).read_bytes(), encoding)


def save(c: dict, path: str | Path, encoding: str | None = None, **kw) -> None:
    Path(path).write_bytes(build(c, encoding, **kw))


# --------------------------------------------------------------------------- map helpers

def get_map(c: dict, index: int) -> bytes | None:
    """Decompressed H3M bytes of scenario ``index`` (None for void scenarios)."""
    m = c["maps"][index]
    return _unb64(m) if isinstance(m, str) else m


def set_map(c: dict, index: int, h3m: bytes, map_name: str | None = None) -> None:
    """Replace (or add) the map of scenario ``index``.

    ``h3m`` may be raw or gzip-compressed H3M bytes. ``map_name`` sets the
    scenario's map file name (required when filling a void scenario).
    """
    if h3m[:2] == b"\x1f\x8b":
        h3m = gzip.decompress(h3m)
    s = c["header"]["scenarios"][index]
    if map_name is not None:
        s["map_name"] = map_name
    if not s["map_name"]:
        raise ValueError("void scenario: pass map_name to give it a map")
    while len(c["maps"]) <= index:
        c["maps"].append(None)
    c["maps"][index] = _b64(h3m)


def clear_map(c: dict, index: int) -> None:
    """Turn scenario ``index`` into a void scenario."""
    s = c["header"]["scenarios"][index]
    s["map_name"] = ""
    s["packed_map_size"] = 0
    c["maps"][index] = None


_BAD_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str) -> str:
    name = name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    name = _BAD_FS.sub("_", name).strip().rstrip(".")
    return name or "map"


def map_filenames(c: dict) -> list[str | None]:
    """File-system-safe, unique .h3m file names aligned with the scenarios."""
    names: list[str | None] = []
    used = set()
    for i, s in enumerate(c["header"]["scenarios"]):
        if i >= len(c["maps"]) or c["maps"][i] is None:
            names.append(None)
            continue
        fn = safe_filename(s["map_name"])
        if not fn.lower().endswith(".h3m"):
            fn += ".h3m"
        if fn.lower() in used:
            fn = f"{i:02d}_{fn}"
        used.add(fn.lower())
        names.append(fn)
    return names


def dump_maps(c: dict, out_dir: str | Path, compress: bool = True) -> list[Path]:
    """Write every embedded map as ``<map_name>`` (gzip like normal .h3m files)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, fn in enumerate(map_filenames(c)):
        if fn is None:
            continue
        data = get_map(c, i)
        p = out / fn
        p.write_bytes(_gz(data) if compress else data)
        paths.append(p)
    return paths


def to_dir(c: dict, out_dir: str | Path, compress: bool = True) -> Path:
    """Unpack a campaign into ``campaign.json`` + one .h3m per scenario.

    ``campaign.json`` holds the header and, instead of base64 payloads,
    ``map_files`` (names relative to the directory). ``from_dir`` reverses it,
    so the directory is an editable source form of the campaign.
    """
    out = Path(out_dir)
    dump_maps(c, out, compress)
    doc = {"format": "h3c-dir", "encoding": c.get("encoding", "gbk"), "header": c["header"],
           "map_files": map_filenames(c)}
    for i, m in enumerate(c.get("extra_maps", [])):
        fn = f"extra_{i:02d}.h3m"
        data = _unb64(m) if isinstance(m, str) else m
        (out / fn).write_bytes(_gz(data) if compress else data)
        doc.setdefault("extra_map_files", []).append(fn)
    if c.get("trailing"):
        doc["trailing"] = c["trailing"]
    p = out / "campaign.json"
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return p


def _read_h3m_file(p: Path) -> bytes:
    data = p.read_bytes()
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def from_dir(path: str | Path) -> dict:
    """Inverse of ``to_dir``: campaign.json (+ the .h3m files it names) -> campaign dict."""
    path = Path(path)
    if path.is_dir():
        path = path / "campaign.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    maps = [None if fn is None else _b64(_read_h3m_file(base / fn)) for fn in doc["map_files"]]
    c = {"format": "h3c", "encoding": doc.get("encoding", "gbk"), "header": doc["header"],
         "maps": maps}
    if doc.get("extra_map_files"):
        c["extra_maps"] = [_b64(_read_h3m_file(base / fn)) for fn in doc["extra_map_files"]]
    if doc.get("trailing"):
        c["trailing"] = doc["trailing"]
    return c


def strip_maps(c: dict) -> dict:
    """Copy of the campaign without map payloads (for human-readable JSON)."""
    d = copy.deepcopy(c)
    d["maps"] = [None if m is None else f"<{len(_unb64(m))} bytes>" for m in d["maps"]]
    if "extra_maps" in d:
        d["extra_maps"] = [f"<{len(_unb64(m))} bytes>" for m in d["extra_maps"]]
    return d


def game_campaigns(game_dir: str | Path, chronicles: bool = True):
    """Yield (label, file name, raw bytes) for the campaigns of an H3 install:
    every .h3c in Data/H3bitmap.lod and Data/H3ab_bmp.lod, plus Maps/C1-*..C8-*.h3c."""
    try:
        from .lod import Lod
    except ImportError:  # pragma: no cover
        from lod import Lod
    game = Path(game_dir)
    for lod_name in ("H3bitmap.lod", "H3ab_bmp.lod"):
        p = game / "Data" / lod_name
        if p.exists():
            lod = Lod(p)
            for e in lod.entries.values():
                if e.name.lower().endswith(".h3c"):
                    yield f"{lod_name}:{e.name}", e.name, lod.read(e.name)
    maps = game / "Maps"
    if chronicles and maps.exists():
        for f in sorted(maps.iterdir()):
            n = f.name
            if (n.lower().endswith(".h3c") and len(n) > 3 and n[0] in "cC"
                    and n[1] in "12345678" and n[2] == "-"):
                yield f"Maps/{n}", n, f.read_bytes()


def media_name(kind: str, idx: int) -> str | None:
    table = CAMPAIGN_VIDEO if kind == "video" else CAMPAIGN_MUSIC
    return table[idx] if 0 <= idx < len(table) else None


if __name__ == "__main__":  # quick inspection: python h3c.py file.h3c
    import sys

    camp = load(sys.argv[1])
    print(json.dumps(strip_maps(camp), ensure_ascii=False, indent=1))
