# Evo 隐私政策

生效日期：2026 年 10 月 24 日　·　适用于 Evo 0.1.0 及以后的版本

Evo 是一个开源的 Apex Legends 战绩记录工具，由个人开发者制作和维护，与 EA、Respawn 没有任何关系。

**一句话说明：Evo 记录的所有数据都保存在你自己的电脑上。只有你同意了"匿名统计"，Evo 才会把你以后每一局的部分数据（传奇、名次、击杀、伤害等，不含游戏名、ID 和截图）发给开发者；默认不发。** 没有账号，没有广告，不收集崩溃报告。会联网的只有"检查新版本"、你点了才开始的"一键更新"、你同意后才发送的"匿名统计"（开着时，数据页"全体玩家"还会下载所有玩家的汇总），以及电脑上没有 WebView2 时、你同意后才运行的微软 WebView2 安装程序：自动检查和匿名统计在第一次打开 Evo 时都会先问你要不要开启，开了也可以随时关掉；一键更新只在你点了"立即更新"之后才下载（0.1.0 还没有这些功能，完全不联网；0.1.0 之后的版本才有检查新版本，0.3.3 起才有一键更新，0.3.4 起才有安装 WebView2，0.3.7 起才有匿名统计）。

Evo 的源代码是公开的（[GitHub](https://github.com/AugustHuangMX/Evo)），这份说明里的每一条都能在代码里核对：Evo 联网的代码只在 `update_check.py`（检查新版本）、`updater.py`（一键更新）、`telemetry.py`（匿名统计）和 `global_stats.py`（数据页"全体玩家"下载汇总）；`webview2.py` 在你同意后运行微软的 WebView2 安装程序（由它联网）。接收匿名统计的服务器程序也在源代码里（`server/stats/`）。

---

## 一、Evo 在你电脑上保存了什么

Evo 检测到游戏在运行时，会定时截取屏幕，从截图里读出这一局的数据。下面这些都保存在 Evo 的**数据文件夹**里（0.3.5 起默认是程序旁边的 `Evo Tracker Data` 文件夹，可以在设置页改到别的地方；0.3.4 及以前默认是 `%LOCALAPPDATA%\Evo`，0.3.5 第一次打开时会自动搬到程序旁边；用安装程序装的，是你安装时选的数据文件夹；具体位置可以在 Evo 窗口的【设置】页看到并直接打开）。数据不在 `%LOCALAPPDATA%\Evo` 时，那里只留一个小文件 `data_location.txt`，里面写着数据文件夹在哪里（换新版本时靠它找到数据）：

| 内容 | 文件 | 里面有什么 |
|---|---|---|
| 战绩数据库 | `data\evo.db` | 每一局的开始和结束时间、你自己的游戏名、模式、地图、传奇、名次、击杀/助攻/击倒、伤害、生存时间、急救和重生次数、排位分（RP）变化、段位、"待确认"标记和你写的修正说明、对应截图的位置、Evo 的版本号；以及你自己的几个游戏名分别属于哪个账号（多账号功能用）；0.2.0 之后的版本还有总结页上两名队友的游戏名、卡片下面的 ID 和他们这一局的击杀/助攻/击倒、伤害、生存时间、急救和重生次数；0.3.1 起还有：每次回到大厅时读到的你自己的段位和段内分数、你的老板分（参与分）、对局中认出的两名队友用的传奇，以及同一项在不同画面上各读到的数（用来互相核对）；0.3.7 起还有：你在战绩页改过哪几项（只记改了哪一项）；0.4.0 起还有：每一局你手上拿过哪些武器、各拿了多久（对局中看右下角的武器图标认的，数据页"最常用武器"用） |
| 截图 | `captures\` | 记录时保存的游戏画面（见下面第二节）。0.4.0 起每一局的总结页截图一直留着（存成 JPG），在战绩页随时能看，删除这一局时一起删；其他截图默认识别完就删，只留需要你确认的那几局要用的；设置页可以打开"保留全部截图"（0.2.0 及以前的版本会保留全部截图） |
| 运行记录 | `logs\evo.log` | Evo 什么时候开始和停止记录、屏幕分辨率、游戏画面比例（0.3.5 起，还有 Apex 视频设置里的分辨率）、数据文件在电脑上的路径（路径里可能有你的 Windows 用户名）、识别结果、你的游戏名、内存占用、出错信息。文件最多约 4 MB，写满后自动覆盖最旧的内容 |
| 设置 | `settings.json` | 你在设置页选的选项、是否开启自动检查更新、上次检查的时间和结果、上次一键更新的结果、哪些提示已经显示过；0.3.7 起还有：是否同意匿名统计、从什么时候同意的、匿名编号、上次发送的时间和结果 |
| 匿名统计的发送记录 | `data\telemetry_sent.json` | 0.3.7 起，只在你同意匿名统计以后才有：已经发送过哪些局（每局一串由匿名编号算出来的对局编号和一个摘要，用来避免重复发送） |
| 全体玩家的汇总 | `data\global_stats.json` | 0.4.0 起，只在你开着匿名统计、打开过数据页"全体玩家"以后才有：从服务器下载的汇总（见第四节）和下载的时间 |
| 窗口缓存 | `webview\` | Evo 窗口（WebView2）自己的缓存，只和 Evo 的本地页面有关 |
| 数据库备份 | `data\evo.db.bak-…` | 只在你点"清空所有战绩"时自动生成，防止误删 |

数据库里只存**你自己和你的队友**的数据：0.2.0 之后的版本会从总结页读出两名队友的游戏名、卡片下面的 ID 和他们这一局的数据，0.3.1 起还会从对局中左下角认出两名队友的游戏名和传奇，用来在战绩页显示你和谁一起打、各打得怎么样（0.2.0 及以前的版本不存队友）。对局中的画面只在内存里看，不存成截图。Evo 不会把对手的名字、ID 写进数据库，除了你自己的段位，也不存任何人的段位。

数据文件夹以外，Evo 只在你打开"开机自动启动"时（0.3.6 起）往 Windows 注册表的启动项里写一条：`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run` 里的 `Evo Tracker`，内容是 Evo.exe 的位置。关掉开关就删掉这一条。

## 二、截图里可能有什么

截图是记录时那一刻的完整屏幕画面，所以除了你的战绩，还可能拍到：

- 你自己的游戏名；
- 队友和对手的游戏名、队友卡片上的 ID（例如总结页、击杀信息栏）；
- 当时盖在游戏上面的其他内容，例如 Discord、Steam 的好友列表或聊天悬浮窗、系统通知。

这些截图只留在你的电脑上。如果你要把截图发给别人（包括发给我反馈问题），请先看一眼里面有没有你不想公开的内容。

## 三、会联网的功能：检查新版本、一键更新、安装 WebView2

0.1.0 还没有这些功能；0.1.0 之后的版本才有检查新版本，0.3.3 起才有一键更新，0.3.4 起才有安装 WebView2。

- **什么时候联网**：只有两种情况——你开启了"自动检查新版本"（第一次打开 Evo 时会问你，你同意之前不会检查；开启后每次启动 Evo 时检查一次，Evo 一直开着时每 8 小时再检查一次）；或者你在设置页手动点了"检查更新"。另外，你点了"立即更新"时会下载新版本的安装包（见下面"一键更新"）。
- **下载了什么**：检查时只下载一个公开的小文件 `https://dl.evotracker.cn/latest.json`，里面写着最新的版本号、发布日期、更新内容，以及新版本安装包的地址和校验码（SHA-256）。从 0.4.0 起，这个文件读不到时，Evo 改读腾讯云对象存储（COS，上海）上的同一个文件（见下面"国内备用线路"）。
- **一键更新（0.3.3 起）**：发现新版本后，你点了"立即更新"，Evo 才从 `https://dl.evotracker.cn` 下载新版本的安装包（和官网上下载的是同一个文件，约 100 MB），下载完核对 SHA-256，对不上就删掉。安装包临时放在 Evo 程序文件夹里的 `_update` 文件夹，装好后自动删掉。下载好以后，你再点"安装并重新启动"，Evo 才会安装：关闭几秒钟，换上新版本的程序文件，自动重新打开。你的战绩数据不受影响。
- **只下载变了的文件（0.4.0 起）**：有从你现在的版本升上来的补丁包时，Evo 只下载补丁包（新版本里变了的文件，一般十几 MB），在本机用它和现有的程序文件拼出新版本，每个文件都核对 SHA-256；对不上就改下完整的安装包。补丁包和安装包放在同样的地方，也一样核对。
- **国内备用线路（0.4.0 起）**：`dl.evotracker.cn` 太慢或连不上时（国内晚上常这样），Evo 会自动改从**腾讯云对象存储（COS，上海）**下载同一个文件，从已经下到的地方接着下。这个存储桶是我自己的，文件和 `dl.evotracker.cn` 上的一样。不管从哪里下载，都按 `latest.json` 写的 SHA-256 核对，文件被换掉会被发现、删掉。
- **发送了什么**：请求里只带 Evo 自己的版本号（`Evo/0.1.0` 这样的文字）。**不发送任何战绩、截图、游戏名、电脑信息或设备编号。**
- **服务器能看到什么**：和打开任何网页一样，提供这个文件的服务器（Cloudflare，见第六节）能看到你的 IP 地址和请求时间。改走国内备用线路时，腾讯云也能看到（见第六节）。
- **发现新版本以后**：Evo 只会提醒你，不会在你不知道的时候下载或安装任何东西。要不要更新、在 Evo 里更新还是去官网下载，由你决定。
- **怎么关掉**：Evo 窗口 →【设置】→【更新】→ 关掉"自动检查新版本"。关掉以后 Evo 不再检查新版本（你手动点"检查更新"或"立即更新"、同意安装 WebView2、开着匿名统计时除外）。
- **安装 WebView2（0.3.4 起）**：Evo 的窗口要用微软的 WebView2 组件显示（Windows 11 自带，Windows 10 一般随 Edge 装上）。打开窗口前 Evo 会检查电脑上有没有它；没有时弹一个对话框问你要不要安装。你点"是"，Evo 才运行微软的 WebView2 安装程序（随 Evo 一起提供，有微软的数字签名），由它从微软的服务器下载（一百多 MB）并安装，这一步适用[微软的隐私声明](https://privacy.microsoft.com/)，微软的服务器能看到你的 IP 地址。Evo 自己不发送任何数据。你点"否"，Evo 就用你的默认浏览器打开面板，不联网，下次打开 Evo 时再问。电脑上已经有 WebView2 时什么都不会发生。

## 四、匿名统计（0.3.7 起，默认不发）

为了知道有多少人在用 Evo、大家用哪些传奇，以及哪些画面 Evo 还读不准，0.3.7 起 Evo 可以把你的对局数据匿名发给开发者。**这个功能默认关闭，只有你同意了才会发送。**

- **什么时候问你**：第一次打开 Evo 时，窗口顶部会问一次"愿意发送匿名的对局数据，帮助改进 Evo 吗？"。你点"同意"之前，Evo 不发送任何统计数据；点"不了"或不回答，就一直不发。以后可以在【设置】→【匿名统计】里随时打开或关闭。
- **发送什么**：
  - 一个匿名编号：Evo 在你电脑上随机生成的一串字符，和你的游戏账号、电脑都没有关系，只用来区分哪些数据来自同一台电脑、统计每天有多少人在用；
  - Evo 的版本号；
  - 每天一次："今天在用 Evo"，加上日期、屏幕分辨率、Apex 视频设置里的分辨率和显示模式（全屏、无边框、窗口），以及设置页选的游戏画面比例；
  - 每一局（只发你同意以后结束的局）：日期、模式、地图、你用的传奇、两名队友用的传奇、名次、击杀/助攻/击倒、伤害、RP 变化、段位（只有大段，如"钻石"，不含段内分数）、RP 是从哪个画面读的、这一局是否"待确认"、你在战绩页改过哪几项（不发改成了什么、原来是多少）、记这一局的 Evo 版本号，以及一个对局编号（由匿名编号算出来的一串乱码，只用来在你修正以后更新服务器上的同一局）；0.4.0 起还有两名队友这一局的击杀/助攻/击倒、伤害（总结页上读到的，不含他们的游戏名和 ID；同意以后结束的局会补发一次）。没读出来的项是空的。
- **不发送什么**：你和队友的游戏名、ID，截图，运行记录，你写的备注，文件路径，Windows 用户名，电脑的硬件信息和设备编号。对手的任何信息都不发送。
- **什么时候发送**：Evo 开着时，启动后约 1 分钟发一次，之后每小时一次（没有新的局要发时不联网）；每局结束 1 小时以后才发（大厅校准、你的修正多半已经做完）。发过的局之后又改了（比如你在战绩页修正），会再发一次，覆盖服务器上原来那一局。连不上就等下次，不影响 Evo 的其他功能。
- **发到哪里**：`https://stats.evotracker.cn`，由 Cloudflare 的 Workers 服务接收，存在 Cloudflare 的 D1 数据库里（亚太地区的服务器，不在中国大陆）。和打开任何网页一样，Cloudflare 能看到你的 IP 地址和请求时间；接收程序不把 IP 地址存进数据库，也不记请求日志。
- **怎么用**：只用来做统计（每天有多少人在用、各版本有多少人、各传奇的选用率和表现、各段位的人数、识别准不准、常见的分辨率）和改进 Evo 的识别。汇总以后的统计（例如"本赛季各传奇的选用率"）会显示在 Evo 的数据页"全体玩家"里，以后也可能公开在官网上，但不会公开任何一个编号的数据，也不会提供或出售给别人。
- **全体玩家（0.4.0 起）**：开着匿名统计时，你打开 Evo 窗口的【数据】→【全体玩家】，Evo 会从 `https://stats.evotracker.cn` 下载一份汇总：所有开启了匿名统计的玩家合在一起的数字（各传奇的出场次数、选用率和表现，热门的传奇组合，各段位的人数，每名玩家的场均名次、胜率、场均击杀和伤害——只有从小到大排好的一串数字，看不出是谁的），用来和你自己的数据对比；"你"的那部分在你电脑上算，不上传。下载时不发送匿名编号和任何战绩，服务器能看到的和打开网页一样（IP 地址和请求时间）。下载的汇总存在 `data\global_stats.json`，24 小时内不重复下载（点右上角的刷新按钮会马上下载）。没开匿名统计时这一页是锁住的，不下载。
- **保留多久**：用来做长期统计（比如看各赛季的变化），不会自动删除；不再需要时会删掉。
- **怎么关掉**：【设置】→【匿名统计】→ 关掉开关，之后不再发送任何数据。

如果 Evo 以后要加任何新的联网功能，会先在 Evo 里问你、默认不开启，并更新这份说明。

## 五、Evo 不做的事

- 不经你同意不发送任何战绩；截图和运行记录在任何情况下都不上传；
- 没有账号系统，不需要登录；
- 没有广告、第三方统计代码或崩溃自动上报（匿名统计只在你同意后发送，见第四节）；
- 不读取游戏的内存，不读取 EA、Steam 账号信息；游戏的文件只读一个：Apex 自己的视频设置文件（`Saved Games\Respawn\Apex\local\videoconfig.txt`），只看里面的分辨率和是不是全屏，用来判断游戏画面是 16:9、16:10 还是 4:3（0.3.5 起；只读，不修改，也不保存里面的其他内容）；
- 不记录键盘鼠标输入，不读取你电脑上与 Evo 无关的文件；
- 不查看、不记录其他玩家的段位或战绩。

另外：

- **Evo 窗口**里的页面是 Evo 在你电脑上自己运行的本地网页（地址是 `127.0.0.1`），只有你这台电脑能打开，同一网络里的其他设备访问不到。页面不加载任何网上的内容。
- **分享战绩（0.3.6 起）**：分享用的图片在你电脑上生成，Evo 不上传。图上有这一局（或今天、最近几十局）的数据，默认还有你的游戏名（分享前可以取消勾选）；队友只显示用的传奇和这一局的数据，不写名字和 ID。复制或保存的图片发给谁、发到哪里由你决定，发出去以后适用那个平台（如微信、QQ）自己的隐私政策。
- 窗口用的是 Windows 自带的 **Microsoft Edge WebView2** 组件。它是微软的软件，是否向微软发送诊断数据，由你 Windows 的"诊断和反馈"设置决定；Evo 不会更改这个设置。
- 你在 Evo 里点"源代码"、"去下载"这类链接时，会用你的默认浏览器打开对应网站，之后适用那些网站自己的隐私政策。

## 六、官网 evotracker.cn 和下载

- 官网 `evotracker.cn`、下载地址 `dl.evotracker.cn` 和接收匿名统计的 `stats.evotracker.cn` 都由 **Cloudflare** 提供服务（Cloudflare 是一家美国公司，服务器不在中国大陆）。
- 官网**不使用 Cookie、不加统计代码、不运行任何脚本、不加载其他网站的内容**（字体、图片都在本站）。
- 为了把网页和文件送到你这里、防御网络攻击，Cloudflare 会处理你的 IP 地址、浏览器类型等访问信息，具体见 [Cloudflare 隐私政策](https://www.cloudflare.com/privacypolicy/)。
- 我在 Cloudflare 后台能看到汇总的访问统计（例如访问次数、下载量、大致来自哪些国家或地区）；被 Cloudflare 安全规则拦截的访问，后台可能显示其 IP 地址。我不会用这些信息去识别具体的人，也不会提供给别人。
- 官网下载区"下载慢？尝试备用下载方式"里的"国内备用下载"，和一键更新的国内备用线路，用的是**腾讯云对象存储（COS）**上海服务器上的同一个文件（存储桶是我自己的）。从那里下载时，腾讯云会处理你的 IP 地址等访问信息，具体见 [腾讯云隐私政策](https://cloud.tencent.com/document/product/301/11470)。我在腾讯云后台能看到汇总的流量和请求次数，不会用来识别具体的人，也不会提供给别人。
- 从 GitHub 下载或浏览源代码、加入 QQ 群，适用 GitHub 和腾讯 QQ 各自的隐私政策。

## 七、你主动发给我的反馈文件

遇到问题时，你可以**自愿**把 `evo.log` 和截图发给我。Evo 不会自动发送这些文件。

- **发之前**：`evo.log` 可以用记事本打开，看里面有什么（见第一节），不想发的行可以删掉；截图里别人的名字可以先涂掉。
- **发到哪里要注意**：GitHub Issues 和 QQ 群聊里的内容**所有人都能看到**。如果只想给开发者看，请在 QQ 群里私聊开发者。
- **我怎么用**：只用来查你反馈的那个问题，不会给别人，也不会用作别的用途。
- **保留多久**：问题修好或确认无法修复以后删除，最长不超过 90 天。
- **放进测试集（只在你明确同意时）**：有些截图对改进识别很有帮助。我会先问你，你同意后才会把截图加进 Evo 的测试集；加进去之前会把与识别无关的部分（包括其他玩家的名字和 ID）涂黑。测试集以后可能随源代码公开在 GitHub 上，只包含涂黑后的版本。你可以随时撤回同意，我会把你的截图从测试集里删掉（已经被别人下载的公开副本无法收回）。

你随时可以要求我删除你发过来的文件，联系方式见第十一节。

## 八、怎么查看、导出和删除你的数据

数据都在你自己的电脑上，完全由你掌控（开着匿名统计时发出去的那部分见第四节）：

- **查看和修正**：在 Evo 窗口的【总览】【战绩】页查看；识别错的局可以在战绩页改正。
- **导出**：【设置】→【导出全部 CSV】，导出的文件里包含你的游戏名。
- **清空战绩**：【设置】→【清空所有战绩】。清空前会自动备份一份数据库，截图不会删除；备份和截图需要手动删掉。
- **全部删除**：右键托盘图标 → 退出，然后删除整个数据文件夹（位置见【设置】页）。卸载 Evo 时，删除程序文件夹不会删除数据文件夹，要另外删除。

## 九、未成年人

Evo 不收集能认出你是谁的个人信息（匿名统计也不包含），也包括未成年人。如果你未满 14 周岁，开启匿名统计、发送反馈文件之前请先征得父母或其他监护人的同意。

## 十、这份说明的修改

修改时会更新本文顶部的生效日期，修改记录可以在 GitHub 上查看。如果修改涉及 Evo 收集或发送的数据，会写在新版本的更新内容里。

## 十一、联系我

对这份说明有疑问，或想要删除你发给我的文件：

- GitHub Issues：<https://github.com/AugustHuangMX/Evo/issues>（公开可见，请不要在这里贴日志或截图）
- QQ 群：1128519951（<https://qm.qq.com/q/6L2kTbmCWc>），私下的请求请在群里私聊开发者

---

## Privacy Policy (English)

Effective date: October 24, 2026 · Applies to Evo 0.1.0 and later

Evo is an open-source match tracker for Apex Legends, made and maintained by an individual developer. It is not affiliated with EA or Respawn.

**This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.**

- **All data stays on your computer.** Evo captures the screen while the game is running and saves match results, screenshots, a log file and settings in its local data folder (shown in Evo's Settings page; from 0.3.5 the default is an `Evo Tracker Data` folder next to the program and it can be moved in Settings; earlier versions used `%LOCALAPPDATA%\Evo`). Nothing is uploaded unless you opt in to anonymous statistics (see below). There is no account system, no third-party analytics, no ads and no automatic crash reporting.
- **Anonymous statistics (from 0.3.7, off by default, opt-in only).** On first launch Evo asks once whether you want to send anonymous match data to help improve Evo; nothing is sent unless you click "同意" (Agree), and you can turn it off at any time in Settings. If you opt in, Evo sends to `https://stats.evotracker.cn` (a Cloudflare Worker storing data in a Cloudflare D1 database in the Asia-Pacific region; source code in `server/stats/`): a random anonymous ID generated on your computer (not linked to your game account or device), the Evo version, once a day a "used today" record with the date, screen resolution, the resolution and display mode from Apex's video settings and the aspect-ratio setting, and for each match that ends after you opted in: date, mode, map, your legend, your two teammates' legends, placement, kills/assists/knockdowns, damage, RP change, (from 0.4.0) your two teammates' kills/assists/knockdowns and damage for that match (no names or IDs), rank tier (e.g. "Diamond", no points), which screen the RP was read from, whether the match was flagged for review, which fields you corrected (not the values), the Evo version that recorded it, and a match key derived from the anonymous ID. It never sends player names or IDs (yours or anyone else's), screenshots, logs, your notes, file paths or device information. Data is sent about a minute after Evo starts and then every hour (only when there is something new to send); the receiving server does not store IP addresses or keep request logs (Cloudflare can see your IP address as with any web request). The data is used only for statistics and to improve recognition; aggregated statistics (never per-ID data) are shown in Evo and may be published on the website; it is not shared or sold. From 0.4.0, while anonymous statistics are on, Evo's Stats page has an "All players" (全体玩家) view: it downloads an aggregated summary from `https://stats.evotracker.cn` (legend pick rates and performance, popular squads, players per rank tier, and sorted per-player averages that cannot be linked to anyone) to compare with your own data, which is computed on your computer and not uploaded. The download sends no anonymous ID and no match data, is stored as `data\global_stats.json`, and is repeated at most once every 24 hours unless you press refresh. With anonymous statistics off, this view is locked and nothing is downloaded. Data is kept for long-term statistics (for example, comparing seasons) and deleted when no longer needed.
- **Teammates (versions after 0.2.0).** From the post-match summary screen, Evo also saves your two teammates' in-game names, the IDs shown under their cards, and their stats for that match (kills/assists/knockdowns, damage, survival time, revives, respawns), so the Matches page can show who you played with. Opponents' names and IDs are not saved, and nobody's rank is saved.
- **Weapons (from 0.4.0).** During a match Evo also records which weapon you are holding (recognized from the weapon icon at the bottom right) and for how long, for the "Most used weapons" table on the Stats page. This stays on your computer and is not included in anonymous statistics.
- **Screenshots** show the whole screen at that moment, so they may contain other players' in-game names and anything else on screen (for example a Discord or Steam overlay). They stay on your computer. From 0.4.0, the post-match summary screenshot of every match is kept (saved as JPG) so you can look at it on the Matches page; it is deleted when you delete that match. Other screenshots are deleted once a match is recognized, except those needed for matches you still have to confirm.
- **Update check and in-app update (network features added after version 0.1.0, which makes no network connections at all).** Evo asks on first launch whether to check for updates automatically (once each time Evo starts, then once every 8 hours while it keeps running); it stays off unless you agree, and it can be turned off at any time in Settings. You can also check manually. The check downloads the public file `https://dl.evotracker.cn/latest.json` (latest version number, release date, release notes, and the address and SHA-256 checksum of the new package). From version 0.3.3, when you click "立即更新" (Update now), Evo downloads the new package from `https://dl.evotracker.cn` (the same file as on the website), verifies its SHA-256 and keeps it in the `_update` folder inside Evo's program folder; it installs it (closes for a few seconds, replaces its program files and reopens) only after you click "安装并重新启动" (Install and restart). Your match data is not affected. From version 0.4.0, if `dl.evotracker.cn` is too slow or unreachable, Evo automatically switches to a backup route in mainland China: the same file from the developer's own bucket on Tencent Cloud Object Storage (COS, Shanghai), resuming where it stopped; if `latest.json` cannot be read from `dl.evotracker.cn`, the same file is read from that bucket. Also from 0.4.0, when a patch package for your current version exists, Evo downloads only the changed files (usually around 10–20 MB), rebuilds the new version from them and your existing program files, and checks every file's SHA-256, falling back to the full package if anything does not match. Whichever route is used, files are checked against the SHA-256 in `latest.json`. The requests contain only Evo's version number in the User-Agent (`Evo/<version>`) — no match data, screenshots, player names or device information. As with any web request, the server (Cloudflare, or Tencent Cloud for the backup route) can see your IP address and the time of the request. Evo never downloads or installs updates without you asking.
- **Installing WebView2 (from version 0.3.4).** The Evo window needs Microsoft Edge WebView2 (built into Windows 11, usually installed with Edge on Windows 10). If it is missing, Evo asks whether to install it. Only if you click "是" (Yes) does Evo run Microsoft's WebView2 installer (shipped with Evo, signed by Microsoft), which downloads and installs WebView2 from Microsoft's servers under the [Microsoft Privacy Statement](https://privacy.microsoft.com/); Microsoft's servers can see your IP address. Evo itself sends nothing. If you click "否" (No), Evo opens its panel in your default browser instead, without any network connection, and asks again the next time it starts.
- **Start with Windows (from 0.3.6).** Only if you turn it on, Evo adds one entry named `Evo Tracker` (the path of Evo.exe) under `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`; turning it off removes the entry.
- **Sharing (from 0.3.6).** Share images are created on your computer; Evo uploads nothing. They show your stats and, by default, your in-game name (you can untick it before sharing); teammates appear only as the legend they played and their stats for that match, without names or IDs. Where you send a copied or saved image is up to you.
- **The Evo window** shows a local page served on `127.0.0.1` only, and loads nothing from the internet. It uses Microsoft Edge WebView2, whose diagnostic data is governed by your Windows privacy settings.
- **Website.** `evotracker.cn` and `dl.evotracker.cn` are served by Cloudflare. The website uses no cookies, no analytics and no scripts. Cloudflare processes access data such as IP addresses to deliver the site and protect it from attacks ([Cloudflare Privacy Policy](https://www.cloudflare.com/privacypolicy/)). The developer only looks at aggregated statistics. The website's backup download link ("国内备用下载", under "下载慢？") points to the same file in the developer's bucket on Tencent Cloud Object Storage (COS, Shanghai); Tencent Cloud processes access data such as your IP address to deliver it ([Tencent Cloud Privacy Policy](https://cloud.tencent.com/document/product/301/11470)), and the developer only sees aggregated traffic.
- **Feedback files.** If you choose to send your log file or screenshots to the developer, they are used only to investigate your issue, are not shared, and are deleted once the issue is resolved (at most 90 days). Screenshots are added to Evo's test set only with your explicit consent, with unrelated content (including other players' names and IDs) blacked out; you can withdraw consent at any time.
- **Your data, your control.** You can view, correct, export (CSV) and delete all data from Evo's Settings page, or delete the data folder.
- **Children.** If you are under 14, ask a parent or guardian before turning on anonymous statistics or sending feedback files.
- **Contact:** [GitHub Issues](https://github.com/AugustHuangMX/Evo/issues) (public) or the QQ group 1128519951.
