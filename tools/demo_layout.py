"""Demo for h3tools.layout: compile a 36x36 ASCII layout, save it as
out/demo_layout.h3m and load it in VCMI.

    PYTHONIOENCODING=utf-8 python -X utf8 tools/demo_layout.py [--no-vcmi]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from h3tools import h3m, layout  # noqa: E402

LAYOUT = r'''
####################################
#*******####***.......####.........#
#***********###....................#
#*****A*****##.........B...........#
#***=====*****.....................#
#*******==***.......###............#
#***##****=.........####.....%%%%..#
#**###*****=........###.....%%%%%%.#
#****......=................%%%C%%%#
#..........=.........w......%%%%%%.#
#..........=.........w.......%%%%..#
#....##....=.........w.............#
#...###....=.........w.............#
#..........==========b=============#
#...................ww.........=...#
#................~~~~~~~.......=...#
#..............~~~~~~~~~~......=...#
#.............~~~~~~~~~~~~~....=...#
#.............~~~~~~~~~~~~~....=...#
#....###......~~~~~~~~~~~~.....=...#
#...####.......~~~~~~~~~~......=...#
#....##.........~~~~~~~~.......=...#
#..............................=...#
#.........,,,,,,,..............=...#
#........,,,,,,,,,,.....##.....=...#
#........,,,,D,,,,,....####....=...#
#........,,,,=,,,,,.....##.....=...#
#.........,,,=,,,..............=...#
#............=.................=...#
#............===================...#
#....::::......................."""#
#...::::::...............###...""""#
#...:::E::...............####..""""#
#....::::..................#...""""#
#..........................""""""""#
####################################
'''


def main() -> int:
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__.strip())
        return 0
    # custom legend entry: a road crossing the river (ford)
    compiled = layout.compile_layout(LAYOUT, legend={"b": {"road": 1, "river": 1}}, seed=3)
    m = h3m.blank_map(36, name="Layout demo", description="h3tools.layout demo")
    layout.apply_to_map(m, compiled)
    out = ROOT / "out" / "demo_layout.h3m"
    out.parent.mkdir(parents=True, exist_ok=True)
    h3m.save(m, out)
    h3m.load(out)  # round trip
    print(f"saved {out}: {len(compiled['obstacles'])} obstacles, "
          f"{len(m['templates'])} templates")
    print("anchors:", compiled["anchors"])
    if compiled["changes"]:
        print("repaired tiles (x, y): (wanted, got):", compiled["changes"])
    if compiled["failed"]:
        print("approximate views at:", compiled["failed"])
    if "--no-vcmi" in sys.argv:
        return 0
    return subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools" / "vcmi_check.py"),
                           str(out)]).returncode


if __name__ == "__main__":
    sys.exit(main())
