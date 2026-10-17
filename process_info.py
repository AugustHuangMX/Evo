"""本程序（进程）的内存占用和优先级，用 Windows 自带的功能（ctypes），不需要安装别的包。

记录程序用它把自己设成低优先级（CPU 紧张时让游戏优先），并在日志里报告内存。
"""

import ctypes
from ctypes import wintypes

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


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
