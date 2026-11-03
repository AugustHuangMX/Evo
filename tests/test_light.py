"""轻量（原则 3）：打游戏时不跑识别引擎，识别模型用到时才加载、闲置 2 分钟后释放；托盘程序不加载识别用的库。

画面顺序：5 帧游戏画面 → 一局的总结页 → 大厅 → 200 帧游戏画面（假时钟下约 200 秒）。
"游戏画面"用的是对局中自己的界面（captures/samples/对局中.png）：认传奇、存右上角都只比外观，不用识别引擎；
右上角一局结束、回到大厅才读（停在总结页时只读总结页，2026-10-27）。0.3.0 以前用的是录像里观战对手的截图，现在它右下角的"返回大厅"会被认成"小队已淘汰"。
截图不在仓库里，见 fake_game.py。
托盘程序（evo_app.py，面板也在这个程序里）一直开着，只有记录子进程才该加载 OpenCV、识别引擎、截屏组件：
另开一个 Python 只导入 evo_app 和 panel_app，检查这些库没被带进来（不用截图）。
读结算画面、大厅时不让 Windows 降频（live_collect.FullSpeed）：直接喂画面检查什么时候打开、交还，本机真的设置一次；
上面那次记录里，到了总结页才打开、回到游戏画面后交还，日志里写了识别平均每次几秒。

运行：python tests/test_light.py
"""

import re
import subprocess
import sys
from unittest import mock

from fake_game import CODE, FakeCamera, MissingShots, load, report, run_collect, shot  # 要在项目代码之前导入

import live_collect
import ocr
import process_info

NIGHT = "live/20261016_211452/"
HEAVY = ("cv2", "numpy", "rapidocr", "onnxruntime", "dxcam")  # 只该在记录子进程里加载的库


def tray_imports():
    """托盘程序（含面板）加载了哪些不该加载的库。"""
    code = f"import sys, evo_app, panel_app; print(','.join(m for m in {HEAVY!r} if m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], cwd=CODE, capture_output=True, text=True, check=True)
    return [m for m in out.stdout.strip().split(",") if m]


def main():
    print("轻量：打游戏时不跑识别，模型按需加载")
    heavy = tray_imports()
    ok = report({f"托盘程序和面板不加载识别用的库（加载了：{'、'.join(heavy) or '无'}）": not heavy})
    ok = speed_checks() and ok
    shots = recording_checks()
    return ok if shots is None else ok and shots


class StepClock:
    """FullSpeed 用的假时钟：只有 monotonic()，手动往前拨。"""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


def speed_run(steps, works=True):
    """直接喂画面给 FullSpeed：steps 里是 (画面, 对局中认出过自己的传奇, 这一帧之后过了几秒)。
    返回 (每次设置时是第几步、打开还是交还, 日志)。"""
    calls, log, clock = [], [], StepClock()
    with mock.patch.object(live_collect, "time", clock):
        speed = live_collect.FullSpeed(log.append, apply=lambda on: calls.append((step, on)) or works)
        for step, (screen, in_match, seconds) in enumerate(steps):
            speed.update(screen, in_match)
            clock.now += seconds
        step = "结束"
        speed.stop()
    return calls, log


def speed_checks():
    """读结算画面、大厅时不让 Windows 降频（live_collect.FullSpeed、process_info.set_full_speed）。"""
    end = [(None, True, 0.3), ("eliminated", True, 1), ("summary", False, 1), ("summary", False, 1), ("xp", False, 1),
           (None, False, 5), (None, False, 6), (None, False, 1)]  # 离开经验值页 5 秒还开着，11 秒交还
    calls, log = speed_run(end)
    lobby, _ = speed_run([("lobby", False, 1)] * 3 + [("match_start", False, 5), (None, True, 0.3), (None, True, 0.3)])
    held, _ = speed_run([("summary", False, 1), (None, False, 1)])  # 停止记录时还开着
    failed, failed_log = speed_run([("summary", False, 1), (None, False, 20), (None, False, 1), ("lobby", False, 1)],
                                   works=False)
    real_on, real_off = process_info.set_full_speed(True), process_info.set_full_speed(False)
    return report({
        "对局中不动；进淘汰画面就打开，总结页、经验值页一直开着，离开 10 秒后交还（只设置了两次）":
            calls == [(1, True), (7, False)],
        "日志只写一行（第一次打开时）": log == ["读结算画面、大厅时不让 Windows 降频（对局中照旧由 Windows 决定）"],
        "大厅打开，开局画面还开着，对局中认出自己的传奇就马上交还": lobby == [(0, True), (4, False)],
        "停止记录时还开着：交还": held == [(0, True), ("结束", False)],
        "Windows 不支持时照常运行，日志说一次":
            len(failed) == 4 and failed_log == ["这台电脑的 Windows 不能设置省电限速，照常运行"],
        "本机真的能设置（打开、交还都成功）": real_on and real_off,
    })


def recording_checks():
    """用假游戏跑记录程序，检查识别发生在哪些画面上、模型什么时候加载和释放。找不到截图时返回 None。"""
    try:
        images = {"游戏": load(shot("samples/对局中.png")),
                  "总结页": load(shot(NIGHT + "05_222749.png")),
                  "大厅": load(shot(NIGHT + "lobby_211506_enter.png"))}
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None

    ran_on = []  # 每次识别时屏幕上是哪种画面
    real_create = ocr.create_engine

    def counting_engine(params=None):
        engine = real_create(params)

        def run(img, **kwargs):
            ran_on.append(FakeCamera.showing)
            return engine(img, **kwargs)

        return run

    speed = []  # 每次设置"不降频"时屏幕上是哪种画面、打开还是交还

    def record_speed(on):
        speed.append((FakeCamera.showing, on))
        return True

    frames = ["游戏"] * 5 + ["总结页"] * 3 + ["大厅"] * 3 + ["游戏"] * 200
    with mock.patch.object(ocr, "create_engine", counting_engine), \
            mock.patch.object(live_collect, "set_full_speed", record_speed):
        rows, _, log = run_collect(frames, images.get)
    loads = [line for line in log if line.startswith("加载识别模型")]
    releases = [line for line in log if line.startswith("识别模型闲置")]
    stats = [line for line in log if line.startswith("本次记录")]
    return report({
        f"游戏画面上一次识别都不跑（211 帧里有 205 帧是游戏画面，识别全部发生在总结页、大厅上：{len(ran_on)} 次）":
            ran_on and set(ran_on) <= {"总结页", "大厅"},
        "总结页照常识别并记下（第 8 名，688 伤害）": len(rows) == 1 and (rows[0]["placement"], rows[0]["damage"]) == (8, 688),
        "识别模型只加载了一次，而且是到了总结页才加载": len(loads) == 1 and ran_on[:1] == ["总结页"],
        "离开总结页、闲置 2 分钟后释放了模型，之后没有再加载": len(releases) == 1 and log.index(releases[0]) > log.index(loads[0]),
        "结束时在日志里报告了这次的截屏、识别次数和内存": len(stats) == 1,
        "识别次数后面写了平均每次几秒": len(stats) == 1 and re.search(r"识别 \d+ 次（平均每次 \d+\.\d\d 秒）", stats[0]),
        f"到了总结页才不让 Windows 降频，回到游戏画面后交还（{speed}）": speed == [("总结页", True), ("游戏", False)],
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
