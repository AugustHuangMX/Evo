"""游戏画面的宽高比（16:9、16:10、4:3）：Evo 按它换算界面在截图上的位置（见 layout.py）。

截图的尺寸只说明显示器的比例：DX12 全屏用拉伸分辨率（如启动项 -w 2560 -h 1600）时，游戏按 16:10 摆界面，
再压扁成显示器的 2560×1440，截图看起来还是 16:9。所以要另外知道游戏自己用的分辨率（2026-10-20 用户决定）：
- 设置页"游戏画面比例"（settings.json 里的 game_aspect）：自动检测（默认）/ 16:9 / 16:10 / 4:3
- 自动检测：读 Apex 自己的视频设置文件（存档文件夹里的 Respawn\\Apex\\local\\videoconfig.txt，只读不改，原则 1 的例外）。
  全屏时用里面的分辨率（setting.defaultres × setting.defaultresheight）；不是全屏（无边框窗口、窗口）或读不到文件：
  和截图一样。2026-10-20 实测：DX12 全屏时文件里是 fullscreen 1、nowindowborder 1，分辨率就是启动项写的那个
记录程序每隔几秒看一眼（AspectWatcher：设置改了、文件改了才重新读），变了就写一行日志。

查看自动检测的结果：python code/game_aspect.py

app_settings 用到时才 import：诊断工具（evo_diag.py）只读视频设置文件，不能连带 import paths.py
（它一 import 就会在程序旁边建数据文件夹）。
"""

import ctypes
import os
import sys
import time
import uuid
from ctypes import wintypes
from fractions import Fraction
from pathlib import Path

import layout

SETTING_KEY = "game_aspect"
AUTO = "auto"
ASPECTS = {"16:9": 16 / 9, "16:10": 16 / 10, "4:3": 4 / 3}  # 设置页能选的
CHECK_SECONDS = 5  # 记录程序每隔几秒看一眼设置和视频设置文件


def _saved_games():
    """Windows 的"保存的游戏"文件夹（一般是 C:\\Users\\<用户名>\\Saved Games，用户可能挪过位置，所以问系统）。"""
    try:
        guid = uuid.UUID("4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4")  # FOLDERID_SavedGames
        buf = (ctypes.c_ubyte * 16).from_buffer_copy(guid.bytes_le)
        path = ctypes.c_wchar_p()
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        if shell32.SHGetKnownFolderPath(ctypes.byref(buf), 0, None, ctypes.byref(path)) == 0:
            found = Path(path.value)
            ctypes.WinDLL("ole32").CoTaskMemFree(path)
            return found
    except (OSError, AttributeError, ValueError):
        pass
    return Path.home() / "Saved Games"


# 测试时用 EVO_VIDEOCONFIG 指到临时文件夹，免得读到开发者电脑上真实的游戏设置
VIDEO_CONFIG = (Path(os.environ["EVO_VIDEOCONFIG"]) if os.environ.get("EVO_VIDEOCONFIG")
                else _saved_games() / "Respawn" / "Apex" / "local" / "videoconfig.txt")


def name(aspect):
    """宽高比 → "16:10" 这样的写法（不是常见的几种就写成 "1.85:1"）。"""
    for label, value in ASPECTS.items():
        if abs(aspect - value) < layout.SAME_ASPECT:
            return label
    r = Fraction(aspect).limit_denominator(20)
    return f"{r.numerator}:{r.denominator}" if r.denominator <= 20 else f"{aspect:.2f}:1"


def read_video_config(path=None):
    """读 Apex 的视频设置文件，返回 {"setting.defaultres": "2560", ...}；读不到返回 None。
    文件是 UTF-16（带 BOM）或 UTF-8 的文本，每行 "键" "值"。"""
    try:
        raw = Path(path or VIDEO_CONFIG).read_bytes()
    except OSError:
        return None
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    out = {}
    for line in text.splitlines():
        parts = [p for p in line.strip().split('"') if p.strip()]
        if len(parts) == 2:
            out[parts[0].strip()] = parts[1].strip()
    return out


def from_video_config(path=None):
    """自动检测：(宽高比或 None, 说明)。None = 和截图一样。"""
    cfg = read_video_config(path)
    if cfg is None:
        return None, "没找到 Apex 的视频设置文件，按截图的比例"
    try:
        w, h = int(cfg["setting.defaultres"]), int(cfg["setting.defaultresheight"])
        fullscreen = cfg.get("setting.fullscreen") == "1"
    except (KeyError, ValueError):
        return None, "Apex 的视频设置文件里没有分辨率，按截图的比例"
    if not fullscreen:
        return None, f"Apex 视频设置：不是全屏（文件里的分辨率 {w}×{h} 不一定在用），按截图的比例"
    if w <= 0 or h <= 0:
        return None, "Apex 视频设置里的分辨率不对，按截图的比例"
    return w / h, f"Apex 视频设置：全屏 {w}×{h}"


def choice():
    """设置页选的：AUTO 或 ASPECTS 里的一个。"""
    import app_settings

    value = app_settings.get(SETTING_KEY, AUTO)
    return value if value in ASPECTS else AUTO


def current(path=None):
    """现在该用的宽高比和说明：(宽高比或 None, 说明)。"""
    picked = choice()
    if picked != AUTO:
        return ASPECTS[picked], f"设置页手动选的 {picked}"
    aspect, why = from_video_config(path)
    return aspect, f"自动检测（{why}）"


class AspectWatcher:
    """记录程序里每截一张调用一次 check(截图)：每隔 CHECK_SECONDS 秒看一眼设置和视频设置文件，
    宽高比变了就设给 layout，并写一行日志。fixed：测试时直接指定（如 "16:10"），不看设置和文件。"""

    def __init__(self, log, clock=time.monotonic, path=None, fixed=None):
        self.log, self.clock, self.path, self.fixed = log, clock, path, fixed
        self.next_check = None
        self.state = None  # (设置, 文件修改时间, 截图尺寸)：都没变就不用重新读
        self.aspect = None
        self.announced = None

    def check(self, frame):
        now = self.clock()
        if self.next_check is not None and now < self.next_check:
            return
        self.next_check = now + CHECK_SECONDS
        try:
            mtime = Path(self.path or VIDEO_CONFIG).stat().st_mtime
        except OSError:
            mtime = None
        h, w = frame.shape[:2]
        state = (self.fixed or choice(), mtime, (w, h))
        if state == self.state:
            return
        self.state = state
        if self.fixed:
            aspect, why = ASPECTS[self.fixed], f"测试时指定的 {self.fixed}"
        else:
            aspect, why = current(self.path)
        layout.set_game_aspect(aspect)
        used = aspect or w / h
        text = f"游戏画面比例：{name(used)}（{why}；截图 {w}×{h}）"
        if text != self.announced:
            self.log(text)
            self.announced = text

    def reset(self):
        """停止记录时：换算回"和截图一样"，不影响同一个进程里以后的事（如测试）。"""
        layout.set_game_aspect(None)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"视频设置文件：{VIDEO_CONFIG}")
    aspect, why = from_video_config()
    print(f"自动检测：{name(aspect) if aspect else '和截图一样'}（{why}）")
    print(f"设置页选的：{'自动检测' if choice() == AUTO else choice()}")


if __name__ == "__main__":
    main()
