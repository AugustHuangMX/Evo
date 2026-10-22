"""总览页"关注我"：开发者的 B 站、小黑盒主页（panel_app.FOLLOW_LINKS、overview.html、panel/static/brands/）。
不用截图，不开浏览器（用 Flask 自带的测试客户端）。

- 总览页最下面有"关注我"和两个按钮，地址是正式地址（不带分享追踪参数），交给系统浏览器打开（target="_blank"、rel="noopener noreferrer"）
- 没有对局时也显示
- 两个图标是写对了的 SVG、单色（颜色跟随文字）、不引用别的文件，面板能取到

运行：python tests/test_follow.py
"""

import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import panel_app
import storage
from paths import PANEL_DIR

FOLDER = Path(tempfile.mkdtemp(prefix="evo-follow-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里
EXPECTED = {"哔哩哔哩": "https://space.bilibili.com/26649278",
            "小黑盒": "https://www.xiaoheihe.cn/app/user/profile/765d6910c805"}


def follow_links(page):
    """总览页"关注我"那一块里的按钮：[(名字, 地址, 整个 <a> 标签)]。"""
    found = re.search(r'<section class="card follow-card".*?</section>', page, re.S)
    if not found:
        return []
    return [(" ".join(re.sub(r"<[^>]+>", " ", body).split()).removesuffix(" ↗"), href, tag)
            for tag, href, body in re.findall(r'(<a [^>]*href="([^"]+)"[^>]*>)(.*?)</a>', found.group(0), re.S)]


def main():
    print("总览页\"关注我\"：B 站、小黑盒")
    path = FOLDER / "follow.db"
    conn = storage.connect(path)
    storage.insert_match(conn, {"ended_at": "2026-10-22 20:00:00", "placement": 3, "legend": "恶灵"})
    conn.close()
    empty = FOLDER / "empty.db"
    storage.connect(empty).close()

    panel_app.app.config.update(DB=path, TESTING=True)
    client = panel_app.app.test_client()
    links = follow_links(client.get("/").get_data(as_text=True))
    panel_app.app.config["DB"] = empty
    empty_links = follow_links(panel_app.app.test_client().get("/").get_data(as_text=True))
    panel_app.app.config["DB"] = path

    icons = {}
    for f in panel_app.FOLLOW_LINKS:
        text = (PANEL_DIR / "static" / f["icon"]).read_text(encoding="utf-8")
        try:
            root = ET.fromstring(text)
            icons[f["name"]] = {"svg": root.tag.endswith("svg"), "color": root.get("fill") == "currentColor",
                                "paths": len(root.findall("{http://www.w3.org/2000/svg}path")),
                                "external": bool(re.search(r'href=|url\(|<image', text)),
                                "served": client.get("/static/" + f["icon"]).status_code}
        except ET.ParseError:
            icons[f["name"]] = {}

    checks = {
        "总览页有\"关注我\"，两个按钮：哔哩哔哩、小黑盒": [name for name, _, _ in links] == list(EXPECTED),
        "地址是正式地址（不带分享追踪参数）": {name: href for name, href, _ in links} == EXPECTED,
        "交给系统浏览器打开（target=_blank、rel=noopener noreferrer）":
            bool(links) and all('target="_blank"' in tag and 'rel="noopener noreferrer"' in tag for _, _, tag in links),
        "没有对局时也显示": [name for name, _, _ in empty_links] == list(EXPECTED),
        "两个图标都是写对了的 SVG": all(i.get("svg") and i.get("paths") for i in icons.values()) and len(icons) == 2,
        "图标单色、颜色跟随文字（fill=currentColor）": all(i.get("color") for i in icons.values()),
        "图标不引用别的文件（不联网）": all(i.get("external") is False for i in icons.values()),
        "面板能取到图标": all(i.get("served") == 200 for i in icons.values()),
    }
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
