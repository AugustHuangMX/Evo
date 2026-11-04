"""国内渠道（Mirror酱，mirrorchyan.py、update_check、updater、update_patch.compose、设置页、release.make_zip）。
不联网：Mirror酱 的接口和下载都由本机的小服务器假扮。

- 问 Mirror酱：请求带现在的版本（v 开头）、windows、amd64、evo_desktop，不带 CDK；带了 CDK 才有下载信息；
  更新内容只取"新增""改进""修复"；下载信息不对（http、SHA-256、大小）当作没有；各个错误代码给出对应的原因；
  连不上、不是 JSON 也不出错；出错信息里没有 CDK 和请求地址；核对 CDK 记进设置
- 检查更新：latest.json 读不到时用 Mirror酱 查到的版本（没有国际渠道的安装包信息）；读得到时用 latest.json 的
- 一键更新（国内渠道）：没填 CDK 时不能下；完整包下好、核对、解压；增量包（路径带不带 Evo/ 都行）照文件清单拼出
  新版本，删掉的旧文件没了；本机的文件对不上、changes.json 有越界路径、没有文件清单、SHA-256 对不上、CDK 过期、
  Mirror酱 上还没有这个版本：都停下写明原因，不自动改用国际渠道；下载地址过期了再要一次；
  "这次改用国际渠道下载"从 dl.evotracker.cn 下；日志里没有 CDK 和下载地址
- 设置页：更新渠道两个选项、点国内渠道时的说明（用户的原文）、"不再提示"、CDK 输入框（页面里没有 CDK 本身）、
  保存并核对 CDK、清除 CDK、格式不对不保存；提示条：没填 CDK、国内渠道失败后"这次改用国际渠道下载"
- 发版：压缩包里有所有文件的清单（Evo/_internal/evo_files.json），大小和 SHA-256 都对，不列它自己

运行：python tests/test_mirrorchyan.py
"""

import hashlib
import http.server
import json
import shutil
import sys
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path
from unittest import mock

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_settings  # noqa: E402
import mirrorchyan  # noqa: E402
import panel_app  # noqa: E402
import update_check  # noqa: E402
import update_patch  # noqa: E402
import updater  # noqa: E402
from storage import APP_VERSION  # noqa: E402
from test_updater import FOLDER, App, make_tree, tree  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CDK = "TEST-CDK-1234567890"
NEW = "9.9.9"
NOTE = ("## 新增\r\n- 战绩页可以改地图\r\n\r\n## 改进\r\n- 页面不再跳动\r\n\r\n## 别的\r\n- 不要这条\r\n\r\n"
        "## 修复\r\n- 修了一个问题\r\n\r\n---\r\n- 下载说明也不要")


# ---------- 假的 Mirror酱 ----------

class Mirror(http.server.BaseHTTPRequestHandler):
    """/api/... 回 answer（(HTTP 状态, 内容)，内容是字典就当 JSON）；别的路径从 files 里取（下载）。"""
    answer = (200, {})
    files = {}
    expire_once = set()  # 这些下载路径第一次回 403（下载地址过期了）
    asked = []  # 问接口时带的参数
    downloads = []  # 下载过的路径

    def do_GET(self):  # noqa: N802
        path, _, query = self.path.partition("?")
        if path.startswith("/api/"):
            self.asked.append(dict(urllib.parse.parse_qsl(query, keep_blank_values=True)))
            status, body = self.answer
            data = json.dumps(body).encode("utf-8") if isinstance(body, dict) else body
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.downloads.append(path)
        if path in self.expire_once:
            self.expire_once.discard(path)
            self.send_error(403)
            return
        data = self.files.get(path)
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


def ok_answer(version=f"v{NEW}", download=None):
    data = {"version_name": version, "release_note": NOTE, "channel": "stable", "os": "windows", "arch": "amd64"}
    if download:
        data.update(download)
    return 200, {"code": 0, "msg": "ok", "data": data}


def offer(base, path, data, kind="full"):
    """Mirror酱 回答里的下载信息（下载地址带一个假的签名参数，模拟有时效的地址）。"""
    Mirror.files[path] = data
    return {"url": f"{base}{path}?sign=SECRET-SIGN", "sha256": hashlib.sha256(data).hexdigest(), "filesize": len(data),
            "update_type": kind, "cdk_expired_time": 1796400000}


# ---------- 假的新旧两个版本 ----------

OLD_FILES = {"Evo.exe": b"old exe", "_internal/keep.txt": b"keep", "_internal/gone.txt": b"gone",
             "LICENSE.txt": b"license"}
NEW_FILES = {"Evo/Evo.exe": b"new exe", "Evo/_internal/keep.txt": b"keep", "Evo/_internal/added.txt": b"added",
             "Evo/LICENSE.txt": b"license"}


def manifest(files, version=NEW):
    return json.dumps({"version": version, "files": {name: [len(data), hashlib.sha256(data).hexdigest()]
                                                     for name, data in files.items()}}).encode("utf-8")


NEW_TREE = {**NEW_FILES, update_patch.MANIFEST: manifest(NEW_FILES)}  # 新版本装好后的样子


def zipped(files):
    path = FOLDER / f"mirror-{time.monotonic_ns()}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path.read_bytes()


def incremental(prefix="", changes=None, files=None, with_manifest=True):
    """Mirror酱 的增量包：变了的文件 + 清单 + changes.json。prefix：路径前面带不带 "Evo/"。"""
    inside = files if files is not None else {"Evo/Evo.exe": b"new exe", "Evo/_internal/added.txt": b"added"}
    if with_manifest:
        inside = {**inside, update_patch.MANIFEST: NEW_TREE[update_patch.MANIFEST]}
    out = {prefix + name.removeprefix("Evo/"): data for name, data in inside.items()}
    out["changes.json"] = json.dumps(changes or {
        "added": ["_internal/added.txt"], "modified": ["Evo.exe", "_internal/evo_files.json"],
        "deleted": ["_internal/gone.txt"]}).encode("utf-8")
    return zipped(out)


def start():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mirror)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def latest_info(base):
    """latest.json 的样子（国际渠道的安装包放在本机服务器的 /intl.zip）。"""
    data = zipped(NEW_TREE)
    Mirror.files["/intl.zip"] = data
    return {"version": NEW, "date": "2026-11-04", "page": "https://evotracker.cn/#download", "notes": ["测试"],
            "download": {"url": f"{base}/intl.zip", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                         "mirrors": [], "patches": {}}}


def settle(latest, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = updater.state(latest)
        if state["phase"] not in ("downloading", "extracting"):
            return state
        time.sleep(0.05)
    return updater.state(latest)


def fresh(app):
    """每项检查前：程序文件夹是旧版本，没有下载过的东西，没有正在进行的下载。"""
    for path in list(app.dir.iterdir()):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    make_tree(app.dir, OLD_FILES)
    with updater._lock:
        updater._job.clear()
    Mirror.asked.clear()
    Mirror.downloads.clear()


def cn(key=CDK):
    app_settings.set_value(mirrorchyan.CHANNEL_KEY, "cn")
    app_settings.set_value(mirrorchyan.CDK_KEY, key)


# ---------- 各项检查 ----------

def client_checks(base):
    api = f"{base}/api/resources/Evo/latest"
    out = {}
    with mock.patch.object(mirrorchyan, "API", api):
        Mirror.asked.clear()
        Mirror.answer = ok_answer()
        plain = mirrorchyan.latest()
        out["不带 CDK：带现在的版本（v 开头）、windows、amd64、evo_desktop，不带 cdk"] = Mirror.asked[-1] == {
            "current_version": f"v{APP_VERSION}", "os": "windows", "arch": "amd64", "user_agent": "evo_desktop"}
        out["版本号去掉 v；更新内容只取新增、改进、修复（到 --- 为止）；没有下载信息"] = (
            plain["version"] == NEW and plain["download"] is None
            and plain["notes"] == ["新增：战绩页可以改地图", "改进：页面不再跳动", "修复：修了一个问题"])

        good = offer(base, "/pkg.zip", b"x" * 100, "incremental")
        Mirror.answer = ok_answer(download=good)
        withkey = mirrorchyan.latest(CDK)
        out["带 CDK：请求里有 cdk，回答里的下载信息（地址、SHA-256、大小、增量包）、CDK 到期日"] = (
            Mirror.asked[-1].get("cdk") == CDK and withkey["download"] == {
                "url": good["url"], "sha256": good["sha256"], "size": 100, "update_type": "incremental"}
            and withkey["cdk_expires"] is not None)
        bad = [{**good, "url": "http://example.com/x.zip"}, {**good, "url": "ftp://example.com/x.zip"}, {**good, "url": "https://user:pw@example.com/x.zip"},
               {**good, "sha256": "abc"}, {**good, "filesize": 0}, {**good, "filesize": True}, {**good, "filesize": "100"},
               {**good, "filesize": 2 * 1024 ** 3}]
        results = []
        for one in bad:
            Mirror.answer = ok_answer(download=one)
            results.append(mirrorchyan.latest(CDK)["download"])
        out["下载信息不对（http、带用户名密码、SHA-256、大小）：当作没有"] = results == [None] * len(bad)
        Mirror.answer = ok_answer(download={**good, "update_type": "weird", "sha256": good["sha256"].upper()})
        odd = mirrorchyan.latest(CDK)["download"]
        out["不认识的包种类当作完整包；SHA-256 统一成小写"] = odd["update_type"] == "full" and odd["sha256"] == good["sha256"]

        def error(answer):
            Mirror.answer = answer
            try:
                mirrorchyan.latest(CDK)
            except mirrorchyan.MirrorError as e:
                return str(e), e.code
            return None, None

        codes = {code: error((403, {"code": code, "msg": "x"})) for code in (7001, 7002, 7003, 7004, 7005)}
        out["CDK 过期、不对、今天次数用完、不是给 Evo 的、被封：各有原因"] = (
            "过期" in codes[7001][0] and "不对" in codes[7002][0] and "次数用完" in codes[7003][0]
            and "不是给 Evo" in codes[7004][0] and "封禁" in codes[7005][0]
            and [c for _, c in codes.values()] == [7001, 7002, 7003, 7004, 7005])
        messages = [error((404, {"code": 8001, "msg": "x"})), error((200, {"code": 1, "msg": "x"})),
                    error((500, b"<html>oops</html>")), error((200, b"not json")),
                    error((200, {"code": 0, "data": {"version_name": "latest"}}))]
        out["没有这个版本（8001）、别的代码、HTTP 500、不是 JSON、版本号格式不对：都给出原因，不出错"] = (
            "暂时没有" in messages[0][0] and "代码 1" in messages[1][0] and "HTTP 500" in messages[2][0]
            and "格式不对" in messages[3][0] and "格式不对" in messages[4][0])
        texts = [m for m, _ in [*codes.values(), *messages]]
    with mock.patch.object(mirrorchyan, "API", "http://127.0.0.1:9/api/resources/Evo/latest"):
        try:
            mirrorchyan.latest(CDK, timeout=3)
            down = ""
        except mirrorchyan.MirrorError as e:
            down = str(e)
    out["连不上：给出原因"] = "连不上 Mirror酱" in down
    out["出错信息里没有 CDK、没有请求地址"] = all(CDK not in t and "http" not in t for t in [*texts, down])
    out["CDK 格式：去掉前后空格；有空格、中文、太长的不收"] = (
        mirrorchyan.clean_cdk(f"  {CDK} ") == CDK and mirrorchyan.clean_cdk("a b") is None
        and mirrorchyan.clean_cdk("中文") is None and mirrorchyan.clean_cdk("x" * 129) is None
        and mirrorchyan.clean_cdk("") is None)
    with mock.patch.object(mirrorchyan, "API", api):
        Mirror.answer = ok_answer(download=good)
        valid = mirrorchyan.check_cdk(CDK)
        saved_ok = app_settings.get(mirrorchyan.CHECK_KEY)
        Mirror.answer = (403, {"code": 7002, "msg": "KEY_INVALID"})
        invalid = mirrorchyan.check_cdk("WRONG")
    out["核对 CDK：有效时记下到期日；不对时记下原因，并标明是 CDK 的问题"] = (
        valid["ok"] and valid["expires"] and saved_ok == valid
        and not invalid["ok"] and invalid["cdk"] and "不对" in invalid["error"])
    return out


def check_checks(base):
    api = f"{base}/api/resources/Evo/latest"
    out = {}
    patches = [mock.patch.object(mirrorchyan, "API", api), mock.patch.object(update_check, "TEST_HOST", "127.0.0.1")]
    for p in patches:
        p.start()
    try:
        Mirror.answer = ok_answer()
        Mirror.asked.clear()
        with mock.patch.object(update_check, "LATEST_URLS", [f"{base}/no-latest.json"]):
            fallback = update_check.check(timeout=5)
        out["latest.json 读不到、Mirror酱 查得到：用 Mirror酱 的版本（没有国际渠道的安装包信息）"] = (
            fallback["ok"] and fallback["newer"] and fallback["latest"]["version"] == NEW
            and fallback["latest"]["download"] is None and fallback["latest"]["source"] == "mirrorchyan"
            and fallback["latest"]["notes"][0] == "新增：战绩页可以改地图" and len(Mirror.asked) == 1
            and "cdk" not in Mirror.asked[0])
        Mirror.files["/latest.json"] = json.dumps({"version": "9.9.8", "notes": ["自己的"]}).encode("utf-8")
        with mock.patch.object(update_check, "LATEST_URLS", [f"{base}/latest.json"]):
            own = update_check.check(timeout=5)
        out["latest.json 读得到：用它的（Mirror酱 也问了一次，不带 CDK）"] = (
            own["ok"] and own["latest"]["version"] == "9.9.8" and len(Mirror.asked) == 2 and "cdk" not in Mirror.asked[1])
        cn()  # 选了国内渠道、填了 CDK：检查更新时照样不带 CDK
        with mock.patch.object(update_check, "LATEST_URLS", [f"{base}/latest.json"]):
            update_check.check(timeout=5)
        out["选了国内渠道、填了 CDK：检查更新时也不带 CDK"] = "cdk" not in Mirror.asked[-1]
        Mirror.answer = (500, b"down")
        with mock.patch.object(update_check, "LATEST_URLS", [f"{base}/no-latest.json"]):
            neither = update_check.check(timeout=5)
        out["两边都读不到：没查到（说的是 latest.json 的原因）"] = not neither["ok"] and "连不上更新服务器" in neither["error"]
    finally:
        for p in reversed(patches):
            p.stop()
        app_settings.set_value(mirrorchyan.CHANNEL_KEY, None)
        app_settings.set_value(mirrorchyan.CDK_KEY, None)
        app_settings.set_value("update_latest", None)
    return out


def download_checks(base):
    api = f"{base}/api/resources/Evo/latest"
    out = {}
    with App("mirror-app") as app, mock.patch.object(mirrorchyan, "API", api):
        latest = latest_info(base)

        fresh(app)
        app_settings.set_value(mirrorchyan.CHANNEL_KEY, "cn")
        app_settings.set_value(mirrorchyan.CDK_KEY, None)
        need = updater.state(latest)
        try:
            updater.start_download(latest)
            refused = ""
        except updater.UpdateError as e:
            refused = str(e)
        out["国内渠道没填 CDK：显示要先填，能改用国际渠道；点下载说要先填 CDK"] = (
            need["phase"] == "need_cdk" and need["global_ok"] and "CDK" in refused)

        cn()
        ready = updater.state(latest)
        out["填了 CDK：可以点立即更新（国内渠道，大小要问了才知道）"] = (
            ready["phase"] == "available" and ready["channel"] == "cn" and ready["size_mb"] is None)

        def run(answer, expire=False):
            fresh(app)
            Mirror.answer = answer
            if expire:
                Mirror.expire_once.add("/full.zip")
            updater.start_download(latest)
            return settle(latest)

        full = run(ok_answer(download=offer(base, "/full.zip", zipped(NEW_TREE))))
        staged = app.dir / "_update" / NEW / "Evo"
        out["完整包：带 CDK 和现在的版本问地址、下载、核对、解压，下好了；没去下国际渠道的安装包"] = (
            full["phase"] == "ready" and tree(staged) == NEW_TREE and Mirror.asked[-1]["cdk"] == CDK
            and Mirror.asked[-1]["current_version"] == f"v{APP_VERSION}" and "/intl.zip" not in Mirror.downloads)

        bare = run(ok_answer(download=offer(base, "/inc.zip", incremental(), "incremental")))
        out["增量包（路径不带 Evo/）：照清单拼出新版本，和完整包一模一样，删掉的旧文件没了"] = (
            bare["phase"] == "ready" and tree(staged) == NEW_TREE and "Evo/_internal/gone.txt" not in tree(staged))
        rooted = run(ok_answer(download=offer(base, "/inc2.zip", incremental("Evo/"), "incremental")))
        out["增量包（路径带 Evo/）：一样能拼"] = rooted["phase"] == "ready" and tree(staged) == NEW_TREE

        def failed(answer, before=None):
            fresh(app)
            if before:
                before()
            Mirror.answer = answer
            updater.start_download(latest)
            state = settle(latest)
            return state, not (app.dir / "_update" / NEW).exists()

        changed, clean1 = failed(ok_answer(download=offer(base, "/inc3.zip", incremental(), "incremental")),
                                 lambda: (app.dir / "_internal" / "keep.txt").write_bytes(b"edited"))
        out["本机的文件被改过：不装，写明对不上，可以改用国际渠道；没有自动去下国际渠道"] = (
            changed["phase"] == "failed" and "对不上" in changed["error"] and changed["channel"] == "cn"
            and changed["global_ok"] and clean1 and "/intl.zip" not in Mirror.downloads)
        escape, clean2 = failed(ok_answer(download=offer(base, "/inc4.zip", incremental(
            changes={"deleted": ["../../outside.txt"]}), "incremental")))
        out["changes.json 有越界路径（..）：不装"] = escape["phase"] == "failed" and "Evo 里" in escape["error"] and clean2
        nolist, clean3 = failed(ok_answer(download=offer(base, "/inc5.zip", incremental(with_manifest=False),
                                                         "incremental")))
        out["增量包里没有文件清单：不装"] = nolist["phase"] == "failed" and "清单" in nolist["error"] and clean3
        extra, clean4 = failed(ok_answer(download=offer(base, "/inc6.zip", incremental(
            files={"Evo/Evo.exe": b"new exe", "Evo/_internal/added.txt": b"added", "Evo/evil.dll": b"x"}),
            "incremental")))
        out["增量包里有清单上没有的文件：不装"] = extra["phase"] == "failed" and "清单上没有" in extra["error"] and clean4
        wrong = offer(base, "/bad.zip", zipped(NEW_TREE))
        wrong["sha256"] = "0" * 64
        badsha, clean5 = failed(ok_answer(download=wrong))
        out["下载的包 SHA-256 和 Mirror酱 给的对不上：不装"] = (
            badsha["phase"] == "failed" and "SHA-256" in badsha["error"] and clean5)
        expired, clean6 = failed((403, {"code": 7001, "msg": "KEY_EXPIRED"}))
        out["CDK 过期：写明原因，不下载任何东西"] = (
            expired["phase"] == "failed" and "过期" in expired["error"] and clean6 and not Mirror.downloads)
        older, clean7 = failed(ok_answer(version="v9.9.8"))
        out["Mirror酱 上还没有要装的版本：写明过一会儿再试"] = (
            older["phase"] == "failed" and "还没有 9.9.9" in older["error"] and clean7)

        again = run(ok_answer(download=offer(base, "/full.zip", zipped(NEW_TREE))), expire=True)
        out["下载地址过期（403）：再问一次地址，从头下好"] = (
            again["phase"] == "ready" and len(Mirror.asked) == 2 and Mirror.downloads.count("/full.zip") == 2)

        fresh(app)
        updater.start_download(latest, "global")
        intl = settle(latest)
        out["这次改用国际渠道下载：从 latest.json 的地址下，不问 Mirror酱"] = (
            intl["phase"] == "ready" and "/intl.zip" in Mirror.downloads and not Mirror.asked)
        logs = "\n".join(app.logs)
        out["日志里没有 CDK、没有下载地址的签名"] = CDK not in logs and "SECRET-SIGN" not in logs and "sign=" not in logs
    app_settings.set_value(mirrorchyan.CHANNEL_KEY, None)
    app_settings.set_value(mirrorchyan.CDK_KEY, None)
    return out


def panel_checks(base):
    api = f"{base}/api/resources/Evo/latest"
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]
    out = {}
    app_settings.set_value(mirrorchyan.NOTICE_KEY, None)
    page = client.get("/settings").get_data(as_text=True)
    out["设置页：自动检查新版本（不再写说明）、更新渠道两个选项、了解 Mirror酱 的链接"] = (
        'id="update-auto-name">自动检查新版本</div>' in page and "国际渠道（免费）" in page
        and "国内渠道（Mirror酱，需要 CDK）" in page and mirrorchyan.PAGE.replace("&", "&amp;") in page)
    out["点国内渠道时的说明：用户的原文、关闭、不再提示"] = (
        "Mirror酱 和开发者（我）会根据金额分成" in page and "因此优先推荐大家使用国际渠道！" in page
        and "data-mirror-never" in page and 'data-mirror-notice="1"' in page)
    client.post("/update/mirror-notice-off", data={"token": token})
    out["点了不再提示：记进设置，页面上不再弹"] = (
        app_settings.get(mirrorchyan.NOTICE_KEY) is True
        and 'data-mirror-notice="0"' in client.get("/settings").get_data(as_text=True))
    with mock.patch.object(mirrorchyan, "API", api):
        Mirror.answer = ok_answer(download={"cdk_expired_time": 1796400000})
        saved = client.post("/update/channel", data={"token": token, "channel": "cn", "cdk": f" {CDK} "},
                            follow_redirects=True).get_data(as_text=True)
    out["选国内渠道、填 CDK：保存、核对（有效、到期日），CDK 本身不出现在页面上，输入框是密码框"] = (
        app_settings.get(mirrorchyan.CHANNEL_KEY) == "cn" and app_settings.get(mirrorchyan.CDK_KEY) == CDK
        and "CDK 有效" in saved and "到期" in saved and CDK not in saved and 'type="password"' in saved)
    bad = client.post("/update/channel", data={"token": token, "channel": "cn", "cdk": "has space"},
                      follow_redirects=True).get_data(as_text=True)
    out["CDK 格式不对：不保存，写明原因"] = app_settings.get(mirrorchyan.CDK_KEY) == CDK and "格式不对" in bad
    client.post("/update/channel", data={"token": token, "channel": "global"})
    out["改回国际渠道：CDK 留着"] = (app_settings.get(mirrorchyan.CHANNEL_KEY) == "global"
                              and app_settings.get(mirrorchyan.CDK_KEY) == CDK)
    client.post("/update/channel", data={"token": token, "channel": "global", "clear": "1"})
    out["清除 CDK"] = app_settings.get(mirrorchyan.CDK_KEY) is None and app_settings.get(mirrorchyan.CHECK_KEY) is None

    with App("mirror-panel") as app, mock.patch.object(mirrorchyan, "API", api):
        latest = latest_info(base)
        app_settings.set_value("update_latest", latest)
        fresh(app)
        app_settings.set_value(mirrorchyan.CHANNEL_KEY, "cn")
        banner = client.get("/").get_data(as_text=True)
        out["提示条：选了国内渠道没填 CDK，写明要先填，可以这次用国际渠道下载"] = (
            "要先在设置页填 CDK" in banner and "这次用国际渠道下载" in banner and 'value="global"' in banner)
        cn()
        Mirror.answer = (403, {"code": 7003, "msg": "RESOURCE_QUOTA_EXHAUSTED"})
        client.post("/update/download", data={"token": token, "next": "/"})
        settle(latest)
        failed = client.get("/").get_data(as_text=True)
        out["国内渠道没下成：提示条写原因，有\"这次改用国际渠道下载\""] = (
            "次数用完" in failed and "这次改用国际渠道下载" in failed)
        client.post("/update/download", data={"token": token, "channel": "global", "next": "/"})
        done = settle(latest)
        out["点\"这次改用国际渠道下载\"：从国际渠道下好，设置里还是国内渠道"] = (
            done["phase"] == "ready" and "/intl.zip" in Mirror.downloads and mirrorchyan.channel() == "cn")
    for key in (mirrorchyan.CHANNEL_KEY, mirrorchyan.CDK_KEY, mirrorchyan.NOTICE_KEY, "update_latest"):
        app_settings.set_value(key, None)
    return out


def release_checks():
    sys.path.insert(0, str(ROOT / "tools"))
    import release

    app = FOLDER / "release-app"
    make_tree(app, {"Evo.exe": b"exe", "_internal/a.dll": b"dll", "licenses/x.txt": b"lic"})
    target = FOLDER / "release.zip"
    with mock.patch.object(release, "APP", app):
        count = release.make_zip(target, "说明\n第二行")
    with zipfile.ZipFile(target) as z:
        names = z.namelist()
        listed = json.loads(z.read(update_patch.MANIFEST))
        actual = {n: [len(z.read(n)), hashlib.sha256(z.read(n)).hexdigest()] for n in names if n != update_patch.MANIFEST}
    return {
        "发版：压缩包里有文件清单，列着其余每个文件的大小和 SHA-256（不列它自己），版本号对": (
            count == len(names) == 5 and listed["version"] == APP_VERSION and listed["files"] == actual
            and update_patch.MANIFEST not in listed["files"] and f"Evo/{release.README_NAME}" in listed["files"]),
    }


def main():
    print("国内渠道（Mirror酱）：问版本、检查更新、一键更新、增量包、设置页、发版的文件清单")
    server, base = start()
    try:
        checks = client_checks(base)
        checks.update(check_checks(base))
        checks.update(download_checks(base))
        checks.update(panel_checks(base))
        checks.update(release_checks())
    finally:
        server.shutdown()
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
