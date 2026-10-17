"""Minimal PNG writer (standard library only).

    write_png(path, width, height, pixels, channels=4)

`pixels` is raw 8-bit data, row-major, `channels` bytes per pixel
(3 = RGB, 4 = RGBA). Rows use filter type 0; that is cheap to build in pure
Python and compresses well enough for map previews.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

_COLOR_TYPE = {1: 0, 2: 4, 3: 2, 4: 6}  # channels -> PNG colour type


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png(width: int, height: int, pixels, channels: int = 4, level: int = 3) -> bytes:
    if channels not in _COLOR_TYPE:
        raise ValueError(f"unsupported channel count {channels}")
    stride = width * channels
    if len(pixels) != stride * height:
        raise ValueError(f"pixel buffer is {len(pixels)} bytes, expected {stride * height}")
    mv = memoryview(pixels)
    comp = zlib.compressobj(level)
    parts = []
    zero = b"\0"
    # Compress in bands so a 4608x4608 preview never needs a second full copy.
    band = max(1, (1 << 22) // max(stride, 1))
    for y0 in range(0, height, band):
        rows = []
        for y in range(y0, min(height, y0 + band)):
            rows.append(zero)
            rows.append(mv[y * stride:(y + 1) * stride])
        parts.append(comp.compress(b"".join(rows)))
    parts.append(comp.flush())
    ihdr = struct.pack(">IIBBBBB", width, height, 8, _COLOR_TYPE[channels], 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", b"".join(parts)) + _chunk(b"IEND", b""))


def write_png(path, width: int, height: int, pixels, channels: int = 4, level: int = 3) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(width, height, pixels, channels, level))
