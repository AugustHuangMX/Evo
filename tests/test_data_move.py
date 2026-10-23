"""数据文件夹放在哪里、搬家（paths.py、data_move.py，2026-10-20 用户决定）：

- 找数据文件夹的顺序：环境变量 → 程序旁边的 data_location.txt → C 盘的指路文件 → C 盘旧的默认位置（有数据时，等着搬）
  → 程序旁边的 Evo Tracker Data（在临时文件夹里、写不进去时用 C 盘）
- 检查新位置：现在的位置、互相包含、程序文件夹里、相对路径都不行；那里已有 Evo 的数据时写出几局、不合并
- 搬：复制、核对（文件大小、数据库局数），窗口缓存不搬；核对不过就删掉复制的、原来的不动
- 指路文件：程序旁边和 C 盘各一份；搬回 C 盘默认位置时删掉 C 盘那份
- 新位置的 Evo 打开后：只删旧位置里搬走的和窗口缓存，用户自己的文件不动；结果在窗口顶部说一次
- 整个流程（假装是打包好的 Evo，托盘程序的几个动作换成记录）：Apex 开着不搬；搬的时候按住记录；搬完打开新的 Evo、退出
- 老玩家：数据还在 C 盘旧的默认位置时自动搬到程序旁边；自动搬失败过、程序旁边已有数据时不自动搬
- 用源码运行：安装版搬过家后，跟着 C 盘的指路文件找到新位置
- 设置页：用源码运行时不能改（写出原因）；"下一步"检查后显示结果；搬的时候不让改别的
全部用临时文件夹，不碰正式数据。

运行：python tests/test_data_move.py
"""

import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest import mock

from fake_game import _TEMP, report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_settings
import data_move
import panel_app
import paths
import source_run
import storage

ROOT = Path(tempfile.mkdtemp(prefix="evo-move-", dir=_TEMP))


def fresh(name):
    folder = ROOT / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    return folder


def make_data(folder, games=3):
    """一个假的数据文件夹：数据库（games 局）、截图、日志、设置、自己选的提示音、窗口缓存。"""
    folder = Path(folder)
    conn = storage.connect(folder / "data" / "evo.db")
    for i in range(games):
        storage.insert_match(conn, {"placement": i + 1, "ended_at": f"2026-10-2{i} 21:00:00"})
    conn.close()
    (folder / "captures" / "live" / "run1").mkdir(parents=True)
    (folder / "captures" / "live" / "run1" / "01.png").write_bytes(b"x" * 300_000)
    (folder / "logs").mkdir()
    (folder / "logs" / "evo.log").write_text("记录\n", encoding="utf-8")
    (folder / "settings.json").write_text("{}", encoding="utf-8")
    (folder / "sounds" / "custom").mkdir(parents=True)
    (folder / "sounds" / "custom" / "xp.wav").write_bytes(b"w" * 2000)
    (folder / "webview").mkdir()
    (folder / "webview" / "cache.bin").write_bytes(b"c" * 1000)
    return folder


def games_in(folder):
    conn = sqlite3.connect(Path(folder) / "data" / "evo.db")
    try:
        return conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    finally:
        conn.close()


def resolve_checks():
    out = {}
    exe, local = fresh("r/apps/Evo"), fresh("r/local/Evo")
    out["设了 EVO_DATA_DIR：用它"] = paths.resolve_data_dir({"EVO_DATA_DIR": str(ROOT)}, True, exe, local)[1] == "env"
    out["用源码运行：项目文件夹"] = paths.resolve_data_dir({}, False, exe, local, app_dir=ROOT) == (ROOT, "project")
    with mock.patch.object(paths, "_in_temp", lambda p: False):
        found = paths.resolve_data_dir({}, True, exe, local)
        out[f"打包版、什么都没有：程序旁边的 Evo Tracker Data，并建好文件夹（{found[1]}）"] = (
            found == (exe.parent / "Evo Tracker Data", "default") and found[0].is_dir())
        make_data(local)
        out["C 盘旧的默认位置有数据库：先接着用（等着搬）"] = paths.resolve_data_dir({}, True, exe, local) == (local, "legacy")
        moved = fresh("r/moved/Evo Tracker Data")
        (local / "data_location.txt").write_text(str(moved), encoding="utf-8")
        out["C 盘有指路文件、指的文件夹在：用它"] = paths.resolve_data_dir({}, True, exe, local) == (moved, "pointer")
        shutil.rmtree(moved)
        out["指路文件指的文件夹不在了：不用它"] = paths.resolve_data_dir({}, True, exe, local)[1] == "legacy"
        (exe / "data_location.txt").write_text(str(ROOT / "r" / "E盘"), encoding="utf-8")
        out["程序旁边有 data_location.txt：最优先"] = paths.resolve_data_dir({}, True, exe, local) == (ROOT / "r" / "E盘", "program")
        nowhere = ROOT / "r" / "没有这个盘" / "x" / "Evo"
        shutil.rmtree(local)
        out["程序旁边建不了文件夹：用 C 盘"] = paths.resolve_data_dir({}, True, nowhere, local) == (local, "local")
    out["程序在临时文件夹里（在压缩包里直接双击）：用 C 盘，不在临时文件夹旁边放数据"] = \
        paths.resolve_data_dir({}, True, fresh("r/tmpexe/Evo"), local)[1] == "local"
    return out


def check_checks():
    out = {}
    current, exe = make_data(fresh("c/E/Evo Tracker Data")), fresh("c/E/Evo Tracker")
    out['选 D:\\Games → 放在 D:\\Games\\Evo Tracker Data；选的就叫这个名字时直接用'] = (
        data_move.target_for("D:\\Games") == Path("D:\\Games\\Evo Tracker Data")
        and data_move.target_for('"D:\\Evo Tracker Data"') == Path("D:\\Evo Tracker Data"))
    problem = lambda chosen: data_move.check(chosen, current, exe)["problem"]  # noqa: E731
    out["就是现在的位置：不搬"] = "已经在这里" in problem(current.parent)
    out["在现在的数据文件夹里面：不搬"] = "里面" in problem(current / "data")
    nested = make_data(fresh("c2/Evo Tracker Data/old"))  # 选 c2 → 放在 c2\Evo Tracker Data，现在的数据在它里面
    out["现在的数据文件夹在新位置里面：不搬"] = "里面" in (data_move.check(ROOT / "c2", nested, exe)["problem"] or "")
    out["放在程序文件夹里：不搬（换版本时会被一起删掉）"] = "程序文件夹" in problem(exe)
    out["相对路径：请写完整的路径"] = "完整的路径" in problem("Games")
    out["没写：请先选一个文件夹"] = "请先选" in problem("  ")
    other = make_data(fresh("c/F/Evo Tracker Data"), games=5)
    result = data_move.check(other.parent, current, exe)
    out[f"那里已经有 Evo 的数据：写出几局、最近一局，不算问题（{result['existing']}）"] = (
        result["problem"] is None and result["existing"] == {"games": 5, "last": "2026-10-24 21:00"})
    result = data_move.check(ROOT / "c" / "G", current, exe)
    out[f"能搬：写出要搬多大、那个盘还剩多少，试写时建的文件夹不留下（{result['size_mb']} MB）"] = (
        result["problem"] is None and result["existing"] is None and result["free_gb"] is not None
        and not (ROOT / "c" / "G").exists())
    return out


def move_checks():
    out = {}
    src = make_data(fresh("m/old"))
    target = ROOT / "m" / "new" / "Evo Tracker Data"
    seen = []
    items = data_move.move_files(src, target, progress=lambda done, total: seen.append((done, total)))
    out[f"复制：数据库、截图、日志、设置、自己选的提示音都在，窗口缓存不搬（{items}）"] = (
        items == ["captures", "data", "logs", "settings.json", "sounds"] and games_in(target) == 3
        and (target / "captures" / "live" / "run1" / "01.png").stat().st_size == 300_000
        and (target / "sounds" / "custom" / "xp.wav").stat().st_size == 2000
        and not (target / "webview").exists())
    out["进度：最后一次等于总大小"] = bool(seen) and seen[-1][0] == seen[-1][1] > 0
    out["复制完原来的还在（新位置的 Evo 打开后才删）"] = games_in(src) == 3
    bad = ROOT / "m" / "bad" / "Evo Tracker Data"
    with mock.patch.object(data_move, "_games", side_effect=[3, 2]):
        try:
            data_move.move_files(src, bad)
            failed = False
        except data_move.MoveError as e:
            failed = "数据库对不上" in str(e)
    out["核对不过：报错，复制了一半的删掉，原来的不动"] = failed and not bad.exists() and games_in(src) == 3

    exe, local = fresh("m/exe"), fresh("m/local")
    data_move.write_pointers(target, exe, local)
    out["指路文件：程序旁边和 C 盘各写一份"] = (
        paths.read_pointer(exe / "data_location.txt") == target == paths.read_pointer(local / "data_location.txt"))
    data_move.write_pointers(local, exe, local)
    out["搬回 C 盘默认位置：删掉 C 盘的指路文件"] = not (local / "data_location.txt").exists()
    return out


def after_start_checks():
    out = {}
    old = make_data(fresh("a/old"))
    (old / "我的笔记.txt").write_text("用户自己的文件", encoding="utf-8")
    new = ROOT / "a" / "new" / "Evo Tracker Data"
    items = data_move.move_files(old, new)
    data_move._write_json(new / data_move.MARKER, {"from": str(old), "items": items, "keep_old": False})
    log = []
    data_move.after_start(log.append, new)
    result = data_move.take_result()
    out[f"新位置打开后：删掉旧位置搬走的和窗口缓存，用户自己的文件不动（剩下 {sorted(p.name for p in old.iterdir())}）"] = (
        sorted(p.name for p in old.iterdir()) == ["我的笔记.txt"] and not (new / data_move.MARKER).exists())
    out[f"窗口顶部说一次：搬到了哪里、几局（{result}）"] = (
        result is not None and result["ok"] and result["games"] == 3 and not result["kept"]
        and data_move.take_result() is None and any("删掉了旧位置" in line for line in log))
    shutil.rmtree(old)
    old = make_data(fresh("a/old2"))
    data_move._write_json(new / data_move.MARKER, {"from": str(old), "items": [], "keep_old": True})
    data_move.after_start(lambda _: None, new)
    out["改用那里已有的数据：原来的一样都不删"] = games_in(old) == 3 and data_move.take_result()["kept"]
    return out


class Hooks:
    def __init__(self, game=False):
        self.game, self.calls = game, []

    def connect(self):
        data_move.connect(game_running=lambda: self.game, hold=lambda on: self.calls.append(("hold", on)),
                          quit_app=lambda: self.calls.append(("quit",)), relaunch=lambda auto: self.calls.append(("relaunch",)))


def wait_phase(want, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = data_move.state()
        if job and job["phase"] in want:
            return job
        time.sleep(0.05)
    return data_move.state()


def as_installed(src, exe, local, source="default"):
    """假装是打包好的 Evo、数据在 src。"""
    return [mock.patch.object(paths, "FROZEN", True), mock.patch.object(paths, "DATA_DIR", src),
            mock.patch.object(paths, "DATA_SOURCE", source), mock.patch.object(paths, "EXE_DIR", exe),
            mock.patch.object(paths, "LOCAL_DIR", local), mock.patch.object(data_move, "QUIT_DELAY", 0)]


def flow_checks():
    out = {}
    src, exe, local = make_data(fresh("f/E/Evo Tracker Data")), fresh("f/E/Evo Tracker"), fresh("f/local")
    patches = as_installed(src, exe, local)
    for p in patches:
        p.start()
    try:
        hooks = Hooks(game=True)
        hooks.connect()
        try:
            data_move.start(ROOT / "f" / "D")
            refused = False
        except data_move.MoveError as e:
            refused = "Apex 正在运行" in str(e)
        out["Apex 开着：不搬"] = refused and data_move.state() is None
        hooks.game = False
        data_move.start(ROOT / "f" / "D")
        job = wait_phase(("restarting", "failed"))
        time.sleep(0.2)
        target = ROOT / "f" / "D" / "Evo Tracker Data"
        out[f"搬：按住记录、复制、核对，改指路文件，打开新的 Evo，然后退出（{hooks.calls}）"] = (
            job["phase"] == "restarting" and hooks.calls == [("hold", True), ("relaunch",), ("quit",)]
            and games_in(target) == 3 and (target / data_move.MARKER).is_file()
            and paths.read_pointer(exe / "data_location.txt") == target
            and paths.read_pointer(local / "data_location.txt") == target)
        out["搬的时候：面板问到的是\"正在重新打开\"，不让改别的（busy）"] = data_move.busy()
        with data_move._lock:
            data_move._job.clear()

        hooks2 = Hooks()
        hooks2.connect()
        with mock.patch.object(data_move, "verify", side_effect=data_move.MoveError("核对时发现数据库对不上")):
            data_move.start(ROOT / "f" / "H")
            job = wait_phase(("failed", "restarting"))
        out[f"出错：写出原因，恢复记录，不重新打开，指路文件不改（{job.get('error')}）"] = (
            job["phase"] == "failed" and hooks2.calls == [("hold", True), ("hold", False)]
            and paths.read_pointer(exe / "data_location.txt") == target and not (ROOT / "f" / "H").exists())
        data_move.dismiss()
        out["点\"知道了\"：失败提示消失"] = data_move.state() is None

        other = make_data(fresh("f/K/Evo Tracker Data"), games=7)
        hooks3 = Hooks()
        hooks3.connect()
        try:
            data_move.start(other.parent)
            refused = False
        except data_move.MoveError as e:
            refused = "已经有一份" in str(e)
        data_move.start(other.parent, use_existing=True)
        job = wait_phase(("restarting", "failed"))
        out["那里已有数据：不选\"改用\"就不搬；选了只改指路文件、不复制"] = (
            refused and job["phase"] == "restarting" and games_in(other) == 7
            and paths.read_pointer(exe / "data_location.txt") == other)
        with data_move._lock:
            data_move._job.clear()
    finally:
        for p in patches:
            p.stop()
    return out


def auto_checks():
    out = {}
    exe, local = fresh("u/D/Evo"), make_data(fresh("u/local"))
    patches = as_installed(local, exe, local, source="legacy")
    for p in patches:
        p.start()
    try:
        Hooks().connect()
        with mock.patch.object(paths, "_in_temp", lambda p: False):
            out["数据还在 C 盘旧的默认位置：自动搬到程序旁边的 Evo Tracker Data"] = \
                data_move.auto_target() == exe.parent / "Evo Tracker Data"
            app_settings.set_value(data_move.AUTO_FAILED_KEY, "空间不够")
            out["自动搬失败过：不再自动试"] = data_move.auto_target() is None
            app_settings.set_value(data_move.AUTO_FAILED_KEY, None)
            make_data(exe.parent / "Evo Tracker Data")
            out["程序旁边已经有一份数据：不自动搬（不合并）"] = data_move.auto_target() is None
    finally:
        for p in patches:
            p.stop()
    out["用源码运行（不是打包版）：不自动搬"] = data_move.auto_target() is None
    return out


def relaunch_checks():
    args = ["--game", "notepad.exe", "--background", "--wait-pid", "123", "--no-window"]
    return {"重新打开时带上原来的参数、去掉上次的 --wait-pid；手动搬去掉 --background，自动搬留着": (
        data_move.relaunch_args(args, keep_background=False) == ["--game", "notepad.exe", "--no-window"]
        and data_move.relaunch_args(args, keep_background=True) == ["--game", "notepad.exe", "--background", "--no-window"])}


def source_run_checks():
    out = {}
    folder = fresh("s")
    pointer, local_pointer = folder / "installed_data_location.txt", folder / "local_pointer.txt"
    moved = fresh("s/新位置/Evo Tracker Data")
    pointer.write_text(str(folder / "旧位置"), encoding="utf-8")
    local_pointer.write_text(str(moved), encoding="utf-8")
    out["用源码运行：安装版搬过家（记下的文件夹不在了），跟着 C 盘的指路文件找到新位置"] = \
        source_run.installed_data_dir(pointer, local_pointer) == moved
    (folder / "旧位置").mkdir()
    out["记下的文件夹还在：照旧用它"] = source_run.installed_data_dir(pointer, local_pointer) == folder / "旧位置"
    return out


def panel_checks():
    out = {}
    panel_app.app.config["DB"] = str(_TEMP / "panel-move.db")
    storage.connect(panel_app.app.config["DB"]).close()
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]
    page = client.get("/settings").get_data(as_text=True)
    out["设置页有\"更改数据位置\"；用源码运行时写出不能改的原因、按钮变灰"] = (
        "更改数据位置" in page and "用源码运行" in page and 'id="move-folder" name="folder"' in page
        and 'aria-describedby="move-blocked"' in page)
    src, exe, local = make_data(fresh("p/E/Evo Tracker Data")), fresh("p/E/Evo Tracker"), fresh("p/local")
    patches = as_installed(src, exe, local)
    for p in patches:
        p.start()
    try:
        Hooks().connect()
        client.post("/data/check", data={"token": token, "folder": str(ROOT / "p" / "D")})
        page = client.get("/settings").get_data(as_text=True)
        out["\"下一步\"以后：展开、写出要搬到哪里、有\"开始搬\"按钮"] = (
            '<details class="move-data" id="data-move" open>' in page and "开始搬" in page
            and str(ROOT / "p" / "D" / "Evo Tracker Data") in page)
        client.post("/data/check", data={"token": token, "folder": str(exe)})
        page = client.get("/settings").get_data(as_text=True)
        out["选了程序文件夹：写出不能搬的原因"] = "不能搬到这里" in page and "程序文件夹" in page
        with data_move._lock:
            data_move._job.update(phase="copying", target="D:\\x", done=5 * 2**20, total=10 * 2**20, auto=False,
                                  use_existing=False)
        page = client.get("/settings").get_data(as_text=True)
        progress = client.get("/data/progress").get_json()
        blocked = client.post("/settings/game-aspect", data={"token": token, "aspect": "4:3"}, follow_redirects=True)
        out["正在搬：顶部显示进度，进度接口给出百分比，改别的设置被拦下"] = (
            "data-move-poll=\"copying\"" in page and progress["percent"] == 50
            and "正在搬数据文件夹" in blocked.get_data(as_text=True) and app_settings.get("game_aspect") != "4:3")
        with data_move._lock:
            data_move._job.clear()
    finally:
        for p in patches:
            p.stop()
    return out


def main():
    print("数据文件夹（放在哪里、搬家）")
    checks = {}
    for part in (resolve_checks, check_checks, move_checks, after_start_checks, flow_checks, auto_checks,
                 relaunch_checks, source_run_checks, panel_checks):
        checks.update(part())
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
