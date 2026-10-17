"""Decode Heroes III .def sprite files (animations) into RGBA frames.

Layout (see VCMI client/render/CDefFile.cpp):

    u32 type, u32 width, u32 height, u32 group_count
    256 x RGB palette
    per group: u32 group_id, u32 frame_count, 8 unknown bytes,
               frame_count x name[13], frame_count x u32 offset
    per frame (at offset): u32 size, u32 format, u32 full_w, u32 full_h,
               u32 w, u32 h, i32 left, i32 top, then pixel data in one of
               four formats (0 raw, 1 RLE with u32 row offsets, 2 and 3
               packed 3-bit code + 5-bit length segments).

Pixels are palette indices. In adventure map sprites the first palette slots
are special (VCMI clientsdl2/render/ScalableImage.cpp):

    0  transparent           1  shadow border (25 % black)
    4  shadow body (50 %)    2, 3  shadow border/body when the palette slot
                                   holds the magic pink, else a plain colour
    5  player flag colour (transparent on objects that cannot be owned)

    d = DefFile(data)                  # or load_def("AVCtowx0.def")
    f = d.frame(group=0, index=0)      # Frame: palette indices + geometry
    w, h, rgba = d.rgba(0, 0, mode="object", flag=(255, 0, 0))

`load_def(name)` looks the sprite up in the game's sprite LODs and caches
decoded files.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from . import config
    from .lod import Lod
except ImportError:  # running as a loose module
    import config
    from lod import Lod

__all__ = ["DefFile", "Frame", "DefError", "load_def", "find_def", "sprite_lods",
           "SHADOW_ALPHA", "FLAG_INDEX"]

SPRITE_LODS = ("H3sprite.lod", "H3ab_spr.lod")

FLAG_INDEX = 5
# Magic colours of the special slots in H3 palettes, for slots 2 and 3 VCMI only
# treats them as shadow when the palette really holds that colour.
_MAGIC = {2: (255, 100, 255), 3: (255, 50, 255)}
SHADOW_ALPHA = {1: 64, 2: 64, 3: 128, 4: 128}


def __getattr__(name: str):
    if name == "GAME_DATA":  # the game's Data folder ($H3_DATA or <h3_game_dir>/Data), looked up on use
        return config.game_data()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class DefError(Exception):
    pass


@dataclass
class Frame:
    """One decoded frame: `pixels` holds w*h palette indices, row-major.

    The pixel rectangle sits at (left, top) inside a full_w x full_h canvas.
    """
    name: str
    full_w: int
    full_h: int
    w: int
    h: int
    left: int
    top: int
    pixels: bytes


def _similar(c1, c2, threshold=8) -> bool:
    return all(abs(a - b) < threshold for a, b in zip(c1, c2))


class DefFile:
    def __init__(self, data: bytes, name: str = ""):
        self.name = name
        self.data = data
        if len(data) < 16 + 768:
            raise DefError(f"{name}: too short for a DEF ({len(data)} bytes)")
        self.type, self.width, self.height, count = struct.unpack_from("<IIII", data, 0)
        pal = data[16:16 + 768]
        self.palette = [(pal[i], pal[i + 1], pal[i + 2]) for i in range(0, 768, 3)]
        self.groups: dict[int, list[int]] = {}
        self.frame_names: dict[int, list[str]] = {}
        pos = 16 + 768
        for _ in range(count):
            gid, n = struct.unpack_from("<II", data, pos)
            pos += 16
            names = []
            for j in range(n):
                raw = data[pos + 13 * j:pos + 13 * j + 13]
                names.append(raw.split(b"\0", 1)[0].decode("latin-1"))
            pos += 13 * n
            self.groups[gid] = list(struct.unpack_from(f"<{n}I", data, pos))
            pos += 4 * n
            self.frame_names[gid] = names
        self._frames: dict[tuple[int, int], Frame] = {}

    # ------------------------------------------------------------------ frames
    def group_sizes(self) -> dict[int, int]:
        return {g: len(offs) for g, offs in sorted(self.groups.items())}

    def has_frame(self, group: int = 0, index: int = 0) -> bool:
        return group in self.groups and 0 <= index < len(self.groups[group])

    def frame(self, group: int = 0, index: int = 0) -> Frame:
        key = (group, index)
        fr = self._frames.get(key)
        if fr is None:
            if not self.has_frame(group, index):
                raise DefError(f"{self.name}: no frame {index} in group {group} "
                               f"(groups {self.group_sizes()})")
            fr = self._decode(self.groups[group][index], self.frame_names[group][index])
            self._frames[key] = fr
        return fr

    def _decode(self, off: int, name: str) -> Frame:
        d = self.data
        _size, fmt, full_w, full_h, w, h, left, top = struct.unpack_from("<IIIIIIii", d, off)
        base = off + 32
        # Old-style frames (SGTWMTA/B.DEF): a 16-byte header, no margins.
        if fmt == 1 and w > full_w and h > full_h:
            left = top = 0
            w, h = full_w, full_h
            base -= 16
        out = bytearray(w * h)
        if w and h:
            if fmt == 0:
                out[:] = d[base:base + w * h]
            elif fmt == 1:
                row_offs = struct.unpack_from(f"<{h}I", d, base)
                for y in range(h):
                    p = base + row_offs[y]
                    row = bytearray()
                    while len(row) < w:
                        code = d[p]
                        n = d[p + 1] + 1
                        p += 2
                        if code == 0xFF:
                            row += d[p:p + n]
                            p += n
                        else:
                            row += bytes((code,)) * n
                    out[y * w:(y + 1) * w] = row[:w]
            elif fmt in (2, 3):
                if fmt == 2:
                    p = base + struct.unpack_from("<H", d, base)[0]
                for y in range(h):
                    if fmt == 3:
                        p = base + struct.unpack_from("<H", d, base + y * 2 * (w // 32))[0]
                    row = bytearray()
                    while len(row) < w:
                        seg = d[p]
                        p += 1
                        code = seg >> 5
                        n = (seg & 31) + 1
                        if code == 7:
                            row += d[p:p + n]
                            p += n
                        else:
                            row += bytes((code,)) * n
                    out[y * w:(y + 1) * w] = row[:w]
            else:
                raise DefError(f"{self.name}: unsupported frame format {fmt}")
        return Frame(name, full_w, full_h, w, h, left, top, bytes(out))

    # ------------------------------------------------------------- palettes
    def colour_table(self, mode: str = "object", flag=None) -> list[tuple[int, int, int, int]]:
        """RGBA for each palette index.

        mode "opaque":   every index is its palette colour (terrain tiles).
        mode "colorkey": index 0 transparent, the rest opaque (rivers, roads).
        mode "object":   VCMI's adventure-map special colours (see module doc).
                         `flag` is the RGB used for index 5, None = transparent.
        """
        table = [(r, g, b, 255) for r, g, b in self.palette]
        if mode == "opaque":
            return table
        table[0] = (0, 0, 0, 0)
        if mode == "colorkey":
            return table
        if mode != "object":
            raise ValueError(f"unknown mode {mode!r}")
        table[1] = (0, 0, 0, SHADOW_ALPHA[1])
        table[4] = (0, 0, 0, SHADOW_ALPHA[4])
        for i, magic in _MAGIC.items():
            if _similar(self.palette[i], magic):
                table[i] = (0, 0, 0, SHADOW_ALPHA[i])
        table[FLAG_INDEX] = (0, 0, 0, 0) if flag is None else (flag[0], flag[1], flag[2], 255)
        return table

    def rgba(self, group: int = 0, index: int = 0, mode: str = "object", flag=None,
             full: bool = True) -> tuple[int, int, bytes]:
        """Frame as RGBA bytes. With full=True the result is full_w x full_h
        (margins transparent), otherwise just the w x h pixel rectangle."""
        fr = self.frame(group, index)
        table = self.colour_table(mode, flag)
        lut = [bytes(c) for c in table]
        body = b"".join(lut[i] for i in fr.pixels)
        if not full:
            return fr.w, fr.h, body
        fw, fh = fr.full_w, fr.full_h
        canvas = bytearray(fw * fh * 4)
        for y in range(fr.h):
            cy = fr.top + y
            if not 0 <= cy < fh:
                continue
            x0 = max(0, fr.left)
            x1 = min(fw, fr.left + fr.w)
            if x1 <= x0:
                continue
            sx0 = x0 - fr.left
            src = body[(y * fr.w + sx0) * 4:(y * fr.w + sx0 + x1 - x0) * 4]
            canvas[(cy * fw + x0) * 4:(cy * fw + x1) * 4] = src
        return fw, fh, bytes(canvas)


# ---------------------------------------------------------------- LOD lookup
@lru_cache(maxsize=None)
def sprite_lods(data_dir: str | None = None) -> tuple[Lod, ...]:
    root = Path(data_dir) if data_dir else config.game_data()
    lods = []
    for name in SPRITE_LODS:
        p = root / name
        if p.exists():
            lods.append(Lod(p))
    if not lods:
        raise FileNotFoundError(f"no sprite LODs in {root}")
    return tuple(lods)


def _normalise(name: str) -> str:
    name = name.strip()
    if "." not in name:
        name += ".def"
    return name.upper()


def find_def(name: str, data_dir: str | None = None) -> bytes | None:
    key = _normalise(name)
    for lod in sprite_lods(data_dir):
        if key in lod.entries:
            return lod.read(key)
    return None


@lru_cache(maxsize=512)
def load_def(name: str, data_dir: str | None = None) -> DefFile:
    """Decoded DEF from the game's sprite LODs (cached). Raises KeyError if missing."""
    data = find_def(name, data_dir)
    if data is None:
        raise KeyError(f"{name} not found in {SPRITE_LODS}")
    return DefFile(data, _normalise(name))


if __name__ == "__main__":
    import sys

    try:
        from .png import write_png
    except ImportError:
        from png import write_png

    if len(sys.argv) < 2:
        print("usage: defsprite.py NAME.def [group] [frame] [-o out.png]")
        sys.exit(2)
    args = sys.argv[1:]
    out = None
    if "-o" in args:
        i = args.index("-o")
        out = args[i + 1]
        del args[i:i + 2]
    d = load_def(args[0])
    print(f"{d.name}: type=0x{d.type:02X} size={d.width}x{d.height} groups={d.group_sizes()}")
    g = int(args[1]) if len(args) > 1 else 0
    i = int(args[2]) if len(args) > 2 else 0
    fr = d.frame(g, i)
    print(f"frame {g}/{i} '{fr.name}': full {fr.full_w}x{fr.full_h}, "
          f"rect {fr.w}x{fr.h} at ({fr.left},{fr.top})")
    if out:
        w, h, px = d.rgba(g, i)
        write_png(out, w, h, px)
        print("wrote", out)
