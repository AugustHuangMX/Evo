"""Evo 主程序：任务栏托盘图标 + 后台自动记录 + 按需打开的面板。打包成 Evo.exe 时，入口就是这个文件。

- 每 5 秒看一次 Apex 有没有在运行（只看进程名）。在运行、而且没有暂停，就启动一个记录子程序；
  游戏关掉或暂停时让它安全停下。子程序退出后，识别模型占的内存全部还给系统，托盘本身只占几十 MB，
  不玩游戏时几乎不占 CPU。识别模块万一崩溃也只影响子程序，托盘还在，下次检测到游戏会重新开始
- 打开 Evo 就弹出窗口（加 --background 则只待在托盘里：开机自启时这样打开，见 autostart.py）。Evo 已经在运行时再双击，
  就把窗口弹出来。关掉窗口 Evo 仍在托盘里继续记录，第一次关时会弹通知提示一下
- 右键托盘图标：打开 Evo / 暂停记录 / 退出；单击图标也能打开窗口。面板显示在 Evo 自己的窗口里
  （Windows 自带的 WebView2，见 app_window.py），窗口也在单独的进程里，关掉就把内存还给系统
- 图标：平时是 Evo 图标；正在记录时右下角有个小红点；暂停时变灰。鼠标停在图标上能看到状态
- 同一个数据文件夹同时只能运行一个 Evo（防止同一局被记两遍）
- 运行记录写在数据文件夹的 logs/evo.log（打包后没有终端窗口，出了问题看这里）
- 一键更新（updater.py）：用户点了"安装并重新启动"，新版本的 Evo.exe 跑起来后托盘程序自己退出，
  新版本换好程序文件再把 Evo 打开（它带 --apply-update 运行，不弹窗口、不记录）
- 数据文件夹搬家（data_move.py）：设置页"更改位置"，或数据还在 C 盘旧的默认位置时自动搬到程序旁边。
  搬好后打开新的 Evo（--wait-pid 等这个退出），托盘程序自己退出；新的 Evo 启动时删掉旧位置里搬走的东西

运行：python code/evo_app.py
  用源码运行、这台电脑上装过 Evo 时：战绩记进安装版的数据文件夹（启动前先备份数据库），截图全部存进项目的 captures/，
  见 source_run.py。所以源码版和安装版不能同时开
测试：python code/evo_app.py --game notepad.exe   （把别的程序当作游戏，用来测试"开始/停止记录"；
  不想碰正式数据就先设 EVO_DATA_DIR 指向一个测试用的文件夹）
自检：python code/evo_app.py --check-image 截图.png   （识别截图，结果写进 logs/evo.log）
"""

import argparse
import ctypes
import hashlib
import logging
import multiprocessing
import socket
import sys
import threading
import time
import webbrowser
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty

import source_run

# 用源码运行时：战绩记进安装版的数据文件夹，截图存进项目（见 source_run.py）。要在导入 paths 之前设好；
# 只在直接运行本文件时这样（记录、窗口子程序会继承环境变量），被别的代码 import 时不改
INSTALLED_DATA = source_run.use_installed_data() if __name__ == "__main__" else None

import pystray  # noqa: E402
from PIL import Image, ImageDraw, ImageOps  # noqa: E402

import app_settings  # noqa: E402
import autostart  # noqa: E402
import data_move  # noqa: E402
import storage  # noqa: E402
import webview2  # noqa: E402
from game_detect import GAME_PROCESSES, game_running  # noqa: E402
from icons import base_icon  # noqa: E402
from paths import ASSETS_DIR, CAPTURES_DIR, DATA_DIR, DB_PATH, FROZEN, LOG_DIR  # noqa: E402

APP_NAME = "Evo"
# 同一个数据文件夹只能运行一个 Evo（防止同一局被记两遍）；数据文件夹不同（比如测试用的临时文件夹）互不影响
_INSTANCE_TAG = hashlib.sha1(str(DATA_DIR).lower().encode("utf-8")).hexdigest()[:12]
MUTEX_NAME = f"Local\\EvoTracker-{_INSTANCE_TAG}"
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


SHOW_EVENT_NAME = f"Local\\EvoTrackerShow-{_INSTANCE_TAG}"  # 再次双击 Evo 时，用这个信号让已经在运行的 Evo 弹出窗口
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateEventW.restype = _k32.OpenEventW.restype = ctypes.c_void_p
_k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.SetEvent.argtypes = _k32.CloseHandle.argtypes = [ctypes.c_void_p]
_k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]


def ask_running_evo_to_show():
    """Evo 已经在运行：通知它把窗口弹出来。成功返回 True；对方是不认识这个信号的旧版时返回 False。"""
    handle = _k32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)  # EVENT_MODIFY_STATE
    if not handle:
        return False
    _k32.SetEvent(handle)
    _k32.CloseHandle(handle)
    return True


def listen_for_show(callback):
    """在后台一直等"弹出窗口"的信号（用户又双击了 Evo），收到就调用 callback。"""
    handle = _k32.CreateEventW(None, False, False, SHOW_EVENT_NAME)  # 自动复位：收到一次就清掉

    def loop():
        while True:
            if _k32.WaitForSingleObject(handle, 1000) == 0:  # WAIT_OBJECT_0：收到信号
                callback()

    threading.Thread(target=loop, name="show-signal", daemon=True).start()


UPDATE_FIRST_DELAY = 15  # 秒：启动后等一会儿再检查新版本，不和打开窗口抢
UPDATE_FIRST_DELAY_BACKGROUND = 120  # 开机自启（--background）时：刚开机网络可能还没连上，等久一点
UPDATE_EVERY = 8 * 3600  # 之后每 8 小时检查一次


def start_update_checker(log, notify, stop, first_delay=UPDATE_FIRST_DELAY):
    """用户同意了自动检查新版本（设置 update_auto）才联网：启动 15 秒（开机自启时 2 分钟）后查一次，之后每 8 小时一次。
    发现新版本时用托盘通知提醒，同一个版本只提醒一次；窗口顶部的提示条由面板根据检查结果显示。"""

    def loop():
        if stop.wait(first_delay):
            return
        while True:
            if app_settings.get("update_auto") is True:
                import update_check

                result = update_check.check(attempts=2)  # 网不好时隔几秒再试一次
                if not result["ok"]:
                    log(f"检查新版本：没查到（{result['error']}）")
                elif result["newer"]:
                    version = result["latest"]["version"]
                    log(f"检查新版本：发现 {version}")
                    # 点过"以后再说"的版本、已经提醒过的版本都不再弹通知
                    if update_check.available() and app_settings.get("update_notified") != version:
                        notify(f"发现 Evo 新版本 {version}，打开 Evo 查看更新内容。", "Evo 有新版本")
                        app_settings.set_value("update_notified", version)
            if stop.wait(UPDATE_EVERY):
                return

    threading.Thread(target=loop, name="update-check", daemon=True).start()


STATS_FIRST_DELAY = 60  # 秒：启动后等一会儿再发匿名统计，排在检查新版本后面
STATS_FIRST_DELAY_BACKGROUND = 180  # 开机自启时：等网络连上
STATS_EVERY = 3600  # 之后每小时一次（没有要发的就不联网；2026-10-22 用户决定，原来是 8 小时）


def start_telemetry(log, stop, first_delay=STATS_FIRST_DELAY):
    """用户同意了匿名统计（telemetry.py）才联网：启动 1 分钟（开机自启时 3 分钟）后发一次，之后每小时一次。
    没同意时什么都不做；没有要发的也不联网。发了什么、没发出去都写进日志。"""

    def loop():
        if stop.wait(first_delay):
            return
        while True:
            import telemetry

            result = telemetry.send()
            if result is not None and not result["ok"]:
                log(f"匿名统计：没发出去（{result['error']}），下次再发")
            elif result is not None and (result["matches"] or result["ping"]):
                log(f"匿名统计：发送了 {result['matches']} 局" + ("和今天的日活" if result["ping"] else ""))
            if stop.wait(STATS_EVERY):
                return

    threading.Thread(target=loop, name="telemetry", daemon=True).start()


SOURCE_BACKUPS = 5  # 源码运行前的数据库备份留最近几份


def backup_before_source_run(log):
    """用源码运行、战绩记进安装版的数据库时，先备份一份（正在改的代码万一写坏了正式数据，还能找回来）。
    备份放在数据库旁边，叫 evo.db.bak-source-时间，只留最近几份。返回备份文件（没有数据库时返回 None）。"""
    if not DB_PATH.is_file():
        return None
    import sqlite3

    backup = DB_PATH.with_name(f"{DB_PATH.name}.bak-source-{time.strftime('%Y%m%d-%H%M%S')}")
    conn = sqlite3.connect(DB_PATH)  # 不用 storage.connect：它会先升级表结构，备份要留升级前的样子
    try:
        storage.backup_db(conn, backup)
    finally:
        conn.close()
    for old in sorted(DB_PATH.parent.glob(f"{DB_PATH.name}.bak-source-*"))[:-SOURCE_BACKUPS]:
        old.unlink(missing_ok=True)
    log(f"用源码运行：战绩记进安装版的数据库，启动前已备份到 {backup.name}；截图全部存进项目 {CAPTURES_DIR}")
    return backup


def tell_user(text):
    """打包后没有终端，用弹窗告诉用户；用源码运行时打印到终端。"""
    if FROZEN:
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x40)
    else:
        print(text)


# ---------- 图标 ----------

def load_icons():
    """三种状态的图标：等待游戏、正在记录（右下角小红点）、已暂停（变灰）。"""
    base, _ = base_icon(64)

    custom_rec = ASSETS_DIR / "icon" / "evo-recording.png"
    if custom_rec.is_file():
        recording = Image.open(custom_rec).convert("RGBA").resize((64, 64), Image.LANCZOS)
    else:
        recording = base.copy()
        d = ImageDraw.Draw(recording)
        d.ellipse((38, 38, 63, 63), fill=(255, 255, 255, 255))  # 白边：和图标的深色底分开
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
        self.held = False  # 正在搬数据文件夹（data_move.py）：不开始记录，正在记录的也停下
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

    def hold(self, on):
        """搬数据文件夹时按住记录（data_move.py）：on=True 不开始记录、正在记录的停下；on=False 恢复。"""
        self.held = on
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
            want = (game and not self.paused and not self.held
                    and time.monotonic() - self._last_error > RETRY_AFTER_ERROR)
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
        # 名字不要改：设置页删截图时靠它认出"正在记录"，不动正在记录的文件夹（screenshots.recording_folder）
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


def window_process(url, focus, queue):
    """窗口子进程的入口：打开 Evo 窗口，关掉窗口这个进程就退出。打不开时把原因交给托盘程序。
    电脑上没有 WebView2 时先问用户要不要装（webview2.py）；不装、没装上，就让托盘程序改用浏览器打开。"""
    try:
        if not webview2.available():
            if not webview2.ask():
                queue.put(("no-webview2", "用户选了先不装"))
                return
            ok, detail = webview2.install()
            if not ok:
                webview2.tell_failed()
                queue.put(("no-webview2", detail))
                return
            queue.put(("installed", detail))
        import app_window

        app_window.run(url, focus)
    except Exception:  # noqa: BLE001
        import traceback

        queue.put(("error", traceback.format_exc()))


class PanelServer:
    """第一次点"打开面板"时，在本程序里启动面板（只接受本机访问），然后在单独的进程里打开 Evo 窗口。
    窗口已经开着，就把它调到最前面；窗口打不开（比如电脑上没有 WebView2、用户不装），改用浏览器打开。"""

    def __init__(self, log, on_window_closed=None):
        self.log, self.url, self._server = log, None, None
        self._window, self._focus = None, None
        self.on_window_closed = on_window_closed or (lambda: None)  # 用户关掉窗口后调用（退出程序时不算）
        self._closing = False
        self._no_webview2 = False  # 这次运行里已经问过要不要装 WebView2、没装上：不再问，直接用浏览器

    def open(self, window=True):
        url = self._start_server()
        if url is None or not window:
            return url
        if self._window is not None and self._window.is_alive():
            self._focus.set()  # 已经开着：调到最前面
            return url
        if self._no_webview2 and not webview2.available():  # 用户后来自己装好了，就照常打开窗口
            webbrowser.open(url)
            return url
        ctx = multiprocessing.get_context("spawn")
        self._focus, queue = ctx.Event(), ctx.Queue()
        self._window = ctx.Process(target=window_process, args=(url, self._focus, queue), name="evo-window",
                                   daemon=True)
        self._window.start()
        threading.Thread(target=self._watch_window, args=(self._window, queue, url), name="window-watch",
                         daemon=True).start()
        return url

    def _watch_window(self, proc, queue, url):
        problem = None
        while True:  # 等窗口关闭；中间装好 WebView2 时马上记进日志
            try:
                kind, detail = queue.get(timeout=1)
            except Empty:
                if proc.is_alive():
                    continue
                break
            if kind == "installed":
                self.log(detail)
            else:
                problem = kind, detail
        if problem is None:
            if not self._closing:
                self.on_window_closed()  # 用户关掉了窗口（Evo 还在托盘里）
            return
        kind, detail = problem
        if kind == "no-webview2":
            self._no_webview2 = True
            self.log(f"电脑上没有 WebView2（{detail}），改用浏览器打开面板")
        else:
            self.log(f"Evo 窗口打不开，改用浏览器打开面板。原因：\n{detail}")
        webbrowser.open(url)

    def _start_server(self):
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
        return self.url

    def close(self):
        self._closing = True
        if self._window is not None and self._window.is_alive():
            self._window.terminate()
        if self._server is not None:
            self._server.shutdown()


# ---------- 自检 ----------

def check_images(paths, log):
    """识别几张截图，把结果写进日志。打包后用来确认识别模型和配置都在；以后排查问题也能用。
    每张图各认一次游戏语言（screens.GameScreens），按认出的语言读（英文截图也能自检）。"""
    import game_lang
    from eliminated import read_eliminated
    from hud import TOP_FIELDS, TOP_LABELS, Hud
    from lobby import LobbyReader, describe
    from match_start import read_match_start
    from ocr import SMALL_CROP_PARAMS, create_engine, load_config, load_image
    from player_name import PlayerNameReader
    from screens import GameScreens
    from summary_page import is_champion, read_summary, show
    from xp_page import read_xp

    engine = create_engine(SMALL_CROP_PARAMS)
    detector = GameScreens(engine)
    hud = Hud(engine)
    if detector.missing or hud.missing:
        log(f"自检：这些画面没有参照图（config/templates/）：{'、'.join(detector.missing + hud.missing)}")
    for path in paths:
        try:
            img = load_image(path)
        except ValueError:
            log(f"自检 {path}：打不开这张图片")
            continue
        detector = GameScreens(engine)  # 每张图重新认语言（几张图可能是不同语言的）
        screen, lang = detector.detect(img), detector.lang
        cfg = load_config("summary.json", lang)
        if hud.lang != lang:
            hud.use_language(lang)
        problems = []
        if len(detector.detectors) > 1 and screen is not None:
            log(f"自检 {Path(path).name}：游戏语言 {game_lang.label(lang)}")
        if screen == "summary":
            results, problems = read_summary(engine, img, cfg)
            v = {k: show(r["value"]) for k, r in results.items()}
            name = PlayerNameReader(engine, load_config("player_name.json", lang)).read(img, "summary")
            text = (f"总结页 第 {v['placement']} 名，击杀/助攻/击倒 {v['kda']}，伤害 {v['damage']}，生存 {v['survival_time']}，"
                    f"玩家名 {name[0] if name else '没读到'}{'，捍卫者（冠军）' if is_champion(engine, img, cfg) else ''}")
        elif screen == "xp":
            r, problems = read_xp(engine, img, load_config("xp.json", lang))
            text = f"经验值页 RP {r['rp_delta']}，第 {r['placement']} 名，段位 {r['rank_tier']} {r['rank_division']} {r['rank_progress']}"
        elif screen == "match_start":
            r = read_match_start(engine, img, load_config("match_start.json", lang))
            text = f"开局画面 模式 {r['mode']}，地图 {r['map']}"
        elif screen == "eliminated":
            text = f"淘汰画面 第 {read_eliminated(engine, img, load_config('eliminated.json', lang))['placement']} 名"
        elif screen == "champion":
            text = "冠军横幅（捍卫者）"
        elif screen == "squad_out":
            text = "返回大厅按钮（小队已淘汰）"
        elif screen == "lobby":
            text = f"大厅 {describe(LobbyReader(engine, load_config('lobby.json', lang)).read(img))}"
        elif hud.own_legend(img) is not None:
            values, _, _ = hud.read_top(hud.top_snip(img))
            mates = [hud.legends.name(k) if k else "-" for k in hud.mate_legends(img)]
            top = "，".join(f"{TOP_LABELS[k]} {'-' if values[k] is None else values[k]}" for k in TOP_FIELDS)
            text = (f"对局中 自己 {hud.legends.name(hud.own_legend(img))}{'（倒地）' if hud.knocked(img) else ''}，"
                    f"队友 {'、'.join(mates)}；右上角 {top}")
        else:
            text = "观战（成盒以后）" if hud.spectating(img) else "没认出是哪个画面"
        log(f"自检 {Path(path).name}：{text}" + "".join(f"\n    ⚠ {p}" for p in problems))


# ---------- 托盘 ----------

def main():
    multiprocessing.freeze_support()  # 打包成 exe 后，子程序要靠这一行才能正常启动
    parser = argparse.ArgumentParser(description="Evo：托盘图标 + 后台自动记录")
    parser.add_argument("--game", nargs="+", default=sorted(GAME_PROCESSES),
                        help="把哪些进程当作游戏（测试用，默认 Apex 的进程名）")
    parser.add_argument("--check-seconds", type=float, default=5, help="多久看一次游戏有没有在运行（默认 5）")
    parser.add_argument("--background", action="store_true",
                        help="启动时不弹窗口，只待在托盘里（开机自启时用）")
    parser.add_argument("--no-window", action="store_true", help="启动时只启动面板服务，不弹窗口（测试用）")
    parser.add_argument("--check-image", type=Path, nargs="+", metavar="截图",
                        help="自检：识别这些截图，把结果写进日志后退出")
    # 一键更新时，旧的 Evo 用这几个参数打开新版本的 Evo.exe，让它把自己装进程序文件夹（见 updater.py）
    parser.add_argument("--apply-update", type=Path, metavar="程序文件夹", help=argparse.SUPPRESS)
    # 一键更新装好后、数据文件夹搬好后（data_move.py），新打开的 Evo 先等旧的退出
    parser.add_argument("--wait-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--from-version", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.apply_update:
        import updater

        updater.apply_update(args.apply_update, args.wait_pid, args.from_version)
        return

    if args.check_image:
        log = setup_logging().info
        log(f"Evo {storage.APP_VERSION} 自检")
        check_images(args.check_image, log)
        return

    if args.wait_pid:
        import updater

        updater.wait_exit(args.wait_pid, data_move.OLD_EXIT_WAIT)

    if already_running():
        # 再次双击 Evo：让已经在运行的那个把窗口弹出来，自己安静退出
        if INSTALLED_DATA is not None:
            print("安装版 Evo 正在运行（和源码版用同一个数据文件夹，只能开一个），已让它弹出窗口。")
            print("要用源码记录：先右键托盘图标 → 退出安装版，再运行 python code/evo_app.py")
            ask_running_evo_to_show()
        elif not ask_running_evo_to_show():
            tell_user("Evo 已经在运行了，请看任务栏右下角的托盘图标。")
        return

    logger = setup_logging()
    log = logger.info
    # 后台线程里没接住的错误也写进日志（打包后没有终端窗口，出了问题只能看日志）
    threading.excepthook = lambda a: logger.error(f"后台线程 {a.thread.name if a.thread else ''} 出错",
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    log(f"Evo {storage.APP_VERSION} 启动{'（源码运行）' if not FROZEN else ''}。数据文件夹：{DATA_DIR}；"
        f"把这些进程当作游戏：{'、'.join(args.game)}" + ("；只进托盘、不弹窗口（--background）" if args.background else ""))
    if INSTALLED_DATA is not None:
        backup_before_source_run(log)
    data_move.after_start(log)  # 刚搬过数据文件夹：删掉旧位置里搬走的东西，窗口顶部说一次
    autostart.after_start(log)  # 开机自启：程序换了位置就跟着改；状态写进日志
    import updater

    if FROZEN:
        updater.after_start(log)  # 刚一键更新过：结果写进日志、窗口顶部显示一次；过一会儿删掉装过的版本
        webview2.unblock(log)  # 用 Windows 自带的解压缩解开的：去掉 DLL 上"从网上下载的"标记，不然窗口打不开

    icons = load_icons()
    tips = {"waiting": "等待 Apex 启动", "recording": "正在记录", "paused": "已暂停记录"}
    icon = pystray.Icon(APP_NAME, icons["waiting"], f"{APP_NAME} · {tips['waiting']}")

    def window_closed():
        """第一次关掉窗口时提示一下：Evo 没有退出，还在托盘里。只提示这一次。"""
        if not app_settings.get("tray_tip_shown"):
            icon.notify("Evo 还在后台运行，打游戏时会自动记录。右键托盘图标可以退出。", APP_NAME)
            app_settings.set_value("tray_tip_shown", True)
            log("第一次关掉窗口：已提示 Evo 还在后台运行")

    panel = PanelServer(log, on_window_closed=window_closed)

    def refresh():
        state = recorder.state
        text = tips[state] + (f"（已记 {recorder.saved} 局）" if recorder.saved else "")
        icon.icon = icons[state]
        icon.title = f"{APP_NAME} · {text}"
        icon.update_menu()

    recorder = Recorder(log, game_names=set(args.game), check_seconds=args.check_seconds, on_change=refresh)

    def toggle_pause(_icon, _item):
        recorder.toggle_pause()

    stop_update_checker = threading.Event()

    def shut_down(reason):
        log(reason)
        stop_update_checker.set()
        recorder.quit()
        panel.close()
        icon.stop()

    def quit_app(_icon, _item):  # 托盘菜单的"退出"（pystray 只接受 0–2 个参数的函数）
        shut_down("正在退出……")

    # 一键更新：Apex 开着时不能安装；安装时新版本跑起来以后，让托盘程序像点了"退出"一样退出
    updater.connect(game_running=lambda: game_running(recorder.game_names),
                    quit_app=lambda: shut_down("一键更新：新版本已经开始安装，正在退出……"), log=log)
    # 搬数据文件夹：Apex 开着时不搬；搬的时候按住记录；搬好后打开新的 Evo，自己像点了"退出"一样退出
    data_move.connect(game_running=lambda: game_running(recorder.game_names), hold=recorder.hold,
                      quit_app=lambda: shut_down("数据文件夹搬好了，正在退出，新位置的 Evo 马上打开……"),
                      relaunch=data_move.relaunch)

    icon.menu = pystray.Menu(
        pystray.MenuItem("打开 Evo", lambda _i, _m: panel.open(), default=True),
        pystray.MenuItem(lambda _m: "继续记录" if recorder.paused else "暂停记录", toggle_pause),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )

    def setup(ic):
        try:
            ic.visible = True
            recorder.start()
            listen_for_show(lambda: panel.open())  # 再次双击 Evo 时弹出窗口
            start_update_checker(log, icon.notify, stop_update_checker,  # 用户同意了才联网检查新版本
                                 UPDATE_FIRST_DELAY_BACKGROUND if args.background else UPDATE_FIRST_DELAY)
            start_telemetry(log, stop_update_checker,  # 用户同意了才发匿名统计
                            STATS_FIRST_DELAY_BACKGROUND if args.background else STATS_FIRST_DELAY)
            try:  # 绑定了微信小程序才联网，定时上传战绩（cloud_sync.py）。小程序的文件只在开发者电脑上，
                import cloud_sync  # 不上传 GitHub、小程序上线前不打进安装包（2026-10-24 用户决定）：没有就不同步
            except ImportError:
                cloud_sync = None
            if cloud_sync is not None:
                cloud_sync.start(log, stop_update_checker, background=args.background)
            data_move.watch_auto_move(log, stop_update_checker)  # 数据还在 C 盘旧的默认位置：Apex 没开时自动搬到程序旁边
            if not args.background:
                panel.open(window=not args.no_window)  # 一打开 Evo 就弹出窗口，而不是只在托盘里
        except Exception:  # noqa: BLE001  打包后没有终端窗口：出错要写进日志，不然什么都看不到
            logger.exception("启动时出错")

    icon.run(setup=setup)
    log("Evo 已退出")


if __name__ == "__main__":
    main()
