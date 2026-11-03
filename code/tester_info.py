"""测试工具（evo_tester.py）用的电脑信息：显示器（分辨率、缩放、主显示器、HDR）、Apex 的视频设置和兼容性设置、
开着的几个已知软件、电脑上的 Evo（数据文件夹、日志、设置里跟识别有关的几项），写成 info.json / info.txt。

只读：只读 Windows 的显示设置、注册表里的兼容性设置、开机自启和图形设置、进程名；只查 Evo.exe 自己在哪个文件夹，不打开游戏进程。
不 import paths.py（它一 import 就会在程序旁边建"Evo Tracker Data"文件夹）：Evo 的数据文件夹按 find_evo_data_dirs 找。
"""

import ctypes
import json
import os
import platform
import re
import shutil
import winreg
from ctypes import wintypes
from pathlib import Path

from capture import GPU_PREF_NAMES, gpu_preference
from game_aspect import ASPECTS, AUTO, SETTING_KEY, from_video_config, read_video_config
from game_detect import GAME_PROCESSES, processes

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
GPU_VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x8086: "Intel", 0x1414: "微软，软件模拟的"}
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


def gpus():
    """电脑上所有的显卡（DXGI 看到的，包括没接显示器的）：["名字（厂商）", ...]。dxcam 只列接着显示器的那块；
    双显卡的笔记本上，程序跑在哪块显卡上，屏幕就算在哪块上（见 capture.py），所以要另外列全。"""
    from dxcam._libs.dxgi import DXGI_ADAPTER_DESC1
    from dxcam.util.io import enum_dxgi_adapters

    names = []
    for adapter in enum_dxgi_adapters():
        desc = DXGI_ADAPTER_DESC1()
        adapter.GetDesc1(ctypes.byref(desc))
        names.append(f"{desc.Description}（{GPU_VENDORS.get(desc.VendorId, f'厂商 {desc.VendorId:#06x}')}）")
    return names


def gpu_preferences(exes):
    """Windows 图形设置里给这几个 Evo.exe 选的显卡：{路径: "节能" / "高性能" / "让 Windows 决定" / "没设过"}。"""
    return {str(exe): GPU_PREF_NAMES.get(gpu_preference(exe), "没设过") for exe, _ in exes}


def evo_settings(folder):
    """Evo 设置里跟识别有关的几项（EVO_SETTINGS）；读不到返回 {}。"""
    try:
        data = json.loads((Path(folder) / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: data[key] for key in EVO_SETTINGS if key in data}


def game_aspect_guess(evo_dirs, video_path=None):
    """游戏画面的宽高比：(宽高比或 None, 说明)，None = 和截图一样。和 Evo 的 game_aspect.current() 一样的顺序：
    Evo 设置页手动选的 → Apex 视频设置（全屏时）→ 和截图一样。Evo 的设置直接读 settings.json（不 import app_settings）。"""
    picked = evo_settings(evo_dirs[0][0]).get(SETTING_KEY, AUTO) if evo_dirs else AUTO
    if picked in ASPECTS:
        return ASPECTS[picked], f"Evo 设置页手动选的 {picked}"
    return from_video_config(video_path)


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


# ---------- 诊断信息 ----------

def _safe(func, *args):
    """读一项信息，出错时返回 {"错误": ...}，不让整个工具停下来。"""
    try:
        return func(*args)
    except Exception as e:
        return {"错误": f"{type(e).__name__}: {e}"}


def collect_info(dxcam_mod, camera, notes, evo_dirs, version="-"):
    """开始时读的信息（info.json 的大部分；截屏结果在结束时加上）。notes：建截屏对象时的说明（capture.open_camera）；
    version：测试工具的版本。"""
    info = {"工具": {"版本": version},
            "Windows": {"系统": platform.platform(), "版本号": platform.version(), "处理器架构": platform.machine()}}
    dx = {"截屏对象": notes["dxcam"] or "建好了", "截法": notes["截法"] or f"建不起来：{notes.get('错误')}"}
    if notes["图形设置"]:
        dx["图形设置"] = notes["图形设置"]
    if dxcam_mod is not None:
        dx["显卡"] = _safe(lambda: dxcam_mod.device_info().strip())
        dx["输出"] = _safe(lambda: dxcam_mod.output_info().strip())
        dx["全部显卡"] = _safe(gpus)
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
                   "设置": evo_settings(evo_dirs[0][0]) if evo_dirs else {},
                   "图形设置": _safe(lambda: gpu_preferences(evo_exes()))}
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
    lines += ["", "[dxcam（Evo 用的截屏方式，用不了时换 mss）]", f"  截屏对象：{dx['截屏对象']}",
              f"  这次的截法：{dx.get('截法', '-')}"]
    if dx.get("图形设置"):
        lines.append(f"  图形设置：{dx['图形设置']}")
    if dx.get("Evo 截的尺寸"):
        w, h = dx["Evo 截的尺寸"]
        lines.append(f"  Evo 截的是：{dx.get('Evo 截的显示器') or '主显示器'}，{w}×{h}")
    for key in ("显卡", "输出"):
        if key in dx:
            lines += [f"  {key}："] + [f"    {line}" for line in str(dx[key]).splitlines()]
    if "全部显卡" in dx:
        found = dx["全部显卡"]
        lines.append(f"  全部显卡（包括没接显示器的）：{'、'.join(found) if isinstance(found, list) else found}")
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
    prefs = evo.get("图形设置")
    if isinstance(prefs, dict):
        lines += [f"  Windows 图形设置：{exe}：{pref}" for exe, pref in prefs.items()]
    elif prefs:
        lines.append(f"  Windows 图形设置：读不到（{prefs}）")
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
