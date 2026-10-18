"""淘汰画面：小队被淘汰时屏幕中间的"排名第 X"。认得出、读得对、不会把别的画面当成它；记录程序读到名次写进日志。

用的是 2 张 1080p 录像截图（一张还有"全军覆没"那行，另一张那行已经消失、"排名第"三个字大了约 6%），
把第一张整张放大、缩小（模拟字再大一些或小一些的时候），外加 captures/ 里其他所有截图（总结页、经验值页、开局画面、大厅、观战等）当作"不是淘汰画面"的例子。
截图不在仓库里，见 fake_game.py。

运行：python tests/test_eliminated.py
"""

from fake_game import SHOTS, MissingShots, load, report, run_collect, shot  # 要在项目代码之前导入

import cv2

from eliminated import read_eliminated
from ocr import SMALL_CROP_PARAMS, LazyEngine, load_config
from screens import ScreenDetector

SAMPLES = {"samples/死亡瞬间.png": 17, "samples/vlcsnap-2026-10-16-18h27m05s111.png": 16}
SPECTATE = "samples/vlcsnap-2026-10-16-18h27m31s652.png"  # 观战画面，不是要认的画面
ZOOMS = (0.9, 1.15)  # 字比参照图小 10%、大 15%


def zoom(img, factor):
    """整张图以中心为准放大或缩小 factor 倍，大小不变（"排名第 X"正好在画面中间，相当于只有字变大变小）。"""
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 0, factor)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def main():
    print("淘汰画面（排名第 X）")
    try:
        images = {relative: load(shot(relative)) for relative in [*SAMPLES, SPECTATE]}
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None

    engine = LazyEngine(SMALL_CROP_PARAMS)
    detector = ScreenDetector(engine)
    cfg = load_config("eliminated.json")
    found = {relative: detector.detect(images[relative]) for relative in SAMPLES}
    read = {relative: read_eliminated(engine, images[relative], cfg)["placement"] for relative in SAMPLES}
    zoomed = [zoom(images["samples/死亡瞬间.png"], f) for f in ZOOMS]
    zoom_ok = [detector.detect(z) == "eliminated" and read_eliminated(engine, z, cfg)["placement"] == 17 for z in zoomed]
    zoom_text = "、".join(f"{f} 倍 {'✓' if ok else '✗'}" for f, ok in zip(ZOOMS, zoom_ok))

    # 其他截图只做外观检查（不跑识别）：一张都不该像淘汰画面
    others = [p for p in SHOTS.rglob("*.png") if p.relative_to(SHOTS).as_posix() not in SAMPLES
              and not {"debug_crops", "legends"} & set(p.relative_to(SHOTS).parts)]  # 这两个文件夹里是小块，不是整屏
    alike = [p.name for p in others if detector.looks_like(load(p), "eliminated")]

    names = {17: "淘汰17", 16: "淘汰16"}
    pictures = {"观战": images[SPECTATE], **{names[v]: images[k] for k, v in SAMPLES.items()}}
    frames = ["观战", "淘汰17", "淘汰17", "淘汰17", "观战", "淘汰16", "观战"]
    rows, _, log = run_collect(frames, pictures.get, keep_all=True)
    said = [line for line in log if "淘汰画面：" in line]
    return report({
        "两张样本都认出是淘汰画面（包括\"全军覆没\"已经消失、字变大的那张）": all(v == "eliminated" for v in found.values()),
        f"读出名次 17 和 16（读到：{list(read.values())}）": list(read.values()) == list(SAMPLES.values()),
        f"字小 10%、大 15% 也认得出、读得对（{zoom_text}）": all(zoom_ok),
        f"其他 {len(others)} 张截图没有一张像淘汰画面（像的：{'、'.join(alike) or '无'}）": others and not alike,
        "记录程序：每次停在淘汰画面只读一次名次，写进日志": [line.split("] ")[1] for line in said]
        == ["淘汰画面：排名第 17", "淘汰画面：排名第 16"],
        "保留全部截图时，淘汰画面也存截图（攒样本）": any("进入淘汰画面，已存截图" in line for line in log),
        "名次还没写进数据库（第 2 步再做）": len(rows) == 0,
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
