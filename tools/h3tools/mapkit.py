"""High-level story-map authoring on top of h3m.py.

    from h3tools.mapkit import MapKit
    k = MapKit(size=36, name="...", description="...")
    k.town((8, 6), faction="tower", owner=0, name="我们的城", main=True)
    k.hero((9, 7), "daremyth", owner=0, name="小满", biography="...")
    k.sign((10, 8), "路标……")
    k.event((14, 20), "苇语村空无一人……", guards=[("skeleton", 20)])
    k.timed_event(1, "{序章}\\n……")
    k.victory_capture_town((30, 30))
    k.check()                     # overlaps, bounds, reachability
    k.save("out/maps/ch1.h3m")

Positions given to the helpers are the tile the hero steps on (the object's
entrance); the anchor (bottom-right tile that H3M stores) is derived from the
template's visit mask. Names such as "tower", "daremyth", "skeleton",
"speculum", "lightningBolt" are VCMI identifiers looked up in out/gamedb/.

Object templates come from the maps that ship with the game (whatever the
original editor wrote is known to be valid), cached in
out/gamedb/corpus_templates.json.
"""
from __future__ import annotations

import collections
import json
import random
from pathlib import Path

from . import config, h3m

OUT_DIR = Path(__file__).resolve().parents[2] / "out"
GAMEDB = OUT_DIR / "gamedb"
TEMPLATE_CACHE = GAMEDB / "corpus_templates.json"

FACTIONS = ["castle", "rampart", "tower", "inferno", "necropolis", "dungeon",
            "stronghold", "fortress", "conflux"]
RESOURCES = ["wood", "mercury", "ore", "sulfur", "crystal", "gems", "gold"]
PRIMARY = ["attack", "defence", "spellpower", "knowledge"]
ALL_PLAYERS = list(range(8))

# Object classes used by the helpers.
C_ARTIFACT, C_PANDORA, C_EVENT, C_HERO, C_MINE, C_MONSTER = 5, 6, 26, 34, 53, 54
C_BOTTLE, C_PRISON, C_RESOURCE, C_SEER, C_SIGN, C_SCROLL = 59, 62, 79, 83, 91, 93
C_TOWN, C_QUEST_GUARD = 98, 215


def __getattr__(name: str):
    if name == "GAME_DIR":  # older scripts: the original game folder, looked up on use
        return config.game_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class MapKitError(ValueError):
    pass


# ---------------------------------------------------------------- game data

_db_cache: dict[str, list] = {}


def _db(name: str) -> list:
    if name not in _db_cache:
        path = GAMEDB / f"{name}.json"
        if not path.exists():
            raise MapKitError(f"{path} missing: run `python -X utf8 tools/h3tools/gamedb.py build`")
        _db_cache[name] = json.loads(path.read_text(encoding="utf-8"))
    return _db_cache[name]


def lookup(table: str, key) -> dict:
    """Find an entry by numeric id, VCMI identifier or exact Chinese name."""
    for e in _db(table):
        if key == e.get("id") or key == e.get("vcmi") or key == e.get("name"):
            return e
    k = str(key).lower()
    near = [f"{e.get('vcmi')}({e.get('name')})" for e in _db(table)
            if k in str(e.get("vcmi", "")).lower() or k in str(e.get("name", ""))][:8]
    raise MapKitError(f"{table}: no entry {key!r}" + (f"; did you mean {', '.join(near)}?" if near else ""))


def creature_id(c) -> int:
    return c if isinstance(c, int) else lookup("creatures", c)["id"]


def artifact_id(a) -> int:
    return a if isinstance(a, int) else lookup("artifacts", a)["id"]


def spell_id(s) -> int:
    return s if isinstance(s, int) else lookup("spells", s)["id"]


def hero_entry(h) -> dict:
    return lookup("heroes", h)


def faction_id(f) -> int:
    return f if isinstance(f, int) else FACTIONS.index(f)


def army(units) -> list | None:
    """[("skeleton", 20), ...] -> codec army (7 slots)."""
    if units is None:
        return None
    slots = [[creature_id(c), n] for c, n in units]
    if len(slots) > 7:
        raise MapKitError("an army has at most 7 slots")
    return slots + [[-1, 0]] * (7 - len(slots))


def resources(**amounts) -> list:
    unknown = set(amounts) - set(RESOURCES)
    if unknown:
        raise MapKitError(f"unknown resources {unknown}")
    return [amounts.get(r, 0) for r in RESOURCES]


# ---------------------------------------------------------------- templates

def _corpus_maps():
    """Every map shipped with the game: Maps/*.h3m plus all campaign scenarios."""
    from . import h3c
    from .lod import Lod
    game = config.game_dir()
    for p in sorted((game / "Maps").glob("*.h3m")):
        yield h3m.load(p)
    raws = [p.read_bytes() for p in sorted((game / "Maps").glob("*.h3c"))]
    for lod_name in ("H3bitmap.lod", "H3ab_bmp.lod"):
        lod = Lod(game / "Data" / lod_name)
        raws += [lod.read(n) for n in lod.names() if n.lower().endswith(".h3c")]
    for raw in raws:
        members, _, _ = h3c.split_members(raw)
        for data in members[1:]:
            yield h3m.parse(data)


def _harvest_templates() -> list[dict]:
    """Every distinct template used by the maps shipped with the game."""
    seen: dict[str, dict] = {}
    counts: collections.Counter = collections.Counter()
    for m in _corpus_maps():
        for o in m["objects"]:
            t = m["templates"][o["template"]]
            sig = json.dumps(t, sort_keys=True)
            seen[sig] = t
            counts[sig] += 1
    out = [dict(t, count=counts[s]) for s, t in seen.items()]
    out.sort(key=lambda t: (t["class"], t["subclass"], -t["count"]))
    return out


_templates: list[dict] | None = None


def corpus_templates() -> list[dict]:
    global _templates
    if _templates is None:
        if TEMPLATE_CACHE.exists():
            _templates = json.loads(TEMPLATE_CACHE.read_text(encoding="utf-8"))
        else:
            _templates = _harvest_templates()
            TEMPLATE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            TEMPLATE_CACHE.write_text(json.dumps(_templates, ensure_ascii=False), encoding="utf-8")
    return _templates


def objects_txt_template(e: dict) -> dict:
    """Convert an OBJECTS.TXT entry (out/gamedb/templates.json) to the codec's
    template dict. In the text masks index 0 is the anchor tile and indices
    run leftwards, then upwards; in H3M bytes row 5 / bit 7 is the anchor."""
    def mask(chars: str, want: str) -> list[int]:
        return [sum(1 << j for j in range(8) if chars[(5 - i) * 8 + (7 - j)] == want)
                for i in range(6)]
    return {"def": e["def"], "block_mask": mask(e["passability"], "1"),
            "visit_mask": mask(e["visitability"], "1"),
            "landscape": e["allowed_terrains"], "allowed_terrains": e["editor_terrains"],
            "class": e["class"], "subclass": e["subclass"], "group": e["editor_group"],
            "is_overlay": e["overlay"]}


def _hero_template(hero_class: int) -> dict | None:
    """Hero sprites are not in OBJECTS.TXT; the editor writes ahNN_e.def with
    the same masks for every hero class."""
    base = next((t for t in corpus_templates() if t["class"] == C_HERO), None)
    if base is None:
        return None
    return dict({k: v for k, v in base.items() if k != "count"},
                **{"def": f"ah{hero_class:02d}_e.def", "subclass": hero_class, "count": 0})


def find_template(cls: int, sub: int | None = None, terrain: int | None = None,
                  def_name: str | None = None) -> dict:
    cands = [t for t in corpus_templates() if t["class"] == cls
             and (sub is None or t["subclass"] == sub)
             and (def_name is None or t["def"].lower() == def_name.lower())]
    if not cands and cls == C_HERO and sub is not None:
        cands = [t for t in [_hero_template(sub)] if t]
    if not cands:
        cands = [dict(objects_txt_template(e), count=0) for e in _db("templates")
                 if e["class"] == cls and (sub is None or e["subclass"] == sub)
                 and (def_name is None or e["def"].lower() == def_name.lower())]
    if not cands:
        raise MapKitError(f"no template for class {cls} subclass {sub} def {def_name}")
    if terrain is not None:
        fitting = [t for t in cands if terrain in t["allowed_terrains"]]
        cands = fitting or cands
    best = max(cands, key=lambda t: t["count"])
    return {k: v for k, v in best.items() if k != "count"}


def visit_offset(t: dict) -> tuple[int, int] | None:
    """(dx, dy) from the visitable tile to the anchor, scanning like VCMI."""
    for i in range(5, -1, -1):          # bottom row first
        for j in range(7, -1, -1):      # bit 7 is the anchor column
            if t["visit_mask"][i] >> j & 1:
                return 7 - j, 5 - i
    return None


# Classes a hero may enter from the tile above (VCMI isOnVisitableFromTopList);
# editor groups 2-5 (creatures, heroes, artifacts, resources) also qualify.
_FROM_TOP = {29, 82, 86, 11, 59, 8, 111, 33, 219, 81, 12, 9, 212, 215, 22}
# Objects a hero walks through once they are satisfied / defeated / triggered.
_PASS_THROUGH = {C_EVENT, C_MONSTER, 71, 72, 73, 74, 75, 162, 163, 164, C_QUEST_GUARD, 9, 212}


# Objects the hero stands on (events, grail) are not blocking, and VCMI only
# checks the entry direction of blocking objects (CMap::checkForVisitableDir).
_NON_BLOCKING = {C_EVENT, 36}


def visitable_from_top(t: dict) -> bool:
    return t["group"] in (2, 3, 4, 5) or t["class"] in _FROM_TOP or t["class"] in _NON_BLOCKING


def blocked_offsets(t: dict) -> list[tuple[int, int]]:
    """Offsets (left, up) from the anchor of every blocked tile."""
    return [(7 - j, 5 - i) for i in range(6) for j in range(8)
            if not t["block_mask"][i] >> j & 1]


# ---------------------------------------------------------------- the kit

class MapKit:
    def __init__(self, size: int = 36, two_levels: bool = False, name: str = "",
                 description: str = "", difficulty: int = 1, seed: int = 0):
        self.m = h3m.blank_map(size, two_levels, name, description)
        self.m["difficulty"] = difficulty
        self.size = size
        self.rng = random.Random(seed)
        self.blocked: dict[tuple, str] = {}     # (x, y, z) -> label of the object blocking it
        self.entrances: dict[tuple, str] = {}   # visitable tile -> label
        self.entry_rules: dict[tuple, tuple[bool, bool]] = {}  # tile -> (from_top, pass_through)
        self.labels: dict[str, dict] = {}
        self._next_identifier = 1
        for p in self.m["players"]:            # nobody is playable until player() says so
            p.update(can_human=False, can_computer=False, factions=[], random_faction=False)

    # -- terrain ---------------------------------------------------------
    def tile(self, x: int, y: int, z: int = 0) -> list:
        return self.m["terrain"]["tiles"][z][y][x]

    def apply_layout(self, compiled: dict) -> dict:
        """Apply a layout.compile_layout() result; returns its anchors."""
        from . import layout
        before = len(self.m["objects"])
        layout.apply_to_map(self.m, compiled)
        for o in self.m["objects"][before:]:
            t = self.m["templates"][o["template"]]
            for dx, dy in blocked_offsets(t):
                self.blocked[(o["x"] - dx, o["y"] - dy, o["z"])] = "obstacle"
        return compiled.get("anchors", {})

    # -- low level -------------------------------------------------------
    def _template_index(self, t: dict) -> int:
        for i, existing in enumerate(self.m["templates"]):
            if existing == t:
                return i
        self.m["templates"].append(t)
        return len(self.m["templates"]) - 1

    def identifier(self) -> int:
        self._next_identifier += 1
        return self._next_identifier

    def place(self, cls: int, sub: int, at, payload: dict | None = None,
              label: str | None = None, def_name: str | None = None) -> dict:
        x, y, *rest = at
        z = rest[0] if rest else 0
        if not (0 <= x < self.size and 0 <= y < self.size):
            raise MapKitError(f"{label or cls}: {at} is outside the map")
        t = find_template(cls, sub, self.tile(x, y, z)[0], def_name)
        off = visit_offset(t) or (0, 0)
        ax, ay = x + off[0], y + off[1]
        tiles = [(ax - dx, ay - dy, z) for dx, dy in blocked_offsets(t)]
        name = label or f"{h3m.kind_of(cls, sub)}@{x},{y}"
        for tx, ty, tz in tiles:
            if not (0 <= tx < self.size and 0 <= ty < self.size):
                raise MapKitError(f"{name}: tile {(tx, ty)} is outside the map")
            if (tx, ty, tz) in self.blocked:
                raise MapKitError(f"{name}: tile {(tx, ty)} already taken by {self.blocked[(tx, ty, tz)]}")
            if (tx, ty, tz) in self.entrances:
                raise MapKitError(f"{name}: would cover the entrance of {self.entrances[(tx, ty, tz)]}")
            if self.tile(tx, ty, tz)[0] in (8, 9) and self.tile(tx, ty, tz)[0] not in t["allowed_terrains"]:
                raise MapKitError(f"{name}: tile {(tx, ty)} is water/rock")
        for tile in tiles:
            self.blocked[tile] = name
        if visit_offset(t) is not None:
            self.entrances[(x, y, z)] = name
            self.entry_rules[(x, y, z)] = (visitable_from_top(t), cls in _PASS_THROUGH)
        obj = {"x": ax, "y": ay, "z": z, "template": self._template_index(t),
               "kind": h3m.kind_of(cls, sub), **(payload or {})}
        self.m["objects"].append(obj)
        if label:
            self.labels[label] = obj
        obj["_at"] = (x, y, z)   # stripped on save
        return obj

    # -- players, towns, heroes -----------------------------------------
    def player(self, color: int, faction, human: bool = True, computer: bool = True,
               ai_tactic: int = 0):
        p = self.m["players"][color]
        p.update(can_human=human, can_computer=computer, ai_tactic=ai_tactic,
                 factions=[faction_id(faction)], random_faction=False)
        return p

    def town(self, at, faction, owner: int = -1, name: str | None = None,
             main: bool = False, has_fort: bool = True, buildings: dict | None = None,
             garrison=None, events: list | None = None, label: str | None = None) -> dict:
        obj = self.place(C_TOWN, faction_id(faction), at, {
            "identifier": self.identifier(), "owner": owner, "name": name,
            "army": army(garrison), "formation": 0, "buildings": buildings,
            "has_fort": has_fort, "must_have_spells": [], "banned_spells": [],
            "events": events or [], "alignment": -1}, label)
        if main:
            x, y, *rest = at
            self.m["players"][owner]["main_town"] = {
                "generate_hero": False, "town_type": -1, "x": x, "y": y, "z": rest[0] if rest else 0}
        return obj

    def hero(self, at, hero, owner: int, name: str | None = None, biography: str | None = None,
             experience: int | None = None, units=None, artifacts: dict | None = None,
             primary_skills: list | None = None, secondary_skills: list | None = None,
             spells: list | None = None, patrol_radius: int = -1, main: bool = True,
             label: str | None = None) -> dict:
        h = hero_entry(hero)
        obj = self.place(C_HERO, h["class_id"], at, {
            "identifier": self.identifier(), "owner": owner, "hero": h["id"], "name": name,
            "experience": experience, "portrait": None, "secondary_skills": secondary_skills,
            "army": army(units), "formation": 0, "artifacts": artifacts,
            "patrol_radius": patrol_radius, "biography": biography, "gender": -1,
            "spells": [spell_id(s) for s in spells] if spells is not None else None,
            "primary_skills": primary_skills}, label)
        p = self.m["players"][owner]
        p.setdefault("heroes", []).append({"hero": h["id"], "name": name or ""})
        if main:
            p["main_hero"] = {"hero": h["id"], "portrait": -1, "name": name or ""}
        self.m["allowed_heroes"] = [i for i in self.m["allowed_heroes"] if i != h["id"]]
        return obj

    def campaign_hero(self, at, hero, owner: int = 0, label: str | None = None) -> dict:
        """Slot for a hero carried over from the previous campaign scenario
        (hero placeholder, class 214), wired up like the original AB maps:
        the hero is reserved for the campaign and listed for the player.
        Played as a standalone map VCMI removes the placeholder, so give the
        player a town (with generate_hero) or another hero as well."""
        h = hero_entry(hero)
        obj = self.place(214, 0, at, {"owner": owner, "hero": h["id"]}, label or h["vcmi"])
        self.m["reserved_heroes"] = sorted(set(self.m.get("reserved_heroes", [])) | {h["id"]})
        self.m["allowed_heroes"] = [i for i in self.m["allowed_heroes"] if i != h["id"]]
        heroes = self.m["players"][owner].setdefault("heroes", [])
        if not any(e["hero"] == h["id"] for e in heroes):
            heroes.append({"hero": h["id"], "name": ""})
        return obj

    # -- story objects ----------------------------------------------------
    def sign(self, at, text: str, label: str | None = None) -> dict:
        return self.place(C_SIGN, 0, at, {"message": text}, label)

    def ocean_bottle(self, at, text: str, label: str | None = None) -> dict:
        return self.place(C_BOTTLE, 0, at, {"message": text}, label)

    @staticmethod
    def _box(text, guards, experience, mana, morale, luck, gold_etc, primary,
             secondary, artifacts_, spells, creatures) -> dict:
        return {
            "message": text, "guards": army(guards),
            "experience": experience, "mana": mana, "morale": morale, "luck": luck,
            "resources": resources(**(gold_etc or {})),
            "primary_skills": [(primary or {}).get(k, 0) for k in PRIMARY],
            "secondary_skills": secondary or [],
            "artifacts": [artifact_id(a) for a in artifacts_ or []],
            "spells": [spell_id(s) for s in spells or []],
            "creatures": [[creature_id(c), n] for c, n in creatures or []],
        }

    def event(self, at, text: str, guards=None, experience: int = 0, mana: int = 0,
              morale: int = 0, luck: int = 0, give: dict | None = None,
              primary: dict | None = None, secondary=None, artifacts=None, spells=None,
              creatures=None, players=ALL_PLAYERS, computer: bool = False,
              once: bool = True, label: str | None = None) -> dict:
        payload = self._box(text, guards, experience, mana, morale, luck, give, primary,
                            secondary, artifacts, spells, creatures)
        payload.update(players=list(players), computer_activate=computer, remove_after_visit=once)
        return self.place(C_EVENT, 0, at, payload, label)

    def pandora(self, at, text: str, guards=None, experience: int = 0, mana: int = 0,
                morale: int = 0, luck: int = 0, give: dict | None = None,
                primary: dict | None = None, secondary=None, artifacts=None, spells=None,
                creatures=None, label: str | None = None) -> dict:
        return self.place(C_PANDORA, 0, at, self._box(
            text, guards, experience, mana, morale, luck, give, primary, secondary,
            artifacts, spells, creatures), label)

    @staticmethod
    def quest(kind: str, first: str = "", nxt: str = "", done: str = "",
              deadline: int = -1, **fields) -> dict:
        """kind: level(level=), artifacts(artifacts=[..]), army(army=[(c,n)]),
        resources(resources=dict), primary_skills(primary_skills=[4]),
        kill_hero/kill_monster(target=<object dict or identifier>), hero(hero=), player(player=)."""
        q = {"type": kind, "deadline": deadline, "first_visit_text": first,
             "next_visit_text": nxt, "completed_text": done}
        if kind == "artifacts":
            q["artifacts"] = [artifact_id(a) for a in fields["artifacts"]]
        elif kind == "army":
            q["army"] = [[creature_id(c), n] for c, n in fields["army"]]
        elif kind == "resources":
            q["resources"] = resources(**fields["resources"])
        elif kind in ("kill_hero", "kill_monster"):
            target = fields["target"]
            q["target"] = target["identifier"] if isinstance(target, dict) else target
        elif kind == "hero":
            q["hero"] = hero_entry(fields["hero"])["id"]
        else:
            q.update(fields)
        return q

    @staticmethod
    def reward(kind: str, **fields) -> dict:
        """kind: experience/mana(amount=), morale/luck(amount=), resource(resource=, amount=),
        primary_skill(skill=, amount=), secondary_skill(skill=, level=), artifact(artifact=),
        spell(spell=), creature(creature=, amount=)."""
        r = {"type": kind, **fields}
        if kind == "artifact":
            r["artifact"] = artifact_id(r["artifact"])
        elif kind == "spell":
            r["spell"] = spell_id(r["spell"])
        elif kind == "creature":
            r["creature"] = creature_id(r["creature"])
        elif kind == "resource" and isinstance(r["resource"], str):
            r["resource"] = RESOURCES.index(r["resource"])
        elif kind == "primary_skill" and isinstance(r["skill"], str):
            r["skill"] = PRIMARY.index(r["skill"])
        return r

    def seer_hut(self, at, quest: dict, reward: dict, variant: int = 0,
                 label: str | None = None) -> dict:
        return self.place(C_SEER, variant, at, {"quest": quest, "reward": reward}, label)

    def quest_guard(self, at, quest: dict, label: str | None = None) -> dict:
        return self.place(C_QUEST_GUARD, 0, at, {"quest": quest}, label)

    def artifact(self, at, art, message: str | None = None, guards=None,
                 label: str | None = None) -> dict:
        if guards is not None and message is None:
            message = ""
        return self.place(C_ARTIFACT, artifact_id(art), at,
                          {"message": message, "guards": army(guards)}, label)

    def monster(self, at, creature, count: int = 0, message: str | None = None,
                disposition: int = 2, never_flees: bool = False, no_grow: bool = False,
                give: dict | None = None, artifact=None, label: str | None = None) -> dict:
        payload = {"identifier": self.identifier(), "count": count, "disposition": disposition,
                   "message": message, "never_flees": never_flees, "no_grow": no_grow}
        if message is not None:
            payload["resources"] = resources(**(give or {}))
            payload["artifact"] = artifact_id(artifact) if artifact is not None else -1
        return self.place(C_MONSTER, creature_id(creature), at, payload, label)

    def resource(self, at, res: str, amount: int = 0, label: str | None = None) -> dict:
        return self.place(C_RESOURCE, RESOURCES.index(res), at,
                          {"message": None, "guards": None, "amount": amount}, label)

    def mine(self, at, res: str, owner: int = -1, label: str | None = None) -> dict:
        return self.place(C_MINE, RESOURCES.index(res), at, {"owner": owner}, label)

    def obj(self, at, cls: int, sub: int = 0, label: str | None = None,
            def_name: str | None = None, **payload) -> dict:
        """Any other object (e.g. a windmill, a monolith, a specific rock sprite)
        by class/subclass. For objects without an entrance `at` is the anchor."""
        return self.place(cls, sub, at, payload, label, def_name)

    # -- map-level --------------------------------------------------------
    def timed_event(self, day: int, text: str, players=ALL_PLAYERS, human: bool = True,
                    computer: bool = False, repeat_days: int = 0, give: dict | None = None,
                    name: str = "") -> dict:
        ev = {"name": name or f"day {day}", "message": text, "resources": resources(**(give or {})),
              "players": list(players), "human": human, "computer": computer,
              "first_day": day - 1, "repeat_days": repeat_days}
        self.m["events"].append(ev)
        return ev

    def rumor(self, name: str, text: str):
        self.m["rumors"].append({"name": name, "text": text})

    def victory_capture_town(self, at, allow_normal: bool = False, applies_to_ai: bool = False):
        x, y, *rest = at
        self.m["victory"] = {"type": "capture_town", "allow_normal": allow_normal,
                             "applies_to_ai": applies_to_ai, "x": x, "y": y, "z": rest[0] if rest else 0}

    def victory_defeat_hero(self, at, allow_normal: bool = False, applies_to_ai: bool = False):
        x, y, *rest = at
        self.m["victory"] = {"type": "defeat_hero", "allow_normal": allow_normal,
                             "applies_to_ai": applies_to_ai, "x": x, "y": y, "z": rest[0] if rest else 0}

    def victory_acquire_artifact(self, art, allow_normal: bool = False, applies_to_ai: bool = False):
        self.m["victory"] = {"type": "acquire_artifact", "allow_normal": allow_normal,
                             "applies_to_ai": applies_to_ai, "artifact": artifact_id(art)}

    def ban_artifacts(self, *arts):
        """Keep artifacts out of random spawns and artifact merchants (e.g. a
        unique story item that must exist only once on the map)."""
        ids = {artifact_id(a) for a in arts}
        self.m["disabled_artifacts"] = sorted(set(self.m.get("disabled_artifacts", [])) | ids)

    def loss_lose_hero(self, at):
        x, y, *rest = at
        self.m["loss"] = {"type": "lose_hero", "x": x, "y": y, "z": rest[0] if rest else 0}

    # -- checks & output --------------------------------------------------
    def passable(self, x: int, y: int, z: int = 0) -> bool:
        return (x, y, z) not in self.blocked and self.tile(x, y, z)[0] not in (8, 9)

    def reachable_from(self, start) -> set:
        """Tiles a hero can walk to from `start` (8-neighbour, objects entered
        from the side or below, entrances are dead ends except guards/gates)."""
        x0, y0, *rest = start
        z = rest[0] if rest else 0
        seen = {(x0, y0, z)}
        todo = [(x0, y0, z)]
        while todo:
            x, y, z = todo.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy, z)
                    if n in seen or not (0 <= n[0] < self.size and 0 <= n[1] < self.size):
                        continue
                    if n in self.entrances:
                        from_top, through = self.entry_rules[n]
                        if dy == 1 and not from_top:   # moving down = entering from above
                            continue
                        seen.add(n)
                        if through:
                            todo.append(n)
                        continue
                    if self.passable(*n):
                        seen.add(n)
                        todo.append(n)
        return seen

    def check(self, start=None) -> list[str]:
        problems = []
        if start is None:
            heroes = [o for o in self.m["objects"] if o["kind"] == "hero" and o.get("owner", -1) >= 0
                      and self.m["players"][o["owner"]]["can_human"]]
            if not heroes:   # a campaign scenario may start with only a carried-over hero
                heroes = [o for o in self.m["objects"] if o["kind"] == "hero_placeholder"
                          and o.get("owner", -1) >= 0 and self.m["players"][o["owner"]]["can_human"]]
            start = heroes[0]["_at"] if heroes else None
        if start is not None:
            reach = self.reachable_from(start)
            for tile, label in self.entrances.items():
                if tile not in reach:
                    problems.append(f"unreachable from {start[:2]}: {label} at {tile[:2]}")
        for i, p in enumerate(self.m["players"]):
            if (p["can_human"] or p["can_computer"]) and not any(
                    o.get("owner") == i for o in self.m["objects"] if o["kind"] in ("hero", "town")):
                problems.append(f"player {i} is playable but owns no hero or town")
        return problems

    def build(self) -> dict:
        m = json.loads(json.dumps(self.m, default=list))
        for o in m["objects"]:
            o.pop("_at", None)
        return m

    def save(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        m = self.build()
        h3m.parse(h3m.build(m))   # fail early if the dict does not round-trip
        h3m.save(m, path)
        return path
