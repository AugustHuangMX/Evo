"""Evo 的图标：托盘、程序文件（exe）、窗口标题栏和浏览器标签页都从这里取，保证是同一个。

用 assets/icon/evo.png（用户设计的图标），自动裁掉四周的透明空白、居中放进正方形；
文件不在的话，用一个盾牌临时代替。

面板总览页的段位图标也在这里生成：原图（游戏里的段位徽章）放在 assets/ranks/，
换赛季、换图标后运行 python code/icons.py，重新生成 panel/static/ranks/ 里的小图和铭牌位置。
"""

import json

from PIL import Image, ImageDraw

from paths import ASSETS_DIR, PANEL_DIR

ICON_FILES = ("evo.png",)

RANK_FILES = {"bronze": "Ranked_Tier1_Bronze.png", "silver": "Ranked_Tier2_Silver.png", "gold": "Ranked_Tier3_Gold.png",
              "platinum": "Ranked_Tier4_Platinum.png", "diamond": "Ranked_Tier5_Diamond.png",
              "master": "Ranked_Tier6_Master.png", "predator": "Ranked_Tier7_Apex_Predator.png"}  # 菜鸟没有图标
RANK_SIZE = 192  # 面板上显示 96 像素，高分屏放大 2 倍也清楚
RANK_DIR = PANEL_DIR / "static" / "ranks"


def draw_shield(size=64):
    """临时图标：一个盾牌（设计稿里原来的标志）。"""
    s = 4 * size / 28  # 先画 4 倍大再缩小，边缘更平滑
    img = Image.new("RGBA", (4 * size, 4 * size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    outer = [(14, 2), (25, 7), (25, 15), (23.5, 20), (19.5, 24), (14, 27), (8.5, 24), (4.5, 20), (3, 15), (3, 7)]
    inner = [(14, 8), (20, 10.5), (20, 15), (19, 18), (17, 20), (14, 21.5), (11, 20), (9, 18), (8, 15), (8, 10.5)]
    d.polygon([(x * s, y * s) for x, y in outer], outline=(255, 90, 90, 255), width=round(2.4 * s))
    d.polygon([(x * s, y * s) for x, y in inner], fill=(255, 90, 90, 255))
    return img.resize((size, size), Image.LANCZOS)


def square_icon(img, pad=0.04):
    """裁掉四周的透明空白，居中放进正方形（四周留一点边）。小尺寸（如托盘的 16 像素）下图形才不会太小。"""
    box = img.getchannel("A").point(lambda a: 255 if a > 10 else 0).getbbox()
    if box:
        img = img.crop(box)
    side = round(max(img.size) * (1 + 2 * pad))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return canvas


def base_icon(size=64):
    """程序图标，返回 (图片, 来源说明)。"""
    for name in ICON_FILES:
        path = ASSETS_DIR / "icon" / name
        if path.is_file():
            return square_icon(Image.open(path).convert("RGBA")).resize((size, size), Image.LANCZOS), str(path)
    return draw_shield(size), "面板的盾牌（assets/icon/ 里还没有图标）"


def find_plate(img):
    """段位徽章底部的黑色铭牌（游戏里在上面写段数 IV–I）。从底部正中往上找一段足够高的深色；
    返回 {"x": 中心, "y": 中心, "h": 高度}，都是占图片边长的百分比。找不到返回 None。"""
    px, (w, h) = img.load(), img.size
    cx = w // 2

    def dark(x, y):
        r, g, b, a = px[x, y]
        return a > 200 and max(r, g, b) < 50

    def dark_center(y):  # 正中间 5 个像素都是深色
        return all(dark(x, y) for x in range(cx - 2, cx + 3))

    y = h - 1
    while y > 0:
        while y > 0 and not dark_center(y):
            y -= 1
        bottom = y
        while y > 0 and dark_center(y):
            y -= 1
        if bottom - y >= 0.1 * h:  # 太矮的不是铭牌（如白银、猎杀者底部的小缺口），接着往上找
            mid = (y + 1 + bottom) // 2
            left = right = cx
            while left > 0 and dark(left - 1, mid):
                left -= 1
            while right < w - 1 and dark(right + 1, mid):
                right += 1
            return {"x": round(100 * (left + right + 1) / 2 / w, 1), "y": round(100 * mid / h, 1),
                    "h": round(100 * (bottom - y) / h, 1)}
    return None


def make_rank_badges():
    """生成面板用的段位图标 panel/static/ranks/<段位>.webp，和铭牌位置 ranks.json。"""
    RANK_DIR.mkdir(parents=True, exist_ok=True)
    plates = {}
    for tier, name in RANK_FILES.items():
        path = ASSETS_DIR / "ranks" / name
        if not path.is_file():
            print(f"跳过 {tier}：没有 {path}")
            continue
        img = square_icon(Image.open(path).convert("RGBA"), pad=0)
        plates[tier] = find_plate(img)
        img.resize((RANK_SIZE, RANK_SIZE), Image.LANCZOS).save(RANK_DIR / f"{tier}.webp", quality=90, method=6)
        print(f"{tier:9s} 铭牌 {plates[tier]}")
    (RANK_DIR / "ranks.json").write_text(json.dumps(plates, indent=1), encoding="utf-8")
    print(f"已写到 {RANK_DIR}")


if __name__ == "__main__":
    make_rank_badges()
