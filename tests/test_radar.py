"""六维图（radar.py、panel/templates/_radar.html；总览页、数据页"我的"）。不用截图，不联网：统计服务器由本机的小服务器冒充。

- 自己的六项：场均伤害、场均击杀（击杀来自经验值页的局不算）、场均助攻、击杀参与率（(击杀 + 助攻) ÷ (击杀 + 助攻 + 老板分)）、
  英雄池（局数占 5% 以上的传奇有几个，待确认的局也算）、生存（前五率）；不算待确认的局；只算和比的玩家同一种模式的局
- 外圈 = 同分段前 5% 的玩家（排好序的场均取第 95 百分位），平均 = 同分段玩家的平均；超过外圈的算满
- 分段 = 最近一次读到的段位那一组；那一组玩家太少就和全部段位比；没有排位局和匹配比
- 没开匿名统计：锁住，一个请求都不发；还没下载过：页面上写正在下载，下载由页面的脚本发起（POST 刷新），好了 /radar 给出新卡片
- 存着的是服务器加这几项以前的汇总：那几项写"—"、不画平均，下载了 1 小时以上就再下载一次
- "全部账号"时不画
- 数据页"我的"卡片那页上面是大图（图 + 数字表），点开的完整表格页没有

运行：python tests/test_radar.py
"""

import http.server
import json
import re
import threading
from datetime import datetime, timedelta

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_settings
import global_stats
import panel_app
import radar
import storage
import telemetry
from post_match import XP_ONLY_NOTE


def people(damage_from=400, n=25):
    """同分段每名玩家的场均（排好序）。n=25 时第 95 百分位是第 23 个（从 0 数）。"""
    return {"place": [8.0] * n, "win": [0.05] * n,
            "damage": [damage_from + i * 50 for i in range(n)], "kills": [round(i * 0.1, 2) for i in range(n)],
            "assists": [round(i * 0.08, 2) for i in range(n)], "kp": [round(0.3 + i * 0.02, 4) for i in range(n)],
            "pool": [1] * 5 + [2] * 5 + [3] * 5 + [4] * 5 + [5, 5, 6, 6, 7],
            "top5": [round(i * 0.03, 4) for i in range(n)]}


def part(players, people_):
    return {"players": players, "matches": players * 10, "legends": [], "combos": [], "people": people_,
            "weapons": [], "weapon_games": 0}


SUMMARY = {"v": 1, "made_at": "2026-10-28T12:07:00.000Z", "recent_from": "2026-10-22",
           "slices": {"all.ranked.all": part(300, people(300, 30)), "all.ranked.diamond": part(98, people()),
                      "all.ranked.master": part(3, people(n=3)), "all.ranked.low": part(150, people(100)),
                      "all.pubs.all": part(120, people(900))},
           "tiers": {"all": {"players": 300, "counts": {"钻石": 98}}}}
OLD_SUMMARY = json.loads(json.dumps(SUMMARY))  # 服务器加这几项以前的汇总：没有 assists、kp、pool、top5
for _part in OLD_SUMMARY["slices"].values():
    for _key in radar.NEW_KEYS:
        del _part["people"][_key]


class Handler(http.server.BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):  # noqa: N802
        self.requests.append(("GET", self.path))
        body = json.dumps(SUMMARY).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802  开启匿名统计时 telemetry 会马上发一次：照收
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.requests.append(("POST", self.path))
        self.send_response(200)
        self.send_header("Content-Length", "11")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_args):
        pass


def summary_gets():
    return [r for r in Handler.requests if r[0] == "GET"]


def new_db():
    """自己的排位局（钻石）：
    1 恶灵 第 3 名 击杀/助攻/老板分 2/1/1 伤害 800；2 恶灵 第 8 名 1/2/3 600；3 罗芭 第 12 名 0/0/2 200；
    4 恶灵 冠军 4/1/没读到老板分 1400；5 恶灵 第 5 名 击杀来自经验值页 3/2/0 没有伤害；
    6 希尔 待确认（只算进英雄池）；另有 1 局没认出模式（匹配）的动力小子。最后在大厅看到钻石。
    → 伤害 750、击杀 1.75（不算第 5 局）、助攻 1.2、击杀参与 (3+3+0)/(6+1+3+2) = 50%、英雄池 3 个、生存 3/5 = 60%"""
    path = storage.DB_PATH.parent / "radar-test.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)
    now = datetime.now()
    games = [("恶灵", 3, 2, 1, 1, 800, None, 0), ("恶灵", 8, 1, 2, 3, 600, None, 0), ("罗芭", 12, 0, 0, 2, 200, None, 0),
             ("恶灵", 1, 4, 1, None, 1400, None, 0), ("恶灵", 5, 3, 2, 0, None, XP_ONLY_NOTE, 0),
             ("希尔", 20, 9, 9, 9, 3000, "名次对不上", 1)]
    for i, (legend, place, kills, assists, boss, damage, note, review) in enumerate(games):
        storage.insert_match(conn, {
            "ended_at": f"{now - timedelta(hours=10 - i):%Y-%m-%d %H:%M:%S}", "player_name": "ImperialHzd",
            "mode": "ranked", "legend": legend, "placement": place, "kills": kills, "assists": assists,
            "participation": boss, "damage": damage, "rank_tier": "钻石", "review_note": note, "needs_review": review})
    storage.insert_match(conn, {"ended_at": f"{now - timedelta(hours=2):%Y-%m-%d %H:%M:%S}", "player_name": "ImperialHzd",
                                "mode": None, "legend": "动力小子", "placement": 2, "kills": 6, "assists": 0,
                                "participation": 0, "damage": 2000})
    storage.save_lobby(conn, {"read_at": f"{now:%Y-%m-%d %H:%M:%S}", "player_name": "ImperialHzd",
                              "rank_tier": "钻石", "rank_division": "IV", "rank_progress": 120, "score": 12120})
    conn.close()
    return path


def card_numbers(page):
    """六维图的表格：{项目: (你, 平均, 前 5%)}。"""
    card = page.split('id="radar-card"')[1].split("</section>")[0] if 'id="radar-card"' in page else ""
    rows = re.findall(r'<th scope="row">(.*?)</th>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>',
                      card, re.S)
    return {name: (you, avg, top) for name, you, avg, top in rows}


def write_cache(summary, age):
    global_stats.CACHE_PATH.write_text(json.dumps({
        "fetched_at": (datetime.now() - age).strftime("%Y-%m-%d %H:%M:%S"), "summary": summary}, ensure_ascii=False),
        encoding="utf-8")


def checks():
    out = {}
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]

    def page(url="/"):
        return client.get(url).get_data(as_text=True)

    # ---- 自己的六项（不经过页面）----
    row = {"needs_review": 0, "review_note": None, "mode": "ranked", "placement": 9, "kills": 1, "assists": 0,
           "participation": 1, "damage": 100}
    many = [{**row, "legend": "恶灵"}] * 19 + [{**row, "legend": "罗芭"}] * 2 + [{**row, "legend": "希尔"}]  # 希尔 1/22 < 5%
    out["英雄池：局数占 5% 以上的传奇才算（偶尔玩一局的不算）"] = radar.own_numbers(many)["pool"] == 2
    out["读不到的项是空的，不当作 0"] = radar.own_numbers([{**row, "legend": None, "placement": None, "kills": None,
                                                     "assists": None, "participation": None, "damage": None}]) == {
        "damage": None, "kills": None, "assists": None, "kp": None, "pool": None, "top5": None}

    # ---- 没开匿名统计 ----
    for value in (None, False):
        app_settings.set_value(telemetry.CONSENT_KEY, value)
        Handler.requests.clear()
        overview, fragment = page(), page("/radar?size=big")
        out[f"没开匿名统计（{value}）：总览页的六维图锁住，有开启按钮，一个请求都不发"] = (
            'id="radar-card"' in overview and "开启匿名统计后可以查看六维图" in overview
            and "开启匿名统计</button>" in overview and "radar-you" not in overview and not Handler.requests
            and "开启匿名统计后可以查看六维图" in fragment)

    # ---- 在锁住的卡片上开启：回到总览页 ----
    reply = client.post("/settings/telemetry", data={"token": token, "on": "1", "next": "/?"})
    # 开启时后台马上发一次（线程 telemetry-now），它写设置文件的那一下读设置会读到空的，等它发完再往下
    for thread in threading.enumerate():
        if thread.name == "telemetry-now":
            thread.join(30)
    out["在卡片上开启匿名统计：回到总览页"] = reply.status_code == 302 and reply.headers["Location"].endswith("/")

    # ---- 开了、还没下载过：页面上写正在下载，下载由页面的脚本发起 ----
    global_stats.CACHE_PATH.unlink(missing_ok=True)
    Handler.requests.clear()
    loading = page()
    out["还没下载过：写正在下载，打开页面本身不下载（脚本再 POST 刷新）"] = (
        "正在下载全体玩家的数据" in loading and 'data-fetch="1"' in loading and not summary_gets()
        and "/stats/everyone/refresh" in loading)
    fetched = client.post("/stats/everyone/refresh", data={"token": token}).get_json()
    small = page("/radar?size=small")
    out["下载好以后 /radar 给出新卡片（小图：详细链接、读屏用的表格）"] = (
        fetched["ok"] and len(summary_gets()) == 1 and 'data-fetch="0"' in small and "radar-you" in small
        and "详细 →" in small and "radar-table sr-only" in small)

    # ---- 数字 ----
    numbers = card_numbers(page("/radar?size=big"))
    expected = {"场均伤害": ("750", "1,000", "1,550"), "场均击杀": ("1.8", "1.2", "2.3"), "场均助攻": ("1.2", "1.0", "1.8"),
                "击杀参与率": ("50%", "54%", "76%"), "英雄池（常用传奇）": ("3 个", "3.2 个", "6 个"),
                "生存（前五率）": ("60%", "36%", "69%")}
    out["你 / 平均 / 前 5% 都对（不算待确认、匹配的局；击杀来自经验值页的局不算击杀、击杀参与）"] = numbers == expected
    if numbers != expected:
        print("    实际：", numbers)
    big = page("/radar?size=big&range=all")
    out["数据页的大图：标题带范围，写着和钻石的 98 名玩家比，表格看得见"] = (
        "六维图 · 全部" in big and re.search(r'和钻石的 <span class="num">98</span> 名玩家比', big) is not None
        and "radar-table sr-only" not in big and "radar-avg" in big)

    stats_page, full_table = page("/stats?range=all"), page("/stats/weapons")
    out["数据页\"我的\"：卡片上面是六维图大图（数字表看得见）；点开的完整表格页没有"] = (
        'class="card radar-card radar-big"' in stats_page and "六维图 · 全部" in stats_page
        and card_numbers(stats_page) == expected and "radar-card" not in full_table)
    global_stats.CACHE_PATH.unlink(missing_ok=True)
    loading_pages = [page("/"), page("/stats")]
    out["还没下载过时，总览页、数据页都带着后台下载的脚本"] = all(
        'data-fetch="1"' in p and 'card.dataset.fetch !== "1"' in p for p in loading_pages)
    client.post("/stats/everyone/refresh", data={"token": token})

    rows = [{**row, "legend": "恶灵", "damage": 5000}] * 3
    view = radar.view(SUMMARY, rows, "钻石")
    out["超过外圈的算满（不画到圈外）"] = view["axes"][0]["you_r"] == 1
    out["分段玩家太少（大师以上 3 名）：和全部段位比"] = (radar.view(SUMMARY, rows, "大师")["compare"] == "全部段位"
                                          and radar.view(SUMMARY, rows, "大师")["axes"][0]["top"] == "1,700")
    out["读不到段位：和全部段位比"] = radar.view(SUMMARY, rows, None)["compare"] == "全部段位"
    pubs = radar.view(SUMMARY, [{**r, "mode": None} for r in rows], "钻石")
    out["范围里没有排位局：和匹配的玩家比"] = pubs["compare"] == "匹配" and pubs["mode"] == "pubs"

    # ---- 老的汇总（服务器加这几项以前的）----
    old = radar.view(OLD_SUMMARY, rows, "钻石")
    out["老的汇总：新的几项写\"—\"，不画平均、不画你（少于 3 项），写明全体玩家的数据还不全"] = (
        [a["avg"] for a in old["axes"]][2:] == ["—"] * 4 and old["avg_points"] is None and old["you_points"] is None
        and old["partial"])
    write_cache(OLD_SUMMARY, timedelta(minutes=30))
    recent_old = radar.card(rows, "钻石")["fetch"]
    write_cache(OLD_SUMMARY, timedelta(hours=2))
    stale_old = radar.card(rows, "钻石")["fetch"]
    write_cache(SUMMARY, timedelta(hours=2))
    fresh_new = radar.card(rows, "钻石")["fetch"]
    write_cache(SUMMARY, timedelta(hours=25))
    stale_new = radar.card(rows, "钻石")["fetch"]
    out["什么时候在后台重新下载：老的汇总 1 小时以后，新的 24 小时以后"] = (
        recent_old is False and stale_old is True and fresh_new is False and stale_new is True)

    # ---- 全部账号 ----
    conn = storage.connect(panel_app.app.config["DB"])
    storage.insert_match(conn, {"ended_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}", "player_name": "AltAccount",
                                "mode": "ranked", "legend": "命脉", "placement": 9, "rank_tier": "白金"})
    conn.close()
    with client.session_transaction() as s:
        s["account"] = "all"
    out["\"全部账号\"时不画，提示选一个账号"] = "六维图按账号分开" in page() and "radar-you" not in page("/radar?size=small")
    return out


def main():
    print("六维图（和同分段的 Evo 玩家比：外圈前 5%、虚线平均；没开匿名统计时锁住）")
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
