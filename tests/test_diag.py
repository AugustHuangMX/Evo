"""诊断工具（code/evo_diag.py，打包成 Evo诊断工具.exe 给玩家用）和看压缩包的脚本（tools/diag_report.py）。

- 轻：另开一个 Python 只导入 evo_diag，检查没加载 paths.py（它一 import 就会在程序旁边建数据文件夹）、识别引擎、OpenCV
- 截屏（假摄像头、假时钟，不真的等）：游戏没开时每 10 秒看一眼、每分钟最多存一张；看到游戏后每 2 秒一张；画面没变不存；
  比 1920 宽的缩到 1920 宽，每分钟另存一张原尺寸；没有新画面、出错都记下，连续 30 次没有新画面提示一次；
  游戏关掉（连续 3 次看不到进程）就停；到时间、截满、硬盘快满、按回车也停；每次截屏 captures.csv 一行；每分钟一次缩略图
- 找 Evo 的数据文件夹：Evo.exe 旁边的 data_location.txt / 程序旁边的 Evo Tracker Data、指路文件；
  旧的默认位置别处都没有才用；没有 logs\\evo.log 的不算；同一个只算一次
- 诊断信息：这台电脑上真的读显示器（有主显示器、HDR 一栏）；Apex 视频设置只取四项；Evo 的设置只带三项；info.txt 各节都在
- 打包：压缩包里的文件和文件夹一样、JPEG 不再压缩、打完删掉文件夹；Evo 的日志带上
- 真的截几秒（dxcam，把 explorer.exe 当作游戏）：压缩包里有截图和诊断信息
- diag_report：用真实截图（简体 16:9、英文 16:10 拉伸，按工具的做法缩到 1920 宽）做压缩包，认得出各画面、传奇和游戏语言
  （截图找不到就跳过）

运行：python tests/test_diag.py
"""

import csv
import json
import subprocess
import sys
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from fake_game import CODE, ROOT, SHOTS, MissingShots, load, report, shot  # 要在项目代码之前导入

import numpy as np
from PIL import Image

import evo_diag
import game_aspect

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
    rec = evo_diag.Recorder(folder, camera, names, logs.append, clock=clock.monotonic, wait=clock.wait, now=clock.now,
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
    cases = {"按了回车": dict(stop=True), "截满了 1 分钟": dict(minutes=1),
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
    found = evo_diag.find_evo_data_dirs(exes=exes, local_dir=local)
    only_legacy = evo_diag.find_evo_data_dirs(exes=[], local_dir=lonely)
    none = evo_diag.find_evo_data_dirs(exes=[(empty / "x" / "Evo.exe", "开机自启")], local_dir=empty)
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
    apex = evo_diag.apex_video()
    video.unlink()
    settings_dir = tmp / "settings"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(json.dumps({"game_language": "auto", "game_aspect": "16:10",
                                                            "keep_all_screenshots": False, "cloud_device": "秘密",
                                                            "telemetry_id": "x"}), encoding="utf-8")
    picked = evo_diag.evo_settings(settings_dir)
    info = evo_diag.collect_info(None, None, "测试时不建截屏对象", [(settings_dir, "开机自启")])
    mons = info["显示器"]
    text = evo_diag.render_info(info)
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
    (folder / "frames" / "0001.jpg").write_bytes(evo_diag.encode_jpeg(np.zeros((1080, 1920, 3), np.uint8)))
    (folder / "info.txt").write_text("信息\n" * 100, encoding="utf-8")
    logs = tmp / "pack" / "evo" / "logs"
    logs.mkdir(parents=True)
    for name in ("evo.log", "evo.log.1"):
        (logs / name).write_text("Evo 0.3.8 启动\n", encoding="utf-8")
    copied = evo_diag.copy_evo_logs([(logs.parent, "开机自启")], folder)
    zip_path = evo_diag.make_zip(folder, tmp / "pack" / "Evo诊断_x.zip")
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
    code = ("import sys, evo_diag; print(','.join(m for m in ('paths', 'app_settings', 'cv2', 'rapidocr', 'onnxruntime')"
            " if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=CODE, capture_output=True, text=True, check=True)
    loaded = [m for m in out.stdout.strip().split(",") if m]
    return {f"只导入 evo_diag 不加载 paths.py、识别引擎、OpenCV（加载了：{'、'.join(loaded) or '无'}）": not loaded}


def real_checks(tmp):
    """真的截几秒：dxcam 截主显示器，把 explorer.exe 当作游戏（一定开着）。"""
    out = tmp / "real"
    result = subprocess.run([sys.executable, str(CODE / "evo_diag.py"), "--minutes", "0.12", "--game", "explorer.exe",
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
    return {
        f"真的截几秒：压缩包里有截图（{len(frames)} 张）、诊断信息、截屏记录、工具日志":
            frames and all(n in names for n in ("info.json", "info.txt", "captures.csv", "tool_log.txt")),
        f"截屏对象和 Evo 一样建好了（{info['dxcam']['截屏对象']}）": info["dxcam"]["截屏对象"] == "建好了",
        "没留下文件夹": [p for p in out.iterdir() if p.is_dir()] == [],
    }


def fake_zip(tmp, name, images, video=None):
    """按工具的做法（缩到 1920 宽）把几张真实截图做成一个诊断压缩包。"""
    folder = tmp / name
    (folder / "frames").mkdir(parents=True)
    rows = []
    for i, img in enumerate(images, 1):
        h, w = img.shape[:2]
        file = f"frames/{i:04d}_150000_{w}x{h}.jpg"
        (folder / file).write_bytes(evo_diag.encode_jpeg(img, evo_diag.SMALL_WIDTH))
        rows.append({"序号": i, "时间": "15:00:00", "游戏开着": "是", "结果": "存了", "宽": w, "高": h, "文件": file})
    with open(folder / "captures.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, evo_diag.CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    info = evo_diag.collect_info(None, None, "测试", [])
    info["Apex"]["视频设置"] = video or "没找到视频设置文件"
    info["截屏结果"] = {"看到的游戏进程": {GAME: "15:00:00"}}
    Path(folder, "info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    Path(folder, "info.txt").write_text("测试\n", encoding="utf-8-sig")
    return evo_diag.make_zip(folder, tmp / f"{name}.zip")


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
    print("诊断工具（截屏、找 Evo、诊断信息、打包、看压缩包）")
    with tempfile.TemporaryDirectory(prefix="evo-diag-") as name:
        tmp = Path(name)
        checks = {**light_checks(), **recorder_checks(tmp), **stop_checks(tmp), **finder_checks(tmp),
                  **info_checks(tmp), **zip_checks(tmp), **real_checks(tmp)}
        ok = report(checks)
        shots = report_checks(tmp)
        if shots is not None:
            ok = report(shots) and ok
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
