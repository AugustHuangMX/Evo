"""截屏对象：截主显示器，每张是 BGR 的 numpy 数组。记录程序（live_collect.collect）和测试工具（tester_capture）共用。

先用 dxcam（DXGI 桌面复制：快、省 CPU）。建不起来时（2026-11-03，几位玩家反馈"Evo 一打开 Apex 就识别不到了"：
其实 Evo 认出了游戏，是记录程序一启动就在建截屏对象时出错退出，60 秒后重试又出错，见 docs/识别经验.md）：
1. 报的是 DXGI_ERROR_UNSUPPORTED：双显卡的电脑（多是游戏本）上，屏幕接在核显上、程序却跑在独显上时用不了桌面复制
   （微软 KB3019314）。玩家在 NVIDIA 控制面板里把"首选图形处理器"全局设成独显时，Evo 也会跑在独显上。
   这时在 Windows 的图形设置里把本程序设成「节能」（和玩家自己去 设置 → 系统 → 屏幕 → 显示卡 里设一样，
   只改本程序这一项，不碰游戏）。新开的进程才生效：restart=True 时抛出 NeedRestart，托盘程序马上新开一个记录进程
   （evo_app.Recorder）；测试工具下次打开时生效
2. 已经是「节能」了还是建不起来，或者是别的原因：改用 mss（GDI，Windows 自带的截屏）。每张多约 25 毫秒 CPU，
   对游戏帧数的影响没实测过，所以只在 dxcam 用不了时用
只截屏，不碰游戏（原则 1）。用源码运行时（python.exe 不归 Evo 管）不改图形设置，直接换 mss。

用法：python code/capture.py   → 建一次截屏对象，打印用的是哪种截法、截一张的尺寸
"""

import sys
import winreg
from pathlib import Path

import numpy as np

DXGI_ERROR_UNSUPPORTED = 0x887A0004  # "指定的设备接口或功能级别在此系统上不受支持"
# Windows 的图形设置：每个程序一个值，名字是 exe 的完整路径，内容如 "GpuPreference=1;"（可能还有别的设置）。测试时换成别的位置
GPU_PREFS_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
GPU_PREF = "GpuPreference"
POWER_SAVING = "1"
GPU_PREF_NAMES = {"0": "让 Windows 决定", "1": "节能", "2": "高性能"}


class NeedRestart(Exception):
    """刚把本程序设成了「节能」：新开的进程才生效。"""


def program_exe():
    """要改图形设置的程序：打包好的 Evo.exe、测试工具的 exe。用源码运行时返回 None（python.exe 不归 Evo 管）。"""
    return Path(sys.executable) if getattr(sys, "frozen", False) else None


def hresult(error):
    """COM 出错时的错误码（无符号，如 0x887A0004）；别的错误返回 None。"""
    code = error.args[0] if error.args else None
    return code & 0xFFFFFFFF if isinstance(code, int) else None


def _parts(value):
    """"GpuPreference=2;AppStatus=4096;" → [("GpuPreference", "2"), ("AppStatus", "4096")]，顺序不变。"""
    out = []
    for part in (value or "").split(";"):
        if part.strip():
            name, _, val = part.partition("=")
            out.append((name.strip(), val.strip()))
    return out


def gpu_preference(exe):
    """Windows 图形设置里给 exe 选的："0" 让 Windows 决定、"1" 节能、"2" 高性能；没设过返回 None。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, GPU_PREFS_KEY) as key:
            value = winreg.QueryValueEx(key, str(exe))[0]
    except OSError:
        return None
    return dict(_parts(value)).get(GPU_PREF)


def with_power_saving(value):
    """一个程序的图形设置改成「节能」，别的设置照旧："AppStatus=4096;" → "AppStatus=4096;GpuPreference=1;"。"""
    parts = [(n, POWER_SAVING if n == GPU_PREF else v) for n, v in _parts(value)]
    if GPU_PREF not in dict(parts):
        parts.append((GPU_PREF, POWER_SAVING))
    return "".join(f"{n}={v};" for n, v in parts)


def set_power_saving(exe):
    """在 Windows 图形设置里把 exe 设成「节能」（只影响当前 Windows 用户，不用管理员权限）。写不进去抛出 OSError。"""
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, GPU_PREFS_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
        try:
            old = winreg.QueryValueEx(key, str(exe))[0]
        except FileNotFoundError:
            old = ""
        winreg.SetValueEx(key, str(exe), 0, winreg.REG_SZ, with_power_saving(old))


def _use_power_saving(exe, log, restart):
    """dxcam 报 DXGI_ERROR_UNSUPPORTED 时：把 exe 设成「节能」。返回这次怎么处理的（写进说明）；
    刚设好、restart 时抛出 NeedRestart。"""
    if exe is None:
        return "用源码运行，不改图形设置"
    was = gpu_preference(exe)
    if was == POWER_SAVING:
        return f"{exe.name} 已经是「节能」"
    try:
        set_power_saving(exe)
    except OSError as e:
        return f"想把 {exe.name} 设成「节能」，写不进去（{e}）"
    if gpu_preference(exe) != POWER_SAVING:
        return f"想把 {exe.name} 设成「节能」，写了没生效"
    log(f"截屏：dxcam 建不起来（DXGI_ERROR_UNSUPPORTED：双显卡的电脑上，{exe.name} 跑在了没接屏幕的显卡上）。"
        f"已在 Windows 的图形设置里把 {exe.name} 设成「节能」（原来是{GPU_PREF_NAMES.get(was, '没设过')}；"
        "设置 → 系统 → 屏幕 → 显示卡 里看得到，只改了这一项）")
    if restart:
        raise NeedRestart("截屏：图形设置要新开的记录程序才生效")
    return f"刚把 {exe.name} 设成「节能」（原来是{GPU_PREF_NAMES.get(was, '没设过')}），下次打开生效"


class MssCamera:
    """dxcam 用不了时的截法：mss（GDI，Windows 自带的截屏）截主显示器。用法和 dxcam 的截屏对象一样
    （width、height、grab、release），每次都是现截的（没有"没有新画面"）。要在建它的那个线程里用。"""

    def __init__(self):
        import mss

        try:
            import cv2  # 记录程序里本来就加载了；测试工具不带 OpenCV，用 numpy 转
        except ImportError:
            cv2 = None
        self._cv2 = cv2
        self._sct = mss.MSS()  # mss 10.2 起的写法；它会把本程序设成认得清缩放（截到的是实际像素，和 dxcam 一样）
        monitors = self._sct.monitors[1:]  # 第 0 个是所有屏幕拼起来的
        mon = next((m for m in monitors if m.get("is_primary")), monitors[0])
        self._mon = {k: mon[k] for k in ("left", "top", "width", "height")}
        self.width, self.height = mon["width"], mon["height"]

    def grab(self, new_frame_only=False):
        shot = self._sct.grab(self._mon)
        bgra = np.frombuffer(shot.raw, np.uint8).reshape(shot.height, shot.width, 4)
        if self._cv2 is not None:
            return self._cv2.cvtColor(bgra, self._cv2.COLOR_BGRA2BGR)  # 2560×1440 约 3 毫秒
        return bgra[:, :, :3].copy()  # 测试工具不带 OpenCV：约 25 毫秒

    def release(self):
        self._sct.close()


def open_camera(log=print, restart=True, exe=None):
    """建截屏对象，返回 (截屏对象, 说明)。截屏对象有 width、height、grab(new_frame_only)、release()。
    说明：{"截法": "dxcam" 或 "mss", "dxcam": None 或建不起来的原因, "图形设置": None 或这次怎么处理的}。
    restart：刚设成「节能」时，True = 抛出 NeedRestart（记录程序：托盘程序马上新开一个），False = 这次先用 mss（测试工具）。
    exe：要改图形设置的程序，默认 program_exe()。mss 也建不起来时抛出它的错误。"""
    notes = {"截法": "dxcam", "dxcam": None, "图形设置": None}
    try:
        import dxcam

        return dxcam.create(output_color="BGR", processor_backend="numpy"), notes
    except Exception as e:  # noqa: BLE001 dxcam 加载不了、截屏对象建不起来：下面换办法
        error = e
    notes["dxcam"] = f"{type(error).__name__}: {error}"
    if hresult(error) == DXGI_ERROR_UNSUPPORTED:
        notes["图形设置"] = _use_power_saving(program_exe() if exe is None else exe, log, restart)
    camera = MssCamera()
    notes["截法"] = "mss"
    pref = f"；{notes['图形设置']}" if notes["图形设置"] else ""
    log(f"截屏：dxcam 用不了（{notes['dxcam']}{pref}），改用 mss（Windows 自带的截屏，每张多花一点 CPU）")
    return camera, notes


if __name__ == "__main__":
    cam, how = open_camera()
    frame = cam.grab()
    print(f"截法：{how['截法']}，屏幕 {cam.width}x{cam.height}，截到 {None if frame is None else frame.shape}")
    cam.release()
