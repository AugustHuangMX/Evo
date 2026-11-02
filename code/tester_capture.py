"""测试工具（evo_tester.py）的截屏和打包：截法和 Evo 的记录程序一样（dxcam、主显示器、同样的参数），截到的就是 Evo 看到的样子。

三种截法（config/tester.json 的 capture）：
- every（打一局、排查问题，Recorder）：游戏开着时每 interval 秒截一张（和上一张存的几乎一样就不存），比 1920 宽的缩到
  1920 宽（Evo 识别本来就按 1920×1080 换算）；每分钟另存一张原尺寸
- still（赛季数据页，StillRecorder）：画面停稳了、和上一张存的不一样才存一张原尺寸，"嘀"一声
- weapons（靶场：武器，WeaponRecorder）：右下角手上的武器换了就存几张（整张缩小的 + 右下角无损的一块），"嘀"一声
都一样的：游戏没开时每 10 秒看一眼、每分钟最多存一张（看得出截屏本身通不通）；每次截屏记一行（captures.csv）：
拿到新画面没有、和上一张差多少。游戏开着却一直"没有新画面" = 截屏卡住了（Evo 这时拿到的是 dxcam 缓存的上一张旧图）；
每分钟给每个显示器存一张很小的缩略图（monitors/，用 mss，和 dxcam 不是一条路）：看得出游戏在哪个屏幕上，看不清字
- 打包：整个文件夹打成一个 zip（JPEG、PNG 不再压缩），核对后删掉文件夹
- 只看不动（原则 1）：只截屏、只读进程名
"""

import csv
import ctypes
import io
import shutil
import sys
import time
import uuid
import zipfile
from collections import Counter
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from game_detect import GAME_PROCESSES
from layout import Mapping
from tester_info import KNOWN_APPS

INTERVAL = 2.0  # 游戏开着时每隔几秒截一张
IDLE_INTERVAL = 10.0  # 游戏没开时每隔几秒看一眼
IDLE_SAVE_SECONDS = 60  # 游戏没开时最多每隔这么久存一张
SMALL_WIDTH = 1920  # 比这个宽的截图缩到这个宽
JPEG_QUALITY = 85
FULL_SECONDS = 60  # 游戏开着时每隔这么久另存一张原尺寸
FULL_QUALITY = 92  # 原尺寸那张：质量高一点，颜色不压缩（4:4:4）
THUMB_SECONDS = 60  # 每隔这么久给每个显示器存一张缩略图
THUMB_WIDTH = 480
SAME = 1.5  # 和上一张存的相比，缩略图平均每个像素差不到这么多（0–255），算"没变"，不存（同 tools/frame_capture.py）
THUMB_STEP = 16  # 比较"变没变"时隔这么多像素取一个
GAME_CHECK_SECONDS = 5  # 每隔几秒看一眼进程列表
GAME_GONE_CHECKS = 3  # 游戏开过以后，连续这么多次看不到 = 关掉了
STALE_SECONDS = 60  # 游戏开着时连续这么久没有新画面，在窗口里提示一次（每 2 秒一张时是 30 次）
STATUS_SECONDS = 60  # 每隔这么久在窗口里报一次进度
MAX_MINUTES = 60
MAX_MB = 1500
SPARE_MB = 500  # 硬盘至少还要剩"已截的大小 + 这么多"（打包时要再占一份）
CSV_FIELDS = ["序号", "时间", "游戏开着", "结果", "宽", "高", "和上一张差", "文件", "毫秒"]

# "停稳了才存"（赛季数据页，StillRecorder）：2026-11-02 用 11-01 的数据页截图量的
FINE_STEP = 4  # 比较时隔这么多像素取一个
CHANGE_LEVEL = 40  # 一个像素变了这么多（0–255）才算"变了"（同 config/season_stats.json 的 unchanged.level）
STILL_SHARE = 0.0005  # 和上一次截到的相比，变了的像素不到这么多（0.05%），算没动
STILL_SECONDS = 1.0  # 没动这么久算停稳了
NEW_SHARE = 0.001  # 和上一张存的相比，变了的像素有这么多（0.1%），算换了一页（同一页 0.02%，不同赛季至少 0.21%）

# "换了武器才存"（靶场：武器，WeaponRecorder）
WEAPON_SETTLE = 0.5  # 图标换了以后，等这么久再存（拾取动画只有一两张）
WEAPON_GAP = 1.0  # 同一把隔这么久存一张
WEAPON_SAVES = 3  # 每个样子存几张（tools/weapon_templates.py 要至少 2 张一样的才做参照图）
WEAPON_NEW = 0.3  # 图标的白色像素有这么多不一样（占两者合起来的），算换了一把
WEAPON_FALLBACK = 10.0  # 这么久没存过就存一张
CROP_BOX = [1400, 880, 520, 200]  # 右下角存无损的一块（1920×1080 基准，贴下边）：武器栏的图标和下面两把武器的名字


# ---------- 截屏 ----------

def _thumb(frame):
    """比较"变没变"用：隔 THUMB_STEP 个像素取一个，转成灰度。"""
    return frame[::THUMB_STEP, ::THUMB_STEP, :3].astype(np.float32).mean(axis=2)


def encode_jpeg(frame, width=None, quality=JPEG_QUALITY, subsampling=2):
    """BGR（dxcam 给的，和 Evo 一样）的截图 → JPEG。width：比它宽就缩到它宽。subsampling：0 = 颜色不压缩（4:4:4）。"""
    img = Image.fromarray(np.ascontiguousarray(frame[:, :, 2::-1]))
    if width and img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.Resampling.BOX)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, subsampling=subsampling)
    return buf.getvalue()


class Recorder:
    """截屏、存图、把每次截屏的结果记进 captures.csv。游戏开着时每 interval 秒一张，画面没变不存（"打一局""排查问题"）。
    camera：dxcam 的截屏对象（None = 建不起来，只看进程、存缩略图）；names()：现在开着的进程名；
    thumbs(时间)：给每个显示器存缩略图，返回存了多少字节（可以不给）；log(文字)：写进窗口和 tool_log.txt。
    full_seconds：游戏开着时每隔这么久另存一张原尺寸（None = 不存）；beep()：存好一样东西时"嘀"一声（可以不给）。
    clock、wait(事件, 秒)、now：测试时换成假时钟；free_mb()：硬盘还剩多少 MB。"""

    def __init__(self, out_dir, camera, names, log, thumbs=None, minutes=MAX_MINUTES, max_mb=MAX_MB,
                 games=GAME_PROCESSES, clock=time.monotonic, wait=None, now=datetime.now, free_mb=None,
                 interval=INTERVAL, full_seconds=FULL_SECONDS, beep=None):
        self.out_dir, self.camera, self.names, self.log, self.thumbs = Path(out_dir), camera, names, log, thumbs
        self.minutes, self.max_mb = minutes, max_mb
        self.interval, self.full_seconds = interval, full_seconds
        self.stale_warn = max(1, round(STALE_SECONDS / interval))  # 连续这么多次没有新画面，提示一次
        self.beep = beep or (lambda: None)
        self.games = {g.lower() for g in games}
        self.clock, self.now = clock, now
        self.wait = wait or (lambda event, seconds: event.wait(seconds))
        self.free_mb = free_mb or (lambda: shutil.disk_usage(self.out_dir).free / 2 ** 20)
        self.game_on = False
        self.gone = 0  # 游戏开过以后，连续几次没看到
        self.games_seen = {}  # 看到过的游戏进程名 → 第一次看到的时间
        self.apps = set()  # 开着的 KNOWN_APPS
        self.grabs = self.saved = self.full = self.same = self.no_new = self.no_new_game = self.errors = 0
        self.thumb_bytes = 0
        self.stale = self.longest_stale = 0  # 游戏开着时连续没有新画面的次数（现在、最长）
        self.sizes = Counter()  # 截到的尺寸 → 次数
        self.nbytes = 0
        self.size = None
        self.last_thumb = None  # 上一张存下的截图的缩略图
        self.last_idle_save = None
        self.next_full = None
        self.started = self.ended = None
        self._csv_file = open(self.out_dir / "captures.csv", "w", newline="", encoding="utf-8-sig")
        self._csv = csv.DictWriter(self._csv_file, CSV_FIELDS)
        self._csv.writeheader()

    def close(self):
        self._csv_file.close()

    @property
    def mb(self):
        return (self.nbytes + self.thumb_bytes) / 2 ** 20

    def run(self, stop):
        """截到 stop（threading.Event）被设上、游戏关掉、到时间、截满或硬盘快满为止，返回结束的原因。"""
        self.started = self.now()
        start = self.clock()
        deadline = start + self.minutes * 60
        next_game = next_thumb = start
        next_status = start + STATUS_SECONDS
        try:
            while True:
                tick = self.clock()
                if stop.is_set():
                    return "点了结束"
                if tick >= deadline:
                    return f"截满了 {self.minutes:g} 分钟"
                if self.mb >= self.max_mb:
                    return f"截图满了 {self.max_mb} MB"
                if self.free_mb() < self.mb + SPARE_MB:
                    return "硬盘空间快不够了"
                if tick >= next_game:
                    next_game = tick + GAME_CHECK_SECONDS
                    if self._check_game():
                        return "游戏关掉了"
                if self.camera is not None:
                    self._grab(tick)
                if self.thumbs is not None and tick >= next_thumb:
                    next_thumb = tick + THUMB_SECONDS
                    self.thumb_bytes += self.thumbs(self.now())
                if tick >= next_status:
                    next_status = tick + STATUS_SECONDS
                    self.log(self.status())
                interval = self.interval if self.game_on else IDLE_INTERVAL
                self.wait(stop, max(0.0, interval - (self.clock() - tick)))
        finally:
            self.ended = self.now()

    def status(self):
        state = "游戏开着" if self.game_on else "等游戏打开"
        stale = f"；⚠ 没有新画面 {self.no_new_game} 次" if self.no_new_game else ""
        return f"[{self.now():%H:%M:%S}] {state}：已存 {self.saved} 张（{self.mb:.0f} MB）{stale}"

    def _check_game(self):
        """看一眼进程列表（只看名字）。返回 True = 游戏开过、现在关掉了。"""
        names = {n.lower() for n in self.names()}
        self.apps |= names & KNOWN_APPS.keys()
        running = sorted(names & self.games)
        if running:
            self.gone = 0
            for name in running:
                self.games_seen.setdefault(name, f"{self.now():%H:%M:%S}")
            if not self.game_on:
                self.game_on = True
                self.next_full = None
                self.log(f"[{self.now():%H:%M:%S}] 看到游戏了（{'、'.join(running)}），开始每 {self.interval:g} 秒截一张")
            return False
        if self.game_on:
            self.gone += 1
            if self.gone >= GAME_GONE_CHECKS:
                self.game_on = False
                self.log(f"[{self.now():%H:%M:%S}] 游戏关掉了")
                return True
        return False

    def _grab(self, tick):
        self.grabs += 1
        now = self.now()
        row = {"序号": self.grabs, "时间": f"{now:%H:%M:%S}", "游戏开着": "是" if self.game_on else "否"}
        began = time.perf_counter()
        try:
            frame = self.camera.grab(new_frame_only=True)  # 拿不到新画面时返回 None（Evo 这时用的是缓存的上一张）
        except Exception as e:  # 切换分辨率、切出游戏等
            self.errors += 1
            self._row(row, f"出错：{e}", began)
            return
        if frame is None:
            self.no_new += 1
            if self.game_on:
                self.no_new_game += 1
                self.stale += 1
                self.longest_stale = max(self.longest_stale, self.stale)
                if self.stale == self.stale_warn:
                    self.log(f"[{now:%H:%M:%S}] ⚠ 游戏开着，但已经连续 {self.stale_warn} 次（约 {STALE_SECONDS} 秒）"
                             "没截到新画面：截屏可能卡住了（会记在诊断信息里，接着截）")
            files = []
            result = self._no_new_frame(tick, now, files)
            row["文件"] = " ".join(files)
            self._row(row, result, began)
            return
        self.stale = 0
        h, w = frame.shape[:2]
        self.sizes[f"{w}x{h}"] += 1
        if (w, h) != self.size:
            self.size = (w, h)
            self._size_changed()
            self.log(f"[{now:%H:%M:%S}] 截到的画面：{w}x{h}")
        files = []
        result, diff = self._keep(frame, tick, now, files)
        if self.game_on and self.full_seconds and (self.next_full is None or tick >= self.next_full):
            self.next_full = tick + self.full_seconds
            self.full += 1
            files.append(self._save("full", f"{now:%H%M%S}_{w}x{h}.jpg",
                                    encode_jpeg(frame, None, FULL_QUALITY, subsampling=0)))
        row.update({"宽": w, "高": h, "和上一张差": diff, "文件": " ".join(files)})
        self._row(row, result, began)

    def _size_changed(self):
        """截到的画面尺寸变了（切分辨率、全屏和桌面来回切）：以前的"上一张"不能拿来比了。"""
        self.last_thumb = None

    def _no_new_frame(self, tick, now, files):
        """这次没截到新画面（dxcam 返回 None）：返回 captures.csv 里的结果。别的截法可以改写。"""
        return "没有新画面"

    def _keep(self, frame, tick, now, files):
        """这一张存不存（存了就把文件名加进 files），返回 (结果, captures.csv 里"和上一张差"那一栏)。
        这里是"每隔几秒一张、画面没变不存"；游戏没开时每分钟最多存一张。别的截法改写这个。"""
        h, w = frame.shape[:2]
        small = _thumb(frame)
        diff = None if self.last_thumb is None else float(np.mean(np.abs(small - self.last_thumb)))
        shown = "" if diff is None else f"{diff:.1f}"
        idle_due = self.last_idle_save is None or tick - self.last_idle_save >= IDLE_SAVE_SECONDS
        if diff is not None and diff < SAME:
            self.same += 1
            return "没变", shown
        if self.game_on or idle_due:
            self.saved += 1
            files.append(self._save("frames", f"{self.saved:04d}_{now:%H%M%S}_{w}x{h}.jpg",
                                    encode_jpeg(frame, SMALL_WIDTH)))
            self.last_thumb = small
            if not self.game_on:
                self.last_idle_save = tick
            return "存了", shown
        return "没存（游戏没开）", shown

    def _save(self, folder, name, data):
        path = self.out_dir / folder / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        self.nbytes += len(data)
        return f"{folder}/{name}"

    def _row(self, row, result, began):
        row.update({"结果": result, "毫秒": f"{(time.perf_counter() - began) * 1000:.0f}"})
        self._csv.writerow(row)
        self._csv_file.flush()

    def summary(self):
        return {"开始": f"{self.started:%Y-%m-%d %H:%M:%S}" if self.started else None,
                "结束": f"{self.ended:%Y-%m-%d %H:%M:%S}" if self.ended else None,
                "截屏次数": self.grabs, "存了": self.saved, "原尺寸": self.full, "没变没存": self.same,
                "没有新画面": self.no_new, "游戏开着时没有新画面": self.no_new_game,
                "游戏开着时最长连续没有新画面": self.longest_stale, "出错": self.errors,
                "截到的尺寸": dict(self.sizes), "大小MB": round(self.mb, 1),
                "看到的游戏进程": self.games_seen, "开着的软件": sorted(self.apps)}


def _fine(frame):
    """比较"停稳没有""换页没有"用：隔 FINE_STEP 个像素取一个，转成灰度（比 _thumb 细，换赛季时只变几个数字也看得出）。"""
    return frame[::FINE_STEP, ::FINE_STEP, :3].astype(np.int16).mean(axis=2)


def changed_share(a, b):
    """两张 _fine 之间，变化超过 CHANGE_LEVEL 的像素占多少（0–1）。"""
    return float(np.mean(np.abs(a - b) > CHANGE_LEVEL))


class StillRecorder(Recorder):
    """"赛季数据页"：画面停稳了（STILL_SECONDS 里几乎没变），并且和上一张存的不一样，才存一张原尺寸，"嘀"一声。
    数据页的背景不动，换一个赛季时只有右边一栏的数字变（2026-11-01 的截图：同一页之间变化的像素 0.02%，
    不同赛季的两页至少 0.21%），所以数"变化大的像素占多少"，不看整张平均差多少（不同赛季的两页平均只差 0.45）。
    不用知道数据页在画面的哪里：没适配的分辨率、语言也照样存。下拉菜单停着时也会存（看得出有哪些赛季）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pages = 0  # 存了几页
        self.prev_fine = self.last_fine = None  # 上一次截到的、上一张存的
        self.latest = None  # 上一次截到的画面（没有新画面时接着用它）
        self.still_since = None

    def _size_changed(self):
        super()._size_changed()
        self.prev_fine = self.last_fine = self.latest = self.still_since = None

    def _keep(self, frame, tick, now, files):
        if not self.game_on:
            return super()._keep(frame, tick, now, files)
        self.latest = np.array(frame, copy=True)  # dxcam 下次可能用同一块内存
        fine = _fine(frame)
        moved = None if self.prev_fine is None else changed_share(fine, self.prev_fine)
        self.prev_fine = fine
        shown = "" if moved is None else f"{moved * 100:.2f}%"
        if moved is None or moved >= STILL_SHARE:
            self.still_since = tick
            return "在动", shown
        return self._settled(self.latest, fine, tick, now, files), shown

    def _no_new_frame(self, tick, now, files):
        """画面一点没变时 dxcam 不给新画面（2026-11-02 真截桌面时碰到）：当作没动，用上一张接着算。"""
        if not self.game_on or self.latest is None or self.still_since is None:
            return "没有新画面"
        return "没有新画面（当作没动）：" + self._settled(self.latest, self.prev_fine, tick, now, files)

    def _settled(self, frame, fine, tick, now, files):
        """画面没动：停够了、和上一张存的不一样就存。返回结果。"""
        if tick - self.still_since < STILL_SECONDS:
            return "还没停稳"
        if self.last_fine is not None and changed_share(fine, self.last_fine) < NEW_SHARE:
            self.same += 1
            return "和上一张存的一样"
        h, w = frame.shape[:2]
        self.saved += 1
        self.pages += 1
        files.append(self._save("pages", f"{self.saved:04d}_{now:%H%M%S}_{w}x{h}.jpg",
                                encode_jpeg(frame, None, FULL_QUALITY, subsampling=0)))
        self.last_fine = fine
        self.beep()
        self.log(f"[{now:%H:%M:%S}] 存好了第 {self.pages} 页")
        return "存了"

    def summary(self):
        return {**super().summary(), "存了几页": self.pages}


def icon_mask(frame, cfg, aspect=None):
    """右下角手上那把的大图标那块（config/weapons.json 的 icon_box），按基准尺寸取样，白色的像素为 True。
    和 weapons.icon_mask 一样挑白色，只是不用 OpenCV（取样代替缩放）：这里只看"换没换"，不认是哪一把。"""
    box = cfg["icon_box"]
    bw, bh = box[2], box[3]
    x0, y0, x1, y1 = Mapping(frame.shape, cfg["base_resolution"], cfg.get("anchor"), aspect).box(box)
    piece = frame[max(0, y0):y1, max(0, x0):x1, :3]
    if piece.shape[0] == 0 or piece.shape[1] == 0:
        return np.zeros((bh, bw), bool)
    ys = (np.arange(bh) * piece.shape[0] // bh)
    xs = (np.arange(bw) * piece.shape[1] // bw)
    piece = piece[ys][:, xs].astype(np.int16)
    lo, hi = piece.min(axis=2), piece.max(axis=2)
    mask = (lo >= cfg["white_min"]) & (hi - lo < cfg["white_spread"])
    for x, y, w, h in cfg.get("ignore_boxes", []):  # 配件格、弹药小图标：挖掉不看
        mx, my = max(0, x - box[0]), max(0, y - box[1])
        mask[my:my + h, mx:mx + w] = False
    return mask


def mask_change(a, b):
    """两个图标的白色像素有多少不一样（占两者合起来的几成，0–1）。"""
    union = np.count_nonzero(a | b)
    return np.count_nonzero(a ^ b) / union if union else 0.0


class WeaponRecorder(Recorder):
    """"靶场：武器"：右下角手上那把的图标换了（换枪、单拿 / 双持），停稳 WEAPON_SETTLE 秒后隔 WEAPON_GAP 秒存
    WEAPON_SAVES 张，存够了"嘀"一声，测试员换下一把。每张存两样：整张缩到 1920 宽（frames/，看全貌、读武器名）和
    右下角一块原尺寸无损的 PNG（crops/，文件名带它在原图上的位置；做参照图要无损，见 tools/weapon_templates.py）。
    换回已经存过的样子不再存。这么久（WEAPON_FALLBACK）没存过就存一张：图标那块对不上（没校准的画面比例）时也有截图。
    cfg：config/weapons.json；aspect：游戏的宽高比（None = 和截图一样，见 layout.py）。"""

    def __init__(self, *args, cfg, aspect=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg, self.aspect = cfg, aspect
        self.shape = None  # 现在手上那把的图标
        self.shape_since = None
        self.shape_saves = 0  # 这个样子存了几张
        self.done = []  # 存够了的样子
        self.last_save = None

    def _size_changed(self):
        super()._size_changed()
        self.shape = None

    def _keep(self, frame, tick, now, files):
        if not self.game_on:
            return super()._keep(frame, tick, now, files)
        mask = icon_mask(frame, self.cfg, self.aspect)
        pixels = int(np.count_nonzero(mask))
        shown = str(pixels)
        if pixels >= self.cfg["min_pixels"]:
            if self.shape is None or mask_change(mask, self.shape) > WEAPON_NEW:
                self.shape, self.shape_since = mask, tick
                done = any(mask_change(mask, old) <= WEAPON_NEW for old in self.done)
                self.shape_saves = WEAPON_SAVES if done else 0
                return ("换了一把（这个样子存过了）" if done else "换了一把，等它停稳"), shown
            if self.shape_saves >= WEAPON_SAVES:
                return "这把存好了", shown
            if tick - self.shape_since < WEAPON_SETTLE:
                return "等它停稳", shown
            if self.last_save is not None and tick - self.last_save < WEAPON_GAP:
                return "等一会儿再存", shown
            self._save_both(frame, tick, now, files)
            self.shape_saves += 1
            if self.shape_saves == WEAPON_SAVES:
                self.done.append(self.shape)
                self.beep()
                self.log(f"[{now:%H:%M:%S}] 存好了第 {len(self.done)} 把（听到\"嘀\"就换下一把）")
            return f"存了（这把第 {self.shape_saves} 张）", shown
        if self.last_save is None or tick - self.last_save >= WEAPON_FALLBACK:
            self._save_both(frame, tick, now, files)
            return "存了（隔一会儿存一张）", shown
        return "手上没有武器", shown

    def _save_both(self, frame, tick, now, files):
        h, w = frame.shape[:2]
        self.saved += 1
        self.last_save = tick
        files.append(self._save("frames", f"{self.saved:04d}_{now:%H%M%S}_{w}x{h}.jpg", encode_jpeg(frame, SMALL_WIDTH)))
        x0, y0, x1, y1 = Mapping(frame.shape, self.cfg["base_resolution"], "bottom", self.aspect).box(CROP_BOX)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        buf = io.BytesIO()
        Image.fromarray(np.ascontiguousarray(frame[y0:y1, x0:x1, 2::-1])).save(buf, "PNG")
        files.append(self._save("crops", f"{self.saved:04d}_{now:%H%M%S}_{w}x{h}_x{x0}_y{y0}.png", buf.getvalue()))

    def summary(self):
        return {**super().summary(), "存好了几把": len(self.done)}


RECORDERS = {"every": Recorder, "still": StillRecorder, "weapons": WeaponRecorder}  # config/tester.json 的 capture


def make_recorder(project, out_dir, camera, names, log, weapons_cfg=None, aspect=None, **options):
    """按测试项目（config/tester.json 里的一项）建截屏的对象。options：Recorder 的其他参数（thumbs、beep、测试用的假时钟等）。"""
    kind = project["capture"]
    options = {"interval": project.get("interval", INTERVAL), "minutes": project.get("max_minutes", MAX_MINUTES),
               "max_mb": project.get("max_mb", MAX_MB), **options}
    if kind == "every":
        options.setdefault("full_seconds", project.get("full_seconds"))
    else:
        options["full_seconds"] = None  # 数据页存的本来就是原尺寸；武器存了右下角原尺寸的一块
    if kind == "weapons":
        options.update(cfg=weapons_cfg, aspect=aspect)
    return RECORDERS[kind](out_dir, camera, names, log, **options)


class MonitorThumbs:
    """每个显示器存一张小缩略图（mss，Windows 自带的截屏方式，和 dxcam 不是一条路），返回存了多少字节。
    看得出游戏在哪个屏幕上、dxcam 卡住时 Windows 自己截不截得到；THUMB_WIDTH 宽，看不清字。"""

    def __init__(self, out_dir, log):
        import mss

        self.out_dir, self.log = Path(out_dir) / "monitors", log
        self.sct = mss.MSS()  # mss 10.2 起的写法（旧的 mss.mss() 已弃用）
        self.monitors = [dict(m) for m in self.sct.monitors[1:]]  # 第 0 个是所有屏幕拼起来的，不要
        self.failed = False

    def __call__(self, now):
        total = 0
        for index, mon in enumerate(self.monitors, 1):
            try:
                shot = self.sct.grab(mon)
                img = Image.frombytes("RGB", shot.size, shot.rgb)
            except Exception as e:
                if not self.failed:
                    self.log(f"[{now:%H:%M:%S}] 缩略图截不到（不影响主要的截图）：{e}")
                    self.failed = True
                continue
            img.thumbnail((THUMB_WIDTH, THUMB_WIDTH), Image.Resampling.BOX)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=80)
            self.out_dir.mkdir(exist_ok=True)
            (self.out_dir / f"{now:%H%M%S}_screen{index}.jpg").write_bytes(buf.getvalue())
            total += buf.tell()
        return total

    def close(self):
        self.sct.close()


def open_camera():
    """和 Evo 的记录程序一样建截屏对象（live_collect.collect：主显示器、BGR、numpy）。
    返回 (dxcam 模块或 None, 截屏对象或 None, 建不起来的原因或 None)。"""
    try:
        import dxcam
    except Exception as e:
        return None, None, f"dxcam 加载不了：{e}"
    try:
        return dxcam, dxcam.create(output_color="BGR", processor_backend="numpy"), None
    except Exception as e:
        return dxcam, None, f"截屏对象建不起来（Evo 也会在这里出错）：{e}"


# ---------- 打包 ----------

def make_zip(folder, zip_path):
    """把 folder 打成 zip_path（JPEG、PNG 本来就压缩过，不再压缩，只是装进去），核对条数后删掉 folder。"""
    folder, zip_path = Path(folder), Path(zip_path)
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    part = zip_path.with_name(zip_path.name + ".part")
    with zipfile.ZipFile(part, "w") as zf:
        for path in files:
            kind = zipfile.ZIP_STORED if path.suffix in (".jpg", ".png") else zipfile.ZIP_DEFLATED
            zf.write(path, path.relative_to(folder).as_posix(), compress_type=kind)
    with zipfile.ZipFile(part) as zf:
        if len(zf.namelist()) != len(files):
            raise OSError("压缩包里的文件数对不上")
    part.replace(zip_path)
    shutil.rmtree(folder)
    return zip_path


def _known_folder(guid):
    """Windows 的特殊文件夹（如桌面，可能被挪到 OneDrive 里），查不到返回 None。"""
    try:
        buf = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
        path = ctypes.c_wchar_p()
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        if shell32.SHGetKnownFolderPath(ctypes.byref(buf), 0, None, ctypes.byref(path)) == 0:
            found = Path(path.value)
            ctypes.WinDLL("ole32").CoTaskMemFree(path)
            return found if found.is_dir() else None
    except (OSError, AttributeError, ValueError):
        pass
    return None


def default_out_dir():
    """压缩包默认放在桌面；没有桌面就放在本程序旁边。"""
    desktop = _known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")  # FOLDERID_Desktop
    if desktop is not None:
        return desktop
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
