"""Obstacle catalogue and mask filler (trees, mountains, rocks, lakes, ...).

    cat = catalogue()                       # list of Obstacle
    placed = fill_obstacles(mask, terrain_grid, random.Random(1))
    # -> [{"template": <codec template dict>, "x": ax, "y": ay, "z": 0}, ...]

Catalogue: every OBJECTS.TXT entry (out/gamedb/templates.json, written by
gamedb.py) of a decorative class (VCMI's obstacle classes 114-161, 177, 199,
206-211; see OBSTACLE_CLASSES) that blocks at least one tile and has no
visitable tile. Templates also used by the original maps are taken verbatim
from the corpus (mapkit.corpus_templates(), which also gives a usage count
used as a weight); the others are converted with mapkit.objects_txt_template.

Coordinates: like H3M, an object's (x, y) is its anchor = the bottom-right
tile of its 8x6 mask; block_mask row 5 / bit 7 is the anchor and blocked
tiles extend up/left (bit clear = blocked). See VCMI ObjectTemplate::readMap /
MapReaderH3M::readObjectTemplate.

Terrain fit: an obstacle may cover a tile only when that tile's terrain is
in the template's editor terrain list (codec "allowed_terrains" -- the
editor menu it appears under), so snow gets snowy trees, water gets reefs
and water rocks, etc.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import mapkit

__all__ = ["OBSTACLE_CLASSES", "Obstacle", "catalogue", "fill_obstacles",
           "ObstacleError"]

# Static decoration classes (VCMI MapObjectID: brush .. reef, the duplicate
# lake/trees ids, and the AB/SoD hills/rocks/foliage). Classes with no
# blocking template (kelp, river deltas, flowers that never block ...) drop
# out automatically.
OBSTACLE_CLASSES = frozenset(list(range(114, 162)) + [177, 199, 206, 207, 208, 209, 210, 211])
_EXCLUDE_CLASSES = frozenset((143,))  # river delta: placed by rivers, not fillers


class ObstacleError(ValueError):
    pass


@dataclass
class Obstacle:
    template: dict                      # codec template dict
    blocked: list                       # [(dx, dy)] offsets left/up from the anchor
    terrains: frozenset                 # editor terrains it fits
    weight: float = 1.0
    size: int = field(init=False)

    def __post_init__(self):
        self.size = len(self.blocked)


_catalogue: list[Obstacle] | None = None


def catalogue() -> list[Obstacle]:
    global _catalogue
    if _catalogue is not None:
        return _catalogue
    corpus = {}
    for t in mapkit.corpus_templates():
        key = (t["def"].lower(), t["class"], t["subclass"])
        if key not in corpus or t["count"] > corpus[key]["count"]:
            corpus[key] = t
    out = []
    seen = set()
    for e in mapkit._db("templates"):
        cls = e["class"]
        if cls not in OBSTACLE_CLASSES or cls in _EXCLUDE_CLASSES:
            continue
        if e["editor_group"] != 0 or not e["blocked"] or e["visitable"]:
            continue
        key = (e["def"].lower(), cls, e["subclass"])
        if key in seen:
            continue
        seen.add(key)
        c = corpus.get(key)
        if c is not None:
            tpl = {k: v for k, v in c.items() if k != "count"}
            count = c["count"]
        else:
            tpl = mapkit.objects_txt_template(e)
            # the editor never writes water into a land object's landscape bits
            if 8 not in tpl["allowed_terrains"]:
                tpl["landscape"] = [t for t in tpl["landscape"] if t != 8]
            count = 0
        blocked = mapkit.blocked_offsets(tpl)
        if not blocked or tpl["visit_mask"] != [0] * 6:
            continue
        terrains = frozenset(tpl["allowed_terrains"])
        if not terrains:
            continue
        # common objects a bit more likely, but rare ones still show up
        out.append(Obstacle(tpl, blocked, terrains, 1.0 + min(count, 400) ** 0.5))
    if not out:
        raise ObstacleError("no obstacle templates found (run gamedb.py first?)")
    _catalogue = out
    return out


def fill_obstacles(blocked_mask, terrain_grid, rng=None, z=0, allowed=None,
                   catalogue_=None, max_size=None):
    """Cover every True cell of blocked_mask[y][x] with obstacles.

    terrain_grid[y][x] gives the terrain id per tile. Obstacles never block a
    tile outside the mask and never overlap each other's blocked tiles; the
    anchor (x, y) is always inside the map, blocked tiles too, while purely
    decorative (passable) parts of a sprite may hang over neighbouring tiles
    or the top/left map edge, as in the original maps.

    Biggest first: for each size (blocked-tile count) from large to small,
    every still-free mask tile is tried (random order) with the obstacles of
    that size that fit its terrain; leftovers get 1-tile obstacles.

    `allowed(obstacle) -> bool` filters the catalogue (e.g. by class);
    `max_size` caps the blocked-tile count. Returns a list of
    {"template": dict, "x": int, "y": int, "z": int}; raises ObstacleError if
    some masked tile cannot be covered (no 1-tile obstacle for its terrain).
    """
    rng = rng if rng is not None else random.Random(0)
    h = len(blocked_mask)
    w = len(blocked_mask[0]) if h else 0
    cat = catalogue_ if catalogue_ is not None else catalogue()
    if allowed is not None:
        cat = [o for o in cat if allowed(o)]
    if max_size is not None:
        cat = [o for o in cat if o.size <= max_size]
    # rock (9) is impassable by itself and has no obstacles
    free = {(x, y) for y in range(h) for x in range(w)
            if blocked_mask[y][x] and terrain_grid[y][x] != 9}
    placed = []

    def fits(o, ax, ay):
        if not (0 <= ax < w and 0 <= ay < h):
            return False
        for dx, dy in o.blocked:
            p = (ax - dx, ay - dy)
            if p not in free or terrain_grid[p[1]][p[0]] not in o.terrains:
                return False
        return True

    def place(o, ax, ay):
        for dx, dy in o.blocked:
            free.discard((ax - dx, ay - dy))
        placed.append({"template": dict(o.template), "x": ax, "y": ay, "z": z})

    sizes = sorted({o.size for o in cat}, reverse=True)
    by_size = {s: [o for o in cat if o.size == s] for s in sizes}
    for s in sizes:
        if s > len(free):
            continue
        group = by_size[s]
        order = sorted(free)
        rng.shuffle(order)
        for p in order:
            if p not in free:
                continue
            ter = terrain_grid[p[1]][p[0]]
            cands = [o for o in group if ter in o.terrains]
            if not cands:
                continue
            # weighted random order without replacement
            keyed = sorted(cands, key=lambda o: -rng.random() ** (1.0 / o.weight))
            done = False
            for o in keyed:
                offs = list(o.blocked)
                rng.shuffle(offs)
                for dx, dy in offs:       # put tile p under blocked cell (dx, dy)
                    ax, ay = p[0] + dx, p[1] + dy
                    if fits(o, ax, ay):
                        place(o, ax, ay)
                        done = True
                        break
                if done:
                    break
    if free:
        # last resort: any 1-tile obstacle whose landscape allows the terrain
        ones = [o for o in (catalogue_ or catalogue()) if o.size == 1]
        for p in sorted(free):
            ter = terrain_grid[p[1]][p[0]]
            cands = [o for o in ones if ter in o.template.get("landscape", [])
                     and 0 <= p[0] + o.blocked[0][0] < w and 0 <= p[1] + o.blocked[0][1] < h]
            if not cands:
                raise ObstacleError(f"no obstacle can block tile {p} (terrain {ter})")
            o = rng.choice(cands)
            place(o, p[0] + o.blocked[0][0], p[1] + o.blocked[0][1])
    return placed
