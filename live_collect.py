"""实战采集：打游戏时在后台运行，自动识别每一局的总结页并存进数据库。

每秒截一次屏（只截屏，不碰游戏），判断是不是下面几种画面（见 screens.py：先比外观，不到 1 毫秒；像了才用识别引擎
读关键字确认）。识别模型用到时才加载，闲置 2 分钟后释放（打游戏的大部分时间用不到）：
- 总结页（中间卡片上有"击杀/助攻/击倒"）：改成每 0.3 秒检查一次，读全了就存一局（见 SummaryWatcher）
- 经验值页：离开时识别最后一帧（数字有动画，最后一帧才是最终结果），补上这一局的 RP、段位，名次缺的话也补上。
  没有总结页的局（第 2 名、冠军）只靠经验值页单独记一局。怎么归到同一局见 post_match.py
- 开局画面：读本局的模式和地图（见 match_start.py），两项都读到就不再读
- 对局中：每 3 秒看一眼左下角自己的头像，认出用的是哪个传奇（见 legends.py 和 LegendWatcher，不用识别模型）
- 经验值页、开局画面、大厅（见 config/screens.json）：进入时存一张截图，离开前最后一帧再存一张（见 ScreenWatcher）。
  开局画面用来分隔每一局
- 其他画面：什么都不做（大部分时间都是这样）

数据存在数据文件夹（见 paths.py；用源码运行时就是项目文件夹）的 data/evo.db（用 python storage.py 查看），
截图存在 captures/live/<开始时间>/。
测试阶段截图都保留，用来做测试集；正式版只保留"待确认"的截图（原则 2）。

运行：python live_collect.py
停止：在终端按 Ctrl+C，或到达 --minutes 设定的时长后自动停止
"""

import argparse
import gc
import time
from datetime import datetime
from pathlib import Path

import cv2
import dxcam

import storage
from legends import LegendDetector
from match_start import read_match_start
from ocr import SMALL_CROP_PARAMS, LazyEngine, load_config
from paths import CAPTURES_DIR
from post_match import PostMatch
from process_info import memory_mb, set_low_priority
from screens import ScreenDetector
from summary_page import KEY_FIELDS, read_summary, show, summary_to_match
from xp_page import read_xp

LATE_FIELDS = ("placement", "squad_kills")  # 总结页上晚 1–2 秒才滑进来的项
MODEL_IDLE_SECONDS = 120  # 识别模型闲置这么久就释放
NO_SCREEN_WARNING_MINUTES = 45  # 这么久一个画面都没认出来，在日志里提醒（可能是游戏更新了界面，参照图过时了）


def same_match(a, b, min_fields=4):
    """两次读数是不是同一局：两边都读到的项全部相同（至少要有 min_fields 项可比）。
    用于在总结页和经验值页之间来回切换时，避免同一局被存好几次。"""
    if a is None or b is None:
        return False
    both = [k for k in a if a[k] is not None and b.get(k) is not None]
    return len(both) >= min_fields and all(a[k] == b[k] for k in both)


def save_png(path, frame):
    cv2.imencode(".png", frame)[1].tofile(path)


class SummaryWatcher:
    """总结页：名次、击杀/助攻/击倒、伤害都读到了，并且连续两次相同（名次会晚 1–2 秒滑进来，数字也可能有动画），
    就保存截图、把这一局写进数据库。还没读全就离开了、等太久、或者停止记录时，用读得最全的一次存下，标记"待确认"。
    在总结页和别的画面之间来回切换，同一局不重复存。"""

    def __init__(self, engine, cfg, conn, post, run_dir, log, timeout):
        self.engine, self.cfg, self.conn, self.post = engine, cfg, conn, post
        self.run_dir, self.log, self.timeout = run_dir, log, timeout
        self.saved = 0  # 这次记录存下了几局
        self.visit = None  # 这一次停在总结页的情况：开始时间、上一次的读数、读得最全的一次
        self.done = False  # 这一次总结页已经存过了
        self.last_saved = None  # 最近存进数据库的那一局的读数（切到别的页再切回来时，用来认出是同一局）

    @property
    def waiting(self):
        """正停在总结页、这一局还没存下。"""
        return self.visit is not None and not self.done

    def update(self, on_summary, frame, now):
        """每截一帧调用一次。on_summary：这一帧是不是总结页。"""
        if not on_summary:
            if self.waiting:
                self._record_best("总结页停留太短，名次等数据可能没读全，请核对")
            self.visit, self.done = None, False
            return
        if self.done:
            return
        if self.visit is None:
            self.visit = {"start": time.monotonic(), "last": None, "best": None, "core_stable": False}
        visit = self.visit
        results, problems = read_summary(self.engine, frame, self.cfg)
        values = {name: r["value"] for name, r in results.items()}
        filled = sum(v is not None for v in values.values())
        if visit["best"] is None or filled >= visit["best"][0]:
            visit["best"] = (filled, results, problems, frame.copy(), now)
        complete = all(values[k] is not None for k in KEY_FIELDS)
        # 除名次、小队总击杀以外的数字都读到了，且和上一次相同
        core = {k: v for k, v in values.items() if k not in LATE_FIELDS}
        if visit["last"] is not None and all(v is not None for v in core.values()) \
                and core == {k: v for k, v in visit["last"].items() if k not in LATE_FIELDS}:
            visit["core_stable"] = True

        if complete and values == visit["last"]:  # 关键项都读到了，且连续两次相同
            self._record(results, problems, frame, now)
            self.done = True
        elif time.monotonic() - visit["start"] > self.timeout:
            self._record_best(f"在总结页等了 {self.timeout:g} 秒，数字仍没读全或不稳定，请核对")
            self.done = True
        visit["last"] = values

    def finish(self):
        """停止记录时：如果正停在还没存下的总结页，先存下。"""
        if self.waiting:
            self._record_best("总结页停留太短，名次等数据可能没读全，请核对")

    def _record(self, results, problems, frame, when, note=None):
        """把一次总结页的读数存进数据库。如果和上一局是同一局（来回切页），就不重复存。"""
        values = {name: r["value"] for name, r in results.items()}
        if same_match(values, self.last_saved):
            self.log(f"[{when:%H:%M:%S}] 回到了同一局的总结页，不重复保存")
            return
        if note:
            problems = problems + [note]
        image_path = self.run_dir / f"{self.saved + 1:02d}_{when:%H%M%S}.png"
        save_png(image_path, frame)
        match = summary_to_match(results, problems, f"{when:%Y-%m-%d %H:%M:%S}", image_path)
        match_id = storage.insert_match(self.conn, match)
        self.saved += 1
        self.last_saved = values
        self.log(f"[{when:%H:%M:%S}] 第 {self.saved} 局（编号 {match_id}）：第 {show(values['placement'])} 名，"
                 f"击杀/助攻/击倒 {show(values['kda'])}，伤害 {show(values['damage'])}")
        for p in problems:
            self.log(f"           ⚠ {p}")
        self.post.on_summary_saved(match_id, values, when)

    def _record_best(self, note):
        """数字没等到读全：用这次停留里读得最全的一次存下（会标记待确认）。
        如果只是名次还没滑进来、其他数字已经连续两次一致，就只标"名次没读到"（经验值页可以补上），不加 note。"""
        _, results, problems, frame, when = self.visit["best"]
        self._record(results, problems, frame, when, None if self.visit["core_stable"] else note)


class ScreenWatcher:
    """经验值页、开局画面、大厅（见 config/screens.json）：进入时存一张截图，离开前最后一帧再存一张。
    离开经验值页时识别最后一帧（数字有动画，最后一帧才是最终结果）；进入开局画面表示上一局彻底结束；
    在开局画面读本局的模式和地图，读到就不再读。"""

    def __init__(self, engine, screens, xp_cfg, start_cfg, post, run_dir, log):
        self.engine, self.screens, self.xp_cfg, self.start_cfg = engine, screens, xp_cfg, start_cfg
        self.post, self.run_dir, self.log = post, run_dir, log
        self.current = None  # 现在停在哪个画面（None = 都不是）
        self.last = None  # 这个画面最近的一帧和时间，离开时存下来
        self.frames = 0  # 在这个画面已经看到了几帧
        self.enter_path = None  # 进入这个画面时存的截图

    def update(self, screen, frame, now):
        """每截一帧调用一次。screen：这一帧是哪个画面（如 "xp"），都不是则为 None。"""
        if screen != self.current:
            self.leave()
            if screen == "match_start":
                self.post.finish()
            if screen is not None:
                self.enter_path = self.run_dir / f"{screen}_{now:%H%M%S}_enter.png"
                save_png(self.enter_path, frame)
                self.log(f"[{now:%H:%M:%S}] 进入{self._label(screen)}，已存截图")
            self.current, self.frames = screen, 0
        if screen is not None:
            self.last = (frame.copy(), now)
            self.frames += 1
        if screen == "match_start" and not self.post.info_complete():
            self.post.on_match_start(read_match_start(self.engine, frame, self.start_cfg), now)

    def leave(self):
        """离开当前画面：存下最后一帧（只看到一帧时，进入时已经存过了）；如果是经验值页，识别最后一帧。"""
        if self.current is None:
            return
        last_frame, last_time = self.last
        path = self.enter_path
        if self.frames > 1:
            path = self.run_dir / f"{self.current}_{last_time:%H%M%S}_leave.png"
            save_png(path, last_frame)
            self.log(f"[{last_time:%H:%M:%S}] 离开{self._label(self.current)}，已存最后一帧")
        if self.current == "xp":
            reading, problems = read_xp(self.engine, last_frame, self.xp_cfg)
            self.post.on_xp(reading, problems, path, last_time)

    def _label(self, screen):
        return self.screens["screens"][screen]["label"]


class LegendWatcher:
    """对局中认出自己用的传奇：从开局画面之后，到总结页或经验值页之前，每隔几秒看一眼左下角自己的头像，
    同一个传奇认出 2 次就记下（交给 PostMatch，存这一局时一起写进去），这一局就不再看了。
    - 开局画面：新的一局开始，之前认出的不算数（比如在靶场里认出的）
    - 阵亡后观战：左下角是被观战的人，整块还往上移了约 60，在自己头像的位置上认不出来，所以不会认成队友
    - 刚开始记录时也看（可能正在对局中）"""

    VOTES = 2  # 同一个传奇认出这么多次才算数

    def __init__(self, detector, post, every=3.0):
        self.detector, self.post, self.every = detector, post, every
        self.checks = 0  # 一共看了几次（统计用）
        self._restart()

    def _restart(self):
        self.active, self.votes, self.next_check = True, {}, 0.0

    def update(self, screen, frame, now, tick):
        """每截一帧调用一次。screen：这一帧是哪个画面（None = 都不是，比如正在对局中）；tick：time.monotonic()。"""
        if screen == "match_start":
            self._restart()
            self.post.on_legend(None, now)
            return
        if screen in ("summary", "xp"):  # 这一局打完了
            self.active = False
            return
        if not self.active or screen is not None or tick < self.next_check:
            return
        self.next_check = tick + self.every
        self.checks += 1
        key = self.detector.identify(frame)
        if key is None:
            return
        self.votes[key] = self.votes.get(key, 0) + 1
        if self.votes[key] >= self.VOTES:
            self.active = False
            self.post.on_legend(self.detector.name(key), now)


def collect(db_path=storage.DB_PATH, interval=1.0, fast=0.3, summary_timeout=10.0,
            stop=None, deadline=None, log=print, status=None):
    """采集主循环：一直截屏、识别、记录，直到下面任何一种情况发生，然后安全收尾（没存完的局存下、释放截屏和识别模型）：
    - stop（threading.Event）被设置：托盘程序在游戏关闭、暂停或退出时用它喊停
    - 到达 deadline（time.monotonic() 的时刻）
    - 在终端按 Ctrl+C
    log：输出一行文字的函数（终端里是 print，托盘程序里写进日志文件）
    status：字典，记下的局数会实时写进 status["saved"]（托盘图标显示用）
    返回这次一共记下了几局。"""
    set_low_priority()
    cfg = load_config("summary.json")
    screens = load_config("screens.json")
    run_dir = CAPTURES_DIR / "live" / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    conn = storage.connect(db_path)
    post = PostMatch(conn, log=log)

    camera = dxcam.create(output_color="BGR", processor_backend="numpy")
    # clock 用 lambda 包一层：测试时会把本文件的 time 换成假时钟
    engine = LazyEngine(SMALL_CROP_PARAMS, MODEL_IDLE_SECONDS, clock=lambda: time.monotonic(), log=log)
    detector = ScreenDetector(engine, screens)
    summary = SummaryWatcher(engine, cfg, conn, post, run_dir, log, summary_timeout)
    other = ScreenWatcher(engine, screens, load_config("xp.json"), load_config("match_start.json"), post, run_dir, log)
    legend_detector = LegendDetector()
    legend = LegendWatcher(legend_detector, post)
    log(f"屏幕: {camera.width}x{camera.height}")
    log(f"数据库: {Path(db_path).resolve()}")
    log(f"截图保存到: {run_dir.resolve()}")
    if detector.missing:
        log(f"⚠ 这些画面没有参照图，每次都要跑识别：{'、'.join(detector.missing)}（见 screens.py）")
    if legend_detector.missing:
        log(f"⚠ 这些传奇没有参照图，认不出来：{'、'.join(legend_detector.name(k) for k in legend_detector.missing)}"
            "（见 legends.py）")
    log("开始记录，到总结页时会自动保存。")

    def keep_going():
        return not (stop is not None and stop.is_set()) and (deadline is None or time.monotonic() < deadline)

    def total():
        return summary.saved + post.inserted

    frames, last_seen, warned = 0, time.monotonic(), False  # 截了几帧；最近一次认出画面的时刻
    try:
        while keep_going():
            tick = time.monotonic()
            on_summary = False
            try:
                frame = camera.grab(new_frame_only=False)
            except Exception as e:  # 切换分辨率、切出游戏等情况，跳过这一次
                log(f"[{datetime.now():%H:%M:%S}] 截屏出错，跳过：{e}")
                frame = None

            if frame is not None:
                frames += 1
                now = datetime.now()
                screen = detector.detect(frame)
                on_summary = screen == "summary"
                summary.update(on_summary, frame, now)
                other.update(None if on_summary else screen, frame, now)
                legend.update(screen, frame, now, tick)
                if screen is not None:
                    last_seen, warned = tick, False
                elif not warned and tick - last_seen > NO_SCREEN_WARNING_MINUTES * 60:
                    log(f"⚠ 已经 {NO_SCREEN_WARNING_MINUTES} 分钟没认出任何画面（大厅、开局、总结页、经验值页）。"
                        "如果这段时间打完过对局，可能是游戏更新了界面：用 python screens.py 截图.png 检查，必要时重新生成参照图")
                    warned = True
            engine.release_if_idle()

            if status is not None:
                status["saved"] = total()
            wait = fast if (on_summary and summary.waiting) else interval  # 在总结页等数字读全时，检查得快一些
            time.sleep(max(0, wait - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        log("已手动停止。")
    finally:
        summary.finish()
        other.leave()
        post.finish()
        camera.release()
        conn.close()
        saved = total()
        log(f"本次记录：截屏 {frames} 次，其中外观像的 {detector.looked_alike} 次；识别 {engine.calls} 次，"
            f"加载识别模型 {engine.loads} 次；认传奇 {legend.checks} 次；内存峰值 {memory_mb()[1]:.0f} MB")
        del engine, detector, camera, summary, other, legend, legend_detector  # 释放识别模型和截屏，交还内存
        gc.collect()
    if status is not None:
        status["saved"] = saved
    return saved


def main():
    parser = argparse.ArgumentParser(description="打游戏时自动识别每局的总结页并存进数据库")
    parser.add_argument("--interval", type=float, default=1, help="每隔几秒检查一次（默认 1）")
    parser.add_argument("--fast", type=float, default=0.3, help="在总结页、数字还没存下时，每隔几秒检查一次（默认 0.3）")
    parser.add_argument("--summary-timeout", type=float, default=10,
                        help="在总结页最多等几秒让数字读全，超时就先存下并标记待确认（默认 10）")
    parser.add_argument("--minutes", type=float, default=240, help="最多运行几分钟（默认 240）")
    parser.add_argument("--db", type=Path, default=storage.DB_PATH, help="数据库文件（默认 data/evo.db）")
    args = parser.parse_args()

    print("按 Ctrl+C 停止。\n")
    saved = collect(db_path=args.db, interval=args.interval, fast=args.fast, summary_timeout=args.summary_timeout,
                    deadline=time.monotonic() + args.minutes * 60)
    mem_now, mem_peak = memory_mb()
    print(f"\n本次共保存 {saved} 局。内存: 现在 {mem_now:.0f} MB，峰值 {mem_peak:.0f} MB")
    print("查看数据库里的记录: python storage.py")


if __name__ == "__main__":
    main()
