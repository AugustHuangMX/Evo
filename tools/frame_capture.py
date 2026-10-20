"""截整屏样本（开发工具）：适配新分辨率、新画面时用。打游戏时每隔 1 秒截一张整屏存下来，事后再挑。

Evo 只认得出已经校准过的画面，没校准的分辨率（如 16:10、4:3）它一张都不会存，所以要用这个工具截。
截屏方式和 Evo 一样（dxcam），截到的就是 Evo 看到的样子，包括分辨率。
- 画面和上一张存的几乎一样（比如停在菜单里）就不存，省空间
- 画面尺寸变了（游戏切分辨率、全屏和桌面来回切）会在终端里提示，文件名里也带着尺寸
- 默认存 JPEG（质量 95、不压缩颜色，约 1 MB 一张）；--png 存无损的 PNG（约 4 MB 一张）
- 存在 captures/frames/<开始时间>/，文件名如 0001_204512_1920x1200.jpg（第几张_时分秒_尺寸）

用法：
1. 运行 python tools/frame_capture.py，然后进游戏
2. 正常打一局；每个画面多停几秒（大厅、总结页、经验值页等动画播完）
3. 打完回到终端按 Ctrl+C 停止（或到达 --minutes 设定的时长后自动停止）

本脚本只做被动截屏：不碰游戏进程，不模拟按键，不注册键盘钩子。
"""

import argparse
import time
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import cv2
import dxcam
import numpy as np

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
from paths import CAPTURES_DIR
from process_info import set_low_priority

SAME = 1.5  # 和上一张存的相比，缩略图平均每个像素差不到这么多（0–255），算"没变"，不存
THUMB_SHRINK = 16  # 比较前缩小到 1/16，不在乎细节和噪点


def aspect(w, h):
    """宽高比，如 "16:9"、"16:10"、"4:3"。"""
    r = Fraction(w, h).limit_denominator(20)
    return "16:10" if r == Fraction(8, 5) else f"{r.numerator}:{r.denominator}"


def thumb(frame):
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (max(1, g.shape[1] // THUMB_SHRINK), max(1, g.shape[0] // THUMB_SHRINK)),
                      interpolation=cv2.INTER_AREA).astype(np.float32)


def main():
    parser = argparse.ArgumentParser(description="打游戏时每隔几秒截一张整屏（适配新分辨率用）")
    parser.add_argument("--interval", type=float, default=1, help="每隔几秒截一张（默认 1）")
    parser.add_argument("--minutes", type=float, default=60, help="最多运行几分钟，到时自动停止（默认 60）")
    parser.add_argument("--png", action="store_true", help="存无损的 PNG（默认 JPEG）")
    parser.add_argument("--out", type=Path, default=None, help="存到哪个文件夹（默认 captures/frames/<开始时间>）")
    args = parser.parse_args()

    set_low_priority()
    out = args.out or CAPTURES_DIR / "frames" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    ext, params = (".png", [cv2.IMWRITE_PNG_COMPRESSION, 1]) if args.png else \
        (".jpg", [cv2.IMWRITE_JPEG_QUALITY, 95, cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444])
    camera = dxcam.create(output_color="BGR", processor_backend="numpy")
    print(f"保存到: {out}")
    print("按 Ctrl+C 停止。\n")

    size, last, saved, skipped, nbytes = None, None, 0, 0, 0
    deadline = time.monotonic() + args.minutes * 60
    next_report = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            tick = time.monotonic()
            try:
                frame = camera.grab(new_frame_only=False)
            except Exception as e:  # 切换分辨率、切出游戏等情况，跳过这一次
                print(f"[{datetime.now():%H:%M:%S}] 截屏出错，跳过：{e}")
                frame = None
            if frame is not None:
                h, w = frame.shape[:2]
                if (w, h) != size:
                    note = "" if size else "。刚开始截到的一般是桌面；游戏切到全屏后，这里会再显示一行游戏的尺寸"
                    size, last = (w, h), None
                    print(f"[{datetime.now():%H:%M:%S}] 截到的画面：{w}x{h}（{aspect(w, h)}）{note}")
                t = thumb(frame)
                if last is not None and float(np.mean(np.abs(t - last))) < SAME:
                    skipped += 1
                else:
                    last = t
                    saved += 1
                    data = cv2.imencode(ext, frame, params)[1]
                    data.tofile(out / f"{saved:04d}_{datetime.now():%H%M%S}_{w}x{h}{ext}")
                    nbytes += data.size
            if tick >= next_report:
                next_report = tick + 60
                print(f"[{datetime.now():%H:%M:%S}] 已存 {saved} 张（{nbytes / 2 ** 20:.0f} MB），"
                      f"没变化跳过 {skipped} 张")
            time.sleep(max(0, args.interval - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        print("已手动停止。")
    finally:
        camera.release()
    print(f"\n一共存了 {saved} 张（{nbytes / 2 ** 20:.0f} MB），在 {out}")


if __name__ == "__main__":
    main()
