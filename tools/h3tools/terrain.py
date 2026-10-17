"""Terrain, road and river views for H3M maps (port of VCMI's map editor logic).

The H3M tile is 7 bytes; the codec (h3m.py) exposes it as a 7-int list
``[terrain, view, river, river_dir, road, road_dir, flags]`` stored in
``map["terrain"]["tiles"][z][y][x]``.

Terrain ids: 0 dirt, 1 sand, 2 grass, 3 snow, 4 swamp, 5 rough,
6 subterranean, 7 lava, 8 water, 9 rock.
River ids: 0 none, 1 clear (water), 2 icy, 3 mud, 4 lava.
Road ids: 0 none, 1 dirt, 2 gravel, 3 cobblestone.

Flag byte (``tile[6]``), verified against VCMI (MapRenderer.cpp,
CDrawRoadsOperation.cpp, TerrainTile.h) and the original-map corpus:

    bit 0  terrain image flipped horizontally
    bit 1  terrain image flipped vertically
    bit 2  river image flipped horizontally
    bit 3  river image flipped vertically
    bit 4  road image flipped horizontally
    bit 5  road image flipped vertically
    bit 6  "coast": set on every LAND tile that has a water tile among its
           8 neighbours, never on water (100% of 1.9M corpus tiles agree)
    bit 7  favourable winds -- never stored by the editor; the game sets it
           at runtime around Favorable Winds objects

i.e. each 2-bit flip field is 0 none, 1 horizontal, 2 vertical, 3 both.

What is ported:
  * CDrawTerrainOperation (lib/mapping/CMapOperation.cpp): pattern matching
    with config/terrainViewPatterns.json, view/flip selection
    (updateTerrainViews) and the selection expansion that repairs terrain
    shapes the tile set cannot draw (expandSelection / getInvalidTiles).
  * CDrawLinesOperation (lib/mapping/CDrawRoadsOperation.cpp) for roads and
    rivers, with corrections measured on the corpus (see _ROAD_PATTERNS).

Public API:
    update_terrain_views(tiles, width, height, levels, rng, ...)
    paint_terrain(tiles, cells, terrain, rng, ...)   VCMI-style brush
    fill(tiles, cells, terrain)                      raw type assignment
    update_coast_flags(tiles)
    draw_road(tiles, cells, road_type, rng) / draw_river(...)
    update_road_views(...) / update_river_views(...)
    terrain_view_info(...)   pattern id/flip/frame ranges (for validation)
"""
from __future__ import annotations

import heapq
import random

__all__ = [
    "DIRT", "SAND", "GRASS", "SNOW", "SWAMP", "ROUGH", "SUBTERRANEAN", "LAVA",
    "WATER", "ROCK", "TERRAIN_NAMES", "ROAD_DIRT", "ROAD_GRAVEL",
    "ROAD_COBBLESTONE", "RIVER_CLEAR", "RIVER_ICY", "RIVER_MUD", "RIVER_LAVA",
    "FLAG_TERRAIN_FLIP", "FLAG_RIVER_FLIP", "FLAG_ROAD_FLIP", "FLAG_COAST",
    "FLAG_FAVORABLE_WINDS",
    "update_terrain_views", "terrain_view_info", "paint_terrain", "fill",
    "find_invalid_tiles", "repair_terrain", "update_coast_flags",
    "draw_road", "draw_river", "update_road_views", "update_river_views",
    "line_view_info", "native_river",
]

DIRT, SAND, GRASS, SNOW, SWAMP, ROUGH, SUBTERRANEAN, LAVA, WATER, ROCK = range(10)
TERRAIN_NAMES = ["dirt", "sand", "grass", "snow", "swamp", "rough",
                 "subterranean", "lava", "water", "rock"]
ROAD_DIRT, ROAD_GRAVEL, ROAD_COBBLESTONE = 1, 2, 3
RIVER_CLEAR, RIVER_ICY, RIVER_MUD, RIVER_LAVA = 1, 2, 3, 4

FLAG_TERRAIN_FLIP = 0b00000011
FLAG_RIVER_FLIP = 0b00001100
FLAG_ROAD_FLIP = 0b00110000
FLAG_COAST = 0b01000000
FLAG_FAVORABLE_WINDS = 0b10000000

# terrains.json: sand, water and rock need a sand transition; rock is the only
# impassable terrain; water is the only water terrain.
_TRANSITION_REQUIRED = frozenset((SAND, WATER, ROCK))
_GROUP = {DIRT: "dirt", SAND: "sand", WATER: "water", ROCK: "rock"}  # rest: "normal"


def native_river(terrain: int) -> int:
    """River type VCMI's RMG pairs with a terrain (terrains.json "river")."""
    return {DIRT: RIVER_MUD, SAND: RIVER_MUD, ROUGH: RIVER_MUD, SNOW: RIVER_ICY,
            LAVA: RIVER_LAVA}.get(terrain, RIVER_CLEAR)


# ---------------------------------------------------------------------------
# Pattern tables -- config/terrainViewPatterns.json, verbatim and in order
# (the order matters: the first matching pattern wins). HotA mappings dropped.
# ---------------------------------------------------------------------------
_VIEW_PATTERNS = [
    ("x1", "T N N N N T N T T", {"normal": "73,74", "dirt": "45"}, 0, False),
    ("x2", "D D N D N N N N S", {"normal": "75"}, 0, False),
    ("x3", "S S N S N N N N D", {"normal": "76"}, 0, False),
    ("x4", "N N S N N D S D D", {"normal": "77"}, 0, False),
    ("x5", "N N D N N D D D S", {"normal": "78"}, 0, False),
    ("n1", "N N N N N N N N N", {"normal": "49-56,57-72", "dirt": "21-28,29-44",
                                 "sand": "0-11,12-23", "water": "20-32",
                                 "rock": "0-7"}, 0, True),
    ("m1", "T N N N N N N N T", {"normal": "40,42", "dirt": "20"}, 0, False),
    ("m2", "D N N N N N N N S", {"normal": "41"}, 0, False),
    ("m3", "N N D,N N N D S D,N D,N", {"normal": "43"}, 0, False),
    ("m4", "N N S N N D,N D,N D D,N", {"normal": "44"}, 0, False),
    ("m5", "N N D N N D N N S", {"normal": "45"}, 0, False),
    ("m6", "N N N N N N D,N D S", {"normal": "46"}, 0, False),
    ("m7", "N N ? N N S D-1,N D-1,N ?", {"normal": "47"}, 1, False),
    ("m8", "N N D-1,N N N D-1,N ? S ?", {"normal": "48"}, 1, False),
    ("s2", "? N N T N N ? N N", {"normal": "4-7,24-27", "dirt": "4-7",
                                 "water": "4-7", "rock": "2D:16-19"}, 0, False),
    ("s3", "? T ? N N N N N N", {"normal": "8-11,28-31", "dirt": "8-11",
                                 "water": "8-11", "rock": "2D:20-23"}, 0, False),
    ("s4", "N N N N N s3-1,m7-1,m8-1,s1-1 N s2-1,m7-1,m8-1,s1-1 T",
     {"normal": "12-15,32-35", "dirt": "12-15", "water": "12-15",
      "rock": "4D:24-31"}, 2, False),
    ("s5", "T T ? T N s6-1,m1-1,m2-1,N ?,x1-1,s1-1 s6-1,m1-1,m2-1,N N",
     {"normal": "16-17,36-37", "dirt": "16-17", "water": "16-17",
      "rock": "4D:32-39"}, 1, False),
    ("s6", "N N N N N s5-1,N N s5-1,N T", {"normal": "18-19,38-39", "dirt": "18-19",
                                           "water": "18-19", "rock": "4D:40-47"}, 1, False),
    ("s1", "? S-1,? ? S-1,? N N ? N N", {"normal": "20-23", "dirt": "0-3",
                                         "water": "0-3", "rock": "4D:8-15"}, 1, False),
    ("s7", "? D-1,? ? D-1,? N N ? N N", {"normal": "0-3", "rock": "4D:8-15"}, 1, False),
]

_TYPE_PATTERNS = [
    ("n1", "N! N! ? N! N! ? ? ? ?"),
    ("n2", "N! N! D,S D,S N! N! D,S D,S N!"),
    ("n3", "D,S D,S N! D,S N! N! N! N! D,S"),
    ("s1", "T N N N N N,T N N T"),
    ("s2", "N N,T T N N N T N N"),
]

_STANDARD = frozenset(("D", "S", "T", "N", "N!", "?"))


def _parse_cells(text):
    cells = []
    for cell in text.split():
        rules = []
        for r in cell.split(","):
            name, _, pts = r.partition("-")
            rules.append((name, int(pts) if pts else 0))
        cells.append(tuple(rules))
    assert len(cells) == 9, text
    return cells


def _flips(cells):
    """[identity, H, V, HV] -- the order VCMI's in-place flipPattern yields."""
    def h(c):
        return [c[2], c[1], c[0], c[5], c[4], c[3], c[8], c[7], c[6]]

    def v(c):
        return c[6:9] + c[3:6] + c[0:3]
    return [cells, h(cells), v(cells), h(v(cells))]


class _Pattern:
    __slots__ = ("id", "flips", "minp", "maxp", "deco", "mapping", "diff", "rot")

    def __init__(self, pid, cells, minp=0, deco=False, mapping=""):
        self.id = pid
        self.flips = _flips(cells)
        self.minp = minp
        self.maxp = 1 << 30
        self.deco = deco
        self.diff = False
        self.rot = 0
        if ":" in mapping:
            mode, mapping = mapping.split(":")
            self.diff = True
            self.rot = int(mode[:-1])
        self.mapping = []
        for part in mapping.split(",") if mapping else []:
            lo, _, hi = part.partition("-")
            self.mapping.append((int(lo), int(hi or lo)))


_GROUPS: dict[str, list[_Pattern]] = {}
for _pid, _data, _maps, _minp, _deco in _VIEW_PATTERNS:
    _cells = _parse_cells(_data)
    for _g, _m in _maps.items():
        _GROUPS.setdefault(_g, []).append(_Pattern(_pid, _cells, _minp, _deco, _m))
_GROUP_BY_ID = {g: {p.id: p for p in ps} for g, ps in _GROUPS.items()}
_TYPE_PATTERN = {pid: _Pattern(pid, _parse_cells(d)) for pid, d in _TYPE_PATTERNS}


def _group_of(t: int) -> str:
    return _GROUP.get(t, "normal")


# ---------------------------------------------------------------------------
# Terrain grid wrapper
# ---------------------------------------------------------------------------
class _Terrain:
    """Pattern matcher over a terrain type grid types[z][y][x]."""

    def __init__(self, types, width, height, refined=True):
        self.t = types
        self.w = width
        self.h = height
        self.refined = refined
        self._vcmi = {}          # (z, y, x) -> VCMI's choice, cached
        self._strict_s4 = False  # True while re-checking s4 (see best_view)

    # -- VCMI validateTerrainView / validateTerrainViewInner ----------------
    def validate(self, x, y, z, pattern, rec=0):
        """-> (ok, transition replacement '', 'D' or 'S', flip)"""
        for flip in range(4):
            ok, trans = self._inner(x, y, z, pattern, pattern.flips[flip], rec)
            if ok:
                return True, trans, flip
        return False, "", 0

    def _neighbour(self, x, y, z, cx, cy, center):
        w, h = self.w, self.h
        if 0 <= cx < w and 0 <= cy < h:
            return self.t[z][cy][cx], True
        wh, wl, hh, hl = cx >= w, cx < 0, cy >= h, cy < 0
        if (wh or wl) and (hh or hl):
            return center, False
        if wh:
            return self.t[z][cy][cx - 1], False
        if hh:
            return self.t[z][cy - 1][cx], False
        if wl:
            return self.t[z][cy][cx + 1], False
        return self.t[z][cy + 1][cx], False

    def _inner(self, x, y, z, pattern, data, rec, count=False):
        """count=True: never fail, return (number of violated cells, trans)."""
        center = self.t[z][y][x]
        total = 0
        fails = 0
        trans = ""
        center_tr = center in _TRANSITION_REQUIRED
        for i in range(9):
            if i == 4:
                continue
            cx = x + i % 3 - 1
            cy = y + i // 3 - 1
            t, inmap = self._neighbour(x, y, z, cx, cy, center)
            # in VCMI only in-map neighbours can be "alien"; rock-rock is the
            # only impassable pair and it is the same type anyway
            alien = inmap and t != center
            t_tr = t in _TRANSITION_REQUIRED
            top = -1
            for name, pts in data[i]:
                if name not in _STANDARD:
                    if rec == 0 and inmap:
                        if center == t or (center == DIRT and not t_tr):
                            if self._strict_s4 and pattern.id == "s4":
                                hit = self.vcmi_id(cx, cy, z) == name
                            else:
                                p = _GROUP_BY_ID[_group_of(t)].get(name)
                                hit = p is not None and self.validate(cx, cy, z, p, 1)[0]
                            if hit and pts > top:
                                top = pts
                        continue
                    name = "N"
                native = native_strong = (name == "N" or name == "N!") and not alien
                if center == DIRT:
                    native = name == "N" and not t_tr
                    sand = (name == "S" or name == "T") and t_tr
                    ok = name == "?" or sand or native or native_strong
                elif center == SAND:
                    ok = True
                elif center_tr:  # water, rock
                    sand = (name == "S" or name == "T") and alien
                    ok = name == "?" or sand or native
                else:
                    dirt = (name == "D" or name == "T") and alien and not t_tr
                    sand = (name == "S" or name == "T") and t_tr
                    if not trans and name == "T" and (dirt or sand):
                        trans = "D" if dirt else "S"
                    if name == "T":
                        ok = (dirt and trans != "S") or (sand and trans != "D")
                    else:
                        ok = name == "?" or dirt or sand or native
                if ok and pts > top:
                    top = pts
            if top == -1:
                if not count:
                    return False, ""
                fails += 1
                continue
            total += top
        if count:
            return fails + (0 if pattern.minp <= total <= pattern.maxp else 1), trans
        if pattern.minp <= total <= pattern.maxp:
            return True, trans
        return False, ""

    def relaxed_view(self, x, y, z):
        """Best-effort view for a tile no pattern fits (a shape the tile set
        cannot draw, e.g. a 1-wide land strip): the pattern/flip violating
        the fewest cells, earliest in table order on ties."""
        best = None
        for p in _GROUPS[_group_of(self.t[z][y][x])]:
            for flip in range(4):
                fails, trans = self._inner(x, y, z, p, p.flips[flip], 1, count=True)
                if best is None or fails < best[0]:
                    best = (fails, p, flip, trans)
        return best[1], best[2], best[3]

    def uniform(self, x, y, z):
        """True when all 8 neighbours (with edge clamping) equal the centre."""
        c = self.t[z][y][x]
        row = self.t[z]
        for dy in (-1, 0, 1):
            yy = min(max(y + dy, 0), self.h - 1)
            r = row[yy]
            for dx in (-1, 0, 1):
                xx = min(max(x + dx, 0), self.w - 1)
                if r[xx] != c:
                    return False
        return True

    def vcmi_view(self, x, y, z):
        """VCMI's choice: first pattern of the terrain's group that validates.
        -> (pattern, flip, transition) or None ("No pattern detected")."""
        key = (z, y, x)
        if key in self._vcmi:
            return self._vcmi[key]
        center = self.t[z][y][x]
        group = _group_of(center)
        res = None
        strict, self._strict_s4 = self._strict_s4, False
        if self.uniform(x, y, z):
            res = _GROUP_BY_ID[group]["n1"], 0, ""
        else:
            for p in _GROUPS[group]:
                ok, trans, flip = self.validate(x, y, z, p)
                if ok:
                    res = p, flip, trans
                    break
        self._strict_s4 = strict
        self._vcmi[key] = res
        return res

    def vcmi_id(self, x, y, z):
        res = self.vcmi_view(x, y, z)
        return res[0].id if res else None

    def best_view(self, x, y, z):
        """Pattern used for a tile: VCMI's choice, with corrections measured
        on the original maps (refined=True).

        s4 (concave corner, frames 12-15/32-35) references its two orthogonal
        neighbours: "s3/m7/m8/s1" and "s2/m7/m8/s1". VCMI accepts a neighbour
        that merely *validates* such a pattern; outer corners of diagonal
        ("staircase") shores validate s1 although they are drawn as s5, so
        VCMI puts a concave corner where the original editor puts the
        staircase piece s6 (frames 18-19/38-39). ~3% of all tiles on the
        corpus. Refined: a reference counts only when it is the neighbour's
        actual (VCMI) pattern; if s4 then fails, the later patterns are tried.
        Also two flip conventions for x1/m1 (below).
        """
        res = self.vcmi_view(x, y, z)
        if not self.refined or res is None:
            return res
        p, flip, trans = res
        if p.id == "x1":
            # frames 73/74 (dirt 45) are drawn rotated by 180 degrees
            # relative to VCMI's x1 data: the editor stores flip 3-f (all
            # ~570 corpus tiles).
            return p, 3 - flip, trans
        if p.id == "m1" and flip == 1:
            # m1 is point-symmetric, so H and V both match; the editor stores
            # V (all ~570 corpus tiles).
            return p, 2, trans
        if p.id != "s4":
            return res
        patterns = _GROUPS[_group_of(self.t[z][y][x])]
        start = patterns.index(res[0])
        self._strict_s4 = True
        try:
            for p in patterns[start:]:
                ok, trans, flip = self.validate(x, y, z, p)
                if ok:
                    return p, flip, trans
        finally:
            self._strict_s4 = False
        return None

    # -- VCMI getInvalidTiles -----------------------------------------------
    def tile_valid(self, x, y, z):
        t = self.t[z][y][x]
        valid = self.validate(x, y, z, _TYPE_PATTERN["n1"])[0]
        if valid and t in (WATER, ROCK):
            for pid in ("s1", "s2"):
                valid = not self.validate(x, y, z, _TYPE_PATTERN[pid])[0]
                if not valid:
                    break
        elif not valid and t != WATER and t != ROCK:
            for pid in ("n2", "n3"):
                valid = self.validate(x, y, z, _TYPE_PATTERN[pid])[0]
                if valid:
                    break
        return valid

    def invalid_tiles(self, x, y, z):
        center = self.t[z][y][x]
        foreign, native = set(), set()
        center_valid = False
        for yy in range(y - 1, y + 2):
            for xx in range(x - 1, x + 2):
                if 0 <= xx < self.w and 0 <= yy < self.h:
                    if not self.tile_valid(xx, yy, z):
                        if self.t[z][yy][xx] == center:
                            native.add((z, yy, xx))
                        else:
                            foreign.add((z, yy, xx))
                    elif xx == x and yy == y:
                        center_valid = True
        return foreign, native, center_valid


# ---------------------------------------------------------------------------
# helpers on the codec's tile lists
# ---------------------------------------------------------------------------
def _dims(tiles, width=None, height=None, levels=None):
    levels = len(tiles) if levels is None else levels
    height = len(tiles[0]) if height is None else height
    width = len(tiles[0][0]) if width is None else width
    return width, height, levels


def _types(tiles):
    return [[[t[0] for t in row] for row in level] for level in tiles]


def _iter_cells(cells, levels):
    """cells: iterable of (x, y) or (x, y, z)."""
    for c in cells:
        if len(c) == 2:
            yield c[0], c[1], 0
        else:
            yield c[0], c[1], c[2]


def _set_view(tile, pattern, flip, trans, rng, decorations):
    mapping = pattern.mapping[0]
    if pattern.deco:
        if len(pattern.mapping) >= 2 and rng.randint(0, 99) < decorations:
            mapping = pattern.mapping[1]
    if trans:
        mapping = pattern.mapping[0] if trans == "D" else pattern.mapping[1]
    if not pattern.diff:
        tile[1] = rng.randint(mapping[0], mapping[1])
        tile[6] = (tile[6] & ~FLAG_TERRAIN_FLIP) | flip
    else:
        per = (mapping[1] - mapping[0] + 1) // pattern.rot
        f = 1 if (pattern.rot == 2 and flip == 2) else flip
        first = mapping[0] + f * per
        tile[1] = rng.randint(first, first + per - 1)
        # VCMI zeroes the whole flag byte here; only the terrain flip is
        # meaningful for rock (no roads/rivers/coast), so clear just that.
        tile[6] &= ~FLAG_TERRAIN_FLIP


# Default share of decorated interior frames: the corpus has ~15% on grass,
# ~18% on dirt/sand, up to ~27% on snow.
DEFAULT_DECORATIONS = 15


def update_terrain_views(tiles, width=None, height=None, levels=None, rng=None,
                         positions=None, decorations=DEFAULT_DECORATIONS,
                         coast=True, refined=True, fallback=True):
    """Recompute view + terrain flip of every tile (or of `positions`, an
    iterable of (x, y, z)) from the terrain types, in place.

    Returns the list of (x, y, z) for which no pattern fits (VCMI logs "No
    pattern detected" and leaves the tile alone). With fallback=True (default)
    such tiles get the closest pattern (relaxed_view) instead of keeping
    their old view. Run paint_terrain / repair_terrain first to avoid them.
    Also refreshes the coast bit unless coast=False. refined=False gives
    VCMI's choice exactly (see _Terrain.best_view for the one difference).
    `decorations` = percent of interior tiles drawn with decorated frames.
    """
    width, height, levels = _dims(tiles, width, height, levels)
    rng = rng if rng is not None else random.Random(0)
    grid = _Terrain(_types(tiles), width, height, refined)
    if positions is None:
        positions = [(x, y, z) for z in range(levels) for y in range(height)
                     for x in range(width)]
    else:
        positions = sorted(set(positions), key=lambda p: (p[2], p[1], p[0]))
    failed = []
    for x, y, z in positions:
        res = grid.best_view(x, y, z)
        if res is None:
            failed.append((x, y, z))
            if not fallback:
                continue
            res = grid.relaxed_view(x, y, z)
        _set_view(tiles[z][y][x], res[0], res[1], res[2], rng, decorations)
    if coast:
        update_coast_flags(tiles)
    return failed


def terrain_view_info(tiles, x, y, z=0, grid=None, refined=True):
    """Which pattern VCMI would use for a tile.

    -> None or dict(pattern=id, flip=0..3, ranges=[(lo, hi), ...] of allowed
    frames, flip_stored=flip value written to the flag bits)."""
    if grid is None:
        w, h, _ = _dims(tiles)
        grid = _Terrain(_types(tiles), w, h, refined)
    res = grid.best_view(x, y, z)
    if res is None:
        return None
    p, flip, trans = res
    if trans:
        ranges = [p.mapping[0] if trans == "D" else p.mapping[1]]
    elif p.deco:
        ranges = list(p.mapping)
    else:
        ranges = [p.mapping[0]]
    stored = flip
    if p.diff:
        per = (ranges[0][1] - ranges[0][0] + 1) // p.rot
        f = 1 if (p.rot == 2 and flip == 2) else flip
        first = ranges[0][0] + f * per
        ranges = [(first, first + per - 1)]
        stored = 0
    return {"pattern": p.id, "flip": flip, "ranges": ranges, "flip_stored": stored,
            "trans": trans}


def update_coast_flags(tiles):
    """Bit 6: land tile with water among its 8 neighbours (editor behaviour)."""
    for level in tiles:
        h = len(level)
        w = len(level[0])
        for y in range(h):
            for x in range(w):
                tile = level[y][x]
                coast = False
                if tile[0] != WATER:
                    for yy in range(max(0, y - 1), min(h, y + 2)):
                        r = level[yy]
                        for xx in range(max(0, x - 1), min(w, x + 2)):
                            if r[xx][0] == WATER:
                                coast = True
                                break
                        if coast:
                            break
                tile[6] = (tile[6] | FLAG_COAST) if coast else (tile[6] & ~FLAG_COAST)


def fill(tiles, cells, terrain):
    """Set the terrain type of cells ((x, y) or (x, y, z)) without any
    repair; call update_terrain_views (or repair_terrain) afterwards."""
    for x, y, z in _iter_cells(cells, len(tiles)):
        tiles[z][y][x][0] = terrain


# ---------------------------------------------------------------------------
# VCMI's terrain brush (CDrawTerrainOperation::execute)
# ---------------------------------------------------------------------------
def paint_terrain(tiles, cells, terrain, rng=None, decorations=DEFAULT_DECORATIONS,
                  update_views=True, max_steps=None):
    """Paint `terrain` on cells exactly like VCMI's editor brush: set the
    type, then grow/shrink neighbouring areas until every tile can be drawn
    by the tile set (expandSelection), then recompute views around every
    changed tile. Returns the set of (x, y, z) whose type changed beyond the
    requested cells (the "expansion")."""
    w, h, levels = _dims(tiles)
    rng = rng if rng is not None else random.Random(0)
    types = _types(tiles)
    grid = _Terrain(types, w, h)
    cells = [(x, y, z) for x, y, z in _iter_cells(cells, levels)]
    invalidated = set()

    def touch(x, y, z):
        for yy in range(max(0, y - 1), min(h, y + 2)):
            for xx in range(max(0, x - 1), min(w, x + 2)):
                invalidated.add((z, yy, xx))

    for x, y, z in cells:
        touch(x, y, z)
        types[z][y][x] = terrain
    extra = _expand_selection(grid, [(z, y, x) for x, y, z in cells], touch,
                              max_steps)
    for z, y, x in invalidated:
        tiles[z][y][x][0] = types[z][y][x]
    requested = set(cells)
    changed = {(x, y, z) for z, y, x in extra} - requested
    if update_views:
        update_terrain_views(tiles, w, h, levels, rng,
                             positions=[(x, y, z) for z, y, x in invalidated],
                             decorations=decorations)
    return changed


def _expand_selection(grid, start, touch, max_steps=None):
    """Port of CDrawTerrainOperation::expandSelection. Positions are (z, y, x)
    tuples so Python ordering equals int3 ordering. Returns tiles whose type
    was changed by the expansion."""
    types = grid.t
    heap = list(set(start))
    heapq.heapify(heap)
    members = set(heap)
    changed = set()
    steps = 0
    limit = max_steps if max_steps is not None else 50 * (len(members) + 64) + 100000

    def add(p):
        if p not in members:
            members.add(p)
            heapq.heappush(heap, p)

    while heap:
        steps += 1
        if steps > limit:
            raise RuntimeError("terrain expansion did not converge")
        cz, cy, cx = heap[0]
        if (cz, cy, cx) not in members:
            heapq.heappop(heap)
            continue
        center_type = types[cz][cy][cx]

        def update(p):
            z, y, x = p
            types[z][y][x] = center_type
            changed.add(p)
            add(p)
            touch(x, y, z)

        foreign, native, _ = grid.invalid_tiles(cx, cy, cz)
        for p in sorted(foreign):
            update(p)
        foreign, native, _ = grid.invalid_tiles(cx, cy, cz)
        if (cz, cy, cx) in native:
            # "explosion": grow the centre's terrain onto the best neighbour
            suitable = set()
            inv_foreign = 1 << 30
            inv_native = 0
            center_valid = False
            for yy in range(max(0, cy - 1), min(grid.h, cy + 2)):
                for xx in range(max(0, cx - 1), min(grid.w, cx + 2)):
                    if types[cz][yy][xx] == center_type:
                        continue
                    former = types[cz][yy][xx]
                    types[cz][yy][xx] = center_type
                    tf, tn, tvalid = grid.invalid_tiles(xx, yy, cz)
                    nnorm = (1 << 30) if not tn else len(tn)
                    put = add_it = False
                    if tvalid:
                        if not center_valid:
                            center_valid = True
                            put = True
                        elif len(tf) < inv_foreign:
                            put = True
                        else:
                            add_it = True
                    elif not center_valid:
                        if nnorm > inv_native or (nnorm == inv_native and len(tf) < inv_foreign):
                            put = True
                        elif nnorm == inv_native and len(tf) == inv_foreign:
                            add_it = True
                    if put:
                        inv_native = nnorm
                        inv_foreign = len(tf)
                        suitable.clear()
                        add_it = True
                    if add_it:
                        suitable.add((cz, yy, xx))
                    types[cz][yy][xx] = former
            if len(suitable) == 1:
                update(next(iter(suitable)))
            else:
                for dx, dy in ((0, -1), (-1, 0), (0, 1), (1, 0),
                               (-1, -1), (-1, 1), (1, 1), (1, -1)):
                    p = (cz, cy + dy, cx + dx)
                    if p in suitable:
                        update(p)
                        break
        else:
            for p in native:
                add(p)
            members.discard((cz, cy, cx))
            heapq.heappop(heap)
    return changed


def find_invalid_tiles(tiles, levels=None):
    """(x, y, z) of tiles whose terrain shape the tile set cannot draw
    (VCMI getInvalidTiles criteria: needs a 2x2 block of its own type, or a
    1-wide diagonal for land; water/rock may not touch diagonally only)."""
    w, h, levels = _dims(tiles, levels=levels)
    grid = _Terrain(_types(tiles), w, h)
    return [(x, y, z) for z in range(levels) for y in range(h) for x in range(w)
            if not grid.tile_valid(x, y, z)]


def repair_terrain(tiles, rng=None, decorations=DEFAULT_DECORATIONS, order=None):
    """Make a hand-made type grid drawable: re-paints every terrain type with
    the VCMI brush (biggest area first, so small patches win over their
    surroundings and get grown instead of erased), then recomputes all views.
    Returns {(x, y, z): (wanted, got)} for every tile whose type changed."""
    w, h, levels = _dims(tiles)
    rng = rng if rng is not None else random.Random(0)
    wanted = _types(tiles)
    by_type: dict[int, list] = {}
    for z in range(levels):
        for y in range(h):
            for x in range(w):
                by_type.setdefault(wanted[z][y][x], []).append((x, y, z))
    if order is None:
        order = sorted(by_type, key=lambda t: -len(by_type[t]))
    for t in order:
        if t in by_type:
            paint_terrain(tiles, by_type[t], t, rng, decorations, update_views=False)
    update_terrain_views(tiles, w, h, levels, rng, decorations=decorations)
    diff = {}
    for z in range(levels):
        for y in range(h):
            for x in range(w):
                if tiles[z][y][x][0] != wanted[z][y][x]:
                    diff[(x, y, z)] = (wanted[z][y][x], tiles[z][y][x][0])
    return diff


# ---------------------------------------------------------------------------
# Roads and rivers (CDrawLinesOperation)
# ---------------------------------------------------------------------------
# VCMI's table (lib/mapping/CDrawRoadsOperation.cpp), in order:
# (rule cells, road frames, river frames, has H flip, has V flip);
# "+" = neighbour has a line, "-" = it has none, "?" = either.
_VCMI_LINE_PATTERNS = [
    ("- - - - + - - - -", (14, 14), (9, 9), False, False),      # single tile
    ("? - + - + + + + ?", (2, 5), (-1, -1), True, True),        # straight with angle
    ("? - ? - + + ? + ?", (0, 1), (0, 3), True, True),          # turn
    ("? - ? - + + ? - ?", (15, 15), (11, 12), True, False),     # dead end horizontal
    ("? - ? - + - ? + ?", (14, 14), (9, 10), False, True),      # dead end vertical
    ("? + ? - + + ? + ?", (6, 7), (7, 8), True, False),         # T horizontal
    ("? - ? + + + ? + ?", (8, 9), (5, 6), False, True),         # T vertical
    ("? - ? + + + ? - ?", (12, 13), (11, 12), False, False),    # straight horizontal
    ("? + ? - + - ? + ?", (10, 11), (9, 10), False, False),     # straight vertical
    ("? + ? + + + ? + ?", (16, 16), (4, 4), False, False),      # X cross
]

# Tables measured on the original maps (refined=True). Entries:
# (rule cells, frames, has H flip, has V flip, forced stored flip or None).
# Roads: a turn whose arm continues diagonally (one of the two "outer"
#   diagonals is road) is drawn with the diagonal pieces 2-5 -- VCMI demands
#   both outer diagonals and draws the sharp turn 0-1 otherwise (23% of road
#   tiles); an isolated road tile is stored with V flip. A tile with only
#   diagonal neighbours matches nothing in VCMI (left as is); here it is drawn
#   like an isolated tile.
# Rivers: dead ends are plain straight pieces, never flipped; an isolated
#   river tile uses 9-10.
_ROAD_PATTERNS = [
    ("- - - - + - - - -", (14, 14), False, False, 2),   # single tile
    ("? - ? - + - ? - ?", (14, 14), False, False, 2),   # only diagonal neighbours
    ("? - + - + + ? + ?", (2, 5), True, True, None),    # turn, diagonal continues
    ("? - ? - + + + + ?", (2, 5), True, True, None),    # (other arm)
    ("? - ? - + + ? + ?", (0, 1), True, True, None),    # sharp turn
    ("? - ? - + + ? - ?", (15, 15), True, False, None),  # dead end horizontal
    ("? - ? - + - ? + ?", (14, 14), False, True, None),  # dead end vertical
    ("? + ? - + + ? + ?", (6, 7), True, False, None),   # T horizontal
    ("? - ? + + + ? + ?", (8, 9), False, True, None),   # T vertical
    ("? - ? + + + ? - ?", (12, 13), False, False, None),  # straight horizontal
    ("? + ? - + - ? + ?", (10, 11), False, False, None),  # straight vertical
    ("? + ? + + + ? + ?", (16, 16), False, False, None),  # X cross
]
_RIVER_PATTERNS = [
    ("- - - - + - - - -", (9, 10), False, False, None),  # single tile
    ("? - ? - + - ? - ?", (9, 10), False, False, None),  # only diagonal neighbours
    ("? - ? - + + ? + ?", (0, 3), True, True, None),     # turn
    ("? - ? - + + ? - ?", (11, 12), True, False, 0),     # dead end horizontal
    ("? - ? - + - ? + ?", (9, 10), False, True, 0),      # dead end vertical
    ("? + ? - + + ? + ?", (7, 8), True, False, None),    # T horizontal
    ("? - ? + + + ? + ?", (5, 6), False, True, None),    # T vertical
    ("? - ? + + + ? - ?", (11, 12), False, False, None),  # straight horizontal
    ("? + ? - + - ? + ?", (9, 10), False, False, None),   # straight vertical
    ("? + ? + + + ? + ?", (4, 4), False, False, None),    # X cross
]


def _line_flip(cells, flip):
    c = list(cells)
    if flip in (1, 3):
        c = [c[2], c[1], c[0], c[5], c[4], c[3], c[8], c[7], c[6]]
    if flip in (2, 3):
        c = c[6:9] + c[3:6] + c[0:3]
    return c


def _compile_lines(rows):
    out = []
    for cells, frames, hf, vf, forced in rows:
        cells = cells.split()
        flips = [f for f in range(4)
                 if not ((f == 3 and not (hf and vf)) or (f == 1 and not hf)
                         or (f == 2 and not vf))]
        out.append(([(f, _line_flip(cells, f)) for f in flips], frames, forced))
    return out


_LINE_TABLES = {
    (False, False): _compile_lines([(c, r, hf, vf, None) for c, r, _, hf, vf
                                    in _VCMI_LINE_PATTERNS if r[0] >= 0]),
    (True, False): _compile_lines([(c, r, hf, vf, None) for c, _, r, hf, vf
                                   in _VCMI_LINE_PATTERNS if r[0] >= 0]),
    (False, True): _compile_lines(_ROAD_PATTERNS),
    (True, True): _compile_lines(_RIVER_PATTERNS),
}


def _line_best(has, x, y, z, w, h, river, refined=True):
    """-> (frames (lo, hi), stored flip) or None"""
    around = [0 <= x + i % 3 - 1 < w and 0 <= y + i // 3 - 1 < h
              and has(x + i % 3 - 1, y + i // 3 - 1, z) for i in range(9)]
    for flips, frames, forced in _LINE_TABLES[(river, refined)]:
        for flip, data in flips:
            for i in range(9):
                if i == 4:
                    continue
                r = data[i]
                if (r == "+" and not around[i]) or (r == "-" and around[i]):
                    break
            else:
                return frames, (flip if forced is None else forced)
    return None


def _update_lines(tiles, positions, rng, river, refined=True):
    w, h, levels = _dims(tiles)
    ti, di, shift = (2, 3, 2) if river else (4, 5, 4)
    mask = 3 << shift

    def has(x, y, z):
        return tiles[z][y][x][ti] != 0

    for x, y, z in sorted(set(positions), key=lambda p: (p[2], p[1], p[0])):
        tile = tiles[z][y][x]
        if not tile[ti]:
            tile[di] = 0
            tile[6] &= ~mask
            continue
        res = _line_best(has, x, y, z, w, h, river, refined)
        if res is None:
            continue
        (lo, hi), flip = res
        tile[di] = rng.randint(lo, hi)
        # (VCMI's river writer masks with 0b00111111, wiping the coast bit and
        # OR-ing into the old flip -- a bug; only the river bits change here.)
        tile[6] = (tile[6] & ~mask) | (flip << shift)


def _draw_lines(tiles, cells, line_type, rng, river, refined):
    w, h, levels = _dims(tiles)
    rng = rng if rng is not None else random.Random(0)
    ti = 2 if river else 4
    invalid = set()
    for x, y, z in _iter_cells(cells, levels):
        tiles[z][y][x][ti] = line_type
        for yy in range(max(0, y - 1), min(h, y + 2)):
            for xx in range(max(0, x - 1), min(w, x + 2)):
                invalid.add((xx, yy, z))
    _update_lines(tiles, invalid, rng, river, refined)


def draw_road(tiles, cells, road_type=ROAD_DIRT, rng=None, refined=True):
    """Put a road (1 dirt, 2 gravel, 3 cobblestone; 0 erases) on cells and
    recompute road frames/flips of them and their neighbours."""
    _draw_lines(tiles, cells, road_type, rng, False, refined)


def draw_river(tiles, cells, river_type=RIVER_CLEAR, rng=None, refined=True):
    """Put a river (1 clear, 2 icy, 3 mud, 4 lava; 0 erases) on cells."""
    _draw_lines(tiles, cells, river_type, rng, True, refined)


def _all_positions(tiles):
    w, h, levels = _dims(tiles)
    return [(x, y, z) for z in range(levels) for y in range(h) for x in range(w)]


def update_road_views(tiles, rng=None, refined=True):
    """Recompute road frames/flips of the whole map from the road types."""
    _update_lines(tiles, _all_positions(tiles), rng or random.Random(0), False, refined)


def update_river_views(tiles, rng=None, refined=True):
    """Recompute river frames/flips of the whole map from the river types."""
    _update_lines(tiles, _all_positions(tiles), rng or random.Random(0), True, refined)


def line_view_info(tiles, x, y, z=0, river=False, refined=True):
    """-> None or dict(frames=(lo, hi), flip=stored flip) for a road/river tile."""
    w, h, _ = _dims(tiles)
    ti = 2 if river else 4

    def has(xx, yy, zz):
        return tiles[zz][yy][xx][ti] != 0
    res = _line_best(has, x, y, z, w, h, river, refined)
    if res is None:
        return None
    return {"frames": res[0], "flip": res[1]}
