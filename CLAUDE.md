# Evo（项目说明）

Evo：一个轻量、开源的 Apex Legends 战绩记录器，面向 PC 玩家（Steam / EA App）。通过屏幕截图 + OCR 自动记录每局的名次、击杀、伤害和排位分（RP）变化。绑定微信小程序以后，战绩同步到云端（微信云开发），手机上也能看，可以随时解绑。

命名：项目名 Evo（取自游戏里随伤害升级的进化护甲）。名称中不用 "Apex"、"Legends" 等 EA 商标，只在简介中写 "for Apex Legends"。仓库名和 Python 包名用 `evo-tracker`。

## 先读哪些文件

| 什么时候 | 读 |
|---|---|
| 每次开工 | [claude/todo.md](claude/todo.md)（所有待办只放在那里）、[claude/progress.md](claude/progress.md)（已完成的里程碑和版本记录；10-22 及以前的里程碑在 [claude/progress_archive.md](claude/progress_archive.md)，平时不读，要查旧记录时再看） |
| 改模型（识别逻辑、`config/`） | [docs/模型流程.drawio](docs/模型流程.drawio)（实际怎么跑）、[docs/对局全流程图.md](docs/对局全流程图.md)（规则为什么这样定）、[docs/识别经验.md](docs/识别经验.md)（实测结论、游戏画面特征） |
| 改页面 | [docs/面板.md](docs/面板.md) |
| 打包、安装、发布、改托盘或窗口 | [docs/底座.md](docs/底座.md) |
| 做小程序、云同步（`miniprogram/`、Evo 上传战绩） | [docs/小程序.md](docs/小程序.md) |
| 做「派圈助手」（和合作者一起做的小程序，集成 Evo；2026-10-26 起） | 仓库在 **`E:\paiquan-miniprogram`**（私有共享仓库 `AugustHuangMX/paiquan-miniprogram`，用户是拥有者）：先读那里的 `AGENTS.md`、`AI-CONTEXT.md`、`PRODUCT.md`（合作者的规矩：中英双语、深浅主题、不编数据、600 条测试）。合作者给了用户很大的自由度：**首页（地图页）和「我的」页由用户全权负责，结构也可以改；所有页面都可以滑动**（2026-10-27，原来的"一屏出结论"取消了），怎么改问用户、不用等合作者；他 `AGENTS.md` 的 8 条红线照守。在 Evo 这个窗口里直接改那个文件夹，不另开窗口。在开发者工具里看效果、截图：照 `miniprogram/paiquan-preview/README.md` 做（只在本机；用我们的 AppID 和测试云环境复制一份，不碰他的项目和云环境） |
| 给新版本做宣传图（新功能配图） | [release/做图流程.md](release/做图流程.md)：每次都照它做（`python tools/feature_cards.py`），风格不要自己改 |

- 做完一项：从 todo.md 删掉，在 progress.md 记一笔；不要把待办写进本文件
- 改了模型：流程图、对局全流程图.md 要跟着改，三者（代码、流程图、记录）以最新讨论为准

## 用词和分工

| 名称 | 指什么 |
|---|---|
| 模型 | 整套识别逻辑：画面判断、读数、校验、各来源合并、规则配置（`config/`）、测试集。最重要的部分 |
| 引擎 | OCR 本身（RapidOCR + PP-OCRv6 模型文件），模型用到的一个工具 |
| 页面 | Evo 窗口里的面板（`panel/`、`panel_app.py`） |
| 底座 | 托盘、进程、数据库、文件位置、检查更新、打包安装。用户不太关心细节，但**改动也要先问**：问的时候写短一些（改什么、为什么、对用户有什么影响、我的建议），原理不展开 |
| 网站 | 官网和下载（`site/`，只在本机，不进 git） |
| 小程序 | 微信小程序（`miniprogram/app/`）和它的云端（微信云开发的云函数、云数据库，`miniprogram/cloud/`），Evo 里上传战绩的那部分也算 |

## 关于我（开发者）

- 我是编程新手：懂一点 Python。
- 遇到需要我做决定的地方，先问我，不要自己假设
- 项目默认使用 VS Code 进行开发。

## 项目原则（不可违背）

1. **只看不动**：只做被动截屏（DXGI 桌面复制，如 dxcam）。绝不注入游戏、不读写游戏内存、不 hook 渲染、不在游戏画面上绘制浮层、不模拟任何键盘鼠标输入、不注册全局键盘钩子。Apex PC 版用 EA Javelin 反作弊，比之前的 EAC 更敏感。唯一的例外（2026-10-20 用户决定）：读 Apex 自己的视频设置文件 `videoconfig.txt` 里的分辨率和是不是全屏，判断游戏画面是 16:9、16:10 还是 4:3（`game_aspect.py`；只读不改，设置页可以改成手动选）
2. **轻量**：
   - 后台运行内存 < 100 MB，识别时峰值 < 300 MB，空闲时 CPU 接近 0%
   - 不影响游戏帧数（用 CapFrameX / PresentMon 实测平均帧数和 1% low）
   - 不使用 Electron。界面 = 托盘图标 + 按需打开的 Evo 窗口（Windows 自带的 WebView2 显示本地网页面板），窗口在单独的进程里，关掉就把内存还给系统
   - OCR 只用 CPU、限制单线程、低优先级，模型按需加载；交火时不用引擎
   - 读结算画面、大厅时不让 Windows 降频（2026-10-27 用户决定：笔记本玩家的识别慢 10–15 倍，等不到提示音；只在这几秒，游戏在菜单里，优先级不变，对局中照旧由 Windows 决定。见 docs/底座.md）
3. **开源**：AGPL-3.0 许可证（2026-10-24 用户决定，之前是 MIT：防止加速器等公司把代码放进不公开源码的产品，不管放在客户端还是服务器上；代码和提示音都用它，传奇头像等素材另算）。可以借鉴其他项目（如 MaaAssistantArknights）的**设计思路**，但不复制别人的代码：即使许可证允许（MAA 也是 AGPL-3.0），Evo 的代码也要全部是自己的，以后才能单独授权给别人。
4. **数据可信优先**：识别置信度低时，不悄悄写入错误数据，而是保留截图并标记"待确认"，由用户在面板中修正；读不出来的项留空，不当作 0。
5. **配置驱动**：识别规则（区域、模板、阈值）写在配置文件里（`config/`），而不是写死在代码中，以便适应赛季更新和社区贡献。
6. 在调整前端界面的时候（官网、客户端、小程序），永远使用 ui-ux-pro-max 这个 skill。

## 技术栈

- Python 3.13（Windows）：新版 numpy 要求 3.12+，onnxruntime 暂不支持 3.15
- 文件布局：程序代码在 `code/`（互相 import 时直接写模块名，如 `import storage`）；只在开发时用的脚本（打包、安装、发布、伪数据、探测工具、`evo.spec`）在 `tools/`（先 `import code_path` 才能 import 程序代码）；测试在 `tests/`；根目录只放文档和文件夹；素材放 `assets/`。运行命令都带上文件夹，如 `python code/evo_app.py`、`python tools/build.py`
- 截屏 `dxcam`（备选 `mss`）；图像 `opencv-python-headless` + `numpy`（不要装 `opencv-python`，会冲突）；引擎 RapidOCR + onnxruntime（模型内置、单线程）；存储 SQLite；面板 Flask + 纯 HTML/CSS；窗口 `pywebview` + WebView2；托盘 `pystray`；打包 PyInstaller（一个文件夹）。细节见 docs/底座.md
- 小程序：微信原生写法（JavaScript + WXML / WXSS）+ 微信云开发（云函数用 Node.js），用微信开发者工具调试。细节见 docs/小程序.md
- 打游戏用安装好的 Evo：程序在 `E:\Evo Tracker`，数据在 `E:\Evo Tracker Data`。用源码运行 `python code/evo_app.py` 时战绩也记进这个数据文件夹，所以源码版和安装版不能同时开；Evo 开着时不能重新安装
- 下载和一键更新（2026-10-24 用户决定）：**一键更新只下载变了的文件**（增量更新：和玩家现在的版本比，只下变了的那些，0.3.7 → 0.3.8 是 11 MB，完整包是 112 MB），以后每次发布都这样；完整压缩包只给第一次安装和太旧的版本。**国内玩家多，默认走腾讯云 COS（上海）**（2026-10-28 用户决定）：官网下载区「国内下载」（COS）和「海外下载」（dl.evotracker.cn）两个一样大的按钮并排，首屏的下载按钮是国内下载；一键更新（0.4.3 起）先走 COS，太慢或出错再换 dl.evotracker.cn，检查更新也先读 COS 上的 latest.json。gh-proxy 用户决定暂时不用。细节见 docs/底座.md，剩下没做的见 todo.md

## 模型（怎么识别、记录）

1. 托盘程序每 5 秒看一次进程列表，有 Apex（`r5apex.exe` / `r5apex_dx12.exe`）就启动记录子进程（`live_collect.collect()`）
2. 每次只截一张全屏，先判断是哪个画面（`screens.py`，先比外观、像了才用引擎读关键字）：总结页、经验值页、开局画面、大厅、淘汰画面、冠军横幅、返回大厅按钮。都不是时，看左下角认不认得出自己的传奇（`in_match.py`）
3. 对局中（每 0.3 秒一张）只比外观：认自己和队友的传奇、倒地、成盒观战、右下角手上的武器（`weapons.py`）；右上角那一小块存在内存里，一局结束以后才读
4. 结算画面上读名次、击杀、伤害、RP 等（停在总结页、还没存下时只读总结页）；对局中最后的右上角等回到大厅或下一局开局时才读；回到大厅读段位和分数
5. 各来源的读数都记进 `readings` 表，按优先级合并（`post_match.settle`），对不上标待确认；每次回大厅用总分校准各局 RP（`calibration.py`）

## 数据（`code/storage.py`，各列的说明在里面）

- `matches`：每局一行（名次、击杀/助攻/击倒、老板分、伤害、RP、段位、待确认……）
- `teammates`：每局最多两名队友（名字和传奇以对局中为准，ID 和数据来自总结页）
- `readings`：每局 × 每项 × 每个来源一行，不只记最后采用的那个
- `lobby_readings`：每次回大厅读到的段位和总分
- `weapon_holds`：每局 × 每一段拿在手上的武器（从这局第一次拿到武器算起的秒数）
- `player_names`：玩家名 → 账号（按名字区分账号，改名后合并）
- 数据库升级：`SCHEMA_VERSION` + `MIGRATIONS`，只加列、加表

RP：游戏里没有单一的"总 RP"，只有"段位 + 段内进度"。**总分以大厅为准**（打完一局通常不回大厅，所以每次回大厅都要抓住，用来校准）。每局 RP 的来源：经验值页明细 → 对局中右上角最后的 RP（只限自己最后被淘汰；先成盒、拿冠军时不是最终值）→ 前后两次大厅总分之差（只缺一局时补上）。

## 测试

- 运行：`python tests/run_all.py`（约 5 分钟，20 组）。改了模型、记录程序或 `config/` 之后都要跑，新赛季更新后也要跑一遍
- 做法：`tests/fake_game.py` 用"假摄像头"把真实截图按顺序喂给记录程序，数据写进临时文件夹（不碰正式数据），时钟是假的。正确答案写在 `tests/` 里的 CSV 和测试文件里
- 测试只 import 项目代码，项目代码不能 import 测试（否则 PyInstaller 会把测试打进安装包）

## 协作约定

- 每完成一个能运行的小功能就提交一次，提交信息用中文简要说明
- 改动前先说明计划，改动后说明如何验证
- 新增依赖前先说明理由和对内存 / 体积的影响
- 不要删除或覆盖我的截图样本
- 不提交到仓库：`captures/`（测试截图）、`data/`、数据库文件、`site/`；小程序的 AppSecret、云开发的密钥（也不写进 Evo）；别的玩家的名字和 ID（队友的测试答案只在本机：`tests/replay_20261016_teammates.csv`、`tests/in_match_teammates.json`）
- **微信小程序的内容不上传 Evo 的 GitHub 仓库**（2026-10-24 用户决定；2026-10-26 调整：和合作者一起做的小程序「派圈助手」在私有共享仓库 `AugustHuangMX/paiquan-miniprogram` 里，集成 Evo 的代码放那里没问题）：`miniprogram/`、`docs/小程序.md`，以及 Evo 里绑定、上传战绩的 `code/cloud_sync.py`、`code/panel_cloud.py`、`code/qr.py`、`panel/templates/_cloud.html`、`tests/test_cloud_sync.py`、`tools/cloud_ping.py`（都写在 `.gitignore` 里，只在本机；之前的提交在本机分支 `local/miniprogram-history`，不推送）。GitHub 上的 Evo 没有这些文件也要能运行（找不到就不显示那一栏、不同步）；小程序上线前也不打进安装包（`tools/evo.spec` 的 `WITH_CLOUD`）。别的文档里写到小程序照常上传。推送前先查 `git log origin/main..main -- <这些路径>` 为空
