"""开发者公告（notice_check.py、托盘里读公告的线程、窗口顶部的提示条）。不联网：公告文件由本机的小服务器提供。

- 和"自动检查新版本"共用开关（2026-11-01 用户决定不另外问）：关着时不联网、不显示
- 读公告：先读 COS 上的，读不到（出错、格式不对）再读 dl.evotracker.cn 的；只带 User-Agent；都读不到时留着上次的；
  读到了没有公告就清空（撤下）
- 每一条的规矩：id、level、text 不对的去掉；链接只接受自己网站的 https；过了 until 不显示；versions 不给这个版本的不显示；
  最多 5 条
- 托盘：读到重要公告用托盘通知提醒一次，Apex 开着时不弹、关了游戏再弹；普通公告不弹；打开窗口时上次读过了 5 分钟就再读
- 面板：顶部提示条（重要的写"重要公告"）、只当纯文字显示、点"知道了"不再显示（也不再提醒）、没带口令被拒绝；
  第一次打开的提问和设置页写着公告

运行：python tests/test_notice.py
"""

import http.server
import json
import os
import re
import threading
import time
from datetime import date

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_settings
import evo_app
import notice_check
import panel_app
import storage

os.environ["no_proxy"] = "127.0.0.1,localhost"  # 电脑上设了代理时，本机的测试服务器也直接连
VERSION = storage.APP_VERSION


class Handler(http.server.BaseHTTPRequestHandler):
    files = {}  # 路径 → (状态码, 内容)
    requests = []  # 收到的请求：(路径, 请求头)

    def do_GET(self):  # noqa: N802
        self.requests.append((self.path, dict(self.headers)))
        status, data = self.files.get(self.path, (404, b"not found"))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


def serve(path, notices=None, status=200, body=None):
    Handler.files[path] = (status, body if body is not None else json.dumps({"notices": notices or []}).encode("utf-8"))


def one(nid="n1", level="info", text="新赛季画面改了，识别暂时不准", **more):
    return {"id": nid, "level": level, "text": text, **more}


def banners(page):
    return re.findall(r'<div class="banner-info notice-banner[^"]*".*?</div>', page, re.S)


def format_checks(out):
    bad = [one(nid="有中文"), one(nid="x" * 41), one(nid="lv", level="urgent"), one(nid="empty", text="  "),
           one(nid="date", until="2026-13-01"), one(nid="ver", versions="大于 0.5"), "不是字典", one(nid="ok")]
    kept = notice_check.clean_list(bad)
    out["id、level、正文、日期、版本写错的那一条去掉，不影响别的"] = [n["id"] for n in kept] == ["ok"]
    link = notice_check.clean(one(link="https://evotracker.cn/notice"))
    evil = [notice_check.clean(one(link=url))["link"] for url in
            ("https://evil.example.com/", "http://evotracker.cn/", "javascript:alert(1)", "https://evotracker.cn.evil.com/")]
    out["链接只接受自己网站的 https，别的不显示链接（公告照样显示）"] = (
        link["link"] == "https://evotracker.cn/notice" and evil == [None, None, None, None])
    long = notice_check.clean(one(text="一行\n两行   三 " + "字" * 300))
    out["正文变成一行、最多 200 个字"] = long["text"].startswith("一行 两行 三 字") and len(long["text"]) == 200
    many = notice_check.clean_list([one(nid=f"n{i}") for i in range(8)] + [one(nid="n0")])
    out["最多 5 条，id 重复的只留第一条"] = [n["id"] for n in many] == ["n0", "n1", "n2", "n3", "n4"]
    ok = notice_check.versions_ok
    out["版本：<=、>=、<、>、=，逗号隔开的都要满足；写错了返回 None"] = (
        ok("<=0.5.0", "0.5.0") and not ok("<=0.5.0", "0.5.1") and ok(">=0.5.1,<0.6.0", "0.5.9")
        and not ok(">=0.5.1,<0.6.0", "0.6.0") and ok("0.5.1", "0.5.1") and not ok(">0.5.1", "0.5.1")
        and ok(None) is True and ok("0.5", "0.5.0") is None and ok("<=0.5.0,", "0.5.0") is None)


def fetch_checks(out, base):
    cos, r2 = "/cos/notice.json", "/r2/notice.json"
    notice_check.URLS = [base + cos, base + r2]
    Handler.files.clear()

    # ---- 开关关着：不联网 ----
    app_settings.set_value("update_auto", False)
    Handler.requests.clear()
    serve(cos, [one()])
    out["\"自动检查新版本\"关着：不联网，也不显示"] = (
        notice_check.check() is None and not Handler.requests and notice_check.visible() == [])

    # ---- 先读 COS ----
    app_settings.set_value("update_auto", True)
    result = notice_check.check()
    out["先读 COS 上的；只带 User-Agent（Evo/版本号），不带编号、战绩"] = (
        result["ok"] and [n["id"] for n in result["notices"]] == ["n1"]
        and [r[0] for r in Handler.requests] == [cos]
        and Handler.requests[0][1].get("User-Agent") == f"Evo/{VERSION}"
        and not {"Cookie", "Authorization"} & set(Handler.requests[0][1]))
    out["读到了就显示"] = [n["id"] for n in notice_check.visible()] == ["n1"]

    # ---- COS 读不到：改读 dl.evotracker.cn ----
    for status, body in ((451, b'{"error": "content illegal"}'), (200, b"<html>not json</html>"),
                         (200, b'{"notice": []}')):
        serve(cos, status=status, body=body)
        serve(r2, [one(nid="r2")])
        Handler.requests.clear()
        result = notice_check.check()
        out[f"COS 回 {status}{'（格式不对）' if status == 200 else ''}：改读 dl.evotracker.cn 的"] = (
            result["ok"] and [n["id"] for n in result["notices"]] == ["r2"]
            and [r[0] for r in Handler.requests] == [cos, r2])

    # ---- 都读不到：留着上次的 ----
    serve(cos, status=503, body=b"")
    serve(r2, status=503, body=b"")
    result = notice_check.check()
    state = app_settings.get(notice_check.STATE_KEY)
    out["都读不到：写明原因，留着上次读到的（照样显示）"] = (
        not result["ok"] and "连不上" in result["error"] and state["ok"] is False
        and [n["id"] for n in notice_check.visible()] == ["r2"])
    notice_check.URLS = ["http://127.0.0.1:9/notice.json"]  # 没有服务器的端口
    offline = notice_check.check()
    notice_check.URLS = [base + cos, base + r2]
    out["连不上：写明连不上"] = not offline["ok"] and "连不上" in offline["error"]

    # ---- 撤下 ----
    serve(cos, [])
    result = notice_check.check()
    out["读到了、没有公告：清空（撤下的公告不再显示）"] = result["ok"] and notice_check.visible() == []
    out["读过就记下时间（打开窗口时据此决定要不要再读）"] = (
        not notice_check.due(300) and notice_check.due(0))


def show_checks(out, base):
    cos = "/cos/notice.json"
    today = date(2026, 11, 2)
    serve(cos, [one(nid="old", until="2026-11-01"), one(nid="today", until="2026-11-02"),
                one(nid="mine", versions=f"<={VERSION}"), one(nid="newer", versions=f">{VERSION}"),
                one(nid="imp", level="important", text="重要的事")])
    notice_check.check()
    shown = [n["id"] for n in notice_check.visible(today)]
    out["过了 until 不显示（当天还显示）；不给这个版本的不显示"] = shown == ["today", "mine", "imp"]
    out["要用托盘提醒的只有重要公告"] = [n["id"] for n in notice_check.to_notify(today)] == ["imp"]
    notice_check.mark_notified("imp")
    out["提醒过的不再提醒（窗口里照样显示）"] = (
        notice_check.to_notify(today) == [] and "imp" in [n["id"] for n in notice_check.visible(today)])
    app_settings.set_value(notice_check.NOTIFIED_KEY, None)
    notice_check.dismiss("today")
    notice_check.dismiss("../../坏的")
    out["点了\"知道了\"：不再显示，也不再提醒；不认识的 id 不记"] = (
        "today" not in [n["id"] for n in notice_check.visible(today)]
        and app_settings.get(notice_check.DISMISSED_KEY) == ["today"])
    notice_check.dismiss("imp")
    out["重要公告点了\"知道了\"：也不再用托盘提醒"] = notice_check.to_notify(today) == []
    for i in range(60):
        notice_check.mark_notified(f"x{i}")
    out["提醒过的 id 只留最近 50 条"] = len(app_settings.get(notice_check.NOTIFIED_KEY)) == 50


def panel_checks(out):
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]
    for key in (notice_check.DISMISSED_KEY, notice_check.NOTIFIED_KEY):
        app_settings.set_value(key, None)
    app_settings.set_value(notice_check.STATE_KEY, {"ok": True, "checked": time.time(), "list": [
        one(nid="info", text="<b>周末</b>服务器维护 & 测试", link="https://evotracker.cn/news"),
        one(nid="imp", level="important", text="0.5.0 有个问题，请更新", link="https://evil.example.com/")]})
    page = client.get("/").get_data(as_text=True)
    found = banners(page)
    out["顶部两条提示条：普通的写\"公告\"，重要的写\"重要公告\"、是黄色（notice-important）"] = (
        len(found) == 2 and '<span class="notice-tag">公告</span>' in found[0] and "notice-important" not in found[0]
        and '<span class="notice-tag">重要公告</span>' in found[1] and "notice-important" in found[1])
    out["正文只当纯文字显示（HTML 原样显示出来，不生效）"] = (
        "&lt;b&gt;周末&lt;/b&gt;服务器维护 &amp; 测试" in found[0] and "<b>周末</b>" not in page)
    out["自己网站的链接显示\"详情 ↗\"、交给浏览器打开；别的网站的链接不显示"] = (
        'href="https://evotracker.cn/news" target="_blank" rel="noopener noreferrer">详情 ↗' in found[0]
        and "evil.example.com" not in page and "详情" not in found[1])
    reply = client.post("/notice/dismiss", data={"token": token, "id": "info", "next": "/stats"})
    after = client.get("/").get_data(as_text=True)
    out["点\"知道了\"：回到原来的页面，这条不再显示"] = (
        reply.status_code == 302 and reply.headers["Location"].endswith("/stats")
        and len(banners(after)) == 1 and "0.5.0 有个问题" in after)
    out["没带口令：\"知道了\"被拒绝"] = client.post("/notice/dismiss", data={"id": "imp"}).status_code == 403
    app_settings.set_value("update_auto", False)
    off = client.get("/").get_data(as_text=True)
    out["关掉\"自动检查新版本\"：公告也不显示"] = banners(off) == []
    app_settings.set_value("update_auto", None)
    first = client.get("/").get_data(as_text=True)
    out["第一次打开的提问写着公告（每 30 分钟）"] = (
        "要不要自动检查新版本和公告？" in first and "每 30 分钟看一次有没有开发者公告" in first)
    app_settings.set_value("update_auto", True)
    settings = client.get("/settings").get_data(as_text=True)
    # 2026-11-04 用户改：设置页的开关只叫"自动检查新版本"，不写说明（公告照旧跟着这个开关）
    out["设置页的开关叫\"自动检查新版本\""] = 'id="update-auto-name">自动检查新版本</div>' in settings


def tray_checks(out, base):
    """托盘里读公告的线程：时间改短来试。"""
    cos = "/cos/notice.json"
    for key in (notice_check.DISMISSED_KEY, notice_check.NOTIFIED_KEY, notice_check.STATE_KEY):
        app_settings.set_value(key, None)
    app_settings.set_value("update_auto", True)
    serve(cos, [one(nid="t-info"), one(nid="t-imp", level="important", text="重要：今晚维护")])
    saved = evo_app.NOTICE_EVERY, evo_app.NOTICE_AGAIN, evo_app.NOTICE_GAME_WAIT
    evo_app.NOTICE_EVERY, evo_app.NOTICE_AGAIN, evo_app.NOTICE_GAME_WAIT = 30, 2.5, 0.2
    notes, logs, playing, stop = [], [], [True], threading.Event()
    Handler.requests.clear()
    try:
        evo_app.start_notice_checker(logs.append, lambda text, title: notes.append((title, text)), stop,
                                     lambda: playing[0], first_delay=0)
        time.sleep(1)
        out["启动后读一次；Apex 开着时不弹托盘通知"] = len(Handler.requests) == 1 and notes == []
        playing[0] = False
        time.sleep(0.6)
        out["关了游戏：重要公告用托盘通知提醒一次，普通的不弹"] = notes == [("Evo 公告", "重要：今晚维护")]
        out["日志写读到了哪几条、提醒了哪条"] = (
            "公告：现在有 2 条（t-info、t-imp）" in logs and "公告：用托盘通知提醒了 t-imp" in logs)
        before = len(Handler.requests)
        notice_check.wake.set()  # 刚读过（不到 NOTICE_AGAIN）：打开窗口不再读
        time.sleep(0.3)
        quick = len(Handler.requests) == before
        time.sleep(1.1)
        notice_check.wake.set()  # 上次读过了 NOTICE_AGAIN：打开窗口时再读一次
        time.sleep(0.5)
        out["打开窗口：刚读过不再读，上次读过了一会儿再读一次"] = quick and len(Handler.requests) == before + 1
        out["同一条只提醒一次；结果没变时日志不重复写"] = (
            len(notes) == 1 and logs.count("公告：现在有 2 条（t-info、t-imp）") == 1)
        app_settings.set_value("update_auto", False)
        time.sleep(2.7)
        before = len(Handler.requests)
        notice_check.wake.set()
        time.sleep(0.5)
        out["中途关掉\"自动检查新版本\"：不再联网"] = len(Handler.requests) == before
    finally:
        stop.set()
        notice_check.wake.set()
        evo_app.NOTICE_EVERY, evo_app.NOTICE_AGAIN, evo_app.NOTICE_GAME_WAIT = saved


def main():
    print("开发者公告（和检查新版本共用开关、读公告、显示规则、托盘提醒、提示条）")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    urls = notice_check.URLS
    notice_check.URLS = [f"{base}/cos/notice.json"]  # 绝不连到真正的服务器
    db = panel_app.app.config["DB"]
    path = storage.DB_PATH.parent / "notice-test.db"
    path.unlink(missing_ok=True)
    storage.connect(path).close()
    panel_app.app.config.update(DB=path, TESTING=True)
    out = {}
    try:
        format_checks(out)
        fetch_checks(out, base)
        show_checks(out, base)
        panel_checks(out)
        tray_checks(out, base)
    finally:
        notice_check.URLS, panel_app.app.config["DB"] = urls, db
        server.shutdown()
        for key in ("update_auto", notice_check.STATE_KEY, notice_check.DISMISSED_KEY, notice_check.NOTIFIED_KEY):
            app_settings.set_value(key, None)
    return report(out)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
