"""数据文件夹搬家（2026-10-20 用户决定）：设置页"更改位置"，和老玩家从 C 盘自动搬到程序旁边（paths.py 第 3 条）。

用户选一个文件夹（如 D:\\Games），数据放进它下面的 Evo Tracker Data（选的就叫这个名字时直接用它）。
和一键更新一样在托盘程序的后台线程里做，面板每半秒问一次进度（state）：
1. 检查（check）：Apex 开着不搬（免得丢掉正在记录的这一局）；不能是现在的位置、不能在现在的数据文件夹里面
   （或反过来）、不能在程序文件夹里（换版本时会被一起删掉）；要能写入、空间够（数据大小 × 1.1 + 50 MB）。
   那里已经有 Evo 的数据：不合并、不覆盖，问用户"改用那份"还是取消（2026-10-20 用户决定）
2. 搬（_run）：暂停记录 → 复制 Evo 自己的数据库、截图、运行记录、设置、自己选的提示音（数据库用 SQLite 的备份功能，其他文件逐个复制；
   窗口的缓存 webview\\ 正在用，不搬；数据文件夹里别的东西不碰）→
   核对（每个文件的大小、数据库的局数和完整性）→ 在新位置写 _moved_from.json → 改指路文件（Evo.exe 旁边和
   %LOCALAPPDATA%\\Evo 里的 data_location.txt，见 paths.py）→ 打开新的 Evo（--wait-pid 等这个退出）→ 托盘程序退出。
   任何一步出错：删掉复制了一半的东西，指路文件不改，接着用原来的位置
3. 新位置的 Evo 打开后（after_start）：数据库读得出来，就删掉旧位置里搬走的那些（只删搬走的和窗口缓存，别的不动；
   文件夹空了也删掉），顶部提示一次（2026-10-20 用户决定：新位置打开成功后自动删）
改用那里已有的数据（use_existing）：不复制，只改指路文件、重新打开；现在的数据留在原处，不删。
只有打包好的 Evo 能搬（用源码运行时数据在安装版的数据文件夹，见 source_run.py）。
"""

import json
import os
import shutil
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import app_settings
import paths

MARKER = "_moved_from.json"  # 搬完写在新位置：从哪里搬来的、搬了哪些（新的 Evo 打开后据此删旧的）
# 只搬 Evo 自己的这几样，数据文件夹里别的东西不碰（万一数据文件夹指到了放着别的东西的地方）。
# 窗口缓存 webview\\ 正在用，不搬（新位置会重新生成），旧的在新的 Evo 打开后删掉
ITEMS = ("data", "captures", "logs", "settings.json", "sounds")  # sounds：设置页选的自己的提示音、调过音量的副本（sounds.py）
SQLITE_TEMP = (".db-journal", ".db-wal", ".db-shm")  # 数据库的临时文件：数据库用备份功能复制，这些不要
CHUNK = 1024 * 1024
QUIT_DELAY = 1.5  # 秒：新的 Evo 打开后，旧的等一下再退出，让页面先显示"正在重新打开"
OLD_EXIT_WAIT = 120  # 秒：新的 Evo 最多等旧的退出这么久
DELETE_RETRY = 10  # 秒：旧的刚退出时文件可能还被占着几秒，删不掉就重试
RESULT_KEY = "data_move_result"  # app_settings：上次搬家的结果，窗口顶部显示一次
AUTO_FAILED_KEY = "data_move_auto_failed"  # app_settings：自动搬到程序旁边失败过（不再自动试，设置页可以手动改）

_hooks = None  # 托盘程序接上（connect）：{"game_running", "hold", "quit", "relaunch"}；单独运行面板时没有，不能搬
_lock = threading.Lock()
_job = {}  # 正在搬：phase（copying / verifying / restarting / failed）、target、done、total、error、auto


class MoveError(Exception):
    """没法搬，str(e) 是给用户看的原因。"""


def connect(game_running, hold, quit_app, relaunch):
    """托盘程序启动时接上：game_running() Apex 在不在运行；hold(True/False) 暂停 / 恢复记录；
    quit_app() 像点了"退出"一样退出；relaunch(auto) 打开新的 Evo（它会等这个退出；auto：自动搬的）。"""
    global _hooks
    _hooks = {"game_running": game_running, "hold": hold, "quit": quit_app, "relaunch": relaunch}


def supported():
    """能不能在 Evo 里搬：打包好的 Evo、托盘程序在运行、数据文件夹不是环境变量指定的（测试、源码运行）。"""
    return bool(paths.FROZEN and _hooks is not None and paths.DATA_SOURCE != "env")


def unsupported_reason():
    if not paths.FROZEN or paths.DATA_SOURCE == "env":
        return "用源码运行（或用环境变量指定了数据文件夹）时不能在这里改位置"
    if _hooks is None:
        return "单独运行面板时不能改位置，请从 Evo 打开"
    return None


def blocker():
    """现在不能搬的原因（显示在按钮旁边）；可以搬返回 None。"""
    reason = unsupported_reason()
    if reason:
        return reason
    if _hooks["game_running"]():
        return "Apex 正在运行：关掉游戏后再搬，免得丢掉正在记录的这一局"
    if busy():
        return "正在搬，请稍等"
    return None


def busy():
    with _lock:
        return _job.get("phase") in ("copying", "verifying", "restarting")


def state():
    """面板显示什么：没在搬（也没失败）返回 None；否则 {"phase", "target", "percent", "done_mb", "total_mb", "error"}。"""
    with _lock:
        job = dict(_job)
    if not job:
        return None
    if job.get("total"):
        job["percent"] = min(100, int(job["done"] * 100 / job["total"]))
        job["done_mb"], job["total_mb"] = round(job["done"] / 2**20), max(1, round(job["total"] / 2**20))
    else:
        job["percent"], job["done_mb"], job["total_mb"] = 0, 0, 0
    return job


def dismiss():
    """失败提示上点了"知道了"。"""
    with _lock:
        if _job.get("phase") == "failed":
            _job.clear()


def take_result():
    """上次搬家的结果（窗口顶部显示一次就不再显示）；没有返回 None。"""
    result = app_settings.get(RESULT_KEY)
    if result is not None:
        app_settings.set_value(RESULT_KEY, None)
    return result


# ---------- 检查 ----------

def target_for(chosen):
    """用户选的文件夹 → 数据放在哪里：它下面的 Evo Tracker Data；选的就叫这个名字时直接用它。"""
    chosen = Path(str(chosen).strip().strip('"'))
    return chosen if chosen.name.lower() == paths.DATA_NAME.lower() else chosen / paths.DATA_NAME


def _inside(a, b):
    """a 在 b 里面（或就是 b）。"""
    try:
        Path(a).resolve().relative_to(Path(b).resolve())
        return True
    except ValueError:
        return False


def _files(src):
    """要搬的文件（相对路径），和一共多大。"""
    files, total = [], 0
    if not Path(src).is_dir():
        return files, total
    for item in (Path(src) / name for name in ITEMS):
        if not item.exists():
            continue
        for p in ([item] if item.is_file() else sorted(q for q in item.rglob("*") if q.is_file())):
            if p.name.endswith(SQLITE_TEMP):
                continue
            files.append(p.relative_to(src))
            total += p.stat().st_size
    return files, total


def _existing_ancestor(path):
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def describe_data(folder):
    """一个数据文件夹里有几局、最近一局什么时候（"那里已经有一份 Evo 的数据"时给用户看）。读不出来返回 None。"""
    db = Path(folder) / "data" / "evo.db"
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            games, last = conn.execute("SELECT COUNT(*), MAX(ended_at) FROM matches").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return {"games": None, "last": None}
    return {"games": games, "last": (last or "")[:16] or None}


def check(chosen, current=None, exe_dir=None):
    """检查能不能搬到 chosen（用户选的文件夹）。返回 {"target", "problem"（不能搬的原因或 None）,
    "existing"（那里已有的数据：局数、最近一局，或 None）, "size_mb", "free_gb"}。current、exe_dir 是测试时替换用的。"""
    current = Path(current or paths.DATA_DIR)
    exe_dir = Path(exe_dir or paths.EXE_DIR)
    out = {"chosen": str(chosen).strip(), "target": None, "problem": None, "existing": None, "size_mb": 0, "free_gb": None}
    if not out["chosen"]:
        out["problem"] = "请先选一个文件夹"
        return out
    target = target_for(chosen)
    out["target"] = str(target)
    if not target.is_absolute() or not target.drive:
        out["problem"] = "请写完整的路径，比如 D:\\Games"
        return out
    _, size = _files(current)
    out["size_mb"] = round(size / 2**20)
    if target.resolve() == current.resolve():
        out["problem"] = "数据已经在这里了"
    elif _inside(target, current) or _inside(current, target):
        out["problem"] = "新位置不能在现在的数据文件夹里面，现在的数据文件夹也不能在新位置里面"
    elif _inside(target, exe_dir):
        out["problem"] = "这里在 Evo 的程序文件夹里，换新版本时会被一起删掉，请选程序文件夹外面的地方"
    if out["problem"]:
        return out
    existing = describe_data(target)
    if existing is not None:
        out["existing"] = existing  # 不合并、不覆盖：让用户选"改用那份"或者取消
        return out
    try:
        free = shutil.disk_usage(_existing_ancestor(target)).free
    except OSError:
        out["problem"] = f"找不到这个位置：{target.drive}"
        return out
    out["free_gb"] = round(free / 2**30, 1)
    need = size * 1.1 + 50 * 2**20
    if free < need:
        out["problem"] = f"空间不够：要约 {round(need / 2**20)} MB，那个盘只剩 {round(free / 2**20)} MB"
        return out
    problem = _writable(target)
    if problem:
        out["problem"] = problem
    return out


def _writable(target):
    """试着在 target 里建一个文件再删掉；不行返回原因。试的时候建的文件夹不留下。"""
    created = []
    folder = Path(target)
    try:
        missing = []
        p = folder
        while not p.exists():
            missing.append(p)
            p = p.parent
        for p in reversed(missing):
            p.mkdir()
            created.append(p)
        probe = folder / ".evo-write-test"
        probe.write_bytes(b"ok")
        probe.unlink()
        return None
    except OSError as e:
        return f"这个位置不能写入（{e.strerror or e}）。可以换一个文件夹，比如 D 盘下面新建的文件夹"
    finally:
        for p in reversed(created):
            try:
                p.rmdir()
            except OSError:
                pass


# ---------- 搬 ----------

def start(chosen, use_existing=False, auto=False):
    """开始搬（后台线程）。use_existing：那里已经有 Evo 的数据时改用那份（不复制）。auto：自动从 C 盘搬到程序旁边。
    不能搬时抛出 MoveError。"""
    reason = blocker()
    if reason:
        raise MoveError(reason)
    result = check(chosen)
    if result["problem"]:
        raise MoveError(result["problem"])
    if result["existing"] is not None and not use_existing:
        raise MoveError("那里已经有一份 Evo 的数据：请选\"改用那份数据\"，或者换一个文件夹")
    if use_existing and result["existing"] is None:
        raise MoveError("那里没有 Evo 的数据")
    target = Path(result["target"])
    with _lock:
        if _job.get("phase") in ("copying", "verifying", "restarting"):
            raise MoveError("正在搬，请稍等")
        _job.clear()
        _job.update(phase="copying", target=str(target), done=0, total=0, auto=auto, use_existing=use_existing)
    threading.Thread(target=_run, args=(paths.DATA_DIR, target, use_existing, auto), name="data-move",
                     daemon=True).start()


def _set(**fields):
    with _lock:
        _job.update(fields)


def _run(src, target, use_existing, auto):
    _hooks["hold"](True)
    log = _log()
    try:
        if use_existing:
            items = []
            log(f"数据文件夹：改用 {target} 里已有的数据（原来的 {src} 留着不动）")
        else:
            log(f"数据文件夹：开始从 {src} 搬到 {target}")
            items = move_files(src, target, progress=lambda done, total: _set(done=done, total=total),
                               on_verify=lambda: _set(phase="verifying"))
        _write_json(target / MARKER, {"from": str(src), "items": items, "keep_old": use_existing, "auto": auto,
                                      "time": f"{datetime.now():%Y-%m-%d %H:%M:%S}"})
        write_pointers(target)
        _set(phase="restarting")
        log(f"数据文件夹：已经{'搬' if not use_existing else '改'}到 {target}，重新打开 Evo")
        _hooks["relaunch"](auto)
        threading.Timer(QUIT_DELAY, _hooks["quit"]).start()
    except Exception as e:  # noqa: BLE001  出错：数据还在原来的位置，接着用
        error = str(e) if isinstance(e, MoveError) else f"出错了：{e}"
        log(f"数据文件夹：没搬成（{error}），接着用原来的位置 {src}\n{traceback.format_exc()}")
        if auto:
            app_settings.set_value(AUTO_FAILED_KEY, error)
        _hooks["hold"](False)
        _set(phase="failed", error=error)


def move_files(src, target, progress=None, on_verify=None):
    """把 src 里要搬的东西复制到 target 并核对，返回搬了的顶层项（名字列表，新的 Evo 打开后据此删旧的）。
    出错就删掉这次复制的、建的，抛出 MoveError。progress(已复制, 一共)：字节数。"""
    src, target = Path(src), Path(target)
    files, total = _files(src)
    created = _topmost_missing(target)  # 这次要新建的最上面一层文件夹（出错时连它一起删掉）；target 已经有了就是 None
    copied = []
    try:
        target.mkdir(parents=True, exist_ok=True)
        done = 0
        for rel in files:
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if rel.as_posix() == "data/evo.db":
                _backup_db(src / rel, dest)
                done += (src / rel).stat().st_size
            else:
                done = _copy(src / rel, dest, done, total, progress)
            copied.append(rel)
            if progress:
                progress(done, total)
        if on_verify:
            on_verify()
        verify(src, target, files)
    except Exception as e:
        if created is not None:
            shutil.rmtree(created, ignore_errors=True)
        else:
            for rel in copied:
                (target / rel).unlink(missing_ok=True)
        if isinstance(e, MoveError):
            raise
        raise MoveError(f"复制时出错：{e}") from e
    return sorted({rel.parts[0] for rel in files})


def _topmost_missing(path):
    """path 和它上面还不存在的文件夹里，最上面的那一层；path 已经存在返回 None。"""
    path, top = Path(path), None
    while not path.exists() and path.parent != path:
        top, path = path, path.parent
    return top


def _copy(src, dest, done, total, progress):
    with open(src, "rb") as fin, open(dest, "wb") as fout:
        while True:
            chunk = fin.read(CHUNK)
            if not chunk:
                break
            fout.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    shutil.copystat(src, dest)
    return done


def _backup_db(src, dest):
    """数据库用 SQLite 自己的备份功能复制：就算面板这时正在读，复制出来的也是完整的一份。"""
    source = sqlite3.connect(src)
    try:
        copy = sqlite3.connect(dest)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()


def verify(src, target, files):
    """核对：每个文件都在、大小一样（数据库看局数和完整性）。对不上抛出 MoveError。"""
    for rel in files:
        a, b = Path(src) / rel, Path(target) / rel
        if not b.is_file():
            raise MoveError(f"核对时发现少了文件：{rel}")
        if rel.as_posix() == "data/evo.db":
            if _games(a) != _games(b) or _integrity(b) != "ok":
                raise MoveError("核对时发现数据库对不上")
        elif a.stat().st_size != b.stat().st_size:
            raise MoveError(f"核对时发现文件大小对不上：{rel}")


def _games(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    finally:
        conn.close()


def _integrity(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def write_pointers(target, exe_dir=None, local_dir=None):
    """改指路文件：Evo.exe 旁边的 data_location.txt（程序文件夹写不进去就算了），和 %LOCALAPPDATA%\\Evo 里的那份
    （数据搬回 C 盘默认位置时删掉它）。见 paths.py。"""
    exe_dir, local_dir = Path(exe_dir or paths.EXE_DIR), Path(local_dir or paths.LOCAL_DIR)
    text = f"{Path(target)}\n"
    try:
        (exe_dir / paths.POINTER_NAME).write_text(text, encoding="utf-8")
    except OSError:
        pass
    local = local_dir / paths.POINTER_NAME
    if Path(target).resolve() == local_dir.resolve():
        local.unlink(missing_ok=True)
    else:
        local_dir.mkdir(parents=True, exist_ok=True)
        local.write_text(text, encoding="utf-8")


def relaunch(auto=False):
    """打开新的 Evo（打包好的 Evo.exe），让它等这个退出后再启动（--wait-pid），用新的数据文件夹。
    带上这次启动时的参数（如测试用的 --no-window、--game）；用户在窗口里点的"开始搬"，去掉 --background，
    搬完要看得到窗口；开机时自动搬的，留着（不弹窗口）。"""
    import updater  # 用它的 spawn：去掉 PyInstaller 的环境变量，新开的 Evo 不是自己的子进程

    args = relaunch_args(sys.argv[1:], keep_background=auto)
    updater.spawn([sys.executable, *args, "--wait-pid", str(os.getpid())], paths.EXE_DIR)


def relaunch_args(argv, keep_background):
    """重新打开时带上的参数：去掉上次的 --wait-pid；keep_background=False 时也去掉 --background。"""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a == "--wait-pid":
            skip = True
        elif not a.startswith("--wait-pid=") and (keep_background or a != "--background"):
            out.append(a)
    return out


# ---------- 新的 Evo 打开后 ----------

def after_start(log, data_dir=None):
    """新位置的 Evo 打开后调用（托盘程序启动时）：刚搬过家就删掉旧位置里搬走的东西，结果交给面板显示一次。"""
    data_dir = Path(data_dir or paths.DATA_DIR)
    marker = data_dir / MARKER
    info = _read_json(marker)
    if info is None:
        return
    old = Path(info.get("from", ""))
    message = {"ok": True, "to": str(data_dir), "from": str(old), "kept": bool(info.get("keep_old")), "left": []}
    try:
        games = _games(data_dir / "data" / "evo.db") if (data_dir / "data" / "evo.db").is_file() else 0
    except sqlite3.Error as e:
        log(f"数据文件夹：新位置 {data_dir} 的数据库读不出来（{e}），旧位置 {old} 的数据先不删")
        message.update(ok=False, kept=True)
        app_settings.set_value(RESULT_KEY, message)
        marker.unlink(missing_ok=True)
        return
    if not info.get("keep_old") and str(old) and old.resolve() != data_dir.resolve():
        left = delete_old(old, info.get("items", []))
        message["left"] = left
        log(f"数据文件夹：已经搬到 {data_dir}（{games} 局），删掉了旧位置 {old} 里的数据"
            + (f"；这些没删掉：{'、'.join(left)}" if left else ""))
    else:
        log(f"数据文件夹：改用 {data_dir}（{games} 局）；原来位置 {old} 的数据留着")
    message["games"] = games
    app_settings.set_value(RESULT_KEY, message)
    app_settings.set_value(AUTO_FAILED_KEY, None)
    marker.unlink(missing_ok=True)


def delete_old(old, items, retry=DELETE_RETRY):
    """删掉旧位置里搬走的顶层项和窗口缓存（别的不动）；文件夹空了也删掉。返回没删掉的项。"""
    old = Path(old)
    left = []
    for name in [*items, "webview"]:
        path = old / name
        if not path.exists():
            continue
        deadline = time.monotonic() + retry
        while True:
            try:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
                break
            except OSError:
                if time.monotonic() > deadline:
                    left.append(name)
                    break
                time.sleep(0.5)
    try:
        old.rmdir()  # 空了才删得掉（C 盘默认位置里留着指路文件，就不会空）
    except OSError:
        pass
    return left


# ---------- 老玩家：自动从 C 盘搬到程序旁边 ----------

def auto_target():
    """要不要自动搬：数据还在 C 盘旧的默认位置（paths.py 的 "legacy"）、程序旁边能放、以前没自动搬失败过。
    要搬返回程序旁边的文件夹，不搬返回 None。"""
    if paths.DATA_SOURCE != "legacy" or not supported() or app_settings.get(AUTO_FAILED_KEY):
        return None
    folder = paths.default_data_dir(paths.EXE_DIR)
    if folder is None or paths.has_data(folder):  # 程序旁边已经有一份数据：不合并，不自动搬（设置页可以手动改）
        return None
    return folder


def watch_auto_move(log, stop, check_seconds=30):
    """托盘程序启动后在后台等着：要自动搬、Apex 没在运行时就开始搬（搬完 Evo 会重新打开一次）。"""
    def loop():
        target = auto_target()
        if target is None:
            return
        log(f"数据文件夹：数据还在 C 盘旧的默认位置 {paths.DATA_DIR}，等 Apex 没在运行时自动搬到 {target}")
        while not stop.is_set():
            if not _hooks["game_running"]() and not busy():
                try:
                    start(target, auto=True)
                except MoveError as e:
                    log(f"数据文件夹：自动搬到 {target} 没开始（{e}），接着用 {paths.DATA_DIR}；可以在设置页手动改")
                    app_settings.set_value(AUTO_FAILED_KEY, str(e))
                return
            stop.wait(check_seconds)

    threading.Thread(target=loop, name="data-auto-move", daemon=True).start()


# ---------- 小工具 ----------

def _log():
    """写进 Evo 的运行记录（托盘程序的 logger）。"""
    import logging

    return logging.getLogger("evo").info


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
