"""测试工具（code/evo_tester.py、tester_capture.py、tester_info.py，打包成 Evo测试工具.exe 给玩家用）和看压缩包的脚本（tools/diag_report.py）。

- 轻：另开一个 Python 只导入 evo_tester，检查没加载 paths.py（它一 import 就会在程序旁边建数据文件夹）、识别引擎、OpenCV
- 截屏（假摄像头、假时钟，不真的等）：
  打一局、排查问题：游戏没开时每 10 秒看一眼、每分钟最多存一张；看到游戏后每 2 秒一张；画面没变不存；
  比 1920 宽的缩到 1920 宽，每分钟另存一张原尺寸；没有新画面、出错都记下，连续 30 次没有新画面提示一次；
  游戏关掉（连续 3 次看不到进程）就停；到时间、截满、硬盘快满、点了结束也停；每次截屏 captures.csv 一行；每分钟一次缩略图
  赛季数据页：每 0.5 秒一张，停稳 1 秒、和上一张存的不一样才存原尺寸，嘀一声；几乎没变（0.01% 的像素）不存
  靶场：武器：右下角图标换了，停稳后隔 1 秒存 3 张（整张缩小的 + 右下角原尺寸的 PNG），嘀一声；换回存过的不存；
  没拿武器时每 10 秒一张
- 测试项目（config/tester.json）、武器名单；测试员档案（exe 旁边写不进去就存到别处）；压缩包的名字
- 窗口（藏起来不显示，截屏用假的）：没填昵称不让往下走；填档案 → 选项目 → 录 → 结束 → 备注 → 打包 → 完成
- 打一局对答案（假的 Evo 数据库和日志）：读这段时间 Evo 记下的局；每局的时间段（开局前最后一次在大厅 → 最后一次读数 + 20 秒）；
  确认对的局截图全删、记错的全留、对局之间的大厅每 10 秒留一张；勾了全部带上不删；过了半夜的截图算第二天
- 打包前写 answers.json（Session）：打一局删掉确认对的截图；赛季数据页带上 Evo 核对后保存的
- 录的时候的提醒：Evo 标了待确认的攒到 2 局、游戏开着 1 小时 Evo 一局没记下、Evo 没开着录满 1 小时，每种一次
- 找 Evo 的数据文件夹：Evo.exe 旁边的 data_location.txt / 程序旁边的 Evo Tracker Data、指路文件；
  旧的默认位置别处都没有才用；没有 logs\\evo.log 的不算；同一个只算一次
- 诊断信息：这台电脑上真的读显示器（有主显示器、HDR 一栏）；Apex 视频设置只取四项；Evo 的设置只带三项；info.txt 各节都在
- 打包：压缩包里的文件和文件夹一样、JPEG 不再压缩、打完删掉文件夹；Evo 的日志带上
- 真的截几秒（dxcam，把 explorer.exe 当作游戏）：压缩包里有截图和诊断信息
- diag_report：用真实截图（简体 16:9、英文 16:10 拉伸，按工具的做法缩到 1920 宽）做压缩包，认得出各画面、传奇和游戏语言
  （截图找不到就跳过）

运行：python tests/test_tester.py
"""

import csv
import json
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from fake_game import CODE, ROOT, SHOTS, MissingShots, load, report, shot  # 要在项目代码之前导入

import numpy as np
from PIL import Image

import game_aspect
import tester_capture
import tester_info
import tester_projects

GAME = "r5apex.exe"


class Clock:
    """假时钟：wait 不真的等，只把时钟往前拨。"""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def wait(self, event, seconds):
        self.t += seconds

    def now(self):
        return datetime(2026, 10, 24, 15, 0, 0) + timedelta(seconds=self.t)


class ScriptCamera:
    """按剧本交出画面：数字 = 一张新画面（数字不同画面就不同），"same" = 和上一张一模一样，
    "none" = 没有新画面（dxcam 返回 None），"error" = 截屏出错。剧本用完以后一直交新画面。"""

    def __init__(self, script, size=(2400, 1350)):
        self.script, self.size = list(script), size
        self.last, self.extra = None, 1000
        w, h = size
        self.base = np.tile(np.linspace(0, 120, w, dtype=np.float32), (h, 1))[:, :, None].repeat(3, axis=2)

    def grab(self, new_frame_only=True):
        if self.script:
            item = self.script.pop(0)
        else:
            self.extra += 1
            item = self.extra
        if item == "none":
            return None
        if item == "error":
            raise RuntimeError("切换分辨率")
        if item != "same":
            self.last = np.clip(self.base + (item % 12) * 10, 0, 255).astype(np.uint8)
        return self.last


def make_recorder(folder, camera, names, clock, **options):
    logs = []
    rec = tester_capture.Recorder(folder, camera, names, logs.append, clock=clock.monotonic, wait=clock.wait, now=clock.now,
                            free_mb=options.pop("free_mb", lambda: 10 ** 6), **options)
    return rec, logs


def recorder_checks(tmp):
    """一整段：桌面 80 秒 → 游戏开着约 2 分钟（中间截屏卡住 1 分钟）→ 关掉游戏。"""
    clock = Clock()
    folder = tmp / "run"
    folder.mkdir()
    idle = list(range(1, 9))  # 游戏没开时 t=0..70 秒的 8 次截屏，每次都是新画面
    game = [20, 21, "same", "same", 22, "error", 23] + ["none"] * 30 + [24]
    camera = ScriptCamera(idle + game)

    def names():
        on = 75 <= clock.t < 200
        return {"explorer.exe", "Discord.exe"} | ({GAME} if on else set())

    thumbs = []

    def take_thumbs(now):
        thumbs.append(clock.t)
        return 1000

    rec, logs = make_recorder(folder, camera, names, clock, thumbs=take_thumbs)
    reason = rec.run(threading.Event())
    rec.close()
    with open(folder / "captures.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    idle_rows = [r for r in rows if r["游戏开着"] == "否"]
    sizes = {p.parent.name: Image.open(p).size for p in sorted(folder.rglob("*.jpg"))}
    small = [Image.open(p).size for p in (folder / "frames").glob("*.jpg")]
    full = [Image.open(p).size for p in (folder / "full").glob("*.jpg")]
    warned = [line for line in logs if "截屏可能卡住了" in line]
    s = rec.summary()
    return {
        f"游戏关掉（连续 3 次看不到进程）就停（{reason}，t={clock.t:g}）": reason == "游戏关掉了" and 210 <= clock.t <= 216,
        f"游戏没开时每 10 秒一次、每分钟最多存一张（{len(idle_rows)} 次，存了 "
        f"{sum(r['结果'] == '存了' for r in idle_rows)} 张）":
            len(idle_rows) == 8 and [r["结果"] for r in idle_rows].count("存了") == 2
            and [r["结果"] for r in idle_rows].count("没存（游戏没开）") == 6,
        "看到游戏以后每 2 秒一次": all(b - a == 2 for a, b in zip(*[[
            int(r["时间"][-2:]) + 60 * int(r["时间"][3:5]) for r in rows if r["游戏开着"] == "是"][i:] for i in (0, 1)])),
        f"画面没变不存（{s['没变没存']} 次）、出错记下（{s['出错']} 次）": s["没变没存"] == 2 and s["出错"] == 1,
        f"游戏开着时没有新画面 30 次、最长连续 30 次（{s['游戏开着时没有新画面']}、{s['游戏开着时最长连续没有新画面']}）":
            s["游戏开着时没有新画面"] == 30 and s["游戏开着时最长连续没有新画面"] == 30,
        f"连续 30 次没有新画面在窗口里提示一次（{len(warned)} 次）": len(warned) == 1,
        f"截图缩到 1920 宽（{set(small)}）": bool(small) and set(small) == {(1920, 1080)},
        f"每分钟另存一张原尺寸（{len(full)} 张，{set(full)}）": len(full) == 2 and set(full) == {(2400, 1350)},
        f"每次截屏 captures.csv 一行（{len(rows)} 行、截屏 {s['截屏次数']} 次）": len(rows) == s["截屏次数"] > 0,
        f"每分钟一次缩略图（t = {thumbs}）": thumbs[:4] == [0, 60, 120, 180],
        f"记下游戏进程和开着的软件（{s['看到的游戏进程']}，{s['开着的软件']}）":
            list(s["看到的游戏进程"]) == [GAME] and s["开着的软件"] == ["discord.exe"],
        f"文件夹：frames、full（{sorted(sizes)}）": sorted(sizes) == ["frames", "full"],
    }


def stop_checks(tmp):
    """另外几种停下的原因。"""
    out = {}
    cases = {"点了结束": dict(stop=True), "截满了 1 分钟": dict(minutes=1),
             "截图满了": dict(max_mb=0.0001, game=True), "硬盘空间快不够了": dict(free_mb=lambda: 100)}
    for i, (want, case) in enumerate(cases.items()):
        clock = Clock()
        folder = tmp / f"stop{i}"
        folder.mkdir()
        options = {k: v for k, v in case.items() if k in ("minutes", "max_mb", "free_mb")}
        rec, _ = make_recorder(folder, ScriptCamera([]), lambda: {GAME} if case.get("game") else set(), clock,
                               **options)
        stop = threading.Event()
        if case.get("stop"):
            stop.set()
        reason = rec.run(stop)
        rec.close()
        out[f"{want}就停（{reason}）"] = reason.startswith(want)
    return out


class ListCamera:
    """按顺序交出给定的画面（numpy 数组），用完以后一直交最后一张。"""

    def __init__(self, frames):
        self.frames = list(frames)

    def grab(self, new_frame_only=True):
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


def run_mode(tmp, name, project, frames, seconds, **options):
    """用假时钟跑一种截法 seconds 秒（游戏一直开着），返回 (截屏对象, 窗口里的提示, 嘀了几声)。"""
    clock = Clock()
    folder = tmp / name
    folder.mkdir()
    beeps = []
    logs = []
    rec = tester_capture.make_recorder(project, folder, ListCamera(frames), lambda: {GAME}, logs.append,
                                       clock=clock.monotonic, wait=clock.wait, now=clock.now,
                                       free_mb=lambda: 10 ** 6, beep=lambda: beeps.append(clock.t),
                                       minutes=seconds / 60, **options)
    rec.run(threading.Event())
    rec.close()
    return rec, logs, beeps


def still_checks(tmp):
    """赛季数据页（停稳了、和上一张存的不一样才存原尺寸）：每 0.5 秒一张。"""
    h, w = 1350, 2400
    base = np.tile(np.linspace(20, 120, w, dtype=np.float32), (h, 1)).astype(np.uint8)[:, :, None].repeat(3, axis=2)
    rng = np.random.default_rng(1)

    def moving():
        return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)

    page_a = base.copy()
    page_b = base.copy()
    page_b[400:500, 1700:1800] += 100  # 换了一个赛季：右边几个数字变了（约 0.3% 的像素）
    page_b2 = page_b.copy()
    page_b2[600:620, 1700:1720] += 100  # 几乎没变（0.01%）：当作同一页
    frames = [moving(), moving(), moving()] + [page_a] * 4 + [moving(), moving()] + [page_b] * 4 + [page_b2] * 4
    project = {"capture": "still", "interval": 0.5}
    rec, logs, beeps = run_mode(tmp, "still", project, frames, 10)
    pages = sorted((tmp / "still" / "pages").glob("*.jpg"))
    sizes = {Image.open(p).size for p in pages}
    with open(tmp / "still" / "captures.csv", encoding="utf-8-sig") as f:
        results = [r["结果"] for r in csv.DictReader(f)]
    return {
        f"数据页：停稳 1 秒才存，换了一页再存，几乎没变不存（存了 {len(pages)} 页，{results[:12]}）":
            len(pages) == 2 and rec.pages == 2 and results[3:7] == ["在动", "还没停稳", "存了", "和上一张存的一样"],
        f"数据页：存原尺寸（{sizes}），不另存 full/": sizes == {(w, h)} and not (tmp / "still" / "full").exists(),
        f"数据页：每存一页嘀一声（t = {beeps}）": beeps == [2.5, 5.5],
        f"数据页：每 0.5 秒一张（截屏 {rec.grabs} 次）": rec.grabs == 20,
    }


def weapon_checks(tmp):
    """靶场：武器（右下角的图标换了，停稳后隔 1 秒存 3 张，嘀一声；存过的样子不再存；没拿武器时每 10 秒存一张）。"""
    cfg = json.loads((ROOT / "config" / "weapons.json").read_text(encoding="utf-8"))
    h, w = 1440, 2560
    sx = w / 1920
    empty = np.full((h, w, 3), 40, np.uint8)
    empty[:, :, 1] = np.tile(np.arange(w) % 200, (h, 1))  # 不是纯色：存下的那块看得出是不是原样

    def holding(x0, y0, x1, y1):
        frame = empty.copy()
        frame[round(y0 * sx):round(y1 * sx), round(x0 * sx):round(x1 * sx)] = 250  # 基准坐标里的一块白色图标
        return frame

    gun_a = holding(1540, 965, 1600, 995)
    gun_b = holding(1620, 970, 1700, 990)
    frames = [empty] + [gun_a] * 8 + [gun_b] * 7 + [gun_a] * 3 + [empty] * 42
    rec, logs, beeps = run_mode(tmp, "weapons", {"capture": "weapons", "interval": 0.5}, frames, 30,
                                weapons_cfg=cfg, aspect=None)
    folder = tmp / "weapons"
    crops = sorted((folder / "crops").glob("*.png"))
    small = sorted((folder / "frames").glob("*.jpg"))
    first = crops[1]  # 第一把的第一张
    x0, y0 = (int(part[1:]) for part in first.stem.split("_")[-2:])
    crop = np.asarray(Image.open(first))[:, :, ::-1]
    same = np.array_equal(crop, gun_a[y0:y0 + crop.shape[0], x0:x0 + crop.shape[1]])
    times = [p.stem.split("_")[1] for p in crops]
    return {
        f"武器：两把各存 3 张、换回存过的不再存、没拿武器时每 10 秒一张（共 {len(crops)} 张，{times}）":
            len(crops) == len(small) == 9 and rec.summary()["存好了几把"] == 2
            and times == ["150000", "150001", "150002", "150003", "150005", "150006", "150007", "150017", "150027"],
        f"武器：每把存够了嘀一声（t = {beeps}）": beeps == [3.0, 7.0],
        f"武器：右下角那块是原尺寸无损的 PNG，文件名带位置（x{x0} y{y0}，{crop.shape[1]}×{crop.shape[0]}）":
            same and (x0, y0) == (1867, 1173) and crop.shape[:2] == (267, 693),
        "武器：整张缩到 1920 宽": {Image.open(p).size for p in small} == {(1920, 1080)},
    }


def project_checks(tmp):
    """测试项目（config/tester.json）、武器名单、测试员档案、压缩包的名字。"""
    projects = tester_projects.load_projects()
    weapons = tester_projects.weapon_list()
    first, second = tmp / "profile" / "exe", tmp / "profile" / "local"
    (first.parent).mkdir()
    first.write_text("这是一个文件，档案写不进这里", encoding="utf-8")  # 当作 exe 旁边写不进去
    saved = tester_projects.save_profile({"nickname": " 小明 ", "game_language": "English", "x": 1}, [first, second])
    loaded = tester_projects.load_profile([first, second])
    name = tester_projects.package_name(projects["weapons"], "a/b c", datetime(2026, 11, 2, 21, 30, 5))
    captures = {key: spec["capture"] for key, spec in projects.items()}
    return {
        f"测试项目按顺序：打一局、靶场：武器、赛季数据页、排查问题（{[p['name'] for p in projects.values()]}）":
            list(projects) == ["match", "weapons", "seasons", "diag"]
            and all(p["steps"] and p["summary"] for p in projects.values()),
        f"每项的截法都认得（{captures}）": set(captures.values()) <= set(tester_capture.RECORDERS),
        f"武器名单来自 config/weapons.json（{len(weapons)} 把，如 {weapons[0]}）": len(weapons) >= 30,
        f"档案：exe 旁边写不进去就存到下一个地方（{saved}），读回来只有三项（{loaded}）":
            saved == second / "tester_profile.json"
            and loaded == {"nickname": "小明", "game_language": "English", "launch_options": ""},
        f"压缩包的名字去掉文件名里不能用的字符（{name}）": name == "Evo测试_靶场-武器_abc_20261102_213005",
    }


def fake_evo(tmp):
    """一个假的 Evo 数据文件夹（数据库 + 日志），2026-11-02 21:00–22:00 打了两局：
    第 1 局 21:10 开局、21:25:30 总结页、21:26:10 经验值页；第 2 局 21:36 开局、21:50 总结页（Evo 标了待确认）；
    另有一局 20:00 打的（测试开始以前，不算）；21:28 大厅读到钻石 I 600。"""
    import storage

    evo = tmp / "evo_data"
    (evo / "data").mkdir(parents=True)
    (evo / "logs").mkdir()
    conn = storage.connect(evo / "data" / "evo.db")
    day = "2026-11-02"
    old = storage.insert_match(conn, {"ended_at": f"{day} 20:00:00", "placement": 9})
    storage.save_readings(conn, old, "总结页", {"placement": 9}, f"{day} 20:00:00")
    first = storage.insert_match(conn, {"ended_at": f"{day} 21:25:30", "mode": "排位", "map": "世界尽头", "legend": "罗芭",
                                        "placement": 5, "kills": 3, "assists": 1, "knockdowns": 4, "damage": 1200,
                                        "rp_delta": 45})
    storage.save_readings(conn, first, "总结页", {"placement": 5, "kills": 3}, f"{day} 21:25:30")
    storage.save_readings(conn, first, "经验值页", {"rp_delta": 45}, f"{day} 21:26:10")
    second = storage.insert_match(conn, {"ended_at": f"{day} 21:50:00", "mode": "排位", "placement": 12, "kills": 0,
                                         "damage": 350, "rp_delta": -30, "needs_review": 1, "review_note": "名次对不上"})
    storage.save_readings(conn, second, "总结页", {"placement": 12}, f"{day} 21:50:00")
    storage.save_lobby(conn, {"read_at": f"{day} 21:28:00", "rank_tier": "钻石", "rank_division": "I",
                              "rank_progress": 600, "score": 15600})
    conn.close()
    lines = ["21:00:05 [21:00:05] 进入大厅", "21:08:00 [21:08:00] 进入大厅", "21:08:01 [21:08:01] 大厅：钻石 I 555（总分 15555）",
             "21:10:00 [21:10:00] 进入开局画面", "21:11:00 [21:11:00] 对局中：看到自己的界面",
             "21:25:31 [21:25:30] 第 1 局（编号 2）：第 5 名", "21:27:00 [21:27:00] 进入大厅", "21:35:00 [21:35:00] 进入大厅",
             "21:36:00 [21:36:00] 进入开局画面"]
    (evo / "logs" / "evo.log").write_text("".join(f"{day} {line}\n" for line in lines), encoding="utf-8")
    return evo, first, second


def fake_frames(folder, start, minutes, step=5):
    """frames/ 每 step 秒一张、full/ 每分钟一张（空文件，名字和测试工具存的一样）。"""
    (folder / "frames").mkdir(parents=True)
    (folder / "full").mkdir()
    for i in range(minutes * 60 // step):
        t = start + timedelta(seconds=i * step)
        (folder / "frames" / f"{i + 1:04d}_{t:%H%M%S}_1920x1080.jpg").write_bytes(b"")
        if t.second == 0:
            (folder / "full" / f"{t:%H%M%S}_2560x1440.jpg").write_bytes(b"")


def answers_checks(tmp):
    """打一局对答案（tester_answers.py）：读 Evo 这段时间记下的局、分每局的时间段、确认对的局删截图、大厅每 10 秒留一张。"""
    import tester_answers as ta

    evo, first, second = fake_evo(tmp)
    db = ta.db_path([(evo, "开机自启")])
    start, end = datetime(2026, 11, 2, 21, 0, 0), datetime(2026, 11, 2, 22, 0, 0)
    matches = ta.evo_matches(db, start, end)
    events = ta.log_events(evo / "logs", start, end)
    windows = ta.match_windows(matches, events, start)
    shown = [[f"{a:%H:%M:%S}", f"{b:%H:%M:%S}"] for a, b in windows]
    lobby = ta.lobby_readings(db, start, end)

    def times(folder, sub):
        return [ta.datetime.strptime(p.name.split("_")[-2], "%H%M%S").time() for p in sorted((folder / sub).glob("*.jpg"))]

    run = tmp / "answers_run"
    fake_frames(run, start, 60)
    answers = {"局": {first: {"判断": "对"}, second: {"判断": "不对", "正确的": {"placement": 11}}}, "漏记": "没有",
               "大厅": {"Evo 读的": "钻石 I 600", "判断": "对"}, "全部带上": False}
    result = ta.apply_match_answers(run, start, matches, events, answers)
    left = [ta.datetime.combine(start.date(), t) for t in times(run, "frames")]
    in_first = [t for t in left if windows[0][0] <= t <= windows[0][1]]
    in_second = [t for t in left if windows[1][0] <= t <= windows[1][1]]
    lobby_left = [t for t in left if not any(a <= t <= b for a, b in windows)]
    gaps = {(b - a).total_seconds() for a, b in zip(lobby_left, lobby_left[1:])
            if not any(a < w[0] <= b for w in windows)}  # 同一段大厅里相邻两张隔几秒
    keep_all = tmp / "answers_keep"
    fake_frames(keep_all, start, 60)
    ta.apply_match_answers(keep_all, start, matches, events, {**answers, "全部带上": True})
    unsure = tmp / "answers_unsure"
    fake_frames(unsure, start, 60)
    ta.apply_match_answers(unsure, start, matches, events, {**answers, "漏记": "有"})
    night = tmp / "answers_night"
    fake_frames(night, datetime(2026, 11, 2, 23, 59, 0), 2, step=30)
    crossed = [t for t, _ in ta.frame_files(night, datetime(2026, 11, 2, 23, 59, 0))]
    return {
        f"读到这段时间 Evo 记下的两局（测试开始以前那局不算；{[m['match_id'] for m in matches]}）":
            [m["match_id"] for m in matches] == [first, second] and matches[0]["last_read"].strftime("%H:%M:%S") == "21:26:10",
        f"每局的时间段：开局前最后一次在大厅 → 最后一次读数 + 20 秒（{shown}）":
            shown == [["21:08:01", "21:26:30"], ["21:35:00", "21:50:20"]],
        f"大厅读数（{lobby and ta.rank_text(lobby[-1])}）": [ta.rank_text(r) for r in lobby] == ["钻石 I 600"],
        f"确认对的局：截图全删（剩 {len(in_first)} 张；删了 {result['截图']['确认对的局里删了']} 张，含每分钟的原图）":
            not in_first and result["截图"]["确认对的局里删了"] == 222 + 18 and not any(
                windows[0][0].time() <= t <= windows[0][1].time() for t in times(run, "full")),
        f"记错的局：截图全留（{len(in_second)} 张）": len(in_second) == 185,
        f"没漏记、大厅也对：对局之间的大厅每 10 秒留一张（隔 {sorted(gaps)} 秒）": gaps == {10.0},
        "勾了全部带上：一张不删": len(list((keep_all / "frames").glob("*.jpg"))) == 720,
        "说有漏记的局：对局之间的大厅全留": len(list((unsure / "frames").glob("*.jpg"))) == 720 - 222,
        f"答案里写着每局的时间段、测试员的判断和改正（{result['局'][1]['测试员']}、{result['局'][1]['正确的']}）":
            result["局"][1]["测试员"] == "不对" and result["局"][1]["正确的"] == {"placement": 11}
            and result["局"][0]["时间段"] == ["2026-11-02 21:08:01", "2026-11-02 21:26:30"],
        f"过了半夜的截图算第二天（{[f'{t:%d %H:%M:%S}' for t in crossed]}）":
            [f"{t:%d %H:%M}" for t in crossed] == ["02 23:59"] * 3 + ["03 00:00"] * 3,
    }


def session_answers_checks(tmp):
    """Session 打包前写 answers.json：打一局（删掉确认对的局的截图）、赛季数据页（Evo 读到的、核对后保存的）。"""
    import storage

    from evo_tester import Session

    evo, first, second = fake_evo(tmp / "session")
    conn = storage.connect(evo / "data" / "evo.db")
    stat = storage.save_season_pending(conn, {"player_name": "小明", "season": 25, "kind": "ranked",
                                              "season_name": "排位", "games": 598, "kills": 593,
                                              "read_at": "2026-11-02 21:40:00"})
    storage.save_season(conn, stat, {"kills": 594})  # 测试员在 Evo 的赛季页把击杀改成 594 再保存
    conn.close()
    projects = tester_projects.load_projects()
    start, end = datetime(2026, 11, 2, 21, 0, 0), datetime(2026, 11, 2, 22, 0, 0)
    out = {}
    for key in ("match", "seasons"):
        session = Session(projects[key], {"nickname": "小明"}, tmp / "session" / key, lambda _text: None)
        session.stamp, session.ended = start, end
        session.evo_dirs = [(evo, "开机自启")]
        session.db = evo / "data" / "evo.db"
        fake_frames(session.folder, start, 60)
        session._write_answers({"局": {first: {"判断": "对"}}, "漏记": "有", "全部带上": False})
        session._log_file.close()
        out[key] = (json.loads((session.folder / "answers.json").read_text(encoding="utf-8")),
                    len(list((session.folder / "frames").glob("*.jpg"))))
    (match, match_left), (seasons, _) = out["match"], out["seasons"]
    row = seasons["赛季"][0] if seasons["赛季"] else {}
    return {
        f"打一局：answers.json 有两局、确认对的那局截图删了（剩 {match_left} 张）、带上 Evo 的大厅读数":
            [m["match_id"] for m in match["局"]] == [first, second] and match["局"][0]["测试员"] == "对"
            and match_left == 720 - 222 and match["Evo 大厅读数"][0]["rank_progress"] == 600,
        f"赛季数据页：answers.json 带上核对后保存的（{row.get('status')}，击杀 {row.get('kills')}，改过 {row.get('edited')}）":
            row.get("status") == "saved" and row.get("kills") == 594 and row.get("edited") == "kills",
    }


def reminder_checks():
    """打一局录的时候的提醒（evo_tester.Reminder）：每种只提醒一次。"""
    from evo_tester import Reminder

    flagged = [{"needs_review": 1}, {"needs_review": 0}, {"needs_review": 1}]
    r = Reminder(review=2, minutes=60)
    first = r.check(600, 600, True, flagged[:2], 100)
    second = r.check(900, 900, True, flagged, 200)
    again = r.check(1200, 1200, True, flagged, 300)
    nothing = Reminder(review=2, minutes=60)
    early = nothing.check(3000, 3000, True, [], 500)
    late = nothing.check(4000, 3700, True, [], 600)
    unreadable = Reminder().check(4000, 3700, True, None, 600)
    no_evo = Reminder()
    no_evo_early, no_evo_late = no_evo.check(3000, 0, False, None, 800), no_evo.check(3700, 0, False, None, 900)
    return {
        f"Evo 标了待确认的攒到 2 局提醒一次（{second}）": not first and len(second) == 1 and "2 局" in second[0] and not again,
        f"游戏开着 1 小时 Evo 一局没记下：提醒（{late}）；读不到数据库不算": not early and len(late) == 1 and not unreadable,
        f"Evo 没开着录满 1 小时：提醒，带上已经多大（{no_evo_late}）": not no_evo_early and "900 MB" in no_evo_late[0],
    }


def window_checks(tmp):
    """窗口（tester_window.py，藏起来不显示）：填档案 → 选赛季数据页 → 录 → 结束 → 写备注 → 打包 → 完成。截屏用假的。"""
    import tkinter as tk

    import tester_window

    zip_file = tmp / "window.zip"
    zip_file.write_bytes(b"zip")
    opened, saved, sessions = [], [], []

    class Rec:
        saved, mb, game_on, pages = 3, 1.0, True, 2

    class FakeSession:
        def __init__(self, project, profile, log, notice):
            self.project, self.profile, self.folder = project, profile, tmp
            self.recorder = self.info = self.note = self.answers = None
            sessions.append(self)
            log("准备好了")
            notice("Evo 已经有 2 局标了待确认")

        def start(self):
            self.recorder, self.info = Rec(), {}

        def run(self, stop):
            stop.wait(10)
            return "点了结束"

        def evo_record(self):
            return {"数据库": "evo.db", "局": [], "大厅": [], "赛季": [{"status": "pending"}, {"status": "saved"}]}

        def finish(self, note, answers=None):
            self.note, self.answers = note, answers
            return zip_file

    def widgets(parent, cls):
        for child in parent.winfo_children():
            if isinstance(child, cls):
                yield child
            yield from widgets(child, cls)

    def find(cls, text):
        return next(w for w in widgets(root, cls) if w.cget("text") == text)

    def pump(until, seconds=5):
        end = time.time() + seconds
        while not until() and time.time() < end:
            root.update()
            time.sleep(0.02)
        return until()

    root = tk.Tk()
    root.withdraw()
    try:
        app = tester_window.TesterApp(root, tester_projects.load_projects(), {}, tester_projects.languages(),
                                      tester_projects.weapon_list(), saved.append, FakeSession, lambda: True, "测试",
                                      open_folder=opened.append)
        find(tk.Button, "下一步").invoke()
        asked = any(w.cget("text") == "请填一下昵称" for w in widgets(root, tk.Label))
        next(widgets(root, tk.Entry)).insert(0, "小明")
        find(tk.Radiobutton, "English").invoke()
        find(tk.Button, "下一步").invoke()
        on_projects = any(w.cget("text") == "开始" for w in widgets(root, tk.Button))
        find(tk.Radiobutton, "赛季数据页").invoke()
        find(tk.Button, "开始").invoke()
        recording = pump(lambda: app.state == "recording" and "存好了 2 页" in app.v_count.cget("text"))
        noticed = any("待确认" in w.cget("text") for w in widgets(app.notice, tk.Label))
        find(tk.Button, "结束").invoke()
        finishing = pump(lambda: app.state == "finishing")
        season_line = any("还有 1 项没核对" in w.cget("text").replace("\u00a0", " ") for w in widgets(root, tk.Label))
        next(widgets(root, tk.Text)).insert("1.0", "21:35 第二页没嘀")
        find(tk.Button, "打包").invoke()
        done = pump(lambda: app.state == "done")
    finally:
        root.destroy()
    session = sessions[0] if sessions else None
    return {
        f"窗口：没填昵称不让往下走（提示了：{asked}）": asked,
        f"窗口：档案存下来（{saved}）、到选项目": saved == [{"nickname": "小明", "game_language": "English",
                                                   "launch_options": ""}] and on_projects,
        f"窗口：选的项目和档案交给截屏（{session and session.project['key']}）":
            session is not None and session.project["key"] == "seasons" and session.profile["nickname"] == "小明",
        f"窗口：录的时候显示存了几页（{recording}）、提醒放在黄色框里（{noticed}）、点结束到收尾（{finishing}）":
            recording and noticed and finishing,
        f"窗口：赛季数据页收尾时说 Evo 读到几项、几项没核对（{season_line}）": season_line,
        f"窗口：备注跟着打包（{session and session.note}），打完到完成、打开文件夹（{opened}）":
            done and session.note == "21:35 第二页没嘀" and opened == [zip_file],
    }


def finder_checks(tmp):
    def evo_dir(folder, log=True):
        folder.mkdir(parents=True, exist_ok=True)
        if log:
            (folder / "logs").mkdir(exist_ok=True)
            (folder / "logs" / "evo.log").write_text("Evo 0.3.8 启动\n", encoding="utf-8")
        return folder

    default = evo_dir(tmp / "find" / "Evo Tracker Data")  # D:\Evo → D:\Evo Tracker Data
    moved = evo_dir(tmp / "find" / "moved")
    prog_b = tmp / "find" / "progB"
    prog_b.mkdir()
    (prog_b / "data_location.txt").write_text(str(moved), encoding="utf-8")
    local = evo_dir(tmp / "find" / "local")
    pointed = evo_dir(tmp / "find" / "pointed")
    (local / "data_location.txt").write_text(str(pointed), encoding="utf-8")
    empty = evo_dir(tmp / "find" / "empty", log=False)
    lonely = evo_dir(tmp / "find" / "lonely")
    exes = [(tmp / "find" / "progA" / "Evo.exe", "正在运行的 Evo"), (prog_b / "Evo.exe", "开机自启"),
            (tmp / "find" / "progA" / "Evo.exe", "开机自启")]
    found = tester_info.find_evo_data_dirs(exes=exes, local_dir=local)
    only_legacy = tester_info.find_evo_data_dirs(exes=[], local_dir=lonely)
    none = tester_info.find_evo_data_dirs(exes=[(empty / "x" / "Evo.exe", "开机自启")], local_dir=empty)
    want = [(default, "正在运行的 Evo"), (moved, "开机自启"), (pointed, "指路文件")]
    return {
        f"程序旁边的 Evo Tracker Data、data_location.txt、指路文件都找到，同一个只算一次（{found}）": found == want,
        "旧的默认位置：别处都没有才用": only_legacy == [(lonely, "旧的默认位置")] and all(f != local for f, _ in found),
        "没有 logs\\evo.log 的不算": none == [],
    }


def info_checks(tmp):
    video = Path(game_aspect.VIDEO_CONFIG)
    video.write_text('"VideoConfig"\n{\n\t"setting.defaultres"\t\t"3840"\n\t"setting.defaultresheight"\t\t"2160"\n'
                     '\t"setting.fullscreen"\t\t"1"\n\t"setting.nowindowborder"\t\t"0"\n\t"setting.gamma"\t\t"1.0"\n}\n',
                     encoding="utf-8")
    apex = tester_info.apex_video()
    video.unlink()
    settings_dir = tmp / "settings"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(json.dumps({"game_language": "auto", "game_aspect": "16:10",
                                                            "keep_all_screenshots": False, "cloud_device": "秘密",
                                                            "telemetry_id": "x"}), encoding="utf-8")
    picked = tester_info.evo_settings(settings_dir)
    info = tester_info.collect_info(None, None, {"截法": None, "dxcam": "测试时不建截屏对象", "图形设置": None,
                                                "错误": "测试"}, [(settings_dir, "开机自启")])
    mons = info["显示器"]
    text = tester_info.render_info(info)
    return {
        f"这台电脑上读得到显示器，有一个是主显示器、每个都有 HDR 一栏（{mons}）":
            isinstance(mons, list) and sum(m["primary"] for m in mons) == 1 and all("hdr" in m for m in mons),
        f"Apex 视频设置只取四项（{apex}）": apex == {"宽": "3840", "高": "2160", "全屏": "1", "无边框": "0"},
        f"Evo 的设置只带三项（{picked}）": set(picked) == {"game_language", "game_aspect", "keep_all_screenshots"},
        "info.txt 各节都在": all(f"[{s}" in text for s in ("电脑", "显示器", "dxcam", "Apex", "Evo")),
    }


def zip_checks(tmp):
    folder = tmp / "pack" / "Evo诊断_x"
    (folder / "frames").mkdir(parents=True)
    (folder / "frames" / "0001.jpg").write_bytes(tester_capture.encode_jpeg(np.zeros((1080, 1920, 3), np.uint8)))
    (folder / "info.txt").write_text("信息\n" * 100, encoding="utf-8")
    logs = tmp / "pack" / "evo" / "logs"
    logs.mkdir(parents=True)
    for name in ("evo.log", "evo.log.1"):
        (logs / name).write_text("Evo 0.3.8 启动\n", encoding="utf-8")
    copied = tester_info.copy_evo_logs([(logs.parent, "开机自启")], folder)
    zip_path = tester_capture.make_zip(folder, tmp / "pack" / "Evo诊断_x.zip")
    with zipfile.ZipFile(zip_path) as zf:
        kinds = {i.filename: i.compress_type for i in zf.infolist()}
    return {
        f"Evo 的日志带上（{copied} 个）": copied == 2,
        f"压缩包里的文件和文件夹一样（{sorted(kinds)}）":
            sorted(kinds) == ["evo_logs/evo.log", "evo_logs/evo.log.1", "frames/0001.jpg", "info.txt"],
        "JPEG 不再压缩、文字压缩": kinds["frames/0001.jpg"] == zipfile.ZIP_STORED
                              and kinds["info.txt"] == zipfile.ZIP_DEFLATED,
        "打完删掉文件夹、不留 .part": not folder.exists() and not list((tmp / "pack").glob("*.part")),
    }


def light_checks():
    code = ("import sys, evo_tester; print(','.join(m for m in ('paths', 'app_settings', 'cv2', 'rapidocr', 'onnxruntime')"
            " if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=CODE, capture_output=True, text=True, check=True)
    loaded = [m for m in out.stdout.strip().split(",") if m]
    return {f"只导入 evo_tester 不加载 paths.py、识别引擎、OpenCV（加载了：{'、'.join(loaded) or '无'}）": not loaded}


def real_checks(tmp):
    """真的截几秒：dxcam 截主显示器，把 explorer.exe 当作游戏（一定开着）。"""
    out = tmp / "real"
    result = subprocess.run([sys.executable, str(CODE / "evo_tester.py"), "--minutes", "0.12", "--game", "explorer.exe",
                             "--out", str(out), "--no-wait"], stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, encoding="utf-8", timeout=120)
    zips = list(out.glob("*.zip"))
    if result.returncode != 0 or len(zips) != 1:
        print(result.stdout[-2000:], result.stderr[-2000:])
        return {"真的截几秒：生成了压缩包": False}
    with zipfile.ZipFile(zips[0]) as zf:
        names = zf.namelist()
        info = json.loads(zf.read("info.json"))
    frames = [n for n in names if n.startswith("frames/")]
    out_checks = {
        f"真的截几秒：压缩包里有截图（{len(frames)} 张）、诊断信息、截屏记录、工具日志、tester.json":
            frames and all(n in names for n in ("info.json", "info.txt", "captures.csv", "tool_log.txt", "tester.json")),
        f"截屏对象和 Evo 一样建好了（{info['dxcam']['截屏对象']}，{info['dxcam']['截法']}）":
            info["dxcam"]["截屏对象"] == "建好了" and info["dxcam"]["截法"] == "dxcam",
        f"诊断信息里列了全部显卡（{info['dxcam'].get('全部显卡')}）、Evo.exe 的图形设置":
            isinstance(info["dxcam"].get("全部显卡"), list) and len(info["dxcam"]["全部显卡"]) >= 1
            and "图形设置" in info["Evo"],
        "没留下文件夹": [p for p in out.iterdir() if p.is_dir()] == [],
    }
    # 另外两种截法也真跑几秒：桌面常常在动（终端在刷新），数据页那种不一定存得下，只看截了、记了、打包了；
    # 武器那种第一张一定存（手上没武器时每 10 秒存一张）
    for project, folder in (("seasons", None), ("weapons", "crops/")):
        where = tmp / f"real_{project}"
        subprocess.run([sys.executable, str(CODE / "evo_tester.py"), "--project", project, "--minutes", "0.05",
                        "--game", "explorer.exe", "--out", str(where), "--no-wait"], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
        zips = list(where.glob("*.zip"))
        zf = zipfile.ZipFile(zips[0]) if len(zips) == 1 else None
        names = zf.namelist() if zf else []
        tester = json.loads(zf.read("tester.json")) if zf else {}
        rows = len(zf.read("captures.csv").decode("utf-8-sig").splitlines()) - 1 if zf else 0
        saved = folder is None or any(n.startswith(folder) for n in names)
        out_checks[f"真的截几秒（{project}）：截了 {rows} 次{f'、{folder} 里有截图' if folder else ''}、"
                   f"tester.json 写着项目（{tester.get('项目')}）"] = rows >= 4 and saved and tester.get("项目") == project
    return out_checks


def fake_zip(tmp, name, images, video=None):
    """按工具的做法（缩到 1920 宽）把几张真实截图做成一个诊断压缩包。"""
    folder = tmp / name
    (folder / "frames").mkdir(parents=True)
    rows = []
    for i, img in enumerate(images, 1):
        h, w = img.shape[:2]
        file = f"frames/{i:04d}_150000_{w}x{h}.jpg"
        (folder / file).write_bytes(tester_capture.encode_jpeg(img, tester_capture.SMALL_WIDTH))
        rows.append({"序号": i, "时间": "15:00:00", "游戏开着": "是", "结果": "存了", "宽": w, "高": h, "文件": file})
    with open(folder / "captures.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, tester_capture.CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    info = tester_info.collect_info(None, None, {"截法": None, "dxcam": "测试", "图形设置": None}, [])
    info["Apex"]["视频设置"] = video or "没找到视频设置文件"
    info["截屏结果"] = {"看到的游戏进程": {GAME: "15:00:00"}}
    Path(folder, "info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    Path(folder, "info.txt").write_text("测试\n", encoding="utf-8-sig")
    return tester_capture.make_zip(folder, tmp / f"{name}.zip")


def run_report(zip_path):
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "diag_report.py"), str(zip_path)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=600)
    if result.returncode != 0:
        print(result.stderr[-2000:])
    return result.stdout


def report_checks(tmp):
    try:
        night = "live/20261016_211452/"
        zh = [load(shot(night + n)) for n in ("lobby_211501_enter.png", "match_start_211755_enter.png",
                                              "05_222749.png", "xp_213324_enter.png")] + [load(shot("samples/对局中.png"))]
        en_files = sorted((SHOTS / "frames" / "英文").glob("*.jpg"))
        if len(en_files) < 1501:
            raise MissingShots(SHOTS / "frames" / "英文")
        en = [load(en_files[n - 1]) for n in (50, 106, 658, 667, 300, 1491)]  # 编号同 test_english.py
    except MissingShots as e:
        print(f"  ⏭ 跳过看压缩包的脚本：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None
    zh_out = run_report(fake_zip(tmp, "zh", zh))
    en_out = run_report(fake_zip(tmp, "en", en, {"宽": "2560", "高": "1600", "全屏": "1", "无边框": "1"}))
    want_zh = ["大厅 1 张", "开局画面 1 张", "总结页 1 张", "经验值页 1 张", "游戏语言：简体中文", "左下角认出自己的传奇：1 张（罗芭 1）"]
    want_en = ["大厅 1 张", "开局画面 1 张", "总结页 1 张", "经验值页 1 张", "淘汰画面 1 张", "游戏语言：English",
               "左下角认出自己的传奇：1 张（罗芭 1）", "Apex 视频设置：全屏 2560×1600"]
    return {
        f"简体（16:9）：{'、'.join(want_zh)}": all(w in zh_out for w in want_zh),
        f"英文（16:10 拉伸，按 Apex 视频设置换算）：{'、'.join(want_en)}": all(w in en_out for w in want_en),
    }


def main():
    print("测试工具（截屏、找 Evo、诊断信息、打包、看压缩包）")
    with tempfile.TemporaryDirectory(prefix="evo-tester-") as name:
        tmp = Path(name)
        checks = {**light_checks(), **recorder_checks(tmp), **stop_checks(tmp), **still_checks(tmp),
                  **answers_checks(tmp), **session_answers_checks(tmp), **reminder_checks(),
                  **weapon_checks(tmp), **project_checks(tmp), **window_checks(tmp), **finder_checks(tmp),
                  **info_checks(tmp), **zip_checks(tmp), **real_checks(tmp)}
        ok = report(checks)
        shots = report_checks(tmp)
        if shots is not None:
            ok = report(shots) and ok
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
