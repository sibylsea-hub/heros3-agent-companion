"""Render a Heroes III map (h3m dict) to an image, like the adventure map.

    from h3tools import h3m, render
    m = h3m.load("Maps/Arrogance.h3m")
    w, h, rgba = render.render_map(m, level=0, scale=0.5, annotate=True)
    render.save_png("out/previews/arrogance.png", m, scale=0.5)

CLI:
    python -X utf8 tools/h3tools/render.py MAP.h3m [-o out.png] [--level 1]
        [--scale 0.5] [--annotate] [--grid] [--no-objects]

Drawing follows VCMI client/mapView/MapRenderer.cpp:
- terrain tiles (`view` frame, flags bit 0 = mirror left-right, bit 1 =
  mirror top-bottom), then rivers (flags bits 2-3), then roads (bits 4-5,
  drawn half a tile lower, as H3 does), all from frame 0 of group 0, so water
  and lava show their first palette-animation step;
- objects: frame 0 of group 0 of the template's def, the full frame's
  bottom-right corner on the anchor tile's bottom-right corner, cut into
  32x32 cells and drawn tile by tile in H3's order (VCMI
  lib/mapObjects/MapObjectDrawOrder.cpp): every object is stamped onto each
  tile of its .msk box in map order and goes below the objects it is lower
  than (draw layers from the block mask, `is_overlay` objects at the bottom);
  on a tile the flat objects come first, then all shadows, then the other
  bodies by layer, heroes and boats in fixed slots between them. Objects
  that change the terrain (cursed ground, magic plains, ...) go first.
- heroes use the in-game def AHxx_.def, group 2 (facing east, VCMI's idle
  default), with the owner's flag AF0n.def on top. Ownable objects get their
  owner's flag colour (palette index 5), grey when neutral.
- events (and the grail) are invisible in the game and are not drawn.

`annotate=True` outlines the entrance tile(s) of story objects:
event magenta, sign/bottle yellow, seer hut cyan, quest guard orange,
Pandora's box white; and puts a dot in the owner colour on towns and heroes.

Scale must be 1, 0.5, 0.25 or 0.125. Sprites are downsampled once each
(box filter on colour, majority vote on transparency) and cached, so small
scales are faster than full size.
"""
from __future__ import annotations

import re
import sys
import time
from functools import lru_cache
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "h3tools"

from . import h3m  # noqa: E402
from .defsprite import DefFile, load_def, sprite_lods  # noqa: E402
from .png import write_png  # noqa: E402

__all__ = ["render_map", "render_rgb", "save_png", "PLAYER_COLORS", "NEUTRAL_COLOR",
           "ANNOTATE_COLORS"]

TILE = 32
TERRAIN_DEFS = ["DIRTTL", "SANDTL", "GRASTL", "SNOWTL", "SWMPTL",
                "ROUGTL", "SUBBTL", "LAVATL", "WATRTL", "ROCKTL"]
RIVER_DEFS = {1: "CLRRVR", 2: "ICYRVR", 3: "MUDRVR", 4: "LAVRVR"}
ROAD_DEFS = {1: "DIRTRD", 2: "GRAVRD", 3: "COBBRD"}

# VCMI client/render/Graphics.cpp
PLAYER_COLORS = [(0xFF, 0x00, 0x00), (0x31, 0x52, 0xFF), (0x9C, 0x73, 0x52), (0x42, 0x94, 0x29),
                 (0xFF, 0x84, 0x00), (0x8C, 0x29, 0xA5), (0x09, 0x9C, 0xA5), (0xC6, 0x7B, 0x8C)]
NEUTRAL_COLOR = (0x84, 0x84, 0x84)

# object class ids
EVENT, GRAIL, HERO, RANDOM_HERO, HERO_PLACEHOLDER, BOAT = 26, 36, 34, 70, 214, 8
TOWN, RANDOM_TOWN, MINE, ABANDONED_MINE = 98, 77, 53, 220
HEROES = (HERO, RANDOM_HERO, HERO_PLACEHOLDER)   # all become heroes in the game
TERRAIN_OBJECTS = frozenset({21, 46, *range(222, 232)})  # VCMI "terrain" handler
ANNOTATE_COLORS = {
    26: (255, 0, 255),    # event
    91: (255, 255, 0),    # sign
    59: (255, 255, 0),    # ocean bottle
    83: (0, 255, 255),    # seer hut
    215: (255, 140, 0),   # quest guard
    6: (255, 255, 255),   # Pandora's box
}
_HERO_DEF = re.compile(r"^(AH\d\d)_E\.DEF$", re.I)
_RUNS = re.compile(rb"(.)\1*", re.S)


# --------------------------------------------------------------------------
# compiled sprites
# --------------------------------------------------------------------------
class Sprite:
    """A frame prepared for fast blitting onto an RGB bytearray.

    (ox, oy) is the offset of the first row/column relative to the
    bottom-right corner of the anchor tile, in output pixels. `rows` holds
    (dy, runs); a run is (dx, n, rgb_bytes, None) for opaque pixels or
    (dx, n, None, translate_table) for shadow pixels.
    """
    __slots__ = ("ox", "oy", "rows", "_cells")

    def __init__(self, ox, oy, rows):
        self.ox, self.oy, self.rows = ox, oy, rows
        self._cells = None

    def cells(self, T: int) -> dict:
        """The sprite cut along the tile grid: {(cdx, cdy): Sprite}, where
        cdx/cdy count tiles left/up from the anchor tile (0 = anchor)."""
        if self._cells is None:
            cells: dict[tuple[int, int], list] = {}
            for dy, runs in self.rows:
                yr = self.oy + dy
                cdy = (-yr - 1) // T
                if cdy < 0:
                    continue
                for dx, n, data, table in runs:
                    x = self.ox + dx
                    end = x + n
                    while x < end:
                        cdx = (-x - 1) // T
                        stop = min(end, -cdx * T)       # right edge of this cell
                        if cdx >= 0:
                            k = x - (self.ox + dx)
                            part = data[3 * k:3 * (k + stop - x)] if data is not None else None
                            cell = cells.setdefault((cdx, cdy), [])
                            if not cell or cell[-1][0] != yr:
                                cell.append((yr, []))
                            cell[-1][1].append((x, stop - x, part, table))
                        x = stop
            self._cells = {k: Sprite(0, 0, rows) for k, rows in cells.items()}
        return self._cells


_shade_tables: dict[int, bytes] = {}


def _shade(alpha: int) -> bytes:
    t = _shade_tables.get(alpha)
    if t is None:
        k = 255 - alpha
        t = _shade_tables[alpha] = bytes((c * k + 127) // 255 for c in range(256))
    return t


def _frame_planes(d: DefFile, group: int, index: int, mode: str, flag, hflip: bool, vflip: bool):
    """Alpha and RGB planes of a frame's pixel rectangle, after mirroring.
    Returns (ox, oy, w, h, alpha_rows, rgb_rows) with (ox, oy) relative to
    the full frame's bottom-right corner."""
    fr = d.frame(group, index)
    table = d.colour_table(mode, flag)
    ta = bytes(c[3] for c in table)
    tr = bytes(c[0] for c in table)
    tg = bytes(c[1] for c in table)
    tb = bytes(c[2] for c in table)
    w, h, left, top = fr.w, fr.h, fr.left, fr.top
    rows = [fr.pixels[y * w:(y + 1) * w] for y in range(h)]
    if hflip:
        rows = [r[::-1] for r in rows]
        left = fr.full_w - w - left
    if vflip:
        rows.reverse()
        top = fr.full_h - h - top
    alpha_rows, rgb_rows = [], []
    for r in rows:
        alpha_rows.append(r.translate(ta))
        rgb = bytearray(3 * w)
        rgb[0::3] = r.translate(tr)
        rgb[1::3] = r.translate(tg)
        rgb[2::3] = r.translate(tb)
        rgb_rows.append(bytes(rgb))
    return left - fr.full_w, top - fr.full_h, w, h, alpha_rows, rgb_rows


def _reduce2(ox, oy, w, h, alpha_rows, rgb_rows):
    """Halve a sprite: 2x2 box filter on colour, majority vote on opacity,
    shadow alpha averaged. Pads first so blocks stay aligned to the tile grid."""
    pl, pt = ox & 1, oy & 1                  # ox, oy are often negative; & keeps parity right
    W = w + pl + ((w + pl) & 1)
    H = h + pt + ((h + pt) & 1)
    A = bytearray(W * H)
    C = bytearray(3 * W * H)
    for y in range(h):
        p = (y + pt) * W + pl
        A[p:p + w] = alpha_rows[y]
        C[3 * p:3 * (p + w)] = rgb_rows[y]
    oa_rows, oc_rows = [], []
    for y in range(0, H, 2):
        oa = bytearray(W // 2)
        oc = bytearray(3 * (W // 2))
        r0 = y * W
        r1 = r0 + W
        for x in range(0, W, 2):
            i0 = r0 + x
            i2 = r1 + x
            a0, a1, a2, a3 = A[i0], A[i0 + 1], A[i2], A[i2 + 1]
            o = x >> 1
            if a0 == a1 == a2 == a3 == 255:
                j0, j2 = 3 * i0, 3 * i2
                oc[3 * o] = (C[j0] + C[j0 + 3] + C[j2] + C[j2 + 3] + 2) >> 2
                oc[3 * o + 1] = (C[j0 + 1] + C[j0 + 4] + C[j2 + 1] + C[j2 + 4] + 2) >> 2
                oc[3 * o + 2] = (C[j0 + 2] + C[j0 + 5] + C[j2 + 2] + C[j2 + 5] + 2) >> 2
                oa[o] = 255
                continue
            if not (a0 or a1 or a2 or a3):
                continue
            opaque = [i for i, a in ((i0, a0), (i0 + 1, a1), (i2, a2), (i2 + 1, a3)) if a == 255]
            if len(opaque) >= 2:
                n = len(opaque)
                for c in range(3):
                    oc[3 * o + c] = (sum(C[3 * i + c] for i in opaque) + n // 2) // n
                oa[o] = 255
            else:
                s = sum(a for a in (a0, a1, a2, a3) if a != 255) >> 2
                if s >= 16:
                    oa[o] = s
        oa_rows.append(bytes(oa))
        oc_rows.append(bytes(oc))
    return (ox - pl) >> 1, (oy - pt) >> 1, W // 2, H // 2, oa_rows, oc_rows


def _compile(ox, oy, alpha_rows, rgb_rows, part: str) -> Sprite:
    """part: "all", "body" (opaque only) or "shadow" (translucent only)."""
    rows = []
    for dy, (ar, cr) in enumerate(zip(alpha_rows, rgb_rows)):
        runs = []
        for m in _RUNS.finditer(ar):
            a = ar[m.start()]
            if a == 0:
                continue
            s, e = m.span()
            if a == 255:
                if part != "shadow":
                    runs.append((s, e - s, cr[3 * s:3 * e], None))
            elif part != "body":
                runs.append((s, e - s, None, _shade(a)))
        if runs:
            rows.append((dy, runs))
    return Sprite(ox, oy, rows)


_CACHES: dict[int, "SpriteCache"] = {}   # one per scale, shared between renders


class SpriteCache:
    def __init__(self, shift: int):
        self.shift = shift          # output = input >> shift (scale = 1 / 2**shift)
        self.cache: dict[tuple, tuple[Sprite, Sprite] | None] = {}
        self.missing: set[str] = set()

    def get(self, name: str, group: int = 0, index: int = 0, mode: str = "object",
            flag=None, hflip=False, vflip=False):
        """(body, shadow) sprites; for mode != "object" body holds everything
        and shadow is None. Returns None when the def or frame is missing."""
        key = (name.upper(), group, index, mode, flag, hflip, vflip)
        if key in self.cache:
            res = self.cache[key]
            if res is None:
                self.missing.add(name)
            return res
        res = None
        try:
            d = load_def(name)
        except KeyError:
            self.missing.add(name)
            d = None
        if d is not None:
            if not d.has_frame(group, index):
                if d.has_frame(group, 0):
                    index = 0
                elif d.groups:
                    group, index = min(d.groups), 0
            if d.has_frame(group, index):
                planes = _frame_planes(d, group, index, mode, flag, hflip, vflip)
                for _ in range(self.shift):
                    planes = _reduce2(*planes)
                ox, oy, _w, _h, ar, cr = planes
                if mode == "object":
                    res = (_compile(ox, oy, ar, cr, "body"), _compile(ox, oy, ar, cr, "shadow"))
                else:
                    res = (_compile(ox, oy, ar, cr, "all"), None)
            else:
                self.missing.add(f"{name}[{group}:{index}]")
        self.cache[key] = res
        return res


# --------------------------------------------------------------------------
# canvas
# --------------------------------------------------------------------------
class Canvas:
    def __init__(self, w: int, h: int, fill=(0, 0, 0)):
        self.w, self.h = w, h
        self.stride = 3 * w
        self.px = bytearray(bytes(fill) * (w * h))

    def blit(self, sp: Sprite, x: int, y: int) -> None:
        """Draw sprite with its origin (ox, oy) relative to point (x, y)."""
        px, W, H, stride = self.px, self.w, self.h, self.stride
        x0 = x + sp.ox
        y0 = y + sp.oy
        for dy, runs in sp.rows:
            cy = y0 + dy
            if cy < 0 or cy >= H:
                continue
            base = cy * stride
            for dx, n, data, table in runs:
                cx = x0 + dx
                if cx < 0 or cx + n > W:
                    s = max(0, -cx)
                    e = min(n, W - cx)
                    if e <= s:
                        continue
                    if data is not None:
                        data = data[3 * s:3 * e]
                    cx += s
                    n = e - s
                p = base + 3 * cx
                if data is not None:
                    px[p:p + 3 * n] = data
                else:
                    px[p:p + 3 * n] = px[p:p + 3 * n].translate(table)

    def fill_rect(self, x, y, w, h, rgb, alpha=255):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        n = x1 - x0
        if alpha >= 255:
            row = bytes(rgb) * n
            for yy in range(y0, y1):
                p = yy * self.stride + 3 * x0
                self.px[p:p + 3 * n] = row
        elif rgb == (0, 0, 0):
            t = _shade(alpha)
            for yy in range(y0, y1):
                p = yy * self.stride + 3 * x0
                self.px[p:p + 3 * n] = self.px[p:p + 3 * n].translate(t)
        else:
            for yy in range(y0, y1):
                p = yy * self.stride + 3 * x0
                for i in range(n):
                    for c in range(3):
                        q = p + 3 * i + c
                        self.px[q] = (rgb[c] * alpha + self.px[q] * (255 - alpha) + 127) // 255

    def outline(self, x, y, w, h, rgb, width=1):
        self.fill_rect(x, y, w, width, rgb)
        self.fill_rect(x, y + h - width, w, width, rgb)
        self.fill_rect(x, y, width, h, rgb)
        self.fill_rect(x + w - width, y, width, h, rgb)

    def disc(self, cx, cy, r, rgb, border=(0, 0, 0)):
        for dy in range(-r - 1, r + 2):
            for dx in range(-r - 1, r + 2):
                d2 = dx * dx + dy * dy
                if d2 <= r * r:
                    self.fill_rect(cx + dx, cy + dy, 1, 1, rgb)
                elif d2 <= (r + 1) * (r + 1):
                    self.fill_rect(cx + dx, cy + dy, 1, 1, border)

    def rgba(self) -> bytes:
        n = self.w * self.h
        out = bytearray(4 * n)
        out[0::4] = self.px[0::3]
        out[1::4] = self.px[1::3]
        out[2::4] = self.px[2::3]
        out[3::4] = b"\xff" * n
        return bytes(out)


# --------------------------------------------------------------------------
# map rendering
# --------------------------------------------------------------------------
def _mask_tiles(mask, x, y):
    """Tiles whose bit is set in a 6-byte template mask (bit 7 of byte 5 = anchor)."""
    out = []
    for i, byte in enumerate(mask):
        for j in range(8):
            if (byte >> j) & 1:
                out.append((x - (7 - j), y - (5 - i)))
    return out


def _owner_colour(obj, cls):
    owner = obj.get("owner")
    if owner is None:
        if cls in (MINE, ABANDONED_MINE):
            return NEUTRAL_COLOR
        return None
    if isinstance(owner, int) and 0 <= owner < 8:
        return PLAYER_COLORS[owner]
    return NEUTRAL_COLOR


@lru_cache(maxsize=None)
def _msk_size(def_name: str) -> tuple[int, int]:
    """Object size in tiles from the def's .msk file (VCMI ObjectTemplate::readMsk)."""
    key = def_name.rsplit(".", 1)[0].upper() + ".MSK"
    for lod in sprite_lods():
        if key in lod.entries:
            d = lod.read(key)
            if len(d) >= 2:
                return d[0], d[1]
    return 8, 6


def _draw_layers(block_mask, w: int, h: int, prio: int) -> list[list[int]]:
    """H3 draw layer of each cell, [dy][dx] counted from the anchor
    (VCMI ObjectTemplate::calculateDrawLayers). A column is numbered bottom to
    top and restarts where a blocked cell sits on a passable one; a passable
    cell with a blocked cell to its right takes that cell's layer."""
    if prio:
        return [[0 if prio > 0 else 255] * w for _ in range(h)]

    def blocked(dx, dy):
        return dx < 8 and dy < 6 and not (block_mask[5 - dy] >> (7 - dx)) & 1

    layers = [[0] * w for _ in range(h)]
    for x in range(w):
        layer = 1
        for y in range(h):
            if y > 0 and blocked(x, y) and not blocked(x, y - 1):
                layer = 1
            if x > 0 and not blocked(x, y) and blocked(x - 1, y):
                layer = layers[y][x - 1]
            layers[y][x] = layer
            if layer < 254:
                layer += 1
    return layers


class _Placed:
    __slots__ = ("idx", "cls", "ax", "ay", "w", "h", "layers", "prio", "fixed", "ground",
                 "body", "shadow", "flag")

    def layer_at(self, tx, ty):
        dx = self.ax - tx
        dy = self.ay - ty
        if 0 <= dx < self.w and 0 <= dy < self.h:
            return self.layers[dy][dx]
        return 0


def _place_objects(cache: SpriteCache, templates, objs) -> list[_Placed]:
    placed = []
    for idx, o in objs:
        tp = templates[o["template"]]
        cls = tp.get("class")
        if cls == GRAIL:            # VCMI keeps only the grail position, no object
            continue
        p = _Placed()
        p.idx, p.cls, p.ax, p.ay = idx, cls, o["x"], o["y"]
        name, group = tp["def"], 0
        flag = _owner_colour(o, cls)
        p.flag = None
        p.w, p.h = (1, 1) if cls == EVENT else _msk_size(name)
        if cls in HEROES:
            hm = _HERO_DEF.match(name)
            if cls == HERO and hm:
                name, group = hm.group(1) + "_.DEF", 2
            owner = o.get("owner")
            if isinstance(owner, int) and 0 <= owner < 8:
                p.flag = cache.get(f"AF{owner:02d}.DEF", 2, 0)
            flag = None             # heroes show their owner with the flag sprite
        sp = None
        if cls != EVENT:            # events are invisible in the game
            sp = cache.get(name, group, 0, "object", flag)
            if sp is None and group:
                sp = cache.get(tp["def"], 0, 0, "object", flag)
        p.body, p.shadow = sp if sp else (None, None)
        p.prio = 100 if tp.get("is_overlay") else 0
        p.layers = _draw_layers(tp.get("block_mask") or [255] * 6, p.w, p.h, p.prio)
        p.fixed = cls in HEROES or cls == BOAT
        p.ground = cls in TERRAIN_OBJECTS
        placed.append(p)
    return placed


def _draw_objects(cv: Canvas, cache: SpriteCache, T: int, size: int, templates, objs) -> None:
    """Draw objects tile by tile in H3's order (VCMI MapObjectDrawOrder):
    objects are stamped onto every tile of their box in map order, each going
    below the objects it is lower than; per tile the bottom layer (flat
    objects) comes first, then every shadow, then the other bodies by layer,
    with heroes and boats in fixed slots between layers 1/2 (their own row)
    and 2/3 (the row above)."""
    placed = _place_objects(cache, templates, objs)
    ordered: dict[tuple[int, int], list[_Placed]] = {}
    fixed: dict[tuple[int, int], list[_Placed]] = {}
    lower_cache: dict[tuple[int, int], bool] = {}

    def lower_in_any(a: _Placed, b: _Placed) -> bool:
        key = (a.idx, b.idx)
        r = lower_cache.get(key)
        if r is None:
            r = False
            for x in range(max(a.ax - a.w + 1, b.ax - b.w + 1, 0), min(a.ax, b.ax, size - 1) + 1):
                for y in range(max(a.ay - a.h + 1, b.ay - b.h + 1, 0), min(a.ay, b.ay, size - 1) + 1):
                    if a.layer_at(x, y) < b.layer_at(x, y):
                        r = True
                        break
                if r:
                    break
            lower_cache[key] = r
        return r

    def goes_below(a: _Placed, b: _Placed, tx: int, ty: int) -> bool:
        la, lb = a.layer_at(tx, ty), b.layer_at(tx, ty)
        if la != lb:
            return la < lb
        if a.prio != b.prio:
            return a.prio > b.prio
        if lower_in_any(a, b):
            return True
        return not lower_in_any(b, a) and a.idx < b.idx

    for p in placed:
        for fy in range(p.h):
            ty = p.ay - fy
            if not 0 <= ty < size:
                continue
            for fx in range(p.w):
                tx = p.ax - fx
                if not 0 <= tx < size:
                    continue
                if p.fixed:
                    fixed.setdefault((tx, ty), []).append(p)
                    continue
                lst = ordered.setdefault((tx, ty), [])
                pos = len(lst)
                while pos and goes_below(p, lst[pos - 1], tx, ty):
                    pos -= 1
                lst.insert(pos, p)

    def cell(sprite, p, tx, ty):
        if sprite is not None:
            c = sprite.cells(T).get((p.ax - tx, p.ay - ty))
            if c is not None:
                cv.blit(c, (p.ax + 1) * T, (p.ay + 1) * T)

    def hero(p, tx, ty):
        cell(p.shadow, p, tx, ty)
        cell(p.body, p, tx, ty)
        if p.flag:
            cell(p.flag[1], p, tx, ty)
            cell(p.flag[0], p, tx, ty)

    # objects that change the terrain go below everything else
    for (tx, ty), lst in ordered.items():
        for p in lst:
            if p.ground:
                cell(p.shadow, p, tx, ty)
                cell(p.body, p, tx, ty)

    for key in set(ordered) | set(fixed):
        tx, ty = key
        lst = [(p, p.layer_at(tx, ty)) for p in ordered.get(key, ()) if not p.ground]
        hs = fixed.get(key)

        def layers(lo, hi):
            for p, layer in lst:
                if lo <= layer <= hi:
                    cell(p.body, p, tx, ty)

        layers(0, 0)
        for p, _layer in lst:
            cell(p.shadow, p, tx, ty)
        if not hs:
            layers(1, 255)
            continue
        body_row = sorted((h for h in hs if h.ay == ty), key=lambda h: h.ax - tx)
        head_row = sorted((h for h in hs if h.ay != ty), key=lambda h: h.ax - tx)
        layers(1, 1)
        for h in body_row:
            hero(h, tx, ty)
        layers(2, 2)
        for h in head_row:
            hero(h, tx, ty)
        layers(3, 255)


def _scale_shift(scale: float) -> int:
    for shift in range(4):
        if abs(scale - 1 / (1 << shift)) < 1e-9:
            return shift
    raise ValueError(f"scale must be 1, 0.5, 0.25 or 0.125, not {scale}")


def render_rgb(m, level=0, scale=1.0, objects=True, grid=False, annotate=False,
               stats: dict | None = None) -> Canvas:
    """Render one level of a map dict (or a path to a .h3m) to an RGB Canvas."""
    if not isinstance(m, dict):
        m = h3m.load(m)
    terr = m["terrain"]
    size = terr["size"]
    levels = len(terr["tiles"])
    if not 0 <= level < levels:
        raise ValueError(f"map has {levels} level(s), no level {level}")
    shift = _scale_shift(scale)
    T = TILE >> shift
    cache = _CACHES.get(shift)
    if cache is None:
        cache = _CACHES[shift] = SpriteCache(shift)
    cache.missing = set()
    cv = Canvas(size * T, size * T)
    tiles = terr["tiles"][level]
    t0 = time.perf_counter()

    # terrain
    for y in range(size):
        row = tiles[y]
        for x in range(size):
            t = row[x]
            ter = t[0]
            flags = t[6]
            name = TERRAIN_DEFS[ter] if 0 <= ter < len(TERRAIN_DEFS) else None
            sp = cache.get(name, 0, t[1], "opaque", None, bool(flags & 1), bool(flags & 2)) if name else None
            if sp:
                cv.blit(sp[0], (x + 1) * T, (y + 1) * T)
            else:
                cv.fill_rect(x * T, y * T, T, T, (255, 0, 255))
    # rivers
    for y in range(size):
        row = tiles[y]
        for x in range(size):
            t = row[x]
            if t[2] in RIVER_DEFS:
                sp = cache.get(RIVER_DEFS[t[2]], 0, t[3], "colorkey", None,
                               bool(t[6] & 4), bool(t[6] & 8))
                if sp:
                    cv.blit(sp[0], (x + 1) * T, (y + 1) * T)
    # roads: H3 draws a road tile half a tile lower than its own tile
    half = T // 2
    for y in range(size):
        row = tiles[y]
        for x in range(size):
            t = row[x]
            if t[4] in ROAD_DEFS:
                sp = cache.get(ROAD_DEFS[t[4]], 0, t[5], "colorkey", None,
                               bool(t[6] & 16), bool(t[6] & 32))
                if sp:
                    cv.blit(sp[0], (x + 1) * T, (y + 1) * T + half)
    t1 = time.perf_counter()

    templates = m.get("templates", [])
    objs = [(i, o) for i, o in enumerate(m.get("objects", [])) if o.get("z", 0) == level]
    if objects:
        _draw_objects(cv, cache, T, size, templates, objs)
    t2 = time.perf_counter()

    if grid:
        for i in range(size):
            cv.fill_rect(i * T, 0, 1, cv.h, (0, 0, 0), 96)
            cv.fill_rect(0, i * T, cv.w, 1, (0, 0, 0), 96)
    if annotate:
        r = max(2, T // 8)
        for idx, o in objs:
            tp = templates[o["template"]]
            cls = tp.get("class")
            entrances = _mask_tiles(tp.get("visit_mask", [0] * 6), o["x"], o["y"])
            if cls in ANNOTATE_COLORS:
                for ex, ey in entrances or [(o["x"], o["y"])]:
                    cv.outline(ex * T, ey * T, T, T, ANNOTATE_COLORS[cls])
            if cls in (TOWN, RANDOM_TOWN, HERO, RANDOM_HERO, HERO_PLACEHOLDER):
                col = _owner_colour(o, cls) or NEUTRAL_COLOR
                ex, ey = (entrances or [(o["x"], o["y"])])[0]
                cv.disc(ex * T + T // 2, ey * T + T // 2, r, col)
    if stats is not None:
        stats.update(ground_s=t1 - t0, objects_s=t2 - t1, sprites=len(cache.cache),
                     missing=sorted(cache.missing))
    return cv


def render_map(m, level=0, scale=1.0, objects=True, grid=False, annotate=False):
    """Render one level of a map dict (or .h3m path). Returns (width, height, rgba_bytes)."""
    cv = render_rgb(m, level, scale, objects, grid, annotate)
    return cv.w, cv.h, cv.rgba()


def save_png(path, m, level=0, scale=1.0, objects=True, grid=False, annotate=False,
             stats: dict | None = None):
    """Render and write a PNG (RGB, the map is always opaque). Returns (width, height)."""
    cv = render_rgb(m, level, scale, objects, grid, annotate, stats)
    t = time.perf_counter()
    write_png(path, cv.w, cv.h, cv.px, channels=3)
    if stats is not None:
        stats["png_s"] = time.perf_counter() - t
    return cv.w, cv.h


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="Render an H3M map to PNG.")
    ap.add_argument("map")
    ap.add_argument("-o", "--out")
    ap.add_argument("--level", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--annotate", action="store_true")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--no-objects", action="store_true")
    a = ap.parse_args(argv)
    src = Path(a.map)
    out = Path(a.out) if a.out else (Path(__file__).resolve().parents[2] / "out" / "previews"
                                     / f"{src.stem}{'-u' if a.level else ''}.png")
    t = time.perf_counter()
    m = h3m.load(src)
    t_load = time.perf_counter() - t
    stats: dict = {}
    w, h = save_png(out, m, a.level, a.scale, not a.no_objects, a.grid, a.annotate, stats)
    total = time.perf_counter() - t
    size = m["terrain"]["size"]
    print(f"{out}: {w}x{h} ({size}x{size} tiles, level {a.level}, scale {a.scale}) "
          f"in {total:.2f}s [load {t_load:.2f}, ground {stats['ground_s']:.2f}, "
          f"objects {stats['objects_s']:.2f}, png {stats['png_s']:.2f}; {stats['sprites']} sprites]")
    if stats["missing"]:
        print("missing sprites:", ", ".join(stats["missing"]))


if __name__ == "__main__":
    main()
