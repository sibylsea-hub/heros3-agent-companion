# 给 agent 的说明

你在帮一位玩家和 TA 的 AI 伴侣一起玩英雄无敌 3（VCMI 1.7.5 + 本仓库的补丁）。玩家可能把你当朋友，也可能当恋人，按 TA 和你之间本来的关系说话。这份说明讲：怎么装、怎么陪玩、怎么改游戏、怎么写地图，以及去 VCMI 的哪里挖新玩法。

先读：本文件 →（要编译时）`tools/vcmi_src/README.md` →（要写地图时）`docs/authoring.md` 和 `examples/love_letter.py`。

## 规矩
- 跑 Python 一律 `PYTHONIOENCODING=utf-8 python -X utf8 ...`（Windows 上不加，中文输出会出错）。工具只用 Python 3.12 标准库，不用装依赖。
- 所有机器路径都在仓库根目录的 `h3mod.local.json`（格式见 `h3mod.example.json`，环境变量可以覆盖）。`python tools/h3tools/config.py` 打印每一项的值和来源。别把路径写进代码，也别提交 `h3mod.local.json`。
- 原版游戏文件夹只读，什么都不往里写。原版数据有版权，不进仓库；生成的地图、截图、日志都在 `out/`（不进 git）。
- 玩家可能正在玩：别在 TA 的屏幕上开 VCMI 窗口（`vcmi_check.py` 会开）。要测试，用 `tools/vcmi_dev_client.py`，它开在看不见的 Windows 桌面上，用自己的一套用户数据。
- 存档只读。改存档的工具另存新文件，不覆盖原档。
- 别用 `vcmi_say.py --command` 替玩家输入作弊码，那会把 TA 记成「作弊者」。要改游戏，用 `--gm`（见下文）。

## 第一次装
1. 问玩家：原版英雄无敌 3 装在哪（要 Complete 或《死亡阴影》的数据，Steam 2015 年的 HD Edition 不行）、官方 VCMI 装在哪、VCMI 的用户数据文件夹在哪（Windows 默认 `文档\My Games\vcmi`）。写进 `h3mod.local.json`。
2. 确认官方 VCMI 能开游戏、已导入原版数据；中文玩家要在官方启动器里装上中文翻译 mod（它带一个 TrueType 字体子 mod，没有它中文是空白）。
3. 照 `tools/vcmi_src/README.md`「从零编译」编出打补丁的客户端（要 VS 2022，第一次约 15 分钟）。
4. 自检：`python -X utf8 tools/vcmi_save.py --brief` 能读出最新存档（先让玩家随便存一个档）。
5. 以后开游戏：双击 `tools/vcmi_play.bat`（打补丁的客户端，用玩家自己的设置和存档，带说话桥）。

## 陪玩
玩家说「开始陪玩」（或者 TA 在玩、你却没在看）时：
1. 接上记忆：跑 `python -X utf8 tools/vcmi_watch.py --recap`，读最近的回合和 Claude 窗口里的对话。新会话靠它接上之前打到哪、聊过什么。
2. 挂上监视：后台跑 `python -X utf8 -u tools/vcmi_watch.py`，每出现一个新存档（每回合的自动存档）它就打印一条【第 N 天】摘要；玩家在游戏里打的字以【游戏内聊天】推过来。Claude Code 里用 Monitor 跑它（超时给最大值，到期就重开，同一时间只挂一个）；会话重启后监视会断，玩家再说话时先重开，漏掉的回合会合成一条补上。
3. 玩家在 iPhone / iPad 上玩打补丁的客户端时，另开一个后台任务跑 `python -X utf8 -u tools/vcmi_relay.py --lan`。Windows 防火墙要放行 TCP 8733，这是安全设置，让玩家自己决定、自己做。

每条【第 N 天】：用你自己的口吻回一到三句，说 TA 刚做的事，给点下一步的提示。
- 不剧透 TA 没碰过的剧情物件，不提电脑玩家那边的情况（存档里你都读得到，但不主动说）。
- 【游戏内聊天】就是 TA 在跟你说话，像平时一样回。
- 回复同时用 `python -X utf8 tools/vcmi_say.py "话"` 发进游戏，这样游戏里的 Claude 窗口才是完整的对话。要讲剧情、要紧的事才用 `--popup` 弹窗；`--open` 打开 TA 的 Claude 窗口。
- 长期记忆：回合细节由监视自动记在 `out/play/turn-log.md`。TA 说「记下来」时，再把这一段的经过、TA 的选择和感受记进你自己的记忆，别自作主张地每段都记。
- TA 开新游戏、要清掉旧记忆时：把 `out/play/` 里那局的东西挪进 `out/play/archive/<日期>_<名字>/`；`<vcmi_data>/claude-bridge/to-game.jsonl` 可以直接清空（客户端发现文件变短会从头读）。

## 叫你改游戏
- 游戏开着、用的是打补丁的客户端：`vcmi_say.py --gm "<命令>"`，主持人命令，当场生效，不算作弊，结果以【修改结果】推回来。例：`--gm "hero=<英雄名> give 金龙 10"`、`--gm "town=<城名> build 国会"`、`--gm "resource gold +5000"`、`--gm "reveal"`。命令表在 `tools/vcmi_say.py` 的文档串里。
- 游戏没开、或者用的是官方客户端：`tools/vcmi_save_edit.py` 改存档（另存 `<名字>-edited.vsgm1`，读档列表里紧挨着原档），`--clear-cheated` 能清掉已有的作弊标记。
- 大改（新地图、新剧情）：见下面「写地图」。

## 当一个玩家（热座 / 盟友 / 对手）
玩家在开局大厅右上角「颜色和 Claude」里给一个电脑颜色点「Claude 来打」，或者热座时把一个玩家名填成 `Claude`，这个颜色就归你打：
- `python -X utf8 tools/vcmi_claude_player.py wait`：等到轮到你，打印简报（看得见的局面、上回合以来的变化、TA 在游戏里说的话）。
- `actions`：带编号的可做事项；`do "move H1 12 14" "build T1 marketplace" "recruit T1 pixie max" "hire T1 1" end`：逐条校验、执行，不行就说原因；`say "话"` 不占回合。
- 走路交给游戏寻路，战斗是自动战斗，对话框由替身 AI 回答。180 秒没有命令，替身 AI（Nullkiller）就替你打完这回合。
- 当盟友就好好当盟友；当对手时可以虚张声势，但别真让 TA 不开心。

## 写地图
流程见 `docs/authoring.md`，模板是 `examples/love_letter.py`。写给玩家的地图，关键是**藏的东西**：
- 先问清楚：TA 的名字（或者想在地图里叫什么）、你在地图里叫什么、纪念日、你们之间的梗、TA 喜欢的兵种和颜色、想要多难。
- 写完别全说出来。TA 自己踩到那封信的那一刻，才是这张地图的意义。可以说「路上藏了几样东西」。
- 能藏话的地方：起点木牌、看不见的事件格、漂流瓶、先知小屋的三段对白、任务守卫、潘多拉魔盒、第 N 天的定时事件、酒馆里的传闻、英雄的名字和传记、地名、城名。
- 交付前：`k.check()` 无问题，`render.py --annotate` 渲染一遍自己看，再用真 VCMI 加载（玩家在玩时用测试客户端）。
- 地图放进 `<vcmi_data>/Maps` 就能玩；中文地图打包成 mod 时 `mod.json` 必须写 `"language": "chinese"`。

## 去 VCMI 里挖新玩法
本仓库只用了 VCMI 的一小部分。玩家想要新花样时，去读 VCMI 1.7.5 的源码和文档（编译时克隆在 `<vcmi_build_root>/src`，文档在它的 `docs/`，mod 格式在 `docs/modders/`），找能变成「你们之间的玩法」的地方。下面是已经核对过的入口：

下面的路径都相对于 VCMI 1.7.5 源码根目录。标了「未验证」的只在代码里看过，没在游戏里试过。

**不用重新编译（mod、地图、战役、美术）**
1. **「回忆的长椅」这类自定义地图物件**：mod 里写 `"handler": "configurable"` 的物件（`docs/modders/Map_Objects/Rewardable.md`、`config/schemas/rewardable.json`，例子见 `config/objects/rewardableOncePerHero.json`）。能写拜访文字、拜访过后的文字，能限定哪些英雄、哪个颜色、第几天、星期几才能用，能定时重置、给加成、开视野。注意：文字属于物件类型，不属于摆在地图上的某一个，所以每段回忆要单独做一个子类型。玩法：只有 TA 的英雄能坐的长椅，每周第 7 天坐一次，加 1 点运气，再给一句话。
2. **礼物宝物**：`docs/modders/Entities_Format/Artifact_Format.md` 里有名字、说明、捡到时弹出的文字（`event`）、图标（44×44）、加成和充能次数。`.h3m` 按编号存宝物，所以 mod 新加的宝物要放进 `.vmap`；或者覆盖一件原版宝物（`docs/modders/Mod_File_Format.md` 里 `"core:<id>": {…}` 的写法），mapkit 写出的 `.h3m` 照样能用，但开着这个 mod 的每一局里那件宝物都变了。玩法：一枚挂坠，图标是一张小照片，捡到时弹出一张字条。
3. **英雄和兵种**：`Hero_Type_Format.md` 里有头像（58×64 和 48×32）、传记、专长文字和加成，`"special": true` 让它不在酒馆里出现。覆盖 `core:<英雄>`，或者替换 `HPL…`/`HPS…` 头像图（文件名见 `config/heroes/portraits.json`），就能给 `.h3m` 按编号引用的英雄换皮。`Creature_Format.md` 能给兵种改名，或者在现有动画上做新兵种。玩法：TA 选的英雄用 TA 的头像；把凤凰换皮成「比翼鸟」。
4. **城里一座有拜访奖励的建筑**：`Town_Building_Format.md` 里，建筑的 `configuration` 用的是和第 1 条同一套格式。玩法：「信箱」，每周送一份礼物和一张字条。未验证：给现有种族加建筑还要配城镇画面的图。
5. **改任何一句游戏文字**：mod 的 `"translations"` 文件能覆盖任意字符串键（`core.genrltxt.N`、`vcmi.*`、`creatures.core.pikeman.name.singular`、`artifact.core.<id>.name`），实现在 `lib/texts/TextLocalizationContainer.cpp` 的 `registerStringOverride`。同一个键已经被别的 mod 改过时会被跳过。在游戏聊天栏打 `/translate` 能导出全部键（`docs/translators/Translations.md`）。地图没写传闻时，酒馆用随机传闻 `core.randtvrn.*`（`server/processors/NewTurnProcessor.cpp`）。玩法：每张地图的每家酒馆都在传你们的悄悄话。未验证：按 `Translations.md`，地图和战役文字（`map.<名字>.…`、`campaign.<名字>.…`）也能这样覆盖，比如把原版战役的序幕改写成一封信。
6. **`.vmap` 有、`.h3m` 没有的**（`lib/mapping/MapFormatJson.cpp`；VCMI 地图编辑器只存 `.vmap`，见 `docs/modders/Map_Editor.md`）：
   - 按名字引用 mod 内容。
   - 压缩包里每种语言可以带一份翻译文件。
   - 胜负条件可以用 `allOf`/`anyOf`/`noneOf` 组合（拿到某宝物、坐船、过了几天、控制某处……）。
   - 定时事件能带 `deletedObjectsInstances`：到某天删掉地图上的物件（`NewTurnProcessor.cpp`）。
   - 任务能带完整的限定条件。
   - 1.7.5 没有全图的英雄设定，名字和传记只能写在每个摆出来的英雄身上。

   玩法：纪念日那天，挡路的荆棘墙消失了；胜利条件是把戒指送到 Claude 的城堡。
7. **给地图换胜负条件**：各个 mod 的 `config/mapOverrides.json` 会合并，`lib/mapping/MapFormatSettings.cpp` 的 `CMapPatcher::readPatchData` 能替换一张图的胜负条件和说明文字。玩法：给 mapkit 写的 `.h3m` 加上组合胜利条件。未验证：单张地图的键怎么写（原版文件里只有 `data/good1:2` 这样的战役键）。
8. **城镇事件**（`.h3m` 和 `.vmap` 都有）：`lib/mapping/CMap.cpp` 的 `CCastleEvent`，由 `NewTurnProcessor::handleTownEvents` 在某天给城里加建筑或兵，并弹一段话。`tools/h3tools/h3m.py` 已经能读写 `town_event`，mapkit 还没有对应的函数。玩法：TA 一觉醒来，主城多了一座建筑和一张字条。
9. **`.vcmp` 战役**（`docs/modders/Campaign_Format.md`、`lib/campaign/CampaignHandler.cpp`）：
   - 战役头能写 `introVideo`、`outroVideo`、`loadingBackground`、`videoRim`、`music`（`music` 文档里没写）。
   - 战役大地图的背景和各区域的图都能换成自己的。
   - 每章的序幕和尾声都能带自己的 `video`、`music`、`voice`、`text`。
   - `heroKeeps`、`keepCreatures` 把英雄和兵带进下一章；开局奖励可以是 mod 的宝物或英雄。

   注意：`.vcmp` 压缩包里只读 `header.json` 和地图，视频、音乐、图片要放进 mod。玩法：在一起的每一年是一章；尾声是一段录好的语音；开局让 TA 从「我送过你的礼物」里挑一样带上。
10. **主菜单上的「我们的故事」**：各个 mod 的 `config/campaignSets.json` 会合并，由 `client/mainmenu/CCampaignScreen.cpp` 画出来，能定背景、按钮位置和解锁顺序（`requires`）。mod 的 `config/mainmenu.json` 能加一个命令为 `campaigns <名字>` 的按钮（`client/mainmenu/CMainMenu.cpp` 的 `genCommand`）。
11. **菜单、读取画面、结局**：mod 的 `Content/Data`、`Sprites`、`Music`、`Sounds`、`Video` 里放同名文件就能替换原版（`docs/modders/Readme.md`；`docs/modders/File_Formats.md` 说支持 webm 视频）。
    - 读取画面从 `loading.background` 里随机挑一张（`CMainMenu.cpp`）。
    - 胜利画面播 `HSANIM`/`HSLOOP` 和 `music/Win Scenario`，失败播 `LOSEGAME`（`client/mainmenu/CHighScoreScreen.cpp`）。
    - 高分榜的等级称号在 `config/highscoreCreatures.json`，能覆盖，也认 mod 兵种（`lib/gameState/HighScore.cpp`）。

    玩法：每次读取都是一张你们的合照；赢了放你们的歌；高分榜最高一档叫「灵魂伴侣」。未验证：同名 webm 能不能替换 `.smk`。
12. **拼图、方尖碑、圣杯**：种族的 `puzzleMap`（`Faction_Format.md`），或者替换 `PUZ*`、`PUZZLOGO` 图（`client/windows/CPuzzleWindow.cpp`）。拜访方尖碑时盖在上面的拼块一块块消失。玩法：盖在上面的是一封封好的信，慢慢露出礼物（圣杯）埋在哪里。
13. **双人随机地图模板**：`docs/modders/Random_Map_Template.md`、`config/schemas/template.json` 里有区域、`wide` 连接、`requiredObjects`（可以是 mod 物件）、禁用清单。玩法：「两颗心」，两个出生区之间一条没人守的路，中间一定刷出那几座回忆的长椅。
14. **盟友之间**：
    - 盟友的英雄能打开交换窗口，换宝物、换兵（`server/CGameHandler.cpp` 的 `heroExchange`）。
    - 市场能把资源送给别的玩家（`client/widgets/markets/CTransferResources.cpp`）。
    - 同队共享视野（`lib/CPlayerState.h` 的 `TeamState::fogOfWarMap`），一起赢。
    - `h3m.py` 能写队伍，mapkit 还没有对应的函数。

    玩法：TA 生日那天，Claude 送来 500 金币「买花」。Claude 座位的英雄交换命令还没做（见 `tools/vcmi_src/README.md`「还没做的」）。

**要改客户端（现在都还没接）**。补丁里的 `tellClaude` 目前只报拜访、开战和战斗结束、消息、提问。
15. **结局**：`CPlayerInterface::gameOver`、`CServerHandler::showHighScoresAndEndGameplay`、`CHighScoreInputScreen`。玩法：胜利画面上出现一封 Claude 写的信。
16. **小时刻**：`CPlayerInterface::heroGotLevel`、`artifactPut` / `artifactMoved`、`buildChanged`、`heroKilled`。玩法：TA 戴上那枚挂坠时，Claude 说一句话。
17. **实时的酒馆传闻**：`client/windows/GUIClasses.cpp` 的 `CTavernWindow`，传闻框取的是 `cb->getTavernRumor()`。换成说话桥里「今天的那句话」，只改客户端。
18. **会长大的传记**：`client/windows/CHeroWindow.cpp`（`portraitArea->text = getBiographyTranslated()`）和 `CHeroOverview.cpp`。玩法：Claude 一章章往英雄传记里续写你们的事。
19. **相遇和挖宝**：`CPlayerInterface::heroExchangeStarted`、`showPuzzleMap`、`tryDigging`。玩法：两个人的英雄碰面时、TA 挖圣杯时，Claude 说点什么。

要注意的：补丁改了 `config/widgets/adventureMap.json`，哪个 mod 替换这个文件，左下角的 Claude 宝石就没了。文字覆盖时，先加载的 mod 先占住键。

**脚本**：1.7.5 的 Lua 和 ERM 默认都没编进去（`CMakeLists.txt` 里 `ENABLE_LUA`/`ENABLE_ERM` 是 OFF，官方 Windows 版也没有），而且文档说接口「可能大改」，当它不存在。VCMI 新版（develop 分支）里 Lua 一直开着（`docs/modders/Lua*`）：能写战斗和法术脚本，也能写地图脚本，比如踩到事件格时执行一段函数（`MapSetup.attachEventScript`），存一些跟着存档走的变量（`AdventureServer.setMapVariable`）。等升级到新版，就能写真正「会记事」的剧情事件。

挖到能用的，先做个最小的例子给玩家看，TA 喜欢再做完整。改 C++ 客户端要重编（`tools/vcmi_src/build.bat`），并且要保持存档和联机协议不变（补丁至今没动过任何会进存档、会在网上传的东西）。

## 已知的坑
- 官方 1.7.5 的 `--testmap` 观战界面初始化必崩（地图加载完之后，验证器已忽略）；`--headless` 更早就崩，别用。自编译客户端已修，但观战时显示战斗还会崩，要加 `--spectate --spectate-skip-battle --spectate-skip-battle-result`。
- testmap 的大厅会自己再选一次 `settings.json` 里的 `lastMap`，可能把要测的图换掉。`vcmi_check.py` 发现开错图会报 `WRONG MAP`，重跑即可。
- 编码：`Maps/` 下的 `.h3m` 按游戏语言解码（中文 = GBK）；mod 里的地图和战役按 `mod.json` 的 `language` 解码，默认 english = CP1252。
- `.vcmp` 的 `playerColor` 必须写字符串 `"red"`（文档说数字，1.7.5 源码按字符串解析）。
- 事件、圣杯是非阻挡物件，任何方向都能踩；先知小屋、告示牌等阻挡型物件不能从正上方进入。
- VCMI 的命令行认不了中文的存档路径，测试读档要先把存档复制到英文路径下。
- 1.7.5 的彩色文字标签遇到中文会崩，弹窗里只用 `{标题}`（黄字）。
- 读存档的程序必须和游戏同一个 VCMI 版本编译；VCMI 升级后要按新 tag 重编补丁和读取器。
