"""一键更新（updater.py、update_check.safe_download、面板上的按钮）。不用截图，不联网：安装包由本机的小服务器提供。

- latest.json 里的安装包信息：只接受 dl.evotracker.cn 的 https；SHA-256、大小格式不对就当作没有（只能去官网下载）
- 下载：下载好、核对、解压后显示"已下载好"；SHA-256 对不上、服务器上没有（404）、压缩包里的路径往外跑、
  没有 Evo.exe 都报错并删掉下了一半的文件；下载中可以取消；不是打包版、没接上托盘、没有安装包信息时只能去官网
- 安装（换文件）：新的换进去，data_location.txt 和不认识的文件不动，旧的挪进 _old_version；
  中途出错就恢复原来的版本；新版本的 Evo.exe 带 --apply-update 时写结果、重新打开 Evo；等进程退出
- 装好以后：新打开的 Evo 读结果（装上了 / 没装上），面板顶部只说一次；清理装过的版本、留着更新的
- 面板：提示条和设置页的各个阶段（立即更新、进度、已下载好、Apex 开着时不能装并写原因、正在安装）

运行：python tests/test_updater.py
"""

import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from unittest import mock

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_settings
import panel_app
import storage
import update_check
import updater

FOLDER = Path(tempfile.mkdtemp(prefix="evo-updater-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里
os.environ["no_proxy"] = "127.0.0.1,localhost"  # 电脑上设了代理时，本机的测试服务器也直接连
GOOD_SHA = "a" * 64


# ---------- 本机的测试服务器 ----------

class Handler(http.server.BaseHTTPRequestHandler):
    files = {}  # 网址路径 → 内容
    slow = set()  # 这些路径慢慢发（测试"取消"）

    def do_GET(self):  # noqa: N802
        data = self.files.get(self.path)
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        step = 64 * 1024 if self.path in self.slow else len(data)
        for i in range(0, len(data), step):
            try:
                self.wfile.write(data[i:i + step])
            except OSError:
                return
            if self.path in self.slow:
                time.sleep(0.05)

    def log_message(self, *_args):
        pass


def start_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def package(files, stored=False):
    """做一个假安装包，返回内容。"""
    path = FOLDER / f"pkg-{time.monotonic_ns()}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path.read_bytes()


GOOD_FILES = {"Evo/Evo.exe": b"new exe", "Evo/_internal/new.txt": b"new", "Evo/LICENSE.txt": b"MIT new",
              "Evo/licenses/README.txt": b"licenses", "Evo/使用说明（先看这个）.txt": "说明".encode("utf-8")}


def latest_for(base, path, data, version, sha=None):
    Handler.files[path] = data
    return {"version": version, "date": "2026-10-19", "page": "https://evotracker.cn/#download", "notes": ["测试"],
            "download": {"url": base + path, "sha256": sha or hashlib.sha256(data).hexdigest(), "size": len(data)}}


def wait_done(latest, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = updater.state(latest)
        if s["phase"] not in ("downloading", "extracting"):
            return s
        time.sleep(0.05)
    return updater.state(latest)


class App:
    """假装是打包好的 Evo：程序文件夹在临时文件夹里，托盘程序接上了（Apex 在不在运行可以改）。"""

    def __init__(self, name):
        self.dir = FOLDER / name
        self.dir.mkdir()
        self.game = False
        self.quit_called = threading.Event()
        self.patches = [mock.patch.multiple(updater, FROZEN=True, EXE_DIR=self.dir, UPDATE_DIR=self.dir / "_update"),
                        mock.patch.object(update_check, "TEST_HOST", "127.0.0.1")]

    def __enter__(self):
        for p in self.patches:
            p.start()
        updater.connect(game_running=lambda: self.game, quit_app=self.quit_called.set)
        return self

    def __exit__(self, *_exc):
        for p in reversed(self.patches):
            p.stop()
        updater._hooks = None
        with updater._lock:
            updater._job.clear()


# ---------- 各项检查 ----------

def download_info_checks():
    ok = {"url": "https://dl.evotracker.cn/Evo-0.3.3-win64.zip", "sha256": GOOD_SHA, "size": 1000}
    check = update_check.safe_download
    return {
        "安装包信息：dl.evotracker.cn 的 https 地址才接受": check(ok) == ok,
        "http、别的网站、SHA-256 不对、大小不对、没有这一项：都当作没有（只能去官网）": all(check(x) is None for x in [
            {**ok, "url": "http://dl.evotracker.cn/a.zip"}, {**ok, "url": "https://evil.example.com/a.zip"},
            {**ok, "url": "https://evotracker.cn.evil.com/a.zip"}, {**ok, "sha256": "abc"}, {**ok, "sha256": "A" * 64},
            {**ok, "size": 0}, {**ok, "size": True}, {**ok, "size": 2 * 1024 ** 3}, {**ok, "size": "1000"}, None, "x"]),
        "没设测试地址时，本机地址也不接受": check({**ok, "url": "http://127.0.0.1:8000/a.zip"}) is None,
    }


def download_checks(base):
    out = {}
    with App("dl") as app:
        good = latest_for(base, "/good.zip", package(GOOD_FILES), "9.9.9")
        out['有安装包信息：显示"立即更新"'] = updater.state(good)["phase"] == "available"
        updater.start_download(good)
        state = wait_done(good)
        out['下载、核对、解压后显示"已下载好"，没下完的文件删掉了'] = (
            state["phase"] == "ready" and updater.staged("9.9.9")
            and (app.dir / "_update" / "9.9.9" / "Evo" / "_internal" / "new.txt").read_bytes() == b"new"
            and not list((app.dir / "_update").glob("*.part")))
        updater.start_download(good)  # 已经下载好了：再点一次什么也不做
        out["已经下载好了再点一次：不重新下载"] = updater.state(good)["phase"] == "ready"

        app.game = True
        out["Apex 开着：已下载好，但写着不能安装的原因"] = "Apex 正在运行" in (updater.state(good)["blocked"] or "")
        try:
            updater.start_install("9.9.9")
            out["Apex 开着时不能安装"] = False
        except updater.UpdateError as e:
            out["Apex 开着时不能安装"] = "Apex 正在运行" in str(e)
        app.game = False

        def fails(latest, words):
            updater.start_download(latest)
            s = wait_done(latest)
            folder = app.dir / "_update" / latest["version"]
            return (s["phase"] == "failed" and words in s["error"] and not folder.exists()
                    and not list((app.dir / "_update").glob("*.part")) and not list((app.dir / "_update").glob("*.tmp")))

        out["SHA-256 对不上：报错，什么也不留"] = fails(latest_for(base, "/bad.zip", package(GOOD_FILES), "9.9.1",
                                                          sha=GOOD_SHA), "SHA-256")
        missing = latest_for(base, "/x.zip", b"", "9.9.2")
        missing["download"].update(url=base + "/missing.zip", size=100)
        out["服务器上没有（404）：报错"] = fails(missing, "HTTP 404")
        out["压缩包里的路径往外跑（../）：报错，外面没多出文件"] = (
            fails(latest_for(base, "/slip.zip", package({"Evo/Evo.exe": b"x", "../evil.txt": b"x"}), "9.9.3"), "位置不对")
            and not (app.dir / "evil.txt").exists() and not (app.dir / "_update" / "evil.txt").exists())
        out["压缩包里没有 Evo.exe：报错"] = fails(latest_for(base, "/noexe.zip", package({"Evo/a.txt": b"x"}), "9.9.4"),
                                               "没有 Evo.exe")
        out["不是 zip：报错"] = fails(latest_for(base, "/junk.zip", b"not a zip" * 100, "9.9.5"), "打不开")

        Handler.slow.add("/slow.zip")
        slow = latest_for(base, "/slow.zip", package({**GOOD_FILES, "Evo/big.bin": os.urandom(2 * 2**20)}, stored=True),
                          "9.9.6")
        updater.start_download(slow)
        end = time.monotonic() + 10
        while updater.state(slow).get("done", 0) == 0 and time.monotonic() < end:
            time.sleep(0.02)
        progress = updater.state(slow)
        updater.cancel_download()
        after = wait_done(slow)
        out["下载中有进度（百分比、MB）"] = progress["phase"] == "downloading" and 0 <= progress["percent"] < 100
        out['取消：回到"立即更新"，没下完的文件删掉了'] = (
            after["phase"] == "available" and not list((app.dir / "_update").glob("*.part")))

        out["下载前检查：程序文件夹空间不够时报错"] = raises(lambda: updater.check_folder(10 ** 15, app.dir), "空间不够")
    with App("manual"):
        good = latest_for(base, "/good.zip", Handler.files["/good.zip"], "9.9.9")
        no_info = {**good, "download": None}
        out["没有安装包信息（旧格式）：只能去官网"] = updater.state(no_info)["phase"] == "manual"
        with mock.patch.object(updater, "FROZEN", False):
            out["用源码运行：只能去官网"] = updater.state(good)["phase"] == "manual"
        updater._hooks = None
        out["没接上托盘程序（单独运行面板）：只能去官网"] = updater.state(good)["phase"] == "manual"
    out["没有新版本：不显示"] = updater.state(None) is None
    return out


def raises(func, words):
    try:
        func()
    except updater.UpdateError as e:
        return words in str(e)
    return False


def make_tree(root, files):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def replace_checks():
    out = {}
    old_files = {"Evo.exe": b"old exe", "_internal/old.txt": b"old", "LICENSE.txt": b"MIT old",
                 "data_location.txt": b"E:\\Evo Tracker Data\n", "my_notes.txt": b"mine", "_update/keep.txt": b"k"}
    new_files = {name.removeprefix("Evo/"): data for name, data in GOOD_FILES.items()}

    target, new = FOLDER / "replace" / "app", FOLDER / "replace" / "new"
    make_tree(target, old_files)
    make_tree(new, new_files)
    updater.replace_files(target, new, log=lambda _m: None)
    out["换好：Evo.exe、_internal、许可证、使用说明都是新的，旧的 _internal 文件没有了"] = (
        (target / "Evo.exe").read_bytes() == b"new exe" and (target / "_internal" / "new.txt").exists()
        and not (target / "_internal" / "old.txt").exists() and (target / "licenses" / "README.txt").exists()
        and (target / "LICENSE.txt").read_bytes() == b"MIT new" and (target / "使用说明（先看这个）.txt").exists())
    out["不动：data_location.txt、不认识的文件、_update"] = (
        (target / "data_location.txt").read_bytes() == old_files["data_location.txt"]
        and (target / "my_notes.txt").read_bytes() == b"mine" and (target / "_update" / "keep.txt").exists())
    out["旧的挪进 _old_version"] = (target / "_old_version" / "Evo.exe").read_bytes() == b"old exe"

    target2 = FOLDER / "replace" / "app2"
    make_tree(target2, old_files)
    real_copy2, calls = shutil.copy2, []

    def flaky_copy2(src, dst, *args, **kwargs):  # 第二个文件复制失败（比如被杀毒软件占着）
        calls.append(src)
        if len(calls) == 2:
            raise PermissionError(13, "拒绝访问", str(dst))
        return real_copy2(src, dst, *args, **kwargs)

    with mock.patch.object(shutil, "copy2", flaky_copy2):
        ok = raises(lambda: updater.replace_files(target2, new, log=lambda _m: None), "已恢复原来的版本")
    out["复制到一半出错：报错，恢复原来的版本（旧文件回来、新文件删掉）"] = (
        ok and (target2 / "Evo.exe").read_bytes() == b"old exe" and (target2 / "_internal" / "old.txt").exists()
        and not (target2 / "_internal" / "new.txt").exists() and not (target2 / "licenses").exists()
        and (target2 / "LICENSE.txt").read_bytes() == b"MIT old"
        and (target2 / "data_location.txt").read_bytes() == old_files["data_location.txt"])

    # 新版本的 Evo.exe 带 --apply-update 时：不真的打开程序，记下要打开什么
    target3 = FOLDER / "replace" / "app3"
    make_tree(target3, {**old_files, "_update/pending.json": b"{}"})
    launched, boxes = [], []
    with mock.patch.object(updater, "EXE_DIR", new), \
            mock.patch.object(updater, "spawn", lambda args, cwd: launched.append((args, Path(cwd)))), \
            mock.patch.object(updater, "_message_box", boxes.append):
        updater.apply_update(target3, None, "0.3.1")
    result = json.loads((target3 / "_update" / "result.json").read_text(encoding="utf-8"))
    out['--apply-update：先说"已经开始"，换好后写结果、去掉"正在安装"、打开程序文件夹里的 Evo.exe'] = (
        (target3 / "_update" / "started").exists() and result == {"from": "0.3.1", "to": storage.APP_VERSION, "ok": True}
        and not (target3 / "_update" / "pending.json").exists() and (target3 / "Evo.exe").read_bytes() == b"new exe"
        and launched == [([str(target3.resolve() / "Evo.exe")], target3.resolve())] and not boxes
        and "安装完成" in (target3 / "_update" / "install.log").read_text(encoding="utf-8"))

    short = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
    long = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        out["等进程退出：退出了返回 True，等不到返回 False"] = (
            updater.wait_exit(short.pid, 10) and not updater.wait_exit(long.pid, 0.3) and updater.wait_exit(None, 1))
    finally:
        long.kill()
    return out


def after_checks():
    out = {}
    with App("after") as app:
        work = app.dir / "_update"
        work.mkdir()
        logs = []
        (work / "result.json").write_text(json.dumps({"from": "0.3.1", "to": storage.APP_VERSION, "ok": True}),
                                          encoding="utf-8")
        (work / "install.log").write_text("安装完成\n", encoding="utf-8")
        with mock.patch.object(updater, "cleanup", lambda: None):
            updater.after_start(logs.append)
        first, second = updater.take_result(), updater.take_result()
        out['装上了：面板顶部说一次"已更新到"，日志里有安装记录，结果文件删掉'] = (
            first == {"ok": True, "version": storage.APP_VERSION, "from": "0.3.1"} and second is None
            and any("安装完成" in m for m in logs) and not (work / "result.json").exists())

        (work / "pending.json").write_text(json.dumps({"from": storage.APP_VERSION, "to": "9.9.9"}), encoding="utf-8")
        with mock.patch.object(updater, "cleanup", lambda: None):
            updater.after_start(logs.append)
        result = updater.take_result()
        out["安装程序没做完（被拦住了）：说没装上"] = (
            result["ok"] is False and result["version"] == "9.9.9" and "杀毒软件" in result["error"])

        make_tree(app.dir, {"_update/0.0.1/Evo/Evo.exe": b"x", f"_update/{storage.APP_VERSION}/Evo/Evo.exe": b"x",
                            "_update/99.0.0/Evo/Evo.exe": b"x", "_old_version/Evo.exe": b"x",
                            "_old_version-20261019/Evo.exe": b"x"})
        updater.cleanup()
        out["清理：删掉装过的版本和换下来的旧版本，留着更新的版本"] = (
            not (work / "0.0.1").exists() and not (work / storage.APP_VERSION).exists()
            and (work / "99.0.0").exists() and not list(app.dir.glob("_old_version*")))
    return out


def panel_checks(base):
    out = {}
    token = panel_app.app.config["TOKEN"]
    client = panel_app.app.test_client()
    with App("panel") as app:
        good = latest_for(base, "/good.zip", Handler.files["/good.zip"], "9.9.9")
        app_settings.set_value("update_latest", good)
        app_settings.set_value("update_auto", True)
        app_settings.set_value("update_dismissed", None)
        page = client.get("/").get_data(as_text=True)
        settings = client.get("/settings").get_data(as_text=True)
        out['有新版本：提示条和设置页都有"立即更新"'] = ">立即更新</button>" in page and "立即更新 · " in settings

        client.post("/update/download", data={"token": token, "next": "/"})
        wait_done(good)
        out["进度接口：下载好以后说 ready"] = client.get("/update/progress").get_json()["phase"] == "ready"
        page = client.get("/").get_data(as_text=True)
        out['下载好以后：提示条有"安装并重新启动"'] = "安装并重新启动" in page and "已下载好" in page

        app.game = True
        page = client.get("/").get_data(as_text=True)
        out["Apex 开着：按钮变灰，旁边写原因"] = (
            'disabled aria-describedby="update-blocked-top"' in page and "Apex 正在运行" in page)
        blocked = client.post("/update/install", data={"token": token, "next": "/"}, follow_redirects=True)
        out["Apex 开着时点安装：不装，说原因"] = "Apex 正在运行" in blocked.get_data(as_text=True) and not app.quit_called.is_set()
        app.game = False

        with mock.patch.object(updater, "_launch_installer", lambda _v: None):
            done = client.post("/update/install", data={"token": token, "next": "/"}, follow_redirects=True)
        text = done.get_data(as_text=True)
        out['点安装：说"正在安装"，1 秒后让托盘程序退出'] = (
            "正在安装 9.9.9" in text and app.quit_called.wait(3)
            and client.get("/update/progress").get_json()["phase"] == "installing")

        out["没有口令不能下载、安装"] = (client.post("/update/download", data={"next": "/"}).status_code == 403
                                 and client.post("/update/install", data={"next": "/"}).status_code == 403)

        app_settings.set_value(updater.RESULT_KEY, {"ok": True, "version": "9.9.9", "from": "0.3.1"})
        first, again = client.get("/").get_data(as_text=True), client.get("/").get_data(as_text=True)
        out['刚更新过：顶部说一次"已更新到"，再打开就不说了'] = "已更新到 Evo" in first and "已更新到 Evo" not in again

    with mock.patch.object(updater, "FROZEN", False):
        page = client.get("/").get_data(as_text=True)
    out["不能在 Evo 里更新时：和以前一样去官网下载"] = "查看更新内容并下载" in page and ">立即更新</button>" not in page
    for key in ("update_latest", "update_auto", "update_dismissed", updater.RESULT_KEY):
        app_settings.set_value(key, None)  # 别影响后面的测试
    return out


def main():
    print("一键更新：下载、核对、安装、恢复、面板")
    server, base = start_server()
    try:
        checks = download_info_checks()
        checks.update(download_checks(base))
        checks.update(replace_checks())
        checks.update(after_checks())
        checks.update(panel_checks(base))
    finally:
        server.shutdown()
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
