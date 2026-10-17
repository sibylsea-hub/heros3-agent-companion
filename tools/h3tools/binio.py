"""Little-endian binary reader/writer shared by the H3M / H3C codecs.

Strings in H3 files are u32 length + bytes in the game's codepage (GBK for
the Simplified Chinese release). They are decoded with `surrogateescape` so
that bytes which are not valid in the codepage still survive a round trip.
"""
from __future__ import annotations

import struct

DEFAULT_ENCODING = "gbk"


class Reader:
    def __init__(self, data: bytes, encoding: str = DEFAULT_ENCODING):
        self.data = data
        self.pos = 0
        self.encoding = encoding

    def _unpack(self, fmt: str, size: int):
        if self.pos + size > len(self.data):
            raise EOFError(f"read past end at 0x{self.pos:x} (+{size})")
        value = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += size
        return value

    def u8(self) -> int:
        return self._unpack("<B", 1)

    def i8(self) -> int:
        return self._unpack("<b", 1)

    def u16(self) -> int:
        return self._unpack("<H", 2)

    def i16(self) -> int:
        return self._unpack("<h", 2)

    def u32(self) -> int:
        return self._unpack("<I", 4)

    def i32(self) -> int:
        return self._unpack("<i", 4)

    def bool(self) -> bool:
        return self.u8() != 0

    def raw(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise EOFError(f"read past end at 0x{self.pos:x} (+{n})")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def str(self) -> str:
        n = self.u32()
        if n > 500_000:
            raise ValueError(f"implausible string length {n} at 0x{self.pos - 4:x}")
        return self.raw(n).decode(self.encoding, "surrogateescape")

    def bits(self, nbytes: int) -> list[int]:
        """Read an LSB-first bitfield and return the indices of set bits."""
        data = self.raw(nbytes)
        return [i * 8 + b for i, byte in enumerate(data) for b in range(8) if byte >> b & 1]

    def remaining(self) -> int:
        return len(self.data) - self.pos


class Writer:
    def __init__(self, encoding: str = DEFAULT_ENCODING):
        self.buf = bytearray()
        self.encoding = encoding

    def u8(self, v: int):
        self.buf += struct.pack("<B", v)

    def i8(self, v: int):
        self.buf += struct.pack("<b", v)

    def u16(self, v: int):
        self.buf += struct.pack("<H", v)

    def i16(self, v: int):
        self.buf += struct.pack("<h", v)

    def u32(self, v: int):
        self.buf += struct.pack("<I", v)

    def i32(self, v: int):
        self.buf += struct.pack("<i", v)

    def bool(self, v: bool):
        self.u8(1 if v else 0)

    def raw(self, b: bytes):
        self.buf += b

    def str(self, s: str):
        b = s.encode(self.encoding, "surrogateescape")
        self.u32(len(b))
        self.buf += b

    def bits(self, nbytes: int, indices):
        out = bytearray(nbytes)
        for i in indices:
            out[i // 8] |= 1 << (i % 8)
        self.buf += out

    def getvalue(self) -> bytes:
        return bytes(self.buf)
