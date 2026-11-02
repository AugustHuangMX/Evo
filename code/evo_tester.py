"""Evo 测试工具（2026-11-02 起，原来的诊断工具）：帮 Evo 测试、适配新分辨率和语言的玩家，和记不下战绩的玩家，
截一段游戏画面，连同电脑的显示设置、Evo 的日志打成一个压缩包，发给开发者。

和 Evo 分开打包成一个 exe（python tools/build_tester.py），玩家下载后双击运行。
- 截屏、打包见 tester_capture.py；显示器、Apex 设置、Evo 的日志见 tester_info.py
- 结束：玩家在本窗口按回车、游戏关掉、截满时间或大小、硬盘快满。压缩包放在桌面，打开文件夹选中它
- 只看不动（原则 1）：只截屏、只读进程名和 Windows 的显示设置；按回车用的是本窗口自己的输入，不注册全局键盘钩子；
  只找 Evo.exe 自己在哪个文件夹，不打开游戏进程；不联网，压缩包发不发由玩家决定

不 import paths.py（它一 import 就会在程序旁边建"Evo Tracker Data"文件夹）。

开发时运行：python code/evo_tester.py [--minutes 5] [--out 文件夹]
看玩家发来的压缩包：python tools/diag_report.py Evo诊断_xxx.zip
"""

import argparse
import ctypes
import logging
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from game_detect import GAME_PROCESSES, processes
from process_info import memory_mb, set_low_priority
from tester_capture import MAX_MINUTES, MonitorThumbs, Recorder, default_out_dir, make_zip, open_camera
from tester_info import _safe, collect_info, copy_evo_logs, find_evo_data_dirs, write_info

VERSION = "1.0"


# ---------- 窗口 ----------

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


INTRO = f"""Evo 诊断工具 {VERSION}
============================================================
用 Evo 使用出现问题时，请使用这个工具截一段游戏画面，发给 Evo 开发者帮你排查原因。

使用方法：
  1. 打开 Apex（已经开着也行）。
  2. 正常打一局：大厅、开局、打完后的总结页、经验值页（要手动切过去）多停几秒
  3. 打完回到这个窗口按回车（关掉游戏也会自动结束）。务必不要直接点右上角关掉窗口
  4. 桌面上会出现一个压缩包，直接把它发给 Evo 开发者

另外诊断程序也会记下：显示器和显卡的信息、HDR 开没开、Apex 的分辨率设置，以及电脑上 Evo 的日志。
============================================================
"""  # 2026-10-24 用户定的说法


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evo 诊断工具：截一段游戏画面，连同显示设置、Evo 的日志打成压缩包")
    parser.add_argument("--minutes", type=float, default=MAX_MINUTES, help=f"最多截几分钟（默认 {MAX_MINUTES}）")
    parser.add_argument("--out", type=Path, default=None, help="压缩包放在哪个文件夹（默认桌面）")
    parser.add_argument("--no-wait", action="store_true", help="结束后不等回车、不打开文件夹（打包自检、测试用）")
    parser.add_argument("--game", action="append", default=None,
                        help="把这个进程也当作游戏（测试用，如 explorer.exe；可以写几次）")
    args = parser.parse_args(argv)

    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        ctypes.WinDLL("kernel32").SetConsoleTitleW(f"Evo 诊断工具 {VERSION}")
    except (OSError, AttributeError):
        pass
    set_low_priority()
    print(INTRO)

    stamp = datetime.now()
    out_root = args.out or default_out_dir()
    folder = Path(out_root) / f"Evo诊断_{stamp:%Y%m%d_%H%M%S}"
    folder.mkdir(parents=True, exist_ok=True)
    log_file = open(folder / "tool_log.txt", "a", encoding="utf-8-sig")

    def log(text):
        print(text)
        log_file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
        log_file.flush()

    warnings = []

    class DxcamWarnings(logging.Handler):  # dxcam 自己的警告（截屏卡住、显示器变了时会打）：Evo 的日志里没有
        def emit(self, record):
            warnings.append(record)
            log(f"[{datetime.now():%H:%M:%S}] dxcam 警告：{record.getMessage()}")

    dx_logger = logging.getLogger("dxcam")
    dx_logger.addHandler(DxcamWarnings(logging.WARNING))
    dx_logger.setLevel(logging.WARNING)

    log(f"Evo 诊断工具 {VERSION} 开始，先存在：{folder}")
    dxcam_mod, camera, camera_error = open_camera()
    if camera_error:
        log(f"⚠ {camera_error}")
    else:
        log(f"截屏对象建好了：{camera.width}x{camera.height}（和 Evo 截的是同一个显示器）")
    evo_dirs = _safe(find_evo_data_dirs)
    evo_dirs = evo_dirs if isinstance(evo_dirs, list) else []
    log(f"找到了 Evo：{evo_dirs[0][0]}（结束时带上 Evo 的日志）" if evo_dirs
        else "没找到 Evo（没装，或者装了但没开着）：不影响，照样截图")
    info = collect_info(dxcam_mod, camera, camera_error, evo_dirs, VERSION)
    write_info(folder, info)  # 先写一份：万一中途被关掉，也有这些信息

    thumbs = _safe(MonitorThumbs, folder, log)
    if isinstance(thumbs, dict):
        log(f"⚠ 缩略图截不了（不影响主要的截图）：{thumbs['错误']}")
        thumbs = None
    games = GAME_PROCESSES | {g.lower() for g in (args.game or [])}
    recorder = Recorder(folder, camera, lambda: {name for _, name in processes()}, log, thumbs=thumbs,
                        minutes=args.minutes, games=games)
    enter = EnterKey()
    log("开始了：现在去打游戏吧。打完回到这里按回车。\n")
    try:
        reason = recorder.run(enter.pressed)
    except KeyboardInterrupt:
        reason = "按了 Ctrl+C"
    finally:
        recorder.close()
        if camera is not None:
            camera.release()
        if thumbs is not None:
            thumbs.close()

    print()
    log(f"结束（{reason}）：存了 {recorder.saved} 张（{recorder.mb:.0f} MB），内存峰值 {memory_mb()[1]:.0f} MB")
    if not recorder.games_seen:
        log("⚠ 这次没看到游戏开着，截到的只是桌面。要查 Evo 的问题，请开着游戏再来一次")
    if recorder.no_new_game:
        log(f"⚠ 游戏开着时有 {recorder.no_new_game} 次没截到新画面（切出游戏、画面一动不动时也会这样；"
            "一直在游戏里还这样就是截屏卡住了。会写进诊断信息）")
    info["工具"]["结束原因"] = reason
    info["dxcam"]["警告"] = len(warnings)
    info["截屏结果"] = recorder.summary()
    info["Evo"]["日志"] = copy_evo_logs(evo_dirs, folder) if evo_dirs else 0
    write_info(folder, info)
    log("正在打包……")
    log_file.close()
    try:
        zip_path = make_zip(folder, Path(out_root) / f"{folder.name}.zip")
    except OSError as e:
        print(f"\n打包没成功（{e}）。截图都在这个文件夹里，可以自己压缩后发过来：\n  {folder}")
        zip_path = None
    if zip_path is not None:
        size = zip_path.stat().st_size / 2 ** 20
        print("\n" + "=" * 60)
        print(f"截好了：{zip_path}（{size:.0f} MB）")
        print("把这个压缩包发给 Evo 开发者就行。")
        print("=" * 60)
        if not args.no_wait:
            subprocess.Popen(f'explorer /select,"{zip_path}"')  # 打开文件夹、选中压缩包（explorer 要这种引号写法）
    if not args.no_wait and enter.alive:
        print("\n按回车关闭这个窗口。")
        enter.pressed.clear()
        enter.pressed.wait()
    return zip_path


if __name__ == "__main__":
    main()
