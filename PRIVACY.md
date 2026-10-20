# Evo 隐私政策

生效日期：2026 年 10 月 19 日　·　适用于 Evo 0.1.0 及以后的版本

Evo 是一个开源的 Apex Legends 战绩记录工具，由个人开发者制作和维护，与 EA、Respawn 没有任何关系。

**一句话说明：Evo 记录的所有数据都只保存在你自己的电脑上，不会上传给开发者或任何人。** 没有账号，没有统计代码，没有广告，不收集崩溃报告。会联网的只有"检查新版本"、你点了才开始的"一键更新"，以及电脑上没有 WebView2 时、你同意后才运行的微软 WebView2 安装程序：自动检查在第一次打开 Evo 时会先问你要不要开启，开了也可以随时关掉；一键更新只在你点了"立即更新"之后才下载（0.1.0 还没有这些功能，完全不联网；0.1.0 之后的版本才有检查新版本，0.3.3 起才有一键更新，0.3.4 起才有安装 WebView2）。

Evo 的源代码是公开的（[GitHub](https://github.com/AugustHuangMX/Evo)），这份说明里的每一条都能在代码里核对：Evo 联网的代码只在 `update_check.py`（检查新版本）和 `updater.py`（一键更新）；`webview2.py` 在你同意后运行微软的 WebView2 安装程序（由它联网）。

---

## 一、Evo 在你电脑上保存了什么

Evo 检测到游戏在运行时，会定时截取屏幕，从截图里读出这一局的数据。下面这些都保存在 Evo 的**数据文件夹**里（默认是 `%LOCALAPPDATA%\Evo`；用安装程序装的，是你安装时选的数据文件夹；具体位置可以在 Evo 窗口的【设置】页看到并直接打开）：

| 内容 | 文件 | 里面有什么 |
|---|---|---|
| 战绩数据库 | `data\evo.db` | 每一局的开始和结束时间、你自己的游戏名、模式、地图、传奇、名次、击杀/助攻/击倒、伤害、生存时间、急救和重生次数、排位分（RP）变化、段位、"待确认"标记和你写的修正说明、对应截图的位置、Evo 的版本号；以及你自己的几个游戏名分别属于哪个账号（多账号功能用）；0.2.0 之后的版本还有总结页上两名队友的游戏名、卡片下面的 ID 和他们这一局的击杀/助攻/击倒、伤害、生存时间、急救和重生次数；0.3.1 起还有：每次回到大厅时读到的你自己的段位和段内分数、你的老板分（参与分）、对局中认出的两名队友用的传奇，以及同一项在不同画面上各读到的数（用来互相核对） |
| 截图 | `captures\` | 记录时保存的游戏画面（见下面第二节）。默认识别完就删，只留需要你确认的那几局要用的；设置页可以打开"保留全部截图"（0.2.0 及以前的版本会保留全部截图） |
| 运行记录 | `logs\evo.log` | Evo 什么时候开始和停止记录、屏幕分辨率、游戏画面比例（0.3.5 起，还有 Apex 视频设置里的分辨率）、数据文件在电脑上的路径（路径里可能有你的 Windows 用户名）、识别结果、你的游戏名、内存占用、出错信息。文件最多约 4 MB，写满后自动覆盖最旧的内容 |
| 设置 | `settings.json` | 你在设置页选的选项、是否开启自动检查更新、上次检查的时间和结果、上次一键更新的结果、哪些提示已经显示过 |
| 窗口缓存 | `webview\` | Evo 窗口（WebView2）自己的缓存，只和 Evo 的本地页面有关 |
| 数据库备份 | `data\evo.db.bak-…` | 只在你点"清空所有战绩"时自动生成，防止误删 |

数据库里只存**你自己和你的队友**的数据：0.2.0 之后的版本会从总结页读出两名队友的游戏名、卡片下面的 ID 和他们这一局的数据，0.3.1 起还会从对局中左下角认出两名队友的游戏名和传奇，用来在战绩页显示你和谁一起打、各打得怎么样（0.2.0 及以前的版本不存队友）。对局中的画面只在内存里看，不存成截图。Evo 不会把对手的名字、ID 写进数据库，除了你自己的段位，也不存任何人的段位。

## 二、截图里可能有什么

截图是记录时那一刻的完整屏幕画面，所以除了你的战绩，还可能拍到：

- 你自己的游戏名；
- 队友和对手的游戏名、队友卡片上的 ID（例如总结页、击杀信息栏）；
- 当时盖在游戏上面的其他内容，例如 Discord、Steam 的好友列表或聊天悬浮窗、系统通知。

这些截图只留在你的电脑上。如果你要把截图发给别人（包括发给我反馈问题），请先看一眼里面有没有你不想公开的内容。

## 三、会联网的功能：检查新版本、一键更新、安装 WebView2

0.1.0 还没有这些功能；0.1.0 之后的版本才有检查新版本，0.3.3 起才有一键更新，0.3.4 起才有安装 WebView2。

- **什么时候联网**：只有两种情况——你开启了"自动检查新版本"（第一次打开 Evo 时会问你，你同意之前不会检查；开启后每次启动 Evo 时检查一次，Evo 一直开着时每 8 小时再检查一次）；或者你在设置页手动点了"检查更新"。另外，你点了"立即更新"时会下载新版本的安装包（见下面"一键更新"）。
- **下载了什么**：检查时只下载一个公开的小文件 `https://dl.evotracker.cn/latest.json`，里面写着最新的版本号、发布日期、更新内容，以及新版本安装包的地址和校验码（SHA-256）。
- **一键更新（0.3.3 起）**：发现新版本后，你点了"立即更新"，Evo 才从 `https://dl.evotracker.cn` 下载新版本的安装包（和官网上下载的是同一个文件，约 100 MB），下载完核对 SHA-256，对不上就删掉。安装包临时放在 Evo 程序文件夹里的 `_update` 文件夹，装好后自动删掉。下载好以后，你再点"安装并重新启动"，Evo 才会安装：关闭几秒钟，换上新版本的程序文件，自动重新打开。你的战绩数据不受影响。
- **发送了什么**：请求里只带 Evo 自己的版本号（`Evo/0.1.0` 这样的文字）。**不发送任何战绩、截图、游戏名、电脑信息或设备编号。**
- **服务器能看到什么**：和打开任何网页一样，提供这个文件的服务器（Cloudflare，见第五节）能看到你的 IP 地址和请求时间。
- **发现新版本以后**：Evo 只会提醒你，不会在你不知道的时候下载或安装任何东西。要不要更新、在 Evo 里更新还是去官网下载，由你决定。
- **怎么关掉**：Evo 窗口 →【设置】→【更新】→ 关掉"自动检查新版本"。关掉以后 Evo 完全不联网（除非你手动点"检查更新"或"立即更新"，或者同意安装 WebView2）。
- **安装 WebView2（0.3.4 起）**：Evo 的窗口要用微软的 WebView2 组件显示（Windows 11 自带，Windows 10 一般随 Edge 装上）。打开窗口前 Evo 会检查电脑上有没有它；没有时弹一个对话框问你要不要安装。你点"是"，Evo 才运行微软的 WebView2 安装程序（随 Evo 一起提供，有微软的数字签名），由它从微软的服务器下载（一百多 MB）并安装，这一步适用[微软的隐私声明](https://privacy.microsoft.com/)，微软的服务器能看到你的 IP 地址。Evo 自己不发送任何数据。你点"否"，Evo 就用你的默认浏览器打开面板，不联网，下次打开 Evo 时再问。电脑上已经有 WebView2 时什么都不会发生。

如果 Evo 以后要加任何新的联网功能，会先在 Evo 里问你、默认不开启，并更新这份说明。

## 四、Evo 不做的事

- 不上传你的战绩、截图或运行记录；
- 没有账号系统，不需要登录；
- 没有统计代码、广告、遥测或崩溃自动上报；
- 不读取游戏的内存，不读取 EA、Steam 账号信息；游戏的文件只读一个：Apex 自己的视频设置文件（`Saved Games\Respawn\Apex\local\videoconfig.txt`），只看里面的分辨率和是不是全屏，用来判断游戏画面是 16:9、16:10 还是 4:3（0.3.5 起；只读，不修改，也不保存里面的其他内容）；
- 不记录键盘鼠标输入，不读取你电脑上与 Evo 无关的文件；
- 不查看、不记录其他玩家的段位或战绩。

另外：

- **Evo 窗口**里的页面是 Evo 在你电脑上自己运行的本地网页（地址是 `127.0.0.1`），只有你这台电脑能打开，同一网络里的其他设备访问不到。页面不加载任何网上的内容。
- 窗口用的是 Windows 自带的 **Microsoft Edge WebView2** 组件。它是微软的软件，是否向微软发送诊断数据，由你 Windows 的"诊断和反馈"设置决定；Evo 不会更改这个设置。
- 你在 Evo 里点"源代码"、"去下载"这类链接时，会用你的默认浏览器打开对应网站，之后适用那些网站自己的隐私政策。

## 五、官网 evotracker.cn 和下载

- 官网 `evotracker.cn` 和下载地址 `dl.evotracker.cn` 都由 **Cloudflare** 提供服务（Cloudflare 是一家美国公司，服务器不在中国大陆）。
- 官网**不使用 Cookie、不加统计代码、不运行任何脚本、不加载其他网站的内容**（字体、图片都在本站）。
- 为了把网页和文件送到你这里、防御网络攻击，Cloudflare 会处理你的 IP 地址、浏览器类型等访问信息，具体见 [Cloudflare 隐私政策](https://www.cloudflare.com/privacypolicy/)。
- 我在 Cloudflare 后台能看到汇总的访问统计（例如访问次数、下载量、大致来自哪些国家或地区）；被 Cloudflare 安全规则拦截的访问，后台可能显示其 IP 地址。我不会用这些信息去识别具体的人，也不会提供给别人。
- 从 GitHub 下载或浏览源代码、加入 QQ 群，适用 GitHub 和腾讯 QQ 各自的隐私政策。

## 六、你主动发给我的反馈文件

遇到问题时，你可以**自愿**把 `evo.log` 和截图发给我。Evo 不会自动发送这些文件。

- **发之前**：`evo.log` 可以用记事本打开，看里面有什么（见第一节），不想发的行可以删掉；截图里别人的名字可以先涂掉。
- **发到哪里要注意**：GitHub Issues 和 QQ 群聊里的内容**所有人都能看到**。如果只想给开发者看，请在 QQ 群里私聊开发者。
- **我怎么用**：只用来查你反馈的那个问题，不会给别人，也不会用作别的用途。
- **保留多久**：问题修好或确认无法修复以后删除，最长不超过 90 天。
- **放进测试集（只在你明确同意时）**：有些截图对改进识别很有帮助。我会先问你，你同意后才会把截图加进 Evo 的测试集；加进去之前会把与识别无关的部分（包括其他玩家的名字和 ID）涂黑。测试集以后可能随源代码公开在 GitHub 上，只包含涂黑后的版本。你可以随时撤回同意，我会把你的截图从测试集里删掉（已经被别人下载的公开副本无法收回）。

你随时可以要求我删除你发过来的文件，联系方式见第十节。

## 七、怎么查看、导出和删除你的数据

数据都在你自己的电脑上，完全由你掌控：

- **查看和修正**：在 Evo 窗口的【总览】【战绩】页查看；识别错的局可以在战绩页改正。
- **导出**：【设置】→【导出全部 CSV】，导出的文件里包含你的游戏名。
- **清空战绩**：【设置】→【清空所有战绩】。清空前会自动备份一份数据库，截图不会删除；备份和截图需要手动删掉。
- **全部删除**：右键托盘图标 → 退出，然后删除整个数据文件夹（位置见【设置】页）。卸载 Evo 时，删除程序文件夹不会删除数据文件夹，要另外删除。

## 八、未成年人

Evo 不收集任何人的个人信息，也包括未成年人。如果你未满 14 周岁，发送反馈文件之前请先征得父母或其他监护人的同意。

## 九、这份说明的修改

修改时会更新本文顶部的生效日期，修改记录可以在 GitHub 上查看。如果修改涉及 Evo 收集或发送的数据，会写在新版本的更新内容里。

## 十、联系我

对这份说明有疑问，或想要删除你发给我的文件：

- GitHub Issues：<https://github.com/AugustHuangMX/Evo/issues>（公开可见，请不要在这里贴日志或截图）
- QQ 群：1128519951（<https://qm.qq.com/q/6L2kTbmCWc>），私下的请求请在群里私聊开发者

---

## Privacy Policy (English)

Effective date: October 19, 2026 · Applies to Evo 0.1.0 and later

Evo is an open-source match tracker for Apex Legends, made and maintained by an individual developer. It is not affiliated with EA or Respawn.

**This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.**

- **All data stays on your computer.** Evo captures the screen while the game is running and saves match results, screenshots, a log file and settings in its local data folder (shown in Evo's Settings page; `%LOCALAPPDATA%\Evo` by default). Nothing is uploaded. There is no account system, no analytics, no telemetry, no ads and no automatic crash reporting.
- **Teammates (versions after 0.2.0).** From the post-match summary screen, Evo also saves your two teammates' in-game names, the IDs shown under their cards, and their stats for that match (kills/assists/knockdowns, damage, survival time, revives, respawns), so the Matches page can show who you played with. Opponents' names and IDs are not saved, and nobody's rank is saved.
- **Screenshots** show the whole screen at that moment, so they may contain other players' in-game names and anything else on screen (for example a Discord or Steam overlay). They stay on your computer.
- **Update check and in-app update (network features added after version 0.1.0, which makes no network connections at all).** Evo asks on first launch whether to check for updates automatically (once each time Evo starts, then once every 8 hours while it keeps running); it stays off unless you agree, and it can be turned off at any time in Settings. You can also check manually. The check downloads the public file `https://dl.evotracker.cn/latest.json` (latest version number, release date, release notes, and the address and SHA-256 checksum of the new package). From version 0.3.3, when you click "立即更新" (Update now), Evo downloads the new package from `https://dl.evotracker.cn` (the same file as on the website), verifies its SHA-256 and keeps it in the `_update` folder inside Evo's program folder; it installs it (closes for a few seconds, replaces its program files and reopens) only after you click "安装并重新启动" (Install and restart). Your match data is not affected. The requests contain only Evo's version number in the User-Agent (`Evo/<version>`) — no match data, screenshots, player names or device information. As with any web request, the server (Cloudflare) can see your IP address and the time of the request. Evo never downloads or installs updates without you asking.
- **Installing WebView2 (from version 0.3.4; the only other network feature).** The Evo window needs Microsoft Edge WebView2 (built into Windows 11, usually installed with Edge on Windows 10). If it is missing, Evo asks whether to install it. Only if you click "是" (Yes) does Evo run Microsoft's WebView2 installer (shipped with Evo, signed by Microsoft), which downloads and installs WebView2 from Microsoft's servers under the [Microsoft Privacy Statement](https://privacy.microsoft.com/); Microsoft's servers can see your IP address. Evo itself sends nothing. If you click "否" (No), Evo opens its panel in your default browser instead, without any network connection, and asks again the next time it starts.
- **The Evo window** shows a local page served on `127.0.0.1` only, and loads nothing from the internet. It uses Microsoft Edge WebView2, whose diagnostic data is governed by your Windows privacy settings.
- **Website.** `evotracker.cn` and `dl.evotracker.cn` are served by Cloudflare. The website uses no cookies, no analytics and no scripts. Cloudflare processes access data such as IP addresses to deliver the site and protect it from attacks ([Cloudflare Privacy Policy](https://www.cloudflare.com/privacypolicy/)). The developer only looks at aggregated statistics.
- **Feedback files.** If you choose to send your log file or screenshots to the developer, they are used only to investigate your issue, are not shared, and are deleted once the issue is resolved (at most 90 days). Screenshots are added to Evo's test set only with your explicit consent, with unrelated content (including other players' names and IDs) blacked out; you can withdraw consent at any time.
- **Your data, your control.** You can view, correct, export (CSV) and delete all data from Evo's Settings page, or delete the data folder.
- **Contact:** [GitHub Issues](https://github.com/AugustHuangMX/Evo/issues) (public) or the QQ group 1128519951.
