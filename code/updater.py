"""一键更新：在 Evo 里下载新版本、安装、自动重新打开（2026-10-19 用户决定）。

和 update_check.py 一起是 Evo 仅有的联网代码（原则 2）：用户点了"立即更新"才下载安装包，下载完显示
"安装并重新启动"，再点一次才安装。从不在用户不知道时下载或安装。只有打包好的 Evo.exe 能用；用源码运行时只能去官网下载。

流程：
1. 下载（托盘程序里的后台线程，面板每半秒问一次进度）：地址来自 latest.json（只接受 dl.evotracker.cn，见
   update_check.safe_download），边下边写进程序文件夹里的 _update/，边算 SHA-256，和 latest.json 写的对不上就删掉、报错
2. 解压到 _update/<版本号>/Evo/，确认里面有 Evo.exe 和 _internal，写 ready.json（"已下载好"，Evo 重新打开后也认得）
3. 点"安装并重新启动"（Apex 开着时不能点，免得丢掉正在记录的那一局）：打开新版本自己的 Evo.exe --apply-update，
   等它写出 _update/started（说明它真的跑起来了，没被杀毒软件拦住），旧的 Evo 才退出
4. 新版本（在 _update 里运行，apply_update）等旧的 Evo 完全退出，把程序文件夹里要换的东西挪进 _old_version/，
   再把新的复制过去；任何一步出错就把旧的挪回来。然后打开程序文件夹里的 Evo.exe，结果写进 _update/result.json
5. 新打开的 Evo（after_start）读结果，在窗口顶部说"已更新到 x.y.z"或"没装上，已恢复原来的版本"；
   1 分钟后删掉 _update/ 里装过的版本和 _old_version/

要换的东西：新版本压缩包里 Evo 文件夹下的每一项（Evo.exe、_internal、许可证、使用说明），加上 Evo.exe 和 _internal。
不动：data_location.txt（找数据用）、_update、_old_version，和程序文件夹里别的不认识的文件。
为什么放在程序文件夹里而不是数据文件夹：不在 AppData 里运行下载的程序（杀毒软件对这个很敏感），也不占系统盘；
而且下载前已经确认程序文件夹能写入（装在 C:\\Program Files 这类地方的不能一键更新，要去官网下载）。
"""

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from ctypes import wintypes
from datetime import datetime
from pathlib import Path, PurePosixPath

import app_settings
import update_check
from paths import EXE_DIR, FROZEN
from storage import APP_VERSION

UPDATE_DIR = EXE_DIR / "_update"  # 下载、解压、安装记录都在这里
OLD_DIR = "_old_version"  # 安装时旧文件先挪到这里，装好后删掉；出错就挪回去
CHUNK = 256 * 1024
READ_TIMEOUT = 30  # 秒：下载时这么久收不到数据就算断了
STARTED_WAIT = 20  # 秒：新版本的 Evo.exe 这么久还没说"已经开始"，就当作没能启动（多半被杀毒软件拦住了）
QUIT_DELAY = 1.0  # 秒：确认新版本跑起来后，旧的 Evo 等一下再退出，让页面先显示"正在安装"
OLD_EXIT_WAIT = 120  # 秒：新版本最多等旧的 Evo 退出这么久
UNLOCK_WAIT = 30  # 秒：旧的 Evo 刚退出时文件可能还被占着几秒，挪不动就重试
CLEANUP_DELAY = 60  # 秒：Evo 启动后等这么久再删装过的版本（安装程序要先退出）
RESULT_KEY = "update_result"  # app_settings：上次一键更新的结果，窗口顶部显示一次


class UpdateError(Exception):
    """一键更新没法继续，str(e) 是给用户看的原因。"""


class RestoreError(UpdateError):
    """没装上，而且没能恢复原来的版本（只能去官网重新下载）。"""


class _Cancelled(Exception):
    pass


_lock = threading.Lock()
_job = {}  # 正在进行的下载 / 安装：{"phase", "version", "done", "total", "error"}；空 = 没有
_cancel = threading.Event()
_hooks = None  # 托盘程序接上（connect）：{"game_running": 函数, "quit": 函数}；单独运行面板时没有，不能安装


def connect(game_running, quit_app):
    """托盘程序（evo_app.py）启动时接上：怎么知道 Apex 在不在运行、怎么让托盘程序退出。"""
    global _hooks
    _hooks = {"game_running": game_running, "quit": quit_app}


def version_tuple(text):
    return update_check.version_tuple(text)


# ---------- 面板显示什么 ----------

def supported(latest):
    """这个新版本能不能在 Evo 里更新：打包好的 Evo、托盘程序接上了、latest.json 里有安装包信息。"""
    return bool(FROZEN and _hooks is not None and latest and update_check.safe_download(latest.get("download")))


def staged(version):
    """这个版本已经下载、解压好了吗（_update/<版本号>/ready.json）。"""
    folder = UPDATE_DIR / str(version)
    info = _read_json(folder / "ready.json")
    return bool(info and info.get("version") == version and (folder / "Evo" / "Evo.exe").is_file())


def install_blocker():
    """现在不能安装的原因（显示在按钮旁边）；可以安装返回 None。"""
    if _hooks is not None and _hooks["game_running"]():
        return "Apex 正在运行：关掉游戏后再安装，免得丢掉正在记录的这一局"
    return None


def state(latest):
    """面板显示什么。latest 是 update_check.known_newer() 的结果，没有新版本时返回 None。返回字典，phase 是：
    manual（只能去官网下载）/ available（可以点"立即更新"）/ downloading / extracting / failed /
    ready（下载好了，blocked 是现在不能安装的原因）/ installing。"""
    if not latest:
        return None
    version = latest["version"]
    if not supported(latest):
        return {"phase": "manual", "version": version}
    with _lock:
        job = dict(_job)
    if job.get("version") == version and job.get("phase"):
        if job["phase"] == "downloading":
            job["percent"] = min(100, int(job["done"] * 100 / job["total"])) if job["total"] else 0
            job["done_mb"], job["total_mb"] = round(job["done"] / 2**20), round(job["total"] / 2**20)
        return job
    if staged(version):
        return {"phase": "ready", "version": version, "blocked": install_blocker()}
    return {"phase": "available", "version": version,
            "size_mb": round(update_check.safe_download(latest["download"])["size"] / 2**20)}


def take_result():
    """上次一键更新的结果（窗口顶部显示一次就不再显示）：{"ok", "version", "from" 或 "error"}；没有返回 None。"""
    result = app_settings.get(RESULT_KEY)
    if result is not None:
        app_settings.set_value(RESULT_KEY, None)
    return result


# ---------- 下载 ----------

def start_download(latest):
    """点了"立即更新"：在后台开始下载（已经在下载、已经下载好了就什么也不做）。不能下载时抛出 UpdateError。"""
    if not supported(latest):
        raise UpdateError("这个版本不能在 Evo 里更新，请去官网下载")
    info = update_check.safe_download(latest["download"])
    version = latest["version"]
    if version_tuple(version) <= version_tuple(APP_VERSION):
        raise UpdateError(f"已经是 {APP_VERSION}，不用更新")
    with _lock:
        if _job.get("phase") in ("downloading", "extracting", "installing") or staged(version):
            return
        _job.clear()
        _job.update(phase="downloading", version=version, done=0, total=info["size"])
        _cancel.clear()
    threading.Thread(target=_download, args=(version, info), name="update-download", daemon=True).start()


def cancel_download():
    """下载中点了"取消"：下一块数据到了就停下，删掉没下完的文件。"""
    _cancel.set()


def _set_job(**fields):
    with _lock:
        _job.update(fields)


def _end_job(error=None):
    """下载结束：成功或取消就清掉（面板看 ready.json 显示"已下载好"），失败就留着原因。"""
    with _lock:
        if error:
            _job.update(phase="failed", error=error)
        else:
            _job.clear()


def _download(version, info):
    part = UPDATE_DIR / f"Evo-{version}.zip.part"
    try:
        check_folder(info["size"])
        UPDATE_DIR.mkdir(exist_ok=True)
        digest, done = hashlib.sha256(), 0
        request = urllib.request.Request(info["url"], headers={"User-Agent": f"Evo/{APP_VERSION}"})
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT) as response, open(part, "wb") as f:
            while block := response.read(CHUNK):
                if _cancel.is_set():
                    raise _Cancelled
                done += len(block)
                if done > info["size"]:
                    raise UpdateError("下载的文件比应有的大，已经停下。请稍后再试，或者去官网下载")
                f.write(block)
                digest.update(block)
                _set_job(done=done)
        if done != info["size"] or digest.hexdigest() != info["sha256"]:
            raise UpdateError("下载的文件不完整或被改动过（SHA-256 对不上），请重试")
        _set_job(phase="extracting")
        extract(part, UPDATE_DIR / version, version)
        _end_job()
    except _Cancelled:
        _end_job()
    except UpdateError as e:
        _end_job(str(e))
    except (OSError, zipfile.BadZipFile) as e:
        _end_job(_describe(e))
    finally:
        part.unlink(missing_ok=True)


def _describe(e):
    """下载、解压出错时给用户看的原因。"""
    if isinstance(e, urllib.error.HTTPError):
        return f"服务器上没有找到安装包（HTTP {e.code}），请稍后再试，或者去官网下载"
    if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
        return "网络太慢或断了（超时），请重试"
    if isinstance(e, urllib.error.URLError):
        return "连不上下载服务器（网络不通，或者服务器暂时不可用），请稍后再试"
    if isinstance(e, zipfile.BadZipFile):
        return "安装包打不开（文件坏了），请重试"
    return f"写入文件失败（{e.strerror or e}），请重试"


def check_folder(size, folder=None):
    """下载前确认程序文件夹能写入、硬盘空间够。不够时抛出 UpdateError。
    空间按压缩包的 5 倍算：压缩包 + 解压出来约 2.2 倍 + 换的时候再复制一份进程序文件夹。"""
    folder = folder or EXE_DIR
    probe = folder / f".evo-write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        raise UpdateError(f"Evo 所在的文件夹（{folder}）不能写入，没法在 Evo 里更新。请去官网下载新版本") from None
    need, free = size * 5, shutil.disk_usage(folder).free
    if free < need:
        raise UpdateError(f"硬盘空间不够：更新要约 {need / 2**20:.0f} MB，{folder.anchor} 只剩 {free / 2**20:.0f} MB")


def extract(zip_path, target, version):
    """解压到 target（里面是 Evo/ 文件夹），确认有 Evo.exe 和 _internal，最后写 ready.json。
    先解压到 target.tmp，全部成功再改名，所以 target 要么完整、要么没有。"""
    tmp = target.with_name(target.name + ".tmp")
    for folder in (tmp, target):
        shutil.rmtree(folder, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                parts = PurePosixPath(name).parts
                if not parts or parts[0] != "Evo" or ".." in parts or "\\" in name or ":" in name:
                    raise UpdateError("安装包里的文件位置不对，已经停下")
            z.extractall(tmp)
        if not (tmp / "Evo" / "Evo.exe").is_file() or not (tmp / "Evo" / "_internal").is_dir():
            raise UpdateError("安装包里没有 Evo.exe，已经停下")
        _write_json(tmp / "ready.json", {"version": version})
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


# ---------- 安装（旧的 Evo 里） ----------

def start_install(version):
    """点了"安装并重新启动"：打开新版本的 Evo.exe 帮忙安装，确认它跑起来了，1 秒后让托盘程序退出。
    出问题抛出 UpdateError（旧的 Evo 照常运行）。"""
    if not FROZEN or _hooks is None:
        raise UpdateError("只有打包好的 Evo 能在 Evo 里更新")
    reason = install_blocker()
    if reason:
        raise UpdateError(reason)
    if not staged(version):
        raise UpdateError("新版本还没下载好")
    with _lock:
        if _job.get("phase") in ("downloading", "extracting", "installing"):
            raise UpdateError("正在下载或安装，请稍等")
        _job.clear()
        _job.update(phase="installing", version=version)
    try:
        check_folder(0)
        _launch_installer(version)
    except BaseException:
        with _lock:
            _job.clear()
        raise
    threading.Timer(QUIT_DELAY, _hooks["quit"]).start()


def _launch_installer(version):
    exe = UPDATE_DIR / version / "Evo" / "Evo.exe"
    started, pending = UPDATE_DIR / "started", UPDATE_DIR / "pending.json"
    for name in ("started", "result.json", "install.log"):
        (UPDATE_DIR / name).unlink(missing_ok=True)
    # 安装程序万一半路被杀掉、没写结果，下次打开 Evo 时靠这个知道"上次更新没有完成"
    _write_json(pending, {"from": APP_VERSION, "to": version, "time": f"{datetime.now():%Y-%m-%d %H:%M:%S}"})
    blocked = ("安装程序没能启动，可能被杀毒软件拦住了。可以把 Evo 所在的文件夹加入杀毒软件的信任后重试，"
               "或者去官网下载新版本")
    try:
        proc = spawn([str(exe), "--apply-update", str(EXE_DIR), "--wait-pid", str(os.getpid()),
                      "--from-version", APP_VERSION], exe.parent)
    except OSError:
        pending.unlink(missing_ok=True)
        raise UpdateError(blocked) from None
    deadline = time.monotonic() + STARTED_WAIT
    while not started.exists():
        if proc.poll() is not None or time.monotonic() > deadline:
            if proc.poll() is None:
                proc.kill()
            pending.unlink(missing_ok=True)
            raise UpdateError(blocked)
        time.sleep(0.1)


def spawn(args, cwd):
    """另开一个独立的进程：打开它的 Evo 退出后，它照样运行。
    去掉 PyInstaller 留在环境变量里的东西（PYINSTALLER_RESET_ENVIRONMENT）：新开的是另一个 Evo.exe，不是自己的子进程。"""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_PYI_", "_MEIPASS"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    kwargs = {"cwd": str(cwd), "env": env, "close_fds": True, "stdin": subprocess.DEVNULL,
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:  # 所在的"作业"不允许脱离（比如从某些终端里打开的 Evo）：不脱离也能用
        return subprocess.Popen(args, creationflags=flags, **kwargs)


# ---------- 安装（新版本的 Evo.exe --apply-update 里） ----------

def apply_update(target, wait_pid, from_version):
    """新版本的 Evo.exe 带 --apply-update 运行时做的事（它在 _update/<版本号>/Evo 里运行，EXE_DIR 是那里）：
    等旧的 Evo 退出，换掉程序文件，打开换好的 Evo。结果写进程序文件夹的 _update/result.json，过程写进 install.log。"""
    target = Path(target).resolve()
    work = target / "_update"
    log = _file_log(work / "install.log")
    log(f"开始安装 {APP_VERSION}（原来是 {from_version}）：{EXE_DIR} → {target}")
    (work / "started").write_text("", encoding="utf-8")  # 告诉旧的 Evo：已经跑起来了，可以退出了
    result = {"from": from_version, "to": APP_VERSION, "ok": False}
    try:
        if not wait_exit(wait_pid, OLD_EXIT_WAIT):
            raise _OldStillRunning("原来的 Evo 2 分钟内没有退出，没有安装")
        replace_files(target, EXE_DIR, log)
        result["ok"] = True
        log("安装完成")
    except _OldStillRunning as e:  # 旧的还开着：什么也没动，也不能再开一个
        result["error"] = str(e)
        log(str(e))
        _finish(work, result)
        _message_box(f"Evo 更新没有完成：{e}。请右键托盘图标退出 Evo，再重新打开。")
        return
    except RestoreError as e:  # 程序文件夹可能缺东西，打不开了
        result["error"] = str(e)
        log(f"没装上，也没能恢复：\n{traceback.format_exc()}")
        _finish(work, result)
        _message_box(f"{e}\n\n请去官网 evotracker.cn 重新下载 Evo。你的战绩数据不受影响。")
        return
    except Exception as e:  # noqa: BLE001 不管什么错都要写结果、把 Evo 重新打开（已经恢复成原来的版本）
        result["error"] = str(e) or type(e).__name__
        log(f"没装上：\n{traceback.format_exc()}")
    _finish(work, result)
    log("重新打开 Evo")  # 先写：新打开的 Evo 会读完、删掉这份记录，打开以后再写就可能留下一个删不掉的新文件
    try:
        spawn([str(target / "Evo.exe")], target)
    except OSError as e:
        log(f"重新打开 Evo 失败：{e}")
        _message_box(f"Evo {'已更新到 ' + APP_VERSION if result['ok'] else '更新没装上'}，但没能自动打开。请双击 {target / 'Evo.exe'} 打开。")


class _OldStillRunning(Exception):
    pass


def _finish(work, result):
    """结果写进 result.json（新打开的 Evo 读），去掉"正在安装"的标记。"""
    _write_json(work / "result.json", result)
    (work / "pending.json").unlink(missing_ok=True)


def wait_exit(pid, timeout):
    """等这个进程退出，最多 timeout 秒。退出了（或者本来就没有）返回 True。"""
    if not pid:
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE：只能等它退出，别的什么也做不了
    if not handle:
        return True  # 已经退出了
    try:
        return k32.WaitForSingleObject(handle, int(timeout * 1000)) == 0  # WAIT_OBJECT_0
    finally:
        k32.CloseHandle(handle)


def kept(name):
    """程序文件夹里安装时不动的东西。"""
    return name in ("_update", "data_location.txt") or name.startswith(OLD_DIR)


def replace_files(target, new, log=print):
    """把 new（新版本的 Evo 文件夹）里的东西换进 target（程序文件夹）。旧的先挪进 target/_old_version，
    全部复制好才算装上；中途出错就删掉复制了一半的新文件、把旧的挪回来，抛出 UpdateError。
    连恢复都失败时抛出 RestoreError。"""
    names = sorted(p.name for p in new.iterdir() if not kept(p.name))
    replace = sorted({*names, "Evo.exe", "_internal"})
    old = target / OLD_DIR
    if old.exists():  # 上次留下的，还没删掉
        shutil.rmtree(old, ignore_errors=True)
        if old.exists():
            old = target / f"{OLD_DIR}-{time.strftime('%Y%m%d-%H%M%S')}"
    old.mkdir()
    moved, copied = [], []
    try:
        for name in replace:
            if (target / name).exists():
                _retry(os.replace, target / name, old / name)  # 旧的 Evo 刚退出时，文件可能还被占着几秒
                moved.append(name)
        log(f"旧文件已挪进 {old.name}：{'、'.join(moved)}")
        for name in names:
            copied.append(name)
            src, dst = new / name, target / name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        log(f"新文件已复制：{'、'.join(names)}")
    except Exception as e:
        log(f"出错了，恢复原来的版本：{e!r}")
        try:
            for name in copied:
                _remove(target / name)
            for name in moved:
                os.replace(old / name, target / name)
        except OSError as restore_error:
            raise RestoreError(f"更新没装上，也没能恢复原来的版本（{restore_error}）") from e
        reason = e.strerror if isinstance(e, OSError) and e.strerror else e
        raise UpdateError(f"更新没装上，已恢复原来的版本（{reason}）") from e


def _retry(func, *args):
    deadline = time.monotonic() + UNLOCK_WAIT
    while True:
        try:
            return func(*args)
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def _remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _message_box(text):
    """安装程序没有窗口，出了大问题只能弹个框告诉用户。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "Evo 更新", 0x30)  # 黄色感叹号
    except (AttributeError, OSError):
        print(text)


def _file_log(path):
    path.parent.mkdir(parents=True, exist_ok=True)

    def log(msg):
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")

    return log


# ---------- 装好以后（新打开的 Evo 里） ----------

def after_start(log):
    """打包好的 Evo 启动时调用：上次一键更新的结果写进日志、交给面板显示一次；1 分钟后清理装过的版本。"""
    result, pending = _read_json(UPDATE_DIR / "result.json"), _read_json(UPDATE_DIR / "pending.json")
    info = result or pending
    if info:
        details = _read_text(UPDATE_DIR / "install.log")
        if info.get("to") == APP_VERSION:  # 现在运行的就是要装的版本：装上了
            message = {"ok": True, "version": APP_VERSION, "from": info.get("from")}
            log(f"一键更新：已从 {info.get('from')} 更新到 {APP_VERSION}")
        else:
            error = (result or {}).get("error") or "安装程序没有做完，可能被杀毒软件拦住了"
            message = {"ok": False, "version": info.get("to"), "error": error}
            log(f"一键更新：{info.get('to')} 没装上（{error}）")
        if details:
            log("安装记录：\n" + details.rstrip())
        app_settings.set_value(RESULT_KEY, message)
    for name in ("result.json", "pending.json", "install.log", "started"):
        (UPDATE_DIR / name).unlink(missing_ok=True)
    timer = threading.Timer(CLEANUP_DELAY, cleanup)
    timer.daemon = True  # 1 分钟内退出 Evo 就不等它了，下次启动再删
    timer.start()


def cleanup():
    """删掉 _update 里装过的（不比现在新的）版本，和换下来的旧版本。删不掉的（还被占用）下次启动再删。"""
    current = version_tuple(APP_VERSION)
    if UPDATE_DIR.is_dir():
        for p in UPDATE_DIR.iterdir():
            v = version_tuple(p.name)
            if p.is_dir() and v is not None and v <= current:
                shutil.rmtree(p, ignore_errors=True)
        try:
            UPDATE_DIR.rmdir()  # 空了就删掉
        except OSError:
            pass
    for p in EXE_DIR.glob(f"{OLD_DIR}*"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def _read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
