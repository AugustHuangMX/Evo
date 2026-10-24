"""检测 Apex 有没有在运行：看进程列表里有没有游戏的进程名。

只读取进程名（和任务管理器看到的一样），不打开、不读写游戏进程（原则 1）。
用 Windows 自带的功能（ctypes 调用 Toolhelp32），不需要安装别的包。

用法：python code/game_detect.py   → 打印游戏现在是否在运行
"""

import ctypes
from ctypes import wintypes

GAME_PROCESSES = {"r5apex.exe", "r5apex_dx12.exe"}  # Apex 的进程名（不分大小写）

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def processes():
    """当前所有进程：[(进程号, 名字（小写）), ...]，如 [(4321, "r5apex.exe"), ...]。"""
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, INVALID_HANDLE_VALUE):
        return []
    found = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            found.append((entry.th32ProcessID, entry.szExeFile.lower()))
            ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return found


def process_names():
    """当前所有进程的名字（小写），如 {"explorer.exe", "r5apex.exe", ...}。"""
    return {name for _, name in processes()}


def game_running(names=GAME_PROCESSES):
    return bool(process_names() & {n.lower() for n in names})


if __name__ == "__main__":
    found = process_names()
    print(f"当前共有 {len(found)} 个进程。Apex {'正在运行' if game_running() else '没有运行'}。")
