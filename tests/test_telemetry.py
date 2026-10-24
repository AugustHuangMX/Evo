"""匿名统计（telemetry.py、面板上的提问和设置页）。不用截图，不联网：统计服务器由本机的小服务器冒充。

- 没回答、点了"不了"、关掉以后：一个请求都不发
- 只发同意以后结束、已经结束 1 小时的局；只发说好的那几项（游戏名、ID、队友名字、备注、截图位置都不发）
- 日活一天只发一次，没发出去的日子下次连上时补发（最多 30 天）；发过的局不重发，改过（战绩页修正）的局再发一次，
  带上改了哪几项
- 一次最多 100 局，多的分几次；连不上、服务器拒绝时不算发过，下次再发
- 面板：前两个提问回答了才问这一个，两个按钮一样的样式；设置页只有开关和一句说明（2026-10-24 起不显示发送状态、
  发送内容的原样，没有"关闭并删除已发送的数据"）

运行：python tests/test_telemetry.py
"""

import http.server
import json
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_settings
import game_aspect
import panel_app
import storage
import telemetry

os.environ["no_proxy"] = "127.0.0.1,localhost"  # 电脑上设了代理时，本机的测试服务器也直接连
KEYS = (telemetry.CONSENT_KEY, telemetry.SINCE_KEY, telemetry.ID_KEY, telemetry.PING_KEY, telemetry.LAST_KEY,
        telemetry.MISSED_KEY)
BODY_KEYS = {"id", "version", "matches", "ping"}
PING_KEYS = {"day", "screen", "game_res", "game_mode", "aspect"}
MATCH_KEYS = {"k", "day", "version", "mode", "map", "legend", "mates", "mate_stats", "placement", "kills", "assists",
              "knockdowns", "damage", "rp_delta", "tier", "rp_source", "review", "edited"}
SECRETS = ("测试玩家甲", "队友名字乙", "0123456789", "秘密备注", "captures/live")  # 这些一个字都不能发出去
SINCE = "2026-10-20 12:00:00"
NOW = datetime(2026, 10, 21, 10, 0)


class Handler(http.server.BaseHTTPRequestHandler):
    requests = []  # 收到的请求：(路径, 内容)
    status = 200

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.requests.append((self.path, json.loads(body.decode("utf-8")), self.headers.get("User-Agent")))
        reply = json.dumps({"ok": self.status == 200}).encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *_args):
        pass


def start_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def add_match(conn, ended_at, **extra):
    match = {"ended_at": ended_at, "player_name": "测试玩家甲", "mode": "ranked", "map": "世界边缘", "legend": "恶灵",
             "placement": 3, "kills": 4, "assists": 1, "knockdowns": 5, "participation": 2, "damage": 1234,
             "rp_delta": 56, "rp_source": "经验值页", "rank_tier": "钻石", "rank_division": "II", "rank_progress": 868,
             "needs_review": 1, "review_note": "名次对不上：秘密备注", "evidence_path": "captures/live/x/01.png", **extra}
    match_id = storage.insert_match(conn, match)
    storage.save_teammates(conn, match_id, [
        {"slot": "hud1", "name": "队友名字乙", "player_id": "0123456789", "legend": "命脉", "kills": 2, "assists": 1,
         "knockdowns": 3, "damage": 640, "revives": 1},
        {"slot": "hud2", "name": "另一个队友", "player_id": "987", "legend": None}])
    return match_id


def sent_matches():
    return [m for path, body, _ in Handler.requests if path == "/v1/report" for m in body.get("matches", [])]


def send_checks(conn):
    out = {}
    Handler.requests.clear()
    for value in (None, False):
        app_settings.set_value(telemetry.CONSENT_KEY, value)
        out[f"{'没回答' if value is None else '点了不了'}：不发送，一个请求都没有"] = (
            telemetry.send(now=NOW) is None and not Handler.requests)
    Handler.requests.clear()  # 上面万一发了（测试没通过），下面照常检查
    app_settings.set_value(telemetry.PING_KEY, None)

    telemetry.set_consent(True)
    out["同意：生成匿名编号（32 位随机数），记下从什么时候算起"] = (
        telemetry._valid_id(app_settings.get(telemetry.ID_KEY)) and app_settings.get(telemetry.SINCE_KEY) is not None)
    app_settings.set_value(telemetry.SINCE_KEY, SINCE)
    before = add_match(conn, "2026-10-19 20:00:00")
    first = add_match(conn, "2026-10-20 20:00:00")
    second = add_match(conn, "2026-10-20 20:30:00", legend=None, damage=None)
    recent = add_match(conn, "2026-10-21 09:30:00")

    video = Path(storage.DB_PATH.parent / "videoconfig-telemetry.txt")
    video.write_text('"VideoConfig"\n{\n\t"setting.defaultres"\t\t"2560"\n\t"setting.defaultresheight"\t\t"1600"\n'
                     '\t"setting.fullscreen"\t\t"1"\n\t"setting.nowindowborder"\t\t"1"\n}\n', encoding="utf-8")
    with mock.patch.object(game_aspect, "VIDEO_CONFIG", video):
        result = telemetry.send(now=NOW)
    out["发送一次：一个请求，2 局 + 今天的日活"] = (
        result["ok"] and result["matches"] == 2 and result["ping"] and len(Handler.requests) == 1)
    path, body, agent = Handler.requests[0]
    keys = {m["k"] for m in sent_matches()}
    out["同意以前结束的局不发，结束不到 1 小时的也先不发"] = keys == {
        telemetry.match_key(body["id"], storage.get_match(conn, i)) for i in (first, second)}
    out["只发说好的那几项"] = (
        set(body) == BODY_KEYS and set(body["ping"]) == PING_KEYS and all(set(m) == MATCH_KEYS for m in body["matches"]))
    text = json.dumps(body, ensure_ascii=False)
    out["游戏名、队友名字和 ID、备注、截图位置都不在里面"] = not any(s in text for s in SECRETS)
    one = next(m for m in body["matches"] if m["legend"] == "恶灵")
    out["每局：传奇、队友的传奇、名次、击杀、伤害、RP、大段、待确认"] = (
        one["mates"] == ["命脉", None] and one["placement"] == 3 and one["kills"] == 4 and one["damage"] == 1234
        and one["rp_delta"] == 56 and one["tier"] == "钻石" and one["review"] is True and one["day"] == "2026-10-20"
        and one["edited"] == [])
    out["队友：传奇和这一局的击杀/助攻/击倒、伤害一一对应，别的项（救人、名字、ID）不发"] = (
        one["mate_stats"] == [{"kills": 2, "assists": 1, "knockdowns": 3, "damage": 640},
                              {"kills": None, "assists": None, "knockdowns": None, "damage": None}])
    out["没读出来的项发 null（不当作 0）"] = any(m["legend"] is None and m["damage"] is None for m in body["matches"])
    out["日活：日期、Apex 视频设置里的分辨率和显示模式、画面比例设置"] = (
        body["ping"]["day"] == "2026-10-21" and body["ping"]["game_res"] == "2560x1600"
        and body["ping"]["game_mode"] == "fullscreen" and body["ping"]["aspect"] == "auto")
    out["请求只带 Evo 的版本号（User-Agent）"] = path == "/v1/report" and agent == f"Evo/{storage.APP_VERSION}"
    out["对局编号是一串乱码（看不出本机的局号和时间）"] = all(re.fullmatch(r"[0-9a-f]{20}", m["k"]) for m in body["matches"])

    Handler.requests.clear()
    out["再发一次：没有新的局、今天发过日活，不联网"] = (
        telemetry.send(now=NOW)["matches"] == 0 and not Handler.requests)
    later = NOW + timedelta(hours=1)
    telemetry.send(now=later)
    out["1 小时后：刚才太新的那局发了，别的不重发"] = [m["k"] for m in sent_matches()] == [
        telemetry.match_key(body["id"], storage.get_match(conn, recent))]

    # 战绩页改了伤害：再发这一局，带上改了哪一项（不发原来的值）
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]
    row = storage.get_match(conn, first)
    form = {k: "" if row[k] is None else str(row[k]) for k in panel_app.EDITABLE}
    client.post(f"/matches/{first}/update", data={"token": token, **form, "damage": "1300", "next": "/matches"})
    out["战绩页改了伤害：数据库记下改了 damage"] = storage.get_match(conn, first)["edited"] == "damage"
    client.post(f"/matches/{first}/update", data={"token": token, **form, "damage": "1300", "kills": "5",
                                                  "next": "/matches"})
    out["再改击杀：两项都记着"] = storage.get_match(conn, first)["edited"] == "damage,kills"
    Handler.requests.clear()
    telemetry.send(now=later)
    again = sent_matches()
    out["改过的局再发一次（服务器上覆盖），带上 edited 和新的值"] = (
        len(again) == 1 and again[0]["edited"] == ["damage", "kills"] and again[0]["damage"] == 1300
        and again[0]["review"] is False)

    Handler.requests.clear()
    telemetry.send(now=later + timedelta(days=1))
    out["第二天：只发日活"] = (len(Handler.requests) == 1 and Handler.requests[0][1].get("ping", {}).get("day")
                         == "2026-10-22" and not sent_matches())

    # 连不上、服务器拒绝：不算发过，下次再发
    many = [add_match(conn, f"2026-10-22 0{i // 60}:{i % 60:02d}:00") for i in range(250)]
    url, Handler.status = telemetry.STATS_URL, 500
    Handler.requests.clear()
    result = telemetry.send(now=later + timedelta(days=1))
    out["服务器拒绝：记下没发出去和原因，不算发过"] = (
        not result["ok"] and "拒绝" in result["error"] and app_settings.get(telemetry.LAST_KEY)["ok"] is False)
    Handler.status = 200
    telemetry.STATS_URL = "http://127.0.0.1:9"  # 没有服务器的端口
    result = telemetry.send(now=later + timedelta(days=1))
    out["连不上：不报错，写明连不上"] = not result["ok"] and "连不上" in result["error"]
    telemetry.STATS_URL = url
    Handler.requests.clear()
    result = telemetry.send(now=later + timedelta(days=1))
    sizes = [len(body["matches"]) for _, body, _ in Handler.requests]
    out["又连上了：250 局分 3 次发（100、100、50）"] = result["ok"] and result["matches"] == 250 and sizes == [100, 100, 50]

    # 日活没发出去：记着这一天，下次连上时补发（2026-10-24 用户决定）
    day3, day4 = later + timedelta(days=2), later + timedelta(days=3)
    telemetry.STATS_URL = "http://127.0.0.1:9"
    telemetry.send(now=day3)
    telemetry.STATS_URL = url
    out["日活没发出去：记下这一天"] = app_settings.get(telemetry.MISSED_KEY) == ["2026-10-23"]
    Handler.requests.clear()
    last = add_match(conn, "2026-10-23 20:00:00")
    result = telemetry.send(now=day4)
    days = [(body.get("ping", {}).get("day"), len(body["matches"])) for _, body, _ in Handler.requests]
    out["第二天连上了：先补发前一天的日活（和没发的局一起），再发今天的，一个请求一天"] = (
        result["ok"] and result["matches"] == 1 and days == [("2026-10-23", 1), ("2026-10-24", 0)]
        and app_settings.get(telemetry.MISSED_KEY) is None and app_settings.get(telemetry.PING_KEY) == "2026-10-24")
    Handler.requests.clear()
    out["补发完：再发一次不联网"] = not telemetry.send(now=day4)["ping"] and not Handler.requests
    app_settings.set_value(telemetry.MISSED_KEY, [f"2026-09-{d:02d}" for d in range(1, 31)] + ["2026-08-31", "坏的", 5])
    out["最多补 30 天（更早的不补），格式不对的去掉"] = (
        telemetry._ping_days("2026-10-24") == [f"2026-09-{d:02d}" for d in range(1, 31)])
    telemetry.set_consent(True)
    out["重新打开匿名统计：以前没发出去的日活不补"] = app_settings.get(telemetry.MISSED_KEY) is None
    for match_id in [before, first, second, recent, last, *many]:
        storage.delete_match(conn, match_id)
    return out


def panel_checks(conn):
    out = {}
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]

    def page(url="/"):
        return " ".join(client.get(url).get_data(as_text=True).split())

    def post(url, **form):
        return " ".join(client.post(url, data={"token": token, **form}, follow_redirects=True)
                        .get_data(as_text=True).split())

    ask = "愿意发送匿名的对局数据，帮助改进 Evo 吗？"
    for key in KEYS:
        app_settings.set_value(key, None)
    app_settings.set_value("update_auto", None)
    out["第一次打开：先只问自动检查新版本"] = ask not in page()
    app_settings.set_value("update_auto", False)
    first = page()
    out["回答了以后：顶部问要不要发送匿名统计"] = ask in first and ask in page("/matches")
    out["两个按钮一样的样式（不引导点同意）"] = (
        '<button type="submit" class="btn">同意</button>' in first
        and '<button type="submit" class="btn">不了</button>' in first)
    out["提问里能点进设置页（关闭在那里），不再说能看原样、能删除"] = (
        'class="text-link" href="/settings#stats"' in first and "原样" not in first and "删除已发送" not in first)

    with mock.patch.object(telemetry, "send") as sending:
        got = post("/settings/telemetry", on="0", next="/")
        out["点\"不了\"：不发送，不再问，提示以后可以在设置里打开"] = (
            telemetry.consent() is False and ask not in got and "以后可以在" in got and not sending.called)
        settings = page("/settings")
        out["设置页：匿名统计一栏，开关关着，写着不发送"] = (
            '<h2 id="h-stats">匿名统计</h2>' in settings
            and 'aria-checked="false" aria-labelledby="stats-name"' in settings and "已关闭：不发送任何数据" in settings)
        out["设置页的说明：Evo 会将数据用于改进识别（不写\"打开后，Evo ……\"）"] = (
            "Evo 会将数据用于改进识别" in settings and "打开后，Evo" not in settings)
        got = post("/settings/telemetry", on="1", next="/settings")
        out["设置页打开：同意、马上在后台发一次、开关开着"] = (
            telemetry.consent() is True and sending.called
            and 'aria-checked="true" aria-labelledby="stats-name"' in got and "已开启匿名统计" in got)
        got = post("/settings/telemetry", on="0", next="/settings")
        out["设置页关掉：提示不再发送，不再提删除"] = (
            telemetry.consent() is False and "已关闭匿名统计" in got and "删除" not in got.split("已关闭匿名统计")[1][:40])
        telemetry.set_consent(True)

    app_settings.set_value(telemetry.LAST_KEY, {"ok": True, "matches": 3, "ping": True, "error": None,
                                                "at": "2026-10-21 21:00"})
    settings = page("/settings")
    stats = settings.split('<h2 id="h-stats">')[1].split("</section>")[0]
    out["开着时：只有开关和说明，不显示从什么时候起、已发送几局、上次发送"] = (
        'aria-checked="true" aria-labelledby="stats-name"' in stats and "已发送" not in stats
        and "上次" not in stats and "起的对局" not in stats and "已关闭：不发送任何数据" not in stats)
    out["没有\"查看会发送的内容\"、随机编号、\"关闭并删除已发送的数据\""] = (
        "查看会发送的内容" not in settings and "随机编号" not in settings
        and app_settings.get(telemetry.ID_KEY) not in settings and "关闭并删除已发送的数据" not in settings)
    out["删除的地址已经去掉"] = client.post("/settings/telemetry/delete", data={"token": token}).status_code == 404
    out["关于 Evo：不再写'全本地'（2026-10-24 用户决定：不强调不联网）"] = (
        "关于 Evo" in settings and "全本地" not in settings and "会联网" not in settings)

    out["没带口令：拒绝"] = client.post("/settings/telemetry", data={"on": "1"}).status_code == 403
    return out


def main():
    print("匿名统计（同意了才发、只发说好的几项、日活、重发改过的局、连不上、面板的提问和设置页）")
    server, base = start_server()
    url = telemetry.STATS_URL
    telemetry.STATS_URL = base  # 绝不连到真正的统计服务器
    panel_app.app.config["DB"] = storage.DB_PATH
    conn = storage.connect()
    try:
        checks = {**send_checks(conn), **panel_checks(conn)}
    finally:
        conn.close()
        telemetry.STATS_URL = url
        server.shutdown()
        for key in KEYS:
            app_settings.set_value(key, None)
        app_settings.set_value("update_auto", None)
        telemetry.SENT_PATH.unlink(missing_ok=True)
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
