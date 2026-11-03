"""Evo 窗口要用微软的 WebView2 组件（Edge 用的同一个内核）。这里检查电脑上有没有它，没有时问用户要不要装。

为什么要自己检查：pywebview 找不到 WebView2 时不报错，悄悄改用老的 IE 内核。IE 看不懂面板的样式（颜色变量、
grid 布局、woff2 字体），窗口里只剩一堆没排好的文字（2026-10-19 有用户反馈）。所以打开窗口前先检查：
- 有：照常打开 Evo 窗口
- 没有：弹 Windows 自带的对话框（这时网页显示不出来），问要不要安装。用户点"是"才运行微软的 WebView2 安装程序
  （Evergreen Bootstrapper，约 2 MB，打包时放进 _internal/webview2，见 tools/build.py），它从微软的服务器下载、安装，
  一般不需要管理员权限；装好后打开 Evo 窗口。用户点"否"或没装上，改用浏览器打开面板（evo_app.py 的 PanelServer）
联网：只有用户点了"是"，运行的是微软的安装程序，下载的是微软的文件，Evo 自己不发送任何数据（原则 2 的例外，2026-10-19 用户决定）。

判断"有没有"和 pywebview 一样（webview/platforms/winforms.py 的 _is_chromium）：注册表里 WebView2 的版本号
不低于 86.0.622.0，并且 .NET Framework 不低于 4.6.2。两边说法一致，才不会出现"这里说有、pywebview 却用了 IE"。

下载标记（unblock，0.4.1 起）：用 Windows 自带的"全部解压缩"解开压缩包时，每个文件都带上"从网上下载的"标记
（文件的附加数据流 Zone.Identifier）。.NET Framework 不加载带这个标记的程序集，pythonnet 就报
"Failed to resolve Python.Runtime.Loader.Initialize"，窗口打不开、只能用浏览器（2026-10-25 玩家反馈）。
所以打包版每次启动时，先去掉窗口要用的那几个 DLL 上的标记。玩家已经在 SmartScreen 里点过"仍要运行"，
这个标记对 Evo 自己的文件没有别的用处。

单独试：python code/webview2.py   （只显示检查结果，不弹窗、不安装）
"""

import ctypes
import os
import subprocess
import winreg

from paths import APP_DIR, FROZEN

SETUP_NAME = "MicrosoftEdgeWebview2Setup.exe"
# 微软的 WebView2 安装程序：打包后在 _internal/webview2 里；用源码运行时用 build.py 下载到 build/webview2 的那一份
SETUP = (APP_DIR / "webview2" if FROZEN else APP_DIR / "build" / "webview2") / SETUP_NAME
DOWNLOAD_PAGE = "https://developer.microsoft.com/microsoft-edge/webview2/"  # 自动安装失败时，告诉用户去哪里手动下载
INSTALL_TIMEOUT = 30 * 60  # 网慢时下载要好几分钟；超过半小时就当作没装上

NET_FOLDERS = ("pythonnet", "clr_loader", "webview")  # 窗口要用的 .NET 程序集都在 _internal 下的这几个文件夹里
MARK = ":Zone.Identifier"  # Windows 记"这个文件是从网上下载的"的地方

MIN_VERSION = (86, 0, 622, 0)  # pywebview 要求的最低版本
NET_462 = 394802  # .NET Framework 4.6.2 的 Release 编号
# 注册表里 WebView2 的编号：正式版，以及 Beta、Dev、Canary 预览版（pywebview 也认这几个）
CLIENT_KEYS = ("{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",
               "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}", "{65C35B14-6C1D-4122-AC46-7148CC9D6497}")
# 装给本机所有用户的在 HKLM 的 WOW6432Node 下，只装给当前用户的在 HKCU 下（和 pywebview 一样；Evo 只有 64 位版）
CLIENT_PATHS = ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"))

MB_YESNO, MB_OK = 0x4, 0x0
MB_ICONQUESTION, MB_ICONWARNING = 0x20, 0x30
MB_SETFOREGROUND, MB_TOPMOST = 0x10000, 0x40000
IDYES = 6
TITLE = "Evo"
ASK_TEXT = (
    "Evo 的窗口要用微软的 WebView2 组件显示，这台电脑上没有找到它（精简版 Windows、删掉过 Edge 的电脑常见）。\n\n"
    "现在安装吗？\n\n"
    "点“是”：运行微软的 WebView2 安装程序，它会从微软的服务器下载（一百多 MB），装好后自动打开 Evo 窗口。"
    "Evo 不会发送你的任何数据。\n"
    "点“否”：先用浏览器打开 Evo 的面板，战绩照常记录。下次打开 Evo 时还会问你。"
)
FAILED_TEXT = (
    "WebView2 没有装上。\n\n"
    "先用浏览器打开 Evo 的面板，战绩照常记录。也可以到微软官网下载安装 WebView2（选“常青版引导程序”），装好后重新打开 Evo：\n"
    f"{DOWNLOAD_PAGE}"
)


def _registry_value(root, path, name):
    try:
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _version(text):
    """"130.0.2849.80" → (130, 0, 2849, 80)；读不懂的（比如卸载后留下的空值）当作 (0,)。"""
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return (0,)


def installed_version():
    """电脑上 WebView2 的版本号（如 "130.0.2849.80"）；没有、版本太旧、.NET Framework 太旧时返回 None。"""
    release = _registry_value(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
                              "Release")
    if not isinstance(release, int) or release < NET_462:
        return None
    for root, path in CLIENT_PATHS:
        for client in CLIENT_KEYS:
            version = _registry_value(root, rf"{path}\{client}", "pv")
            if version and _version(version) >= MIN_VERSION:
                return str(version)
    return None


def available():
    return installed_version() is not None


def _message(text, flags):
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags | MB_SETFOREGROUND | MB_TOPMOST)


def ask():
    """问用户要不要安装 WebView2，点了"是"返回 True。"""
    return _message(ASK_TEXT, MB_YESNO | MB_ICONQUESTION) == IDYES


def tell_failed():
    _message(FAILED_TEXT, MB_OK | MB_ICONWARNING)


def install():
    """运行微软的安装程序（带它自己的进度窗口），等它结束。返回 (装好了没有, 说明)，说明写进日志。"""
    if not SETUP.exists():
        return False, f"找不到 WebView2 安装程序：{SETUP}"
    try:
        result = subprocess.run([str(SETUP), "/install"], timeout=INSTALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"WebView2 安装程序 {INSTALL_TIMEOUT // 60} 分钟还没结束"
    except OSError as e:  # 被杀毒软件拦住等
        return False, f"WebView2 安装程序运行不了：{e}"
    version = installed_version()
    if version:
        return True, f"WebView2 装好了（版本 {version}，安装程序返回 {result.returncode}）"
    return False, f"WebView2 安装程序结束了（返回 {result.returncode}），但还是找不到 WebView2"


def unblock(log, root=APP_DIR):
    """去掉窗口要用的 DLL 上"从网上下载的"标记（见本文件开头），去掉了、去不掉都写进日志。返回去掉了几个。"""
    removed, failed = 0, []
    for folder in NET_FOLDERS:
        for path in sorted((root / folder).rglob("*.dll")):
            mark = f"{path}{MARK}"
            if not os.path.exists(mark):  # 没有标记；不是 NTFS 的盘（FAT32、exFAT）也没有
                continue
            try:
                os.remove(mark)
                removed += 1
            except OSError:  # 程序文件夹不能写入（如 C:\Program Files）、被杀毒软件拦住
                failed.append(path)
    if removed:
        log(f"去掉了 {removed} 个文件上\"从网上下载的\"标记（Windows 自带的解压缩会加上，带着它 Evo 窗口打不开）")
    if failed:
        log(f"{len(failed)} 个文件去不掉\"从网上下载的\"标记，Evo 窗口可能打不开、改用浏览器：{failed[0]} 等")
    return removed


if __name__ == "__main__":
    print(f"WebView2：{installed_version() or '没有（或版本太旧 / .NET Framework 太旧）'}")
    print(f"安装程序：{SETUP}（{'有' if SETUP.exists() else '没有'}）")
