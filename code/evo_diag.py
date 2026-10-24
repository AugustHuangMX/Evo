"""Evo 诊断工具：玩家用 Evo 记不下战绩时，用它截一段游戏画面，连同电脑的显示设置、Evo 的日志打成一个压缩包，发给开发者。

和 Evo 分开打包成一个 exe（Evo诊断工具.exe，python tools/build_diag.py），玩家下载后双击运行（命令行窗口）：
- 截屏方式和 Evo 的记录程序一样（dxcam、主显示器、同样的参数），截到的就是 Evo 看到的样子
- 游戏开着时每 2 秒截一张（和上一张存的几乎一样就不存），比 1920 宽的缩到 1920 宽（Evo 识别本来就按 1920×1080 换算）；
  每分钟另存一张原尺寸。游戏没开时每 10 秒看一眼、每分钟最多存一张（看得出截屏本身通不通）
- 每次截屏记一行（captures.csv）：拿到新画面没有、和上一张差多少。游戏开着却一直"没有新画面" = 截屏卡住了
  （Evo 这时拿到的是 dxcam 缓存的上一张旧图，什么都认不出）；dxcam 自己的警告也记下来（Evo 的日志里没有）
- 每分钟给每个显示器存一张很小的缩略图（monitors/，用 mss，和 dxcam 不是一条路）：看得出游戏在哪个屏幕上，看不清字
- info.txt（info.json 是同样的内容，给 tools/diag_report.py 读）：Windows 版本、显卡、每个显示器（分辨率、刷新率、缩放、
  主显示器、HDR）、Evo 截的是哪个显示器、Apex 的进程名、Apex 视频设置里的分辨率和是不是全屏（和 Evo 读的同一个文件，
  原则 1 的例外，只取这几项）、Windows 兼容性设置里给 Apex 勾的选项（如"禁用全屏优化"）、开着哪些可能盖住画面或也在截屏的软件
  （只比对几个已知的进程名）
- 电脑上有 Evo 的话，带上 Evo 的日志（evo_logs/）和设置里跟识别有关的三项
- 结束：玩家在本窗口按回车、游戏关掉、截满 60 分钟或 1.5 GB、硬盘快满。打成 Evo诊断_<时间>.zip 放在桌面，打开文件夹选中它
- 只看不动（原则 1）：只截屏、只读进程名和 Windows 的显示设置；按回车用的是本窗口自己的输入，不注册全局键盘钩子；
  只找 Evo.exe 自己在哪个文件夹，不打开游戏进程；不联网，压缩包发不发由玩家决定

不 import paths.py（它一 import 就会在程序旁边建"Evo Tracker Data"文件夹）：Evo 的数据文件夹按 find_evo_data_dirs 找。

开发时运行：python code/evo_diag.py [--minutes 5] [--out 文件夹]
看玩家发来的压缩包：python tools/diag_report.py Evo诊断_xxx.zip
"""

import argparse
import csv
import ctypes
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import winreg
import zipfile
from collections import Counter
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from game_aspect import read_video_config
from game_detect import GAME_PROCESSES, processes
from process_info import memory_mb, set_low_priority

VERSION = "1.0"

INTERVAL = 2.0  # 游戏开着时每隔几秒截一张
IDLE_INTERVAL = 10.0  # 游戏没开时每隔几秒看一眼
IDLE_SAVE_SECONDS = 60  # 游戏没开时最多每隔这么久存一张
SMALL_WIDTH = 1920  # 比这个宽的截图缩到这个宽
JPEG_QUALITY = 85
FULL_SECONDS = 60  # 游戏开着时每隔这么久另存一张原尺寸
FULL_QUALITY = 92  # 原尺寸那张：质量高一点，颜色不压缩（4:4:4）
THUMB_SECONDS = 60  # 每隔这么久给每个显示器存一张缩略图
THUMB_WIDTH = 480
SAME = 1.5  # 和上一张存的相比，缩略图平均每个像素差不到这么多（0–255），算"没变"，不存（同 tools/frame_capture.py）
THUMB_STEP = 16  # 比较"变没变"时隔这么多像素取一个
GAME_CHECK_SECONDS = 5  # 每隔几秒看一眼进程列表
GAME_GONE_CHECKS = 3  # 游戏开过以后，连续这么多次看不到 = 关掉了
STALE_WARN = 30  # 游戏开着时连续这么多次没有新画面（约 1 分钟），在窗口里提示一次
STATUS_SECONDS = 60  # 每隔这么久在窗口里报一次进度
MAX_MINUTES = 60
MAX_MB = 1500
SPARE_MB = 500  # 硬盘至少还要剩"已截的大小 + 这么多"（打包时要再占一份）
CSV_FIELDS = ["序号", "时间", "游戏开着", "结果", "宽", "高", "和上一张差", "文件", "毫秒"]

# 可能盖住游戏画面、或者也在截屏的软件（只比对这几个进程名，不记别的进程）
KNOWN_APPS = {
    "evo.exe": "Evo",
    "discord.exe": "Discord（游戏内悬浮窗）",
    "rtss.exe": "RivaTuner（帧数显示）",
    "msiafterburner.exe": "MSI Afterburner（小飞机）",
    "nvidia overlay.exe": "NVIDIA 游戏内覆盖",
    "gamebar.exe": "Xbox Game Bar",
    "obs64.exe": "OBS（录屏）",
    "overwolf.exe": "Overwolf",
    "medal.exe": "Medal（录屏）",
    "wallpaper32.exe": "Wallpaper Engine",
    "wallpaper64.exe": "Wallpaper Engine",
}
EVO_SETTINGS = ("game_language", "game_aspect", "keep_all_screenshots")  # Evo 设置里跟识别有关的，只带这几项
VIDEO_KEYS = {"setting.defaultres": "宽", "setting.defaultresheight": "高", "setting.fullscreen": "全屏",
              "setting.nowindowborder": "无边框"}  # Apex 视频设置里只取这几项（原则 1 的例外）
LAYERS_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"  # 右键 exe → 属性 → 兼容性
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"  # Evo 的开机自启（见 autostart.py）
EVO_RUN_VALUE = "Evo Tracker"
LOCAL_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Evo"  # 同 paths.LOCAL_DIR
DATA_NAME = "Evo Tracker Data"  # 同 paths.DATA_NAME
POINTER_NAME = "data_location.txt"


# ---------- 显示器（Windows 的显示设置，只读） ----------

class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", ctypes.c_uint32), ("Denominator", ctypes.c_uint32)]


class _PATH_SOURCE(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("statusFlags", ctypes.c_uint32)]


class _PATH_TARGET(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("outputTechnology", ctypes.c_uint32), ("rotation", ctypes.c_uint32), ("scaling", ctypes.c_uint32),
                ("refreshRate", _RATIONAL), ("scanLineOrdering", ctypes.c_uint32), ("targetAvailable", wintypes.BOOL),
                ("statusFlags", ctypes.c_uint32)]


class _PATH(ctypes.Structure):
    _fields_ = [("sourceInfo", _PATH_SOURCE), ("targetInfo", _PATH_TARGET), ("flags", ctypes.c_uint32)]


class _MODE(ctypes.Structure):  # DISPLAYCONFIG_MODE_INFO：只占位（64 字节），不读里面的内容
    _fields_ = [("infoType", ctypes.c_uint32), ("id", ctypes.c_uint32), ("adapterId", _LUID),
                ("data", ctypes.c_uint64 * 6)]


class _HEADER(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int32), ("size", ctypes.c_uint32), ("adapterId", _LUID), ("id", ctypes.c_uint32)]


class _SOURCE_NAME(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("viewGdiDeviceName", ctypes.c_wchar * 32)]


class _TARGET_NAME(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("flags", ctypes.c_uint32), ("outputTechnology", ctypes.c_uint32),
                ("edidManufactureId", ctypes.c_uint16), ("edidProductCodeId", ctypes.c_uint16),
                ("connectorInstance", ctypes.c_uint32), ("monitorFriendlyDeviceName", ctypes.c_wchar * 64),
                ("monitorDevicePath", ctypes.c_wchar * 128)]


class _COLOR_INFO(ctypes.Structure):  # DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO；_2（Windows 11 24H2 起）多一项 activeColorMode
    _fields_ = [("header", _HEADER), ("value", ctypes.c_uint32), ("colorEncoding", ctypes.c_uint32),
                ("bitsPerColorChannel", ctypes.c_uint32), ("activeColorMode", ctypes.c_uint32)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", ctypes.c_wchar * 32)]


QDC_ONLY_ACTIVE_PATHS = 2
GET_SOURCE_NAME, GET_TARGET_NAME, GET_COLOR_INFO, GET_COLOR_INFO_2 = 1, 2, 9, 15
COLOR_MODE_HDR = 2
_MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                      wintypes.LPARAM)

_user32 = ctypes.WinDLL("user32")
_user32.GetDisplayConfigBufferSizes.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                                                ctypes.POINTER(ctypes.c_uint32)]
_user32.QueryDisplayConfig.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p, ctypes.c_void_p]
_user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
_user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, _MONITORENUMPROC, wintypes.LPARAM]
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]


def _hdr(target):
    """一个显示器的 HDR 状态：{"hdr": "开" / "关（支持 HDR）" / "不支持" / ..., "bits": 每种颜色几位}。"""
    info = _COLOR_INFO()
    info.header = _HEADER(GET_COLOR_INFO_2, ctypes.sizeof(_COLOR_INFO), target.adapterId, target.id)
    if _user32.DisplayConfigGetDeviceInfo(ctypes.byref(info)) == 0:  # Windows 11 24H2 起：分得清 HDR 和自动颜色管理
        supported = bool(info.value & (1 << 4))
        text = "开" if info.activeColorMode == COLOR_MODE_HDR else ("关（支持 HDR）" if supported else "不支持")
        return {"hdr": text, "bits": info.bitsPerColorChannel}
    info = _COLOR_INFO()
    info.header = _HEADER(GET_COLOR_INFO, ctypes.sizeof(_COLOR_INFO) - 4, target.adapterId, target.id)
    if _user32.DisplayConfigGetDeviceInfo(ctypes.byref(info)) == 0:
        supported, enabled = bool(info.value & 1), bool(info.value & 2)
        text = "开" if enabled else ("关（支持 HDR）" if supported else "不支持")
        return {"hdr": text, "bits": info.bitsPerColorChannel}
    return {"hdr": "读不到", "bits": None}


def display_paths():
    """Windows 显示设置里正在用的显示器：{"\\\\.\\DISPLAY1": {"name": 显示器型号, "refresh": 刷新率, "hdr", "bits"}}。"""
    n_paths, n_modes = ctypes.c_uint32(), ctypes.c_uint32()
    if _user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), ctypes.byref(n_modes)):
        raise OSError("GetDisplayConfigBufferSizes 失败")
    paths = (_PATH * max(1, n_paths.value))()
    modes = (_MODE * max(1, n_modes.value))()
    if _user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), paths, ctypes.byref(n_modes), modes,
                                  None):
        raise OSError("QueryDisplayConfig 失败")
    found = {}
    for path in paths[:n_paths.value]:
        source = _SOURCE_NAME()
        source.header = _HEADER(GET_SOURCE_NAME, ctypes.sizeof(_SOURCE_NAME), path.sourceInfo.adapterId,
                                path.sourceInfo.id)
        if _user32.DisplayConfigGetDeviceInfo(ctypes.byref(source)):
            continue
        target = _TARGET_NAME()
        target.header = _HEADER(GET_TARGET_NAME, ctypes.sizeof(_TARGET_NAME), path.targetInfo.adapterId,
                                path.targetInfo.id)
        name = target.monitorFriendlyDeviceName if not _user32.DisplayConfigGetDeviceInfo(ctypes.byref(target)) else ""
        rate = path.targetInfo.refreshRate
        found[source.viewGdiDeviceName] = {"name": name or None,
                                           "refresh": round(rate.Numerator / rate.Denominator, 2) if rate.Denominator else None,
                                           **_hdr(path.targetInfo)}
    return found


def monitors():
    """每个显示器：[{"device": "\\\\.\\DISPLAY1", "rect": [左, 上, 右, 下], "primary": 是不是主显示器, "scale": 缩放（%）,
    "name", "refresh", "hdr", "bits"}]。坐标是实际像素（dxcam 已经把本程序设成认得清缩放）。"""
    shcore = ctypes.WinDLL("shcore")
    shcore.GetDpiForMonitor.argtypes = [wintypes.HMONITOR, ctypes.c_int, ctypes.POINTER(wintypes.UINT),
                                        ctypes.POINTER(wintypes.UINT)]
    found = []

    def callback(hmon, _hdc, _rect, _data):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if not _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return True
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        scale = round(dpi_x.value / 96 * 100) if shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x),
                                                                        ctypes.byref(dpi_y)) == 0 else None
        r = info.rcMonitor
        found.append({"device": info.szDevice, "rect": [r.left, r.top, r.right, r.bottom],
                      "primary": bool(info.dwFlags & 1), "scale": scale})
        return True

    _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(callback), 0)
    try:
        extra = display_paths()
    except OSError:
        extra = {}
    for mon in found:
        mon.update(extra.get(mon["device"], {"name": None, "refresh": None, "hdr": "读不到", "bits": None}))
    return found


# ---------- Apex 的设置（只读）、开着的软件 ----------

def apex_video():
    """Apex 视频设置文件里的分辨率和是不是全屏（只取这几项）；没找到文件返回 None。"""
    cfg = read_video_config()
    if cfg is None:
        return None
    return {label: cfg.get(key) for key, label in VIDEO_KEYS.items()}


def compat_flags():
    """Windows 兼容性设置里给 Apex 勾的选项：[(当前用户 / 所有用户, exe 名字, 选项)]。
    "禁用全屏优化"是 DISABLEDXMAXIMIZEDWINDOWEDMODE。只记 exe 的名字，不记它装在哪里。"""
    found = []
    for root, label in ((winreg.HKEY_CURRENT_USER, "当前用户"), (winreg.HKEY_LOCAL_MACHINE, "所有用户")):
        try:
            with winreg.OpenKey(root, LAYERS_KEY) as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    exe = Path(name).name.lower()
                    if exe in GAME_PROCESSES:
                        found.append((label, exe, str(value).strip()))
        except OSError:
            continue
    return found


# ---------- Evo 在哪里（找日志用） ----------

def _exe_path(pid):
    """一个进程的 exe 在哪（只用来找 Evo.exe 自己）；查不到返回 None。"""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION：只能查名字这类信息
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        return Path(buf.value) if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else None
    finally:
        kernel32.CloseHandle(handle)


def evo_exes():
    """电脑上的 Evo.exe：[(路径, 怎么找到的)]。正在运行的，和开机自启里记的。"""
    found = []
    for pid, name in processes():
        if name == "evo.exe":
            path = _exe_path(pid)
            if path is not None:
                found.append((path, "正在运行的 Evo"))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            command = str(winreg.QueryValueEx(key, EVO_RUN_VALUE)[0])
        match = re.match(r'\s*"([^"]+)"|\s*(\S+)', command)
        if match:
            found.append((Path(match.group(1) or match.group(2)), "开机自启"))
    except OSError:
        pass
    return found


def _read_pointer(path):
    try:
        text = Path(path).read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    return Path(text) if text else None


def find_evo_data_dirs(exes=None, local_dir=None):
    """Evo 的数据文件夹：[(文件夹, 怎么找到的)]，里面有 logs\\evo.log 的才算，去掉重复的。
    找法和 paths.resolve_data_dir 一样（不 import 它，见文件开头）：Evo.exe 旁边的 data_location.txt，
    没有就是程序旁边的 Evo Tracker Data；%LOCALAPPDATA%\\Evo 里的指路文件；0.3.4 及以前的 %LOCALAPPDATA%\\Evo。"""
    local = Path(local_dir or LOCAL_DIR)
    candidates = []
    for exe, how in (evo_exes() if exes is None else exes):
        exe_dir = Path(exe).parent
        candidates.append((_read_pointer(exe_dir / POINTER_NAME) or exe_dir.parent / DATA_NAME, how))
    pointer = _read_pointer(local / POINTER_NAME)
    if pointer is not None:
        candidates.append((pointer, "指路文件"))
    found, seen = [], set()
    # 旧的默认位置：别处都没找到才用（数据搬走以后，那里可能还留着以前的日志）
    for folder, how in candidates + ([] if candidates else [(local, "旧的默认位置")]):
        try:
            key = str(Path(folder).resolve()).lower()
        except OSError:
            continue
        if key not in seen and (Path(folder) / "logs" / "evo.log").is_file():
            seen.add(key)
            found.append((Path(folder), how))
    if not found and candidates and (local / "logs" / "evo.log").is_file():
        found.append((local, "旧的默认位置"))
    return found


def evo_settings(folder):
    """Evo 设置里跟识别有关的几项（EVO_SETTINGS）；读不到返回 {}。"""
    try:
        data = json.loads((Path(folder) / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: data[key] for key in EVO_SETTINGS if key in data}


def copy_evo_logs(dirs, out_dir):
    """把 Evo 的日志（logs\\evo.log 和轮换下来的 evo.log.1 等）复制到 out_dir/evo_logs，返回复制了几个。"""
    count = 0
    for index, (folder, _) in enumerate(dirs):
        for path in sorted((Path(folder) / "logs").glob("evo.log*")):
            dest = Path(out_dir) / "evo_logs" / (f"{index + 1}_{path.name}" if len(dirs) > 1 else path.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(path, dest)
                count += 1
            except OSError:
                pass
    return count


# ---------- 截屏 ----------

def _thumb(frame):
    """比较"变没变"用：隔 THUMB_STEP 个像素取一个，转成灰度。"""
    return frame[::THUMB_STEP, ::THUMB_STEP, :3].astype(np.float32).mean(axis=2)


def encode_jpeg(frame, width=None, quality=JPEG_QUALITY, subsampling=2):
    """BGR（dxcam 给的，和 Evo 一样）的截图 → JPEG。width：比它宽就缩到它宽。subsampling：0 = 颜色不压缩（4:4:4）。"""
    img = Image.fromarray(np.ascontiguousarray(frame[:, :, 2::-1]))
    if width and img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.Resampling.BOX)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, subsampling=subsampling)
    return buf.getvalue()


class Recorder:
    """截屏、存图、把每次截屏的结果记进 captures.csv。
    camera：dxcam 的截屏对象（None = 建不起来，只看进程、存缩略图）；names()：现在开着的进程名；
    thumbs(时间)：给每个显示器存缩略图，返回存了多少字节（可以不给）；log(文字)：写进窗口和 tool_log.txt。
    clock、wait(事件, 秒)、now：测试时换成假时钟；free_mb()：硬盘还剩多少 MB。"""

    def __init__(self, out_dir, camera, names, log, thumbs=None, minutes=MAX_MINUTES, max_mb=MAX_MB,
                 games=GAME_PROCESSES, clock=time.monotonic, wait=None, now=datetime.now, free_mb=None):
        self.out_dir, self.camera, self.names, self.log, self.thumbs = Path(out_dir), camera, names, log, thumbs
        self.minutes, self.max_mb = minutes, max_mb
        self.games = {g.lower() for g in games}
        self.clock, self.now = clock, now
        self.wait = wait or (lambda event, seconds: event.wait(seconds))
        self.free_mb = free_mb or (lambda: shutil.disk_usage(self.out_dir).free / 2 ** 20)
        self.game_on = False
        self.gone = 0  # 游戏开过以后，连续几次没看到
        self.games_seen = {}  # 看到过的游戏进程名 → 第一次看到的时间
        self.apps = set()  # 开着的 KNOWN_APPS
        self.grabs = self.saved = self.full = self.same = self.no_new = self.no_new_game = self.errors = 0
        self.thumb_bytes = 0
        self.stale = self.longest_stale = 0  # 游戏开着时连续没有新画面的次数（现在、最长）
        self.sizes = Counter()  # 截到的尺寸 → 次数
        self.nbytes = 0
        self.size = None
        self.last_thumb = None  # 上一张存下的截图的缩略图
        self.last_idle_save = None
        self.next_full = None
        self.started = self.ended = None
        self._csv_file = open(self.out_dir / "captures.csv", "w", newline="", encoding="utf-8-sig")
        self._csv = csv.DictWriter(self._csv_file, CSV_FIELDS)
        self._csv.writeheader()

    def close(self):
        self._csv_file.close()

    @property
    def mb(self):
        return (self.nbytes + self.thumb_bytes) / 2 ** 20

    def run(self, stop):
        """截到 stop（threading.Event）被设上、游戏关掉、到时间、截满或硬盘快满为止，返回结束的原因。"""
        self.started = self.now()
        start = self.clock()
        deadline = start + self.minutes * 60
        next_game = next_thumb = start
        next_status = start + STATUS_SECONDS
        try:
            while True:
                tick = self.clock()
                if stop.is_set():
                    return "按了回车"
                if tick >= deadline:
                    return f"截满了 {self.minutes:g} 分钟"
                if self.mb >= self.max_mb:
                    return f"截图满了 {self.max_mb} MB"
                if self.free_mb() < self.mb + SPARE_MB:
                    return "硬盘空间快不够了"
                if tick >= next_game:
                    next_game = tick + GAME_CHECK_SECONDS
                    if self._check_game():
                        return "游戏关掉了"
                if self.camera is not None:
                    self._grab(tick)
                if self.thumbs is not None and tick >= next_thumb:
                    next_thumb = tick + THUMB_SECONDS
                    self.thumb_bytes += self.thumbs(self.now())
                if tick >= next_status:
                    next_status = tick + STATUS_SECONDS
                    self.log(self.status())
                interval = INTERVAL if self.game_on else IDLE_INTERVAL
                self.wait(stop, max(0.0, interval - (self.clock() - tick)))
        finally:
            self.ended = self.now()

    def status(self):
        state = "游戏开着" if self.game_on else "等游戏打开"
        stale = f"；⚠ 没有新画面 {self.no_new_game} 次" if self.no_new_game else ""
        return f"[{self.now():%H:%M:%S}] {state}：已存 {self.saved} 张（{self.mb:.0f} MB）{stale}"

    def _check_game(self):
        """看一眼进程列表（只看名字）。返回 True = 游戏开过、现在关掉了。"""
        names = {n.lower() for n in self.names()}
        self.apps |= names & KNOWN_APPS.keys()
        running = sorted(names & self.games)
        if running:
            self.gone = 0
            for name in running:
                self.games_seen.setdefault(name, f"{self.now():%H:%M:%S}")
            if not self.game_on:
                self.game_on = True
                self.next_full = None
                self.log(f"[{self.now():%H:%M:%S}] 看到游戏了（{'、'.join(running)}），开始每 {INTERVAL:g} 秒截一张")
            return False
        if self.game_on:
            self.gone += 1
            if self.gone >= GAME_GONE_CHECKS:
                self.game_on = False
                self.log(f"[{self.now():%H:%M:%S}] 游戏关掉了")
                return True
        return False

    def _grab(self, tick):
        self.grabs += 1
        now = self.now()
        row = {"序号": self.grabs, "时间": f"{now:%H:%M:%S}", "游戏开着": "是" if self.game_on else "否"}
        began = time.perf_counter()
        try:
            frame = self.camera.grab(new_frame_only=True)  # 拿不到新画面时返回 None（Evo 这时用的是缓存的上一张）
        except Exception as e:  # 切换分辨率、切出游戏等
            self.errors += 1
            self._row(row, f"出错：{e}", began)
            return
        if frame is None:
            self.no_new += 1
            if self.game_on:
                self.no_new_game += 1
                self.stale += 1
                self.longest_stale = max(self.longest_stale, self.stale)
                if self.stale == STALE_WARN:
                    self.log(f"[{now:%H:%M:%S}] ⚠ 游戏开着，但已经连续 {STALE_WARN} 次没截到新画面：截屏可能卡住了"
                             "（会记在诊断信息里，接着截）")
            self._row(row, "没有新画面", began)
            return
        self.stale = 0
        h, w = frame.shape[:2]
        self.sizes[f"{w}x{h}"] += 1
        if (w, h) != self.size:
            self.size, self.last_thumb = (w, h), None
            self.log(f"[{now:%H:%M:%S}] 截到的画面：{w}x{h}")
        small = _thumb(frame)
        diff = None if self.last_thumb is None else float(np.mean(np.abs(small - self.last_thumb)))
        files = []
        idle_due = self.last_idle_save is None or tick - self.last_idle_save >= IDLE_SAVE_SECONDS
        if diff is not None and diff < SAME:
            self.same += 1
            result = "没变"
        elif self.game_on or idle_due:
            self.saved += 1
            files.append(self._save("frames", f"{self.saved:04d}_{now:%H%M%S}_{w}x{h}.jpg",
                                    encode_jpeg(frame, SMALL_WIDTH)))
            self.last_thumb = small
            if not self.game_on:
                self.last_idle_save = tick
            result = "存了"
        else:
            result = "没存（游戏没开）"
        if self.game_on and (self.next_full is None or tick >= self.next_full):
            self.next_full = tick + FULL_SECONDS
            self.full += 1
            files.append(self._save("full", f"{now:%H%M%S}_{w}x{h}.jpg",
                                    encode_jpeg(frame, None, FULL_QUALITY, subsampling=0)))
        row.update({"宽": w, "高": h, "和上一张差": "" if diff is None else f"{diff:.1f}", "文件": " ".join(files)})
        self._row(row, result, began)

    def _save(self, folder, name, data):
        path = self.out_dir / folder / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        self.nbytes += len(data)
        return f"{folder}/{name}"

    def _row(self, row, result, began):
        row.update({"结果": result, "毫秒": f"{(time.perf_counter() - began) * 1000:.0f}"})
        self._csv.writerow(row)
        self._csv_file.flush()

    def summary(self):
        return {"开始": f"{self.started:%Y-%m-%d %H:%M:%S}" if self.started else None,
                "结束": f"{self.ended:%Y-%m-%d %H:%M:%S}" if self.ended else None,
                "截屏次数": self.grabs, "存了": self.saved, "原尺寸": self.full, "没变没存": self.same,
                "没有新画面": self.no_new, "游戏开着时没有新画面": self.no_new_game,
                "游戏开着时最长连续没有新画面": self.longest_stale, "出错": self.errors,
                "截到的尺寸": dict(self.sizes), "大小MB": round(self.mb, 1),
                "看到的游戏进程": self.games_seen, "开着的软件": sorted(self.apps)}


class MonitorThumbs:
    """每个显示器存一张小缩略图（mss，Windows 自带的截屏方式，和 dxcam 不是一条路），返回存了多少字节。
    看得出游戏在哪个屏幕上、dxcam 卡住时 Windows 自己截不截得到；THUMB_WIDTH 宽，看不清字。"""

    def __init__(self, out_dir, log):
        import mss

        self.out_dir, self.log = Path(out_dir) / "monitors", log
        self.sct = mss.MSS()  # mss 10.2 起的写法（旧的 mss.mss() 已弃用）
        self.monitors = [dict(m) for m in self.sct.monitors[1:]]  # 第 0 个是所有屏幕拼起来的，不要
        self.failed = False

    def __call__(self, now):
        total = 0
        for index, mon in enumerate(self.monitors, 1):
            try:
                shot = self.sct.grab(mon)
                img = Image.frombytes("RGB", shot.size, shot.rgb)
            except Exception as e:
                if not self.failed:
                    self.log(f"[{now:%H:%M:%S}] 缩略图截不到（不影响主要的截图）：{e}")
                    self.failed = True
                continue
            img.thumbnail((THUMB_WIDTH, THUMB_WIDTH), Image.Resampling.BOX)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=80)
            self.out_dir.mkdir(exist_ok=True)
            (self.out_dir / f"{now:%H%M%S}_screen{index}.jpg").write_bytes(buf.getvalue())
            total += buf.tell()
        return total

    def close(self):
        self.sct.close()


def open_camera(log):
    """和 Evo 的记录程序一样建截屏对象（live_collect.collect：主显示器、BGR、numpy）。
    返回 (dxcam 模块或 None, 截屏对象或 None, 建不起来的原因或 None)。"""
    try:
        import dxcam
    except Exception as e:
        return None, None, f"dxcam 加载不了：{e}"
    try:
        return dxcam, dxcam.create(output_color="BGR", processor_backend="numpy"), None
    except Exception as e:
        return dxcam, None, f"截屏对象建不起来（Evo 也会在这里出错）：{e}"


# ---------- 诊断信息 ----------

def _safe(func, *args):
    """读一项信息，出错时返回 {"错误": ...}，不让整个工具停下来。"""
    try:
        return func(*args)
    except Exception as e:
        return {"错误": f"{type(e).__name__}: {e}"}


def collect_info(dxcam_mod, camera, camera_error, evo_dirs):
    """开始时读的信息（info.json 的大部分；截屏结果在结束时加上）。"""
    info = {"工具": {"版本": VERSION},
            "Windows": {"系统": platform.platform(), "版本号": platform.version(), "处理器架构": platform.machine()}}
    dx = {"截屏对象": camera_error or "建好了"}
    if dxcam_mod is not None:
        dx["显卡"] = _safe(lambda: dxcam_mod.device_info().strip())
        dx["输出"] = _safe(lambda: dxcam_mod.output_info().strip())
    if camera is not None:
        output = getattr(camera, "_output", None)
        dx["Evo 截的显示器"] = getattr(output, "devicename", None)
        dx["Evo 截的尺寸"] = [camera.width, camera.height]
    info["dxcam"] = dx
    info["显示器"] = _safe(monitors)
    video = _safe(apex_video)
    info["Apex"] = {"视频设置": video if video is not None else "没找到视频设置文件",
                    "兼容性设置": _safe(compat_flags)}
    info["Evo"] = {"数据文件夹": [[str(folder), how] for folder, how in evo_dirs],
                   "设置": evo_settings(evo_dirs[0][0]) if evo_dirs else {}}
    return info


def _monitor_line(index, mon):
    left, top, right, bottom = mon["rect"]
    parts = [f"{right - left}×{bottom - top}", f"位置 ({left}, {top})"]
    if mon.get("refresh"):
        parts.append(f"刷新率 {mon['refresh']:g} Hz")
    if mon.get("scale"):
        parts.append(f"缩放 {mon['scale']}%")
    parts.append(f"HDR {mon.get('hdr', '读不到')}")
    if mon.get("bits"):
        parts.append(f"每种颜色 {mon['bits']} 位")
    name = f"「{mon['name']}」" if mon.get("name") else ""
    primary = "（主显示器）" if mon["primary"] else ""
    return f"  屏幕 {index}：{mon['device']}{name}{primary}：{'，'.join(parts)}"


def render_info(info):
    """info.json 的内容 → 给人看的 info.txt。"""
    lines = [f"Evo 诊断工具 {info['工具']['版本']}"]
    run = info.get("截屏结果")
    if run:
        lines.append(f"{run['开始']} 到 {run['结束']}，结束原因：{info['工具'].get('结束原因', '-')}")
    win = info["Windows"]
    lines += ["", "[电脑]", f"  {win['系统']}（{win['版本号']}，{win['处理器架构']}）"]
    lines += ["", "[显示器]"]
    if isinstance(info["显示器"], list):
        lines += [_monitor_line(i, m) for i, m in enumerate(info["显示器"], 1)] or ["  （一个都没读到）"]
    else:
        lines.append(f"  读不到：{info['显示器']}")
    dx = info["dxcam"]
    lines += ["", "[dxcam（Evo 用的截屏方式）]", f"  截屏对象：{dx['截屏对象']}"]
    if dx.get("Evo 截的显示器"):
        w, h = dx["Evo 截的尺寸"]
        lines.append(f"  Evo 截的是：{dx['Evo 截的显示器']}，{w}×{h}")
    for key in ("显卡", "输出"):
        if key in dx:
            lines += [f"  {key}："] + [f"    {line}" for line in str(dx[key]).splitlines()]
    apex = info["Apex"]
    video = apex["视频设置"]
    lines += ["", "[Apex]"]
    if isinstance(video, dict) and "错误" not in video:
        lines.append(f"  视频设置：{video.get('宽')}×{video.get('高')}，全屏 {video.get('全屏')}，无边框 {video.get('无边框')}"
                     "（1 = 是）")
    else:
        lines.append(f"  视频设置：{video}")
    flags = apex["兼容性设置"]
    if isinstance(flags, list):
        lines += [f"  兼容性设置（{who}）：{exe}：{value}" + ("  ← 勾了\"禁用全屏优化\""
                                                          if "DISABLEDXMAXIMIZEDWINDOWEDMODE" in value.upper() else "")
                  for who, exe, value in flags] or ["  兼容性设置：没勾任何选项"]
    else:
        lines.append(f"  兼容性设置：读不到（{flags}）")
    if run:
        seen = "、".join(f"{name}（{at} 第一次看到）" for name, at in run["看到的游戏进程"].items())
        lines.append(f"  进程：{seen or '这次没看到游戏开着'}")
        apps = "、".join(KNOWN_APPS.get(a, a) for a in run["开着的软件"])
        lines += ["", "[开着的软件（可能盖住画面或也在截屏，只比对几个已知的名字）]", f"  {apps or '没有'}"]
    evo = info["Evo"]
    lines += ["", "[Evo]"]
    lines += [f"  数据文件夹：{folder}（从{how}找到）" for folder, how in evo["数据文件夹"]] or ["  没找到 Evo（没装，或者装了但没开着）"]
    if evo.get("日志"):
        lines.append(f"  带上了 {evo['日志']} 个日志文件（evo_logs/）")
    if evo["设置"]:
        lines.append("  设置：" + "，".join(f"{k} = {v}" for k, v in evo["设置"].items()))
    if run:
        sizes = "、".join(f"{size}（{n} 次）" for size, n in run["截到的尺寸"].items()) or "-"
        lines += ["", "[截屏结果]",
                  f"  截屏 {run['截屏次数']} 次：存了 {run['存了']} 张（另有原尺寸 {run['原尺寸']} 张），"
                  f"画面没变没存 {run['没变没存']} 次，出错 {run['出错']} 次，共 {run['大小MB']} MB",
                  f"  没有新画面 {run['没有新画面']} 次，其中游戏开着时 {run['游戏开着时没有新画面']} 次"
                  f"（最长连续 {run['游戏开着时最长连续没有新画面']} 次）",
                  f"  截到的尺寸：{sizes}",
                  f"  dxcam 的警告：{info['dxcam'].get('警告', 0)} 条（见 tool_log.txt）"]
    return "\n".join(lines) + "\n"


def write_info(out_dir, info):
    Path(out_dir, "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(out_dir, "info.txt").write_text(render_info(info), encoding="utf-8-sig")


# ---------- 打包 ----------

def make_zip(folder, zip_path):
    """把 folder 打成 zip_path（JPEG 不再压缩，只是装进去），核对条数后删掉 folder。"""
    folder, zip_path = Path(folder), Path(zip_path)
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    part = zip_path.with_name(zip_path.name + ".part")
    with zipfile.ZipFile(part, "w") as zf:
        for path in files:
            kind = zipfile.ZIP_STORED if path.suffix == ".jpg" else zipfile.ZIP_DEFLATED
            zf.write(path, path.relative_to(folder).as_posix(), compress_type=kind)
    with zipfile.ZipFile(part) as zf:
        if len(zf.namelist()) != len(files):
            raise OSError("压缩包里的文件数对不上")
    part.replace(zip_path)
    shutil.rmtree(folder)
    return zip_path


def _known_folder(guid):
    """Windows 的特殊文件夹（如桌面，可能被挪到 OneDrive 里），查不到返回 None。"""
    try:
        buf = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
        path = ctypes.c_wchar_p()
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        if shell32.SHGetKnownFolderPath(ctypes.byref(buf), 0, None, ctypes.byref(path)) == 0:
            found = Path(path.value)
            ctypes.WinDLL("ole32").CoTaskMemFree(path)
            return found if found.is_dir() else None
    except (OSError, AttributeError, ValueError):
        pass
    return None


def default_out_dir():
    """压缩包默认放在桌面；没有桌面就放在本程序旁边。"""
    desktop = _known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")  # FOLDERID_Desktop
    if desktop is not None:
        return desktop
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()


# ---------- 窗口 ----------

class EnterKey:
    """玩家在本窗口按回车（读本窗口自己的输入，不是全局键盘钩子）。pressed：按过回车；alive：还读得到输入。"""

    def __init__(self):
        self.pressed = threading.Event()
        self.alive = sys.stdin is not None
        if self.alive:
            threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while sys.stdin.readline():
                self.pressed.set()
        except (OSError, ValueError):
            pass
        self.alive = False


INTRO = f"""Evo 诊断工具 {VERSION}
============================================================
用 Evo 使用出现问题时，请使用这个工具截一段游戏画面，发给 Evo 开发者帮你排查原因。

使用方法：
  1. 打开 Apex（已经开着也行）。
  2. 正常打一局：大厅、开局、打完后的总结页、经验值页（要手动切过去）多停几秒
  3. 打完回到这个窗口按回车（关掉游戏也会自动结束）。务必不要直接点右上角关掉窗口
  4. 桌面上会出现一个压缩包，直接把它发给 Evo 开发者

另外诊断程序也会记下：显示器和显卡的信息、HDR 开没开、Apex 的分辨率设置，以及电脑上 Evo 的日志。
============================================================
"""  # 2026-10-24 用户定的说法


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evo 诊断工具：截一段游戏画面，连同显示设置、Evo 的日志打成压缩包")
    parser.add_argument("--minutes", type=float, default=MAX_MINUTES, help=f"最多截几分钟（默认 {MAX_MINUTES}）")
    parser.add_argument("--out", type=Path, default=None, help="压缩包放在哪个文件夹（默认桌面）")
    parser.add_argument("--no-wait", action="store_true", help="结束后不等回车、不打开文件夹（打包自检、测试用）")
    parser.add_argument("--game", action="append", default=None,
                        help="把这个进程也当作游戏（测试用，如 explorer.exe；可以写几次）")
    args = parser.parse_args(argv)

    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        ctypes.WinDLL("kernel32").SetConsoleTitleW(f"Evo 诊断工具 {VERSION}")
    except (OSError, AttributeError):
        pass
    set_low_priority()
    print(INTRO)

    stamp = datetime.now()
    out_root = args.out or default_out_dir()
    folder = Path(out_root) / f"Evo诊断_{stamp:%Y%m%d_%H%M%S}"
    folder.mkdir(parents=True, exist_ok=True)
    log_file = open(folder / "tool_log.txt", "a", encoding="utf-8-sig")

    def log(text):
        print(text)
        log_file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
        log_file.flush()

    warnings = []

    class DxcamWarnings(logging.Handler):  # dxcam 自己的警告（截屏卡住、显示器变了时会打）：Evo 的日志里没有
        def emit(self, record):
            warnings.append(record)
            log(f"[{datetime.now():%H:%M:%S}] dxcam 警告：{record.getMessage()}")

    dx_logger = logging.getLogger("dxcam")
    dx_logger.addHandler(DxcamWarnings(logging.WARNING))
    dx_logger.setLevel(logging.WARNING)

    log(f"Evo 诊断工具 {VERSION} 开始，先存在：{folder}")
    dxcam_mod, camera, camera_error = open_camera(log)
    if camera_error:
        log(f"⚠ {camera_error}")
    else:
        log(f"截屏对象建好了：{camera.width}x{camera.height}（和 Evo 截的是同一个显示器）")
    evo_dirs = _safe(find_evo_data_dirs)
    evo_dirs = evo_dirs if isinstance(evo_dirs, list) else []
    log(f"找到了 Evo：{evo_dirs[0][0]}（结束时带上 Evo 的日志）" if evo_dirs
        else "没找到 Evo（没装，或者装了但没开着）：不影响，照样截图")
    info = collect_info(dxcam_mod, camera, camera_error, evo_dirs)
    write_info(folder, info)  # 先写一份：万一中途被关掉，也有这些信息

    thumbs = _safe(MonitorThumbs, folder, log)
    if isinstance(thumbs, dict):
        log(f"⚠ 缩略图截不了（不影响主要的截图）：{thumbs['错误']}")
        thumbs = None
    games = GAME_PROCESSES | {g.lower() for g in (args.game or [])}
    recorder = Recorder(folder, camera, lambda: {name for _, name in processes()}, log, thumbs=thumbs,
                        minutes=args.minutes, games=games)
    enter = EnterKey()
    log("开始了：现在去打游戏吧。打完回到这里按回车。\n")
    try:
        reason = recorder.run(enter.pressed)
    except KeyboardInterrupt:
        reason = "按了 Ctrl+C"
    finally:
        recorder.close()
        if camera is not None:
            camera.release()
        if thumbs is not None:
            thumbs.close()

    print()
    log(f"结束（{reason}）：存了 {recorder.saved} 张（{recorder.mb:.0f} MB），内存峰值 {memory_mb()[1]:.0f} MB")
    if not recorder.games_seen:
        log("⚠ 这次没看到游戏开着，截到的只是桌面。要查 Evo 的问题，请开着游戏再来一次")
    if recorder.no_new_game:
        log(f"⚠ 游戏开着时有 {recorder.no_new_game} 次没截到新画面（切出游戏、画面一动不动时也会这样；"
            "一直在游戏里还这样就是截屏卡住了。会写进诊断信息）")
    info["工具"]["结束原因"] = reason
    info["dxcam"]["警告"] = len(warnings)
    info["截屏结果"] = recorder.summary()
    info["Evo"]["日志"] = copy_evo_logs(evo_dirs, folder) if evo_dirs else 0
    write_info(folder, info)
    log("正在打包……")
    log_file.close()
    try:
        zip_path = make_zip(folder, Path(out_root) / f"{folder.name}.zip")
    except OSError as e:
        print(f"\n打包没成功（{e}）。截图都在这个文件夹里，可以自己压缩后发过来：\n  {folder}")
        zip_path = None
    if zip_path is not None:
        size = zip_path.stat().st_size / 2 ** 20
        print("\n" + "=" * 60)
        print(f"截好了：{zip_path}（{size:.0f} MB）")
        print("把这个压缩包发给 Evo 开发者就行。")
        print("=" * 60)
        if not args.no_wait:
            subprocess.Popen(f'explorer /select,"{zip_path}"')  # 打开文件夹、选中压缩包（explorer 要这种引号写法）
    if not args.no_wait and enter.alive:
        print("\n按回车关闭这个窗口。")
        enter.pressed.clear()
        enter.pressed.wait()
    return zip_path


if __name__ == "__main__":
    main()
