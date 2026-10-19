"""WebView2 检查（webview2.py）：电脑上没有 WebView2 时问用户要不要装，不装、没装上就用浏览器，不再悄悄用 IE 内核。

- 判断有没有：注册表里的版本号和 .NET Framework（用假的注册表），和 pywebview 在这台电脑上的判断一致
- 安装：找不到安装程序、装好了、装完还是没有、超时（假的安装程序，不真的装）
- 窗口子进程（evo_app.window_process）：有就开窗口；没有就问，不装 / 没装上告诉托盘程序，装好了再开窗口
- 托盘程序（evo_app.PanelServer）：没装上改用浏览器，这次运行里不再问；装好了记进日志；打不开窗口也用浏览器
- app_window.run：pywebview 要用的不是 WebView2 就报错（托盘程序改用浏览器），不打开 IE 内核的窗口
- 面板的 .css 一定是 text/css：哪怕注册表被别的软件改成了 text/plain
全部用假的对话框、安装程序、浏览器，不弹窗、不装东西、不打开浏览器。

运行：python tests/test_webview2.py
"""

import os
import queue
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

from fake_game import CODE, report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_window  # noqa: E402
import evo_app  # noqa: E402
import webview2  # noqa: E402

NET_KEY = r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"
RUNTIME = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
MACHINE = rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{RUNTIME}"
USER = rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{RUNTIME}"


def fake_registry(values):
    """values：{注册表路径: 值}（不分 HKLM / HKCU，路径本身已经不一样）。"""
    return mock.patch.object(webview2, "_registry_value", lambda _root, path, _name: values.get(path))


def detect_checks():
    net = {NET_KEY: 528372}  # .NET Framework 4.8

    def found(values):
        with fake_registry(values):
            return webview2.installed_version()

    from webview.platforms import winforms

    return {
        "装给本机所有用户的 WebView2：认得出版本号": found({**net, MACHINE: "155.0.4283.62"}) == "155.0.4283.62",
        "只装给当前用户的：也认得出": found({**net, USER: "130.0.2849.80"}) == "130.0.2849.80",
        "卸载后留下的 0.0.0.0：算没有": found({**net, MACHINE: "0.0.0.0"}) is None,
        "版本太旧（低于 86.0.622.0）：算没有": found({**net, MACHINE: "85.0.564.40"}) is None,
        "注册表里什么都没有：算没有": found(net) is None,
        ".NET Framework 低于 4.6.2：算没有（pywebview 这时也用不了 WebView2）": found({NET_KEY: 394000, MACHINE: "155.0"}) is None,
        "这台电脑上的真实判断和 pywebview 一致": webview2.available() == winforms._is_chromium(),
    }


class FakeSetup:
    def __init__(self, exists=True):
        self._exists = exists

    def exists(self):
        return self._exists

    def __str__(self):
        return "setup.exe"


def install_checks():
    fake_setup = FakeSetup()

    def install(run=None, after=None, setup=fake_setup):
        with mock.patch.object(webview2, "SETUP", setup), \
             mock.patch.object(webview2.subprocess, "run", run or (lambda *a, **k: SimpleNamespace(returncode=0))), \
             mock.patch.object(webview2, "installed_version", lambda: after):
            return webview2.install()

    def timeout(*_a, **_k):
        raise subprocess.TimeoutExpired("setup.exe", 1)

    missing = FakeSetup(exists=False)
    calls = []
    install(run=lambda args, **k: calls.append(args) or SimpleNamespace(returncode=0), after="155.0")
    return {
        "找不到安装程序：没装上，说明里写着找不到": install(setup=missing)[0] is False and "找不到" in install(setup=missing)[1],
        "运行微软的安装程序时只带 /install（显示它自己的进度窗口，不是悄悄装）": calls == [["setup.exe", "/install"]],
        "装完能找到 WebView2：装好了": install(after="155.0")[0] is True,
        "安装程序结束了但还是找不到（比如用户点了取消）：没装上": install(after=None)[0] is False,
        "超时：没装上": install(run=timeout)[0] is False,
    }


def window_process_checks():
    def run(has, answer=False, installed=(True, "装好了")):
        q, events = queue.Queue(), []
        with mock.patch.object(webview2, "available", lambda: has), \
             mock.patch.object(webview2, "ask", lambda: events.append("问") or answer), \
             mock.patch.object(webview2, "install", lambda: events.append("装") or installed), \
             mock.patch.object(webview2, "tell_failed", lambda: events.append("说没装上")), \
             mock.patch.object(app_window, "run", lambda url, focus: events.append("开窗口")):
            evo_app.window_process("http://127.0.0.1:5000/", None, q)
        messages = []
        while not q.empty():
            messages.append(q.get()[0])
        return events, messages

    return {
        "有 WebView2：不问，直接开窗口": run(True) == (["开窗口"], []),
        "没有，用户点“否”：不装、不开窗口，告诉托盘程序": run(False, answer=False) == (["问"], ["no-webview2"]),
        "没有，用户点“是”、装好了：开窗口，告诉托盘程序装好了":
        run(False, answer=True) == (["问", "装", "开窗口"], ["installed"]),
        "没有，用户点“是”、没装上：告诉用户，不开窗口":
        run(False, answer=True, installed=(False, "x")) == (["问", "装", "说没装上"], ["no-webview2"]),
    }


class FakeProcess:
    def is_alive(self):
        return False


def tray_checks():
    def watch(messages, has_later=False):
        logs, browser, closed = [], [], []
        panel = evo_app.PanelServer(logs.append, on_window_closed=lambda: closed.append(True))
        q = queue.Queue()
        for m in messages:
            q.put(m)
        with mock.patch.object(evo_app.webbrowser, "open", browser.append):
            panel._watch_window(FakeProcess(), q, "URL")
            # 再点一次"打开 Evo"：没装上的话这次运行里不再问，直接用浏览器
            with mock.patch.object(panel, "_start_server", lambda: "URL"), \
                 mock.patch.object(webview2, "available", lambda: has_later), \
                 mock.patch.object(evo_app.multiprocessing, "get_context", side_effect=RuntimeError("开窗口")):
                try:
                    panel.open()
                    again = "浏览器"
                except RuntimeError:
                    again = "窗口"
        return logs, browser, closed, again

    no_logs, no_browser, no_closed, no_again = watch([("no-webview2", "用户选了先不装")])
    later = watch([("no-webview2", "用户选了先不装")], has_later=True)[3]
    ok_logs, ok_browser, ok_closed, ok_again = watch([("installed", "WebView2 装好了（版本 155）")])
    err_logs, err_browser, _, _ = watch([("error", "Traceback ...")])
    return {
        "没装上：用浏览器打开面板，日志写着没有 WebView2": no_browser[:1] == ["URL"] and "没有 WebView2" in no_logs[0],
        "没装上：不算用户关了窗口（不弹“Evo 还在托盘里”的通知）": no_closed == [],
        "没装上以后再打开：直接用浏览器，不再开窗口子进程（不再问）": no_again == "浏览器" and no_browser == ["URL", "URL"],
        "用户后来自己装好了 WebView2：再打开就照常开窗口": later == "窗口",
        "装好了：记进日志；窗口关掉时照常算用户关了窗口，不打开浏览器":
        ok_logs == ["WebView2 装好了（版本 155）"] and ok_browser == [] and ok_closed == [True] and ok_again == "窗口",
        "窗口打不开（别的错误）：还是用浏览器": err_browser == ["URL"] and "打不开" in err_logs[0],
    }


def renderer_checks():
    from webview.platforms import winforms

    created = []
    with mock.patch.object(winforms, "renderer", "mshtml"), \
         mock.patch.object(app_window.webview, "create_window", lambda *a, **k: created.append(True)):
        try:
            app_window.run("http://127.0.0.1:5000/")
            refused = False
        except RuntimeError:
            refused = True
    return {"pywebview 要用 IE 内核时：报错（托盘程序改用浏览器），不打开窗口": refused and created == []}


def mime_checks():
    """在另一个 Python 进程里：先把 .css 的类型改成 text/plain（像被别的软件改了注册表），再导入面板。"""
    code = ("import mimetypes; mimetypes.init(); mimetypes.add_type('text/plain', '.css'); "
            "before = mimetypes.guess_type('a.css')[0]; import panel_app; "
            "r = panel_app.app.test_client().get('/static/style.css'); print(before, r.status_code, r.mimetype)")
    out = subprocess.run([sys.executable, "-c", code], cwd=CODE, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout.split()
    return {"注册表里 .css 是 text/plain 时，面板的样式表照样是 text/css": out == ["text/plain", "200", "text/css"]}


def main():
    print("WebView2 检查：没有时问要不要装，不装就用浏览器，不用 IE 内核")
    checks = {}
    for part in (detect_checks, install_checks, window_process_checks, tray_checks, renderer_checks, mime_checks):
        checks.update(part())
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
