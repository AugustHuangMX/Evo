"""赛季（规则在 config/seasons.json）：每个赛季从什么时候开始，每局、每次大厅读数归到哪个赛季。

面板的"本赛季 / 上赛季"（总览页、数据页）和战绩页的赛季筛选用（2026-11-03 用户决定：新赛季从 0 开始算）。
数据库里的时间是玩家电脑上的时间（"2026-11-03 19:52:24"），配置里的开赛时刻是 UTC，换成电脑上的时间再比。
只用标准库：面板会导入它（见 tests/test_light.py）。
"""

import json
from datetime import datetime

from paths import CONFIG_DIR

STAMP = "%Y-%m-%d %H:%M:%S"  # 数据库里时间的写法


def _local(utc_text):
    """"2026-11-03T18:00:00Z" → 电脑上的时间，写成数据库里的样子（"2026-11-03 19:00:00"）。"""
    moment = datetime.fromisoformat(utc_text.replace("Z", "+00:00"))
    return moment.astimezone().strftime(STAMP)


def load(path=None):
    """[(赛季编号, 开始时间（电脑上的时间）), ...]，从早到晚。"""
    data = json.loads((path or CONFIG_DIR / "seasons.json").read_text(encoding="utf-8"))
    return sorted((s["number"], _local(s["start"])) for s in data["seasons"])


SEASONS = load()


def label(number):
    return f"第 {number} 赛季"


def current(now=None, seasons=None):
    """现在是第几赛季（已经开始的最后一个）；第一个赛季开始以前返回 None。"""
    seasons = seasons or SEASONS
    stamp = (now or datetime.now()).strftime(STAMP)
    started = [n for n, start in seasons if start <= stamp]
    return started[-1] if started else None


def previous(now=None, seasons=None):
    """上一个赛季的编号（配置里排在本赛季前面的那个）；没有返回 None。"""
    seasons = seasons or SEASONS
    numbers = [n for n, _ in seasons]
    now_number = current(now, seasons)
    if now_number is None or numbers.index(now_number) == 0:
        return None
    return numbers[numbers.index(now_number) - 1]


def bounds(number, seasons=None):
    """这个赛季的 (开始, 结束)：电脑上的时间，结束是下一个赛季的开始（还没有下一个赛季时为 None）。"""
    seasons = seasons or SEASONS
    numbers = [n for n, _ in seasons]
    i = numbers.index(number)
    return seasons[i][1], (seasons[i + 1][1] if i + 1 < len(seasons) else None)


def contains(number, stamp, seasons=None):
    """这个时间（数据库里的写法）在不在这个赛季里。没有时间的不算。"""
    if not stamp:
        return False
    start, end = bounds(number, seasons)
    return start <= stamp and (end is None or stamp < end)


def of(stamp, seasons=None):
    """这个时间（数据库里的写法）在第几赛季；配置里没有那么早的赛季、没有时间时为 None。"""
    seasons = seasons or SEASONS
    return next((n for n, _ in reversed(seasons) if stamp and contains(n, stamp, seasons)), None)


def pick(rows, number, key="ended_at", seasons=None):
    """只留这个赛季的局（或大厅读数，key="read_at"）。number 为 None（配置里没有这个赛季）时一个都不留。"""
    if number is None:
        return []
    return [r for r in rows if contains(number, r[key], seasons)]


def start_date(number, seasons=None):
    """开赛那天（电脑上的日期，"2026-11-03"），页面上写"11-03 起"用。"""
    return bounds(number, seasons)[0][:10]
