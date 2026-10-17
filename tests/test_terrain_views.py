"""Validate tools/h3tools/terrain.py against maps made with the original editor.

Run:  PYTHONIOENCODING=utf-8 python -X utf8 tests/test_terrain_views.py
          [--step N] [--campaigns] [--vcmi] [--top K]

For every corpus map only the type grids are kept (terrain type, river type,
road type); views, river/road frames and the flag byte are wiped and
recomputed with the public API (update_terrain_views, update_road_views,
update_river_views, update_coast_flags). Each tile is then compared with
what the original editor stored:

  terrain group  the stored frame is in the frame range of the pattern we
                 chose (n1: plain or decorated; D/S transition variant and
                 rock rotation slot must match)
  terrain flip   flag bits 0-1 equal
  coast bit      flag bit 6 equal
  road/river     frame in the same range / flip bits equal (4-5, 2-3)

--vcmi additionally scores VCMI's exact algorithm (refined=False).
Corpus: <game>/Maps/*.h3m (+ scenario maps inside Maps/*.h3c with
--campaigns). The game directory is only read.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import random
import sys
import time
import zlib
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from h3tools import h3m, terrain as T  # noqa: E402
from h3tools import config  # noqa: E402

GAME = config.game_dir()  # h3_game_dir in h3mod.local.json (tools/h3tools/config.py)

ROAD_GROUPS = [(0, 1), (2, 5), (6, 7), (8, 9), (10, 11), (12, 13), (14, 14), (15, 15), (16, 16)]
RIVER_GROUPS = [(0, 3), (4, 4), (5, 6), (7, 8), (9, 10), (11, 12)]


def corpus(campaigns: bool):
    maps_dir = GAME / "Maps"
    for p in sorted(maps_dir.glob("*.h3m")):
        yield p.name, h3m.load(p)
    if campaigns:
        for p in sorted(maps_dir.glob("*.h3c")):
            data = p.read_bytes()
            i = 0
            while data:
                d = zlib.decompressobj(wbits=31)
                raw = d.decompress(data) + d.flush()
                data = d.unused_data
                if i:
                    yield f"{p.name}#{i}", h3m.parse(raw)
                i += 1


def terrain_group(t, view):
    """(pattern id, range index) whose frames contain `view`; n1 ranges merged."""
    for p in T._GROUPS[T._group_of(t)]:
        for i, (lo, hi) in enumerate(p.mapping):
            if p.diff:
                per = (hi - lo + 1) // p.rot
                for r in range(p.rot):
                    if lo + r * per <= view <= lo + r * per + per - 1:
                        return p.id, r
            elif lo <= view <= hi:
                return p.id, 0 if p.deco else i
    return "?", view


def line_group(groups, v):
    for i, (lo, hi) in enumerate(groups):
        if lo <= v <= hi:
            return i
    return -1


class Score:
    def __init__(self):
        self.n = Counter()
        self.ok = Counter()
        self.miss = defaultdict(Counter)

    def add(self, key, good, detail=None):
        self.n[key] += 1
        if good:
            self.ok[key] += 1
        elif detail is not None:
            self.miss[key][detail] += 1

    def pct(self, key):
        return 100.0 * self.ok[key] / self.n[key] if self.n[key] else float("nan")


def score_map(m, refined, sc, per_terrain):
    orig = m["terrain"]["tiles"]
    tiles = copy.deepcopy(orig)
    for level in tiles:
        for row in level:
            for t in row:
                t[1] = t[3] = t[5] = t[6] = 0
    rng = random.Random(1)
    T.update_terrain_views(tiles, rng=rng, refined=refined)
    T.update_road_views(tiles, rng=rng, refined=refined)
    T.update_river_views(tiles, rng=rng, refined=refined)
    for z, level in enumerate(orig):
        for y, row in enumerate(level):
            for x, o in enumerate(row):
                n = tiles[z][y][x]
                go, gn = terrain_group(o[0], o[1]), terrain_group(n[0], n[1])
                ok = go == gn
                sc.add("terrain group", ok, (T.TERRAIN_NAMES[o[0]], gn[0], go[0]))
                per_terrain[o[0]].add("group", ok)
                fo = (o[6] & 3) == (n[6] & 3)
                sc.add("terrain flip", fo, (T.TERRAIN_NAMES[o[0]], gn[0], n[6] & 3, o[6] & 3))
                per_terrain[o[0]].add("flip", fo)
                sc.add("coast bit", (o[6] & 64) == (n[6] & 64))
                sc.add("flags byte (exact)", o[6] == n[6])
                if o[4]:
                    g = line_group(ROAD_GROUPS, o[5]) == line_group(ROAD_GROUPS, n[5])
                    sc.add("road group", g, (ROAD_GROUPS[line_group(ROAD_GROUPS, n[5])],
                                             ROAD_GROUPS[line_group(ROAD_GROUPS, o[5])]))
                    sc.add("road flip", (o[6] >> 4 & 3) == (n[6] >> 4 & 3))
                if o[2]:
                    g = line_group(RIVER_GROUPS, o[3]) == line_group(RIVER_GROUPS, n[3])
                    sc.add("river group", g, (RIVER_GROUPS[line_group(RIVER_GROUPS, n[3])],
                                              RIVER_GROUPS[line_group(RIVER_GROUPS, o[3])]))
                    sc.add("river flip", (o[6] >> 2 & 3) == (n[6] >> 2 & 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=1, help="use every N-th map")
    ap.add_argument("--campaigns", action="store_true")
    ap.add_argument("--vcmi", action="store_true", help="also score VCMI-exact mode")
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    modes = [True, False] if a.vcmi else [True]
    t0 = time.time()
    maps = list(corpus(a.campaigns))[:: a.step]
    print(f"{len(maps)} maps, {sum(len(m['terrain']['tiles']) * m['terrain']['size'] ** 2 for _, m in maps)} tiles")
    failed = False
    for refined in modes:
        sc = Score()
        per_terrain = defaultdict(Score)
        for _, m in maps:
            score_map(m, refined, sc, per_terrain)
        print(f"\n== {'refined (default)' if refined else 'VCMI exact'} ==")
        for key in ["terrain group", "terrain flip", "coast bit", "flags byte (exact)",
                    "road group", "road flip", "river group", "river flip"]:
            print(f"  {key:20} {sc.pct(key):7.3f}%  of {sc.n[key]}")
        print("  per terrain (group / flip):")
        for t in sorted(per_terrain):
            s = per_terrain[t]
            print(f"    {T.TERRAIN_NAMES[t]:13} {s.pct('group'):7.3f}% {s.pct('flip'):7.3f}%  ({s.n['group']} tiles)")
        for key in ["terrain group", "terrain flip", "road group", "river group"]:
            if sc.miss[key]:
                print(f"  top {key} mismatches (terrain, ours, original):")
                for d, c in sc.miss[key].most_common(a.top):
                    print(f"    {d} x{c}")
        if refined and (sc.pct("terrain group") < 99.5 or sc.pct("terrain flip") < 99.5
                        or sc.pct("coast bit") < 100 or sc.pct("road group") < 99
                        or sc.pct("river group") < 99):
            failed = True
    print(f"\n{time.time() - t0:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
