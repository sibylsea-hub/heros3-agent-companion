"""Smoke test for mapkit: build a small story map with every helper, check it,
round-trip it through the codec. Plain script.

    PYTHONIOENCODING=utf-8 python -X utf8 tests/test_mapkit.py [--vcmi]
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from h3tools import h3m                              # noqa: E402
from h3tools.mapkit import MapKit, MapKitError       # noqa: E402

OUT = ROOT / "out" / "maps" / "_smoke.h3m"


def build() -> MapKit:
    k = MapKit(size=36, name="测试图", description="mapkit 冒烟测试")
    k.player(0, "tower")
    k.player(1, "necropolis", human=False)
    k.town((6, 6), "tower", owner=0, name="我们的城", main=True)
    k.hero((7, 9), "daremyth", owner=0, name="小满", biography="塔楼的学徒。",
           units=[("gremlin", 30), ("stoneGargoyle", 8)])
    k.sign((10, 9), "↑ 我们的城\n→ 湖边")
    k.event((12, 9), "苇语村空无一人。", guards=[("skeleton", 10)], players=[0])
    q = k.quest("artifacts", "帮我找回窥镜。", "还没找到？", "就是它！", artifacts=["speculum"])
    k.seer_hut((14, 12), q, k.reward("experience", amount=2000), label="托林")
    k.artifact((20, 4), "speculum", message="石台上静静躺着一面镜片。", guards=[("walkingDead", 8)])
    k.quest_guard((24, 20), k.quest("level", "等你到了六级再来。", "还不够。", "去吧。", level=6))
    k.pandora((18, 14), "老师的信……", give={"gold": 1000}, spells=["lightningBolt"])
    k.monster((16, 16), "walkingDead", 12, message="死者挡住了去路。")
    k.resource((4, 12), "gold")
    k.mine((3, 16), "wood")
    k.obj((9, 14), 101)
    k.town((28, 28), "necropolis", owner=1, name="灰烬墓城", main=True)
    k.hero((27, 31), "nagash", owner=1, name="守门人")
    k.timed_event(1, "{序章}\n老师失踪的第三天……", players=[0])
    k.rumor("流星", "听说有颗星星掉进了湖里。")
    k.victory_capture_town((28, 28))
    return k


def main() -> int:
    failures = 0
    k = build()
    problems = k.check()
    print("check:", problems or "ok")
    failures += bool(problems)

    try:
        k.sign((10, 9), "重叠")
        print("FAIL: overlapping placement was accepted")
        failures += 1
    except MapKitError as e:
        print("overlap rejected:", e)

    path = k.save(OUT)
    m = h3m.load(path)
    raw = h3m.build(m)
    ok = h3m.build(h3m.parse(raw)) == raw
    print("round trip:", "ok" if ok else "FAIL")
    failures += not ok
    kinds = sorted({o["kind"] for o in m["objects"]})
    print("kinds:", ", ".join(kinds))

    if "--vcmi" in sys.argv:
        r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools" / "vcmi_check.py"), str(path)])
        failures += r.returncode != 0
    print("PASS" if not failures else f"{failures} FAILURES")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
