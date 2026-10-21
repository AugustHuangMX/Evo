"""开机自动启动（2026-10-21 用户决定）：Windows 开机启动项（注册表 HKCU\\...\\Run 里的一条"Evo Tracker"），
开机时运行 "Evo.exe" --background：只进托盘、不弹窗口，打游戏时照常自动记录。

- 只影响当前 Windows 用户，不用管理员权限；任务管理器的"启动应用"里看得到、能关
- 默认关。第一次打开 Evo 时在页面顶部问一次（在"要不要自动检查新版本"之后，一次只问一件事），设置页随时改
- 开关显示 Windows 里实际的状态，不另外记：在任务管理器里关掉了，Windows 记在 ...\\Explorer\\StartupApproved\\Run
  （第一个字节是单数 = 关掉了），设置页也显示关；在 Evo 里重新打开时把这条删掉，Windows 就当作开着
- 只有打包好的 Evo.exe 能设置：用源码运行（开机启动的会是 Python）、在压缩包里直接打开（临时文件夹会被删掉）都不行
- 程序文件夹换了位置（如把新版本解压到别处、删掉旧的）：每次打开 Evo 时看一眼，启动项指向的 Evo.exe 不在了就改成现在这个
- 安全软件（火绒、360）可能拦住写注册表：写完读回来核对，没写上就告诉用户
"""

import os
import sys
import winreg
from pathlib import Path

import paths

VALUE_NAME = "Evo Tracker"
# 测试时换成别的位置，不碰真正的开机启动项
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
EXE = Path(sys.executable).resolve() if paths.FROZEN else None  # 开机要打开的 Evo.exe；用源码运行时没有
ASKED_KEY = "autostart_asked"  # settings.json：第一次打开时问过了（不管选的什么），不再问
FAILED_ON = "没有成功开启自启，请检查是否被杀毒软件拦截"  # 2026-10-21 用户定的说法
FAILED_OFF = "没有成功关闭自启，请检查是否被杀毒软件拦截"


def unavailable():
    """不能设置开机自启的原因；能设置返回 None。"""
    if EXE is None:
        return "用源码运行时不能设置（开机启动的会是 Python），请用安装好的 Evo"
    if paths._in_temp(EXE.parent):
        return "在压缩包里直接打开的 Evo 不能设置：请先解压到一个文件夹，再从那里打开"
    return None


def command(exe):
    return f'"{exe}" --background'


def target(cmd):
    """启动项里的命令 → 要打开的程序（引号里的那一段；没有引号时是第一个空格之前）。"""
    cmd = (cmd or "").strip()
    if not cmd:
        return None
    if cmd.startswith('"'):
        return Path(cmd[1:].split('"', 1)[0])
    return Path(cmd.split(" ", 1)[0])


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _read(key_path, name=VALUE_NAME):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _write(exe):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command(exe))


def _delete(key_path):
    """删掉一个值；本来就没有也算删好了。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass


def status():
    """开机自启现在的样子：
    on（开机会打开这个 Evo）、off_in_windows（启动项是这个 Evo 的，但在任务管理器里被关掉了）、
    other（启动项指向另一个位置的 Evo.exe，开机会打开那一个；没有就是 None）。"""
    exe = target(_read(RUN_KEY))
    if exe is None:
        return {"on": False, "off_in_windows": False, "other": None}
    if EXE is None or not _same(exe, EXE):
        return {"on": False, "off_in_windows": False, "other": str(exe)}
    flag = _read(APPROVED_KEY)
    off = isinstance(flag, bytes) and len(flag) > 0 and flag[0] % 2 == 1
    return {"on": not off, "off_in_windows": off, "other": None}


def enable():
    """开机打开这个 Evo（另一个位置的 Evo 的启动项会被换掉）。成功返回 True；
    写不进去，或者写完读回来不对（多半是被安全软件拦住了）返回 False。"""
    if unavailable():
        return False
    try:
        _write(EXE)
        _delete(APPROVED_KEY)  # 以前在任务管理器里关掉过：在 Evo 里重新打开，就是要它开机启动
    except OSError:
        return False
    return status()["on"]


def disable():
    """开机不再打开这个 Evo。只删指向这个 Evo 的启动项（指向另一个位置的 Evo 不动）。
    删好了（或本来就没有）返回 True；删不掉返回 False。"""
    if unavailable() or status()["other"]:
        return True
    try:
        _delete(RUN_KEY)
        _delete(APPROVED_KEY)
    except OSError:
        return False
    return target(_read(RUN_KEY)) is None


def after_start(log):
    """每次打开 Evo 时：启动项指向的 Evo.exe 已经不在了（程序文件夹换了位置），改成现在这个；把开机自启的状态写进日志。"""
    if unavailable():
        return
    exe = target(_read(RUN_KEY))
    if exe is not None and not _same(exe, EXE) and not exe.exists():
        try:
            _write(EXE)
            log(f"开机自启：原来指向的 {exe} 已经不在了，改成现在的 {EXE}")
        except OSError as e:
            log(f"开机自启：原来指向的 {exe} 已经不在了，想改成现在的 {EXE}，没改成（{e}）")
    now = status()
    if now["on"]:
        log("开机自启：已开启")
    elif now["off_in_windows"]:
        log("开机自启：在 Windows 的启动应用里被关掉了")
    elif now["other"]:
        log(f"开机自启：开机打开的是另一个位置的 Evo（{now['other']}）")
