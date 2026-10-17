"""Evo 的图标：托盘、程序文件（exe）、窗口标题栏和浏览器标签页都从这里取，保证是同一个。

优先用 assets/icon/ 里用户设计的图标（evo.png 或 icon.png），自动裁掉四周的透明空白、居中放进正方形；
没有的话，用面板左上角的盾牌临时代替。
"""

from PIL import Image, ImageDraw

from paths import ASSETS_DIR

ICON_FILES = ("evo.png", "icon.png")  # 两个名字都认


def draw_shield(size=64):
    """临时图标：面板左上角的盾牌。"""
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
