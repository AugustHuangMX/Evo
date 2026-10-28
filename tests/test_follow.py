"""总览页"关注我"：开发者的 B 站、小黑盒主页和 Evo 官网（panel_app.FOLLOW_LINKS、overview.html、panel/static/brands/）。
不用截图，不开浏览器（用 Flask 自带的测试客户端）。

- 总览页有"关注我"和三个按钮，地址是正式地址（不带分享追踪参数），交给系统浏览器打开（target="_blank"、rel="noopener noreferrer"）
- 有对局时在左边一列"最近对局"下面（2026-10-28 用户要求）；没有对局时也显示
- 三个图标是写对了的 SVG、单色（颜色跟随文字）、不引用别的文件，面板能取到
- 设置页"关于 Evo"最下面的"支持我"（2026-10-28）：连着三个弹窗（文字是用户写的原文），最后一个是开发者的微信收款码
- 右上角"Evo 官方合作小程序：派圈助手"（2026-10-28，每一页都有）：前面是派圈助手的图标，点了弹出小程序码（弹窗、码、扫码说明、关闭）；
  每一页都有；没有小程序码图片时不显示；有图片时两张都是真的 PNG、面板能取到，文件夹里有许可说明

运行：python tests/test_follow.py
"""

import re
import tempfile
from unittest import mock
import xml.etree.ElementTree as ET
from pathlib import Path

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import panel_app
import storage
from paths import PANEL_DIR

FOLDER = Path(tempfile.mkdtemp(prefix="evo-follow-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里
PNG_HEADER = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10])  # PNG 文件开头的 8 个字节
EXPECTED = {"哔哩哔哩": "https://space.bilibili.com/26649278",
            "小黑盒": "https://www.xiaoheihe.cn/app/user/profile/765d6910c805",
            "Evo 官网": "https://evotracker.cn/"}


def follow_links(page):
    """总览页"关注我"那一块里的按钮：[(名字, 地址, 整个 <a> 标签)]。"""
    found = re.search(r'<section class="card follow-card".*?</section>', page, re.S)
    if not found:
        return []
    return [(" ".join(re.sub(r"<[^>]+>", " ", body).split()).removesuffix(" ↗"), href, tag)
            for tag, href, body in re.findall(r'(<a [^>]*href="([^"]+)"[^>]*>)(.*?)</a>', found.group(0), re.S)]


def main():
    print("总览页\"关注我\"：B 站、小黑盒、Evo 官网；右上角派圈助手；设置页支持我")
    path = FOLDER / "follow.db"
    conn = storage.connect(path)
    storage.insert_match(conn, {"ended_at": "2026-10-22 20:00:00", "placement": 3, "legend": "恶灵"})
    conn.close()
    empty = FOLDER / "empty.db"
    storage.connect(empty).close()

    panel_app.app.config.update(DB=path, TESTING=True)
    client = panel_app.app.test_client()
    page = client.get("/").get_data(as_text=True)
    links = follow_links(page)
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
        "总览页有\"关注我\"，三个按钮：哔哩哔哩、小黑盒、Evo 官网": [name for name, _, _ in links] == list(EXPECTED),
        "地址是正式地址（不带分享追踪参数）": {name: href for name, href, _ in links} == EXPECTED,
        "交给系统浏览器打开（target=_blank、rel=noopener noreferrer）":
            bool(links) and all('target="_blank"' in tag and 'rel="noopener noreferrer"' in tag for _, _, tag in links),
        "没有对局时也显示": [name for name, _, _ in empty_links] == list(EXPECTED),
        "有对局时在左边一列\"最近对局\"下面（不在最下面占满一行）":
            -1 < page.find('class="card list-card"') < page.find('class="card follow-card"') < page.find('class="ov-side"'),
        "三个图标都是写对了的 SVG": all(i.get("svg") and i.get("paths") for i in icons.values()) and len(icons) == 3,
        "图标单色、颜色跟随文字（fill=currentColor）": all(i.get("color") for i in icons.values()),
        "图标不引用别的文件（不联网）": all(i.get("external") is False for i in icons.values()),
        "面板能取到图标": all(i.get("served") == 200 for i in icons.values()),
    }
    checks.update(partner_checks(client))
    checks.update(support_checks(client))
    return report(checks)


def partner_checks(client):
    """右上角"Evo 官方合作小程序：派圈助手"。小程序码图片在不在都要能测：用 mock 换掉"有没有这张图"。"""
    with mock.patch.object(panel_app, "partner_qr", return_value=panel_app.PARTNER_QR):
        overview = client.get("/").get_data(as_text=True)
        others = [client.get(url).get_data(as_text=True) for url in ("/matches", "/stats", "/stats?view=all", "/settings")]
    with mock.patch.object(panel_app, "partner_qr", return_value=None):
        without = client.get("/").get_data(as_text=True)
    header = overview.split("</header>")[0]
    dialog = re.search(r'<dialog class="dialog partner-dialog".*?</dialog>', overview, re.S)
    dialog = dialog.group(0) if dialog else ""
    out = {
        "总览页右上角有按钮\"Evo 官方合作小程序：派圈助手\"（在顶部那一行里）":
            'id="partner-open"' in header and "Evo 官方合作小程序：</span><span class=\"partner-name\">派圈助手" in header,
        "点了弹出小程序码：弹窗里有码（读屏写着是什么）、扫码说明、关闭按钮":
            f'src="/static/{panel_app.PARTNER_QR}"' in dialog and 'alt="派圈助手的小程序码"' in dialog and "扫一扫" in dialog
            and "data-partner-close" in dialog and 'getElementById("partner-open")' in overview,
        "每一页都有（战绩、数据、全体玩家、设置页也有按钮和弹窗）": all(
            'id="partner-open"' in page.split("</header>")[0] and 'id="partner-dialog"' in page for page in others),
        "没有小程序码图片时不显示按钮和弹窗": "partner-open" not in without and "partner-dialog" not in without,
    }
    image = PANEL_DIR / "static" / panel_app.PARTNER_QR
    if image.is_file():
        reply = client.get(f"/static/{panel_app.PARTNER_QR}")
        icon = PANEL_DIR / "static" / panel_app.PARTNER_ICON
        out["按钮前面是派圈助手的图标（PNG、面板能取到，读屏软件跳过）"] = (
            icon.read_bytes()[:8] == PNG_HEADER and client.get(f"/static/{panel_app.PARTNER_ICON}").status_code == 200
            and f'<img class="partner-icon" src="/static/{panel_app.PARTNER_ICON}" alt=""' in header)
        out["两张图都写明了不适用 AGPL（同一个文件夹里的 LICENSE.txt）"] = (image.parent / "LICENSE.txt").is_file()
        out["小程序码图片是 PNG、面板能取到"] = (image.read_bytes()[:8] == PNG_HEADER and reply.status_code == 200
                                       and panel_app.partner_qr() == panel_app.PARTNER_QR)
    else:
        print(f"  （还没有小程序码图片 {image}：真的图片那一项跳过）")
    return out


SUPPORT_TEXTS = (  # 用户写的原文（"Evo" 后面照面板的习惯加了空格）
    "谢谢你对我的支持！Evo 承诺给大家一个免费的使用体验，除非你真的喜欢这个产品，并且有余力，否则我强烈不建议你破费！",
    "是的，我就是想真金白银支持你",
    "看样子你真的很喜欢 Evo，我再一次感谢你的支持！不过，任何消费之前还请三思！你还有机会退出去",
    "我真的想好了！发码！",
)
JPEG_HEADER = bytes([0xFF, 0xD8, 0xFF])


def support_checks(client):
    """设置页"关于 Evo"下面的"支持我"：连着三个弹窗，最后一个是收款码。"""
    settings = client.get("/settings").get_data(as_text=True)
    about = re.search(r'<aside class="card about-card".*?</aside>', settings, re.S)
    about = about.group(0) if about else ""
    dialogs = {n: re.search(rf'<dialog [^>]*id="support-{n}".*?</dialog>', settings, re.S) for n in (1, 2, 3)}
    dialogs = {n: d.group(0) if d else "" for n, d in dialogs.items()}
    image = PANEL_DIR / "static" / "support" / "wechat-pay.jpg"
    return {
        "设置页\"关于 Evo\"最下面（意见反馈下面）有\"支持我\"按钮":
            -1 < about.find("feedback-btn") < about.find('id="support-open"')
            and re.search(r'id="support-open".*?</svg>\s*支持我\s*</button>', about, re.S) is not None,
        "第一个弹窗：原文 + \"是的，我就是想真金白银支持你\"（下一步）+ 关闭":
            SUPPORT_TEXTS[0] in dialogs[1] and f'data-support-next="2">{SUPPORT_TEXTS[1]}<' in dialogs[1]
            and "data-support-close" in dialogs[1],
        "第二个弹窗：原文 + \"我真的想好了！发码！\"（下一步）+ 关闭":
            SUPPORT_TEXTS[2] in dialogs[2] and f'data-support-next="3">{SUPPORT_TEXTS[3]}<' in dialogs[2]
            and "data-support-close" in dialogs[2],
        "第三个弹窗：收款码（读屏写着是什么）+ 关闭":
            'src="/static/support/wechat-pay.jpg"' in dialogs[3] and 'alt="开发者的微信收款码"' in dialogs[3]
            and "data-support-close" in dialogs[3],
        "收款码是 JPG、面板能取到，写明了不适用 AGPL":
            image.read_bytes()[:3] == JPEG_HEADER and client.get("/static/support/wechat-pay.jpg").status_code == 200
            and (image.parent / "LICENSE.txt").is_file(),
        "只在设置页（总览页没有）": "support-open" not in client.get("/").get_data(as_text=True),
    }


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
