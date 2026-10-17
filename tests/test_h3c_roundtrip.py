"""Round-trip test for tools/h3tools/h3c.py over every original campaign.

Checks, per campaign file:
  1. build_header(parse_header(h)) == h   (decompressed header bytes)
  2. every embedded map survives parse -> JSON -> build -> parse byte-identical
  3. the rebuilt file's header equals the original except for packed_map_size,
     which must equal the size of the new gzip members
  4. the scenario count matches the region map table
  5. to_dir -> from_dir (campaign.json + .h3m files) rebuilds identical bytes

Run:  PYTHONIOENCODING=utf-8 python -X utf8 tests/test_h3c_roundtrip.py [game_dir]
"""
from __future__ import annotations

import json
import sys
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from h3tools import h3c  # noqa: E402
from h3tools.lod import Lod  # noqa: E402
from h3tools import config  # noqa: E402

GAME = Path(sys.argv[1]) if len(sys.argv) > 1 else config.game_dir()  # default: h3_game_dir


def campaign_sources(game: Path):
    """Yield (label, raw bytes) for every .h3c in the LODs and Maps/C1..C8."""
    for label, _name, raw in h3c.game_campaigns(game):
        yield label, raw


def check(raw: bytes, tmp: Path | None = None) -> tuple[dict, list[str]]:
    errors = []
    members, sizes, trailing = h3c.split_members(raw)
    head = members[0]
    hdr = h3c.parse_header(head)
    if h3c.build_header(hdr) != head:
        errors.append("header bytes differ after round trip")

    c = h3c.parse(raw)
    # through JSON text and back, to prove the dict is JSON-serialisable and complete
    c2 = json.loads(json.dumps(c, ensure_ascii=False))
    rebuilt = h3c.build(c2)
    m2, s2, t2 = h3c.split_members(rebuilt)
    if t2:
        errors.append("rebuilt file has trailing bytes")
    if m2[1:] != members[1:]:
        errors.append(f"maps differ ({len(members) - 1} -> {len(m2) - 1} members)")
    # header: identical once packed sizes are normalised
    h_new = h3c.parse_header(m2[0])
    nonvoid = [s for s in h_new["scenarios"] if s["map_name"]]
    if [s["packed_map_size"] for s in nonvoid] != s2[1:1 + len(nonvoid)]:
        errors.append("rebuilt packed_map_size does not match gzip member sizes")
    if h3c.build_header(h_new, packed_sizes=[s["packed_map_size"] for s in hdr["scenarios"]]) != head:
        errors.append("rebuilt header differs beyond packed sizes")
    # original packed sizes vs original members
    orig_nonvoid = [s for s in hdr["scenarios"] if s["map_name"]]
    if [s["packed_map_size"] for s in orig_nonvoid] != sizes[1:1 + len(orig_nonvoid)]:
        errors.append("note: original packed_map_size != member size")
    if len(orig_nonvoid) != len(members) - 1:
        errors.append(f"{len(orig_nonvoid)} non-void scenarios but {len(members) - 1} maps")
    expected = h3c.scenario_count(hdr["version"], hdr["region_map"])
    if expected is not None and expected != len(hdr["scenarios"]):
        errors.append("scenario count mismatch")
    if trailing:
        errors.append(f"note: {len(trailing)} trailing bytes")
    if tmp is not None:  # editable directory form: campaign.json + .h3m files
        h3c.to_dir(c, tmp)
        c3 = h3c.from_dir(tmp)
        m3, _, _ = h3c.split_members(h3c.build(c3, fix_sizes=False))
        if m3[0] != head or m3[1:] != members[1:]:
            errors.append("to_dir/from_dir round trip differs")
    return hdr, errors


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s: str, n: int) -> str:
    return s + " " * max(0, n - width(s))


def main() -> int:
    rows = []
    failed = 0
    tmp_root = tempfile.TemporaryDirectory()
    for n, (label, raw) in enumerate(campaign_sources(GAME)):
        try:
            hdr, errors = check(raw, Path(tmp_root.name) / str(n))
        except Exception as ex:  # noqa: BLE001
            rows.append((label, "?", "?", "?", "?", f"FAIL {type(ex).__name__}: {ex}"))
            failed += 1
            continue
        hard = [e for e in errors if not e.startswith("note:")]
        failed += bool(hard)
        status = "PASS" if not hard else "FAIL " + "; ".join(hard)
        notes = [e for e in errors if e.startswith("note:")]
        if notes:
            status += "  (" + "; ".join(notes) + ")"
        nonvoid = sum(1 for s in hdr["scenarios"] if s["map_name"])
        rows.append((label, hdr["name"], f"{hdr['version']} {hdr['_version_name']}",
                     str(hdr["region_map"]), f"{nonvoid}/{len(hdr['scenarios'])}", status))

    heads = ("file", "campaign", "version", "region", "maps/slots", "result")
    widths = [max(width(r[i]) for r in rows + [heads]) for i in range(len(heads))]
    print("  ".join(pad(h, w) for h, w in zip(heads, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(pad(v, w) for v, w in zip(r, widths)))
    print()
    print(f"{len(rows)} campaign files, {len(rows) - failed} passed, {failed} failed")
    tmp_root.cleanup()
    return 1 if failed or not rows else 0


if __name__ == "__main__":
    sys.exit(main())
