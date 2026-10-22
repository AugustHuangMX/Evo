"""生成 Evo 的图标：python tools/make_icon.py  →  assets/icon/evo.svg、assets/icon/evo.png，和官网的图标

图标（2026-10-22 用户给的设计稿）：深色圆角方块上一个由横杠组成的 "E"：一根竖笔，三条右端斜切的横杠，
最上面一条是蓝色，下面两条和竖笔是白色。设计稿是按 1024 的网格画的，下面的数字都是这个网格上的坐标。

- assets/icon/evo.svg：矢量原稿
- assets/icon/evo.png：1024 像素，程序里的图标都从它来（托盘、exe、窗口、面板、分享图片，见 code/icons.py）
- 官网（site/ 在本机才有）：favicon.ico（16、32、48）、img/favicon-32.png、img/evo.png（首页大图，256）

改了下面的形状或颜色，再运行一次就行；之后 python tools/build.py 重新打包，exe 的图标才会变。
"""

import io
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent  # 项目文件夹（本文件在 tools/ 里）
ICON_DIR = ROOT / "assets" / "icon"
SITE = ROOT / "site"

GRID = 1024
RADIUS = 224  # 方块的圆角
TILE = "#0F141A"  # 方块：深色
WHITE = "#E8ECF1"
BLUE = "#3B9EFF"
# 竖笔宽 128，横杠高 112、间隔 104，右端斜切：往下 112 往左缩 64
STEM_AND_ARMS = [(256, 240), (384, 240), (384, 456), (664, 456), (600, 568), (384, 568),
                 (384, 672), (568, 672), (504, 784), (256, 784)]  # 白色：竖笔 + 中间、下面两条横杠
TOP_ARM = [(384, 240), (768, 240), (704, 352), (384, 352)]  # 蓝色：最上面一条
SUPERSAMPLE = 4  # 先画 4 倍大再缩小，边缘更平滑


def svg():
    def path(points):
        (x0, y0), *rest = points
        return f"M{x0} {y0}" + "".join(f"L{x} {y}" for x, y in rest) + "Z"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}">\n'
            f'<rect width="{GRID}" height="{GRID}" rx="{RADIUS}" fill="{TILE}"/>\n'
            f'<path d="{path(STEM_AND_ARMS)}" fill="{WHITE}"/>\n'
            f'<path d="{path(TOP_ARM)}" fill="{BLUE}"/>\n'
            f'</svg>\n')


def render(size):
    """画成 size × size 的 PNG（圆角外面透明）。"""
    big = size * SUPERSAMPLE
    k = big / GRID
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=RADIUS * k, fill=TILE)
    for points, color in ((STEM_AND_ARMS, WHITE), (TOP_ARM, BLUE)):  # 挪半个像素：PIL 会把边线上的像素也涂上，不挪会胖一圈
        d.polygon([(x * k - 0.5, y * k - 0.5) for x, y in points], fill=color)
    return img.resize((size, size), Image.LANCZOS)


def save(path, data):
    path.write_bytes(data)
    print(f"已写 {path.relative_to(ROOT)}")


def png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def main():
    save(ICON_DIR / "evo.svg", svg().encode())
    save(ICON_DIR / "evo.png", png(render(GRID)))
    if not SITE.is_dir():
        print("没有 site/ 文件夹，跳过官网的图标")
        return
    save(SITE / "img" / "evo.png", png(render(256)))
    save(SITE / "img" / "favicon-32.png", png(render(32)))
    sizes = (16, 32, 48)
    buf = io.BytesIO()
    render(48).save(buf, "ICO", sizes=[(s, s) for s in sizes], append_images=[render(s) for s in sizes[:-1]])
    save(SITE / "favicon.ico", buf.getvalue())


if __name__ == "__main__":
    main()
