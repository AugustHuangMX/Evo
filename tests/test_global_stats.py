"""数据页"全体玩家"（global_stats.py、panel/templates/stats_all.html）。不用截图，不联网：统计服务器由本机的小服务器冒充。

- 没开匿名统计：锁住的样子（锁、开启按钮，后面的示意读屏软件跳过），一个请求都不发；在那里开启后回到"全体玩家"
- 开了：还没下载过就在页面打开时自动下载；下载的只有汇总（GET /v1/summary，不带匿名编号）；24 小时内不重复下载，
  刷新按钮马上下载；连不上、服务器出错、格式不对时不覆盖已存的
- 页面：传奇热度先列 8 个、其余点"展开全部"；热门组合先列 5 个；"你"和全体比（不算待确认的局）；
  你在 Evo 玩家中（超过多少玩家，一样的算一半）；段位分布标出你的段位；范围、模式、段位筛选（匹配包括没认出模式的局）
- 数据页"我的"上面有切换

运行：python tests/test_global_stats.py
"""

import gzip
import http.server
import json
import os
import re
import threading
from datetime import date, datetime, timedelta

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_settings
import global_stats
import panel_app
import storage
import telemetry

os.environ["no_proxy"] = "127.0.0.1,localhost"  # 电脑上设了代理时，本机的测试服务器也直接连
TODAY = date.today()
RECENT_FROM = (TODAY - timedelta(days=6)).isoformat()
LEGENDS = ["恶灵", "罗芭", "探路者", "疯玛吉", "艾克赛尔", "万蒂奇", "班加罗尔", "导线管", "希尔", "命脉"]


def legend(name, n, place):
    return {"name": name, "n": n, "pick": round(n / 550, 4), "win": 0.05, "top5": 0.25, "place": place, "kills": 1.2,
            "damage": 600}


def part(players, matches, legends, combos=()):
    return {"players": players, "matches": matches, "legends": legends,
            "combos": [{"legends": c, "n": 9 - i, "rate": 0.02, "place": 10.0} for i, c in enumerate(combos)],
            "people": {"place": [6.0, 8.0, 10.0, 12.0, 14.0], "win": [0.0, 0.0, 0.05, 0.1, 0.2],
                       "kills": [0.5, 1.0, 1.5, 2.0, 2.5], "damage": [400, 600, 800, 1000, 1200]}}


COMBOS = [sorted(["恶灵", "罗芭", "命脉"]), ["万蒂奇", "探路者", "罗芭"], ["导线管", "探路者", "班加罗尔"],
          ["希尔", "恶灵", "疯玛吉"], ["命脉", "恶灵", "艾克赛尔"], ["万蒂奇", "命脉", "罗芭"], ["探路者", "疯玛吉", "罗芭"]]
SUMMARY = {
    "v": 1, "made_at": f"{TODAY.isoformat()}T05:07:00Z", "recent_from": RECENT_FROM,
    "slices": {
        "all.ranked.all": part(12, 340, [legend(n, 100 - i * 9, 9.5 + i / 10) for i, n in enumerate(LEGENDS)], COMBOS),
        "7d.ranked.all": part(5, 80, [legend("罗芭", 30, 9.0), legend("恶灵", 20, 10.0)]),
        "all.ranked.diamond": part(4, 90, [legend("探路者", 40, 9.0)]),
        "all.ranked.low": part(0, 0, []), "all.ranked.master": part(0, 0, []),
        "all.pubs.all": part(6, 70, [legend("动力小子", 25, 8.0)]),
    },
    "tiers": {"all": {"players": 10, "counts": {"钻石": 5, "白金": 3, "黄金": 2}},
              "7d": {"players": 4, "counts": {"钻石": 4}}},
}


class Handler(http.server.BaseHTTPRequestHandler):
    requests = []  # 收到的请求：(方法, 路径, 请求头)
    status = 200
    body = json.dumps(SUMMARY, ensure_ascii=False).encode("utf-8")
    gzip = False

    def _send(self, status, data, gzipped=False):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if gzipped:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self.requests.append(("GET", self.path, dict(self.headers)))
        if self.status != 200:
            self._send(self.status, b'{"ok": false}')
        elif self.gzip:
            self._send(200, gzip.compress(self.body), gzipped=True)
        else:
            self._send(200, self.body)

    def do_POST(self):  # noqa: N802  开启匿名统计时 telemetry 会马上发一次：照收
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.requests.append(("POST", self.path, dict(self.headers)))
        self._send(200, b'{"ok": true}')

    def log_message(self, *_args):
        pass


def summary_gets():
    return [r for r in Handler.requests if r[0] == "GET"]


def new_db():
    """自己的局：恶灵排位 4 局（名次 5/7/9/11，伤害 500/700/900/1100，钻石，最近几天）+ 1 局待确认（名次 20，不算）；
    罗芭排位 1 局（10 天前，名次 3）；动力小子 1 局没认出模式（算匹配）。恶灵那几局的队友是罗芭、命脉。最后在大厅看到钻石。"""
    path = storage.DB_PATH.parent / "global-stats-test.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)
    now = datetime.now()
    for i, (place, damage) in enumerate(((5, 500), (7, 700), (9, 900), (11, 1100))):
        match_id = storage.insert_match(conn, {
            "ended_at": f"{now - timedelta(days=2, hours=i):%Y-%m-%d %H:%M:%S}", "player_name": "ImperialHzd",
            "mode": "ranked", "legend": "恶灵", "placement": place, "kills": 2, "damage": damage, "rank_tier": "钻石"})
        storage.save_teammates(conn, match_id, [{"slot": "left", "legend": "罗芭"}, {"slot": "right", "legend": "命脉"}])
    storage.insert_match(conn, {"ended_at": f"{now - timedelta(days=1):%Y-%m-%d %H:%M:%S}", "player_name": "ImperialHzd",
                                "mode": "ranked", "legend": "恶灵", "placement": 20, "kills": 0, "damage": 0,
                                "rank_tier": "钻石", "needs_review": 1, "review_note": "名次对不上"})
    storage.insert_match(conn, {"ended_at": f"{now - timedelta(days=10):%Y-%m-%d %H:%M:%S}", "player_name": "ImperialHzd",
                                "mode": "ranked", "legend": "罗芭", "placement": 3, "kills": 1, "damage": 300,
                                "rank_tier": "白金"})
    storage.insert_match(conn, {"ended_at": f"{now - timedelta(days=1, hours=5):%Y-%m-%d %H:%M:%S}",
                                "player_name": "ImperialHzd", "mode": None, "legend": "动力小子", "placement": 4,
                                "kills": 6, "damage": 1600})
    storage.save_lobby(conn, {"read_at": f"{now:%Y-%m-%d %H:%M:%S}", "player_name": "ImperialHzd",
                                      "rank_tier": "钻石", "rank_division": 4, "rank_progress": 120, "score": 8120})
    conn.close()
    return path


def rows_of(page, tbody_id=None):
    """表格里每一行的第一格（传奇名或组合）。tbody_id：只看"展开全部"里的那几行。"""
    if tbody_id:
        found = re.search(rf'<tbody id="{tbody_id}" hidden>(.*?)</tbody>', page, re.S)
        page = found.group(1) if found else ""
    return [re.sub(r"<[^>]+>|\s", "", r) for r in re.findall(r'<th scope="row">(.*?)</th>', page, re.S)]


def you_cell(page, name):
    """传奇热度里某个传奇那一行"你"那一格的文字和颜色（better / worse / same），没有返回 None。"""
    row = re.search(rf'{name}</span></th>(.*?)</tr>', page, re.S)
    if not row:
        return None
    cell = re.search(r'<td class="right you-col">(.*?)</td>', row.group(1), re.S).group(1)
    vs = re.search(r"you-(better|worse|same)", cell)
    return re.sub(r"<[^>]+>|\s", "", cell), vs.group(1) if vs else None


def tile(page, label):
    """你在 Evo 玩家中：某一格的数字和"超过多少"。"""
    found = re.search(rf'<div class="label">{label}</div>\s*<div class="beat-value num">(.*?)</div>(.*?)</div>\s*</div>',
                      page, re.S)
    if not found:
        return None
    beat = re.search(r"超过 <span class=\"num\">(\d+)%</span>", found.group(2))
    return found.group(1), int(beat.group(1)) if beat else None


def checks():
    out = {}
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]

    def page(url="/stats?view=all"):
        return client.get(url).get_data(as_text=True)

    # ---- 没开匿名统计 ----
    for value in (None, False):
        app_settings.set_value(telemetry.CONSENT_KEY, value)
        Handler.requests.clear()
        locked = page()
        refresh = client.post("/stats/everyone/refresh", data={"token": token}).get_json()
        out[f"{'没回答' if value is None else '点了不了'}：锁住（锁、开启按钮、后面的示意读屏跳过、点不到），不下载"] = (
            "开启匿名统计后可以查看全体玩家的数据" in locked and 'class="locked-icon"' in locked
            and re.search(r'class="card stats-card locked-preview" aria-hidden="true" inert', locked) is not None
            and 'id="everyone-refresh"' not in locked and refresh["ok"] is False and not Handler.requests)
    out["锁住时：开启按钮发 on=1，开好回到全体玩家；能去设置页关闭"] = (
        re.search(r'name="on" value="1">\s*<input type="hidden" name="next" value="/stats\?view=all">', locked) is not None
        and "/settings#stats" in locked)

    mine = page("/stats")
    out["数据页\"我的\"：上面有切换（我的 / 全体玩家），选中的是我的"] = (
        'aria-current="page">我的</a>' in mine and 'href="/stats?view=all"' in mine)

    reply = client.post("/settings/telemetry", data={"token": token, "on": "1", "next": "/stats?view=all"})
    out["点\"开启匿名统计\"：开启了，回到全体玩家"] = (
        telemetry.consent() is True and reply.status_code == 302 and reply.headers["Location"].endswith("/stats?view=all"))

    # ---- 开了，还没下载过 ----
    global_stats.CACHE_PATH.unlink(missing_ok=True)
    Handler.requests.clear()
    first = page()
    out["开了、还没下载过：写\"正在下载\"，页面打开就自动下载（页面本身不等网络）"] = (
        "正在下载全体玩家的数据" in first and "refresh();" in first and not summary_gets())
    result = client.post("/stats/everyone/refresh", data={"token": token}).get_json()
    gets = summary_gets()
    out["刷新：下载一次汇总，存进数据文件夹"] = (
        result == {"ok": True, "error": None} and len(gets) == 1 and global_stats.cached() is not None)
    install = app_settings.get(telemetry.ID_KEY)
    out["下载时只要汇总：GET /v1/summary，不带匿名编号"] = (
        gets[0][1] == "/v1/summary" and install not in json.dumps(gets[0][2])
        and gets[0][2].get("User-Agent") == f"Evo/{storage.APP_VERSION}")

    full = page()
    out["下载好：人数、局数、更新时间（本机时间），有刷新按钮，不再自动下载"] = (
        "12</span> 名玩家" in full and "340</span> 局" in full and "更新于 今天" in full
        and 'id="everyone-refresh"' in full and "refresh();" not in full)
    shown, more = rows_of(full.split('id="h-combos"')[0]), rows_of(full, "legends-more")
    out["传奇热度：先列 8 个（按出场次数），其余 2 个在\"展开全部 10 个传奇\"里"] = (
        shown == LEGENDS and more == LEGENDS[8:] and "展开全部 10 个传奇" in full)
    out["热门组合：先列 5 个，其余在\"展开全部 7 个组合\"里"] = (
        len(rows_of(full, "combos-more")) == 2 and "展开全部 7 个组合" in full)
    out["\"你\"：恶灵 5 局（待确认那局算局数、不算名次）场均 8.0，比全体 9.5 好，有箭头和读屏文字"] = (
        you_cell(full, "恶灵") == ("8.0（比全体好）5局", "better"))
    out["\"你\"：罗芭 1 局（名次 3）；没用过的传奇写\"—\""] = (
        you_cell(full, "罗芭") == ("3.0（比全体好）1局", "better") and you_cell(full, "探路者") == ("—", None))
    combo_you = re.search(r"命脉.*?恶灵.*?罗芭.*?<td class=\"right you-col\">(.*?)</td>", full.split('id="h-combos"')[1],
                          re.S)
    out["热门组合的\"你\"：恶灵 + 罗芭 + 命脉打了 4 局"] = (
        combo_you is not None and "4 局" in combo_you.group(1))
    out["你在 Evo 玩家中：场均名次 7.0（不算待确认的局），比 5 个人里的 4 个好"] = tile(full, "场均名次") == ("7.0", 80)
    out["你在 Evo 玩家中：场均伤害 700，超过 40% 的玩家；胜率 0.0%"] = (
        tile(full, "场均伤害") == ("700", 40) and tile(full, "胜率")[0] == "0.0%")
    tiers = re.search(r'<ul class="tier-list">(.*?)</ul>', full, re.S)
    out["段位分布（收起来）：按段位从低到高，你的段位标\"你\""] = (
        '<details class="card stats-card more-card">' in full and tiers is not None
        and re.findall(r'<span class="tier-name">([^<]+)', tiers.group(1)) == ["黄金", "白金", "钻石"]
        and re.search(r'<li class="you">\s*<span class="tier-name">钻石<span class="you-tag">你</span>', tiers.group(1)))

    week = page("/stats?view=all&range=7d")
    out["近 7 天：用近 7 天的汇总，你的局只算 7 天以内（罗芭那局 10 天前，不算）"] = (
        rows_of(week.split('id="h-combos"')[0]) == ["罗芭", "恶灵"] and you_cell(week, "罗芭") == ("—", None)
        and "5</span> 名玩家" in week)
    diamond = page("/stats?view=all&tier=diamond")
    out["钻石：用钻石的汇总，筛选按钮选中钻石"] = (
        rows_of(diamond.split('id="h-combos"')[0]) == ["探路者"] and 'aria-current="true">钻石</a>' in diamond)
    pubs = page("/stats?view=all&mode=pubs&tier=diamond")
    out["匹配：没有段位按钮；没认出模式的局算匹配（动力小子 1 局，名次 4）"] = (
        "段位</span>" not in pubs and rows_of(pubs.split('id="h-combos"')[0]) == ["动力小子"]
        and you_cell(pubs, "动力小子") == ("4.0（比全体好）1局", "better"))
    out["网址里不认识的筛选：用默认（全部、排位、全部段位）"] = (
        global_stats.choose({"range": "x", "mode": "y", "tier": "z"}) == ("all", "ranked", "all"))

    # ---- 什么时候重新下载 ----
    Handler.requests.clear()
    cache = global_stats.cached()
    later = datetime.strptime(cache["fetched_at"], "%Y-%m-%d %H:%M:%S")
    out["24 小时内不重新下载，过了 24 小时（或电脑时间往回调了）自动下载"] = (
        not global_stats.stale(cache, later + timedelta(hours=23)) and global_stats.stale(cache, later + timedelta(hours=24))
        and global_stats.stale(cache, later - timedelta(minutes=5)) and "refresh();" not in page() and not summary_gets())

    # ---- 下载不成 ----
    before = global_stats.CACHE_PATH.read_text(encoding="utf-8")
    Handler.status = 500
    error = global_stats.fetch()
    Handler.status = 200
    out["服务器出错：写明原因，不覆盖已存的"] = (
        error["ok"] is False and "500" in error["error"] and global_stats.CACHE_PATH.read_text(encoding="utf-8") == before)
    url = telemetry.STATS_URL
    telemetry.STATS_URL = "http://127.0.0.1:9"  # 没有服务器的端口
    error = global_stats.fetch()
    telemetry.STATS_URL = url
    out["连不上：写明连不上"] = error["ok"] is False and "连不上" in error["error"]
    good = Handler.body
    Handler.body = json.dumps({**SUMMARY, "v": 2}).encode("utf-8")
    bad_format = global_stats.fetch()
    Handler.body = b"<html>not json"
    not_json = global_stats.fetch()
    Handler.body = good
    out["格式不对（不认识的版本、不是 JSON）：不存"] = (
        not bad_format["ok"] and not not_json["ok"] and global_stats.CACHE_PATH.read_text(encoding="utf-8") == before)
    Handler.gzip = True
    zipped = global_stats.fetch()
    Handler.gzip = False
    out["服务器压缩过的回答（gzip）也读得出"] = zipped["ok"] and global_stats.cached()["summary"] == SUMMARY
    out["算\"超过多少\"：一样的算一半；越小越好的反过来；没有可比的不写"] = (
        global_stats.beat([1, 2, 2, 3], 2, 0, False) == 0.5 and global_stats.beat([1, 2, 3, 4], 1, 0, True) == 0.875
        and global_stats.beat([], 5, 0, False) is None and global_stats.beat([1], None, 0, False) is None)

    # ---- 关掉匿名统计以后 ----
    Handler.requests.clear()
    client.post("/settings/telemetry", data={"token": token, "on": "0", "next": "/settings"})
    off = page()
    out["关掉匿名统计以后：又锁上，不显示存着的数据，不下载"] = (
        "开启匿名统计后可以查看全体玩家的数据" in off and "传奇热度</h2>" in off and "名玩家" not in off
        and 'id="h-hot"' not in off and not summary_gets())
    out["没带口令：刷新被拒绝"] = client.post("/stats/everyone/refresh").status_code == 403
    return out


def main():
    print("数据页\"全体玩家\"（开了匿名统计才能看、下载汇总、和自己比、筛选、连不上）")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url, db = telemetry.STATS_URL, panel_app.app.config["DB"]
    telemetry.STATS_URL = f"http://127.0.0.1:{server.server_address[1]}"  # 绝不连到真正的统计服务器
    keys = (telemetry.CONSENT_KEY, telemetry.SINCE_KEY, telemetry.ID_KEY, telemetry.PING_KEY, telemetry.LAST_KEY,
            "update_auto")
    app_settings.set_value("update_auto", False)  # 免得页面顶部问要不要自动检查
    panel_app.app.config["DB"] = new_db()
    try:
        result = checks()
    finally:
        telemetry.STATS_URL, panel_app.app.config["DB"] = url, db
        server.shutdown()
        for key in keys:
            app_settings.set_value(key, None)
        global_stats.CACHE_PATH.unlink(missing_ok=True)
        telemetry.SENT_PATH.unlink(missing_ok=True)
    return report(result)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
