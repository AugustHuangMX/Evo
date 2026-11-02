"""总览页"数据统计"：更多维度（12 项）和模式 / 传奇筛选（2026-10-30）。

不用截图，不开浏览器（用 Flask 自带的测试客户端）。

- 12 项分两组：战斗（场均击杀、助攻、击倒、伤害，单局最高击杀、伤害）、
  排名与生存（场均名次、胜率、前五率、冠军次数、场均生存、场均 RP）
- 模式筛选：全部 / 排位 / 匹配；"匹配"= 不是排位的局，包括别的和没认出模式的
- 传奇筛选：下拉框；和模式可以叠加；不存在的传奇当作"全部传奇"
- 范围沿用近 30 局 / 全部：先按筛选条件筛，再取最近 30 局（"近 30 局恶灵"= 最近的 30 局恶灵）
- 筛选只作用于数据统计和传奇表现；段位、RP 走势、最近对局不受影响
- 读不到的项写"—"不写 0；分享卡片的概况仍然 6 项

运行：python tests/test_overview_stats.py
"""

import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import panel_app
import storage

FOLDER = Path(tempfile.mkdtemp(prefix="evo-ovstats-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里

LABELS = ["场均击杀", "场均助攻", "场均击倒", "场均伤害", "单局最高击杀", "单局最高伤害",
          "场均名次", "胜率", "前五率", "冠军次数", "场均生存", "场均 RP"]

# 模式=匹配（5 局，含一局"别的"、一局没认出模式）：硬编码的答案
PUBS = {
    "场均击杀": "5.0", "场均助攻": "2.5", "场均击倒": "2.0", "场均伤害": "1,140",
    "单局最高击杀": "8", "单局最高伤害": "1,800",
    "场均名次": "4.0", "胜率": "25%", "前五率": "75%", "冠军次数": "1",
    "场均生存": "15:00", "场均 RP": "—",
}


def new_db():
    """40 局：前 35 局排位（恶灵、罗芭轮流），最后 5 局不是排位（3 局匹配 + 1 局别的 + 1 局没认出模式）。
    排位第 i 局：名次 i % 10 + 1、击杀 i % 7、助攻 i % 5、击倒 i % 4、伤害 400 + i × 10、生存 600 + i × 10 秒。
    匹配那 5 局的数见 PUBS。"""
    path = FOLDER / "overview.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)
    start = datetime.now() - timedelta(days=2)
    for i in range(35):
        storage.insert_match(conn, {
            "player_name": "StatsPlayer", "mode": "ranked", "legend": "恶灵" if i % 2 == 0 else "罗芭",
            "placement": i % 10 + 1, "kills": i % 7, "assists": i % 5, "knockdowns": i % 4,
            "damage": 400 + i * 10, "survival_time": 600 + i * 10, "rp_delta": 10 - i % 9,
            "ended_at": f"{start + timedelta(minutes=i):%Y-%m-%d %H:%M:%S}",
        })
    pubs = [
        {"mode": "pubs", "legend": "罗芭", "placement": 1, "kills": 6, "assists": 2, "knockdowns": 1,
         "damage": 1500, "survival_time": 1200},
        {"mode": "pubs", "legend": "罗芭", "placement": 3, "kills": None, "assists": 1, "knockdowns": 2,
         "damage": 900, "survival_time": 900},
        {"mode": "other", "legend": "恶灵", "placement": None, "kills": 2, "assists": None, "knockdowns": 3,
         "damage": 300, "survival_time": 300},
        {"mode": None, "legend": "恶灵", "placement": 5, "kills": 4, "assists": 3, "knockdowns": None,
         "damage": 1200, "survival_time": 1000},
        {"mode": "pubs", "legend": "罗芭", "placement": 7, "kills": 8, "assists": 4, "knockdowns": 2,
         "damage": 1800, "survival_time": 1100},
    ]
    for j, game in enumerate(pubs):
        storage.insert_match(conn, {"player_name": "StatsPlayer",
                                    "ended_at": f"{start + timedelta(minutes=35 + j):%Y-%m-%d %H:%M:%S}", **game})
    conn.close()
    return path


def stat_values(page):
    """总览页"数据统计"里的 12 个数字，按页面上的顺序。"""
    return re.findall(r'<div class="label">([^<]+)</div>\s*<div class="stat-value num(?: [^"]+)?"[^>]*>([^<]+)</div>', page)


def stat_count(page):
    found = re.search(r'共 <span class="num">(\d+)</span> 局', page)
    return int(found.group(1)) if found else None


def mode_links(page):
    """数据统计标题旁的三个模式按钮：{文字: (href, 是否选中)}。"""
    seg = re.search(r'<div class="seg" role="group" aria-label="对局模式">(.*?)</div>', page, re.S)
    if not seg:
        return {}
    return {label: (href, 'class="on"' in attrs)
            for href, attrs, label in re.findall(r'<a href="([^"]+)"([^>]*)>(全部|排位|匹配)</a>', seg.group(1))}


def range_links(page):
    """走势图上的范围按钮：{文字: href}（改范围时要保留模式、传奇筛选）。"""
    seg = re.search(r'<div class="seg" role="group" aria-label="统计范围">(.*?)</div>', page, re.S)
    if not seg:
        return {}
    return {label: href for href, label in re.findall(r'<a href="([^"]+)"[^>]*>(近 30 局|全部)</a>', seg.group(1))}


def legend_card(page):
    found = re.search(r'<section class="card legend-card".*?</section>', page, re.S)
    return found.group(0) if found else ""


def expected(rows, mode="all", legend=None, last30=False):
    """按总览页的规则筛完以后，12 项的答案（用 group_numbers 算，和总览页共用）。"""
    selected = panel_app.filter_mode(rows, mode)
    if legend:
        selected = [r for r in selected if r["legend"] == legend]
    if last30:
        selected = selected[-30:]
    return {s["label"]: s["value"] for group in panel_app.stat_groups(selected) for s in group["items"]}


def main():
    print("总览页数据统计：更多维度、模式 / 传奇筛选")
    path = new_db()
    panel_app.app.config.update(DB=path, TESTING=True)
    conn = storage.connect(path)
    rows = storage.all_matches(conn)
    conn.close()
    client = panel_app.app.test_client()

    pages = {
        "default": client.get("/").get_data(as_text=True),
        "all": client.get("/?range=all").get_data(as_text=True),
        "ranked": client.get("/?range=all&mode=ranked").get_data(as_text=True),
        "ranked30": client.get("/?mode=ranked").get_data(as_text=True),
        "pubs": client.get("/?range=all&mode=pubs").get_data(as_text=True),
        "ziling": client.get("/?range=all&mode=ranked&legend=恶灵").get_data(as_text=True),
        "ziling_pubs": client.get("/?range=all&mode=pubs&legend=恶灵").get_data(as_text=True),
        "unknown_legend": client.get("/?range=all&legend=不存在的传奇").get_data(as_text=True),
    }
    values = {key: dict(stat_values(page)) for key, page in pages.items()}
    counts = {key: stat_count(page) for key, page in pages.items()}

    # 没有符合筛选条件的局：另建只有匹配局的数据库
    lonely_path = FOLDER / "lonely.db"
    lonely_path.unlink(missing_ok=True)
    conn = storage.connect(lonely_path)
    storage.insert_match(conn, {"player_name": "StatsPlayer", "mode": "pubs", "ended_at": "2026-10-30 20:00:00",
                                "placement": 2})
    conn.close()
    panel_app.app.config["DB"] = lonely_path
    lonely = panel_app.app.test_client().get("/?mode=ranked").get_data(as_text=True)
    panel_app.app.config["DB"] = path

    links = mode_links(pages["all"])
    legend_text = legend_card(pages["ziling"])

    checks = {
        "12 项，分两组，每组 6 项": list(values["default"]) == LABELS,
        "默认：近 30 局、模式全部，标题旁写共 30 局": counts["default"] == 30
                                              and values["default"] == expected(rows, last30=True),
        "范围=全部：共 40 局": counts["all"] == 40 and values["all"] == expected(rows),
        "模式=排位：只算排位局（35 局）": counts["ranked"] == 35 and values["ranked"] == expected(rows, "ranked"),
        "排位 + 近 30 局：先筛再取最近 30 局（不算前 5 局排位）": counts["ranked30"] == 30
                                                and values["ranked30"]["单局最高击杀"] == "6"
                                                and values["ranked30"]["单局最高伤害"] == "740",
        "模式=匹配：包括别的和没认出模式的局（5 局）": counts["pubs"] == 5,
        "匹配的 12 个数字（读不到写—，不写 0）": values["pubs"] == PUBS,
        "传奇筛选：恶灵 + 排位（18 局）": counts["ziling"] == 18
                                    and values["ziling"] == expected(rows, "ranked", "恶灵")
                                    and re.search(r'<option value="恶灵" selected>', pages["ziling"]) is not None,
        "传奇筛选可以和模式叠加：恶灵 + 匹配（2 局）": counts["ziling_pubs"] == 2
                                              and values["ziling_pubs"]["场均名次"] == "5.0"
                                              and values["ziling_pubs"]["场均击杀"] == "3.0"
                                              and values["ziling_pubs"]["场均 RP"] == "—",
        "传奇表现跟着模式和传奇筛选（只剩恶灵）": "传奇表现 · 全部 · 排位 · 恶灵" in legend_text
                                          and ">恶灵<" in legend_text and ">罗芭<" not in legend_text,
        "不存在的传奇：当作全部传奇（共 40 局）": counts["unknown_legend"] == 40
                                          and values["unknown_legend"] == expected(rows)
                                          and re.search(r'<option value="">全部传奇</option>', pages["unknown_legend"]),
        "模式按钮：选中项有标记，切换时保留范围": links.get("全部") == ('/?range=all', True)
                                          and links.get("排位") == ('/?range=all&amp;mode=ranked', False)
                                          and links.get("匹配") == ('/?range=all&amp;mode=pubs', False),
        "改范围时保留模式和传奇筛选": range_links(pages["ziling"]) == {
            "近 30 局": "/?mode=ranked&amp;legend=%E6%81%B6%E7%81%B5",
            "全部": "/?range=all&amp;mode=ranked&amp;legend=%E6%81%B6%E7%81%B5"},
        "没有符合筛选的局：写明，不出 12 个空数字": "这个筛选下还没有对局" in lonely and "stat-value" not in lonely,
        "分享卡片的概况仍然 6 项（场均名次、击杀、伤害、前五率、胜率、统计局数）": [
            s["label"] for s in panel_app.stat_cards(rows)] == ["场均名次", "场均击杀", "场均伤害", "前五率", "胜率", "统计局数"],
    }
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
