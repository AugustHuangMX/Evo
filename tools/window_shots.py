"""README 和官网的窗口截图（开发工具）：python tools/window_shots.py

用演示数据截 Evo 窗口的总览、战绩、设置三页（深色主题、带 Evo 标题栏，1262×988，四角按 Windows 11 窗口的圆角透明），
不碰正式数据：
- docs/images/<页>.png：README 用
- site/img/<页>-<内容摘要>.webp：官网首页用（site/ 在本机才有）。官网图片缓存一天，文件名带内容摘要，换了图就换名字，
  打开官网马上看到新图；site/index.html 里的 <img> 跟着改，旧图删掉。改完要上传官网才生效（见 docs/底座.md）

做法和宣传图一样（tools/feature_cards.py）：临时文件夹里生成演示战绩（游戏名是编的 EvoPlayer）、打开面板、
Edge 无界面模式截图；关掉入场动画（走势图从左往右展开等），截到的是播完时的样子。
"""

import hashlib
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）

ROOT = code_path.ROOT
W, H = 1262, 988  # 和官网首页 <img> 上写的宽高一样，换图时页面不会跳
RADIUS = 8  # Windows 11 窗口的圆角
PAGES = {"overview": "/", "matches": "/matches", "settings": "/settings"}
README_DIR = ROOT / "docs" / "images"
SITE_IMG = ROOT / "site" / "img"
INDEX = ROOT / "site" / "index.html"
NO_ANIMATION = "<style>*,*::before,*::after{animation:none!important;transition:none!important}</style>"


def corner_mask():
    """四角圆角外透明（先画 4 倍大再缩小，边缘更平滑）。"""
    s = 4
    mask = Image.new("L", (W * s, H * s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W * s - 1, H * s - 1), radius=RADIUS * s, fill=255)
    return mask.resize((W, H), Image.LANCZOS)


def update_site(files):
    """site/index.html 里三张截图的 <img> 指向新文件，删掉不再用的旧截图。"""
    html = INDEX.read_bytes().decode("utf-8")  # 按字节读写，换行符保持原样
    for name, file in files.items():
        html, n = re.subn(rf'src="img/{name}(-[^"/]*)?\.webp"', f'src="img/{file}"', html)
        if n != 1:
            sys.exit(f"site/index.html 里 img/{name}….webp 应该有 1 处，找到 {n} 处：网页的写法可能变了，请手动改")
    INDEX.write_bytes(html.encode("utf-8"))
    used = "".join(p.read_bytes().decode("utf-8") for p in (ROOT / "site").glob("*.html"))
    for old in SITE_IMG.glob("*.webp"):
        if re.fullmatch(rf"({'|'.join(PAGES)})(-.*)?\.webp", old.name) and f"img/{old.name}" not in used:
            old.unlink()
            print(f"删掉旧图 site/img/{old.name}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="evo-shots-"))
    data = tmp / "data"
    data.mkdir()
    os.environ["EVO_DATA_DIR"] = str(data)  # 在 import 程序代码之前设好：演示数据、设置都在临时文件夹里
    import feature_cards as fc
    os.environ["EVO_VIDEOCONFIG"] = str(tmp / "videoconfig.txt")
    (tmp / "videoconfig.txt").write_text(fc.VIDEOCONFIG, encoding="utf-8")
    (data / "settings.json").write_text('{"update_auto": true, "telemetry_consent": false}',
                                         encoding="utf-8")  # 免得截到顶部的提问（自动检查、匿名统计）

    import panel_app

    @panel_app.app.after_request
    def no_animation(resp):
        if resp.mimetype == "text/html":
            resp.set_data(resp.get_data(as_text=True).replace("</head>", NO_ANIMATION + "</head>", 1))
        return resp

    db, _ = fc.make_demo_data()
    base, server = fc.serve_panel(db)
    mask = corner_mask()
    site_files = {}
    try:
        for name, path in PAGES.items():
            raw = tmp / f"{name}.png"
            fc.edge(tmp, [f"--screenshot={raw}", base + path], size=(W, H), scale=1)
            img = Image.open(raw).convert("RGBA")
            if img.size != (W, H):
                sys.exit(f"{name} 截出来是 {img.size}，应该是 {(W, H)}")
            img.putalpha(mask)
            img.save(README_DIR / f"{name}.png", optimize=True)
            print(f"已写 docs/images/{name}.png")
            if SITE_IMG.is_dir():
                webp = tmp / f"{name}.webp"
                img.save(webp, quality=85, method=6)
                file = f"{name}-{hashlib.sha256(webp.read_bytes()).hexdigest()[:8]}.webp"
                shutil.copyfile(webp, SITE_IMG / file)
                site_files[name] = file
                print(f"已写 site/img/{file}（{webp.stat().st_size // 1024} KB）")
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    if site_files:
        update_site(site_files)
        print("site/index.html 已改好；上传官网才生效")
    else:
        print("没有 site/ 文件夹，跳过官网的截图")


if __name__ == "__main__":
    main()
