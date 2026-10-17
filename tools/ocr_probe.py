"""OCR 探测：用 RapidOCR 读一张截图，把读到的所有文字、位置、置信度打印出来。

用途：看看 OCR 在游戏截图上读得准不准，并找出每个数字在屏幕上的位置（以后写进识别配置）。
顺便测量模型加载耗时、识别耗时和内存占用。

运行：python tools/ocr_probe.py <截图路径>
例如：python tools/ocr_probe.py captures/samples/vlcsnap-2026-10-16-18h26m12s173.png

全程不联网：直接指定安装包里自带的模型文件，RapidOCR 的下载代码不会被执行。
"""

import argparse
import time
from pathlib import Path

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
from ocr import create_engine
from process_info import memory_mb

LOW_SCORE = 0.9  # 置信度低于这个值的结果，在表格里标出来


def to_rect(box):
    """OCR 给的是四个角的坐标，换算成 (x, y, 宽, 高)，方便以后写进配置。"""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    x, y = int(min(xs)), int(min(ys))
    return x, y, int(max(xs)) - x, int(max(ys)) - y


def main():
    parser = argparse.ArgumentParser(description="用 RapidOCR 读一张截图并打印所有结果")
    parser.add_argument("image", type=Path, help="截图路径")
    args = parser.parse_args()

    mem_start, _ = memory_mb()
    engine = create_engine()

    # 第一次识别会顺带加载模型，所以单独计时；第二次才是纯识别耗时
    start = time.perf_counter()
    result = engine(str(args.image))
    first_s = time.perf_counter() - start
    start = time.perf_counter()
    engine(str(args.image))
    second_s = time.perf_counter() - start
    mem_now, mem_peak = memory_mb()

    if result.txts is None:
        print("没有识别到任何文字。")
        return

    # 按从上到下、从左到右排序：先按行（y 每 20 像素算一行），再按 x
    rows = [(to_rect(box), txt, score) for box, txt, score in zip(result.boxes, result.txts, result.scores)]
    rows.sort(key=lambda r: (r[0][1] // 20, r[0][0]))

    print(f"图片: {args.image}  尺寸: {result.img.shape[1]}x{result.img.shape[0]}")
    print(f"共识别到 {len(rows)} 段文字。置信度低于 {LOW_SCORE} 的标了 *\n")
    print(f"{'序号':>4}  {'x':>5} {'y':>5} {'宽':>5} {'高':>5}  {'置信度':>6}  文字")
    for i, ((x, y, w, h), txt, score) in enumerate(rows, 1):
        flag = "*" if score < LOW_SCORE else " "
        print(f"{i:>4}  {x:>5} {y:>5} {w:>5} {h:>5}  {score:>6.3f}{flag} {txt}")

    print("\n===== 性能 =====")
    print(f"第一次（含加载模型）: {first_s:.2f} 秒")
    print(f"第二次（纯识别整张图）: {second_s:.2f} 秒")
    print(f"内存: 启动时 {mem_start:.0f} MB，现在 {mem_now:.0f} MB，峰值 {mem_peak:.0f} MB")


if __name__ == "__main__":
    main()
