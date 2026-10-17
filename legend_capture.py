"""靶场截传奇头像（开发工具）：在靶场里逐个换传奇，自动存下对局中左下角自己的头像，用来做传奇识别的参照图。

每 0.5 秒截一次屏（只截屏，不碰游戏），看左下角头像那一小块：
- 头像那块稳定了约 2 秒（连续几次几乎不变），并且和已经存过的每一张都不一样，就存一次，同时"嘀"一声
- 每次存两样：左下角一大块（legend_01.png，做参照图用）和整张截图（full_01.jpg：万一头像位置量得不准，
  可以重新裁，不用再去一趟靶场）。存在 captures/legends/<开始时间>/
- 打开菜单、选传奇的画面也可能被存下来，没关系，整理参照图时会挑掉

用法：
1. 运行 python legend_capture.py，然后进游戏、去靶场
2. 每换一个传奇，站着别动等 2–3 秒，听到"嘀"一声就是存好了，再换下一个
3. 全部换完，回到终端按 Ctrl+C 停止（或到达 --minutes 设定的时长后自动停止）

本脚本只做被动截屏：不碰游戏进程，不模拟按键，不注册键盘钩子。
"""

import argparse
import time
import winsound
from datetime import datetime

import cv2
import dxcam
import numpy as np

from paths import CAPTURES_DIR
from process_info import set_low_priority
from screens import gray_crop

# 坐标按 1920×1080 写，其他分辨率按比例换算（用 10-16 录像里观战画面的左下角量的）。
# 正式做传奇识别时，这些会挪到 config/ 里
BASE = [1920, 1080]
SAVE_BOX = [0, 820, 480, 180]  # 存下来的一大块：头像、名字、护甲和血条、护甲等级
PORTRAIT_BOX = [95, 893, 50, 60]  # 只看头像中间的脸，判断换没换传奇（避开左边的斜边和右下角的小图标）
THUMB = (25, 30)  # 比较前缩成这么小，不在乎细节和压缩噪点
STABLE = 6  # 和上一次相比，平均每个像素差不到这么多（0–255），算"没在变"
NEW = 15  # 和已存的每一张相比都差这么多以上，算"新头像"
STEADY_CHECKS = 4  # 连续这么多次没在变（约 2 秒）才存，避免存到切换中途的画面


def diff(a, b):
    """两张缩略图平均每个像素差多少（0–255）。"""
    return float(np.mean(np.abs(a - b)))


def native_crop(frame, box):
    """按基准坐标裁一块，保持截图原来的分辨率（不缩放），留着做参照图。"""
    h, w = frame.shape[:2]
    sx, sy = w / BASE[0], h / BASE[1]
    x, y, bw, bh = box
    return frame[round(y * sy) : round((y + bh) * sy), round(x * sx) : round((x + bw) * sx)]


class PortraitWatcher:
    """update(画面) 返回 True 表示这一帧该存：头像那块稳定了一会儿，并且和已存的都不一样。"""

    def __init__(self):
        self.saved = []  # 已存头像的缩略图
        self.last = None  # 上一帧的缩略图
        self.steady = 0  # 连续几次没在变
        self.nearest = None  # 刚存的这张和之前存过的最像的一张差多少（第一张是 None）

    def update(self, frame):
        thumb = cv2.resize(gray_crop(frame, PORTRAIT_BOX, BASE), THUMB, interpolation=cv2.INTER_AREA)
        thumb = thumb.astype(np.float32)
        still = self.last is not None and diff(thumb, self.last) < STABLE
        self.steady = self.steady + 1 if still else 0
        self.last = thumb
        if self.steady < STEADY_CHECKS:
            return False
        nearest = min((diff(thumb, s) for s in self.saved), default=None)
        if nearest is not None and nearest < NEW:
            return False
        self.saved.append(thumb)
        self.nearest = nearest
        return True


def save(path, img, ext, params=()):
    cv2.imencode(ext, img, list(params))[1].tofile(path)


def main():
    parser = argparse.ArgumentParser(description="靶场里逐个换传奇，自动存下左下角自己的头像")
    parser.add_argument("--interval", type=float, default=0.5, help="每隔几秒检查一次（默认 0.5）")
    parser.add_argument("--minutes", type=float, default=30, help="最多运行几分钟，到时自动停止（默认 30）")
    args = parser.parse_args()

    set_low_priority()
    out = CAPTURES_DIR / "legends" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    camera = dxcam.create(output_color="BGR", processor_backend="numpy")
    watcher = PortraitWatcher()
    print(f"屏幕: {camera.width}x{camera.height}")
    print(f"保存到: {out}")
    print("每换一个传奇，站着别动等 2–3 秒，听到“嘀”一声就是存好了。按 Ctrl+C 停止。\n")

    deadline = time.monotonic() + args.minutes * 60
    try:
        while time.monotonic() < deadline:
            tick = time.monotonic()
            try:
                frame = camera.grab(new_frame_only=False)
            except Exception as e:  # 切换分辨率、切出游戏等情况，跳过这一次
                print(f"[{datetime.now():%H:%M:%S}] 截屏出错，跳过：{e}")
                frame = None
            if frame is not None and watcher.update(frame):
                n = len(watcher.saved)
                save(out / f"legend_{n:02d}.png", native_crop(frame, SAVE_BOX), ".png")
                save(out / f"full_{n:02d}.jpg", frame, ".jpg", [cv2.IMWRITE_JPEG_QUALITY, 90])
                note = "" if watcher.nearest is None else f"（和之前存的最像的一张差 {watcher.nearest:.0f}）"
                print(f"[{datetime.now():%H:%M:%S}] 已存第 {n} 张{note}")
                winsound.Beep(1200, 150)
            time.sleep(max(0, args.interval - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        print("已手动停止。")
    finally:
        camera.release()
    print(f"\n一共存了 {len(watcher.saved)} 张，在 {out}")


if __name__ == "__main__":
    main()
