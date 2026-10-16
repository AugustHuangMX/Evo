"""Evo 主程序：任务栏托盘图标 + 后台自动记录 + 按需打开的面板。打包成 Evo.exe 时，入口就是这个文件。

- 每 5 秒看一次 Apex 有没有在运行（只看进程名）。在运行、而且没有暂停，就启动一个记录子程序；
  游戏关掉或暂停时让它安全停下。子程序退出后，识别模型占的内存全部还给系统，托盘本身只占几十 MB，
  不玩游戏时几乎不占 CPU。识别模块万一崩溃也只影响子程序，托盘还在，下次检测到游戏会重新开始
- 右键托盘图标：打开面板 / 暂停记录 / 退出；单击图标也能打开面板
- 图标：平时是盾牌；正在记录时右下角有个小红点；暂停时变灰。鼠标停在图标上能看到状态
- 同一时间只能运行一个 Evo（防止同一局被记两遍）
- 运行记录写在数据文件夹的 logs/evo.log（打包后没有终端窗口，出了问题看这里）

运行：python evo_app.py
测试：python evo_app.py --game notepad.exe   （把别的程序当作游戏，用来测试"开始/停止记录"）
自检：python evo_app.py --check-image 截图.png   （识别截图，结果写进 logs/evo.log）
"""

import argparse
import ctypes
import logging
import multiprocessing
import socket
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty

import pystray
from PIL import Image, ImageDraw, ImageOps

import storage
from game_detect import GAME_PROCESSES, game_running
from paths import ASSETS_DIR, DATA_DIR, FROZEN, LOG_DIR

APP_NAME = "Evo"
MUTEX_NAME = "Local\\EvoTrackerSingleInstance"
RETRY_AFTER_ERROR = 60  # 记录出错后，至少等这么多秒再重新开始，免得反复出错
PANEL_PORTS = range(5000, 5020)


# ---------- 运行记录 ----------

def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("evo")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_DIR / "evo.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    if sys.stdout is not None:  # 用源码运行时同时打印到终端；打包后没有终端
        logger.addHandler(logging.StreamHandler(sys.stdout))
    return logger


# ---------- 只运行一个 ----------

_mutex = None  # 保持引用，程序退出前一直占着


def already_running():
    global _mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def tell_user(text):
    """打包后没有终端，用弹窗告诉用户；用源码运行时打印到终端。"""
    if FROZEN:
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x40)
    else:
        print(text)


# ---------- 图标 ----------

def draw_shield(size=64):
    """临时图标：面板左上角的盾牌（用户设计的图标做好后，放在 assets/icon/evo.png 就会用那个）。"""
    s = 4 * size / 28  # 先画 4 倍大再缩小，边缘更平滑
    img = Image.new("RGBA", (4 * size, 4 * size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    outer = [(14, 2), (25, 7), (25, 15), (23.5, 20), (19.5, 24), (14, 27), (8.5, 24), (4.5, 20), (3, 15), (3, 7)]
    inner = [(14, 8), (20, 10.5), (20, 15), (19, 18), (17, 20), (14, 21.5), (11, 20), (9, 18), (8, 15), (8, 10.5)]
    d.polygon([(x * s, y * s) for x, y in outer], outline=(255, 90, 90, 255), width=round(2.4 * s))
    d.polygon([(x * s, y * s) for x, y in inner], fill=(255, 90, 90, 255))
    return img.resize((size, size), Image.LANCZOS)


ICON_FILES = ("evo.png", "icon.png")  # assets/icon/ 里用户设计的图标，两个名字都认


def square_icon(img, pad=0.04):
    """裁掉四周的透明空白，居中放进正方形（四周留一点边）。小尺寸（如托盘的 16 像素）下图形才不会太小。"""
    box = img.getchannel("A").point(lambda a: 255 if a > 10 else 0).getbbox()
    if box:
        img = img.crop(box)
    side = round(max(img.size) * (1 + 2 * pad))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return canvas


def base_icon(size=64):
    """程序图标：assets/icon/ 里有用户设计的图标就用它，否则用面板的盾牌。返回 (图片, 来源说明)。"""
    for name in ICON_FILES:
        path = ASSETS_DIR / "icon" / name
        if path.is_file():
            return square_icon(Image.open(path).convert("RGBA")).resize((size, size), Image.LANCZOS), str(path)
    return draw_shield(size), "面板的盾牌（assets/icon/ 里还没有图标）"


def load_icons():
    """三种状态的图标：等待游戏、正在记录（右下角小红点）、已暂停（变灰）。"""
    base, _ = base_icon(64)

    custom_rec = ASSETS_DIR / "icon" / "evo-recording.png"
    if custom_rec.is_file():
        recording = Image.open(custom_rec).convert("RGBA").resize((64, 64), Image.LANCZOS)
    else:
        recording = base.copy()
        d = ImageDraw.Draw(recording)
        d.ellipse((38, 38, 63, 63), fill=(255, 255, 255, 255))  # 白边，在红色盾牌上也看得清
        d.ellipse((42, 42, 59, 59), fill=(230, 40, 40, 255))

    gray = ImageOps.grayscale(base.convert("RGB")).convert("RGBA")
    gray.putalpha(base.getchannel("A").point(lambda a: a * 0.6))
    return {"waiting": base, "recording": recording, "paused": gray}


# ---------- 后台记录 ----------

class _QueueStatus(dict):
    """子程序里用：记下的局数一变，就通过队列告诉托盘程序。"""

    def __init__(self, queue):
        super().__init__(saved=0)
        self.queue = queue

    def __setitem__(self, key, value):
        if self.get(key) != value:
            self.queue.put(("status", key, value))
        super().__setitem__(key, value)


def collect_process(stop, queue):
    """子程序的入口：在单独的进程里截屏、识别、记录。游戏关掉后整个进程退出，识别模型占的内存全部还给系统。"""
    try:
        import live_collect

        live_collect.collect(stop=stop, log=lambda msg: queue.put(("log", msg)), status=_QueueStatus(queue))
    except Exception:  # noqa: BLE001 把出错信息交给托盘程序写进日志
        import traceback

        queue.put(("error", traceback.format_exc()))


class Recorder:
    """在后台管理记录：游戏在运行、没暂停时启动记录子程序；游戏关掉、暂停或退出时让它安全停下。"""

    def __init__(self, log, game_names=GAME_PROCESSES, check_seconds=5, on_change=None):
        self.log, self.game_names, self.check_seconds = log, game_names, check_seconds
        self.on_change = on_change or (lambda: None)
        self.paused = False
        self.state = "waiting"  # waiting 等待游戏 / recording 正在记录 / paused 已暂停
        self.saved_before = 0  # 之前几段记录一共记下的局数
        self.status = {"saved": 0}  # 当前这一段记录记下的局数（子程序实时告诉我们）
        self._wake, self._quit = threading.Event(), threading.Event()
        self._stop, self._proc, self._loop_thread = None, None, None
        self._last_error = 0.0

    @property
    def saved(self):
        return self.saved_before + self.status["saved"]

    def start(self):
        self._loop_thread = threading.Thread(target=self._loop, name="recorder", daemon=True)
        self._loop_thread.start()

    def toggle_pause(self):
        self.paused = not self.paused
        self.log(f"点了{'暂停' if self.paused else '继续'}记录")
        self._wake.set()

    def quit(self):
        self._quit.set()
        self._wake.set()
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=60)

    def _recording(self):
        return self._proc is not None and self._proc.is_alive()

    def _loop(self):
        while not self._quit.is_set():
            game = game_running(self.game_names)
            want = game and not self.paused and time.monotonic() - self._last_error > RETRY_AFTER_ERROR
            if want and not self._recording():
                self.log("检测到游戏在运行，开始记录")
                self._start_collect()
            elif not want and self._recording():
                self.log("停止记录：" + ("已暂停" if self.paused else "游戏已关闭"))
                self._stop_collect()
            self._set_state("paused" if self.paused else ("recording" if self._recording() else "waiting"))
            self._wake.wait(self.check_seconds)
            self._wake.clear()
        self._stop_collect()

    def _start_collect(self):
        ctx = multiprocessing.get_context("spawn")
        self.saved_before = self.saved
        self.status = {"saved": 0}
        self._stop, queue = ctx.Event(), ctx.Queue()
        self._proc = ctx.Process(target=collect_process, args=(self._stop, queue), name="evo-collect", daemon=True)
        self._proc.start()
        threading.Thread(target=self._read, args=(self._proc, queue, self.status), name="collect-reader",
                         daemon=True).start()

    def _read(self, proc, queue, status):
        """把子程序发来的日志写进日志文件，局数更新到 status。子程序结束、消息读完就退出。"""
        while proc.is_alive() or not queue.empty():
            try:
                kind, *rest = queue.get(timeout=0.5)
            except Empty:
                continue
            if kind == "log":
                self.log(rest[0])
            elif kind == "status":
                status[rest[0]] = rest[1]
                self.on_change()
            elif kind == "error":
                self._last_error = time.monotonic()
                self.log(f"记录出错，{RETRY_AFTER_ERROR} 秒后重试：\n{rest[0]}")
        self._wake.set()

    def _stop_collect(self):
        if self._recording():
            self._stop.set()
            self._proc.join(timeout=60)  # 等它把没存完的局存好再退出
            if self._proc.is_alive():
                self.log("记录子程序 60 秒内没有停下，强制结束")
                self._proc.terminate()

    def _set_state(self, state):
        if state != self.state:
            self.state = state
        self.on_change()


# ---------- 面板 ----------

def port_free(port):
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) != 0  # 连不上 = 没人在用


class PanelServer:
    """第一次点"打开面板"时，在本程序里启动面板（只接受本机访问），然后用浏览器打开。"""

    def __init__(self, log):
        self.log, self.url, self._server = log, None, None

    def open(self, browser=True):
        if self._server is None:
            import panel_app  # 用到时才加载
            from werkzeug.serving import make_server

            port = next((p for p in PANEL_PORTS if port_free(p)), None)
            if port is None:
                self.log("打开面板失败：端口 5000–5019 都被占用了")
                return None
            self._server = make_server("127.0.0.1", port, panel_app.app, threaded=True)
            threading.Thread(target=self._server.serve_forever, name="panel", daemon=True).start()
            self.url = f"http://127.0.0.1:{port}/"
            self.log(f"面板已启动：{self.url}")
        if browser:
            webbrowser.open(self.url)
        return self.url

    def close(self):
        if self._server is not None:
            self._server.shutdown()


# ---------- 自检 ----------

def check_images(paths, log):
    """识别几张截图，把结果写进日志。打包后用来确认识别模型和配置都在；以后排查问题也能用。"""
    import json

    from live_collect import SCREENS_PATH, is_summary, which_screen
    from match_start import MATCH_START_CONFIG_PATH, read_match_start
    from ocr_probe import create_engine
    from summary_demo import CONFIG_PATH, SMALL_CROP_PARAMS, load_image, read_summary, show
    from xp_page import XP_CONFIG_PATH, read_xp

    cfgs = [json.loads(p.read_text(encoding="utf-8"))
            for p in (CONFIG_PATH, SCREENS_PATH, XP_CONFIG_PATH, MATCH_START_CONFIG_PATH)]
    cfg, screens, xp_cfg, start_cfg = cfgs
    engine = create_engine(SMALL_CROP_PARAMS)
    for path in paths:
        img = load_image(path)
        if img is None:
            log(f"自检 {path}：打不开这张图片")
            continue
        if is_summary(engine, img, cfg):
            results, problems = read_summary(engine, img, cfg)
            v = {k: show(r["value"]) for k, r in results.items()}
            text = f"总结页 第 {v['placement']} 名，击杀/助攻/击倒 {v['kda']}，伤害 {v['damage']}，生存 {v['survival_time']}"
        else:
            screen = which_screen(engine, img, screens)
            problems = []
            if screen == "xp":
                r, problems = read_xp(engine, img, xp_cfg)
                text = f"经验值页 RP {r['rp_delta']}，第 {r['placement']} 名，段位 {r['rank_tier']} {r['rank_division']} {r['rank_progress']}"
            elif screen == "match_start":
                r = read_match_start(engine, img, start_cfg)
                text = f"开局画面 模式 {r['mode']}，地图 {r['map']}"
            else:
                text = "大厅" if screen == "lobby" else "没认出是哪个画面"
        log(f"自检 {Path(path).name}：{text}" + "".join(f"\n    ⚠ {p}" for p in problems))


# ---------- 托盘 ----------

def main():
    multiprocessing.freeze_support()  # 打包成 exe 后，子程序要靠这一行才能正常启动
    parser = argparse.ArgumentParser(description="Evo：托盘图标 + 后台自动记录")
    parser.add_argument("--game", nargs="+", default=sorted(GAME_PROCESSES),
                        help="把哪些进程当作游戏（测试用，默认 Apex 的进程名）")
    parser.add_argument("--check-seconds", type=float, default=5, help="多久看一次游戏有没有在运行（默认 5）")
    parser.add_argument("--open-panel", action="store_true", help="启动后立刻打开面板")
    parser.add_argument("--no-browser", action="store_true", help="和 --open-panel 一起用：只启动面板，不打开浏览器（测试用）")
    parser.add_argument("--check-image", type=Path, nargs="+", metavar="截图",
                        help="自检：识别这些截图，把结果写进日志后退出")
    args = parser.parse_args()

    if args.check_image:
        log = setup_logging().info
        log(f"Evo {storage.APP_VERSION} 自检")
        check_images(args.check_image, log)
        return

    if already_running():
        tell_user("Evo 已经在运行了，请看任务栏右下角的托盘图标。")
        return

    logger = setup_logging()
    log = logger.info
    log(f"Evo {storage.APP_VERSION} 启动。数据文件夹：{DATA_DIR}；把这些进程当作游戏：{'、'.join(args.game)}")

    icons = load_icons()
    tips = {"waiting": "等待 Apex 启动", "recording": "正在记录", "paused": "已暂停记录"}
    icon = pystray.Icon(APP_NAME, icons["waiting"], f"{APP_NAME} · {tips['waiting']}")
    panel = PanelServer(log)

    def refresh():
        state = recorder.state
        text = tips[state] + (f"（已记 {recorder.saved} 局）" if recorder.saved else "")
        icon.icon = icons[state]
        icon.title = f"{APP_NAME} · {text}"
        icon.update_menu()

    recorder = Recorder(log, game_names=set(args.game), check_seconds=args.check_seconds, on_change=refresh)

    def toggle_pause(_icon, _item):
        recorder.toggle_pause()

    def quit_app(_icon, _item):
        log("正在退出……")
        recorder.quit()
        panel.close()
        icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem("打开面板", lambda _i, _m: panel.open(), default=True),
        pystray.MenuItem(lambda _m: "继续记录" if recorder.paused else "暂停记录", toggle_pause),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )

    def setup(ic):
        ic.visible = True
        recorder.start()
        if args.open_panel:
            panel.open(browser=not args.no_browser)

    icon.run(setup=setup)
    log("Evo 已退出")


if __name__ == "__main__":
    main()
