"""实战采集（模型的主循环）：打游戏时在后台运行，按画面识别、记录每一局。流程图见 docs/模型流程.drawio，
规则见 docs/对局全流程图.md。

只截屏，不碰游戏（唯一的例外：读 Apex 自己的视频设置文件，知道游戏画面是 16:9、16:10 还是 4:3，见 game_aspect.py）。
每截一张，先判断是哪个画面（见 screens.py：先比外观，几毫秒；像了才用识别引擎读关键字确认），
再交给对应的部分。识别引擎（OCR）用到时才加载，闲置 2 分钟后释放；对局中交火时一律不用引擎（原则 3）：
- 大厅：读玩家名、段位和段内分数，连续两次一样就记下，并用它校准前后两次大厅之间各局的 RP（见 lobby.py、calibration.py）。
  回到大厅也算上一局结束
- 开局画面：上一局收尾；读本局的模式和地图（见 match_start.py），两项都读到就不再读
- 选角色等别的画面、对局中：都不是上面这些画面。左下角认得出自己的传奇 = 自己的界面：认自己和队友的传奇、
  记下存活 / 倒地 / 成盒，右上角那一块存在内存里（见 in_match.py、hud.py）。对局中截图加快到每 0.3 秒一张
- 淘汰画面（"排名第 X"）、冠军横幅（"捍卫者"）、总结页、经验值页、右下角的"返回大厅"按钮（小队已淘汰，比如全队被淘汰后观战对手；
  自己先成盒、队友还活着时没有这个按钮）：这一局结束了，先读对局中存下的右上角
  （击杀、助攻、老板分、伤害、RP、剩余小队数）和队友名字，交给 PostMatch
  - 淘汰画面：读名次，读到就不再读（见 eliminated.py）；冠军横幅：记第 1 名
  - 总结页：改成每 0.3 秒检查一次，读全了就存一局（见 SummaryWatcher），同一帧再读左右两张卡片上的队友
    （见 teammates.py）、中间卡片上自己的名字、顶部是不是"捍卫者"
  - 经验值页：离开时识别最后一帧（数字有动画，最后一帧才是最终结果），补上 RP、段位
- 各来源读到的数都记进 readings 表，每一项最后用哪个来源的数见 post_match.py（总结页、经验值页读得清楚时以它们为准，
  没有时用对局中的）
- 提示音（见 SoundCues、sounds.py；设置页能关）：小队打完了响一声提醒去总结页，总结页读完了响一声，进经验值页响一声

数据存在数据文件夹（见 paths.py；用源码运行时就是项目文件夹）的 data/evo.db（用 python code/storage.py 查看），
截图存在 captures/live/<开始时间>/。截图留不留见 screenshots.py：
- 默认识别完就删：只存总结页、经验值页（离开前最后一帧），一局结束时只留总结页截图（转成 JPG）和"待确认"的局要用的
- 保留全部截图（攒测试集用；用源码运行时永远保留全部）：经验值页、开局画面、大厅、淘汰画面、冠军横幅也存，
  进入时一张、离开前最后一帧一张；每局结束时还存最后一张自己的界面（hud_时间_last.png，校准对局中的区域用）
- 设置页的"保留全部截图"开关：每局结束时看一眼，改了从下一局开始生效（见 collect 里的 round_end）
每 5 分钟和记录结束时，日志里写一行 CPU、内存占用（方便实测对局中每 0.3 秒一张对 CPU 的影响）。

运行：python code/live_collect.py
停止：在终端按 Ctrl+C，或到达 --minutes 设定的时长后自动停止
"""

import argparse
import gc
import time
from datetime import datetime
from pathlib import Path
from time import perf_counter, process_time  # 单独导入：测试会把本文件的 time 换成假时钟，这两个仍是真的

import cv2
import dxcam

import game_lang
import screenshots
import sounds
import storage
from eliminated import read_eliminated
from game_aspect import AspectWatcher
from hud import Hud
from in_match import InMatchWatcher
from lobby import LobbyReader, LobbyWatcher
from match_start import read_match_start
from ocr import SMALL_CROP_PARAMS, LazyEngine, load_config
from paths import CAPTURES_DIR
from player_name import PlayerNameReader
from post_match import SOURCE_CHAMPION, SOURCE_SUMMARY, PostMatch
from process_info import memory_mb, set_low_priority
from screens import GameScreens
from summary_page import KEY_FIELDS, is_champion, read_summary, show, summary_readings, summary_to_match
from teammates import describe, read_teammates
from xp_page import read_xp

LATE_FIELDS = ("placement", "squad_kills")  # 总结页上晚 1–2 秒才滑进来的项
MODEL_IDLE_SECONDS = 120  # 识别模型闲置这么久就释放
NO_SCREEN_WARNING_MINUTES = 45  # 这么久一个画面都没认出来，在日志里提醒（可能是游戏更新了界面，参照图过时了）
USAGE_EVERY_SECONDS = 300  # 每隔这么久在日志里写一行 CPU、内存占用


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
    就保存截图、把这一局写进数据库（冠军的总结页没有名次：其他数字连续两次相同、顶部是"捍卫者"就算读全）。还没读全就离开了、等太久、或者停止记录时，用读得最全的一次存下，标记"待确认"
    （缺的项之后能用别的来源补上就取消，见 post_match.settle）。在总结页和别的画面之间来回切换，同一局不重复存。"""

    def __init__(self, engine, cfg, conn, post, run_dir, log, timeout, names=None, on_read=None):
        """on_read(时间)：关键项读全、连续两次相同时调用（总结页读完了的提示音）；没读全就离开、等太久都不调用。"""
        self.engine, self.cfg, self.conn, self.post = engine, cfg, conn, post
        self.run_dir, self.log, self.timeout, self.names = run_dir, log, timeout, names
        self.on_read = on_read
        self.saved = 0  # 这次记录存下了几局
        self.name_reads = 0  # 读了几次中间卡片上的名字（统计用）
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

        # 冠军的总结页顶部是"捍卫者"、没有"第 X 名"，名次永远读不到：其他数字稳定了就算读完（第 1 名由冠军横幅补上，
        # 和以前离开时才存一样，只是存得早，"总结页读完了"的提示音也能响）
        champion = (not complete and visit["core_stable"] and values["placement"] is None and "banner" in self.cfg
                    and is_champion(self.engine, frame, self.cfg))
        if (complete and values == visit["last"]) or champion:  # 关键项都读到了，且连续两次相同
            self._record(results, problems, frame, now)
            self.done = True
            if self.on_read is not None:
                self.on_read(now)
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
        readings, scores = summary_readings(results)
        storage.save_readings(self.conn, match_id, SOURCE_SUMMARY, readings, f"{when:%Y-%m-%d %H:%M:%S}", scores)
        self.saved += 1
        self.last_saved = values
        self.log(f"[{when:%H:%M:%S}] 第 {self.saved} 局（编号 {match_id}）：第 {show(values['placement'])} 名，"
                 f"击杀/助攻/击倒 {show(values['kda'])}，伤害 {show(values['damage'])}")
        for p in problems:
            self.log(f"           ⚠ {p}")
        mates = read_teammates(self.engine, frame, self.cfg)  # 读不准的项留空，不因为队友标待确认
        storage.save_teammates(self.conn, match_id, mates)
        for mate in mates:
            self.log(f"           队友 {describe(mate)}")
        name = None
        if self.names is not None:
            self.name_reads += 1
            found = self.names.read(frame, "summary")
            name = found[0] if found else None
        self.post.on_summary_saved(match_id, values, when, name)
        if "banner" in self.cfg and is_champion(self.engine, frame, self.cfg):
            self.post.on_champion(when, SOURCE_SUMMARY)

    def _record_best(self, note):
        """数字没等到读全：用这次停留里读得最全的一次存下（会标记待确认）。
        如果只是名次还没滑进来、其他数字已经连续两次一致，就只标"名次没读到"（经验值页、淘汰画面可以补上），不加 note。"""
        _, results, problems, frame, when = self.visit["best"]
        self._record(results, problems, frame, when, None if self.visit["core_stable"] else note)


class SoundCues:
    """提示音（2026-10-21 用户决定，声音和开关见 sounds.py）：每一局每一声最多响一次，下一局开局、回到大厅时重新算。
    - 对局结束：进入淘汰画面、冠军横幅、右下角"返回大厅"按钮（小队打完了）时，提醒去总结页。
      这一局已经到过总结页或经验值页就不响（已经在那里了，比如从总结页切回"实况"又看到"返回大厅"）；
      自己先成盒、队友还活着时没有这几个画面，不响；提前回大厅、成盒后直接进了下一局也不响
    - 总结页读完了：SummaryWatcher 读全、存下这一局时（summary_read）；停留太短没读全、等太久都不响
    - 经验值页：进入时（数字有动画，离开时才读，没有"读完了"的那一刻）"""

    MATCH_END = ("eliminated", "champion", "squad_out")

    def __init__(self, log, play=None):
        self.log = log
        self.play = play or sounds.play
        self.current = None  # 上一帧是哪个画面（只在进入画面时判断）
        self.played = set()  # 这一局已经响过的
        self.on_pages = False  # 这一局已经到过总结页或经验值页

    def update(self, screen, now):
        """每截一帧调用一次。screen：这一帧是哪个画面（总结页也算），都不是则为 None。"""
        if screen == self.current:
            return
        self.current = screen
        if screen in ("match_start", "lobby"):
            self.played.clear()
            self.on_pages = False
        elif screen in self.MATCH_END and not self.on_pages:
            self._cue("match_end", now)
        elif screen in ("summary", "xp"):
            self.on_pages = True
            if screen == "xp":
                self._cue("xp", now)

    def summary_read(self, now):
        self.on_pages = True
        self._cue("summary", now)

    def _cue(self, name, now):
        if name in self.played:
            return
        self.played.add(name)  # 关着时也算响过：这一局中途打开开关，不补响
        if self.play(name):
            self.log(f"[{now:%H:%M:%S}] 提示音：{sounds.SOUNDS[name]}")


class ScreenWatcher:
    """经验值页、开局画面、大厅、淘汰画面、冠军横幅（见 config/screens.json）。
    离开经验值页时识别最后一帧（数字有动画，最后一帧才是最终结果）；进入开局画面或大厅表示上一局彻底结束；
    在开局画面读本局的模式和地图，读到就不再读；在淘汰画面读名次，这一次停留里读到就不再读，交给 PostMatch 归到这一局；
    进入冠军横幅告诉 PostMatch 记第 1 名。
    截图：保留全部时，进入时存一张、离开前最后一帧再存一张；否则只存经验值页离开前的最后一帧
    （没有总结页的局要拿它当识别依据），大厅、开局画面、淘汰画面、冠军横幅不存。"""

    def __init__(self, engine, screens, xp_cfg, start_cfg, elim_cfg, post, run_dir, log, keep_all=True):
        self.engine, self.screens, self.xp_cfg, self.start_cfg = engine, screens, xp_cfg, start_cfg
        self.elim_cfg = elim_cfg
        self.post, self.run_dir, self.log, self.keep_all = post, run_dir, log, keep_all  # 每局结束时可能改（round_end）
        self.current = None  # 现在停在哪个画面（None = 都不是）
        self.last = None  # 这个画面最近的一帧和时间，离开时存下来
        self.frames = 0  # 在这个画面已经看到了几帧
        self.enter_path = None  # 进入这个画面时存的截图（没存为 None）
        self.placement = None  # 这一次停在淘汰画面读到的名次

    def update(self, screen, frame, now):
        """每截一帧调用一次。screen：这一帧是哪个画面（如 "xp"），都不是则为 None。"""
        if screen != self.current:
            self.leave()
            if screen in ("match_start", "lobby"):
                self.post.finish()
            if screen == "champion":
                self.post.on_champion(now, SOURCE_CHAMPION)
            self.enter_path = None
            if screen is not None and self.keep_all:
                self.enter_path = self.run_dir / f"{screen}_{now:%H%M%S}_enter.png"
                save_png(self.enter_path, frame)
                self.log(f"[{now:%H:%M:%S}] 进入{self._label(screen)}，已存截图")
            elif screen is not None:
                self.log(f"[{now:%H:%M:%S}] 进入{self._label(screen)}")
            self.current, self.frames, self.placement = screen, 0, None
        if screen is not None:
            self.last = (frame.copy(), now)
            self.frames += 1
        if screen == "match_start" and not self.post.info_complete():
            self.post.on_match_start(read_match_start(self.engine, frame, self.start_cfg), now)
        if screen == "eliminated" and self.placement is None:
            self._read_placement(frame, now)

    def leave(self):
        """离开当前画面：存下最后一帧（保留全部时，只看到一帧的话进入时已经存过了；否则只存经验值页）；
        如果是经验值页，识别最后一帧。"""
        if self.current is None:
            return
        last_frame, last_time = self.last
        path = self.enter_path
        if (self.frames > 1) if self.keep_all else (self.current == "xp"):
            path = self.run_dir / f"{self.current}_{last_time:%H%M%S}_leave.png"
            save_png(path, last_frame)
            self.log(f"[{last_time:%H:%M:%S}] 离开{self._label(self.current)}，已存最后一帧")
        if self.current == "xp":
            reading, problems = read_xp(self.engine, last_frame, self.xp_cfg)
            self.post.on_xp(reading, problems, path, last_time)

    def _read_placement(self, frame, now):
        """读淘汰画面上的名次。没读出来时只在第一帧提示一次，之后每帧再试。"""
        reading = read_eliminated(self.engine, frame, self.elim_cfg)
        self.placement = reading["placement"]
        if self.placement is not None:
            self.log(f"[{now:%H:%M:%S}] 淘汰画面：排名第 {self.placement}")
            self.post.on_eliminated(self.placement, reading["score"], now)
        elif self.frames == 1:
            self.log(f"[{now:%H:%M:%S}] 淘汰画面：没读出名次（原文：{reading['raw'] or '无'}），下一帧再读")

    def _label(self, screen):
        return self.screens["screens"][screen]["label"]


class Usage:
    """CPU、内存占用：每 USAGE_EVERY_SECONDS 秒在日志里写一行，记录结束时写整段的平均。"""

    def __init__(self, log):
        self.log = log
        self.start = self.mark = (perf_counter(), process_time())

    def tick(self, frames, in_match):
        wall, cpu = perf_counter(), process_time()
        if wall - self.mark[0] >= USAGE_EVERY_SECONDS:
            share = (cpu - self.mark[1]) / (wall - self.mark[0]) * 100
            self.log(f"[{datetime.now():%H:%M:%S}] 占用：最近 {(wall - self.mark[0]) / 60:.0f} 分钟 CPU 平均 {share:.0f}% 个核，"
                     f"内存 {memory_mb()[0]:.0f} MB（{'对局中，每 0.3 秒一张' if in_match else '每 1 秒一张'}；共截屏 {frames} 次）")
            self.mark = (wall, cpu)

    def total(self):
        wall, cpu = perf_counter() - self.start[0], process_time() - self.start[1]
        return f"CPU 平均 {cpu / wall * 100:.0f}% 个核" if wall > 0 else "CPU -"


def collect(db_path=storage.DB_PATH, interval=1.0, fast=0.3, summary_timeout=10.0, match_interval=None,
            stop=None, deadline=None, log=print, status=None, keep_all=None, game_aspect=None):
    """采集主循环：一直截屏、识别、记录，直到下面任何一种情况发生，然后安全收尾（没存完的局存下、释放截屏和识别模型）：
    - stop（threading.Event）被设置：托盘程序在游戏关闭、暂停或退出时用它喊停
    - 到达 deadline（time.monotonic() 的时刻）
    - 在终端按 Ctrl+C
    interval：平时每隔几秒截一张；fast：停在总结页、数字还没读全时；match_interval：对局中（默认看 config/hud.json，0.3 秒）
    log：输出一行文字的函数（终端里是 print，托盘程序里写进日志文件）
    status：字典，记下的局数会实时写进 status["saved"]（托盘图标显示用）
    keep_all：保留全部截图。None = 按设置页的开关（screenshots.keep_all()），每局结束时再看一眼，
    改了从下一局开始生效；True / False = 整次记录都这样（测试时直接指定）
    game_aspect：游戏画面比例。None = 按设置页（默认自动检测，读 Apex 的视频设置文件，见 game_aspect.py），
    每隔几秒看一眼；"16:10" 等 = 整次记录都这样（测试时直接指定）
    返回这次一共记下了几局。"""
    set_low_priority()
    usage = Usage(log)
    run_dir = CAPTURES_DIR / "live" / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    conn = storage.connect(db_path)

    def keep_now():
        return screenshots.keep_all() if keep_all is None else keep_all

    keep = keep_now()
    tidied = {"deleted": 0, "converted": 0, "freed": 0}  # 这次记录识别完删掉的截图、转成 JPG 的总结页截图
    protect = set()  # 中途关掉"保留全部截图"时，关之前存下的截图：不删（以前存下的不自动删）

    def round_end():
        """一局彻底结束（下一局开局、回到大厅，或停止记录）：先看一眼"保留全部截图"开关（设置页改了，从下一局开始生效），
        再删掉这次记录里不需要的截图（只留"待确认"的局要用的）。开着时什么都不删。"""
        nonlocal keep
        if keep_now() != keep:
            keep = other.keep_all = not keep
            if keep:
                log("           设置页打开了\"保留全部截图\"：从下一局开始全部保留，刚结束的这一局也不删")
            else:
                protect.update(p.resolve() for p in run_dir.glob("*.png"))
                log(f"           设置页关掉了\"保留全部截图\"：从下一局开始识别完就删，之前存下的 {len(protect)} 张不删")
        if keep:
            return
        done = screenshots.tidy(conn, run_dir, protect)
        if done.deleted or done.converted:
            tidied["deleted"] += done.deleted
            tidied["converted"] += done.converted
            tidied["freed"] += done.freed
            log(f"           识别完删掉 {done.deleted} 张截图，总结页截图 {done.converted} 张转成 JPG"
                f"（腾出 {done.freed / 2 ** 20:.0f} MB）")

    name_cfg = load_config("player_name.json")
    post = PostMatch(conn, log=log, name_slip=name_cfg["slip_similarity"], on_round_end=round_end)

    camera = dxcam.create(output_color="BGR", processor_backend="numpy")
    # clock 用 lambda 包一层：测试时会把本文件的 time 换成假时钟
    engine = LazyEngine(SMALL_CROP_PARAMS, MODEL_IDLE_SECONDS, clock=lambda: time.monotonic(), log=log)
    detector = GameScreens(engine, log=lambda message: log(f"[{datetime.now():%H:%M:%S}] {message}"))
    lang = detector.lang  # 各部分现在用哪种游戏语言的规则（见 game_lang.py）
    hud = Hud(engine, lang=lang)
    match_interval = hud.cfg["match_interval"] if match_interval is None else match_interval
    cues = SoundCues(log)
    summary = SummaryWatcher(engine, load_config("summary.json", lang), conn, post, run_dir, log, summary_timeout,
                             PlayerNameReader(engine, name_cfg), on_read=cues.summary_read)
    other = ScreenWatcher(engine, detector.cfg, load_config("xp.json", lang), load_config("match_start.json", lang),
                          load_config("eliminated.json", lang), post, run_dir, log, keep_all=keep)
    in_match = InMatchWatcher(hud, post, log, name_cfg, save=lambda frame, name: save_png(run_dir / name, frame),
                              keep_all=lambda: other.keep_all)
    lobby = LobbyWatcher(LobbyReader(engine, load_config("lobby.json", lang)), post, conn, log)
    aspect = AspectWatcher(log, clock=lambda: time.monotonic(), fixed=game_aspect)

    def use_language(lang):
        """认定了游戏语言（或认出画面的语言变了，见 screens.GameScreens）：各部分换成这种语言的识别规则。"""
        names = load_config("player_name.json", lang)
        summary.cfg, summary.names.cfg = load_config("summary.json", lang), names
        other.screens = detector.cfg
        other.xp_cfg, other.start_cfg, other.elim_cfg = (load_config(n, lang) for n in
                                                         ("xp.json", "match_start.json", "eliminated.json"))
        hud.use_language(lang)
        in_match.name_place = names["places"]["hud"]
        lobby.reader.cfg = load_config("lobby.json", lang)

    log(f"屏幕: {camera.width}x{camera.height}")
    if len(detector.detectors) > 1:
        log(f"认得出的游戏语言：{'、'.join(game_lang.label(x) for x in detector.detectors)}（认出第一个画面时定下来）")
    log(f"数据库: {Path(db_path).resolve()}")
    log(f"截图保存到: {run_dir.resolve()}（{'保留全部' if keep else '识别完就删，只留待确认的局要用的'}；"
        "设置页改了开关，从下一局开始生效）")
    log(f"截图频率：对局中每 {match_interval:g} 秒一张，其他时候每 {interval:g} 秒一张")
    if detector.missing:
        log(f"⚠ 这些画面没有参照图，每次都要跑识别：{'、'.join(detector.missing)}（见 screens.py）")
    if hud.legends.missing:
        log(f"⚠ 这些传奇没有参照图，认不出来：{'、'.join(hud.legends.name(k) for k in hud.legends.missing)}"
            "（见 legends.py）")
    if hud.missing:
        log(f"⚠ 对局中缺参照图：{'、'.join(hud.missing)}（认不出观战、读不出伤害，见 hud.py）")
    log("开始记录。")

    def keep_going():
        return not (stop is not None and stop.is_set()) and (deadline is None or time.monotonic() < deadline)

    def total():
        return summary.saved + post.inserted

    frames, last_seen, warned = 0, time.monotonic(), False  # 截了几帧；最近一次认出画面的时刻
    next_lang_check = time.monotonic()  # 下一次看设置页"游戏语言"的时刻
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
                aspect.check(frame)  # 游戏画面比例（16:10、4:3 时界面摆法不一样，见 layout.py）
                if tick >= next_lang_check:  # 设置页的"游戏语言"：每隔几秒看一眼，改了几秒内生效
                    next_lang_check = tick + game_lang.CHECK_SECONDS
                    detector.use_choice(game_lang.choice())
                screen = detector.detect(frame)
                if detector.lang != lang:
                    lang = detector.lang
                    use_language(lang)
                on_summary = screen == "summary"
                cues.update(screen, now)  # 提示音：小队打完了、进经验值页（总结页读完了由 summary 告诉它）
                in_match.update(screen, frame, now)  # 先交对局中的数据：结算画面上要用
                summary.update(on_summary, frame, now)
                other.update(None if on_summary else screen, frame, now)
                lobby.update(screen, frame, now)
                if screen is not None:
                    last_seen, warned = tick, False
                elif not warned and tick - last_seen > NO_SCREEN_WARNING_MINUTES * 60 and not in_match.active:
                    log(f"⚠ 已经 {NO_SCREEN_WARNING_MINUTES} 分钟没认出任何画面（大厅、开局、淘汰画面、冠军横幅、总结页、"
                        "经验值页）。如果这段时间打完过对局，可能是游戏更新了界面：用 python code/screens.py 截图.png 检查，"
                        "必要时重新生成参照图")
                    warned = True
            engine.release_if_idle()
            usage.tick(frames, in_match.active)

            if status is not None:
                status["saved"] = total()
            if on_summary and summary.waiting:
                wait = fast  # 在总结页等数字读全时，检查得快一些
            elif in_match.active:
                wait = match_interval
            else:
                wait = interval
            time.sleep(max(0, wait - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        log("已手动停止。")
    finally:
        now = datetime.now()
        in_match.stop(now)
        summary.finish()
        other.leave()
        post.finish()
        aspect.reset()
        camera.release()
        conn.close()
        left = sum(1 for p in run_dir.iterdir() if p.suffix.lower() in (".png", ".jpg"))
        if not any(run_dir.iterdir()):
            run_dir.rmdir()  # 一张截图都没留下：文件夹也删掉
        saved = total()
        log(f"本次记录：截屏 {frames} 次，其中外观像的 {detector.looked_alike} 次；识别 {engine.calls} 次，"
            f"加载识别模型 {engine.loads} 次；认传奇 {in_match.checks} 次，读右上角 {in_match.top_reads} 块；"
            f"读玩家名 {summary.name_reads + in_match.name_reads} 次（对局中 {in_match.name_reads} 次），"
            f"玩家名 {post.player_name or '没认出'}；大厅读数 {lobby.saved} 次（读了 {lobby.reads} 次）；"
            f"{usage.total()}，内存峰值 {memory_mb()[1]:.0f} MB")
        kinds = "保留全部" if keep else "总结页截图、待确认的局要用的" + ("、关掉开关之前存下的" if protect else "")
        log(f"截图：留下 {left} 张（{kinds}）"
            + (f"，识别完删掉 {tidied['deleted']} 张、总结页截图 {tidied['converted']} 张转成 JPG"
               f"（腾出 {tidied['freed'] / 2 ** 20:.0f} MB）" if tidied["deleted"] or tidied["converted"] or not keep else ""))
        del engine, detector, camera, summary, other, in_match, hud, lobby, aspect, cues  # 释放识别模型和截屏，交还内存
        gc.collect()
    if status is not None:
        status["saved"] = saved
    return saved


def main():
    parser = argparse.ArgumentParser(description="打游戏时自动识别每一局并存进数据库")
    parser.add_argument("--interval", type=float, default=1, help="平时每隔几秒检查一次（默认 1）")
    parser.add_argument("--fast", type=float, default=0.3, help="在总结页、数字还没存下时，每隔几秒检查一次（默认 0.3）")
    parser.add_argument("--match-interval", type=float, default=None,
                        help="对局中每隔几秒截一张（默认看 config/hud.json 的 match_interval，0.3）")
    parser.add_argument("--summary-timeout", type=float, default=10,
                        help="在总结页最多等几秒让数字读全，超时就先存下并标记待确认（默认 10）")
    parser.add_argument("--minutes", type=float, default=240, help="最多运行几分钟（默认 240）")
    parser.add_argument("--db", type=Path, default=storage.DB_PATH, help="数据库文件（默认 data/evo.db）")
    args = parser.parse_args()

    print("按 Ctrl+C 停止。\n")
    saved = collect(db_path=args.db, interval=args.interval, fast=args.fast, summary_timeout=args.summary_timeout,
                    match_interval=args.match_interval, deadline=time.monotonic() + args.minutes * 60)
    mem_now, mem_peak = memory_mb()
    print(f"\n本次共保存 {saved} 局。内存: 现在 {mem_now:.0f} MB，峰值 {mem_peak:.0f} MB")
    print("查看数据库里的记录: python code/storage.py")


if __name__ == "__main__":
    main()
