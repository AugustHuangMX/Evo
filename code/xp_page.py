"""识别经验值页（RP 明细）：本局 RP 变化、名次、计分的击杀/助攻次数、本局结束后的段位和段内进度。

RP 的算法：战斗合计 + 奖励合计 + 定级合计（定级合计已减去参赛花费）。
核对：三个合计之和应等于顶部的大数字；定级合计应等于定级分 + 参赛花费。对不上就标记"待确认"
（多半是动画还没播完：合计、参赛花费那几行会晚一点出现，顶部数字也在跳）。

注意：这里的击杀/助攻是"计入排位点数"的次数。同一名玩家被击杀两次只算一次，所以可能比总结页少，
不能拿来覆盖总结页的数据，只在没有总结页的局（第 2 名、冠军）里使用。

用法：python code/xp_page.py 截图1.png [截图2.png ...]
"""

import argparse
import re
from pathlib import Path

import game_lang
import ranks
from ocr import SMALL_CROP_PARAMS, create_engine, crop, load_config, load_image, read_lines

CONFIG_NAME = "xp.json"
ROMAN = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "1": "I", "l": "I", "|": "I", "!": "I", "！": "I"}


def normalize(text):
    return text.replace("−", "-").replace("：", ":").strip()


def read_text(engine, img, spec, cfg):
    """识别一个小块，返回 (文字, 置信度)。小块里有几段时取置信度最高的一段；
    配置里 join_lines 为真时（英文：次数和分数、"#9"和"10 RP"隔得远，会读成几段）从左到右拼起来，置信度取最低的。"""
    if cfg.get("join_lines"):
        result = engine(crop(img, spec["roi"], cfg, spec.get("anchor")))
        if not result.txts:
            return "", None
        parts = sorted(zip(result.boxes, result.txts, result.scores), key=lambda p: float(p[0][:, 0].min()))
        return normalize(" ".join(t for _, t, _ in parts)), min(s for _, _, s in parts)
    lines = read_lines(engine, img, spec["roi"], cfg, spec.get("anchor"))
    if not lines:
        return "", None
    text, score = max(lines, key=lambda x: x[1])
    return normalize(text), score


def split_count_points(digits):
    """次数和分数连在一起时（如 "18" = 1 次 8 分），找出唯一合理的拆法：分数 = 次数 × 每次的分值（1–40）。"""
    options = []
    for i in range(1, len(digits)):
        count, points = int(digits[:i]), int(digits[i:])
        if (count == 0 and points == 0) or (count > 0 and points % count == 0 and 1 <= points // count <= 40):
            options.append((count, points))
    return options[0] if len(options) == 1 else None


def current_rank(next_text, names=None):
    """由"下一段位：钻石 I"推出当前段位（钻石, II）；"下一段位：大师" → (钻石, I)；
    "下一段位：Apex 猎杀者" → (大师, None)（大师不分段）。段位名和顺序见 config/ranks.json。读不懂返回 None。
    names：画面上的段位名 → 简体段位名（ranks.tier_names，别的语言如 "Diamond"）；不给就是简体。"""
    name = next_text.split(":")[-1].replace(" ", "")
    tiers, divisions = ranks.TIERS, ranks.DIVISIONS
    names = names or ranks.tier_names()
    keys = {shown: shown.replace(" ", "").lower() for shown in names}  # 比较时不管空格和大小写（"Apex猎杀者"）
    shown = next((s for s in sorted(names, key=lambda s: len(keys[s]), reverse=True)
                  if name.lower().startswith(keys[s])), None)
    if shown is None:
        return None
    tier = names[shown]
    roman = "".join(ROMAN.get(c, c) for c in name[len(keys[shown]):])
    t = tiers.index(tier)

    def top_of_previous():  # 当前是上一个段位的最高段（不分段的段位没有段）
        if t == 0:
            return None
        prev = tiers[t - 1]
        return prev, (divisions[-1] if ranks.has_divisions(prev) else None)

    if roman == "":
        if ranks.has_divisions(tier):  # 分段的段位却没读到段数（"I"很细，常被漏掉）：不猜，留空（回大厅时用大厅的段位补）
            return None
        return top_of_previous()  # 下一段位不分段（如大师）
    if roman not in divisions:
        return None
    d = divisions.index(roman)
    return (tier, divisions[d - 1]) if d > 0 else top_of_previous()


def read_xp(engine, img, cfg):
    """识别一张经验值页。返回 (结果, 问题列表)。结果里读不出的项为 None。"""
    raw = {}
    for name, spec in cfg["fields"].items():
        text, score = read_text(engine, img, spec, cfg)
        if name != "next_rank":  # 数字那几行修正常见误读（英文把"0 RP"读成"O RP"）；段位名不改（"BRONZE"里有 O）
            for wrong, right in cfg.get("replace", {}).items():
                text = text.replace(wrong, right)
        raw[name] = text if score is not None and score >= cfg["min_score"] else ""
    patterns = cfg["patterns"]
    glued = cfg.get("split_glued", True)  # 次数和分数连在一起时（"00排位点数"）去拆；英文中间隔得远，不会连在一起，不拆

    def points(name):
        m = re.match(patterns["points"], raw[name])
        return int(m[1]) if m else None

    def count_points(name):
        m = re.match(patterns["count_points"], raw[name])
        if not m:
            return None
        if m[2]:
            return int(m[1]), int(m[2])
        return split_count_points(m[1]) if glued else None

    combat, bonus, placement_total = points("combat_total"), points("bonus_total"), points("placement_total")
    fee = points("entry_fee")
    m = re.fullmatch(r"[+-]?\d+", raw["rp_shown"])
    rp_shown = int(m[0]) if m else None
    progress = points("progress")

    # 定级（名次）：通常是"第13 5排位点数"；连在一起时（如"第820"）用 定级合计 − 参赛花费 拆出定级分
    placement = placement_points = None
    m = re.match(patterns["placement_points"], raw["placement"])
    if m and m[2]:
        placement, placement_points = int(m[1]), int(m[2])
    elif m and glued and placement_total is not None and fee is not None:
        expected = str(placement_total - fee)
        if m[1].endswith(expected) and len(m[1]) > len(expected):
            placement, placement_points = int(m[1][:-len(expected)]), int(expected)
    if placement is not None and not 1 <= placement <= 20:
        placement = placement_points = None

    problems = []
    if placement_total is None and placement_points is not None and fee is not None:
        placement_total = placement_points + fee  # 定级合计那一栏还没出现，用明细算
    elif None not in (placement_total, placement_points, fee) and placement_total != placement_points + fee:
        problems.append(f"定级合计 {placement_total} ≠ 定级分 {placement_points} + 参赛花费 {fee}")

    rp = None
    if None in (combat, bonus, placement_total):
        problems.append("RP：明细没读全（可能动画还没播完）")
    else:
        rp = combat + bonus + placement_total
        if rp_shown is None:
            problems.append(f"RP：顶部数字没读到，无法核对明细合计 {rp:+d}")
        elif rp_shown != rp:
            problems.append(f"RP：顶部数字 {rp_shown:+d} 与明细合计 {rp:+d} 不一致（可能动画还没播完）")
    if placement is None:
        problems.append(f"名次：没读到（OCR 原文：{raw['placement'] or '无'}）")

    rank = current_rank(raw["next_rank"], ranks.tier_names(cfg.get("language"))) if raw["next_rank"] else None
    kills, assists = count_points("kills"), count_points("assists")
    result = {
        "rp_delta": rp,
        "rp_shown": rp_shown,
        "placement": placement,
        "kills": kills[0] if kills else None,
        "assists": assists[0] if assists else None,
        "rank_tier": rank[0] if rank else None,
        "rank_division": rank[1] if rank else None,
        "rank_progress": progress if rank else None,
        "raw": raw,
    }
    return result, problems


def show(value):
    return "-" if value is None else str(value)


def main():
    parser = argparse.ArgumentParser(description="识别经验值页截图")
    parser.add_argument("images", type=Path, nargs="+", help="经验值页截图")
    parser.add_argument("--raw", action="store_true", help="同时打印每个小块读到的原文")
    parser.add_argument("--lang", choices=game_lang.languages(), default=game_lang.DEFAULT,
                        help="游戏语言（默认简体中文 zh-CN）")
    args = parser.parse_args()

    cfg = load_config(CONFIG_NAME, args.lang)
    engine = create_engine(SMALL_CROP_PARAMS)
    for path in args.images:
        r, problems = read_xp(engine, load_image(path), cfg)
        rp = "-" if r["rp_delta"] is None else f"{r['rp_delta']:+d}"
        rank = f"{ranks.rank_name(r['rank_tier'], r['rank_division'])} {r['rank_progress']}" if r["rank_tier"] else "-"
        print(f"{path.name:36s} RP {rp:>5s}  第 {show(r['placement']):>2s} 名  "
              f"计分击杀 {show(r['kills'])}  计分助攻 {show(r['assists'])}  段位 {rank}")
        for p in problems:
            print(f"{'':36s} ⚠ {p}")
        if args.raw:
            print(f"{'':36s} {r['raw']}")


if __name__ == "__main__":
    main()
