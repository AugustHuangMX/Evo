# 数据展示优化与上游提交择取（2026-11-03）

> 后续界面修订：按用户反馈清除了演示数据库、测试 CSV、旧预览截图及生成脚本；本地预览改用正式战绩的只读快照。分析区重新设计为跟随 Evo 深浅主题的独立面板，使用“表现走势 / 分组比较 / 伤害与击杀”视图切换、紧凑筛选栏、折叠日期条件和分类明细。后续验证为数据页相关三组回归通过，以及 21 项真实浏览器检查通过；检查了 1440 / 1024 / 390px 布局和两种主题。旧版固定白色分析区及旧演示截图已失效，下文保留首次实现和上游审查的历史记录。

## 范围与结果

- 当前分支：`feat/webdav-sync`；开始时 HEAD 为 `981b88b`，工作区干净。
- 原仓库：<https://github.com/AugustHuangMX/Evo>，本地 `origin` 指向该地址。
- 本次获取的上游快照：`c953344e90ba5d74c3bfeb7e9a948c6076c7b1d8`。
- 共同祖先：`457a1598258a4698f1c773b01f8811d4a769c899`；上游独有提交共 89 个。
- 按历史和文件范围筛选候选，再阅读候选补丁、相关依赖和测试；择取了 9 个源提交的相关改动。没有整体合并上游 main，也没有替换本分支的 WebDAV、统计全局去重或数据库迁移。

## 已择取的上游改进

| 源提交 | 实际引入的内容 | 适配与边界 |
| --- | --- | --- |
| [0b43542](https://github.com/AugustHuangMX/Evo/commit/0b43542) | 打包版启动时清除随下载带来的指定运行库 DLL 区域标记，修复 WebView2 窗口打不开 | 只取启动修复及测试，不改变版本号和发行文件 |
| [3ebe772](https://github.com/AugustHuangMX/Evo/commit/3ebe772) | 重开上一局经验值页时避免重复记一局 | 保留“下一局恰好读数相同仍正常记录”的判定 |
| [10582d4](https://github.com/AugustHuangMX/Evo/commit/10582d4) | 结算 / 大厅识别阶段的 Windows 降频控制、识别耗时统计 | 沿用按需释放的生命周期，保留本分支设置与同步代码 |
| [eb072f9](https://github.com/AugustHuangMX/Evo/commit/eb072f9) | 总结页阶段优先处理总结页，推迟右上角读取 | 取必要的识别流程与测试，不导入上游工作笔记 |
| [b71a177](https://github.com/AugustHuangMX/Evo/commit/b71a177) | 全体玩家武器汇总展示、自己武器占比的本地对照 | **部分引入**：没有导入武器上传、Worker 或远端数据库迁移；旧汇总没有武器数据时显示空状态 |
| [0fdd67b](https://github.com/AugustHuangMX/Evo/commit/0fdd67b) | 我的 / 全体玩家：两列预览卡片、每张最多 5 行、完整表格与返回入口 | 进一步补上模式、地图、传奇和日期条件在卡片 / 明细 / 范围之间的保留 |
| [42752fa](https://github.com/AugustHuangMX/Evo/commit/42752fa) | 队友 RP 小曲线、完整页多序列曲线、悬停与键盘操作 | 额外修正：排除非排位 RP，参照线纳入纵轴；合并冲突中未引入依赖缺失的雷达图、公告、地图或赛季界面 |
| [141ad6c](https://github.com/AugustHuangMX/Evo/commit/141ad6c) | 手动修正对局模式和地图 | **部分引入**：没有更改截图归档规则；修正字段在本地留痕，通过 WebDAV 正常同步 |
| [ece56fb](https://github.com/AugustHuangMX/Evo/commit/ece56fb) | 模式 / 地图校验与不在当前配置名单内的历史值保留 | **部分引入**：未导入整套截图与动画变更；匿名上报仍只发旧服务端认识的 `edited` 字段名，避免旧服务端拒收 |

上述为代码择取移植，不是将这些源提交的版本号、发布记录及所有文件原样 cherry-pick。

## 本分支新增的本地交互分析

入口为“数据 → 我的”；总览的“深入分析”会带入当前范围、模式与传奇条件。

- 模式、地图、传奇、开始日期、结束日期可叠加；先筛选，再取近 30 局或全部，卡片与明细使用同一批对局。
- 七个可切换指标：伤害、击杀、助攻、击倒、名次、生存秒数、RP；逐局趋势或按自然日均值。
- 传奇 / 地图 / 模式的分组比较；可搜索、排序，显示均值的有效分母，以及胜率和前五率。
- 名次分布、伤害与击杀散点图；鼠标及键盘选点，跳转到对应历史对局的正确分页。
- 当前筛选结果 CSV 导出；缺失值留空，文本字段处理公式注入字符。
- 完整表格增加名称搜索及列排序；参照合计不参与排序，缺失值排在最后。
- 本地 SVG 与原生 JavaScript，无 CDN、图表库或新增运行依赖；窄屏重算坐标系，保留可读坐标文字。
- 缺失值不作零；经验值页计分次数不作实际击杀；图表 RP 只使用排位局。待确认局仍纳入本地展示，并明确标出。
- 超过 600 个有效散点时等距抽样绘点，提示抽样数量；统计与导出使用全部筛选结果。

## 暂不引入的有用功能

这些功能有价值，但存在需要单独处理的依赖，不列作“无用”。

| 功能 / 提交 | 暂缓原因 |
| --- | --- |
| 六维雷达与同段位玩家对比 `0a4a8ac` | 依赖扩展后的统计汇总及其人口基准，受匿名统计开关控制；本次本地分析独立可用，没有引入缺少所需字段的对比图 |
| 历史赛季录入 `52ff2a3` / `cadf3a2` / `842dfec` / `b7ece84` | 上游以数据库版本 9 引入 `season_stats`，本分支已有 WebDAV 的 9/10 版迁移；需重新编号迁移并定义赛季数据的同步、冲突和删除语义，不能直接照搬 |
| 后续识别缓存及采集调度 `631c2ee` / `5c0d9b4` / `d96e5c3` | 同时改变识别缓存、配置和采集顺序；本机缺少相应原始截图样本，先不扩大到这组更深入的识别改造 |
| 地图轮换 `bad597a` 及相关服务提交 | 需要新增在线地图服务及缓存 / 定时任务链，与本次本地战绩分析独立 |
| 开发者公告 `5d9fb35` | 新增开发者向客户端发布内容的渠道；不是个人战绩展示的必要功能 |
| 测试工具重构 `ac3d6a8` / `5fa2308` / `ce5c651` | 有助开发者采样，但引入较大工具与样本流程变更；本次保留已有诊断工具 |
| 汇总定时降频 `c43ec70` | 面向上游部署的 D1 配额和地图定时任务；没有修改本分支的部署配置 |
| 国内下载链路 `b9e4453` / `4ade2bc` | 后续历史记录还包含 COS 被封与撤回线路，不能孤立导入早期默认下载改动 |

## 未引入的推广与无关内容

合作小程序入口、图标、小程序码、绑定打包开关、开发者收款弹窗、关注 / 宣传布局、宣传图、网站文案、上游发布号与发布记录，以及上游个人工作笔记，均没有随本次择取引入。已有推广区域没有作为这次新增功能扩展。

## 验证与实际交付边界

- 新增 `tests/test_analytics.py`：21 项，覆盖组合筛选、日期、账号边界、缺失值、分页定位、模式 / 地图修正、旧协议和参照线边界；修正模式后，非排位的历史 RP 不纳入图表、表格或总览合计。
- 额外独立执行无需截图的经验值页规则：重开上一局不重复记、下一局读数相同仍记录、对局读数延后交来仍记录，三项通过；避免把截图组的跳过误当作去重逻辑验证。
- 完整回归：35 组中 27 组通过、8 组因缺少原作者截图样本而跳过；截图缺失的部分没有被当作已验证。
- 检查过程中模拟 WebDAV 的 401、500 错误提示断言出现过间歇失败。测试伪服务在早期错误响应前没有消费 HTTP/1.1 请求体，剩余 XML 会被解析为下一条请求，可能导致 Windows 提前重置连接。修正测试夹具在 401 / 注入错误前读取请求体，并将具体错误文字纳入失败提示；连续 8 轮本地错误响应检查通过。生产 WebDAV 实现未改动，原始失败日志保留。
- 真实 Edge 验证：七指标切换、逐局 / 按日、比较维度、分组搜索排序、趋势及散点键盘选点、CSV 下载、叠加筛选、明细条件保留、完整表格搜索排序、空结果、历史对局分页和队友曲线提示。
- 实际下载的叠加筛选 CSV 经 Python 读取核对：恰好 7 局，全部为排位、世界尽头；浏览器控制台没有错误或警告。
- 1440px 桌面与 390px 窄屏，深 / 浅主题均截图查看；窄屏页面没有横向溢出，宽表格在自身容器内滚动。
- 新增静态资产沿用现有打包规则（整个 `panel/static`、`panel/templates` 已包含）；Python 模块由静态 import 引入。未新增运行依赖。
- 验证资料放在工作区旁的 `validation-20261103/`，不纳入代码提交：回归日志、演示数据库、浏览器检查脚本、CSV 和截图。
- 没有修改正式战绩、替换已安装 EXE、变更正式版本号、推送远端、部署 Worker 或执行生产 D1 迁移。本次交付为本地分支源码及独立演示预览。

## 上游 89 个独有提交的筛选清单

以下逐条记录历史筛选结果。“暂缓 / 不引入”不代表每一项都做了完整运行验证；已采用候选的代码、依赖与验证见上文。

| 提交 | 上游标题 | 本次处理 |
| --- | --- | --- |
| [ce60cfd](https://github.com/AugustHuangMX/Evo/commit/ce60cfd) | todo：小程序名字（EVO Tracker）、传奇头像和段位徽章已改好（用户定的）；用微信扫一扫直接绑定要域名备案，在试定时云函数生成小程序码 | 不引入：合作、推广或关联工作记录 |
| [d1c42e4](https://github.com/AugustHuangMX/Evo/commit/d1c42e4) | 项目瘦身：删掉已经跑过的一次性文件（backfill_teammates.py、apex_capture_test.py、add_mate_stats.sql）和没人用的几处代码；改过时的说法（todo 的"发 0.3.7 时"、legends.json 的命令路径）；安装包瘦身记进 todo | 不引入：与本次功能无关的清理，避免删本地工具 |
| [7949ef2](https://github.com/AugustHuangMX/Evo/commit/7949ef2) | progress.md 瘦身：10-16 到 10-22 的里程碑原样移到 progress_archive.md（177 KB → 70 KB，平时开工不用读），记一笔 10-25 项目瘦身；CLAUDE.md 写上存档的位置，备用线路去掉 gh-proxy（用户决定暂时不用） | 不引入：上游协作、文档或非本次功能记录 |
| [16d55d9](https://github.com/AugustHuangMX/Evo/commit/16d55d9) | 0.4.0 宣传图（9 张：4 张新功能 + 5 张特色功能，用户定的）：宣传图.json 改成 0.4.0；做图工具加 label（小标签只写版本号）、_shot=lang，演示数据加武器、全体玩家汇总（不联网）、三个队友、冠军局；做图流程、progress 跟着改 | 不引入：上游发布流程及版本记录 |
| [a5936af](https://github.com/AugustHuangMX/Evo/commit/a5936af) | todo：小程序的用户隐私保护指引现在不用填（扫码不在要声明的清单里） | 不引入：合作、推广或关联工作记录 |
| [df95de1](https://github.com/AugustHuangMX/Evo/commit/df95de1) | 0.4.0 宣传图按用户意见改：武器识别、全体玩家（传奇热度 + 底部小字）、上分好搭子只放截图，分享战绩三种卡片并排，游戏语言改标题；做图工具加「只放截图」「几张并排」两种排版和底部小字、_shot=share2，截图时也关掉动画（上分趋势丢了曲线）；做图流程跟着改 | 不引入：宣传素材与工具 |
| [9921092](https://github.com/AugustHuangMX/Evo/commit/9921092) | 设置页"微信小程序"：绑定对话框里也能放小程序码图片（码池有码时用微信扫一扫直接绑定） | 不引入：合作、推广或关联工作记录 |
| [5b25201](https://github.com/AugustHuangMX/Evo/commit/5b25201) | 小程序：用微信扫一扫直接绑定（定时云函数预先生成小程序码）、名字 EVO Tracker、传奇头像和段位徽章（todo、progress；小程序的文件只在本机） | 不引入：合作、推广或关联工作记录 |
| [0b43542](https://github.com/AugustHuangMX/Evo/commit/0b43542) | 修复 Evo 窗口打不开、每次用浏览器（0.4.1）：用 Windows 自带的解压缩解开时 DLL 带着"从网上下载的"标记，.NET 不加载；打包版启动时去掉 pythonnet、clr_loader、webview 里 DLL 的标记（webview2.unblock）。版本号 0.4.1、更新内容只有这一条；测试加 6 项，打包实测窗口能打开；bugs_test/ 不提交 | 择取：窗口启动修复 |
| [5331192](https://github.com/AugustHuangMX/Evo/commit/5331192) | todo：我们自己的小程序 EVO Tracker 暂时搁置，和合作者一起做他的小程序「派圈助手」（用户决定）；集成前等用户定的几件事 | 不引入：合作、推广或关联工作记录 |
| [0a226e5](https://github.com/AugustHuangMX/Evo/commit/0a226e5) | CLAUDE.md：小程序内容不进 Evo 的 GitHub 仓库；和合作者做的「派圈助手」在私有共享仓库里，集成代码放那里（用户 10-26 调整） | 不引入：合作、推广或关联工作记录 |
| [b34b050](https://github.com/AugustHuangMX/Evo/commit/b34b050) | CLAUDE.md：写明派圈助手（和合作者做的小程序）的仓库在 E:\paiquan-miniprogram，先读那里的 AGENTS.md 等，在 Evo 窗口里直接改 | 不引入：合作、推广或关联工作记录 |
| [72399df](https://github.com/AugustHuangMX/Evo/commit/72399df) | todo、progress：派圈助手接入 Evo 战绩的页面和云函数做完（在合作者仓库的 feat/evo-stats 分支，没推送），记下剩下要做的几步 | 不引入：合作、推广或关联工作记录 |
| [1a5d5e2](https://github.com/AugustHuangMX/Evo/commit/1a5d5e2) | todo、progress：派圈助手的 feat/evo-stats 分支已推到共享仓库，等合作者看过合回 main | 不引入：合作、推广或关联工作记录 |
| [3ebe772](https://github.com/AugustHuangMX/Evo/commit/3ebe772) | 修复在大厅里重新打开上一局的经验值页会多记一局（0.4.1）：PostMatch 记住上一次用过的经验值页读数，这一轮没有别的数据、读到的都一样时不再记；测试加 2 项，对局全流程图、模型流程图、更新内容跟着改 | 择取：经验值页去重 |
| [3641a7d](https://github.com/AugustHuangMX/Evo/commit/3641a7d) | todo：派圈助手集成停在哪一步（云函数已部署到他的环境、权限已改；等合作者开匿名登录和网关策略），剩下的按顺序列好 | 不引入：合作、推广或关联工作记录 |
| [2e1434f](https://github.com/AugustHuangMX/Evo/commit/2e1434f) | 记录：发布 0.4.1（R2、官网、latest.json 已上线；腾讯云 COS 欠费没传，latest.json 不带备用线路）；todo 加国内站新存储桶 | 不引入：网站、线路或部署记录 |
| [12bcb65](https://github.com/AugustHuangMX/Evo/commit/12bcb65) | todo：派圈助手的云环境 10-24 套餐到期被隔离，要合作者续费才能继续接入 | 不引入：合作、推广或关联工作记录 |
| [c8d1c70](https://github.com/AugustHuangMX/Evo/commit/c8d1c70) | CLAUDE.md、todo：派圈助手的界面在开发者工具里怎么看效果（miniprogram/paiquan-preview，只在本机），测试环境等界面改完再收拾 | 不引入：合作、推广或关联工作记录 |
| [cb4dd46](https://github.com/AugustHuangMX/Evo/commit/cb4dd46) | CLAUDE.md、todo：派圈助手的首页和「我的」页由用户全权负责、结构可以改（合作者同意），todo 加重构这两页；第 6 条 ui-ux-pro-max 扩到客户端和小程序 | 不引入：合作、推广或关联工作记录 |
| [a420017](https://github.com/AugustHuangMX/Evo/commit/a420017) | CLAUDE.md、todo：派圈助手的首页和「我的」页可以滑动，不用一屏出结论 | 不引入：合作、推广或关联工作记录 |
| [bf8f726](https://github.com/AugustHuangMX/Evo/commit/bf8f726) | CLAUDE.md、todo：派圈助手所有页面都可以滑动（取消"一屏出结论"） | 不引入：合作、推广或关联工作记录 |
| [1c626d7](https://github.com/AugustHuangMX/Evo/commit/1c626d7) | todo、progress：派圈助手首页改版做完（分支 ui/maps-me，等用户看截图）；剩下「我的」页、常用武器、隐私说明等列进 todo | 不引入：合作、推广或关联工作记录 |
| [4f61295](https://github.com/AugustHuangMX/Evo/commit/4f61295) | todo：派圈助手首页换了图标、挪了广告；tab 图标破图在开发者工具里查过的几样，剩真机看 | 不引入：合作、推广或关联工作记录 |
| [96a02be](https://github.com/AugustHuangMX/Evo/commit/96a02be) | todo、progress：派圈助手底部 tab 图标破图只是开发者工具的问题，真机正常（用户看过） | 不引入：合作、推广或关联工作记录 |
| [10582d4](https://github.com/AugustHuangMX/Evo/commit/10582d4) | 读结算画面、大厅时不让 Windows 降频；日志记识别平均每次几秒 | 择取：识别性能 |
| [eb072f9](https://github.com/AugustHuangMX/Evo/commit/eb072f9) | 停在总结页时只读总结页：右上角回到大厅、下一局开局时才读，经验值页等总结页存下再读 | 择取：总结页调度 |
| [b71a177](https://github.com/AugustHuangMX/Evo/commit/b71a177) | 匿名统计加上武器，数据页"全体玩家"加"大家常用的武器" | 部分：武器汇总展示，不扩展上传 |
| [e51c59e](https://github.com/AugustHuangMX/Evo/commit/e51c59e) | todo、更新内容：派圈助手合回 main 并提审（等审核）；0.5.0 的更新内容用户确认过，等小程序审核通过再发 | 不引入：合作、推广或关联工作记录 |
| [0f4ec84](https://github.com/AugustHuangMX/Evo/commit/0f4ec84) | 0.4.2：版本号、更新内容（大家常用的武器、总结页读得更快、笔记本上识别更快，用户确认过） | 不引入：上游发布流程及版本记录 |
| [7590a62](https://github.com/AugustHuangMX/Evo/commit/7590a62) | 记录：发布 0.4.2（统计服务器先升级加武器列；R2、官网、latest.json 已上线；COS 欠费照旧跳过） | 不引入：网站、线路或部署记录 |
| [b9e4453](https://github.com/AugustHuangMX/Evo/commit/b9e4453) | 腾讯云 COS 改成默认下载：一键更新先走国内线路，官网改成国内下载、海外下载两个按钮 | 不引入：不能忽略后续下载线路撤回 |
| [049865a](https://github.com/AugustHuangMX/Evo/commit/049865a) | 文档按主题整理：progress.md 只留版本记录，todo.md 只放还没做的 | 不引入：上游协作、文档或非本次功能记录 |
| [4ade2bc](https://github.com/AugustHuangMX/Evo/commit/4ade2bc) | 国内线路换成国内站账号的新存储桶 evo-dl-1312774356（上海）：cos.py 试传通过，0.4.2 安装包已传上并核对 | 不引入：上游特定存储桶配置 |
| [9ea752c](https://github.com/AugustHuangMX/Evo/commit/9ea752c) | 官网国内下载上线：两个国内链接换成新存储桶，官网和隐私页已上传 | 不引入：网站、线路或部署记录 |
| [bac85a2](https://github.com/AugustHuangMX/Evo/commit/bac85a2) | 国内的旧版（0.3.3–0.4.2）一键更新也走 COS：Cloudflare 转发规则 old-evo-cn-to-cos | 不引入：网站、线路或部署记录 |
| [e9b17b1](https://github.com/AugustHuangMX/Evo/commit/e9b17b1) | 官网下载区删掉按钮下面那句"两个是同一个文件……一个慢就换另一个试试" | 不引入：网站、线路或部署记录 |
| [6f2eca0](https://github.com/AugustHuangMX/Evo/commit/6f2eca0) | 官网常见问题：语言、模式、分辨率那一条按用户给的原文改 | 不引入：网站、线路或部署记录 |
| [0fdd67b](https://github.com/AugustHuangMX/Evo/commit/0fdd67b) | 数据页改成两列卡片：每张先列 5 行，点卡片打开完整的表格（"我的"和"全体玩家"都是） | 择取：卡片与完整表格 |
| [0a4a8ac](https://github.com/AugustHuangMX/Evo/commit/0a4a8ac) | 六维图：总览页、数据页"我的"和同分段的 Evo 玩家比（伤害、击杀、助攻、击杀参与、英雄池、生存） | 暂缓：统计汇总依赖，先提供本地分析 |
| [dbb5ea2](https://github.com/AugustHuangMX/Evo/commit/dbb5ea2) | 待办：测试几组同时跑（用户定的做法，实测 163 秒）、设置文件偶尔读到空的（底座） | 不引入：上游协作、文档或非本次功能记录 |
| [fb6b39d](https://github.com/AugustHuangMX/Evo/commit/fb6b39d) | 待办：六维图随 0.4.3 发（统计服务器已部署，隐私页跟发版一起传） | 不引入：网站、线路或部署记录 |
| [e4460cb](https://github.com/AugustHuangMX/Evo/commit/e4460cb) | 总览页右上角"Evo 官方合作小程序：派圈助手"：点了弹出小程序码，用手机微信扫 | 不引入：合作、推广或关联工作记录 |
| [df41719](https://github.com/AugustHuangMX/Evo/commit/df41719) | 待办：0.4.3 前放好派圈助手的小程序码图片（没有它总览页右上角的按钮不显示） | 不引入：合作、推广或关联工作记录 |
| [57f53b7](https://github.com/AugustHuangMX/Evo/commit/57f53b7) | 派圈助手按钮：放上小程序码，按钮前面换成派圈助手的图标 | 不引入：合作、推广或关联工作记录 |
| [2f67b24](https://github.com/AugustHuangMX/Evo/commit/2f67b24) | 派圈助手的弹窗去掉两句话：介绍（地图轮换和倒计时……）和"扫上面的码"（用户要求） | 不引入：合作、推广或关联工作记录 |
| [fd55f45](https://github.com/AugustHuangMX/Evo/commit/fd55f45) | 派圈助手按钮改成每一页都有（用户觉得只在总览页有点突兀） | 不引入：合作、推广或关联工作记录 |
| [61bb433](https://github.com/AugustHuangMX/Evo/commit/61bb433) | todo：下一个版本发完以后拆开 panel_app.py | 不引入：上游协作、文档或非本次功能记录 |
| [987035a](https://github.com/AugustHuangMX/Evo/commit/987035a) | CLAUDE.md：同时开几个窗口时，提交前先看暂存区和每个文件的 diff | 不引入：上游协作、文档或非本次功能记录 |
| [bad597a](https://github.com/AugustHuangMX/Evo/commit/bad597a) | 总览页加"当前地图"：匹配、排位、轮换模式现在的地图、倒计时、下一张（用户点了"显示"才联网） | 暂缓：需独立在线地图服务 |
| [6ee3b34](https://github.com/AugustHuangMX/Evo/commit/6ee3b34) | 总览页"关注我"挪到左边一列"最近对局"下面，并加上"Evo 官网"链接 | 不引入：合作、推广或关联工作记录 |
| [2614be8](https://github.com/AugustHuangMX/Evo/commit/2614be8) | 设置页"关于 Evo"最下面加"支持我"：连着三个弹窗，最后一个是开发者的微信收款码 | 不引入：合作、推广或关联工作记录 |
| [720f577](https://github.com/AugustHuangMX/Evo/commit/720f577) | 统计服务器部署了地图轮换（GET /v1/maps），用真数据核对过 | 不引入：上游协作、文档或非本次功能记录 |
| [628ef13](https://github.com/AugustHuangMX/Evo/commit/628ef13) | 地图轮换改成只有定时任务去上游取：玩家请求只读数据库里存好的，不再撞上游的限额 | 不引入：上游协作、文档或非本次功能记录 |
| [dad643c](https://github.com/AugustHuangMX/Evo/commit/dad643c) | 0.4.3：版本号、更新内容（用户确认过的原文：当前地图、六维数据图、数据页风格改善、一键更新默认走国内） | 不引入：网站、线路或部署记录 |
| [52cfd4f](https://github.com/AugustHuangMX/Evo/commit/52cfd4f) | 记录：官网隐私页加了"当前地图"那段（中英文，和 PRIVACY.md 一样，随 0.4.3 上传） | 不引入：网站、线路或部署记录 |
| [7d175af](https://github.com/AugustHuangMX/Evo/commit/7d175af) | 设置页"总览页显示当前地图"的说明："会联网"改成"要联网" | 不引入：上游协作、文档或非本次功能记录 |
| [65dd231](https://github.com/AugustHuangMX/Evo/commit/65dd231) | 记录：发布 0.4.3（当前地图、六维图、数据页两列卡片、派圈助手按钮、一键更新先走国内；R2、COS、官网、latest.json 已上线） | 不引入：合作、推广或关联工作记录 |
| [ad469ce](https://github.com/AugustHuangMX/Evo/commit/ad469ce) | 总览页的当前地图：去掉右上角"数据来自 Apex Legends Status ↗"的链接（用户要求，0.4.4 热更新） | 不引入：上游发布流程及版本记录 |
| [c43ec70](https://github.com/AugustHuangMX/Evo/commit/c43ec70) | 全体玩家的汇总从每小时算一次改成每 6 小时算一次：D1 一天读了 380 万行，快到免费额度（500 万） | 暂缓：上游部署配额与定时策略 |
| [31d50ca](https://github.com/AugustHuangMX/Evo/commit/31d50ca) | 记录：0.4.3 换了包（去掉当前地图的来源链接，用户要求不发 0.4.4），新的 SHA-256 0b81bfc3… | 不引入：上游协作、文档或非本次功能记录 |
| [b17b4ea](https://github.com/AugustHuangMX/Evo/commit/b17b4ea) | 0.5.0：安装包里打开绑定微信（WITH_CLOUD = True）、版本号、更新内容、隐私政策写同步到派圈助手 | 不引入：合作、推广或关联工作记录 |
| [ec222b0](https://github.com/AugustHuangMX/Evo/commit/ec222b0) | 补丁包以后只做上一版的一个（release.py 的 PATCH_FROM 从 3 改成 1） | 不引入：上游发布流程及版本记录 |
| [cdcd02a](https://github.com/AugustHuangMX/Evo/commit/cdcd02a) | 记录：发布 0.5.0（绑定微信小程序派圈助手；R2、COS、官网、隐私页、latest.json 已上线） | 不引入：合作、推广或关联工作记录 |
| [dcf5de1](https://github.com/AugustHuangMX/Evo/commit/dcf5de1) | 记录：腾讯云 COS 存储桶被封（451 content illegal），官网国内下载改成弹提示框 | 不引入：网站、线路或部署记录 |
| [dc09418](https://github.com/AugustHuangMX/Evo/commit/dc09418) | 记录：COS 被封，Cloudflare 转发规则 old-evo-cn-to-cos 已关掉 | 不引入：网站、线路或部署记录 |
| [631c2ee](https://github.com/AugustHuangMX/Evo/commit/631c2ee) | 认画面：停在同一个画面上、关键字那一块没变就不再读关键字 | 暂缓：深入识别缓存改造，缺原始样本 |
| [5c0d9b4](https://github.com/AugustHuangMX/Evo/commit/5c0d9b4) | 总结页：和上一张比没变的项不再识别，沿用上次读到的 | 暂缓：深入识别缓存改造，缺原始样本 |
| [d96e5c3](https://github.com/AugustHuangMX/Evo/commit/d96e5c3) | 结算画面上只读自己的数据：队友、名字回到大厅再读，经验值页每 0.3 秒一张 | 暂缓：深入采集顺序改造，缺原始样本 |
| [52ff2a3](https://github.com/AugustHuangMX/Evo/commit/52ff2a3) | 游戏里的数据页：认出这一页、读选中的赛季和生涯合计、数字互相核对（录入历史赛季数据第 1 步） | 暂缓：赛季录入依赖链 |
| [cadf3a2](https://github.com/AugustHuangMX/Evo/commit/cadf3a2) | 录入历史赛季数据：新表和录入模式，记录程序认出数据页就读、存成待核对（第 2 步） | 暂缓：数据库迁移版本与 WebDAV 冲突 |
| [842dfec](https://github.com/AugustHuangMX/Evo/commit/842dfec) | "赛季"页面：录入按钮、核对、保存、历史赛季表；设置页"识别"一节加录入按钮（录入历史赛季数据第 3 步） | 暂缓：赛季录入及同步语义 |
| [b7ece84](https://github.com/AugustHuangMX/Evo/commit/b7ece84) | 数据页：坐标改成按游戏的 16:10 换算（之前在压扁的截图上量，实际打游戏时一张都认不出） | 暂缓：赛季识别坐标依赖 |
| [4ae10da](https://github.com/AugustHuangMX/Evo/commit/4ae10da) | 开发工具 tools/demo_seasons.py：用假数据开面板看"赛季"页面，不碰正式数据 | 不引入：上游协作、文档或非本次功能记录 |
| [5d9fb35](https://github.com/AugustHuangMX/Evo/commit/5d9fb35) | 开发者公告：Evo 窗口顶部显示开发者发的公告，重要的再用托盘通知提醒一次 | 不引入：开发者公告渠道 |
| [42752fa](https://github.com/AugustHuangMX/Evo/commit/42752fa) | 数据页：和队友一起的 RP 走势（每行小曲线 + 完整表格页上的大图） | 择取：队友 RP 曲线 |
| [88d02aa](https://github.com/AugustHuangMX/Evo/commit/88d02aa) | 发布 0.5.1 的准备：版本号、更新内容、公告的隐私说明；发版跳过 COS，加传 GitHub Releases | 不引入：网站、线路或部署记录 |
| [2cf843c](https://github.com/AugustHuangMX/Evo/commit/2cf843c) | 记录：发布 0.5.1（R2、官网、隐私页、latest.json 已上线，COS 跳过；第一次传 GitHub Releases） | 不引入：网站、线路或部署记录 |
| [141ad6c](https://github.com/AugustHuangMX/Evo/commit/141ad6c) | 增加手动选择并优化截图与页面体验 | 部分：模式与地图修正 |
| [006a9d4](https://github.com/AugustHuangMX/Evo/commit/006a9d4) | todo：记下派圈助手「比赛」页加选手页（Liquipedia）的决定和进度 | 不引入：合作、推广或关联工作记录 |
| [ece56fb](https://github.com/AugustHuangMX/Evo/commit/ece56fb) | 修好 PR #3（手动改模式和地图、截图按画面分文件夹）里的问题，页面防抖换成不关动效的做法 | 部分：修正校验和历史值保留 |
| [b3e1184](https://github.com/AugustHuangMX/Evo/commit/b3e1184) | 合并 PR #3（EvilScho1ar）：战绩页手动改模式和地图、截图按画面类型分文件夹、页面防抖 | 不引入：上游协作、文档或非本次功能记录 |
| [ac3d6a8](https://github.com/AugustHuangMX/Evo/commit/ac3d6a8) | 测试工具第 1 步：诊断工具拆成三个文件、改名 Evo 测试工具（功能不变） | 暂缓：测试工具大改 |
| [cf669a5](https://github.com/AugustHuangMX/Evo/commit/cf669a5) | todo：派圈助手选手页做到哪了（分支 feat/pro-player 三个提交，还没推送） | 不引入：合作、推广或关联工作记录 |
| [5fa2308](https://github.com/AugustHuangMX/Evo/commit/5fa2308) | 测试工具第 2、3 步：测试项目、三种截法、测试员档案、窗口 | 暂缓：测试工具大改 |
| [4b21dfa](https://github.com/AugustHuangMX/Evo/commit/4b21dfa) | todo：派圈助手选手页照用户的设计稿重做了（分支 feat/pro-player 五个提交，还没推送） | 不引入：合作、推广或关联工作记录 |
| [a740f07](https://github.com/AugustHuangMX/Evo/commit/a740f07) | 发布 0.5.2 的准备：版本号、更新内容 | 不引入：上游发布流程及版本记录 |
| [ce5c651](https://github.com/AugustHuangMX/Evo/commit/ce5c651) | 测试工具第 4、5 步：打一局确认战绩、删掉记对的局的截图、录的时候提醒；赛季数据页带上答案 | 暂缓：测试工具大改 |
| [c953344](https://github.com/AugustHuangMX/Evo/commit/c953344) | 记录：发布 0.5.2（R2、官网、latest.json、GitHub Releases 已上线，COS 跳过） | 不引入：网站、线路或部署记录 |
