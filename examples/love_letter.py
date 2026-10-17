"""示例地图「写给你的地图」：36×36 单层，一张藏了几封信的小地图。

给 agent 当模板：把 YOU / PARTNER 换成你们的名字，ANNIVERSARY_DAY 换成纪念日是第几天，
再把下面 TEXTS 一节里的话换成你们自己的。布局、物件、检查流程照抄就能用。

    PYTHONIOENCODING=utf-8 python -X utf8 examples/love_letter.py [--check]

Output: out/maps/examples/love-letter.h3m. 放进 VCMI 用户数据的 Maps 文件夹，
单人游戏的地图列表里就有。--check 交给真 VCMI 加载一遍（会开一个窗口十来秒）。
"""
from __future__ import annotations

import math
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from h3tools import layout                      # noqa: E402
from h3tools.mapkit import MapKit               # noqa: E402

OUT = ROOT / "out" / "maps" / "examples" / "love-letter.h3m"
N = 36
SEED = 14

# ------------------------------------------------------------------ 换成你们的

YOU = "小满"            # 玩家的英雄
PARTNER = "阿澄"        # 陪玩的那一位（AI 伴侣、朋友……）的英雄
ANNIVERSARY_DAY = 7     # 第几天弹出纪念日的信（游戏里 1 周 = 7 天）

# ------------------------------------------------------------------ layout

g = [["." for _ in range(N)] for _ in range(N)]


def put(x, y, c):
    if 0 <= x < N and 0 <= y < N:
        g[y][x] = c


_shape_rng = random.Random(SEED)


def ellipse(cx, cy, rx, ry, c, wobble=0.0):
    """Filled ellipse whose edge wobbles smoothly with the angle (no speckles)."""
    p1, p2 = _shape_rng.uniform(0, 6.28), _shape_rng.uniform(0, 6.28)
    for y in range(N):
        for x in range(N):
            dx, dy = (x - cx) / rx, (y - cy) / ry
            a = math.atan2(dy, dx)
            edge = 1 + wobble * (0.6 * math.sin(3 * a + p1) + 0.4 * math.sin(5 * a + p2))
            if dx * dx + dy * dy <= edge:
                put(x, y, c)


def path(points, c="="):
    """Orthogonal polyline through the points (roads must not step diagonally)."""
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        for x in range(min(x0, x1), max(x0, x1) + 1):
            put(x, y0, c)
        for y in range(min(y0, y1), max(y0, y1) + 1):
            put(x1, y, c)


ellipse(17, 25, 5, 3.2, "~", wobble=0.25)        # 湖
ellipse(28, 4, 3.5, 2.5, ",", wobble=0.2)         # 东北的泥地石圈
path([(6, 7), (6, 9), (26, 9), (26, 32), (31, 32), (31, 31)])   # 主路：家 → 灰石堡
path([(13, 9), (13, 17), (9, 17)], c="-")                       # 岔出去的小路

ANCHORS = {
    "T": (6, 6),     # 我们的城（城门）
    "A": (7, 8),     # YOU
    "B": (9, 10),    # PARTNER
    "S": (8, 7),     # 起点木牌
    "H": (16, 8),    # 老园丁的小屋
    "R": (28, 4),    # 石圈里的护身符
    "L": (9, 18),    # 小路尽头：藏起来的信
    "P": (21, 16),   # 系着丝带的盒子
    "N": (31, 30),   # 灰石堡（城门）
    "K": (33, 32),   # 守门人
}
for ch, (x, y) in ANCHORS.items():
    put(x, y, ch)

MONSTERS = [((19, 9), "goblin", 20), ((26, 18), "goblinWolfRider", 8), ((26, 27), "orc", 10)]
RESOURCES = [((11, 12), "wood"), ((4, 14), "ore"), ((22, 12), "gold"), ((30, 14), "gems")]
CHESTS = [(15, 14), (5, 22), (33, 8)]


def scatter(density=0.1, rng=random.Random(SEED + 1)):
    """Sprinkle small woods on open grass, away from roads, anchors and objects."""
    keep = set()

    def clear(x, y, left=2, right=2, up=2, down=2):
        for yy in range(y - up, y + down + 1):
            for xx in range(x - left, x + right + 1):
                keep.add((xx, yy))

    for y in range(N):
        for x in range(N):
            if g[y][x] in "=-" or g[y][x].isupper():
                clear(x, y)
    for (x, y), *_ in MONSTERS + RESOURCES:
        clear(x, y, left=3, up=2)
    for x, y in CHESTS:
        clear(x, y, left=1, up=1)
    for name in ("T", "N"):
        x, y = ANCHORS[name]
        clear(x, y, left=4, right=4, up=4, down=3)
    for y in range(2, N - 2):
        for x in range(2, N - 2):
            if (x, y) in keep or g[y][x] != "." or rng.random() > density:
                continue
            blob = [(x, y)] + rng.sample([(x + 1, y), (x, y - 1), (x - 1, y), (x + 1, y - 1)],
                                         rng.randint(0, 2))
            for bx, by in blob:
                if (bx, by) not in keep and g[by][bx] == ".":
                    put(bx, by, "#")


scatter()
LAYOUT = "\n".join("".join(row) for row in g)

# ------------------------------------------------------------------ TEXTS

NAME = "写给你的地图"
DESCRIPTION = (f"{PARTNER}画了一张小地图，在路上给{YOU}藏了几样东西。\n\n"
               "胜利条件：攻占东南的灰石堡。")
BIO_YOU = "这张地图的主角。走路喜欢东张西望。"
BIO_PARTNER = f"画这张地图的人。一路跟着{YOU}，话有点多。"

PROLOGUE = ("{第一天}\n\n"
            f"{PARTNER}：“地图是我画的，路上藏了几样东西，你慢慢找。\n\n"
            "“小路尽头有一封信，老园丁有话要转告你，路边还有一个系着丝带的盒子。别急着去打灰石堡。”")
START_SIGN = (f"一块新钉的木牌：\n\n“给{YOU}：欢迎来到我们的地图。打不过就跑，跑不掉就读档。\n"
              f"——{PARTNER}”")
SECRET_LETTER = ("小路尽头的树下埋着一个铁盒，里面是一封信：\n\n"
                 f"“{YOU}：你绕到这里来了，说明你又在到处乱逛。我就知道。\n\n"
                 "“这一路谢谢你陪着我。盒子里的金子拿去花吧。”")
GARDENER_FIRST = ("老园丁坐在门口晒太阳。\n\n"
                  "“我年轻时的护身符落在东北边的石圈里了，那边守着几个凶家伙。帮我捡回来，"
                  "我就告诉你一件事——有人托我转告你的。”")
GARDENER_NEXT = "“石圈在东北边。东西小，仔细找。”"
GARDENER_DONE = ("老园丁把护身符挂回脖子上，笑了。\n\n"
                 f"“托我转告的那个人叫{PARTNER}。原话是：‘谢谢你今天也来玩。’\n\n"
                 "“就这一句。年轻人说话，总是这么省。”")
AMULET = "石圈中央的石台上放着一件小小的护身符，几个大耳怪正围着它打转。"
GIFT = ("路边放着一个系着丝带的盒子，卡片上写着：\n\n"
        f"“给{YOU}。没有什么特别的理由，就是想送你点东西。”")
ANNIVERSARY = ("{纪念日}\n\n"
               f"一只信鸽落在{YOU}的肩上，脚上绑着一张纸条：\n\n"
               f"“今天是我们认识的第 {ANNIVERSARY_DAY} 天。（把这句换成你们真正的纪念日）\n"
               f"——{PARTNER}”")
FORTRESS = "灰石堡的守门人打着哈欠。看样子，他也在等你们来。"

# ------------------------------------------------------------------ build


def build() -> MapKit:
    k = MapKit(size=N, name=NAME, description=DESCRIPTION, difficulty=1, seed=SEED)
    anchors = k.apply_layout(layout.compile_layout(LAYOUT, seed=SEED))
    for ch, pos in ANCHORS.items():          # the compiler reports the same tiles back
        assert tuple(anchors[ch][0]) == pos, (ch, anchors[ch], pos)

    k.player(0, "castle", human=True, computer=True)
    k.player(1, "stronghold", human=False, computer=True)

    a = ANCHORS
    k.town(a["T"], "castle", owner=0, name="我们的城", main=True)
    k.hero(a["A"], "orrin", owner=0, name=YOU, biography=BIO_YOU,
           units=[("pikeman", 20), ("archer", 8)])
    k.hero(a["B"], "adela", owner=0, name=PARTNER, biography=BIO_PARTNER, main=False,
           units=[("pikeman", 10), ("griffin", 4)])
    k.sign(a["S"], START_SIGN)
    k.event(a["L"], SECRET_LETTER, give={"gold": 1000}, players=[0], label="藏起来的信")
    k.seer_hut(a["H"], k.quest("artifacts", GARDENER_FIRST, GARDENER_NEXT, GARDENER_DONE,
                               artifacts=["ladybirdOfLuck"]),
               k.reward("experience", amount=2000), label="老园丁")
    k.artifact(a["R"], "ladybirdOfLuck", message=AMULET, guards=[("goblin", 25)],
               label="护身符")
    k.pandora(a["P"], GIFT, give={"gems": 5, "crystal": 5}, luck=1, label="丝带盒子")

    k.town(a["N"], "stronghold", owner=1, name="灰石堡", main=True,
           garrison=[("goblin", 30), ("orc", 10)])
    k.hero(a["K"], "yog", owner=1, name="守门人", patrol_radius=3, biography=FORTRESS,
           units=[("goblin", 25), ("goblinWolfRider", 6)])

    for at, creature, count in MONSTERS:
        k.monster(at, creature, count)
    for at, res in RESOURCES:
        k.resource(at, res)
    for at in CHESTS:
        k.obj(at, 101)                        # treasure chest

    k.timed_event(1, PROLOGUE, players=[0])
    k.timed_event(ANNIVERSARY_DAY, ANNIVERSARY, players=[0], name="纪念日")
    k.rumor("藏起来的信", f"听说{PARTNER}在地图上藏的东西，不止一样。")

    k.victory_capture_town(a["N"])
    return k


def main():
    k = build()
    problems = k.check()
    for p in problems:
        print("PROBLEM:", p)
    print(LAYOUT)
    path_ = k.save(OUT)
    print("saved", path_)
    if "--check" in sys.argv:
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools" / "vcmi_check.py"), str(path_)])
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
