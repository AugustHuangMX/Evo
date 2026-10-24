"""数据页"全体玩家"（2026-10-24 用户决定）：开了匿名统计的所有 Evo 玩家的汇总，和自己的数据放在一起比。

- 只有开了匿名统计的人才能看（telemetry.consent() 为 True）。没开时一个请求都不发，页面上是锁住的样子，
  可以在那里直接开启。这个限制只在 Evo 里：汇总本身在服务器上是公开的（Evo 开源，拦不住会改代码的人；以后官网也能用同一份）
- 汇总由统计服务器每小时算一次（server/stats/worker.js 的 makeSummary，格式和每一项怎么算见那里），Evo 只下载结果：
  GET https://stats.evotracker.cn/v1/summary，几十 KB，不带匿名编号。打开"全体玩家"时下载，存在数据文件夹
  （global_stats.json），FRESH_HOURS 小时内不重复下载；右上角的刷新按钮马上重新下载。
  只有 Evo 窗口（面板进程）下载，托盘和记录程序不下载
- 服务器能看到的和打开一个网页一样（IP 地址、时间；见隐私政策第三节）
- 全体的数字按全队三个人算（自己 + 两名队友）；"你"的数字在本机算（只用自己的数据库、选中的账号），
  和全体用同样的范围、模式、段位，同样不算待确认的局
- "匹配"是不是排位的局（包括没认出模式的局：简体中文的匹配开局画面还没加进识别规则），和服务器一样

测试时把 telemetry.STATS_URL 换成本机的测试地址（或者设环境变量 EVO_STATS_URL）。
"""

import gzip
import json
import os
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

import storage
import telemetry
from paths import DB_PATH
from post_match import COUNTED_KILLS_NOTES

CACHE_PATH = DB_PATH.parent / "global_stats.json"  # {"fetched_at": 下载的时间, "summary": 汇总}；和数据库放在一起，搬数据文件夹时一起搬
FRESH_HOURS = 24  # 下载过的汇总这么久以内不重新下载（右上角的刷新按钮不管这个）
TIMEOUT = 15  # 秒：和匿名统计一样，国内有的网络到 Cloudflare 绕得远
MAX_SIZE = 4 * 1024 * 1024
FORMAT = 1  # 汇总的格式版本（服务器的 SUMMARY_NAME = v1）

RANGES = {"all": "全部", "7d": "近 7 天"}  # 赛季以后再说（2026-10-24 用户决定；数据 10-22 才开始，"全部"就是本赛季）
MODES = {"ranked": "排位", "pubs": "匹配"}
TIERS = {"all": "全部段位", "low": "白金及以下", "diamond": "钻石", "master": "大师以上"}  # 只有排位分段位
TIER_ORDER = ("菜鸟", "青铜", "白银", "黄金", "白金", "钻石", "大师", "Apex 猎杀者")
TIER_GROUP = {**{t: "low" for t in TIER_ORDER[:5]}, "钻石": "diamond", "大师": "master", "Apex 猎杀者": "master"}
SHOW_LEGENDS = 8  # 传奇热度先列这么多个，其余点"展开全部"
SHOW_COMBOS = 5  # 热门组合同上
# "你在 Evo 玩家中"的四项：(汇总里的键, 显示名, 小数位（和服务器取整一样）, 越小越好)
PEOPLE = (("place", "场均名次", 2, True), ("win", "胜率", 4, False), ("kills", "场均击杀", 2, False),
          ("damage", "场均伤害", 0, False))
_lock = threading.Lock()  # 页面自动下载和刷新按钮可能同时来，一次只下载一个


def allowed():
    """能不能看全体玩家：开了匿名统计才能看。"""
    return telemetry.consent() is True


# ---------- 下载和存放 ----------

def _valid(summary):
    return (isinstance(summary, dict) and summary.get("v") == FORMAT and isinstance(summary.get("slices"), dict)
            and isinstance(summary.get("tiers"), dict))


def cached():
    """存着的汇总 {"fetched_at", "summary"}；没有、格式不对返回 None。"""
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("fetched_at"), str) or not _valid(data.get("summary")):
        return None
    return data


def stale(cache, now=None):
    """要不要重新下载：没有，或者下载了 FRESH_HOURS 小时以上（电脑时间往回调过也算）。"""
    if cache is None:
        return True
    try:
        fetched = datetime.strptime(cache["fetched_at"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    age = (now or datetime.now()) - fetched
    return not timedelta(0) <= age < timedelta(hours=FRESH_HOURS)


def fetch(now=None):
    """下载一次汇总，存进数据文件夹。没开匿名统计时不联网。返回 {"ok", "error"}。"""
    if not allowed():
        return {"ok": False, "error": "没有开启匿名统计"}
    with _lock:
        request = urllib.request.Request(f"{telemetry.STATS_URL}/v1/summary", headers={
            "User-Agent": f"Evo/{storage.APP_VERSION}", "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read(MAX_SIZE + 1)
                gzipped = response.headers.get("Content-Encoding") == "gzip"
            if len(raw) > MAX_SIZE:
                raise ValueError("太大")
            summary = json.loads((gzip.decompress(raw) if gzipped else raw).decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"统计服务器出错了（{e.code}）"}
        except (ValueError, EOFError, gzip.BadGzipFile):
            return {"ok": False, "error": "统计服务器的回答格式不对"}
        except OSError:
            return {"ok": False, "error": "连不上统计服务器（网络不通，或者服务器暂时不可用）"}
        if not _valid(summary):
            return {"ok": False, "error": "统计服务器的回答格式不对"}
        data = {"fetched_at": (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"), "summary": summary}
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = CACHE_PATH.with_name(CACHE_PATH.name + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, CACHE_PATH)
        return {"ok": True, "error": None}


# ---------- 页面上显示的 ----------

def choose(args):
    """网址里的筛选条件 → (范围, 模式, 段位)。不认识的用默认（全部、排位、全部段位）；匹配不分段位。"""
    range_key = args.get("range") if args.get("range") in RANGES else "all"
    mode = args.get("mode") if args.get("mode") in MODES else "ranked"
    tier = args.get("tier") if mode == "ranked" and args.get("tier") in TIERS else "all"
    return range_key, mode, tier


def _mode_of(row):
    return "ranked" if row["mode"] == "ranked" else "pubs"


def _mine(rows, summary, range_key, mode, tier):
    """自己的局里和这份汇总同样范围、模式、段位的。"""
    start = None
    if range_key == "7d":
        start = summary.get("recent_from") or (date.today() - timedelta(days=6)).isoformat()
    return [r for r in rows if _mode_of(r) == mode
            and (start is None or (r["ended_at"] or "")[:10] >= start)
            and (tier == "all" or TIER_GROUP.get(r["rank_tier"]) == tier)]


def _avg(values):
    return sum(values) / len(values) if values else None


def own_numbers(rows):
    """自己一组局的场均数字（和服务器一样不算待确认的局；击杀来自经验值页的局不算场均击杀，同总览页）。"""
    ok = [r for r in rows if not r["needs_review"]]
    places = [r["placement"] for r in ok if r["placement"] is not None]
    kills = [r["kills"] for r in ok if r["kills"] is not None
             and not any(n in (r["review_note"] or "") for n in COUNTED_KILLS_NOTES)]
    return {"games": len(rows), "place": _avg(places),
            "win": sum(1 for p in places if p == 1) / len(places) if places else None,
            "kills": _avg(kills), "damage": _avg([r["damage"] for r in ok if r["damage"] is not None])}


def beat(points, value, digits, lower_better):
    """value 超过了多少比例的玩家（0–1）：比你差的人数，一样的算一半。没有可比的返回 None。"""
    if value is None or not points:
        return None
    value = round(value, digits)
    worse = sum(1 for p in points if (p > value if lower_better else p < value))
    same = sum(1 for p in points if p == value)
    return (worse + same / 2) / len(points)


def _pct(value, digits=0):
    return "—" if value is None else f"{value:.{digits}%}"


def _num(value, fmt):
    return "—" if value is None else fmt.format(value)


def _versus(you, everyone):
    """你的场均名次和全体比：better / worse / None（没法比、一样）。"""
    if you is None or everyone is None or round(you, 1) == round(everyone, 1):
        return None
    return "better" if you < everyone else "worse"


def when_text(made_at):
    """服务器算的时间（UTC）→ 本机时间：今天 21:07 / 昨天 21:07 / 10-24 21:07。"""
    try:
        made = datetime.fromisoformat(made_at.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        return "—"
    if made.date() == date.today():
        return f"今天 {made:%H:%M}"
    if made.date() == date.today() - timedelta(days=1):
        return f"昨天 {made:%H:%M}"
    return f"{made:%m-%d %H:%M}"


def page(summary, rows, mates, range_key, mode, tier, your_tier=None, icons=None):
    """"全体玩家"一页要显示的。rows：自己的局（选中的账号）；mates：这些局的队友（storage.all_teammates）；
    your_tier：最近一次读到的段位（段位分布上标出"你"）；icons：传奇名 → 头像（panel_app.LEGEND_ICONS）。"""
    icons = icons or {}
    key = f"{range_key}.{mode}.{tier}"
    part = summary["slices"].get(key) or {}
    mine = _mine(rows, summary, range_key, mode, tier)

    legends = part.get("legends") or []
    widest = max((l.get("pick") or 0 for l in legends), default=0) or 1
    legend_rows = []
    for l in legends:
        own = own_numbers([r for r in mine if r["legend"] == l.get("name")])
        legend_rows.append({
            "name": l.get("name"), "icon": icons.get(l.get("name")), "games": l.get("n", 0),
            "pick": _pct(l.get("pick"), 1), "pick_width": round((l.get("pick") or 0) / widest * 100, 1),
            "win": _pct(l.get("win"), 1), "top5": _pct(l.get("top5")),
            "place": _num(l.get("place"), "{:.1f}"), "kills": _num(l.get("kills"), "{:.1f}"),
            "damage": _num(l.get("damage"), "{:,.0f}"),
            "you_games": own["games"], "you_place": _num(own["place"], "{:.1f}"),
            "you_vs": _versus(own["place"], l.get("place")),
        })

    squads = {}
    for m in mates:
        squads.setdefault(m["match_id"], []).append(m["legend"])
    own_combos = {}
    for r in mine:
        squad = [r["legend"], *squads.get(r["match_id"], [])]
        if len(squad) == 3 and all(squad) and len(set(squad)) == 3:
            own_combos.setdefault(tuple(sorted(squad)), []).append(r)
    combo_rows = []
    for c in part.get("combos") or []:
        names = c.get("legends") or []
        own = own_numbers(own_combos.get(tuple(sorted(names)), []))
        combo_rows.append({
            "legends": [{"name": n, "icon": icons.get(n)} for n in names], "games": c.get("n", 0),
            "rate": _pct(c.get("rate"), 1), "place": _num(c.get("place"), "{:.1f}"),
            "you_games": own["games"], "you_place": _num(own["place"], "{:.1f}"),
            "you_vs": _versus(own["place"], c.get("place")),
        })

    you = own_numbers(mine)
    people = part.get("people") or {}
    tiles = []
    for field, label, digits, lower_better in PEOPLE:
        points = people.get(field) or []
        share = beat(points, you[field], digits, lower_better)
        value = you[field]
        text = (_pct(value, 1) if field == "win" else _num(value, "{:,.0f}" if field == "damage" else "{:.1f}"))
        tiles.append({"label": label, "value": text, "beat": None if share is None else round(share * 100),
                      "players": len(points)})

    spread = summary["tiers"].get(range_key) or {}
    counts = spread.get("counts") or {}
    total = sum(counts.values()) or 1
    names = [t for t in TIER_ORDER if t in counts] + sorted(t for t in counts if t not in TIER_ORDER)
    most = max(counts.values(), default=0) or 1
    tier_rows = [{"name": t, "count": counts[t], "share": _pct(counts[t] / total), "width": round(counts[t] / most * 100, 1),
                  "you": t == your_tier} for t in names]

    return {
        "players": part.get("players", 0), "matches": part.get("matches", 0),
        "updated": when_text(summary.get("made_at")),
        "legends": legend_rows, "show_legends": SHOW_LEGENDS,
        "combos": combo_rows, "show_combos": SHOW_COMBOS,
        "you_games": you["games"], "tiles": tiles,
        "tiers": tier_rows, "tier_players": spread.get("players", 0), "your_tier": your_tier,
    }


if __name__ == "__main__":
    print(f"能不能看：{'能（开了匿名统计）' if allowed() else '不能（没开匿名统计）'}；地址：{telemetry.STATS_URL}/v1/summary")
    cache = cached()
    print("存着的汇总：" + ("没有" if cache is None else f"{cache['fetched_at']} 下载，服务器 {cache['summary'].get('made_at')} 算的"))
