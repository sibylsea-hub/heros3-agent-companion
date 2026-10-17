"""Lossless reader/writer for Heroes of Might and Magic III maps (.h3m).

Supports the RoE (0x0E), AB (0x15) and SoD (0x1C) formats. The layout follows
VCMI's lib/mapping/MapFormatH3M.cpp; every byte VCMI skips or ignores is kept
in the dict (padding as hex strings under keys that start with "_", only when
it is not all zeros), so ``build(parse(raw)) == raw``.

    parse(data)  -> dict      decompressed H3M bytes -> JSON-serialisable dict
    build(m)     -> bytes     dict -> decompressed H3M bytes
    load(path)   -> dict      reads a .h3m (gzip or raw)
    save(m, path)             writes a gzip-compressed .h3m
    blank_map(...) -> dict    minimal valid empty map

The schema is documented in H3M_NOTES.md next to this file.

The reader and the writer are the same code: every section is described once
by a method of ``_Codec`` that either reads a field into a dict or writes it
from the dict, so the two directions cannot drift apart. In write mode missing
keys fall back to sensible defaults, which keeps hand-written dicts short.
"""
from __future__ import annotations

import gzip
import struct
from pathlib import Path

try:
    from .binio import Reader, Writer
except ImportError:  # running as a loose module
    from binio import Reader, Writer

__all__ = ["parse", "build", "load", "save", "blank_map", "kind_of", "H3MError",
           "VERSIONS", "OBJECT_CLASS_NAMES"]

VERSIONS = {0x0E: "RoE", 0x15: "AB", 0x1C: "SoD"}
VERSION_IDS = {v: k for k, v in VERSIONS.items()}

VICTORY_TYPES = {
    -1: "standard", 0: "acquire_artifact", 1: "accumulate_creatures",
    2: "accumulate_resources", 3: "upgrade_town", 4: "build_grail",
    5: "defeat_hero", 6: "capture_town", 7: "defeat_monster",
    8: "flag_dwellings", 9: "flag_mines", 10: "transport_artifact",
}
LOSS_TYPES = {-1: "standard", 0: "lose_town", 1: "lose_hero", 2: "time_expires"}
QUEST_TYPES = {
    0: "none", 1: "level", 2: "primary_skills", 3: "kill_hero",
    4: "kill_monster", 5: "artifacts", 6: "army", 7: "resources",
    8: "hero", 9: "player",
}
REWARD_TYPES = {
    0: "nothing", 1: "experience", 2: "mana", 3: "morale", 4: "luck",
    5: "resource", 6: "primary_skill", 7: "secondary_skill", 8: "artifact",
    9: "spell", 10: "creature",
}

# Object class ids (VCMI MapObjectBaseID), used to label payload-less objects.
OBJECT_CLASS_NAMES = {
    0: "nothing", 2: "altar_of_sacrifice", 3: "anchor_point", 4: "arena",
    5: "artifact", 6: "pandoras_box", 7: "black_market", 8: "boat",
    9: "border_guard", 10: "keymaster", 11: "buoy", 12: "campfire",
    13: "cartographer", 14: "swan_pond", 15: "cover_of_darkness",
    16: "creature_bank", 17: "creature_generator1", 18: "creature_generator2",
    19: "creature_generator3", 20: "creature_generator4",
    21: "cursed_ground1", 22: "corpse", 23: "marletto_tower",
    24: "derelict_ship", 25: "dragon_utopia", 26: "event", 27: "eye_of_magi",
    28: "faerie_ring", 29: "flotsam", 30: "fountain_of_fortune",
    31: "fountain_of_youth", 32: "garden_of_revelation", 33: "garrison",
    34: "hero", 35: "hill_fort", 36: "grail", 37: "hut_of_magi",
    38: "idol_of_fortune", 39: "lean_to", 41: "library_of_enlightenment",
    42: "lighthouse", 43: "monolith_one_way_entrance",
    44: "monolith_one_way_exit", 45: "monolith_two_way", 46: "magic_plains1",
    47: "school_of_magic", 48: "magic_spring", 49: "magic_well",
    50: "market_of_time", 51: "mercenary_camp", 52: "mermaid", 53: "mine",
    54: "monster", 55: "mystical_garden", 56: "oasis", 57: "obelisk",
    58: "redwood_observatory", 59: "ocean_bottle", 60: "pillar_of_fire",
    61: "star_axis", 62: "prison", 63: "pyramid", 64: "rally_flag",
    65: "random_art", 66: "random_treasure_art", 67: "random_minor_art",
    68: "random_major_art", 69: "random_relic_art", 70: "random_hero",
    71: "random_monster", 72: "random_monster_l1", 73: "random_monster_l2",
    74: "random_monster_l3", 75: "random_monster_l4", 76: "random_resource",
    77: "random_town", 78: "refugee_camp", 79: "resource", 80: "sanctuary",
    81: "scholar", 82: "sea_chest", 83: "seer_hut", 84: "crypt",
    85: "shipwreck", 86: "shipwreck_survivor", 87: "shipyard",
    88: "shrine_of_magic_incantation", 89: "shrine_of_magic_gesture",
    90: "shrine_of_magic_thought", 91: "sign", 92: "sirens",
    93: "spell_scroll", 94: "stables", 95: "tavern", 96: "temple",
    97: "den_of_thieves", 98: "town", 99: "trading_post",
    100: "learning_stone", 101: "treasure_chest", 102: "tree_of_knowledge",
    103: "subterranean_gate", 104: "university", 105: "wagon",
    106: "war_machine_factory", 107: "school_of_war", 108: "warriors_tomb",
    109: "water_wheel", 110: "watering_hole", 111: "whirlpool",
    112: "windmill", 113: "witch_hut", 114: "brush", 115: "bush",
    116: "cactus", 117: "canyon", 118: "crater", 119: "dead_vegetation",
    120: "flowers", 121: "frozen_lake", 122: "hedge", 123: "hill",
    124: "hole", 125: "kelp", 126: "lake", 127: "lava_flow", 128: "lava_lake",
    129: "mushrooms", 130: "log", 131: "mandrake", 132: "moss", 133: "mound",
    134: "mountain", 135: "oak_trees", 136: "outcropping", 137: "pine_trees",
    138: "plant", 143: "river_delta", 147: "rock", 148: "sand_dune",
    149: "sand_pit", 150: "shrub", 151: "skull", 152: "stalagmite",
    153: "stump", 154: "tar_pit", 155: "trees", 156: "vine",
    157: "volcanic_vent", 158: "volcano", 159: "willow_trees",
    160: "yucca_trees", 161: "reef", 162: "random_monster_l5",
    163: "random_monster_l6", 164: "random_monster_l7", 177: "lake2",
    199: "trees2", 206: "desert_hills", 207: "dirt_hills", 208: "grass_hills",
    209: "rough_hills", 210: "subterranean_rocks", 211: "swamp_foliage",
    212: "border_gate",
    213: "freelancers_guild", 214: "hero_placeholder", 215: "quest_guard",
    216: "random_dwelling", 217: "random_dwelling_lvl",
    218: "random_dwelling_faction", 219: "garrison2", 220: "abandoned_mine",
    221: "trading_post_snow", 222: "clover_field", 223: "cursed_ground2",
    224: "evil_fog", 225: "favorable_winds", 226: "fiery_fields",
    227: "holy_grounds", 228: "lucid_pools", 229: "magic_clouds",
    230: "magic_plains2", 231: "rocklands",
}

_MONSTER_CLASSES = {54, 71, 72, 73, 74, 75, 162, 163, 164}
_ARTIFACT_CLASSES = {5, 65, 66, 67, 68, 69}


def kind_of(cls: int, sub: int) -> str:
    """Readable kind label for an object of template class/subclass."""
    if cls == 26:
        return "event"
    if cls in (34, 70):
        return "hero"
    if cls == 62:
        return "prison"
    if cls in _MONSTER_CLASSES:
        return "monster"
    if cls == 91:
        return "sign"
    if cls == 59:
        return "ocean_bottle"
    if cls == 83:
        return "seer_hut"
    if cls == 113:
        return "witch_hut"
    if cls == 81:
        return "scholar"
    if cls in (33, 219):
        return "garrison"
    if cls in _ARTIFACT_CLASSES:
        return "artifact"
    if cls == 93:
        return "spell_scroll"
    if cls in (76, 79):
        return "resource"
    if cls in (77, 98):
        return "town"
    if cls in (53, 220):
        return "mine" if sub < 7 else "abandoned_mine"
    if cls in (17, 18, 19, 20):
        return "dwelling"
    if cls in (88, 89, 90):
        return "shrine"
    if cls == 6:
        return "pandora"
    if cls == 36 and sub < 1000:
        return "grail"
    if cls in (216, 217, 218):
        return "random_dwelling"
    if cls == 215 or (cls == 212 and sub == 1000):
        return "quest_guard"
    if cls == 87:
        return "shipyard"
    if cls == 42:
        return "lighthouse"
    if cls == 214:
        return "hero_placeholder"
    return OBJECT_CLASS_NAMES.get(cls, "object")


class H3MError(ValueError):
    pass


class _Codec:
    """One description of the format, run either as reader or as writer.

    Value methods (``u8``, ``str``, ``opt`` ...) take the value to write (ignored
    when reading) and return the value read/written. ``f(o, key, fn, *args)``
    applies a value method to ``o[key]``.
    """

    def __init__(self, data: bytes | None, encoding: str, trace: list | None):
        self.rd = data is not None
        if self.rd:
            self.r = Reader(data, encoding)
        else:
            self.w = Writer(encoding)
        self.trace = trace
        self.set_version("SoD")

    # ---- bookkeeping -------------------------------------------------------
    def set_version(self, name: str):
        if name not in VERSION_IDS:
            raise H3MError(f"unsupported map version {name!r}")
        self.version = name
        self.ab = name in ("AB", "SoD")
        self.sod = name == "SoD"
        self.heroes_bytes = 20 if self.ab else 16
        self.factions_bytes = 2 if self.ab else 1
        self.artifacts_bytes = 18 if self.sod else 17
        self.art_slots = 19 if self.sod else 18
        self.heroes_count = 156 if self.ab else 128

    def pos(self) -> int:
        return self.r.pos if self.rd else len(self.w.buf)

    def mark(self, label: str):
        if self.trace is not None:
            self.trace.append((self.pos(), label))

    def f(self, o: dict, key: str, fn, *args, d=None, **kw):
        if self.rd:
            v = fn(None, *args, **kw)
            o[key] = v
            return v
        v = o.get(key, d)
        if v is None and d is not None:
            v = d
        return fn(v, *args, **kw)

    def pad(self, o: dict, key: str, n: int):
        """Bytes VCMI skips (normally zero): kept as hex only when non-zero."""
        if self.rd:
            b = self.r.raw(n)
            if any(b):
                o[key] = b.hex()
            return
        hx = o.get(key)
        if hx:
            b = bytes.fromhex(hx)
            if len(b) != n:
                raise H3MError(f"{key}: expected {n} bytes, got {len(b)}")
            self.w.raw(b)
        else:
            self.w.raw(bytes(n))

    # ---- primitive value methods -------------------------------------------
    def _p(self, name, v):
        if self.rd:
            return getattr(self.r, name)()
        if v is None:
            v = 0
        getattr(self.w, name)(int(v))
        return v

    def u8(self, v=None):
        return self._p("u8", v)

    def i8(self, v=None):
        return self._p("i8", v)

    def u16(self, v=None):
        return self._p("u16", v)

    def i16(self, v=None):
        return self._p("i16", v)

    def u32(self, v=None):
        return self._p("u32", v)

    def i32(self, v=None):
        return self._p("i32", v)

    def flag(self, v=None):
        """u8 boolean; values other than 0/1 are kept as ints."""
        if self.rd:
            b = self.r.u8()
            return bool(b) if b in (0, 1) else b
        self.w.u8(int(v or 0))
        return v

    def str(self, v=None):
        if self.rd:
            return self.r.str()
        self.w.str(v or "")
        return v

    def u8n(self, v=None):
        """u8 id where 0xFF means none -> -1."""
        if self.rd:
            b = self.r.u8()
            return -1 if b == 0xFF else b
        if v is None:
            v = -1
        self.w.u8(0xFF if v == -1 else v)
        return v

    def u16n(self, v=None):
        if self.rd:
            b = self.r.u16()
            return -1 if b == 0xFFFF else b
        if v is None:
            v = -1
        self.w.u16(0xFFFF if v == -1 else v)
        return v

    def owner32(self, v=None):
        """u32 player id where 255 means neutral -> -1."""
        if self.rd:
            b = self.r.u32()
            return -1 if b == 0xFF else b
        if v is None:
            v = -1
        self.w.u32(0xFF if v == -1 else v)
        return v

    def art(self, v=None):
        """Artifact id: u8 in RoE, u16 in AB/SoD; -1 = none."""
        return self.u16n(v) if self.ab else self.u8n(v)

    def cre(self, v=None):
        """Creature id: u8 in RoE, u16 in AB/SoD. -1 = none, -2..-15 = random
        creature of level (-v-2)//2, upgraded if (-v-2) is odd."""
        size = 0x10000 if self.ab else 0x100
        if self.rd:
            b = self.r.u16() if self.ab else self.r.u8()
            return b - size if b >= size - 15 else b
        if v is None:
            v = -1
        b = v + size if v < 0 else v
        if self.ab:
            self.w.u16(b)
        else:
            self.w.u8(b)
        return v

    def bits(self, v, nbytes):
        """LSB-first bitfield as a sorted list of set bit indices."""
        if self.rd:
            return self.r.bits(nbytes)
        self.w.bits(nbytes, v or [])
        return v

    def enum(self, v, table, width="i8"):
        if self.rd:
            raw = getattr(self.r, width)()
            return table.get(raw, raw)
        if isinstance(v, str):
            rev = {name: k for k, name in table.items()}
            if v not in rev:
                raise H3MError(f"unknown enum value {v!r}; expected one of {sorted(rev)}")
            raw = rev[v]
        else:
            raw = int(v)
        getattr(self.w, width)(raw)
        return v

    # ---- composite value methods -------------------------------------------
    def opt(self, v, fn, *args):
        """bool presence flag, then the value if set; absent -> None."""
        if self.rd:
            present = self.r.u8()
            if not present:
                return None
            if present != 1:
                raise H3MError(f"presence flag {present} at 0x{self.r.pos - 1:x}")
            return fn(None, *args)
        self.w.u8(0 if v is None else 1)
        if v is not None:
            fn(v, *args)
        return v

    def arr(self, v, n, fn, *args, fill=None):
        """Fixed-length list (shorter lists are padded with `fill` when writing)."""
        if self.rd:
            return [fn(None, *args) for _ in range(n)]
        v = list(v or [])
        if len(v) > n:
            raise H3MError(f"list of {len(v)} items, at most {n} allowed")
        v += [fill] * (n - len(v))
        for item in v:
            fn(item, *args)
        return v

    def vec(self, v, count_fn, fn, *args):
        """Count-prefixed list."""
        if self.rd:
            n = count_fn(None)
            return [fn(None, *args) for _ in range(n)]
        v = v or []
        count_fn(len(v))
        for item in v:
            fn(item, *args)
        return v

    def dct(self, v, fn, *args):
        """Sub-dict described by method fn(o, *args)."""
        o = {} if self.rd or v is None else v
        fn(o, *args)
        return o

    def res(self, v=None):
        return self.arr(v, 7, self.i32, fill=0)

    def stack(self, v=None):
        if self.rd:
            return [self.cre(), self.u16()]
        c, n = v if v is not None else (-1, 0)
        self.cre(c)
        self.u16(n)
        return v

    def army(self, v=None):
        """7 creature slots as [creature, count]; empty slot = [-1, 0]."""
        return self.arr(v, 7, self.stack, fill=[-1, 0])

    def skill_pair(self, v=None):
        if self.rd:
            return [self.u8(), self.u8()]
        self.u8(v[0])
        self.u8(v[1])
        return v

    def pos3(self, o):
        self.f(o, "x", self.u8)
        self.f(o, "y", self.u8)
        self.f(o, "z", self.u8)

    # ---- whole map -----------------------------------------------------------
    def map(self, m: dict) -> dict:
        self.mark("header")
        if self.rd:
            vid = self.r.u32()
            if vid not in VERSIONS:
                raise H3MError(f"unsupported map format 0x{vid:x} (only RoE/AB/SoD)")
            m["version"] = VERSIONS[vid]
        else:
            if m.get("version", "SoD") not in VERSION_IDS:
                raise H3MError(f"unsupported version {m.get('version')!r}")
            self.w.u32(VERSION_IDS[m.get("version", "SoD")])
        self.set_version(m.get("version", "SoD"))

        self.f(m, "has_players", self.flag, d=True)
        terrain = {} if self.rd else (m.get("terrain") or {})
        size = self.f(terrain, "size", self.i32, d=36)
        two = self.f(terrain, "two_levels", self.flag, d=False)
        self.f(m, "name", self.str, d="")
        self.f(m, "description", self.str, d="")
        self.f(m, "difficulty", self.u8, d=1)
        if self.ab:
            self.f(m, "level_limit", self.u8, d=0)

        self.mark("players")
        self.f(m, "players", self.arr, 8, self.dct, self.player, fill={})
        self.mark("victory/loss conditions")
        self.f(m, "victory", self.dct, self.victory, d={"type": "standard"})
        self.f(m, "loss", self.dct, self.loss, d={"type": "standard"})

        self.mark("teams")
        n_teams = self.f(m, "team_count", self.u8, d=0)
        if n_teams:
            self.f(m, "teams", self.arr, 8, self.u8, fill=0)

        self.mark("allowed heroes")
        self.f(m, "allowed_heroes", self.bits, self.heroes_bytes,
               d=list(range(self.heroes_count)))
        if self.ab:
            self.f(m, "reserved_heroes", self.vec, self.u32, self.u8)
        if self.sod:
            self.f(m, "disposed_heroes", self.vec, self.u8, self.dct, self.disposed_hero)

        self.pad(m, "_pad_options", 31)

        self.mark("allowed artifacts/spells/skills")
        if self.ab:
            self.f(m, "disabled_artifacts", self.bits, self.artifacts_bytes)
        if self.sod:
            self.f(m, "disabled_spells", self.bits, 9)
            self.f(m, "disabled_skills", self.bits, 4)

        self.mark("rumors")
        self.f(m, "rumors", self.vec, self.u32, self.dct, self.rumor)

        if self.sod:
            self.mark("hero settings")
            self.hero_settings(m)

        self.mark("terrain")
        self.tiles(terrain, size, 2 if two else 1)
        if self.rd:
            m["terrain"] = terrain

        self.mark("templates")
        templates = self.f(m, "templates", self.vec, self.u32, self.dct, self.template)

        self.mark("objects")
        self.objects(m, templates)

        self.mark("events")
        self.f(m, "events", self.vec, self.u32, self.dct, self.timed_event)

        self.mark("trailing")
        if self.rd:
            rest = self.r.raw(self.r.remaining())
            m["trailing"] = rest.hex()
        else:
            self.w.raw(bytes.fromhex(m.get("trailing", "00" * 124)))
        return m

    # ---- header parts ----------------------------------------------------------
    def player(self, o):
        self.f(o, "can_human", self.flag, d=False)
        self.f(o, "can_computer", self.flag, d=False)
        self.f(o, "ai_tactic", self.i8, d=0)
        if self.sod:
            self.f(o, "faction_selectable", self.u8, d=0)
        self.f(o, "factions", self.bits, self.factions_bytes,
               d=list(range(9 if self.ab else 8)))
        self.f(o, "random_faction", self.flag, d=False)
        self.f(o, "main_town", self.opt, self.dct, self.main_town)
        self.f(o, "random_hero", self.flag, d=False)
        if self.rd:
            hero = self.u8n()
            if hero == -1:
                o["main_hero"] = None
            else:
                o["main_hero"] = {"hero": hero, "portrait": self.u8n(), "name": self.str()}
        else:
            mh = o.get("main_hero")
            if mh is None:
                self.u8n(-1)
            else:
                self.u8n(mh["hero"])
                self.u8n(mh.get("portrait", -1))
                self.str(mh.get("name", ""))
        if self.ab:
            self.f(o, "placeholder_count", self.u8, d=0)
            self.f(o, "heroes", self.vec, self.u32, self.dct, self.player_hero)

    def main_town(self, o):
        if self.ab:
            self.f(o, "generate_hero", self.flag, d=True)
            self.f(o, "town_type", self.u8n, d=-1)
        self.pos3(o)

    def player_hero(self, o):
        self.f(o, "hero", self.u8n)
        self.f(o, "name", self.str, d="")

    def victory(self, o):
        t = self.f(o, "type", self.enum, VICTORY_TYPES, d="standard")
        if t == "standard":
            return
        self.f(o, "allow_normal", self.flag, d=False)
        self.f(o, "applies_to_ai", self.flag, d=False)
        if t == "acquire_artifact":
            self.f(o, "artifact", self.art)
        elif t == "accumulate_creatures":
            self.f(o, "creature", self.cre)
            self.f(o, "amount", self.i32)
        elif t == "accumulate_resources":
            self.f(o, "resource", self.i8)
            self.f(o, "amount", self.i32)
        elif t == "upgrade_town":
            self.pos3(o)
            self.f(o, "hall_level", self.i8)
            self.f(o, "castle_level", self.i8)
        elif t in ("build_grail", "defeat_hero", "capture_town", "defeat_monster"):
            self.pos3(o)
        elif t in ("flag_dwellings", "flag_mines"):
            pass
        elif t == "transport_artifact":
            self.f(o, "artifact", self.u8n)
            self.pos3(o)
        else:
            raise H3MError(f"unsupported victory condition {t!r}")

    def loss(self, o):
        t = self.f(o, "type", self.enum, LOSS_TYPES, d="standard")
        if t in ("lose_town", "lose_hero"):
            self.pos3(o)
        elif t == "time_expires":
            self.f(o, "days", self.u16)
        elif t != "standard":
            raise H3MError(f"unsupported loss condition {t!r}")

    def disposed_hero(self, o):
        self.f(o, "hero", self.u8)
        self.f(o, "portrait", self.u8n, d=-1)
        self.f(o, "name", self.str, d="")
        self.f(o, "players", self.bits, 1, d=list(range(8)))

    def rumor(self, o):
        self.f(o, "name", self.str, d="")
        self.f(o, "text", self.str, d="")

    def hero_settings(self, m):
        """SoD per-hero-type customisation (map specifications > heroes).
        Stored sparsely: only customised heroes, with their hero type id."""
        if self.rd:
            out = []
            for hero in range(self.heroes_count):
                custom = self.r.u8()
                if custom:
                    if custom != 1:
                        raise H3MError(f"hero setting flag {custom} for hero {hero}")
                    o = {"hero": hero}
                    self.hero_setting(o)
                    out.append(o)
            m["hero_settings"] = out
            return
        by_id = {}
        for o in m.get("hero_settings") or []:
            if o["hero"] in by_id or not 0 <= o["hero"] < self.heroes_count:
                raise H3MError(f"bad or duplicate hero_settings entry {o['hero']}")
            by_id[o["hero"]] = o
        for hero in range(self.heroes_count):
            o = by_id.get(hero)
            self.w.u8(0 if o is None else 1)
            if o is not None:
                self.hero_setting(o)

    def hero_setting(self, o):
        self.f(o, "experience", self.opt, self.u32)
        self.f(o, "secondary_skills", self.opt, self.vec, self.u32, self.skill_pair)
        self.f(o, "artifacts", self.opt, self.dct, self.hero_artifacts)
        self.f(o, "biography", self.opt, self.str)
        self.f(o, "gender", self.u8n, d=-1)
        self.f(o, "spells", self.opt, self.bits, 9)
        self.f(o, "primary_skills", self.opt, self.arr, 4, self.u8)

    def hero_artifacts(self, o):
        self.f(o, "worn", self.arr, self.art_slots, self.art, fill=-1)
        self.f(o, "backpack", self.vec, self.u16, self.art)

    # ---- terrain & templates -----------------------------------------------------
    def tiles(self, t, size, levels):
        n = size * size
        if self.rd:
            raw = self.r.raw(n * levels * 7)
            flat = [list(x) for x in struct.iter_unpack("7B", raw)]
            t["tiles"] = [[flat[(z * size + y) * size:(z * size + y + 1) * size]
                           for y in range(size)] for z in range(levels)]
            return
        tiles = t.get("tiles")
        if tiles is None:
            raise H3MError("terrain.tiles missing")
        if len(tiles) != levels:
            raise H3MError(f"terrain has {len(tiles)} levels, header says {levels}")
        out = bytearray()
        for z, level in enumerate(tiles):
            if len(level) != size:
                raise H3MError(f"level {z}: {len(level)} rows, expected {size}")
            for row in level:
                if len(row) != size:
                    raise H3MError(f"level {z}: row of {len(row)} tiles, expected {size}")
                for tile in row:
                    out += bytes(tile)
        self.w.raw(bytes(out))

    def template(self, o):
        self.f(o, "def", self.str, d="")
        self.f(o, "block_mask", self.arr, 6, self.u8, fill=255)
        self.f(o, "visit_mask", self.arr, 6, self.u8, fill=0)
        self.f(o, "landscape", self.bits, 2)
        self.f(o, "allowed_terrains", self.bits, 2)
        self.f(o, "class", self.u32)
        self.f(o, "subclass", self.u32)
        self.f(o, "group", self.u8, d=0)
        self.f(o, "is_overlay", self.u8, d=0)
        self.pad(o, "_pad", 16)

    # ---- objects ----------------------------------------------------------------
    def objects(self, m, templates):
        if self.rd:
            n = self.r.u32()
            objs = [{} for _ in range(n)]
            m["objects"] = objs
        else:
            objs = m.get("objects") or []
            self.w.u32(len(objs))
        for i, o in enumerate(objs):
            start = self.pos()
            self.pos3(o)
            ti = self.f(o, "template", self.u32)
            if not 0 <= ti < len(templates):
                raise H3MError(f"object {i}: template index {ti} out of range")
            self.pad(o, "_pad_header", 5)
            tpl = templates[ti]
            cls, sub = tpl["class"], tpl["subclass"]
            kind = kind_of(cls, sub)
            if self.trace is not None:
                self.trace.append((start, f"object #{i} {kind} ({cls}:{sub}) "
                                          f"at {o['x']},{o['y']},{o['z']}"))
            if self.rd:
                o["kind"] = kind
            elif (o.get("kind") not in (None, kind)
                  and (kind in _PAYLOAD_KINDS or o["kind"] in _PAYLOAD_KINDS)):
                raise H3MError(f"object {i}: kind {o.get('kind')!r} does not match "
                               f"template {ti} class {cls}:{sub} ({kind!r})")
            handler = _PAYLOAD_KINDS.get(kind)
            if handler:
                getattr(self, handler)(o, cls, sub)

    def message_guards(self, o):
        """Optional message (+ optional guard army) on artifacts, resources,
        events and Pandora's boxes."""
        if self.rd:
            if not self.r.u8():
                o["message"] = None
                o["guards"] = None
                return
            o["message"] = self.str()
            o["guards"] = self.opt(None, self.army)
            self.pad(o, "_pad_message", 4)
            return
        msg, guards = o.get("message"), o.get("guards")
        if msg is None and guards is None:
            self.w.u8(0)
            return
        self.w.u8(1)
        self.str(msg or "")
        self.opt(guards, self.army)
        self.pad(o, "_pad_message", 4)

    def box_content(self, o):
        self.message_guards(o)
        self.f(o, "experience", self.u32)
        self.f(o, "mana", self.i32)
        self.f(o, "morale", self.i8)
        self.f(o, "luck", self.i8)
        self.f(o, "resources", self.res)
        self.f(o, "primary_skills", self.arr, 4, self.u8, fill=0)
        self.f(o, "secondary_skills", self.vec, self.u8, self.skill_pair)
        self.f(o, "artifacts", self.vec, self.u8, self.art)
        self.f(o, "spells", self.vec, self.u8, self.u8)
        self.f(o, "creatures", self.vec, self.u8, self.stack)
        self.pad(o, "_pad_box", 8)

    def obj_event(self, o, cls, sub):
        self.box_content(o)
        self.f(o, "players", self.bits, 1, d=list(range(8)))
        self.f(o, "computer_activate", self.flag, d=False)
        self.f(o, "remove_after_visit", self.flag, d=True)
        self.pad(o, "_pad", 4)

    def obj_pandora(self, o, cls, sub):
        self.box_content(o)

    def obj_hero(self, o, cls, sub):
        if self.ab:
            self.f(o, "identifier", self.u32)
        self.f(o, "owner", self.u8n, d=-1)
        self.f(o, "hero", self.u8n, d=-1)
        self.f(o, "name", self.opt, self.str)
        if self.sod:
            self.f(o, "experience", self.opt, self.u32)
        else:
            self.f(o, "experience", self.u32, d=0)
        self.f(o, "portrait", self.opt, self.u8)
        self.f(o, "secondary_skills", self.opt, self.vec, self.u32, self.skill_pair)
        self.f(o, "army", self.opt, self.army)
        self.f(o, "formation", self.u8, d=0)
        self.f(o, "artifacts", self.opt, self.dct, self.hero_artifacts)
        self.f(o, "patrol_radius", self.u8n, d=-1)
        if self.ab:
            self.f(o, "biography", self.opt, self.str)
            self.f(o, "gender", self.u8n, d=-1)
        if self.sod:
            self.f(o, "spells", self.opt, self.bits, 9)
        elif self.ab:
            self.f(o, "spell", self.u8n, d=-1)
        if self.sod:
            self.f(o, "primary_skills", self.opt, self.arr, 4, self.u8)
        self.pad(o, "_pad", 16)

    def obj_monster(self, o, cls, sub):
        if self.ab:
            self.f(o, "identifier", self.u32)
        self.f(o, "count", self.u16, d=0)
        self.f(o, "disposition", self.u8, d=1)
        if self.rd:
            if self.r.u8():
                o["message"] = self.str()
                o["resources"] = self.res()
                o["artifact"] = self.art()
            else:
                o["message"] = None
        else:
            if o.get("message") is None:
                self.w.u8(0)
            else:
                self.w.u8(1)
                self.str(o["message"])
                self.res(o.get("resources"))
                self.art(o.get("artifact", -1))
        self.f(o, "never_flees", self.flag, d=False)
        self.f(o, "no_grow", self.flag, d=False)
        self.pad(o, "_pad", 2)

    def obj_sign(self, o, cls, sub):
        self.f(o, "message", self.str, d="")
        self.pad(o, "_pad", 4)

    def quest(self, o):
        t = self.f(o, "type", self.enum, QUEST_TYPES, d="none")
        if t == "none":
            return
        if t == "level":
            self.f(o, "level", self.u32)
        elif t == "primary_skills":
            self.f(o, "primary_skills", self.arr, 4, self.u8, fill=0)
        elif t in ("kill_hero", "kill_monster"):
            self.f(o, "target", self.u32)
        elif t == "artifacts":
            self.f(o, "artifacts", self.vec, self.u8, self.art)
        elif t == "army":
            self.f(o, "army", self.vec, self.u8, self.stack)
        elif t == "resources":
            self.f(o, "resources", self.arr, 7, self.u32, fill=0)
        elif t == "hero":
            self.f(o, "hero", self.u8n)
        elif t == "player":
            self.f(o, "player", self.u8n)
        else:
            raise H3MError(f"unsupported quest type {t!r}")
        self.f(o, "deadline", self.i32, d=-1)
        self.f(o, "first_visit_text", self.str, d="")
        self.f(o, "next_visit_text", self.str, d="")
        self.f(o, "completed_text", self.str, d="")

    def reward(self, o):
        t = self.f(o, "type", self.enum, REWARD_TYPES, d="nothing")
        if t in ("experience", "mana"):
            self.f(o, "amount", self.u32)
        elif t in ("morale", "luck"):
            self.f(o, "amount", self.i8)
        elif t == "resource":
            self.f(o, "resource", self.u8)
            self.f(o, "amount", self.u32)
        elif t == "primary_skill":
            self.f(o, "skill", self.u8)
            self.f(o, "amount", self.u8)
        elif t == "secondary_skill":
            self.f(o, "skill", self.u8)
            self.f(o, "level", self.u8)
        elif t == "artifact":
            self.f(o, "artifact", self.art)
        elif t == "spell":
            self.f(o, "spell", self.u8)
        elif t == "creature":
            self.f(o, "creature", self.cre)
            self.f(o, "amount", self.u16)
        elif t != "nothing":
            raise H3MError(f"unsupported reward type {t!r}")

    def obj_seer_hut(self, o, cls, sub):
        if self.ab:
            q = self.f(o, "quest", self.dct, self.quest, d={"type": "none"})
        elif self.rd:
            a = self.u8n()
            q = {"type": "none"} if a == -1 else {"type": "artifacts", "artifacts": [a]}
            o["quest"] = q
        else:
            q = o.get("quest") or {"type": "none"}
            if q.get("type", "none") == "none":
                self.u8n(-1)
            elif q["type"] == "artifacts" and len(q.get("artifacts", [])) == 1:
                self.u8n(q["artifacts"][0])
            else:
                raise H3MError("RoE seer huts only support a single-artifact quest")
        if q.get("type", "none") != "none":
            self.f(o, "reward", self.dct, self.reward, d={"type": "nothing"})
        else:
            self.pad(o, "_pad_reward", 1)
        self.pad(o, "_pad", 2)

    def obj_quest_guard(self, o, cls, sub):
        self.f(o, "quest", self.dct, self.quest, d={"type": "none"})

    def obj_witch_hut(self, o, cls, sub):
        if self.ab:
            self.f(o, "skills", self.bits, 4, d=list(range(28)))

    def obj_scholar(self, o, cls, sub):
        self.f(o, "bonus_type", self.i8, d=-1)
        self.f(o, "bonus_id", self.u8, d=0)
        self.pad(o, "_pad", 6)

    def obj_garrison(self, o, cls, sub):
        self.f(o, "owner", self.owner32, d=-1)
        self.f(o, "army", self.army)
        if self.ab:
            self.f(o, "removable_units", self.flag, d=True)
        self.pad(o, "_pad", 8)

    def obj_artifact(self, o, cls, sub):
        self.message_guards(o)

    def obj_spell_scroll(self, o, cls, sub):
        self.message_guards(o)
        self.f(o, "spell", self.u32, d=0)

    def obj_resource(self, o, cls, sub):
        self.message_guards(o)
        self.f(o, "amount", self.u32, d=0)
        self.pad(o, "_pad", 4)

    def obj_town(self, o, cls, sub):
        if self.ab:
            self.f(o, "identifier", self.u32)
        self.f(o, "owner", self.u8n, d=-1)
        self.f(o, "name", self.opt, self.str)
        self.f(o, "army", self.opt, self.army)
        self.f(o, "formation", self.u8, d=0)
        if self.rd:
            if self.r.u8():
                o["buildings"] = {"built": self.bits(None, 6),
                                  "forbidden": self.bits(None, 6)}
            else:
                o["buildings"] = None
                o["has_fort"] = self.flag()
        else:
            b = o.get("buildings")
            if b is not None:
                self.w.u8(1)
                self.bits(b.get("built"), 6)
                self.bits(b.get("forbidden"), 6)
            else:
                self.w.u8(0)
                self.flag(o.get("has_fort", True))
        if self.ab:
            self.f(o, "must_have_spells", self.bits, 9)
        self.f(o, "banned_spells", self.bits, 9)
        self.f(o, "events", self.vec, self.u32, self.dct, self.town_event)
        if self.sod:
            self.f(o, "alignment", self.u8n, d=-1)
        self.pad(o, "_pad", 3)

    def timed_event(self, o):
        """Global timed event (and the common head of a town event)."""
        self.f(o, "name", self.str, d="")
        self.f(o, "message", self.str, d="")
        self.f(o, "resources", self.res)
        self.f(o, "players", self.bits, 1, d=list(range(8)))
        if self.sod:
            self.f(o, "human", self.flag, d=True)
        self.f(o, "computer", self.flag, d=False)
        self.f(o, "first_day", self.u16, d=0)
        self.f(o, "repeat_days", self.u16, d=0)
        self.pad(o, "_pad", 16)

    def town_event(self, o):
        self.timed_event(o)
        self.f(o, "buildings", self.bits, 6)
        self.f(o, "creatures", self.arr, 7, self.u16, fill=0)
        self.pad(o, "_pad_town", 4)

    def obj_mine(self, o, cls, sub):
        self.f(o, "owner", self.owner32, d=-1)

    def obj_abandoned_mine(self, o, cls, sub):
        self.f(o, "resources", self.bits, 4)

    def obj_owned(self, o, cls, sub):
        self.f(o, "owner", self.owner32, d=-1)

    def obj_shrine(self, o, cls, sub):
        self.f(o, "spell", self.u32, d=255)

    def obj_grail(self, o, cls, sub):
        self.f(o, "radius", self.i32, d=0)

    def obj_random_dwelling(self, o, cls, sub):
        self.f(o, "owner", self.owner32, d=-1)
        if cls in (216, 217):
            ident = self.f(o, "town_identifier", self.u32, d=0)
            if ident == 0:
                self.f(o, "factions", self.bits, self.factions_bytes,
                       d=list(range(9 if self.ab else 8)))
        if cls in (216, 218):
            self.f(o, "min_level", self.u8, d=0)
            self.f(o, "max_level", self.u8, d=6)

    def obj_hero_placeholder(self, o, cls, sub):
        self.f(o, "owner", self.u8n, d=-1)
        hero = self.f(o, "hero", self.u8n, d=-1)
        if hero == -1:
            self.f(o, "power_rank", self.u8, d=0)


_PAYLOAD_KINDS = {
    "event": "obj_event", "pandora": "obj_pandora",
    "hero": "obj_hero", "prison": "obj_hero",
    "monster": "obj_monster",
    "sign": "obj_sign", "ocean_bottle": "obj_sign",
    "seer_hut": "obj_seer_hut", "quest_guard": "obj_quest_guard",
    "witch_hut": "obj_witch_hut", "scholar": "obj_scholar",
    "garrison": "obj_garrison",
    "artifact": "obj_artifact", "spell_scroll": "obj_spell_scroll",
    "resource": "obj_resource", "town": "obj_town",
    "mine": "obj_mine", "abandoned_mine": "obj_abandoned_mine",
    "dwelling": "obj_owned", "shipyard": "obj_owned", "lighthouse": "obj_owned",
    "shrine": "obj_shrine", "grail": "obj_grail",
    "random_dwelling": "obj_random_dwelling",
    "hero_placeholder": "obj_hero_placeholder",
}


# ---- public API -------------------------------------------------------------------

def parse(data: bytes, encoding: str = "gbk", trace: list | None = None) -> dict:
    """Parse decompressed H3M bytes. `trace`, if given, receives
    (offset, section label) pairs for diagnostics."""
    c = _Codec(bytes(data), encoding, trace)
    try:
        return c.map({})
    except (EOFError, struct.error, H3MError) as e:
        last = trace[-1][1] if trace else "?"
        raise H3MError(f"parse failed at 0x{c.r.pos:x} (in {last}): {e}") from e


def build(m: dict, encoding: str = "gbk", trace: list | None = None) -> bytes:
    """Serialise a map dict (as returned by parse) to decompressed H3M bytes."""
    c = _Codec(None, encoding, trace)
    c.map(m)
    return c.w.getvalue()


def load(path, encoding: str = "gbk") -> dict:
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return parse(raw, encoding)


def save(m: dict, path, encoding: str = "gbk") -> None:
    Path(path).write_bytes(gzip.compress(build(m, encoding), mtime=0))


# ---- blank map ----------------------------------------------------------------------

# Interior (non-transition) frames of the terrain defs. The editor mostly uses
# the first 8 and occasionally the rest: dirt 21-28 (+29-44), grass 49-56
# (+57-72), rock 0-7.
_TERRAIN_VIEWS = {0: (21, 28, 44), 2: (49, 56, 72), 9: (0, 7, 7)}

# The two templates the map editor writes at index 0 and 1 of every map.
_EDITOR_TEMPLATES = [
    {"def": "AVWmrnd0.def", "block_mask": [255, 255, 255, 255, 255, 127],
     "visit_mask": [0, 0, 0, 0, 0, 128], "landscape": [0, 1, 2, 3, 4, 5, 6, 7],
     "allowed_terrains": [0], "class": 71, "subclass": 0, "group": 2, "is_overlay": 0},
    {"def": "AVLholg0.def", "block_mask": [255, 255, 255, 255, 255, 255],
     "visit_mask": [0, 0, 0, 0, 0, 0], "landscape": [2], "allowed_terrains": [2],
     "class": 124, "subclass": 0, "group": 0, "is_overlay": 1},
]


def _tile_view(terrain: int, x: int, y: int, z: int) -> int:
    lo, common, hi = _TERRAIN_VIEWS[terrain]
    h = (x * 73856093 ^ y * 19349663 ^ (z + 1) * 83492791) & 0xFFFFFF
    if hi > common and h % 8 == 0:
        return common + 1 + (h >> 3) % (hi - common)
    return lo + (h >> 3) % (common - lo + 1)


def blank_map(size: int = 36, two_levels: bool = False, name: str = "",
              description: str = "", version: str = "SoD",
              terrain: int = 2) -> dict:
    """A minimal valid empty map as the editor would save it.

    Surface is all grass (terrain=2) or all dirt (terrain=0), the underground
    (if any) is solid rock. Player 0 (red) can be human or computer, any
    faction; players 1-7 are disabled. No objects (the player still needs a
    town or hero to actually play), standard victory/loss, the editor's
    default allowed heroes / artifacts / spells / skills.
    """
    if version not in VERSION_IDS:
        raise H3MError(f"unsupported version {version!r}")
    if terrain not in (0, 2):
        raise H3MError("terrain must be 0 (dirt) or 2 (grass)")
    ab, sod = version in ("AB", "SoD"), version == "SoD"
    n_factions = 9 if ab else 8

    def player(playable: bool) -> dict:
        p = {"can_human": playable, "can_computer": playable, "ai_tactic": 0}
        if sod:
            p["faction_selectable"] = 0
        p.update({"factions": list(range(n_factions)) if playable else [],
                  "random_faction": playable, "main_town": None,
                  "random_hero": False, "main_hero": None})
        if ab:
            p.update({"placeholder_count": 0, "heroes": []})
        return p

    levels = [[[[terrain, _tile_view(terrain, x, y, 0), 0, 0, 0, 0, 0]
                for x in range(size)] for y in range(size)]]
    if two_levels:
        levels.append([[[9, _tile_view(9, x, y, 1), 0, 0, 0, 0, 0]
                        for x in range(size)] for y in range(size)])

    m = {
        "version": version, "has_players": True,
        "terrain": {"size": size, "two_levels": two_levels, "tiles": levels},
        "name": name, "description": description, "difficulty": 1,
    }
    if ab:
        m["level_limit"] = 0
    m["players"] = [player(i == 0) for i in range(8)]
    m["victory"] = {"type": "standard"}
    m["loss"] = {"type": "standard"}
    m["team_count"] = 0
    # Editor defaults: RoE allows all 128 heroes; AB/SoD allow 0-144 except
    # Lord Haart (4), the campaign heroes 145-155 are off.
    m["allowed_heroes"] = [h for h in range(145) if h != 4] if ab else list(range(128))
    if ab:
        m["reserved_heroes"] = []
    if sod:
        m["disposed_heroes"] = []
    if ab:
        # Editor defaults: Vial of Dragon Blood (127) and Armageddon's Blade
        # (128) are banned; SoD also bans the combination artifacts 129-140
        # and the unfinished artifacts 141-143.
        m["disabled_artifacts"] = list(range(127, 144 if sod else 129))
    if sod:
        m["disabled_spells"] = []
        m["disabled_skills"] = []
    m["rumors"] = []
    if sod:
        m["hero_settings"] = []
    m["templates"] = [dict(t, block_mask=list(t["block_mask"]),
                           visit_mask=list(t["visit_mask"]),
                           landscape=list(t["landscape"]),
                           allowed_terrains=list(t["allowed_terrains"]))
                      for t in _EDITOR_TEMPLATES]
    m["objects"] = []
    m["events"] = []
    m["trailing"] = "00" * 124
    return m
