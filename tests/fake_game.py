"""测试用的"假游戏"：把截图当作屏幕画面，按顺序喂给真正的记录程序 live_collect.collect()。

- 截图不在仓库里（太大，而且有其他玩家的名字和 ID），只在开发者电脑上项目文件夹的 captures/ 里。
  找不到截图时，测试会跳过并说明原因，不算失败
- 运行时的数据库和截图写到一个临时文件夹，不碰正式数据，测试结束自动删掉
- 时钟是假的：程序里"等 0.3 秒"不真的等，只把假时钟往前拨。所以测试跑得快，
  "在总结页等了几秒"这类判断每次结果都一样

测试文件要最先 import 这个文件（它要在项目代码导入之前设置好临时数据文件夹）。
"""

import atexit
import itertools
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent  # 项目文件夹
CODE = ROOT / "code"  # 程序代码
SHOTS = ROOT / "captures"  # 截图在这里（用源码运行时的数据文件夹）

_TEMP = Path(tempfile.mkdtemp(prefix="evo-test-"))
atexit.register(shutil.rmtree, _TEMP, ignore_errors=True)
os.environ["EVO_DATA_DIR"] = str(_TEMP)  # paths.py 一导入就决定数据文件夹，所以必须在导入项目代码之前设置
os.environ.pop("EVO_CAPTURES_DIR", None)  # 截图也跟着临时文件夹走，绝不写进项目的 captures/
sys.path.insert(0, str(CODE))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import live_collect  # noqa: E402
import storage  # noqa: E402

# 输出统一用 UTF-8：输出被转存到文件或交给别的程序时，Windows 默认用 GBK，打印 ✅ ❌ 会出错
sys.stdout.reconfigure(encoding="utf-8")

BLACK = np.zeros((1440, 2560, 3), np.uint8)  # 代表"别的画面"（对局中、观战等）：什么都认不出
_runs = itertools.count(1)
last_db = None  # 最近一次 run_collect 用的临时数据库


class MissingShots(Exception):
    """找不到测试要用的截图（截图不在仓库里，只在开发者电脑上）。"""


def shot(relative):
    """captures/ 里的一张截图的完整路径。找不到就抛出 MissingShots，让测试跳过。"""
    path = SHOTS / relative
    if not path.is_file():
        raise MissingShots(path)
    return path


def load(path):
    """读截图（路径里有中文也能读）。"""
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


class FakeCamera:
    """假摄像头：按顺序交出画面；交出最后一帧后就喊停（相当于托盘程序让记录停下）。
    showing：现在屏幕上是哪一帧（测试里用来统计"在哪种画面上跑了识别"）。"""

    width, height = 2560, 1440
    showing = None

    def __init__(self, frames, picture, stop):
        self.frames, self.picture, self.stop = frames, picture, stop
        self.i, self.current = 0, None

    def grab(self, new_frame_only=False):
        self.current = FakeCamera.showing = self.frames[self.i]
        self.i += 1
        if self.i == len(self.frames):
            self.stop.set()
        return self.picture(self.current)

    def release(self):
        pass


class FakeClock:
    """代替记录程序里的 time 模块：sleep 不真的等，只把时钟往前拨，并记下每次等了多久、当时是哪一帧。"""

    def __init__(self, camera):
        self.now, self.camera, self.sleeps = 1000.0, camera, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append((self.camera.current, seconds))
        self.now += seconds


def run_collect(frames, picture, **options):
    """把 frames 依次当作屏幕画面喂给 live_collect.collect()。picture(帧) 返回这一帧的图片。
    options 原样交给 collect()（如 summary_timeout=1）。
    返回 (数据库里的全部对局, 每次等待的记录 [(帧, 秒数)], 日志的每一行)。
    这次用的临时数据库记在 fake_game.last_db（要查别的表时用，如队友表）。"""
    global last_db
    stop = threading.Event()
    camera = FakeCamera(frames, picture, stop)
    clock = FakeClock(camera)
    log = []
    db = last_db = _TEMP / f"run{next(_runs)}.db"
    with mock.patch.object(live_collect.dxcam, "create", lambda **kw: camera), \
            mock.patch.object(live_collect, "time", clock):
        live_collect.collect(db_path=db, stop=stop, log=log.append, **options)
    conn = storage.connect(db)
    rows = conn.execute("SELECT * FROM matches ORDER BY match_id").fetchall()
    conn.close()
    return rows, clock.sleeps, log


def report(checks):
    """打印每一项检查的结果（✅ / ❌），全部通过返回 True。checks：{说明: 是否通过}。"""
    for text, ok in checks.items():
        print(f"  {'✅' if ok else '❌'} {text}")
    return all(checks.values())
