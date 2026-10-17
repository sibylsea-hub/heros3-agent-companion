"""Round-trip test for tools/h3tools/h3m.py over every map shipped with the game.

Run:  PYTHONIOENCODING=utf-8 python -X utf8 tests/test_h3m_roundtrip.py [--verbose]

For each map (decompressed bytes `raw`) checks
  1. build(parse(raw)) == raw
  2. json.dumps(parse(raw)) works and build(json.loads(json.dumps(...))) == raw
Corpus: <game>/Maps/*.h3m, Maps/Tutorial.tut, Random_Maps/*.h3m, scenario maps
inside Maps/*.h3c and inside the .h3c files of Data/H3bitmap.lod and
Data/H3ab_bmp.lod. The game directory is only read.
"""
from __future__ import annotations

import gzip
import json
import sys
import time
import zlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from h3tools import h3m  # noqa: E402
from h3tools.lod import Lod  # noqa: E402
from h3tools import config  # noqa: E402

GAME = config.game_dir()  # h3_game_dir in h3mod.local.json (tools/h3tools/config.py)


def gzip_members(data: bytes) -> list[bytes]:
    """Split concatenated gzip members (an .h3c campaign) into their payloads."""
    out = []
    while data:
        d = zlib.decompressobj(wbits=31)
        out.append(d.decompress(data) + d.flush())
        data = d.unused_data
    return out


def corpus():
    """Yield (label, decompressed h3m bytes)."""
    maps_dir = GAME / "Maps"
    for p in sorted(maps_dir.glob("*.h3m")) + [maps_dir / "Tutorial.tut"]:
        yield f"Maps/{p.name}", gzip.decompress(p.read_bytes())
    for p in sorted((GAME / "Random_Maps").glob("*.h3m")):
        yield f"Random_Maps/{p.name}", gzip.decompress(p.read_bytes())
    for p in sorted(maps_dir.glob("*.h3c")):
        for i, m in enumerate(gzip_members(p.read_bytes())[1:], 1):
            yield f"Maps/{p.name}#{i}", m
    for lod_name in ("H3bitmap.lod", "H3ab_bmp.lod"):
        lod = Lod(GAME / "Data" / lod_name)
        for name in sorted(lod.names(), key=str.lower):
            if name.lower().endswith(".h3c"):
                for i, m in enumerate(gzip_members(lod.read(name))[1:], 1):
                    yield f"{lod_name}/{name}#{i}", m


def section_at(trace, offset):
    label = "?"
    for pos, lab in trace:
        if pos > offset:
            break
        label = lab
    return label


def first_diff(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n if len(a) != len(b) else -1


def check(raw: bytes):
    """Return (ok, version, detail)."""
    trace = []
    try:
        m = h3m.parse(raw, trace=trace)
    except Exception as e:  # report and continue with the next map
        return False, "?", f"parse error: {e}"
    version = m["version"]
    try:
        out = h3m.build(m)
    except Exception as e:
        return False, version, f"build error: {type(e).__name__}: {e}"
    if out != raw:
        off = first_diff(raw, out)
        return False, version, (f"build mismatch at 0x{off:x} (raw {len(raw)} B, built {len(out)} B) "
                                f"in section: {section_at(trace, off)}")
    try:
        text = json.dumps(m)
        out2 = h3m.build(json.loads(text))
    except Exception as e:
        return False, version, f"json round trip error: {type(e).__name__}: {e}"
    if out2 != raw:
        off = first_diff(raw, out2)
        return False, version, f"json build mismatch at 0x{off:x} in section: {section_at(trace, off)}"
    return True, version, f"{len(m['objects'])} objects, {len(m['templates'])} templates"


def check_blank():
    results = []
    for version in ("RoE", "AB", "SoD"):
        for two in (False, True):
            m = h3m.blank_map(size=36, two_levels=two, name="Test", description="desc",
                              version=version)
            raw = h3m.build(m)
            ok = h3m.build(h3m.parse(raw)) == raw and h3m.parse(raw) == json.loads(json.dumps(m))
            results.append((f"blank_map({version}, two_levels={two})", ok))
    return results


def check_authoring(sod_maps):
    """Hand-written minimal objects on a blank SoD map: build -> parse -> build
    must be stable and keep the authored values."""
    wanted = {91: "sign", 26: "event", 83: "seer_hut", 98: "town", 34: "hero",
              54: "monster", 6: "pandora", 215: "quest_guard"}
    found = {}
    for m in sod_maps:
        for t in m["templates"]:
            if t["class"] in wanted and t["class"] not in found:
                found[t["class"]] = t
        if len(found) == len(wanted):
            break
    m = h3m.blank_map(size=36, name="测试地图", description="authoring smoke test")
    idx = {}
    for cls in wanted:
        idx[cls] = len(m["templates"])
        m["templates"].append(found[cls])
    m["objects"] = [
        {"x": 5, "y": 5, "z": 0, "template": idx[91], "kind": "sign", "message": "路牌：前方有龙"},
        {"x": 6, "y": 6, "z": 0, "template": idx[26], "kind": "event", "message": "你发现了宝藏",
         "resources": [0, 0, 0, 0, 0, 0, 500], "players": [0]},
        {"x": 8, "y": 6, "z": 0, "template": idx[83], "kind": "seer_hut",
         "quest": {"type": "level", "level": 5, "first_visit_text": "达到五级再来。"},
         "reward": {"type": "experience", "amount": 1000}},
        {"x": 12, "y": 12, "z": 0, "template": idx[98], "kind": "town", "owner": 0,
         "identifier": 1, "name": "起点"},
        {"x": 14, "y": 13, "z": 0, "template": idx[34], "kind": "hero", "owner": 0,
         "hero": found[34]["subclass"] * 8, "identifier": 2},
        {"x": 20, "y": 20, "z": 0, "template": idx[54], "kind": "monster", "count": 10,
         "identifier": 3, "message": "吼！"},
        {"x": 22, "y": 20, "z": 0, "template": idx[6], "kind": "pandora", "experience": 500},
        {"x": 24, "y": 20, "z": 0, "template": idx[215], "kind": "quest_guard",
         "quest": {"type": "kill_monster", "target": 3, "deadline": -1}},
    ]
    m["events"] = [{"name": "intro", "message": "欢迎来到测试地图", "players": [0]}]
    m["rumors"] = [{"name": "r1", "text": "传说北方有宝藏"}]
    try:
        raw = h3m.build(m)
        back = h3m.parse(raw)
        ok = h3m.build(back) == raw
        objs = back["objects"]
        ok = ok and objs[0]["message"] == "路牌：前方有龙" and objs[2]["reward"]["amount"] == 1000
        ok = ok and objs[3]["name"] == "起点" and objs[5]["message"] == "吼！"
        ok = ok and back["events"][0]["message"] == "欢迎来到测试地图"
        ok = ok and [o["kind"] for o in objs] == [o["kind"] for o in m["objects"]]
        detail = f"{len(raw)} B"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    # a kind that contradicts the template must be refused
    bad = dict(m, objects=[dict(m["objects"][0], kind="town")])
    try:
        h3m.build(bad)
        ok, detail = False, "kind/template mismatch not detected"
    except h3m.H3MError:
        pass
    return ok, detail


def main():
    verbose = "--verbose" in sys.argv
    t0 = time.time()
    rows, versions, failures, sod_maps = [], Counter(), [], []
    for label, raw in corpus():
        ok, version, detail = check(raw)
        versions[version] += 1
        rows.append((label, version, ok, detail))
        if not ok:
            failures.append((label, detail))
        elif version == "SoD" and len(sod_maps) < 20:
            sod_maps.append(h3m.parse(raw))

    width = max(len(r[0]) for r in rows)
    print(f"{'map':<{width}}  ver  result")
    for label, version, ok, detail in rows:
        if verbose or not ok:
            print(f"{label:<{width}}  {version:<4} {'PASS' if ok else 'FAIL'}  {detail}")
    blanks = check_blank()
    auth_ok, auth_detail = check_authoring(sod_maps)
    blanks.append(("authoring smoke test (blank + objects)", auth_ok))
    for label, ok in blanks:
        print(f"{label:<{width}}  -    {'PASS' if ok else 'FAIL'}")
    if not auth_ok:
        print(f"  authoring: {auth_detail}")

    passed = sum(1 for r in rows if r[2])
    print()
    print(f"corpus: {len(rows)} maps, {passed} passed, {len(rows) - passed} failed "
          f"({time.time() - t0:.1f}s)")
    print("versions: " + ", ".join(f"{v}={n}" for v, n in sorted(versions.items())))
    print(f"blank_map + authoring: {sum(ok for _, ok in blanks)}/{len(blanks)} passed")
    return 0 if not failures and all(ok for _, ok in blanks) else 1


if __name__ == "__main__":
    sys.exit(main())
