"""开机自动启动（autostart.py）：Windows 开机启动项（注册表 HKCU 的 Run）。

- 开、关、读状态；在任务管理器"启动应用"里关掉了显示关，在 Evo 里重新打开时恢复
- 启动项指向另一个位置的 Evo：那个还在就不动；不在了（程序换了位置），打开 Evo 时改成现在这个
- 用源码运行、在压缩包里直接打开：不能设置
- 被安全软件拦住（写不进去、写了没生效）：返回没开成
- 面板：第一次打开时问一次（回答了"自动检查新版本"以后才问）；设置页的开关；没开成的提示（用户定的说法）
全部写在测试用的注册表位置（HKCU\\Software\\Evo-test），跑完删掉，不碰真正的开机启动项。

运行：python tests/test_autostart.py
"""

import sys
import tempfile
import winreg
from pathlib import Path
from unittest import mock

from fake_game import ROOT, report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_settings  # noqa: E402
import autostart  # noqa: E402
import panel_app  # noqa: E402
import storage  # noqa: E402

TEST_KEY = r"Software\Evo-test"
autostart.RUN_KEY = TEST_KEY + r"\Run"
autostart.APPROVED_KEY = TEST_KEY + r"\StartupApproved"
EXE = ROOT / "_autostart_test" / "Evo.exe"  # 不在临时文件夹里、不用真的存在
OTHER = Path(sys.executable).resolve()  # "另一个位置的 Evo"：要一个真的存在的文件
DISABLED = bytes([3, 0, 0, 0]) + bytes(8)  # 任务管理器里关掉后 Windows 写的值（第一个字节单数）


def run_value():
    return autostart._read(autostart.RUN_KEY)


def set_run(text):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, autostart.VALUE_NAME, 0, winreg.REG_SZ, text)


def turn_off_in_windows():
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, autostart.APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, autostart.VALUE_NAME, 0, winreg.REG_BINARY, DISABLED)


def remove_test_key():
    for sub in (autostart.RUN_KEY, autostart.APPROVED_KEY, TEST_KEY):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except FileNotFoundError:
            pass


def module_checks():
    out = {}
    out["启动项的命令读得出程序：带引号和参数、不带引号"] = (
        autostart.target(r'"D:\Evo Tracker\Evo.exe" --background') == Path(r"D:\Evo Tracker\Evo.exe")
        and autostart.target(r"D:\Evo\Evo.exe --background") == Path(r"D:\Evo\Evo.exe")
        and autostart.target("") is None)

    with mock.patch.object(autostart, "EXE", None):
        out["用源码运行：不能设置（写明原因），开不了"] = "源码" in (autostart.unavailable() or "") and not autostart.enable()
    with mock.patch.object(autostart, "EXE", Path(tempfile.gettempdir()) / "Evo-0.4.0" / "Evo" / "Evo.exe"):
        out["在压缩包里直接打开（临时文件夹）：不能设置"] = "压缩包" in (autostart.unavailable() or "")

    with mock.patch.object(autostart, "EXE", EXE):
        out["打包好的 Evo：能设置"] = autostart.unavailable() is None
        out["一开始：关着"] = autostart.status() == {"on": False, "off_in_windows": False, "other": None}
        out["打开：成功"] = autostart.enable()
        out["启动项写的是 \"Evo.exe\" --background，状态是开着"] = (
            run_value() == f'"{EXE}" --background' and autostart.status()["on"])
        logs = []
        autostart.after_start(logs.append)
        out["打开 Evo 时日志写\"开机自启：已开启\"，启动项不变"] = logs == ["开机自启：已开启"] and run_value() == f'"{EXE}" --background'

        turn_off_in_windows()
        now = autostart.status()
        out["在任务管理器里关掉了：显示关，并说明是在 Windows 里关的"] = not now["on"] and now["off_in_windows"]
        out["在 Evo 里重新打开：Windows 的\"关掉\"记录删掉，显示开着"] = (
            autostart.enable() and autostart._read(autostart.APPROVED_KEY) is None and autostart.status()["on"])

        turn_off_in_windows()
        out["关掉：启动项和 Windows 的记录都删掉"] = (
            autostart.disable() and run_value() is None and autostart._read(autostart.APPROVED_KEY) is None)
        out["本来就没有时再关：也算关好了"] = autostart.disable()

        set_run(f'"{OTHER}" --background')
        now = autostart.status()
        out["启动项指向另一个还在的 Evo：显示关，写出那个位置"] = not now["on"] and now["other"] == str(OTHER)
        logs = []
        autostart.after_start(logs.append)
        out["那个 Evo 还在：打开时不改它"] = run_value() == f'"{OTHER}" --background' and "另一个位置" in logs[0]
        out["关掉：不动另一个 Evo 的启动项"] = autostart.disable() and run_value() == f'"{OTHER}" --background'
        out["打开：换成现在这个"] = autostart.enable() and autostart.status()["on"]

        gone = ROOT / "_autostart_gone" / "Evo.exe"
        set_run(f'"{gone}" --background')
        logs = []
        autostart.after_start(logs.append)
        out["程序换了位置（原来的 Evo.exe 不在了）：打开时改成现在这个，写进日志"] = (
            run_value() == f'"{EXE}" --background' and "已经不在了" in logs[0] and logs[-1] == "开机自启：已开启")

        autostart.disable()
        with mock.patch.object(autostart, "_write", side_effect=PermissionError("拒绝访问")):
            out["写不进去（安全软件拒绝）：没开成"] = not autostart.enable() and run_value() is None
        with mock.patch.object(autostart, "_write", lambda exe: None):
            out["写了没生效（安全软件悄悄拦住）：读回来核对，没开成"] = not autostart.enable()
        logs = []
        autostart.after_start(logs.append)
        out["关着时打开 Evo：什么都不改、日志不写"] = logs == [] and run_value() is None
    return out


def panel_checks():
    out = {}
    panel_app.app.config["DB"] = str(Path(tempfile.mkdtemp(prefix="evo-autostart-")) / "panel.db")
    storage.connect(panel_app.app.config["DB"]).close()
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]
    ask = "是否在开机时自动启动 Evo？"  # 2026-10-22 用户改的说法（0.4.0 起）

    def flat(html):  # 模板里属性分几行写：空白合成一个空格再比
        return " ".join(html.split())

    def page(url="/"):
        return flat(client.get(url).get_data(as_text=True))

    def post(**form):
        return client.post("/settings/autostart", data={"token": token, **form}, follow_redirects=True)

    def posted(**form):
        return flat(post(**form).get_data(as_text=True))

    with mock.patch.object(autostart, "EXE", EXE):
        app_settings.set_value("update_auto", None)
        app_settings.set_value(autostart.ASKED_KEY, None)
        first = page()
        out["第一次打开：先只问\"自动检查新版本\"，不同时问开机自启"] = "要不要自动检查新版本？" in first and ask not in first
        app_settings.set_value("update_auto", False)
        out["回答了检查更新以后：顶部问开机自启"] = ask in page() and ask in page("/matches")

        got = posted(on="1", next="/")
        out["点\"开机自动启动\"：开好，提示开了，不再问"] = (
            autostart.status()["on"] and app_settings.get(autostart.ASKED_KEY) is True
            and "已开启开机自动启动" in got and ask not in got)

        settings = page("/settings")
        out["设置页：第一栏叫\"常规\"，有开机自动启动的开关，显示开着"] = (
            '<h2 id="h-general">常规</h2>' in settings and 'aria-labelledby="autostart-name"' in settings
            and 'aria-checked="true" aria-labelledby="autostart-name"' in settings)
        out["设置页底部\"还在开发中\"不再提开机自动启动"] = "开机自动启动、每局结束后的通知" not in settings

        got = posted(on="0", inline="1", next="/settings")
        out["设置页关掉：启动项删掉，开关显示关"] = (
            run_value() is None and 'aria-checked="false" aria-labelledby="autostart-name"' in got)

        app_settings.set_value(autostart.ASKED_KEY, None)
        got = posted(on="0", next="/")
        out["顶部提问点\"不用了\"：不开、不再问"] = (
            run_value() is None and app_settings.get(autostart.ASKED_KEY) is True and ask not in got)

        turn_off_in_windows()
        set_run(f'"{EXE}" --background')
        app_settings.set_value(autostart.ASKED_KEY, None)
        out["在任务管理器里关掉过、没问过：照样问（现在开机不会启动）"] = ask in page()
        out["设置页写明是在 Windows 的启动应用里关掉的"] = "启动应用" in page("/settings")
        autostart.disable()

        with mock.patch.object(autostart, "_write", side_effect=PermissionError("拒绝访问")):
            got = posted(on="1", inline="1", next="/settings")
            out["设置页开关没开成：开关下面写\"没有成功开启自启，请检查是否被杀毒软件拦截\"，开关还是关"] = (
                'id="autostart-error" role="alert">没有成功开启自启，请检查是否被杀毒软件拦截<' in got
                and 'aria-checked="false" aria-labelledby="autostart-name" aria-describedby="autostart-error"' in got)
            out["这句提示只显示一次"] = "没有成功开启自启" not in page("/settings")
            got = posted(on="1", next="/")
            out["顶部提问点了没开成：提示写在页面顶部"] = (
                'class="flash flash-error" role="status">没有成功开启自启，请检查是否被杀毒软件拦截<' in got)

        out["没带口令：拒绝"] = client.post("/settings/autostart", data={"on": "1"}).status_code == 403

    with mock.patch.object(autostart, "EXE", None):
        app_settings.set_value(autostart.ASKED_KEY, None)
        out["用源码运行：不问"] = ask not in page()
        settings = page("/settings")
        out["用源码运行：设置页的开关变灰，写明原因"] = (
            "用源码运行时不能设置" in settings and 'aria-labelledby="autostart-name" disabled' in settings)
        out["用源码运行：提交开关被拒绝"] = post(on="1").status_code == 400
    app_settings.set_value("update_auto", None)
    app_settings.set_value(autostart.ASKED_KEY, None)
    return out


def main():
    print("开机自动启动（注册表启动项、任务管理器里关掉、换了程序位置、被拦住、面板）")
    remove_test_key()
    try:
        checks = {**module_checks(), **panel_checks()}
    finally:
        remove_test_key()
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
