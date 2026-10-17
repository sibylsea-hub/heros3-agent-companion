"""ASCII layout compiler: one character per tile -> terrain, roads, rivers,
obstacles and named anchors.

    from h3tools import layout
    compiled = layout.compile_layout(TEXT, seed=7)
    layout.apply_to_map(map_dict, compiled)      # map_dict from h3m.blank_map
    compiled["anchors"]["T"]                     # [(x, y), ...]

Default legend (DEFAULT_LEGEND):
    .  grass        ,  dirt        :  sand         *  snow
    %  swamp        "  rough       ^  lava         _  subterranean
    ~  water        X  rock
    #  impassable obstacles, terrain inferred (see below)
    =  dirt road    -  gravel road +  cobblestone road   (terrain inferred)
    w  river (clear water)                                (terrain inferred)
    A-Z, 0-9 (not in the legend)  anchors: passable tiles whose coordinates
       are returned; terrain inferred
A custom legend maps a character to a terrain id (int) or to a dict with any
of {"terrain": id or None, "road": 1-3, "river": 1-4, "obstacle": bool,
"classes": [object class ids]}; it is merged over the default. Use it to
state the terrain under obstacles explicitly, e.g. {"&": {"terrain": 3,
"obstacle": True}} = obstacles on snow, or to pick the kind of obstacle,
e.g. {"m": {"obstacle": True, "classes": [134, 136, 147]}} = only mountains,
outcroppings and rocks (tiles no such obstacle fits get any 1-tile one).

Inferred terrain ("terrain": None):
  * roads, rivers and anchors are always land: the most common land terrain
    among the 8 neighbours (water and rock ignored); with no land neighbour
    (a 1-tile bridge) the nearest land terrain, searching along the
    road/anchor chain first and then anywhere;
  * obstacles: the most common land terrain among the neighbours; water only
    when the obstacle is surrounded by water.
Inference runs outwards in rings, so wide '#' areas take the terrain of the
nearest explicit tiles.

Repair: H3's tile set cannot draw every shape. A land tile needs a 2x2 block
of its own terrain (or a 1-wide diagonal), water/rock need a 2x2 block and
may not touch themselves only diagonally (VCMI getInvalidTiles). Invalid
tiles are fixed locally, the cheapest of: land -> a neighbouring land
terrain or sand (sand fits next to anything, so a 1-tile bridge over a lake
becomes a sand bar); water -> grow into a neighbouring obstacle/plain tile,
else shrink. Anchors/roads/rivers never become water or rock. Every changed
tile is listed in compiled["changes"] as {(x, y): (wanted, got)}.
"""
from __future__ import annotations

import random
from collections import Counter, deque

from . import h3m, obstacles as obs, terrain as T

__all__ = ["DEFAULT_LEGEND", "LayoutError", "compile_layout", "apply_to_map",
           "parse_layout"]

WATER, ROCK, SAND = T.WATER, T.ROCK, T.SAND

DEFAULT_LEGEND = {
    ".": T.GRASS, ",": T.DIRT, ":": T.SAND, "*": T.SNOW, "%": T.SWAMP,
    '"': T.ROUGH, "^": T.LAVA, "_": T.SUBTERRANEAN, "~": T.WATER, "X": T.ROCK,
    "#": {"terrain": None, "obstacle": True},
    "=": {"terrain": None, "road": T.ROAD_DIRT},
    "-": {"terrain": None, "road": T.ROAD_GRAVEL},
    "+": {"terrain": None, "road": T.ROAD_COBBLESTONE},
    "w": {"terrain": None, "river": T.RIVER_CLEAR},
}


class LayoutError(ValueError):
    pass


def _norm(entry):
    if isinstance(entry, int):
        return {"terrain": entry, "road": 0, "river": 0, "obstacle": False, "classes": None}
    e = {"terrain": None, "road": 0, "river": 0, "obstacle": False, "classes": None}
    e.update(entry)
    if e["classes"] is not None:
        e["classes"] = frozenset(e["classes"])
    return e


def parse_layout(text: str, legend: dict | None = None):
    """-> (n, cells[y][x] = normalised legend entry or {"anchor": ch, ...})."""
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    n = len(lines)
    if n == 0:
        raise LayoutError("empty layout")
    bad = [i for i, ln in enumerate(lines) if len(ln) != n]
    if bad:
        raise LayoutError(f"layout must be square: {n} rows but row {bad[0]} has "
                          f"{len(lines[bad[0]])} characters")
    leg = {k: _norm(v) for k, v in DEFAULT_LEGEND.items()}
    for k, v in (legend or {}).items():
        leg[k] = _norm(v)
    cells = []
    for y, ln in enumerate(lines):
        row = []
        for x, ch in enumerate(ln):
            if ch in leg:
                row.append(dict(leg[ch], char=ch))
            elif ch.isascii() and (ch.isupper() or ch.isdigit()):
                row.append({"terrain": None, "road": 0, "river": 0, "obstacle": False,
                            "anchor": ch, "char": ch})
            else:
                raise LayoutError(f"unknown layout character {ch!r} at ({x}, {y})")
        cells.append(row)
    return n, cells


def _neighbours(x, y, n):
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if (dx or dy) and 0 <= x + dx < n and 0 <= y + dy < n:
                yield x + dx, y + dy


def _is_land(t):
    return t is not None and t != WATER and t != ROCK


def _infer_terrain(n, cells):
    """Fill terrain None in rings outwards from the explicit tiles."""
    ter = [[c["terrain"] for c in row] for row in cells]
    land_only = [[bool(c["road"] or c["river"] or c.get("anchor")) for c in row] for row in cells]
    todo = {(x, y) for y in range(n) for x in range(n) if ter[y][x] is None}

    def pick(counts):
        # most common; ties broken by terrain id for determinism
        return max(sorted(counts), key=lambda t: counts[t])

    while todo:
        new = {}
        for x, y in todo:
            near = [ter[yy][xx] for xx, yy in _neighbours(x, y, n) if ter[yy][xx] is not None]
            if not near:
                continue
            land = Counter(t for t in near if _is_land(t))
            if land:
                new[(x, y)] = pick(land)
            elif not land_only[y][x]:
                # obstacle with only water/rock known around it: water only if
                # nothing around it is still undecided
                if all(ter[yy][xx] is not None for xx, yy in _neighbours(x, y, n)):
                    new[(x, y)] = pick(Counter(near))
        if not new:
            break
        for (x, y), t in new.items():
            ter[y][x] = t
        todo -= set(new)
    # leftovers: land-only tiles with no land neighbour (bridges), obstacles
    # in a sea of other undecided obstacles.
    for x, y in sorted(todo, key=lambda p: (p[1], p[0])):
        if ter[y][x] is not None:
            continue
        ter[y][x] = _nearest_land(n, ter, cells, x, y, land_only)
    return ter, land_only


def _nearest_land(n, ter, cells, x0, y0, land_only):
    """Nearest land terrain: first along connected road/river/anchor tiles,
    then by plain distance; grass if the map has no land at all."""
    for chain_only in (True, False):
        seen = {(x0, y0)}
        q = deque([(x0, y0)])
        while q:
            x, y = q.popleft()
            for xx, yy in _neighbours(x, y, n):
                if (xx, yy) in seen:
                    continue
                seen.add((xx, yy))
                if _is_land(ter[yy][xx]):
                    return ter[yy][xx]
                if not chain_only or land_only[yy][xx]:
                    q.append((xx, yy))
    return T.GRASS


def _repair(n, types, protect_land, explicit, max_passes=30):
    """Make every tile drawable (VCMI tile validity). types[y][x] is edited
    in place. Returns {(x, y): (before, after)}."""
    grid = T._Terrain([types], n, n)
    before = [row[:] for row in types]

    def invalid_near(x, y, r=2):
        c = 0
        for yy in range(max(0, y - r), min(n, y + r + 1)):
            for xx in range(max(0, x - r), min(n, x + r + 1)):
                if not grid.tile_valid(xx, yy, 0):
                    c += 1
        return c

    def try_options(x, y, options):
        """options: [(cost, [(xx, yy, t), ...])]; apply the best."""
        best = None
        for cost, changes in options:
            old = [(xx, yy, types[yy][xx]) for xx, yy, _ in changes]
            for xx, yy, t in changes:
                types[yy][xx] = t
            score = (invalid_near(x, y, 3), cost)
            for xx, yy, t in old:
                types[yy][xx] = t
            if best is None or score < best[0]:
                best = (score, changes)
        if best is None:
            return False
        for xx, yy, t in best[1]:
            types[yy][xx] = t
        return True

    for _ in range(max_passes):
        bad = [(x, y) for y in range(n) for x in range(n) if not grid.tile_valid(x, y, 0)]
        if not bad:
            break
        for x, y in bad:
            if grid.tile_valid(x, y, 0):
                continue
            t = types[y][x]
            nb = list(_neighbours(x, y, n))
            options = []
            if _is_land(t) or t is None:
                counts = Counter(types[yy][xx] for xx, yy in nb if _is_land(types[yy][xx])
                                 and types[yy][xx] != t)
                for c, k in counts.most_common():
                    options.append((1 + (2 if explicit[y][x] else 0) - 0.1 * k, [(x, y, c)]))
                if t != SAND:
                    options.append((2 + (2 if explicit[y][x] else 0), [(x, y, SAND)]))
                # or grow this terrain into a flexible neighbour
                for xx, yy in nb:
                    if types[yy][xx] != t and not explicit[yy][xx] and \
                            (_is_land(types[yy][xx]) or not protect_land[yy][xx]):
                        options.append((1.5, [(xx, yy, t)]))
            else:  # water / rock
                for xx, yy in nb:
                    if types[yy][xx] != t and not protect_land[yy][xx]:
                        options.append((1 + (2 if explicit[yy][xx] else 0), [(xx, yy, t)]))
                land = Counter(types[yy][xx] for xx, yy in nb if _is_land(types[yy][xx]))
                repl = land.most_common(1)[0][0] if land else SAND
                options.append((3 + (3 if explicit[y][x] else 0), [(x, y, repl)]))
            try_options(x, y, options)
    return {(x, y): (before[y][x], types[y][x]) for y in range(n) for x in range(n)
            if before[y][x] != types[y][x]}


def compile_layout(text: str, legend: dict | None = None, seed: int = 0,
                   z: int = 0, decorations: int = T.DEFAULT_DECORATIONS) -> dict:
    """Compile an ASCII layout. Returns a dict:
        size       N
        z          level the layout is for (apply_to_map writes it there)
        tiles      [[7-int codec tile]] rows (tiles[y][x]) with views computed
        obstacles  [{"template", "x", "y", "z"}] (anchor = bottom-right tile)
        anchors    {char: [(x, y), ...]} in reading order
        changes    {(x, y): (wanted terrain, final terrain)} made by repair
                   (wanted = explicit or inferred terrain)
        failed     [(x, y)] tiles drawn with an approximate view
    """
    rng = random.Random(seed)
    n, cells = parse_layout(text, legend)
    inferred, land_only = _infer_terrain(n, cells)
    wanted = [row[:] for row in inferred]
    explicit = [[c["terrain"] is not None for c in row] for row in cells]
    types = [row[:] for row in inferred]
    changes = _repair(n, types, land_only, explicit)
    changes = {p: (wanted[p[1]][p[0]], v[1]) for p, v in changes.items()}

    tiles = [[[types[y][x], 0, 0, 0, 0, 0, 0] for x in range(n)] for y in range(n)]
    level = [tiles]
    failed = T.update_terrain_views(level, n, n, 1, rng, decorations=decorations)
    roads = {}
    rivers = {}
    for y in range(n):
        for x in range(n):
            c = cells[y][x]
            if c["road"]:
                roads.setdefault(c["road"], []).append((x, y, 0))
            if c["river"]:
                rivers.setdefault(c["river"], []).append((x, y, 0))
    for kind, cl in sorted(roads.items()):
        T.draw_road(level, cl, kind, rng)
    for kind, cl in sorted(rivers.items()):
        T.draw_river(level, cl, kind, rng)

    placed = []
    kinds = sorted({c["classes"] for row in cells for c in row if c["obstacle"]},
                   key=lambda k: (k is not None, sorted(k or ())))
    for kind in kinds:
        mask = [[c["obstacle"] and c["classes"] == kind for c in row] for row in cells]
        allowed = None if kind is None else (lambda o, kind=kind: o.template["class"] in kind)
        placed += obs.fill_obstacles(mask, types, rng, z=z, allowed=allowed)

    anchors: dict[str, list] = {}
    for y in range(n):
        for x in range(n):
            ch = cells[y][x].get("anchor")
            if ch:
                anchors.setdefault(ch, []).append((x, y))
    return {"size": n, "z": z, "tiles": tiles, "obstacles": placed,
            "anchors": anchors, "changes": changes,
            "failed": [(x, y) for x, y, _ in failed]}


def apply_to_map(map_dict: dict, compiled: dict) -> dict:
    """Write the compiled terrain into level compiled["z"] of a codec map dict
    and append the obstacles as objects (templates deduplicated against the
    map's existing ones). Returns map_dict."""
    terr = map_dict["terrain"]
    n = compiled["size"]
    if terr["size"] != n:
        raise LayoutError(f"layout is {n}x{n} but the map is {terr['size']}x{terr['size']}")
    z = compiled.get("z", 0)
    if z >= len(terr["tiles"]):
        raise LayoutError(f"map has no level {z}")
    terr["tiles"][z] = [[list(t) for t in row] for row in compiled["tiles"]]
    templates = map_dict.setdefault("templates", [])
    objects = map_dict.setdefault("objects", [])
    for o in compiled["obstacles"]:
        tpl = o["template"]
        try:
            idx = templates.index(tpl)
        except ValueError:
            templates.append(dict(tpl))
            idx = len(templates) - 1
        objects.append({"x": o["x"], "y": o["y"], "z": o.get("z", z), "template": idx,
                        "kind": h3m.kind_of(tpl["class"], tpl["subclass"])})
    return map_dict
