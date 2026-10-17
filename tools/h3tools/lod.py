"""Read Heroes III .lod archives (H3bitmap.lod, H3ab_bmp.lod, ...).

Layout: b"LOD\\0", u32 type, u32 count, 80 unknown bytes, then from offset 92
`count` entries of 32 bytes: name[16] (NUL padded), u32 offset, u32 size,
u32 filetype, u32 csize (0 = stored uncompressed, otherwise zlib).
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LodEntry:
    name: str
    offset: int
    size: int
    filetype: int
    csize: int


class Lod:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            head = f.read(92)
            if head[:4] != b"LOD\0":
                raise ValueError(f"{path}: not a LOD archive")
            _, count = struct.unpack_from("<II", head, 4)
            raw = f.read(32 * count)
        self.entries: dict[str, LodEntry] = {}
        for i in range(count):
            name, off, size, ftype, csize = struct.unpack_from("<16sIIII", raw, 32 * i)
            name = name.split(b"\0", 1)[0].decode("latin-1")
            if name:
                self.entries[name.upper()] = LodEntry(name, off, size, ftype, csize)

    def names(self) -> list[str]:
        return [e.name for e in self.entries.values()]

    def read(self, name: str) -> bytes:
        e = self.entries[name.upper()]
        with open(self.path, "rb") as f:
            f.seek(e.offset)
            data = f.read(e.csize or e.size)
        return zlib.decompress(data) if e.csize else data


if __name__ == "__main__":
    import sys

    lod = Lod(sys.argv[1])
    pattern = sys.argv[2].upper() if len(sys.argv) > 2 else ""
    for e in lod.entries.values():
        if pattern in e.name.upper():
            print(f"{e.name:16} {e.size:9} csize={e.csize}")
