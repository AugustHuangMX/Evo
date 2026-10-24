"""段位（config/ranks.json + ranks.py）：每段的起点、换算成连续分数、由"下一段位"推当前段位、总览页的段位卡片和走势图。不用截图。

- 配置：8 个段位从低到高；分界线从低到高不重复；和实测（钻石每段 1000、大师 16,000、钻石入场费 65）、
  EA 帮助页 Split 2 重置表（白金 III 9250、白金 II 10000、白金 I 11000，白银 / 黄金 / 白金 IV 含 +250 保护）对得上
- 换算：这一段的起点 + 段内进度；大师不分段；读不全返回空
- 经验值页"下一段位"：钻石 I → 钻石 II；大师 → 钻石 I；Apex 猎杀者 → 大师（不分段，不再推成"大师 I"）
- 段位卡片只显示"钻石 I · 50 排位点数"，没有分母、进度条、"距下一段还差"（2026-10-17 用户决定）；大师不写段数；
  大厅读数比最后一局新时用大厅的（2026-10-18 用户决定：总分以大厅为准）
- 走势图：分界线按配置画（黄金每段 750）；快升段时画出下一段，离得远不画；最后一局之后的大厅读数接在最后

运行：python tests/test_ranks.py
"""

import tempfile
from pathlib import Path

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import panel_app  # noqa: E402
import ranks  # noqa: E402
import storage  # noqa: E402
from xp_page import current_rank  # noqa: E402

FOLDER = Path(tempfile.mkdtemp(prefix="evo-ranks-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里
TIER_NAMES = ["菜鸟", "青铜", "白银", "黄金", "白金", "钻石", "大师", "Apex 猎杀者"]


def start(name):
    """某一段的起点，如 start("白金 III")。"""
    return dict((n, s) for s, n in ranks.boundaries())[name]


def config_checks():
    scores = [s for s, _ in ranks.boundaries()]
    diamond = [start(f"钻石 {d}") for d in ranks.DIVISIONS] + [start("大师")]
    return {
        "8 个段位从低到高": ranks.TIERS == TIER_NAMES,
        "分界线从低到高、不重复（猎杀者和大师重合，只画大师）": scores == sorted(set(scores))
        and [n for _, n in ranks.boundaries()].count("大师") == 1
        and not any("猎杀者" in n for _, n in ranks.boundaries()),
        "和实测一致：钻石每段 1000、大师 16,000、钻石入场费 65": all(b - a == 1000 for a, b in zip(diamond, diamond[1:]))
        and start("大师") == 16000 and ranks._BY_NAME["钻石"]["entry_fee"] == 65,
        "和 EA 的 Split 2 重置表一致": (start("白金 III"), start("白金 II"), start("白金 I")) == (9250, 10000, 11000)
        and (start("白银 IV"), start("黄金 IV"), start("白金 IV")) == (3250 - 250, 5750 - 250, 8750 - 250),
        "黄金每段 750": [start(f"黄金 {d}") for d in ranks.DIVISIONS] == [5500, 6250, 7000, 7750],
    }


def score_checks():
    return {
        "钻石 II 964 → 14964（钻石 IV 从 12000 起）": ranks.score("钻石", "II", 964) == 14964,
        "黄金 IV 0 → 5500": ranks.score("黄金", "IV", 0) == 5500,
        "大师不分段：203 当作超出 16000 的部分，16203 当作总分": ranks.score("大师", None, 203) == 16203
        and ranks.score("大师", None, 16203) == 16203,
        "读不全（没有段、没有进度、不认识的段位）返回空": ranks.score("钻石", None, 5) is None
        and ranks.score("钻石", "II", None) is None and ranks.score("翡翠", "II", 5) is None,
        "显示名：钻石 II；大师不写段": ranks.rank_name("钻石", "II") == "钻石 II" and ranks.rank_name("大师", None) == "大师",
    }


def xp_checks():
    cases = {
        "下一段位:钻石I": ("钻石", "II"),
        "下一段位:钻石Ⅳ": ("白金", "I"),
        "下一段位:大师": ("钻石", "I"),
        "下一段位:Apex 猎杀者": ("大师", None),
        "下一段位:apex猎杀者": ("大师", None),
        "下一段位:青铜IV": ("菜鸟", "I"),
        "下一段位:菜鸟IV": None,
        "下一段位:翡翠II": None,
        "下一段位:白金": None,  # 分段的段位读不到段数（"I"很细，常被漏掉）：不猜成黄金 I（2026-10-24 繁体截图）
        "下一段位:白金！": ("白金", "II"),  # "I"读成"！"
    }
    wrong = {t: current_rank(t) for t, want in cases.items() if current_rank(t) != want}
    return {f"经验值页由'下一段位'推当前段位（{len(cases)} 种写法）{'' if not wrong else f'：错了 {wrong}'}": not wrong}


def overview(name, games, lobby=()):
    """新建数据库、存几局（每局：段位、段、段内进度；第 i 局 2i:00 结束）和大厅读数（时间、段位、段、段内进度），返回总览页的 HTML。"""
    path = FOLDER / f"{name}.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)
    for i, (tier, division, progress) in enumerate(games):
        storage.insert_match(conn, {"ended_at": f"2026-10-17 2{i}:00:00", "mode": "ranked", "placement": 5,
                                    "rank_tier": tier, "rank_division": division, "rank_progress": progress})
    for when, tier, division, progress in lobby:
        storage.save_lobby(conn, {"read_at": when, "rank_tier": tier, "rank_division": division,
                                  "rank_progress": progress, "score": ranks.score(tier, division, progress)})
    conn.close()
    panel_app.app.config.update(DB=path, TESTING=True)
    return panel_app.app.test_client().get("/").get_data(as_text=True)


def card_checks():
    html = overview("diamond", [("钻石", "II", 900), ("钻石", "I", 50)])
    card = html.split('class="card rank-card"')[1].split("</section>")[0]
    master = overview("master", [("钻石", "I", 980), ("大师", None, 203)])
    master_card = master.split('class="card rank-card"')[1].split("</section>")[0]
    newer = overview("lobby_newer", [("钻石", "II", 900)], [("2026-10-17 20:30:00", "钻石", "II", 955)])
    newer_card = newer.split('class="card rank-card"')[1].split("</section>")[0]
    older = overview("lobby_older", [("钻石", "II", 900), ("钻石", "II", 930)], [("2026-10-17 20:30:00", "钻石", "II", 955)])
    older_card = older.split('class="card rank-card"')[1].split("</section>")[0]
    return {
        "段位卡片：钻石 I · 50 排位点数": 'class="rank-name">钻石 I<' in card and ">50</span>" in card and "排位点数" in card,
        "段位卡片：没有分母、进度条、距下一段还差": "/ 1000" not in card and "bar-fill" not in card and "还差" not in card,
        "大师：只写段位名，徽章上不写段数": 'class="rank-name">大师<' in master_card and "rank-numeral" not in master_card,
        "大厅读数比最后一局新：段位卡片用大厅的（总分以大厅为准），注明\"大厅\"": ">955</span>" in newer_card and "大厅" in newer_card,
        "最后一局比大厅读数新：用最后一局的": ">930</span>" in older_card and "大厅" not in older_card,
    }


def chart_checks():
    def lines(games):
        rows = [{"rank_tier": t, "rank_division": d, "rank_progress": p} for t, d, p in games]
        return [line["name"] for line in panel_app.rp_chart(rows)["lines"]]

    games = [{"ended_at": f"2026-10-17 2{i}:00:00", "rank_tier": "钻石", "rank_division": "II", "rank_progress": p}
             for i, p in enumerate((900, 930))]
    chart = panel_app.rp_chart(games, [{"read_at": "2026-10-17 21:30:00", "score": 14955}])
    with_lobby = (len(chart["line"].split()), chart["count"])
    return {
        "走势图分界线按配置画（黄金 IV 700 → 黄金 III 100，中间是黄金 III 的 6250）": "黄金 III" in lines(
            [("黄金", "IV", 700), ("黄金", "III", 100)]),
        "快升段时画出下一段（钻石 I 900 → 950，画出大师）": "大师" in lines([("钻石", "I", 900), ("钻石", "I", 950)]),
        "离下一段远时不画（钻石 IV 100 → 150，不画钻石 III）": "钻石 III" not in lines(
            [("钻石", "IV", 100), ("钻石", "IV", 150)]),
        "最后一局之后又回了大厅、分数不一样：接在折线最后，\"N 局前\"仍按局数算": with_lobby == (3, 2),
    }


def main():
    print("段位（分数线、经验值页、段位卡片、走势图）")
    checks = {}
    for part in (config_checks, score_checks, xp_checks, card_checks, chart_checks):
        checks.update(part())
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
