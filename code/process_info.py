"""本程序（进程）的内存占用、优先级和省电限速，用 Windows 自带的功能（ctypes），不需要安装别的包。

记录程序用它把自己设成低优先级（CPU 紧张时让游戏优先），读结算画面时不让 Windows 降频（set_full_speed），
并在日志里报告内存。
"""

import ctypes
from ctypes import wintypes

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
PROCESS_POWER_THROTTLING = 4  # SetProcessInformation 的 ProcessPowerThrottling
POWER_THROTTLING_EXECUTION_SPEED = 0x1


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


# 用自己的 WinDLL 对象并声明参数类型，不影响别的代码对 ctypes.windll 的设置
_kernel32 = ctypes.WinDLL("kernel32")
_psapi = ctypes.WinDLL("psapi")
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
_kernel32.SetProcessInformation.restype = wintypes.BOOL
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]


def memory_mb():
    """返回本程序当前内存占用和历史峰值（MB），和任务管理器里看到的"内存"是同一种口径。"""
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    _psapi.GetProcessMemoryInfo(_kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.WorkingSetSize / 2**20, counters.PeakWorkingSetSize / 2**20


def set_low_priority():
    """把本程序设为"低于正常"优先级，CPU 紧张时让游戏优先。"""
    _kernel32.SetPriorityClass(_kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)


def set_full_speed(on):
    """on=True：告诉 Windows 别给本程序降频（省电限速 EcoQoS：笔记本上没有窗口的后台程序可能被压低频率、挪到小核）；
    False：交还给 Windows 决定。优先级不变。成功返回 True（Windows 10 1709 以前没有这个功能，返回 False）。"""
    state = PROCESS_POWER_THROTTLING_STATE(1, POWER_THROTTLING_EXECUTION_SPEED if on else 0, 0)
    return bool(_kernel32.SetProcessInformation(_kernel32.GetCurrentProcess(), PROCESS_POWER_THROTTLING,
                                                ctypes.byref(state), ctypes.sizeof(state)))
