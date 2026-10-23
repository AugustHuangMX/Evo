"""排位段位（规则在 config/ranks.json）：段位名、哪些段位分段、把"段位 + 段 + 段内进度"换算成一条连续的分数线。

面板（走势图、段位卡片）和经验值页识别（由"下一段位"推当前段位）都从这里取，不各写一份。
只用标准库：面板和托盘程序也会导入它，不能带上识别引擎（见 tests/test_light.py）。
"""

import json
from functools import lru_cache

import game_lang
from paths import CONFIG_DIR

_CFG = json.loads((CONFIG_DIR / "ranks.json").read_text(encoding="utf-8"))
DIVISIONS = _CFG["divisions"]  # 每个段位里的段从低到高：IV → I
TIERS = [t["name"] for t in _CFG["tiers"]]  # 段位从低到高（简体，存进数据库、面板上显示的就是这些名字）
_BY_NAME = {t["name"]: t for t in _CFG["tiers"]}


@lru_cache(maxsize=None)
def tier_names(lang=None):
    """游戏画面上的段位名 → 简体段位名（读大厅、经验值页用）。简体就是段位名本身；
    别的语言写在 config/lang/<语言>.json 的 "ranks.json" → "names"（如 {"Diamond": "钻石"}）。"""
    if lang in (None, game_lang.DEFAULT):
        return {t: t for t in TIERS}
    names = game_lang.load("ranks.json", lang).get("names")
    if not names:
        raise ValueError(f"config/lang/{lang}.json 里没有段位名（ranks.json → names）")
    return game_lang.aliases(names)


def has_divisions(tier):
    """这个段位分不分段（大师、猎杀者不分）。"""
    return "starts" in _BY_NAME[tier]


def rank_name(tier, division):
    """面板上显示的段位名：钻石 II；不分段的只写段位名（大师）。"""
    return f"{tier} {division}" if division and has_divisions(tier) else tier


def score(tier, division, progress):
    """换算成连续的分数（用来画走势图）：这一段的起点 + 段内进度。读不全或对不上返回 None。
    不分段的段位（大师）：进度比起点小，就当作超出起点的部分；否则当作总分（还没见过样本，见 config/ranks.json）。"""
    if tier not in _BY_NAME or progress is None:
        return None
    t = _BY_NAME[tier]
    if not has_divisions(tier):
        return progress if progress >= t["start"] else t["start"] + progress
    if division not in DIVISIONS:
        return None
    return t["starts"][DIVISIONS.index(division)] + progress


def boundaries():
    """分数线上每一段的起点，从低到高：[(分数, "钻石 IV"), ..., (16000, "大师")]。
    几个段位起点相同时（大师和猎杀者）只留低的那个。"""
    out = []
    for t in _CFG["tiers"]:
        if has_divisions(t["name"]):
            out += [(s, rank_name(t["name"], d)) for s, d in zip(t["starts"], DIVISIONS)]
        else:
            out.append((t["start"], t["name"]))
    seen, unique = set(), []
    for s, name in out:
        if s not in seen:
            seen.add(s)
            unique.append((s, name))
    return sorted(unique)
