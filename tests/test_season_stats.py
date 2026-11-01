"""游戏里的数据页（season_stats.py）：认出这一页、读右边一栏（选中的赛季）和中间一栏（生涯合计）、数字互相核对。

- 截图：用户 2026-11-01 截的（captures/frames/20261101_161135，2560x1440，简体；游戏全屏 2560x1600 = 16:10 拉伸，
  和实际打游戏一样按 16:10 换算（layout.set_game_aspect）：第一版没换算就测，测试过了、实际一张都认不出），第 30–115 张：大厅、好友列表、
  数据页（下拉菜单一会儿开一会儿收，点了 S30 到 S6）。答案在 season_stats_20261101.csv（程序读出来以后逐项核对过：
  伤害 ÷ 局数 = 平均、击杀 ÷ 阵亡 = 击杀/阵亡比全部对得上，下面六行对着原图看过 S30 排位、S29、S16、S14 四张）。
  被鼠标弹出的说明框挡住的 5 张只核对"挡住了"，不对数字
  - 下拉菜单开着的、别的画面：都不算数据页
  - 数据页：玩家名、赛季、全部 / 排位、赛季名、15 个数都对，核对没有问题
  - 中间一栏（生涯合计）：局数游戏只写"10.1K"，用伤害 ÷ 平均伤害算出 10,146；前 5 名"2.82K"算不出，记约数
  - 同一张读第二遍（同一个 memo）：一次都不识别
  找不到截图时这部分跳过
- 不用截图：parse_number 读"1.03K"这类约数；check 找出对不上的地方

运行：python tests/test_season_stats.py
"""

import csv
from pathlib import Path

from fake_game import SHOTS, report  # 要在项目代码之前导入（设置临时数据文件夹）

from season_stats import FIELDS, check, parse_number

FOLDER = SHOTS / "frames" / "20261101_161135"
ANSWERS = Path(__file__).with_name("season_stats_20261101.csv")
CAREER_FRAMES = ("0038_161232_2560x1440.jpg", "0071_161345_2560x1440.jpg", "0101_161441_2560x1440.jpg")
CAREER = {"games": 10146, "wins": 606, "top5": 2820, "damage": 4678720, "damage_max": 4902, "damage_avg": 461.14,
          "kills": 10256, "deaths": 10920, "kd": 0.94, "kills_max": 19, "knockdowns": 13685, "assists": 9778,
          "win_streak": 4, "revives": 2653, "respawns": 1016}


def number(text):
    return float(text) if "." in text else int(text)


def rule_checks():
    out = {}
    out["\"4,678,720\" → 4678720；\"537.59\" → 537.59；\"0\" → 0"] = (
        parse_number("4,678,720") == (4678720, None) and parse_number("537.59") == (537.59, None)
        and parse_number("0") == (0, None))
    out["\"1.03K\" → 约 1030（1025–1035）；\"10.1K\" → 约 10100（10050–10150）"] = (
        parse_number("1.03K") == (1030, (1025, 1035)) and parse_number("10.1K") == (10100, (10050, 10150)))
    good = {"games": 315, "wins": 26, "top5": 81, "damage": 169379, "damage_max": 2751, "damage_avg": 537.71,
            "kills": 429, "deaths": 353, "kd": 1.22, "kills_max": 10, "knockdowns": 557, "assists": 426,
            "win_streak": 4, "revives": 127, "respawns": 73}
    out["对得上的一栏（S29 全部）：没有问题"] = check(good, {})[2] == []
    _, _, avg = check({**good, "damage_avg": 573.71}, {})
    out[f"平均伤害读错：伤害 ÷ 局数对不上（{avg}）"] = len(avg) == 1 and "平均伤害" in avg[0]
    _, _, kd = check({**good, "kd": 1.32}, {})
    out[f"击杀/阵亡比读错：对不上（{kd}）"] = len(kd) == 1 and "击杀/阵亡比" in kd[0]
    _, _, order = check({**good, "wins": 90}, {})
    out[f"胜利比前 5 名还多：标出来（{order}）"] = any("胜利" in p for p in order)
    fixed, rough, ok = check({**good, "games": 1030, "damage": 444787, "damage_avg": 432.25}, {"games": (1025, 1035)})
    out["局数是约数\"1.03K\"：用伤害 ÷ 平均伤害算出 1029，不再是约数"] = fixed["games"] == 1029 and "games" not in rough
    _, _, far = check({**good, "games": 2000, "damage": 444787, "damage_avg": 432.25}, {"games": (1995, 2005)})
    out[f"算出来不在约数的范围里：对不上（{far}）"] = any("约数" in p for p in far)
    _, _, missing = check({k: v for k, v in good.items() if k != "assists"}, {})
    out[f"缺一项：写明没读到什么（{missing}）"] = missing == ["没读到：助攻"]
    return out


def screenshot_checks():
    """返回检查结果；没有截图返回 None。"""
    if not FOLDER.is_dir():
        return None
    from ocr import SMALL_CROP_PARAMS, create_engine, load_image  # 用到截图才加载识别引擎
    from season_stats import SeasonPage
    import layout

    layout.set_game_aspect(16 / 10)
    try:
        return _screenshot_checks(SeasonPage, create_engine(SMALL_CROP_PARAMS), load_image)
    finally:
        layout.set_game_aspect(None)


def _screenshot_checks(SeasonPage, real, load_image):

    calls = [0]

    def engine(img, **kwargs):
        calls[0] += 1
        return real(img, **kwargs)

    page = SeasonPage(engine)
    with ANSWERS.open(encoding="utf-8-sig") as f:
        answers = list(csv.DictReader(f))
    wrong, pages, covered, names = [], 0, 0, set()
    for want in answers:
        frame = load_image(FOLDER / want["frame"])
        on = page.on_page(frame)
        if on != (want["page"] == "1"):
            wrong.append(f"{want['frame']}：{'认成了' if on else '没认出'}数据页")
            continue
        if not on:
            continue
        pages += 1
        names.add(page.name(frame))
        season, kind, season_name, raw = page.label(frame)
        if (str(season), kind, season_name) != (want["season"], want["kind"], want["season_name"]):
            wrong.append(f"{want['frame']}：赛季读成 {raw}")
        reading = page.read(frame)
        if reading["obscured"] != (want["obscured"] == "1"):
            wrong.append(f"{want['frame']}：{'不该' if reading['obscured'] else '应该'}算被挡住")
            continue
        if reading["obscured"]:
            covered += 1
            continue
        values, approx, problems = check(reading["values"], reading["approx"])
        bad = [f"{k} {values.get(k)}≠{want[k]}" for k in FIELDS if values.get(k) != number(want[k])]
        if bad or problems or approx:
            wrong.append(f"{want['frame']}：{'；'.join(bad + problems)}{'（还有约数）' if approx else ''}")

    career = []
    for name in CAREER_FRAMES:
        reading = page.read(load_image(FOLDER / name), "career")
        career.append(check(reading["values"], reading["approx"]))
    frame = load_image(FOLDER / CAREER_FRAMES[0])
    memo = {}
    page.read(frame, memo=memo)
    before = calls[0]
    again = page.read(frame, memo=memo)
    second = calls[0] - before
    return {
        f"{len(answers)} 张截图：数据页（下拉菜单收着）{pages} 张都认出来了，菜单开着的、别的画面一张都没认成"
        f"；{pages - covered} 张赛季、15 个数全对，核对没问题；被说明框挡住的 {covered} 张认出来了"
        f"（错的：{wrong or '无'}）": not wrong and pages == 38 and covered == 5,
        f"页面上的玩家名都读成 ImperialHzd（{names}）": names == {"ImperialHzd"},
        "生涯合计：局数用伤害 ÷ 平均伤害算出 10,146，其他 14 项都对，没有问题（前 5 名只有约数 2.82K）":
            all(values == CAREER and set(approx) == {"top5"} and approx["top5"] == (2815, 2825) and not problems
                for values, approx, problems in career),
        f"同一张读第二遍（同一个 memo）：一次都不识别，读到的一样（{second} 次）": second == 0
            and again["values"] == page.read(frame)["values"],
    }


def main():
    print("游戏里的数据页（录入历史赛季数据）")
    checks = rule_checks()
    shots = screenshot_checks()
    if shots is None:
        print(f"  ⏭ 截图部分跳过：找不到 {FOLDER}（截图只在开发者电脑上，不在仓库里）")
    else:
        checks.update(shots)
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
