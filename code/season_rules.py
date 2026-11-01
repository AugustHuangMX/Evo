"""历史赛季数据的 15 个数：叫什么、游戏里的约数（"1.03K"）怎么读、互相怎么核对、页面上怎么算比例。

识别（season_stats.py）和"赛季"页面（panel_seasons.py）都用。这个文件不加载识别用的库（面板也要用）。
"""

import re

FIELDS = ("games", "wins", "top5", "damage", "damage_max", "damage_avg", "kills", "deaths", "kd",
          "kills_max", "knockdowns", "assists", "win_streak", "revives", "respawns")
LABELS = {"games": "比赛", "wins": "胜利", "top5": "前 5 名", "damage": "伤害", "damage_max": "最高伤害",
          "damage_avg": "平均伤害", "kills": "击杀", "deaths": "阵亡", "kd": "击杀/阵亡比", "kills_max": "最高击杀",
          "knockdowns": "击倒", "assists": "助攻", "win_streak": "最长连胜", "revives": "急救队友", "respawns": "重生队友"}
DECIMALS = ("damage_avg", "kd")  # 游戏里带两位小数的，其余都是整数
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?K?")
KIND_ALL, KIND_RANKED, KIND_CAREER = "all", "ranked", "career"


def parse_number(text):
    """"4,678,720" → (4678720, None)；"537.59" → (537.59, None)；"1.03K" → (1030, (1025, 1035))：
    第二项是约数的范围（游戏把 1000 以上的写成几 K，只留三位有效数字），不是约数为 None。读不出返回 (None, None)。"""
    m = NUMBER.search(text.replace("，", ","))
    if not m:
        return None, None
    raw = m[0].replace(",", "")
    if raw.endswith("K"):
        digits = raw[:-1]
        step = 10 ** -len(digits.split(".")[1]) if "." in digits else 1
        value = float(digits)
        return round(value * 1000), (round((value - step / 2) * 1000), round((value + step / 2) * 1000))
    return (float(raw) if "." in raw else int(raw)), None


def issues(values, approx):
    """一栏 15 个数里对不上的地方：[(牵涉到的项, 一句话)]。approx：游戏只写了约数的项（{项: 范围} 或项的集合），
    约数不拿来比大小。局数是约数时不在这里核对（见 check）。"""
    out = []
    v = values.get
    missing = [f for f in FIELDS if v(f) is None]
    if missing:
        out.append((tuple(missing), f"没读到：{'、'.join(LABELS[f] for f in missing)}"))
    if v("games") and v("damage") is not None and v("damage_avg") is not None and "games" not in approx:
        if abs(v("damage") / v("games") - v("damage_avg")) > 0.006:
            out.append((("games", "damage", "damage_avg"),
                        f"伤害 {v('damage')} ÷ 比赛 {v('games')} = {v('damage') / v('games'):.2f}，"
                        f"和平均伤害 {v('damage_avg')} 对不上"))
    if v("deaths") and v("kills") is not None and v("kd") is not None:
        if abs(v("kills") / v("deaths") - v("kd")) > 0.006:
            out.append((("kills", "deaths", "kd"),
                        f"击杀 {v('kills')} ÷ 阵亡 {v('deaths')} = {v('kills') / v('deaths'):.2f}，"
                        f"和击杀/阵亡比 {v('kd')} 对不上"))
    for small, big in (("wins", "top5"), ("top5", "games"), ("kills_max", "kills"), ("damage_max", "damage"),
                       ("win_streak", "wins")):
        if v(small) is not None and v(big) is not None and small not in approx and big not in approx \
                and v(small) > v(big):
            out.append(((small, big), f"{LABELS[small]} {v(small)} 比{LABELS[big]} {v(big)} 还多"))
    return out


def check(values, approx):
    """核对一栏的 15 个数，局数是约数时用伤害 ÷ 平均伤害算出准确的（平均伤害有两位小数，够准）。
    返回 (核对后的 values, approx, 问题列表)。问题是给人看的一句话。"""
    values, approx, problems = dict(values), dict(approx), []
    v = values.get
    if "games" in approx and v("damage") is not None and v("damage_avg"):
        games = round(v("damage") / v("damage_avg"))
        low, high = approx["games"]
        if low <= games < high:
            values["games"] = games
            approx.pop("games")
        else:
            problems.append(f"比赛 {v('games')}（约数）和伤害 ÷ 平均伤害 = {games} 对不上")
    return values, approx, [text for _, text in issues(values, approx)] + problems


def empty(values):
    """这个赛季一局都没打（游戏里全是 0）。"""
    return values.get("games") == 0


def show(values, approx):
    """一行字：比赛 821 · 胜利 47 · …（约数后面加"（约）"）。"""
    return " · ".join(f"{LABELS[f]} {values[f]:,}{'（约）' if f in approx else ''}" for f in FIELDS if f in values)


def rates(values):
    """"赛季"页面表格里的几列（算不出为 None）：胜率、前 5 率（百分数）、场均击杀、击杀/阵亡比、场均伤害。"""
    games = values.get("games")

    def per(field, scale=1):
        x = values.get(field)
        return x / games * scale if games and x is not None else None

    return {"win_rate": per("wins", 100), "top5_rate": per("top5", 100), "kills_per_game": per("kills"),
            "kd": values.get("kd"), "damage_avg": values.get("damage_avg")}
