"""六维图（2026-10-28 用户决定）：总览页、数据页上的一张雷达图，和同分段的 Evo 玩家比。

- 六项（顺时针，从正上方开始）：场均伤害、场均击杀、场均助攻、击杀参与率、英雄池、生存（前五率）
- 外圈（满分）= 同分段前 5% 的玩家的水平：打了 3 局以上的玩家每人一个场均，排好序取第 95 百分位，超过也算满
  （用户先说的"最强的那个玩家"，看了实际数字后改成前 5%：最强的往往只打了三四局，图会缩成一小块，来个新玩家就跳）。
  另一种颜色（虚线）画同分段玩家的平均。数字来自全体玩家的汇总（global_stats.py；服务器 worker.js 的 people）
- 分段 = 最近一次读到的段位所在的组（白金及以下 / 钻石 / 大师以上，和"全体玩家"的段位筛选一样）；那一组玩家不到
  MIN_PLAYERS 名、或者读不到段位，就和所有排位玩家比。范围里一局排位都没有时和匹配的玩家比
- "你"的数字：页面上选的范围（近 30 局 / 全部）里、和比的玩家同一种模式的局；和服务器一样不算待确认的局（英雄池除外：
  用了哪个传奇和读得准不准无关）；击杀来自经验值页的局不算进场均击杀和击杀参与（同总览页）
- 击杀参与率 = (击杀 + 助攻) ÷ (击杀 + 助攻 + 老板分)：队友的击杀不是自己的助攻就是老板分，所以老板分越少越高。
  服务器上没有老板分（匿名统计不发），用 (击杀 + 助攻) ÷ 全队击杀，道理一样
- 英雄池 = 局数占 POOL_SHARE 以上的传奇有几个（偶尔玩一两局的不算）
- 只有开了匿名统计的人能看（和"全体玩家"一样，用户决定）；没开时锁住，不下载
"""

import math
from datetime import datetime, timedelta

import global_stats
from post_match import COUNTED_KILLS_NOTES

POOL_SHARE = 0.05  # 英雄池：局数占这么多以上的传奇才算（和统计服务器 worker.js 的 POOL_SHARE 一样）
TOP = 0.95  # 外圈：前 5% 的玩家的水平
MIN_PLAYERS = 20  # 分段里打了 3 局以上的玩家不到这么多，就和所有排位玩家比
GROUP_NAMES = {"low": "白金及以下", "diamond": "钻石", "master": "大师以上", "all": "全部段位"}
# 六项：(汇总里的键, 图上写的, 表格里写的)
AXES = (("damage", "场均伤害", "场均伤害"), ("kills", "场均击杀", "场均击杀"), ("assists", "场均助攻", "场均助攻"),
        ("kp", "击杀参与", "击杀参与率"), ("pool", "英雄池", "英雄池（常用传奇）"), ("top5", "生存", "生存（前五率）"))
NEW_KEYS = ("assists", "top5", "kp", "pool")  # 2026-10-28 服务器才开始算的几项（老的汇总里没有）
OUTDATED_HOURS = 1  # 存着的是老的汇总：下载了这么久就重新下载
# 图的大小（SVG 的坐标）：中心、半径；字写在 HTML 里（按百分比放在顶点外面），图缩小了字也不变小
W, H, CX, CY, R = 240, 220, 120, 110, 70
LABEL_GAP = 12
RINGS = (0.25, 0.5, 0.75, 1)


def _avg(values):
    return sum(values) / len(values) if values else None


def own_numbers(rows):
    """自己一组局的六项（读不到的是 None）。"""
    ok = [r for r in rows if not r["needs_review"]]
    real_kills = [r for r in ok if not any(n in (r["review_note"] or "") for n in COUNTED_KILLS_NOTES)]
    places = [r["placement"] for r in ok if r["placement"] is not None]
    kp_games = [r for r in real_kills if None not in (r["kills"], r["assists"], r["participation"])]
    took = sum(r["kills"] + r["assists"] for r in kp_games)
    squad = took + sum(r["participation"] for r in kp_games)
    picks = {}
    for r in rows:
        if r["legend"]:
            picks[r["legend"]] = picks.get(r["legend"], 0) + 1
    total = sum(picks.values())
    return {
        "damage": _avg([r["damage"] for r in ok if r["damage"] is not None]),
        "kills": _avg([r["kills"] for r in real_kills if r["kills"] is not None]),
        "assists": _avg([r["assists"] for r in ok if r["assists"] is not None]),
        "kp": took / squad if squad else None,
        "pool": sum(1 for n in picks.values() if n / total >= POOL_SHARE) if total else None,
        "top5": sum(1 for p in places if p <= 5) / len(places) if places else None,
    }


def text(key, value, whole=True):
    """一项的数字怎么写。whole：英雄池是整数（自己的、前 5%），平均带一位小数。"""
    if value is None:
        return "—"
    if key == "damage":
        return f"{value:,.0f}"
    if key in ("kp", "top5"):
        return f"{value:.0%}"
    if key == "pool":
        return f"{value:.0f} 个" if whole else f"{value:.1f} 个"
    return f"{value:.1f}"


def top_level(points):
    """前 5% 的玩家的水平：排好序的场均（服务器给的）里第 95 百分位的那个。"""
    return points[round(TOP * (len(points) - 1))] if points else None


def outdated(summary):
    """存着的汇总是服务器加这几项以前算的（2026-10-28 以前）。"""
    people = (summary.get("slices", {}).get("all.ranked.all") or {}).get("people") or {}
    return any(k not in people for k in NEW_KEYS)


def _xy(i, r):
    angle = -math.pi / 2 + i * 2 * math.pi / len(AXES)
    return CX + r * R * math.cos(angle), CY + r * R * math.sin(angle)


def _points(values):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in (_xy(i, v) for i, v in values))


def _side(i):
    """字放在顶点的哪一边：正上方的写在上面、正下方的写在下面，其余左右两边。"""
    x, y = _xy(i, 1)
    if abs(x - CX) < 1:
        return "top" if y < CY else "bottom"
    return "right" if x > CX else "left"


def view(summary, rows, your_tier):
    """画图要用的。rows：页面上选的范围里的局（选中的账号）；your_tier：最近一次读到的段位（没有是 None）。"""
    ranked = [r for r in rows if r["mode"] == "ranked"]
    mode = "ranked" if ranked or not rows else "pubs"
    mine = ranked if mode == "ranked" else rows
    group = global_stats.TIER_GROUP.get(your_tier) if mode == "ranked" else None
    slices = summary.get("slices") or {}
    part = slices.get(f"all.{mode}.{group}") if group else None
    if not part or len((part.get("people") or {}).get("damage") or []) < MIN_PLAYERS:
        group = "all"
        part = slices.get(f"all.{mode}.all") or {}
    people = part.get("people") or {}
    you = own_numbers(mine)

    axes = []
    for i, (key, label, full_label) in enumerate(AXES):
        points = people.get(key) or []
        top = top_level(points)
        avg = _avg(points)
        scaled = bool(top)  # 前 5% 是 0（或者汇总里没有这一项）就没法换算
        x, y = _xy(i, 1 + LABEL_GAP / R)
        axes.append({
            "key": key, "label": label, "full_label": full_label,
            "you": text(key, you[key]), "avg": text(key, avg, whole=False), "top": text(key, top),
            "you_r": min(you[key] / top, 1) if scaled and you[key] is not None else None,
            "avg_r": min(avg / top, 1) if scaled else None,
            "left": round(x / W * 100, 2), "top_pct": round(y / H * 100, 2), "side": _side(i),
        })
    you_known = [(i, a["you_r"]) for i, a in enumerate(axes) if a["you_r"] is not None]
    avg_known = [(i, a["avg_r"]) for i, a in enumerate(axes) if a["avg_r"] is not None]
    compare = "匹配" if mode == "pubs" else GROUP_NAMES[group]
    return {
        "w": W, "h": H, "cx": CX, "cy": CY,
        "rings": [_points((i, r) for i in range(len(AXES))) for r in RINGS],
        "spokes": [_xy(i, 1) for i in range(len(AXES))],
        "axes": axes,
        "you_points": _points(you_known) if len(you_known) >= 3 else None,  # 少于 3 项读得到就不画
        "you_dots": [_xy(i, r) for i, r in you_known],
        "avg_points": _points(avg_known) if len(avg_known) == len(AXES) else None,  # 平均要六项都有才画
        "partial": len(avg_known) < len(AXES),  # 全体玩家的数据还不全（老的汇总、这一项还没人有数据）
        "compare": compare,
        "players": part.get("players", 0),
        "games": len(mine),
        "mode": mode,
    }


def card(rows, your_tier):
    """六维图卡片的状态：locked（没开匿名统计，不下载）、loading（还没下载过全体玩家的数据）、ready。
    fetch：页面打开后要不要在后台下载一次（没有、超过 24 小时，或者是服务器加这几项以前的汇总、下载了 1 小时以上）。"""
    if not global_stats.allowed():
        return {"state": "locked"}
    cache = global_stats.cached()
    if cache is None:
        return {"state": "loading", "fetch": True}
    summary = cache["summary"]
    # 老的汇总：1 小时下载一次（服务器还没更新时不会每打开一次页面就下载一次）
    fetch = global_stats.stale(cache) or (outdated(summary) and global_stats.stale(
        cache, now=datetime.now() + timedelta(hours=global_stats.FRESH_HOURS - OUTDATED_HOURS)))
    return {"state": "ready", "fetch": fetch, **view(summary, rows, your_tier)}
