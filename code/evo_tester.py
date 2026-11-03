"""Evo 测试工具（2026-11-02 起，原来的诊断工具）：帮 Evo 测试、适配新分辨率和语言的玩家，和记不下战绩的玩家，
截一段游戏画面，连同电脑的显示设置、Evo 的日志打成一个压缩包，发给开发者。

和 Evo 分开打包成一个 exe（python tools/build_tester.py），玩家下载后双击运行。
- 测试项目（打一局、靶场：武器、赛季数据页、排查问题）和测试员档案见 tester_projects.py、config/tester.json
- 截屏、打包见 tester_capture.py；显示器、Apex 设置、Evo 的日志见 tester_info.py
- 结束：玩家点"结束"（命令行里按回车）、游戏关掉、截满时间或大小、硬盘快满。压缩包放在桌面，打开文件夹选中它
- 只看不动（原则 1）：只截屏、只读进程名和 Windows 的显示设置；结束用的是本窗口自己的按钮 / 输入，不注册全局键盘钩子；
  只找 Evo.exe 自己在哪个文件夹，不打开游戏进程；不联网，压缩包发不发由玩家决定

不 import paths.py（它一 import 就会在程序旁边建"Evo Tracker Data"文件夹）。

开发时运行：python code/evo_tester.py（窗口）；python code/evo_tester.py --project match [--minutes 5] [--out 文件夹]（命令行）
看玩家发来的压缩包：python tools/diag_report.py Evo测试_xxx.zip
"""

import argparse
import ctypes
import json
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from game_detect import GAME_PROCESSES, processes
from process_info import memory_mb, set_low_priority
from tester_answers import apply_match_answers, db_path, evo_matches, lobby_readings, log_events, season_rows
from tester_capture import MonitorThumbs, default_out_dir, make_recorder, make_zip, open_camera
from tester_info import _safe, collect_info, copy_evo_logs, find_evo_data_dirs, game_aspect_guess, write_info
from tester_projects import (languages, load_config, load_profile, load_projects, package_name, save_profile,
                             weapon_list)

VERSION = "2.1"  # 1.x 是原来的诊断工具；2.1：dxcam 用不了时换 mss（capture.py）
BEEP_HZ, BEEP_MS = 1200, 150  # "嘀"一声（同 tools/legend_capture.py）
WATCH_SECONDS = 30  # 打一局：录的时候每隔这么久看一眼 Evo 记下了什么


# ---------- 一次测试 ----------

def beep():
    """"嘀"一声（存好一页数据页、一把武器时）。另开线程：winsound.Beep 要等声音放完。"""
    import winsound

    threading.Thread(target=lambda: winsound.Beep(BEEP_HZ, BEEP_MS), daemon=True).start()


def evo_running():
    """Evo（安装版的 Evo.exe）开着没有。用源码运行的 Evo 是 python.exe，看不出来。"""
    return "evo.exe" in {name for _, name in processes()}


class Reminder:
    """打一局：录的时候提醒测试员"截图够了，可以结束了"，每种只提醒一次，只提醒、不停（2026-11-02 用户定的）。
    录的时候不知道哪局记错（测试员打完才确认），所以按看得出的提醒：Evo 标了待确认的局攒到 review 局；
    游戏开着 minutes 分钟 Evo 一局没记下（多半没认出画面）；Evo 没开着（截图全要带上）录满 minutes 分钟。"""

    def __init__(self, review=2, minutes=60):
        self.review, self.minutes = review, minutes
        self.told = set()

    def check(self, elapsed, game_seconds, evo_on, matches, mb):
        """elapsed：录了几秒；game_seconds：其中游戏开着几秒；matches：这段时间 Evo 记下的局（None = 读不到 Evo 的数据库）。
        返回这次要提醒的话（可能没有）。"""
        out = []
        flagged = sum(1 for m in matches or [] if m.get("needs_review"))
        if flagged >= self.review and "review" not in self.told:
            self.told.add("review")
            out.append(f"Evo 已经有 {flagged} 局标了待确认，截图够开发者查了，可以结束了（想接着打也行）")
        if (evo_on and matches == [] and game_seconds >= self.minutes * 60 and "nothing" not in self.told):
            self.told.add("nothing")
            out.append(f"游戏开了 {self.minutes} 分钟，Evo 一局都没记下（可能没认出画面）。截图够开发者查了，可以结束了")
        if not evo_on and elapsed >= self.minutes * 60 and "no_evo" not in self.told:
            self.told.add("no_evo")
            out.append(f"Evo 没开着，截图会全部带上，已经 {mb:.0f} MB 了，可以结束了")
        return out


class Session:
    """一次测试：start() 建截屏对象、读诊断信息 → run(stop) 截屏到结束 → evo_record() 读这段时间 Evo 记下的（对答案用）
    → finish() 删掉确认对了的截图、带上 Evo 的日志、写 tester.json 和 answers.json、打包。
    log(文字)：写进窗口和 tool_log.txt；notice(文字)：要测试员看见的提醒（默认同 log）。games：另外当作游戏的进程名（测试用）。"""

    def __init__(self, project, profile, out_root, log, games=(), minutes=None, sound=True, version=VERSION,
                 notice=None):
        self.project, self.profile, self.out_root, self.version = project, profile, Path(out_root), version
        self.minutes, self.sound = minutes, sound
        self.games = GAME_PROCESSES | {g.lower() for g in games}
        self.stamp = datetime.now()
        self.folder = self.out_root / package_name(project, profile.get("nickname"), self.stamp)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.folder / "tool_log.txt", "a", encoding="utf-8-sig")
        self._show = log
        self._notice = notice
        self.warnings = []
        self.camera = self.thumbs = self.recorder = None
        self.evo_dirs, self.db, self.info, self.reason, self.ended = [], None, None, None, None
        self._dx_handler = None
        self._done = threading.Event()  # 截屏结束了（提醒的线程跟着停）

    def log(self, text):
        self._show(text)
        if not self._log_file.closed:
            self._log_file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
            self._log_file.flush()

    def notice(self, text):
        line = f"[{datetime.now():%H:%M:%S}] {text}"
        if self._notice is None:
            self.log(line)
        else:
            self._notice(text)
            if not self._log_file.closed:
                self._log_file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} 提醒：{line}\n")
                self._log_file.flush()

    def start(self):
        session = self

        class DxcamWarnings(logging.Handler):  # dxcam 自己的警告（截屏卡住、显示器变了时会打）：Evo 的日志里没有
            def emit(self, record):
                session.warnings.append(record)
                session.log(f"[{datetime.now():%H:%M:%S}] dxcam 警告：{record.getMessage()}")

        self._dx_handler = DxcamWarnings(logging.WARNING)
        dx_logger = logging.getLogger("dxcam")
        dx_logger.addHandler(self._dx_handler)
        dx_logger.setLevel(logging.WARNING)

        self.log(f"Evo 测试工具 {self.version}：{self.project['name']}，先存在：{self.folder}")
        dxcam_mod, self.camera, notes = open_camera(lambda text: self.log(f"⚠ {text}"))
        if self.camera is None:
            self.log(f"⚠ 截屏对象建不起来（Evo 也会在这里出错）：{notes['错误']}")
        else:
            self.log(f"截屏对象建好了（{notes['截法']}）：{self.camera.width}x{self.camera.height}"
                     "（和 Evo 截的是同一个显示器）")
        evo_dirs = _safe(find_evo_data_dirs)
        self.evo_dirs = evo_dirs if isinstance(evo_dirs, list) else []
        self.log(f"找到了 Evo：{self.evo_dirs[0][0]}（结束时带上 Evo 的日志）" if self.evo_dirs
                 else "没找到 Evo（没装，或者装了但没开着）：不影响，照样截图")
        self.db = db_path(self.evo_dirs)
        self.info = collect_info(dxcam_mod, self.camera, notes, self.evo_dirs, self.version)
        write_info(self.folder, self.info)  # 先写一份：万一中途被关掉，也有这些信息
        self._write_tester()

        thumbs = _safe(MonitorThumbs, self.folder, self.log)
        if isinstance(thumbs, dict):
            self.log(f"⚠ 缩略图截不了（不影响主要的截图）：{thumbs['错误']}")
            thumbs = None
        self.thumbs = thumbs
        options = {"thumbs": thumbs, "games": self.games, "beep": beep if self.sound else None}
        if self.minutes is not None:
            options["minutes"] = self.minutes
        weapons_cfg = aspect = None
        if self.project["capture"] == "weapons":
            weapons_cfg = load_config("weapons.json")
            aspect, why = game_aspect_guess(self.evo_dirs)
            self.log(f"游戏画面比例：{why}")
        self.recorder = make_recorder(self.project, self.folder, self.camera,
                                      lambda: {name for _, name in processes()}, self.log,
                                      weapons_cfg=weapons_cfg, aspect=aspect, **options)

    def run(self, stop):
        """截到结束（stop 是 threading.Event：玩家点了结束），返回结束的原因。"""
        if self.project.get("answers") == "matches":
            threading.Thread(target=self._watch, args=(stop,), daemon=True).start()
        try:
            self.reason = self.recorder.run(stop)
        except KeyboardInterrupt:
            self.reason = "按了 Ctrl+C"
        finally:
            self.ended = datetime.now()
            self._done.set()
            self.recorder.close()
            if self.camera is not None:
                self.camera.release()
            if self.thumbs is not None:
                self.thumbs.close()
            logging.getLogger("dxcam").removeHandler(self._dx_handler)
        rec = self.recorder
        self.log(f"结束（{self.reason}）：存了 {rec.saved} 张（{rec.mb:.0f} MB），内存峰值 {memory_mb()[1]:.0f} MB")
        if not rec.games_seen:
            self.log("⚠ 这次没看到游戏开着，截到的只是桌面")
        if rec.no_new_game:
            self.log(f"⚠ 游戏开着时有 {rec.no_new_game} 次没截到新画面（切出游戏、画面一动不动时也会这样；"
                     "一直在游戏里还这样就是截屏卡住了。会写进诊断信息）")
        return self.reason

    def _watch(self, stop):
        """打一局：录的时候每 WATCH_SECONDS 秒看一眼 Evo 记下了什么，该提醒就提醒（Reminder）。"""
        reminder = Reminder(self.project.get("remind_review", 2), self.project.get("remind_minutes", 60))
        began = last = time.monotonic()
        game_seconds = 0.0
        while not stop.wait(WATCH_SECONDS) and not self._done.is_set():
            now = time.monotonic()
            if self.recorder.game_on:
                game_seconds += now - last
            last = now
            matches = _safe(evo_matches, self.db, self.stamp, datetime.now()) if self.db else None
            for text in reminder.check(now - began, game_seconds, evo_running(),
                                       matches if isinstance(matches, list) else None, self.recorder.mb):
                self.notice(text)

    def evo_record(self):
        """这段时间 Evo 记下的（对答案用）：{"局": [...], "大厅": [...], "赛季": [...], "数据库": 路径}。读不到的是空的。"""
        end = self.ended or datetime.now()
        out = {"数据库": str(self.db) if self.db else None}
        for key, func in (("局", evo_matches), ("大厅", lobby_readings), ("赛季", season_rows)):
            value = _safe(func, self.db, self.stamp, end) if self.db else []
            out[key] = value if isinstance(value, list) else []
            if isinstance(value, dict):
                self.log(f"⚠ 读 Evo 的数据库出错：{value['错误']}")
        return out

    def _write_answers(self, answers):
        """answers.json：测试员确认的答案（打一局：先删掉确认对的局的截图）、Evo 这段时间记下的。"""
        kind = self.project.get("answers")
        record = self.evo_record()
        data = {"项目": self.project["key"], "Evo 数据库": record["数据库"]}
        if kind == "matches":
            logs = Path(self.evo_dirs[0][0]) / "logs" if self.evo_dirs else None
            events = log_events(logs, self.stamp, self.ended or datetime.now()) if logs else []
            result = apply_match_answers(self.folder, self.stamp, record["局"], events, answers or {})
            data.update(result)
            data["Evo 大厅读数"] = record["大厅"]
            shot = result["截图"]
            if not shot.get("全部带上"):
                self.log(f"确认对了的局删掉 {shot['确认对的局里删了']} 张截图，大厅删掉 {shot['大厅删了']} 张，"
                         f"留下 {shot['留下']} 张")
        elif kind == "season_stats":
            data["赛季"] = record["赛季"]
        else:
            return
        Path(self.folder, "answers.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                                                     encoding="utf-8")

    def _write_tester(self, extra=None):
        """tester.json：哪个项目、测试员档案、工具版本（导入时按它认是哪一项）。"""
        data = {"工具版本": self.version, "项目": self.project["key"], "项目名": self.project["name"],
                "测试员": self.profile, "开始": f"{self.stamp:%Y-%m-%d %H:%M:%S}", **(extra or {})}
        Path(self.folder, "tester.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def finish(self, note="", answers=None):
        """删掉确认对了的截图、写 answers.json（打一局、赛季数据页）、带上 Evo 的日志、写诊断信息和 tester.json、打包。
        answers：打一局时测试员确认的（见 tester_answers.apply_match_answers）。
        返回压缩包的位置；打包没成功返回 None（文件夹还在）。"""
        try:
            self._write_answers(answers)
        except Exception as e:  # 对答案出错也照样打包（截图都还在）
            self.log(f"⚠ 写答案出错了（截图都留着）：{type(e).__name__}: {e}")
        self.info["工具"]["结束原因"] = self.reason
        self.info["dxcam"]["警告"] = len(self.warnings)
        self.info["截屏结果"] = self.recorder.summary()
        self.info["Evo"]["日志"] = copy_evo_logs(self.evo_dirs, self.folder) if self.evo_dirs else 0
        write_info(self.folder, self.info)
        self._write_tester({"结束": f"{datetime.now():%Y-%m-%d %H:%M:%S}", "结束原因": self.reason, "备注": note})
        self.log("正在打包……")
        self._log_file.close()
        try:
            return make_zip(self.folder, self.out_root / f"{self.folder.name}.zip")
        except OSError as e:
            self._show(f"打包没成功（{e}）。截图都在这个文件夹里，可以自己压缩后发过来：\n  {self.folder}")
            return None


# ---------- 命令行（开发、打包自检用；玩家用的是窗口） ----------

class EnterKey:
    """玩家在本窗口按回车（读本窗口自己的输入，不是全局键盘钩子）。pressed：按过回车；alive：还读得到输入。"""

    def __init__(self):
        self.pressed = threading.Event()
        self.alive = sys.stdin is not None
        if self.alive:
            threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while sys.stdin.readline():
                self.pressed.set()
        except (OSError, ValueError):
            pass
        self.alive = False


def main(argv=None):
    projects = load_projects()
    parser = argparse.ArgumentParser(description="Evo 测试工具：截一段游戏画面，连同显示设置、Evo 的日志打成压缩包")
    parser.add_argument("--project", choices=list(projects), default="diag",
                        help="测试项目（config/tester.json；默认 diag = 排查问题）")
    parser.add_argument("--minutes", type=float, default=None, help="最多截几分钟（默认看测试项目）")
    parser.add_argument("--out", type=Path, default=None, help="压缩包放在哪个文件夹（默认桌面）")
    parser.add_argument("--no-wait", action="store_true", help="结束后不等回车、不打开文件夹、不出声（打包自检、测试用）")
    parser.add_argument("--game", action="append", default=None,
                        help="把这个进程也当作游戏（测试用，如 explorer.exe；可以写几次）")
    args = parser.parse_args(argv)

    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        ctypes.WinDLL("kernel32").SetConsoleTitleW(f"Evo 测试工具 {VERSION}")
    except (OSError, AttributeError):
        pass
    set_low_priority()
    project = projects[args.project]
    steps = "\n".join(f"  {i}. {step}" for i, step in enumerate(project["steps"], 1))
    print(f"Evo 测试工具 {VERSION}：{project['name']}\n{'=' * 60}\n{steps.replace('点“结束”', '按回车')}\n{'=' * 60}\n")

    session = Session(project, load_profile(), args.out or default_out_dir(), print, games=args.game or (),
                      minutes=args.minutes, sound=not args.no_wait)
    session.start()
    enter = EnterKey()
    session.log("开始了：现在去打游戏吧。打完回到这里按回车。\n")
    session.run(enter.pressed)
    zip_path = session.finish()
    if zip_path is not None:
        size = zip_path.stat().st_size / 2 ** 20
        print(f"\n{'=' * 60}\n截好了：{zip_path}（{size:.0f} MB）\n把这个压缩包发给 Evo 开发者就行。\n{'=' * 60}")
        if not args.no_wait:
            subprocess.Popen(f'explorer /select,"{zip_path}"')  # 打开文件夹、选中压缩包（explorer 要这种引号写法）
    if not args.no_wait and enter.alive:
        print("\n按回车关闭这个窗口。")
        enter.pressed.clear()
        enter.pressed.wait()
    return zip_path


def window_main():
    """玩家双击打开：窗口（tester_window.py）。"""
    from tester_window import icon_path, run

    out_root = default_out_dir()
    set_low_priority()
    run(load_projects(), load_profile(), languages(), weapon_list(), save_profile,
        lambda project, profile, log, notice: Session(project, profile, out_root, log, notice=notice),
        evo_running, VERSION, icon_path())


if __name__ == "__main__":
    if len(sys.argv) > 1:  # 带参数：命令行（开发、打包自检、测试）
        main()
    else:
        window_main()
