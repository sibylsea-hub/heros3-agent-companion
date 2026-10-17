# 写一张新地图：流程手册

以 `examples/love_letter.py`（「写给你的地图」，36×36）为模板，复制一份改。所有命令在仓库根目录跑，前面都要加 `PYTHONIOENCODING=utf-8`。地图工具要读原版游戏数据，先在 `h3mod.local.json` 里填好 `h3_game_dir`（见 `h3mod.example.json`）。

## 1. 先写故事
先写一份文档：人物、地点草图、胜负条件、**按出现顺序列出的全部游戏内文本**。写给某个人的地图，先问清楚要藏什么：名字、纪念日、只有你们懂的梗、想在哪一刻被看到。原版战役的全部剧情文本可以用 `python -X utf8 tools/dump_campaigns.py` 导出到 `out/campaigns/*/story.md`，对照腔调。

文本规矩：
- 中文地图用简体中文，每个字都要在 GBK 里（地图文件是 GBK）。脚本里跑一遍 `text.encode("gbk")` 就知道。别用 emoji 和生僻符号。
- 弹窗写短段落，段间空一行（`\n\n`）。`{标题}` 显示为黄字；**别用 VCMI 的彩色标签**（1.7.5 中文会崩）。

## 2. 画布局（字符画）
用脚本里的小 DSL（`put` / `ellipse` / `path`）画一张 N×N 字符画，N 取 36/72/108/144。

| 字符 | 含义 |
|---|---|
| `.` `,` `:` `*` `%` `"` `^` `_` | 草地 泥地 沙地 雪地 沼泽 荒地 熔岩 地下 |
| `~` `X` | 水、岩石（不可通行） |
| `#` | 障碍物（树、山、石头，按所在地形自动挑） |
| `=` `-` `+` | 泥路、碎石路、石板路 |
| `w` | 河 |
| `A`–`Z`、`0`–`9`（`X` 除外） | 锚点：剧情物件的入口格，编译器会返回坐标 |

注意：
- 地形块至少 2×2，水和岩石不能只靠对角相连；不满足的格子编译器会就地修（独木桥会变成沙洲），改动列在 `compiled["changes"]`。
- 路、河、锚点永远是陆地。
- 布局字符串里有荒地 `"` 时，别用 `"""` 包字符串。
- 想要自然的形状：`ellipse(..., wobble=0.25)` 是按角度平滑起伏的；空地用 `scatter()` 撒小树丛，它会避开路、锚点和物件。
- 障碍物要指定种类（山脉只要山、雨林只要树），在图例里加 `"classes"`：`{"m": {"obstacle": True, "classes": [134, 136, 147]}}`。类别号是 VCMI 的 MapObjectID（134 山、135 橡树、155 树、148 沙丘、161 礁石……），完整说明见 `tools/h3tools/layout.py` 的文档串。
- 大地图（108、144）不用手画：可以写程序生成字符画（按噪声、按真实地形数据……），再交给同一个编译器。

## 3. 摆物件（mapkit）
```python
k = MapKit(size=N, name=..., description=...)
k.apply_layout(layout.compile_layout(LAYOUT, seed=SEED))
k.player(0, "castle"); k.player(1, "stronghold", human=False)
k.town(at, "castle", owner=0, name="我们的城", main=True)
k.hero(at, "orrin", owner=0, name="你的名字", biography=..., units=[("pikeman", 20)])
k.sign(at, text); k.event(at, text, guards=[...], players=[0])
k.seer_hut(at, k.quest("artifacts", 初访, 再访, 完成, artifacts=["ladybirdOfLuck"]), k.reward("experience", amount=2000))
k.quest_guard(at, k.quest("level", ..., level=5))
k.pandora(at, text, experience=..., give={"gold": 1500}, spells=["lightningBolt"])
k.artifact(at, "ladybirdOfLuck", message=..., guards=[...]); k.monster(at, "goblin", 20, message=...)
k.mine(at, "gold"); k.resource(at, "gems"); k.obj(at, 101)  # 宝箱等任意物件
k.ocean_bottle(at, text)              # 漂流瓶：放在水上，要坐船才捡得到
k.timed_event(day, text, players=[0]); k.rumor(name, text)
k.victory_capture_town(at)            # 或 victory_acquire_artifact("…") / victory_defeat_hero(at)
k.campaign_hero(at, "orrin")          # 战役第二章起：从上一章带过来的英雄的落脚点
k.ban_artifacts("…")                  # 剧情唯一宝物：不让商人卖、不随机刷出
```
- 坐标一律是**英雄踩上去/走进去的那一格**；物件本体自动往左上方延伸。
- 名字用 VCMI 标识符或游戏里的中文名；查：`python -X utf8 tools/h3tools/gamedb.py find 天使`。
- 先知小屋、告示牌等「阻挡型」物件不能从正上方进入；事件、怪物、宝物、任务守卫可以。
- 事件格在地图上看不见，踩到才弹出，适合藏「只有绕路过去才读得到」的信。告示牌看得见，谁都会去读。

## 4. 检查
1. `k.check()`：重叠、出界、压水、**从主角出发每个入口都走得到**。
2. 渲染全图看美观：`python -X utf8 tools/h3tools/render.py out/maps/examples/xxx.h3m -o out/previews/xxx.png --annotate`（彩框标出剧情物件：黄告示牌、青先知、品红事件、白潘多拉、橙任务守卫）。
3. 真 VCMI 加载：`python -X utf8 tools/vcmi_check.py out/maps/examples/xxx.h3m`（会弹窗十来秒；加载后的崩溃是 1.7.5 观战模式的已知 bug，脚本会忽略）。加 `--screenshot out/previews/xxx-vcmi.png`：用自编译客户端以红色人类视角开图，截一张真游戏画面（第 1 天的弹窗也会截进去）。玩家正在玩的时候别跑，它会在玩家的屏幕上开窗口；改用 `tools/vcmi_dev_client.py`（开在看不见的桌面上）。
4. 装进 mod 后让 VCMI 解码全部文本：`python -X utf8 tools/vcmi_texts.py <mod 名>`。

## 5. 装进游戏
- 单张地图：把 `.h3m` 复制进 VCMI 用户数据的 `Maps` 文件夹（`python tools/h3tools/config.py vcmi_data` 打印位置），单人游戏的地图列表里就有。
- 打包成 mod（手机上「从文件安装」，或者一次装好几张图）：mod 里的地图按 `mod.json` 的 `language` 解码，默认是 english（CP1252），中文地图**必须写 `"language": "chinese"`**。
- 多章战役：VCMI 认 `.vcmp`（战役说明 + 每章一张地图 + 战役大地图的图片）。`tools/h3tools/h3c.py` 能无损读写原版 `.h3c`，可以拿原版战役对照结构。`.vcmp` 的 `playerColor` 必须写字符串 `"red"`（文档说数字，1.7.5 源码按字符串解析）。
