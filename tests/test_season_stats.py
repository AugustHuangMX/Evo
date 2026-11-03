"""游戏里的数据页（season_stats.py）：认出这一页、读右边一栏（选中的赛季）和中间一栏（生涯合计）、数字互相核对。

- 截图（找不到的那份跳过），和实际打游戏一样按游戏的比例换算（layout.set_game_aspect：第一版没换算就测，测试过了、
  实际一张都认不出）。答案是程序读出来以后逐项核对过的：伤害 ÷ 局数 = 平均、击杀 ÷ 阵亡 = 击杀/阵亡比全部对得上，
  别的对着原图看过几张。被鼠标弹出的说明框挡住的只核对"挡住了"，不对数字
  - 用户 2026-11-01 截的（captures/frames/20261101_161135，2560x1440，简体；游戏全屏 2560x1600 = 16:10 拉伸），
    第 30–115 张：大厅、好友列表、数据页（下拉菜单一会儿开一会儿收，点了 S30 到 S6）。答案 season_stats_20261101.csv
    （下面六行对着原图看过 S30 排位、S29、S16、S14 四张）。第 41 张鼠标停在下拉框上，弹出的提示挡住了左上角，
    靠右上角认出来（2026-11-03 起；以前当作不是数据页）
  - 两位测试员 2026-11-03 用测试工具截的（captures/testers/，简体；答案里没有玩家名，只核对名字每张都一样、没被截掉）：
    20261103_seasons_4x3（1920x1080 显示器，游戏 1920x1440 = 4:3 拉伸，S30–S14；"击杀/阵亡比"有几张折成两行）、
    20261103_seasons_16x10（2560x1440 显示器，游戏 1920x1200 = 16:10 拉伸，S30–S2，玩家名 19 个字；
    "1,495"读成过"1.495"、1015 局写"1.01K"、全是 0 的赛季、鼠标停在下拉框上的几张）。答案 season_stats_20261103_*.csv
    （下面六行对着原图看过 4:3 的 S30 两项、S20、S14 排位，16:10 的 S2 排位、S14 排位、S22、S26）。
    16:10 最后一张测试工具的窗口盖住了左边的名字：答案里空着的三项必须是"没读到"，不能读成别的数
  - 下拉菜单开着的、别的画面：都不算数据页
  - 数据页：赛季、全部 / 排位、赛季名、15 个数都对，核对没有问题
  - 中间一栏（生涯合计）：局数游戏只写"10.1K"这种，用伤害 ÷ 平均伤害算出准确的；前 5 名算不出，记约数。
    鼠标停在生涯那一栏弹出说明框的，算被挡住
  - 同一张读第二遍（同一个 memo）：一次都不识别
- 不用截图：parse_number 读"1.03K"这类约数、整数里的"1.495"；check 找出对不上的地方

运行：python tests/test_season_stats.py
"""

import csv
from pathlib import Path

from fake_game import SHOTS, report  # 要在项目代码之前导入（设置临时数据文件夹）

from season_stats import FIELDS, LABELS, check, parse_number

SETS = (
    {"label": "用户 11-01（16:10）", "folder": SHOTS / "frames" / "20261101_161135",
     "answers": "season_stats_20261101.csv", "aspect": 16 / 10, "pages": 39, "covered": 6, "player": "ImperialHzd",
     "career_frames": ("0038_161232_2560x1440.jpg", "0071_161345_2560x1440.jpg", "0101_161441_2560x1440.jpg"),
     "career": {"games": 10146, "wins": 606, "top5": 2820, "damage": 4678720, "damage_max": 4902,
                "damage_avg": 461.14, "kills": 10256, "deaths": 10920, "kd": 0.94, "kills_max": 19,
                "knockdowns": 13685, "assists": 9778, "win_streak": 4, "revives": 2653, "respawns": 1016},
     "top5": (2815, 2825), "career_covered": ()},
    {"label": "测试员 11-03（4:3）", "folder": SHOTS / "testers" / "20261103_seasons_4x3" / "pages",
     "answers": "season_stats_20261103_4x3.csv", "aspect": 4 / 3, "pages": 34, "covered": 0, "name_length": 15,
     "career_frames": ("0001_170920_1920x1080.jpg", "0020_171045_1920x1080.jpg", "0039_171227_1920x1080.jpg"),
     "career": {"games": 6410, "wins": 393, "top5": 1880, "damage": 3514970, "damage_max": 4856,
                "damage_avg": 548.36, "kills": 9432, "deaths": 8411, "kd": 1.12, "kills_max": 25,
                "knockdowns": 11898, "assists": 6888, "win_streak": 3, "revives": 2462, "respawns": 453},
     "top5": (1875, 1885), "career_covered": ("0018_171035_1920x1080.jpg",)},
    {"label": "测试员 11-03（16:10）", "folder": SHOTS / "testers" / "20261103_seasons_16x10" / "pages",
     "answers": "season_stats_20261103_16x10.csv", "aspect": 16 / 10, "pages": 62, "covered": 2, "name_length": 19,
     "career_frames": ("0001_170413_2560x1440.jpg", "0042_170632_2560x1440.jpg", "0070_170823_2560x1440.jpg"),
     "career": {"games": 19899, "wins": 621, "top5": 3350, "damage": 9107772, "damage_max": 4188,
                "damage_avg": 457.7, "kills": 21680, "deaths": 21156, "kd": 1.02, "kills_max": 19,
                "knockdowns": 28092, "assists": 11481, "win_streak": 3, "revives": 4771, "respawns": 1495},
     "top5": (3345, 3355), "career_covered": ("0014_170445_2560x1440.jpg",)},
)


def number(text):
    return float(text) if "." in text else int(text)


def rule_checks():
    out = {}
    out["\"4,678,720\" → 4678720；\"537.59\" → 537.59；\"0\" → 0"] = (
        parse_number("4,678,720") == (4678720, None) and parse_number("537.59") == (537.59, None)
        and parse_number("0") == (0, None))
    out["\"1.03K\" → 约 1030（1025–1035）；\"10.1K\" → 约 10100（10050–10150）"] = (
        parse_number("1.03K") == (1030, (1025, 1035)) and parse_number("10.1K") == (10100, (10050, 10150)))
    out["整数项里逗号读成点：\"1.495\" → 1495、\"1.234.567\" → 1234567，\"1.5\" 不动；带小数的项照旧（\"1.495\" → 1.495）"] = (
        parse_number("1.495", integer=True) == (1495, None) and parse_number("1.234.567", integer=True)[0] == 1234567
        and parse_number("1.5", integer=True) == (1.5, None) and parse_number("1.495") == (1.495, None))
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
    fixed, _, edge = check({**good, "games": 1010, "damage": 527829, "damage_avg": 520.03}, {"games": (1005, 1015)})
    out[f"算出来 1015、游戏写\"1.01K\"（范围两头都算）：没有问题（{edge}）"] = fixed["games"] == 1015 and edge == []
    _, _, far = check({**good, "games": 2000, "damage": 444787, "damage_avg": 432.25}, {"games": (1995, 2005)})
    out[f"算出来不在约数的范围里：对不上（{far}）"] = any("约数" in p for p in far)
    _, _, missing = check({k: v for k, v in good.items() if k != "assists"}, {})
    out[f"缺一项：写明没读到什么（{missing}）"] = missing == ["没读到：助攻"]
    return out


def screenshot_checks():
    """返回 (检查结果, 跳过的说明)；一份截图都没有时检查结果为 None。"""
    sets = [spec for spec in SETS if spec["folder"].is_dir()]
    skipped = [f"{spec['label']}：找不到 {spec['folder']}" for spec in SETS if spec not in sets]
    if not sets:
        return None, skipped
    from ocr import SMALL_CROP_PARAMS, create_engine, load_image  # 用到截图才加载识别引擎
    from season_stats import SeasonPage
    import layout

    real = create_engine(SMALL_CROP_PARAMS)
    out = {}
    for spec in sets:
        layout.set_game_aspect(spec["aspect"])
        try:
            out.update(_screenshot_checks(spec, SeasonPage, real, load_image))
        finally:
            layout.set_game_aspect(None)
    return out, skipped


def _screenshot_checks(spec, SeasonPage, real, load_image):
    calls = [0]

    def engine(img, **kwargs):
        calls[0] += 1
        return real(img, **kwargs)

    folder, label = spec["folder"], spec["label"]
    page = SeasonPage(engine)
    with Path(__file__).with_name(spec["answers"]).open(encoding="utf-8-sig") as f:
        answers = list(csv.DictReader(f))
    wrong, pages, covered, names = [], 0, 0, set()
    for want in answers:
        frame = load_image(folder / want["frame"])
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
        blank = [k for k in FIELDS if not want[k]]  # 答案空着的：被别的窗口挡住了，必须是"没读到"
        if blank:
            problems = [p for p in problems if p != f"没读到：{'、'.join(LABELS[k] for k in blank)}"]
        bad = [f"{k} {values.get(k)}≠{want[k] or '没读到'}" for k in FIELDS
               if values.get(k) != (number(want[k]) if want[k] else None)]
        if bad or problems or approx:
            wrong.append(f"{want['frame']}：{'；'.join(bad + problems)}{'（还有约数）' if approx else ''}")

    career = []
    for name in spec["career_frames"]:
        reading = page.read(load_image(folder / name), "career")
        career.append(check(reading["values"], reading["approx"]))
    career_covered = [page.read(load_image(folder / name), "career")["obscured"] for name in spec["career_covered"]]
    if "player" in spec:
        name_ok, name_text = names == {spec["player"]}, f"都读成 {spec['player']}（{names}）"
    else:
        name_ok = len(names) == 1 and all(n and len(n) == spec["name_length"] for n in names)
        name_text = (f"每张都一样、{spec['name_length']} 个字没被截掉"
                     f"（读到 {len(names)} 种，长 {[len(n or '') for n in names]}）")
    out = {
        f"{label}：{len(answers)} 张截图：数据页（下拉菜单收着）{pages} 张都认出来了，菜单开着的、别的画面一张都没认成"
        f"；{pages - covered} 张赛季、15 个数全对，核对没问题；被说明框挡住的 {covered} 张认出来了"
        f"（错的：{wrong or '无'}）": not wrong and pages == spec["pages"] and covered == spec["covered"],
        f"{label}：页面上的玩家名{name_text}": name_ok,
        f"{label}：生涯合计 {len(career)} 张：局数用伤害 ÷ 平均伤害算出 {spec['career']['games']:,}，其他 14 项都对，"
        f"没有问题（前 5 名只有约数）；鼠标停在生涯那一栏的 {len(career_covered)} 张算被挡住":
            all(values == spec["career"] and set(approx) == {"top5"} and approx["top5"] == spec["top5"]
                and not problems for values, approx, problems in career) and all(career_covered),
    }
    if spec is SETS[0]:
        frame = load_image(folder / spec["career_frames"][0])
        memo = {}
        page.read(frame, memo=memo)
        before = calls[0]
        again = page.read(frame, memo=memo)
        second = calls[0] - before
        out[f"同一张读第二遍（同一个 memo）：一次都不识别，读到的一样（{second} 次）"] = \
            second == 0 and again["values"] == page.read(frame)["values"]
    return out


def main():
    print("游戏里的数据页（录入历史赛季数据）")
    checks = rule_checks()
    shots, skipped = screenshot_checks()
    for text in skipped:
        print(f"  ⏭ 截图跳过：{text}（截图只在开发者电脑上，不在仓库里）")
    if shots is not None:
        checks.update(shots)
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
