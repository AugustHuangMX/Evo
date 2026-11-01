"""录入历史赛季数据的"录入模式"（2026-11-01 用户定的）：设置页点按钮 → 接下来 MINUTES 分钟里，记录程序认出游戏里的
数据页就读（见 live_collect.SeasonWatcher、season_stats.py）；到时间或者点"结束"就停。

开关存在设置文件（app_settings）里：录到什么时候。面板写、记录程序每隔几秒读一次（和"游戏语言"一样），
所以按了按钮几秒内生效，游戏还没开时也能先按。这个文件不加载识别用的库：托盘程序和面板也会用它。
"""

from datetime import datetime, timedelta

import app_settings

UNTIL_KEY = "season_import_until"  # 录到什么时候（本地时间，ISO 格式）；没开过、结束了为 None
MINUTES = 10  # 按一次录多久


def start(now=None):
    """开始录入（已经开着就从现在重新算 MINUTES 分钟）。返回录到什么时候。"""
    until = (now or datetime.now()) + timedelta(minutes=MINUTES)
    app_settings.set_value(UNTIL_KEY, until.isoformat(timespec="seconds"))
    return until


def stop():
    app_settings.set_value(UNTIL_KEY, None)


def until():
    """录到什么时候；没开、设置坏了返回 None。"""
    value = app_settings.get(UNTIL_KEY)
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def remaining(now=None):
    """还剩几秒（整数）；没开或已经过了返回 0。"""
    end = until()
    if end is None:
        return 0
    return max(0, int((end - (now or datetime.now())).total_seconds()))


def active(now=None):
    return remaining(now) > 0
