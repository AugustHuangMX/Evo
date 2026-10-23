"""传奇头像（assets/legends/ → python tools/legend_icons.py → panel/static/legends/）：图标、许可文件、面板上显示。不用截图。

- 图标：config/legends.json 里每位传奇都有一个；是去掉了编辑信息的 SVG，正方形 viewBox，大小交给样式
- 许可（每个文件不一样，不适用 AGPL-3.0）：每个原图都写在 LICENSE.txt 里；随安装包发的那份和原图旁边的一样
- 战绩页：自己和队友的传奇前有头像；总览页：最近对局、传奇表现有头像；都是装饰（读屏软件跳过）
- 认不出图标的传奇（以后的新传奇）、没认出传奇的局：留出空位，名字照常显示，不报错
- 面板能提供图标文件（image/svg+xml）

运行：python tests/test_legend_icons.py
"""

import json
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import panel_app  # noqa: E402
import storage  # noqa: E402
from paths import ASSETS_DIR, CONFIG_DIR, PANEL_DIR  # noqa: E402

FOLDER = Path(tempfile.mkdtemp(prefix="evo-legend-icons-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里
ICON_DIR = PANEL_DIR / "static" / "legends"
SRC_DIR = ASSETS_DIR / "legends"
EDITOR_WORDS = ("inkscape", "sodipodi", "<metadata", "<!--")


def icon_checks():
    legends = json.loads((CONFIG_DIR / "legends.json").read_text(encoding="utf-8"))["legends"]
    missing = [info["zh"] for key, info in legends.items() if not (ICON_DIR / f"{key}.svg").is_file()]
    bad = []
    for path in sorted(ICON_DIR.glob("*.svg")):
        text = path.read_text(encoding="utf-8")
        root = ET.fromstring(text)
        box = [float(v) for v in root.get("viewBox", "").split()]
        if (root.tag != "{http://www.w3.org/2000/svg}svg" or len(box) != 4 or abs(box[2] - box[3]) > 1e-6
                or root.get("width") or root.get("height") or any(w in text for w in EDITOR_WORDS)):
            bad.append(path.name)
    return {
        f"每位传奇都有图标（{len(legends)} 位）{'' if not missing else f'：缺 {missing}'}": not missing,
        f"图标是干净的 SVG（正方形 viewBox、没有编辑信息、不写死大小）{'' if not bad else f'：{bad}'}": not bad,
        "面板认得出所有传奇的图标": len(panel_app.LEGEND_ICONS) == len(legends),
    }


def license_checks():
    text = (SRC_DIR / "LICENSE.txt").read_text(encoding="utf-8")
    shipped = ICON_DIR / "LICENSE.txt"
    unlisted = [p.name for p in SRC_DIR.glob("*.svg") if p.name not in text]
    return {
        f"每个原图都写在 LICENSE.txt 里{'' if not unlisted else f'：没写 {unlisted}'}": not unlisted,
        "随安装包发的许可文件和原图旁边的一样": shipped.is_file() and shipped.read_text(encoding="utf-8") == text,
        "许可文件写明不适用 AGPL-3.0、来源和各种许可的链接": "AGPL-3.0" in text and "apexlegends.wiki.gg" in text
        and "creativecommons.org/licenses/by-nc-sa/3.0/" in text and "creativecommons.org/licenses/by-sa/3.0/" in text,
    }


def pages():
    """新建数据库：一局恶灵（两名队友：直布罗陀、没认出）、一局还没有图标的新传奇、一局没认出传奇，返回 (总览页, 战绩页展开第一局)。"""
    path = FOLDER / "icons.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)
    first = storage.insert_match(conn, {"ended_at": "2026-10-20 20:00:00", "mode": "ranked", "placement": 3, "legend": "恶灵",
                                        "map": "世界尽头"})
    storage.save_teammates(conn, first, [{"slot": 1, "name": "甲", "legend": "直布罗陀"}, {"slot": 2, "name": "乙", "legend": None}])
    storage.insert_match(conn, {"ended_at": "2026-10-20 20:30:00", "mode": "ranked", "placement": 5, "legend": "未来传奇"})
    storage.insert_match(conn, {"ended_at": "2026-10-20 21:00:00", "mode": "ranked", "placement": 7})
    conn.commit()
    conn.close()
    panel_app.app.config.update(DB=path, TESTING=True)
    client = panel_app.app.test_client()
    return client.get("/").get_data(as_text=True), client.get(f"/matches?open={first}").get_data(as_text=True), client


def page_checks():
    overview, matches, client = pages()
    recent = overview.split('class="recent"')[1].split("</ul>")[0]
    bars = overview.split('class="legend-list"')[1].split("</section>")[0]
    mates = "".join(re.findall(r'<tr class="mate-row.*?</tr>', matches, re.S))
    icons = re.findall(r'<span class="legend-icon[^"]*"[^>]*>', overview + matches)
    response = client.get("/static/legends/wraith.svg")
    return {
        "战绩页：自己的传奇前有头像": "legends/wraith.svg" in matches.split('class="mate-row')[0],
        "战绩页：队友的传奇前有头像，没认出的留空位": "legends/gibraltar.svg" in mates and 'legend-icon blank' in mates,
        "总览页：最近对局、传奇表现的名字前有头像": "legends/wraith.svg" in recent and "legends/wraith.svg" in bars,
        "还没有图标的新传奇、没认出传奇的局：留空位，名字照常显示": "未来传奇" in matches and "未来传奇" in recent
        and recent.count("legend-icon blank") == 2,
        "头像都是装饰（aria-hidden，读屏软件只读名字）": bool(icons) and all('aria-hidden="true"' in t for t in icons),
        "面板能提供图标文件": response.status_code == 200 and response.mimetype == "image/svg+xml",
    }


def main():
    print("传奇头像（图标、许可文件、战绩页和总览页）")
    checks = {}
    for part in (icon_checks, license_checks, page_checks):
        checks.update(part())
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
