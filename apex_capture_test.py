"""截屏兼容性测试：玩 Apex 时，每隔几秒分别用 dxcam 和 mss 各截一张屏幕。

检查三件事：能不能截到（有没有报错）、是不是黑屏、每次截图耗时多少。
结果写进 results.csv（可以直接用 Stata 的 import delimited 打开），并定期保存 PNG 样本。

运行：python apex_capture_test.py
停止：在终端按 Ctrl+C，或到达 --minutes 设定的时长后自动停止

本脚本只做被动截屏：不碰游戏进程，不模拟按键，不注册键盘钩子。
"""

import argparse
import csv
import ctypes
import platform
import sys
import time
from ctypes import wintypes
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import dxcam
import mss
import mss.tools
import numpy as np

METHODS = ("dxcam", "mss")
STATUSES = ("ok", "black", "none", "error")

# 平均亮度和亮度波动（标准差）都低于这两个值，就判定为黑屏。像素值范围是 0–255
BLACK_MEAN = 10
BLACK_STD = 5

CSV_COLUMNS = ["time", "round", "method", "status", "ms", "width", "height", "mean", "std", "file", "error"]


def parse_args():
    parser = argparse.ArgumentParser(description="截屏兼容性测试（dxcam 和 mss 对比）")
    parser.add_argument("--interval", type=float, default=5, help="每隔几秒测一轮（默认 5）")
    parser.add_argument("--save-every", type=int, default=6, help="每几轮保存一次 PNG（默认 6，约 30 秒一次）")
    parser.add_argument("--minutes", type=float, default=30, help="最多运行几分钟，到时自动停止（默认 30）")
    parser.add_argument("--out", type=Path, default=Path("captures/capture_test"), help="结果保存到哪个文件夹")
    return parser.parse_args()


def set_low_priority():
    """把本程序设为"低于正常"优先级，CPU 紧张时让游戏优先。"""
    BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
    kernel32 = ctypes.windll.kernel32
    # 明确声明参数类型：别的代码（如 ocr_probe.memory_mb）也会设置 GetCurrentProcess 的返回类型，不声明会出错
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)


def primary_monitor(sct):
    """mss 里的主显示器：左上角坐标是 (0, 0) 的那个。dxcam 默认也截主显示器。"""
    for mon in sct.monitors[1:]:
        if mon["left"] == 0 and mon["top"] == 0:
            return mon
    return sct.monitors[1]


def capture(method, camera, sct, monitor):
    """用指定方式截一张图，返回 RGB 格式的 numpy 数组（高 × 宽 × 3）。没有画面时返回 None。"""
    if method == "dxcam":
        # new_frame_only=False：画面没变化时返回上一张，而不是 None
        return camera.grab(new_frame_only=False)
    shot = sct.grab(monitor)
    return np.asarray(shot)[:, :, 2::-1]  # mss 给的是 BGRA，取前三个通道并倒序 → RGB


def brightness(pixels):
    """每隔 8 个像素取一个点，算平均亮度和波动。判断黑屏足够了，而且很快。"""
    sample = pixels[::8, ::8]
    return float(sample.mean()), float(sample.std())


def test_once(method, camera, sct, monitor, run_dir, round_no, save):
    """用一种方式截一张图并检查，返回 CSV 里的一行（字典）。"""
    row = dict.fromkeys(CSV_COLUMNS, "")
    row.update(time=datetime.now().strftime("%H:%M:%S"), round=round_no, method=method)

    if method == "dxcam" and camera is None:
        row.update(status="error", error="dxcam 初始化失败，见 info.txt")
        return row

    try:
        start = time.perf_counter()
        pixels = capture(method, camera, sct, monitor)
        row["ms"] = round((time.perf_counter() - start) * 1000, 1)
    except Exception as e:
        row.update(status="error", error=f"{type(e).__name__}: {e}")
        return row

    if pixels is None:
        row["status"] = "none"
        return row

    height, width = pixels.shape[:2]
    mean, std = brightness(pixels)
    row.update(width=width, height=height, mean=round(mean, 1), std=round(std, 1))
    row["status"] = "black" if mean < BLACK_MEAN and std < BLACK_STD else "ok"

    # 定期存样本；黑屏的图每次都存，方便事后检查（黑图压缩后只有几 KB）
    if save or row["status"] == "black":
        row["file"] = f"r{round_no:04d}_{method}.png"
        mss.tools.to_png(pixels.tobytes(), (width, height), output=run_dir / row["file"])
    return row


def write_info(run_dir, args, sct, dxcam_error):
    lines = [
        f"开始时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"系统: {platform.platform()}",
        f"Python: {sys.version.split()[0]}  dxcam: {version('dxcam')}  mss: {version('mss')}",
        f"设置: 每 {args.interval} 秒一轮，每 {args.save_every} 轮存一次图，最多 {args.minutes} 分钟",
        "",
        "显卡（dxcam）:",
        dxcam.device_info().strip(),
        "显示器（dxcam）:",
        dxcam.output_info().strip(),
        "显示器（mss，第 0 项是所有屏幕合起来）:",
        *[f"  {i}: {mon}" for i, mon in enumerate(sct.monitors)],
    ]
    if dxcam_error:
        lines += ["", f"dxcam 初始化失败: {dxcam_error}"]
    lines += [
        "",
        "请手动补充（测试完填在这里）：",
        "  游戏显示模式（全屏 / 无边框窗口）：",
        "  游戏分辨率：",
        "  游戏界面语言：",
        "  Windows HDR（开 / 关）：",
        "  测试场景（训练场 / 匹配 / 排位）：",
        "  Javelin 有没有弹出提示或踢出游戏：",
    ]
    text = "\n".join(lines)
    (run_dir / "info.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


def print_summary(stats, run_dir):
    print("\n===== 测试结果 =====")
    print(f"{'方式':<8}{'成功':>6}{'黑屏':>6}{'无画面':>6}{'出错':>6}{'平均耗时':>10}{'最长耗时':>10}")
    for method in METHODS:
        s = stats[method]
        times = s["ms"]
        avg = f"{sum(times) / len(times):.1f}ms" if times else "-"
        worst = f"{max(times):.1f}ms" if times else "-"
        print(f"{method:<8}{s['ok']:>6}{s['black']:>6}{s['none']:>6}{s['error']:>6}{avg:>12}{worst:>12}")
    print(f"\n结果保存在: {run_dir.resolve()}")
    print("别忘了打开 info.txt，把最后几项手动补充完整。")


def main():
    args = parse_args()
    set_low_priority()

    run_dir = args.out / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    sct = mss.MSS()
    monitor = primary_monitor(sct)
    camera, dxcam_error = None, ""
    try:
        # processor_backend="numpy"：用 dxcam 自带的转换，不需要安装 OpenCV
        camera = dxcam.create(output_color="RGB", processor_backend="numpy")
    except Exception as e:
        dxcam_error = f"{type(e).__name__}: {e}"

    write_info(run_dir, args, sct, dxcam_error)
    print(f"\n开始测试：每 {args.interval} 秒一轮，按 Ctrl+C 停止。\n")

    stats = {m: {**dict.fromkeys(STATUSES, 0), "ms": []} for m in METHODS}
    deadline = time.monotonic() + args.minutes * 60
    round_no = 0

    with open(run_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        try:
            while time.monotonic() < deadline:
                round_no += 1
                round_start = time.monotonic()
                save = (round_no - 1) % args.save_every == 0  # 第 1 轮就存，之后每 save_every 轮存一次

                parts = []
                for method in METHODS:
                    row = test_once(method, camera, sct, monitor, run_dir, round_no, save)
                    writer.writerow(row)
                    stats[method][row["status"]] += 1
                    if row["ms"] != "":
                        stats[method]["ms"].append(row["ms"])
                    detail = f"{row['ms']}ms" if row["status"] != "error" else row["error"]
                    parts.append(f"{method}: {row['status']} {detail}")
                f.flush()  # 每轮立刻写入硬盘，中途停止也不会丢数据

                saved = "  [已存图]" if save else ""
                print(f"第 {round_no:>3} 轮 {datetime.now():%H:%M:%S} | " + " | ".join(parts) + saved)

                time.sleep(max(0, args.interval - (time.monotonic() - round_start)))
        except KeyboardInterrupt:
            print("\n已手动停止。")
        finally:
            if camera is not None:
                camera.release()
            sct.close()

    print_summary(stats, run_dir)


if __name__ == "__main__":
    main()
