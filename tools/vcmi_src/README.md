# 自编译 VCMI 1.7.5：读存档、改存档、打补丁的客户端

> 许可证：这个文件夹里的补丁、`save_reader.cpp`、`save_edit.cpp` 和编译脚本是 VCMI 的修改版，跟 VCMI 一样用 GPL-2.0-or-later（全文见 `COPYING`）。仓库其余部分用 MIT。

官方 VCMI 1.7.5（`<vcmi_dir>`）**不动它**。这里从同一个 tag 自己编一份，装在 `<vcmi_build_root>\install`，做三件事：

文中 `<vcmi_dir>`、`<vcmi_data>`、`<vcmi_build_root>`、`<vs_root>`、`<conan_home>` 是仓库根目录 `h3mod.local.json` 里的同名设置（格式见 `h3mod.example.json`），`python tools/h3tools/config.py` 会打印当前的值。

1. **读存档**（`vcmi_save_reader.exe`）：链接 VCMI 自己的库加载 `.vsgm1`，输出 JSON。存档是 C++ 二进制序列化，格式随版本变，所以必须和游戏同一个版本编译。**VCMI 升级后要按新 tag 重编**，旧读取器读新存档会报 “too new file format” 或直接读坏。
2. **改存档**（`vcmi_save_edit.exe`）：加兵、资源、宝物、经验/等级、主属性、技能、法术，清掉「作弊者」标记，另存成新存档。走的是和作弊码同一套服务器调用，但不发 `PlayerCheated`，所以不会被记成作弊者。
3. **打补丁的客户端**（`client-testmap.patch`）：修 `--testmap` 的崩溃，加 `--testmap-human`（红色留给人类；`vcmi_check.py --screenshot` 用它截冒险地图），加和 Claude 互通的「说话桥」和游戏内的「Claude」聊天窗口，并把玩家在游戏里遇到的事（拜访、消息、战斗）报给 Claude；热座里名叫 Claude 的座位、或者大厅里交给 Claude 的电脑颜色由 Claude 来打（「Claude 当本地玩家」）；每个颜色可以显示成另一个颜色（「换颜色显示」）。服务器只加了「主持人命令」（一种聊天命令，见「说话桥」）和「电脑座位可以叫 Claude」；库只改了一处显示（颜色名跟着换），没动任何存进存档或在网上传的东西，所以存档和联机协议跟官方版完全一样。

日常用法见仓库根目录的 `AGENTS.md`；这里是原理和重编步骤。

## 文件
| 文件 | 作用 |
|---|---|
| `save_reader.cpp` | 读存档工具 |
| `save_edit.cpp` | 改存档工具 |
| `hook.cmake`、`targets.cmake` | 把读取器挂进 VCMI 自己的 CMake 工程（`-DCMAKE_PROJECT_VCMI_INCLUDE=hook.cmake`），VCMI 源码不用改 |
| `client-testmap.patch` | 客户端补丁，`git apply` 到 1.7.5 源码 |
| `build.bat` | 配置、编译、安装到 `<vcmi_build_root>\install` |
| `../vcmi_local.py` | 运行时设置：写 `install\config\dirs.json`，把玩家的 `settings.json`、`modSettings.json` 复制进私有配置目录 |
| `../vcmi_save.py` | 读存档的 Python 入口（`--brief` 中文摘要） |
| `../vcmi_save_edit.py` | 改存档的 Python 入口（输出 `<名字>-edited.vsgm1`，改完用读取器复查） |
| `../vcmi_play.py`、`../vcmi_play.bat` | 用玩家自己的设置和存档启动打补丁的客户端（带说话桥） |
| `../vcmi_say.py` | 往游戏里发聊天、弹窗、打开 Claude 窗口或「替玩家输入」的命令 |
| `../vcmi_relay.py` | Claude 中转站：iPad、iPhone 上的游戏经局域网和 PC 上的 Claude 互通，并把每回合存档传回 PC |
| `../vcmi_claude_player.py` | Claude 当热座玩家：看简报、列出能做的事、下命令、结束回合、说话（见「Claude 当本地玩家」） |
| `../vcmi_dev_client.py` | 在看不见的 Windows 桌面上开测试客户端（`install-dev` + `userdata-dev`），玩家玩着的时候也能测 |

## 运行时的目录约定
- 工作目录必须是 `install\`：Windows 上 VCMI 从当前目录读游戏数据（`config\`、`Mods\vcmi`）。
- `install\config\dirs.json` 由 `vcmi_local.prepare()` 写：用户数据 = `<vcmi_data>`（读 H3 原版数据、地图、mod），但 config / logs / cache / Saves 都指到 `<vcmi_build_root>\userdata\`。所以自编译版跑测试、自动存档都不会碰玩家的设置和存档。
- 读取器只读存档文件，不写任何东西进 `VCMI-data`。
- `vcmi_play.py` 用的是另一份 `dirs.json`（`vcmi_local.prepare_play()`）：只写 `userDataPath`，跟官方那份一模一样，设置、日志、存档都是玩家自己的。两个客户端的存档互通。
- 例外：官方 `VCMI_server.exe` / 客户端每次启动都会把 `VCMI-data\config\settings.json`、`modSettings.json` 重新写一遍（内容不变），这是 VCMI 自己的行为。

## 从零编译（约 15 分钟，不需要管理员权限）
前提：VS 2022 的「使用 C++ 的桌面开发」（`<vs_root>`，用它自带的 CMake 和 Ninja）、uv、git、Python 3.12。在仓库根目录打开命令提示符，从第 1 步开始。

```bat
:: 1. 源码 + 补丁（H3MOD 记下本仓库的位置）
set H3MOD=%CD%
git clone --depth 1 --branch 1.7.5 --recurse-submodules --shallow-submodules https://github.com/vcmi/vcmi.git <vcmi_build_root>\src
cd /d <vcmi_build_root>\src
git apply "%H3MOD%\tools\vcmi_src\client-testmap.patch"

:: 2. Conan 和 VCMI 官方预编译依赖（release 标签见源码 CI\install_conan_dependencies.sh，1.7.5 是 2026-07-07）
uv tool install conan
set CONAN_HOME=<conan_home>
conan profile detect
curl -L -o <vcmi_build_root>\deps-download\dependencies-windows-x64.txz https://github.com/vcmi/vcmi-dependencies/releases/download/2026-07-07/dependencies-windows-x64.txz
conan cache restore <vcmi_build_root>\deps-download\dependencies-windows-x64.txz
conan install . --output-folder=<vcmi_build_root>\conan-generated --build=never ^
  --profile=dependencies/conan_profiles/msvc-x64 ^
  -c tools.cmake.cmaketoolchain:generator=Ninja -c tools.microsoft.msbuild:vs_version=17 ^
  -s "&:build_type=RelWithDebInfo" -s "&:compiler.version=194" -o "&:target_pre_windows10=True"

:: 3. 编译 + 安装（第一次带 configure）
"%H3MOD%\tools\vcmi_src\build.bat" configure
```
- 预编译依赖是 v142 工具集（`compiler.version=192`）编的，VS 2022 默认只带 v143。`-s "&:compiler.version=194"` 只改 VCMI 自己，依赖照用预编译包。两个工具集二进制兼容。
- `target_pre_windows10=True` 和官方 CI 一致（决定 boost 版本）。
- 不编启动器、地图编辑器、MMAI（省时间、省 Qt）。Nullkiller2 要编：玩家的设置默认用它当电脑 AI。
- 改了 `save_reader.cpp` 或补丁后，只跑 `build.bat`（不带参数）。

## `--testmap` 崩溃：原因和补丁
`--testmap` 把所有颜色交给 AI，客户端于是开观战界面：给 `PlayerColor::SPECTATOR` 装一个 `CPlayerInterface`。1.7.5 里这条路走不通：

1. **初始化崩溃**（日志停在 “Initializing the interface for player invalid”）：`CPlayerInterface::initializeHeroTownList()` 执行 `*cb->getPlayerState(playerID)->playerLocalSettings`，而观战者不是有效玩家，`getPlayerState(SPECTATOR)` 返回空指针。而且观战界面的回调本身也绑在 SPECTATOR 上，冒险地图界面到处按 `playerID` 读玩家状态，光加空指针检查还不够，下一步照样崩（读 0x618）。
2. **修法**（照搬 VCMI develop 分支的修法）：空指针检查，外加 `CClient::findPlayerColorForSpectatorInterface()`：观战界面借一个真实玩家的颜色（第一个正在行动的玩家）来建界面和回调。
3. **还剩的**：观战时一场战斗**显示完**结算那一刻还会崩（观战界面借了红色，去回答红色的战斗结算询问，“Cannot answer the query -1”）。develop 为此又改了一串战斗/网络包处理，不值得搬：加 `--spectate --spectate-skip-battle --spectate-skip-battle-result` 跳过战斗画面就稳了（实测 ch1 全 AI 跑 60 秒、6 天、10 个回合不崩）。
4. **另外两个 testmap 的坑**（也在补丁里）：
   - `--testmap` 会强制 `session.onlyai = true`，所以即使留了人类颜色也会被 AI 接管；`--testmap-human` 时不再强制。
   - 大厅界面文件列表扫完后会自己去选 `general.lastMap`（比如 `MAPS/Arrogance`），可能把 testmap 选的图换掉——**官方客户端的 `vcmi_check.py` 默认模式也可能因此测错图**。补丁让 testmap 先把 `lastMap` 设成要测的图。

## 存档 JSON
`vcmi_save_reader <存档> [-o out.json] [--full-text] [--log 文件]`，或者用 `python -X utf8 tools/vcmi_save.py`。
- `day / week / month / day_of_week`、`acting_players`、`map`（名字、文件、尺寸；战役还有 `campaign`、`scenario`，从 0 数）。
- `game.started`：这一局的开局时间（本地时间，`2026-10-17T03:54:27` 这样）。服务器开新局、开战役的一章时盖上，这局的每个存档（包括手动存档）都带着，VCMI 的自动存档文件夹也按它命名。`vcmi_memory.py` 靠它分出哪些记忆属于哪一局。
- `players[]`：颜色、是否人类、状态、资源；`heroes[]`（名字、类型、**站立格** `pos`、等级、经验、四项主属性（含宝物加成）与基础值、魔法、行动力、二级技能、部队、穿戴宝物、背包、法术书）；`towns[]`（名字、种族、入口格、已建建筑、驻军、驻守/来访英雄）；`visited_objects`、`destroyed_objects`、`quest_log`（物件 id）。
- `story_objects[]`：告示牌、事件、先知小屋、任务守卫、潘多拉、宝物、监狱、带留言的怪物……以原始地图为准，每个都给 `present`（还在不在地图上）、`visited_by`、文本（默认截 180 字），先知/守卫给任务状态（`active_for` 已接、`completed`）。
- `removed_objects[]`：原始地图上有、现在没了的所有物件（捡走的资源、打掉的怪、触发过的事件）。
- 物件 id = 在地图文件里的序号，和 `h3m.py` 读出来的 `objects` 下标一致。游戏中删除物件只把槽位置空，id 不会挪。
- 原始地图从存档里拿：战役存档里带着每章的地图原文，单张地图按 `map.file` 从 mod 里读。
- 看不出来的：告示牌读没读过（VCMI 不记录）。

## 改存档
`python -X utf8 tools/vcmi_save_edit.py [存档] [-o 输出] <改动…>`，不给存档就改最新的那个。改动按顺序执行：
`--hero <编号|名字>`、`--player <颜色>`、`--give <兵种> <数量>`、`--resource <gold|wood|…> <n|+n|-n>`、`--artifact <宝物> [槽位|backpack]`、`--exp <n>`、`--levels <n>`、`--primary <attack|defence|spellpower|knowledge> <n|+n|-n>`、`--skill <技能> <0-3>`、`--spell <法术>`、`--mana <n|+n|-n>`、`--clear-cheated`。名字可以是 VCMI 标识符（`titan`），也可以是游戏里的中文名（`泰坦巨人`）。槽位名：head shoulders neck rightHand leftHand torso rightRing leftRing feet misc1–misc5 mach1–mach4 spellbook。
- **不覆盖原存档**：结果写在旁边，叫 `<名字>-edited.vsgm1`（重名就 `-edited-2`…），读档列表里就在原档下面。
- 原理：像服务器一样把存档整个读进来（游戏状态 + 服务器状态），用 `CGameHandler` 的 `insertNewStack`、`giveResources`、`giveHeroNewArtifact`、`giveExperience`、`changePrimSkill`、`changeSecSkill`、`changeSpells` 改，也就是作弊码用的那几个函数，但不发 `PlayerCheated`。最后按 `CGameHandler::save` 的方式写出，文件头、地图信息都和游戏自己存的一样。
- 升级时自动选技能：升级对话框是服务器上的「询问」，存档里不保存。编辑器替玩家选第一个候选技能。注意：VCMI 只在玩家界面报告「就绪」以后才真正把等级加上去，所以编辑器要先把玩家标成就绪，不然每次回答都会在同一级再升一次。第一版就因此一次加了 20 多点属性，已修。
- `--clear-cheated`：清掉 `PlayerState::cheated`（高分榜上显示「作弊者」、0 分的那个标记）和两个胜负作弊码标记。战役里已经打完的章节，高分记录各自存着「用过作弊」，也一起清掉。

## 说话桥（Claude ↔ 游戏）
游戏里显示的名字是 Claude（窗口标题、宝石旁的标签、默认发言人、主持人命令的【Claude】前缀）。补丁的早期版本叫「回声」，所以代码里还认这个名字：座位名填「回声」也算 Claude 的座位。

只有打补丁的客户端有，PC 上用 `tools/vcmi_play.py`（或双击 `vcmi_play.bat`）启动。代码在 `client/ClaudeBridge.*`（收发）和 `client/windows/ClaudeChatWindow.*`（界面）。

**界面**
- 冒险地图左下角那颗宝石就是入口，旁边常驻一个「Claude」小标签（宝石本身和另外三个角的装饰珠一模一样，没标签认不出来）：点宝石或标签打开「Claude」窗口。窗口上半部分是玩家和 Claude 的对话，下面一行是输入框，√ 发送（回车也行），⊘ 关闭（Esc 也行）。标题下面显示连接状态：本机直连 / 已连上电脑 / 连不上电脑。
- 窗口关着时 Claude 来了消息：标签变成红底、一秒闪一下，显示「Claude 2」这样的未读条数；左下角聊天栏照样短暂显示一行。
- 打中文：在电脑上用微软拼音（Win+空格 切过去）。SDL 默认把输入法的候选词框藏起来，补丁在 `ScreenHandler` 里设了 `SDL_HINT_IME_SHOW_UI`，让 Windows 自己画候选框。用 Moonlight 之类的软件串流到手机上玩时，手机键盘打不进去，要接电脑键盘打字。
- 窗口里打的字只发给 Claude，不进游戏服务器，所以不会被当成作弊码。按 Tab 在聊天栏里打的字照旧进游戏，同时也转给 Claude。
- 宝石盖在地图左下角上，补丁让地图不再接收已经被宝石接住的点击（`MapViewActions` 的 `lastActivated`），否则点宝石时英雄会往宝石下面那格走。
- 对话记录在客户端运行期间一直保留，读档、换图都不清；重开游戏才清空（历史在电脑上的 jsonl 里都有）。

**消息格式**：一行一个 JSON：`{"ts", "from", "text", "kind"}`。
- `to-game.jsonl`：Claude 往里追加，用 `tools/vcmi_say.py` 写。客户端只处理它启动以后新写的行；还没进游戏时先攒着，进了再显示。`kind`：
  - `chat`（默认）：进 Claude 窗口和左下角聊天栏，发言人是 `from`（默认「Claude」）。只在玩家本机显示，不经过服务器。
  - `popup`：弹窗，也记进 Claude 窗口。文字里 `{标题}` 显示成黄色。
  - `open`：把 Claude 窗口打开（`vcmi_say.py --open`）。
  - `command`：等于玩家在聊天栏里打了这句，作弊码会生效，也会把玩家记成作弊者。要「不算作弊」地改，用改存档。
- `from-game.jsonl`：玩家说的每一句（Claude 窗口里的、Tab 聊天栏里的），以及 `command` 替玩家发的那句（`kind` 标成 `command`）。`from` 是这局里玩家的玩家名，新开的游戏是「玩家」。
- 同一个 `from-game.jsonl` 里还有 `kind` 为 `event` 的行：游戏里发生在玩家身上的事，让 Claude 知道玩家在地图上干了什么。来自 `CPlayerInterface` 的几个回调：拜访物件（`heroVisit`，「孟斐拉 拜访 学者 (30, 57, 0)」）、弹出的消息（`showInfoDialog`，事件、先知、告示牌、捡东西的文字都在这里，前缀「消息：」）、要玩家选择的提问（`showBlockingDialog`，前缀「问：」）、开战（对手的英雄和兵）、战斗结束（输赢、双方损失、经验）。不进 Claude 窗口。`vcmi_watch.py` 把它们攒着，附在下一条回合摘要后面（「这期间游戏里：」，每条最多 30 行），也就进了 `turn-log.md` 和 `--recap`。

**主持人命令（实时修改器）**：`vcmi_say.py --gm "<命令>"` 写一行 `kind` 为 `gm` 的消息。客户端（`ClaudeBridge::deliver`）把它变成聊天消息 `#host <密钥> <命令>`，直接用 `CCallback::sendMessage` 发给服务器，不经 `GameChatHandler`，所以密钥不会进 `from-game.jsonl`，也不会进联机大厅的聊天。密钥在 `claude-bridge/host-key.txt`，客户端第一次启动时生成；服务器（`PlayerMessageProcessor::handleHostCommand`，补丁加在 `playerMessage` 最前面）读同一个文件比对，对不上就丢掉、什么也不广播。别的电脑上的玩家不知道密钥，用不了。
- 命令和改存档的一样，调的也是作弊码用的那些 `CGameHandler` 函数，只是不发 `PlayerCheated`：`give` 兵、`resource`、`artifact`、`exp`、`levels`（照常弹升级选技能的窗口）、`primary`、`skill`、`spell`、`mana`、`move`（加行动力）、`build` / `raze`（城镇建筑，`build` 用 `buildStructure(force)`：不花钱、不看前置条件、不管「一个玩家一座国会」）。2026-10-17 又照原版修改器和作弊码里最常用的几样加了：`army <格> <兵种> <数量>` / `army <格> none`（改或清任意一格；只写 `town=` 时改城里驻军）、`recruits [等级] <数量>`（城里兵营的待招募数）、`reveal` / `hide`（地图全开、遮回）、`puzzle`（拼图全开，重写了 `cheatPuzzleReveal`，去掉它里面的 `PlayerCheated`）、`luck` / `morale` / `fly`（永久运气、士气最高、飞行，直接调 `cheatMaxLuck` 等，它们本来就不记作弊）、`teleport <x> <y> [z]`（站立格坐标，内部用 `convertFromVisitablePos` 换成 `moveHero` 要的锚点坐标）。调研时也看过但还没做的：解除每天只能建一座、立即结束战斗、魔法行会加法术、锁定资源。前面可以加 `hero=`、`town=`、`player=` 指定对象，默认是玩家当前选中的英雄或城镇。名字认 VCMI 标识符，也认游戏里的中文名。
- 结果以系统消息「【Claude】……」广播（玩家的聊天栏里看得到），客户端把这条消息以 `kind` 为 `gm-result` 写进 `from-game.jsonl`，`vcmi_watch.py` 马上推【修改结果】。出错时是「【Claude】没办成：原因」。
- iPad、iPhone 上一样：设备上的客户端从中转站收到 `gm` 行，用设备自己的密钥发给设备上的服务器。

**两种传法**
1. **本机文件**（PC 默认）：读写 `<userDataPath>/claude-bridge/` 下的两个 jsonl，也就是 `<vcmi_data>\claude-bridge\`。环境变量 `VCMI_CLAUDE_BRIDGE` 可以改这个文件夹，测试时用。客户端每 0.3 秒看一次。
2. **中转站**（iPad、iPhone、别的电脑）：设备的 `claude-bridge/config.json` 写 `{"relay": "http://<PC 的地址>:8733", "token": "<口令>"}`，客户端就改用 HTTP 找 PC 上的 `tools/vcmi_relay.py`，每秒一次：取 Claude 的新消息、送出玩家说的话。另外每 3 秒扫一遍存档文件夹，把新存档（每回合的自动存档）传上去，中转站放进 `claude-bridge/remote-saves/`，`vcmi_watch.py` 也盯着那里，所以 iPad 上玩一样有每回合推送。连不上时玩家说的话先存着，连上再发。HTTP 用的是 Boost.Beast（VCMI 依赖里自带，只用头文件）；iOS 的 `Info.plist` 补了 `NSLocalNetworkUsageDescription`，第一次连局域网时系统会问一次。中转站的口令在 PC 的 `claude-bridge/relay.json` 里，启动时会把设备该用的 config.json 打印出来，也写进 `claude-bridge/device-config.json`。
- 线程：收发都在 `claudeBridge` 线程里，结果用 `ENGINE->dispatchMainThread()` 交给界面线程（界面线程持有界面锁，调界面安全）。没有用客户端的 stdin 控制台：那样必须由启动器握住 stdin，玩家自己双击启动的游戏就收不到了。
- iOS 上编译要注意：时间戳用 `localtime_r`（Windows 才有 `localtime_s`），路径一律按 UTF-8 传。

## Claude 当本地玩家（热座）
路线图第 2 条的第一版（2026-10-17）。代码在 `client/ClaudePlayer.*`，命令行那一头是 `tools/vcmi_claude_player.py`。

**怎么开**（两种，效果一样）：
- 开新局或读档的大厅里点右上角「颜色和 Claude」（单机和热座大厅都有；联机大厅那个位置是聊天按钮，没有它），在窗口里给一个电脑颜色点「Claude 来打」。哪个颜色都行，包括人类不能选的「只能电脑」的颜色，盟友还是对手看地图怎么分队。
- 多人游戏 → 热座，其中一个玩家名填 `Claude`（大小写随意，`Claude 2` 也算；填 `回声` 也行），给它分一个颜色。读热座存档时名字也这样填。
- 两种都是靠座位的名字：`ClaudePlayer::isClaudeName()`。电脑座位叫 Claude，要服务器两处补丁（`CVCMIServer::setPlayerName` 允许给电脑座位改名，只认 Claude 和改回「电脑」；`setPlayerConnectedId` 把座位交回电脑时不把 Claude 的名字冲掉），名字本来就存在存档的 `StartInfo` 里，所以读档以后还是 Claude 来打。服务器那边这个座位照样是电脑，难度加成之类按电脑算；人类热座座位叫 Claude 时按人算。
- 客户端：电脑座位在 `CClient::initPlayerInterfaces` 的电脑分支里照常装 AI，名字是 Claude 就再 `ClaudePlayer::attach`；人类座位走下面这条。
- 客户端不给这个颜色装 `CPlayerInterface`，装的是普通的冒险 AI（和真人同队用 `settings.json` 的 `ai.adventureAlliedAI`，否则 `adventureEnemyAI`，默认 Nullkiller2），下面叫「替身」。替身照常收到所有游戏事件，替 Claude 回答对话框（升级选技能、打不打、拜访兵营时招不招兵……），打战斗（自动战斗）。
- 但回合不给替身：`NetPacksClient` 处理 `PlayerStartsTurn` 时先问 `ClaudePlayer::onYourTurn`，是 Claude 的颜色就不调替身的 `yourTurn`。`ClaudePlayer` 自己的 `claudePlayer` 线程接下回合（像 `CPlayerInterface` 一样马上回答回合询问），写简报，等命令。命令用这个颜色自己的 `CCallback`（`waitTillRealize`，拿着 `CGameState::mutex` 的共享锁，等服务器答复时自动放开），所以只看得到这个颜色看得到的东西。
- 替身接手：最后一条命令（或回合开始）之后 `timeout` 秒没有新命令，就调 `standIn->yourTurn(QueryID(-1))`。替身把这回合剩下的打完并结束回合；-1 表示回合询问已经答过了，Nullkiller 不会再答一次。Claude 已经做了一半也一样。
- 服务器那边这个座位是「人」，难度、热座规则按人算。界面上当电脑看（`ClaudePlayer::isPerson()` 代替 `isControlledByHuman()`，用在 `CPlayerInterface`、`AdventureMapInterface`、`MapViewController`、`BattleWindow`）：轮到 Claude 时玩家不会收到「轮到某某」的热座对话框，屏幕不换人，像看电脑走那样显示 Claude 的英雄移动；和 Claude 打仗时战斗窗口按「只有一方是人」处理。

**文件**：`<claude-bridge>/player/`，PC 上就是 `<vcmi_data>\claude-bridge\player\`（`VCMI_CLAUDE_BRIDGE` 换文件夹）。
| 文件 | 内容 |
|---|---|
| `inbox.jsonl` | Claude → 游戏，一行一个请求：`{"id", "lines": ["move H1 12 14", "end"], "color"?}`。只处理游戏开始以后写的行 |
| `outbox.jsonl` | 游戏 → Claude：`{"ts", "kind", "color", "day", "text", "id"?, "ok"?}`。`kind`：`turn`（回合开始的简报）、`reply`（回答请求，`id` 相同）、`handover`（替身接手）、`turn-end`（这回合的变化） |
| `briefing.md` | 最近一次的回合简报 |
| `state.json` | 每个 Claude 座位：颜色、名字、是不是轮到了、是否已交给替身、第几天、还剩几秒 |
| `config.json` | 可选：`{"timeout": 180, "idleTimeout": 20, "listenerFresh": 900}`（秒）。测试时环境变量 `VCMI_CLAUDE_PLAYER_TIMEOUT` 覆盖 `timeout` |
| `listener.json` | `vcmi_claude_player.py` 每次运行都碰一下（`wait` 时每 30 秒一次）。回合开始时它超过 `listenerFresh` 秒没被碰过，就当没有窗口在听，限时改用 `idleTimeout`，免得玩家每回合白等三分钟；这回合里一来命令就恢复成 `timeout` |

**简报**（几 KB 中文，`briefing.md`）：日期、限时、地图；「上回合以来」：和自己上一回合结束时的快照比（资源、英雄位置/等级/兵、城镇建筑和驻军、占到或丢掉的矿和兵营、看得见的别家英雄和城），加上这期间弹给这个颜色的消息（地图事件的剧情文字也在这里）、替身替它回答了的询问、战斗结果；资源；英雄（H1、H2……：坐标、行动力、魔法、四项属性、兵、技能、宝物）；城镇（T1……：驻军、已建、今天能建什么和价钱、缺多少、能招什么、酒馆里的英雄）；看得见的别家；每个英雄今天和明天走得到的物件（游戏自己的寻路，带到了还剩多少行动力）。迷雾里的看不到，事件格（`Obj::EVENT`）本来就不列。

**命令**：一次可以发好几条，逐条校验、执行，失败就说原因，后面的不执行。
- `move H1 x y [z]`：目的地要看得见。按游戏寻路（`PathfinderCache`，和界面同一套）一步一步走：每步 `moveHero`，等服务器答复、等对话框都有人答了、战斗打完，再走下一步。行动力用完、途中打仗、被挡住就停下并说明。目的地是物件的入口格就是拜访它，是怪物就是开打；资源堆、宝箱、怪物这种阻挡型物件是站在旁边一格拜访的。
- `build T1 <建筑>`：简报方括号里的代号（`marketplace`）、编号或中文名。先 `canBuildStructure`：钱不够说还差多少，前置没建说要先建什么。
- `recruit T1 <兵种> [数量|max]`：进来访英雄，没有就进驻守英雄，再没有就进城里驻军。超过可招数量就按可招的招，钱不够说最多买几只。
- `hire T1 [1|2|名字]`：酒馆招英雄（要有酒馆、2500 金、城门口没有来访英雄）。
- `end`：结束回合，回答就是这回合的变化。
- 随时能用、不占回合：`say <话>`（玩家的 Claude 窗口和左下角聊天栏里显示，发言人是这个座位的名字）、`brief`、`status`、`actions`（带编号的可做事项，之后直接发编号就执行那一项）、`help`。测试客户端（设了 `VCMI_CLAUDE_PLAYER_DEV`）还有 `screenshot`。
- 「等对话框」怎么判断：客户端记下发来的每个询问（`Query` 包，`CClient::handlePack` 里），回答（`QueryReply`，`CClient::sendRequest` 里）发出去才划掉；再加上这个颜色没有正在打的仗。30 秒还有没人答的询问就不等了。

**还没做的**：中转站（iPad、iPhone 上的游戏还没法把简报送到 PC，那边的 Claude 座位每回合都会在 `idleTimeout` 后交给替身）；MCP 版；同时回合；多个窗口抢着下命令时的操作锁；战略备忘跟着存档走（现在读档后的第一份简报没有「上回合以来」）；更多命令（英雄和城里换兵、英雄之间交换、买魔法书和宝物、坐船、施法、拆分部队）；替身替 Claude 答的对话框还不能让 Claude 自己选（比如拜访野外兵营时替身会自己招兵花钱）。

## 换颜色显示（只改画面）
有的玩家不喜欢红色，大多数地图人类又只能选红色。每个颜色可以显示成另一个颜色：大厅的「颜色和 Claude」窗口里点「显示成某色」，每点一下换下一个颜色，两个颜色成对对调（红显示成紫，紫就显示成红，别的颜色不变）。规则、存档、联机都不变，只是玩家这台设备上画出来的样子；对所有游戏生效，读旧存档也是新颜色。
- 存在 `<用户配置>/h3mod-colors.json`（PC 上是 `<vcmi_data>\config\h3mod-colors.json`）：`{"shown": ["purple", "blue", ...]}`，第 i 个是第 i 个颜色（红、蓝、棕、绿、橙、紫、青、粉）显示成的颜色，必须八个颜色各出现一次，不然整个不用。没放进 `settings.json`：官方 VCMI 和我们共用那个文件，而且它按 schema 严格校验，多一个键会出问题。
- 原理（`client/render/Graphics`）：玩家颜色的调色板（`DATA/PLAYERS.PAL`，界面边框、按钮、战场上的英雄旗都靠它上色）和颜色表（地图上城、矿的旗子，小地图，计时器）在加载时记下原样，按对调表重排；`Graphics::shownColor()` 给按玩家编号选图的地方用：英雄旗（`AF00`–`AF07`）、船旗、对话框边框（`CMessage`）、各种小旗和徽章（`ITGFLAGS`、`CREST58`、`CComponent` 的旗）、大厅玩家行的底色和旗、冒险地图按钮、世界视图图标、盗贼公会、城镇界面的旗、自动生成的配色贴图、按颜色命名的贴图。
- 颜色名（`lib/texts/TextLocalizationContainer::translateString`）：查 `vcmi.capitalColors.N`、`core.plcolors.N` 和 `core.arraytxt.23`–`30`（「红色玩家占有」那八句）时换成对调后的那一句，所以游戏里写的颜色名也对上。这是库里唯一的改动，只影响显示。
- Claude 的简报里「红」「蓝」写的是地图上真正的颜色，但游戏自己的文字（物件的「……玩家占有」）已经换过，所以颜色换过时简报开头会说明哪个显示成哪个，让 Claude 跟玩家说话时用玩家看到的颜色。
- 已经画好、还开着的图不会重画：在大厅里改了颜色，大厅背后的行和旗在关窗口时刷新；游戏里的界面都是开局时才画，所以进了游戏就是新颜色。

## 任何颜色都能选（真的换势力）
地图文件给每个颜色标了「人可以玩 / 只能电脑」，大多数单人图只有红色能选。补丁让大厅「高级选项」里所有颜色的旗子都能点（`OptionsTab`：只要那个颜色电脑能玩就算「玩家或电脑」；战役除外，战役每章用哪个颜色是战役文件定死的），点哪个就去玩哪个。服务器（`CVCMIServer::setPlayer`）在真人接手「只能电脑」的座位时去掉 `compOnly`，座位交回电脑时按地图再标上；存档里记的是去掉以后的样子，所以读档后还是玩家玩那个颜色。
- 这是真的换了势力：那个颜色的城、英雄、开局，地图上写给这个颜色的事件，都归玩家。反过来，作者只给红色写的剧情会落到红色电脑身上；只勾了「电脑触发」的事件（多半是给电脑的补给）玩家当了那个颜色就不触发；特殊胜负条件常常围着原来那一方写，换了颜色可能很奇怪。
- 本来就有好几个人类颜色的图，VCMI 原来就能在高级选项里点旗子换，这里只是把「只能电脑」的也放开。

**测试客户端**：玩家玩的时候不能在桌面上开窗口（串流里会看到，还抢焦点）。`tools/vcmi_dev_client.py` 把测试客户端开在另一个 Windows 桌面上（`CreateDesktop` + `STARTUPINFO.lpDesktop`）：那个桌面从不显示，窗口不出现在屏幕、串流和任务栏里，也抢不到键盘；静音；进程优先级调低。用的是单独的安装 `<vcmi_build_root>\install-dev`（`cmake --install <build> --prefix ...install-dev`）和用户数据 `<vcmi_build_root>\userdata-dev`（玩家的设置复制进去、改成静音窗口化普通难度，存档、日志、说话桥文件夹都是自己的）。坑：启动它的程序要等客户端的窗口出现在那个桌面上，才能关掉自己手里的桌面句柄，否则桌面当场被系统回收，客户端连日志都来不及写就退出。客户端为测试加了 `--testmap-hotseat Claude`（在 `--testmap` 的基础上再加几个热座玩家，名字逗号分隔，隐含 `--testmap-human`）和 `--testmap-claude blue`（把电脑颜色交给 Claude，和大厅窗口做的一样）；VCMI 自带的 `--autoSkip` 让人类座位的回合自动结束。`vcmi_dev_client.py --menu` 停在主菜单。
- 测点界面：看不见的桌面没有鼠标，从外面 `PostMessage` 过去的点击和按键 SDL 都不收（窗口拿不到焦点）。所以测试客户端（设了 `VCMI_CLAUDE_PLAYER_DEV`）在说话桥的轮询里多看两个文件：`claude-bridge/player/screenshot.request` 出现就截一张图（菜单里也行，存进 `userdata-dev\cache\extracted\screenshots`），`claude-bridge/player/ui.request` 里每行 `click x y` 就往 SDL 事件队列里放一次鼠标点击（窗口坐标，1600×900）。玩家的客户端没设这个环境变量，这两个文件不起作用。

## 验证记录
2026-10-16、17 两天在 Windows 上测过（证据是截图和日志，没放进仓库）：
- **读存档**：官方服务器写出的存档、官方客户端玩战役时的自动存档，读出来的天数、英雄位置、捡掉的资源和宝箱都和游戏里一致。
- **改存档**：加兵、资源、宝物、升级、主属性、技能、法术、魔法以后，读取器复查无误，官方服务器和客户端都能读入。
- **作弊标记**：在官方服务器上真的输入作弊码，存档里 `cheated=true`，服务器的 `!cheaters` 报出作弊者；`--clear-cheated` 改完再载入，回答「没有注册作弊者」。
- **说话桥**：聊天、弹窗、命令三种都在游戏里看到；`from-game.jsonl` 一行一条。Claude 窗口的未读数、`--open`、发言人颜色都对。中转站模式（127.0.0.1）：消息经 HTTP 进来，新存档 6 秒内传到 `remote-saves`。
- **Claude 当本地玩家**：测试客户端在看不见的桌面上开一张四色合作图的热座（红 = 人、`--autoSkip`；蓝 = Claude；另两色 Nullkiller2），Claude 用 `vcmi_claude_player.py` 自己打了四局：
  - 没有窗口在听时，每回合 20 秒后替身接手打完。
  - 捡资源、招兵（可招数量不够时按可招的招）、进城、建市场、占矿、招英雄、读木牌（木牌的字原样进了回答）；六种错误命令都被拒并说明原因，失败那条后面的命令不执行；故意不说话 180 秒后替身接手；打一群怪自动战斗输了，报告双方损失。
  - `say` 的话出现在人类玩家屏幕左下角，Claude 标签亮起未读数；轮到 Claude 时人类那边显示电脑回合，地图跟着 Claude 的英雄走。
- **大厅里的「颜色和 Claude」和换颜色显示**：红显示成紫以后，界面、对话框边框、小地图都是紫色；把蓝交给 Claude 后开局，Claude 照常打；读这一局的自动存档，蓝方还是 Claude 来打。蓝显示成青时，游戏自己的文字也变成「青色玩家占有」。
- **任何颜色都能选**：单人图「野蛮人的攻击」（只有红色人类能选）里点蓝旗，开局是蓝方的界面和据点，读档后不变。
- 测试客户端开着时，人类玩家的桌面上查不到它的任何可见窗口。
- 已知：VCMI 的命令行认不了中文的存档路径，测试读档要先把存档复制到英文路径下。还没测：iOS 真机；人类在热座大厅里手动填 `Claude` 开局（测试走的是同一条名字分配路径）。
