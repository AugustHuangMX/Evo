"""总览页的地图轮换（map_rotation.py、panel/templates/_maps.html）。不用截图，不联网：统计服务器由本机的小服务器冒充。

- 先问（2026-10-28 用户决定：隐私说明承诺过、代码签名也要求）：还没问过时卡片上只问一句，不联网；点"显示"才下载，
  点"不用了"卡片就没了。打开以后，页面本身不等网络：还没下载过写"正在获取…"，由页面上的脚本马上下载（POST /maps/refresh）
- 下载的只有 GET /v1/maps（不带编号、战绩）；英文名换成简体（config/rotation.json），查不到的照写英文；
  轮换模式写玩法 + 地图；电脑时钟和服务器差多少，倒计时按服务器的时间算
- 现在这张过了点就当下一张在打，要重新下载；两张都过了点写"—"；服务器没给的模式不反复下载
- 服务器出错、格式不对：写原因，不覆盖存着的
- 设置页"常规"里能随时开关（没问过显示成关）
- 卡片在"最近对局"上面（左边一列），六维图那一列不动；没有对局时也显示

运行：python tests/test_map_rotation.py
"""

import http.server
import json
import os
import re
import threading
import time

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_settings
import map_rotation
import panel_app
import storage
import telemetry

os.environ["no_proxy"] = "127.0.0.1,localhost"  # 电脑上设了代理时，本机的测试服务器也直接连
NOW = 1_800_000_000  # 测试里"电脑现在的时间"（秒）
SKEW = 120  # 服务器比电脑快 2 分钟


def answer(now=NOW + SKEW, ltm=True):
    """统计服务器的回答（格式见 server/stats/worker.js 的 maps）。"""
    modes = {
        "pubs": {"current": {"map": "Storm Point", "code": "storm_point_rotation", "event": None, "start": now - 600,
                             "end": now + 3000},
                 "next": {"map": "World's Edge", "code": "worlds_edge_rotation", "event": None, "start": now + 3000,
                          "end": now + 8400}},
        "ranked": {"current": {"map": "Kings Canyon", "code": "kings_canyon_rotation", "event": None, "start": now - 600,
                               "end": now + 47142},
                   "next": {"map": "New Map 2027", "code": "new_map", "event": None, "start": now + 47142,
                            "end": now + 133542}},
    }
    if ltm:
        modes["mixtape"] = {"current": {"map": "Habitat 4", "code": "habitat_tdm", "event": "TDM", "start": now - 60,
                                        "end": now + 511},
                            "next": {"map": "Fragment East", "code": "fragment_control", "event": "Control",
                                     "start": now + 511, "end": now + 1411}}
    return {"v": 1, "now": now, "modes": modes}


class Handler(http.server.BaseHTTPRequestHandler):
    requests = []  # 收到的请求：(路径, 请求头)
    status = 200
    body = json.dumps(answer()).encode("utf-8")

    def do_GET(self):  # noqa: N802
        self.requests.append((self.path, dict(self.headers)))
        data = self.body if self.status == 200 else b'{"ok": false, "error": "no key"}'
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


def card(page):
    """总览页上的地图卡片（没有返回 ""）。"""
    found = re.search(r'<section class="card maps-card[^"]*".*?</section>', page, re.S)
    return found.group(0) if found else ""


def slots(html):
    """卡片里每一格的文字（去掉标签、多余空白）。"""
    return [" ".join(re.sub(r"<[^>]+>", " ", s).split())
            for s in re.findall(r'<div class="maps-slot".*?(?=<div class="maps-slot"|</div>\s*(?:<p|</section>))', html, re.S)]


def checks():
    out = {}
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]

    def overview():
        return client.get("/").get_data(as_text=True)

    def refresh(now=None):
        if now is None:
            return client.post("/maps/refresh", data={"token": token}).get_json()
        result = map_rotation.fetch(now=now)
        return {**result, "html": card(overview())}

    # ---- 还没问过：卡片上问一次，不联网 ----
    app_settings.set_value(map_rotation.SETTING_KEY, None)
    map_rotation.CACHE_PATH.unlink(missing_ok=True)
    Handler.requests.clear()
    asked = overview()
    not_yet = client.post("/maps/refresh", data={"token": token}).get_json()
    settings = client.get("/settings").get_data(as_text=True)
    out["还没问过：卡片上问一句（显示 / 不用了），没有下载的脚本，不联网"] = (
        not map_rotation.enabled() and "显示匹配、排位、轮换模式现在的地图和倒计时？" in card(asked)
        and re.search(r'name="on" value="1">\s*<input type="hidden" name="next" value="/">\s*<button[^>]*>显示', asked)
        and re.search(r'name="on" value="0">.*?>不用了</button>', asked, re.S)
        and "/maps/refresh" not in asked and not not_yet["ok"] and not Handler.requests)
    out["设置页\"常规\"里有开关，还没问过时是关的"] = (
        re.search(r'id="maps-name">总览页显示当前地图</div>.*?aria-checked="false"', settings, re.S) is not None
        and settings.index('id="h-general"') < settings.index('id="maps-name"') < settings.index('id="h-sounds"'))
    reply = client.post("/settings/maps", data={"token": token, "on": "1", "next": "/"})
    first = overview()
    out["点了\"显示\"：回到总览页；还没下载过写\"正在获取…\"，页面上的脚本马上下载（页面本身不等网络）"] = (
        reply.status_code == 302 and reply.headers["Location"].endswith("/") and map_rotation.enabled()
        and 'data-fetch="1"' in card(first) and "正在获取…" in card(first) and "/maps/refresh" in first
        and not Handler.requests)
    out["卡片在\"最近对局\"上面、左边那一列；六维图那一列不动"] = (
        re.search(r'<div class="ov-main">\s*<section class="card maps-card".*?aria-label="最近对局".*?</div>\s*'
                  r'(?:\{#.*?#\}\s*)?<div class="ov-side">', first, re.S) is not None)
    out["写着数据来源（链接交给系统浏览器打开）"] = (
        'href="https://apexlegendsstatus.com" target="_blank" rel="noopener noreferrer">数据来自 Apex Legends Status' in first)

    # ---- 下载 ----
    result = refresh(now=NOW)
    saved = map_rotation.cached()
    out["下载：只有 GET /v1/maps，不带编号；存进数据文件夹，记下服务器比电脑快多少"] = (
        result["ok"] and [r[0] for r in Handler.requests] == ["/v1/maps"]
        and Handler.requests[0][1].get("User-Agent") == f"Evo/{storage.APP_VERSION}"
        and saved is not None and saved["offset"] == SKEW)
    view = map_rotation.view(saved, now=NOW)
    pubs, ranked, mixtape = view["modes"]
    out["匹配：风暴点，还剩 50 分钟（按服务器的时间算），下一张世界尽头"] = (
        (pubs["name"], pubs["title"], pubs["left"], pubs["next"]) == ("匹配", "风暴点", "50:00", "世界尽头")
        and pubs["end"] == NOW + 3000)
    out["排位：诸王峡谷，还剩 13:05:42；下一张是不认识的新图，照写英文"] = (
        (ranked["title"], ranked["left"], ranked["next"]) == ("诸王峡谷", "13:05:42", "New Map 2027"))
    out["轮换模式：写玩法（团队死斗）、下面一行地图（栖息地4）；下一场写玩法 · 地图"] = (
        (mixtape["name"], mixtape["title"], mixtape["sub"], mixtape["left"], mixtape["next"])
        == ("轮换模式", "团队死斗", "栖息地4", "08:31", "控制 · 碎片东部"))
    out["都还没到点：不用重新下载"] = view["fetch"] is False

    # 页面上（用真实时间算：把测试数据挪到现在）
    real = time.time()
    Handler.body = json.dumps(answer(now=int(real))).encode("utf-8")
    result = refresh()
    shown = slots(result["html"])
    out["页面上：三格依次是匹配、排位、轮换模式，地图、还剩多久、下一张；不再自动下载"] = (
        result["ok"] and len(shown) == 3 and shown[0].startswith("匹配 风暴点 还剩") and "下一张： 世界尽头" in shown[0]
        and shown[2].startswith("轮换模式 团队死斗 栖息地4 还剩") and "下一场： 控制 · 碎片东部" in shown[2]
        and 'data-fetch="1"' not in card(overview()))
    out["倒计时用的换图时间（data-end）是电脑上的时间"] = (
        re.search(r'data-end="(\d+)"', result["html"]) is not None
        and abs(int(re.search(r'data-end="(\d+)"', result["html"]).group(1)) - (int(real) + 3000)) <= 1)

    # ---- 过了点 ----
    later = map_rotation.view(saved, now=NOW + 3000 + 5)  # 电脑时间 NOW + 3000 是匹配这张结束的时候，过了 5 秒
    out["现在这张过了点：当作下一张在打（再下一张不知道），要重新下载"] = (
        later["modes"][0]["title"] == "世界尽头" and later["modes"][0]["next"] is None and later["fetch"] is True)
    gone = map_rotation.view(saved, now=NOW + 10_000)
    out["两张都过了点：写\"—\""] = gone["modes"][0]["title"] is None and gone["modes"][2]["title"] is None
    no_ltm = {**saved, "modes": {k: v for k, v in saved["modes"].items() if k != "mixtape"}}
    view = map_rotation.view(no_ltm, now=NOW)
    out["服务器没给某个模式：那一格写\"—\"，不为它反复下载"] = view["modes"][2]["title"] is None and view["fetch"] is False
    out["还剩多久的写法：不到 1 小时不写小时"] = (
        map_rotation.left_text(3725) == "1:02:05" and map_rotation.left_text(59) == "00:59"
        and map_rotation.left_text(-3) == "00:00")

    # ---- 下载不成 ----
    before = map_rotation.CACHE_PATH.read_text(encoding="utf-8")
    Handler.status = 503
    failed = refresh()
    Handler.status = 200
    out["服务器出错：写明原因（过一会儿自动再试），不覆盖存着的，照样显示存着的"] = (
        not failed["ok"] and "503" in failed["error"] and "没取到最新的地图轮换" in failed["html"]
        and "风暴点" in failed["html"] and map_rotation.CACHE_PATH.read_text(encoding="utf-8") == before)
    good = Handler.body
    Handler.body = b"Error: API key doesn't exist !"
    not_json = map_rotation.fetch()
    Handler.body = json.dumps({"v": 2, "now": 1, "modes": {}}).encode("utf-8")
    new_format = map_rotation.fetch()
    Handler.body = good
    out["格式不对（不是 JSON、不认识的版本）：不存"] = (
        not not_json["ok"] and not new_format["ok"] and map_rotation.CACHE_PATH.read_text(encoding="utf-8") == before)
    url = telemetry.STATS_URL
    telemetry.STATS_URL = "http://127.0.0.1:9"  # 没有服务器的端口
    offline = map_rotation.fetch()
    telemetry.STATS_URL = url
    out["连不上：写明连不上"] = not offline["ok"] and "连不上" in offline["error"]

    # ---- 没有对局 ----
    db = panel_app.app.config["DB"]
    empty = storage.DB_PATH.parent / "maps-empty.db"
    empty.unlink(missing_ok=True)
    storage.connect(empty).close()
    panel_app.app.config["DB"] = empty
    no_games = overview()
    panel_app.app.config["DB"] = db
    out["没有对局时也显示地图"] = "还没有任何对局记录" in no_games and "风暴点" in card(no_games)

    # ---- 不要了、设置页的开关 ----
    settings = client.get("/settings").get_data(as_text=True)
    out["打开以后设置页的开关是开的"] = re.search(r'id="maps-name">.*?aria-checked="true"', settings, re.S) is not None
    client.post("/settings/maps", data={"token": token, "on": "0", "next": "/"})
    Handler.requests.clear()
    off_page = overview()
    off_refresh = client.post("/maps/refresh", data={"token": token}).get_json()
    out["点了\"不用了\"（或在设置页关掉）：总览页没有卡片、没有下载的脚本，也不联网"] = (
        not map_rotation.enabled() and "maps-card" not in off_page and "/maps/refresh" not in off_page
        and not off_refresh["ok"] and not Handler.requests)
    back = client.post("/settings/maps", data={"token": token, "on": "1", "next": "/settings"})
    out["在设置页再打开：回到设置页，总览页又显示"] = (
        back.headers["Location"].endswith("/settings") and map_rotation.enabled() and "maps-card" in overview())
    out["没带口令：下载被拒绝"] = client.post("/maps/refresh").status_code == 403
    return out


def main():
    print("总览页的地图轮换（下载、中文名、倒计时、过了点、连不上、设置页的开关）")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url, db = telemetry.STATS_URL, panel_app.app.config["DB"]
    telemetry.STATS_URL = f"http://127.0.0.1:{server.server_address[1]}"  # 绝不连到真正的统计服务器
    app_settings.set_value("update_auto", False)  # 免得页面顶部问要不要自动检查
    path = storage.DB_PATH.parent / "maps-test.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)
    storage.insert_match(conn, {"ended_at": "2026-10-28 20:00:00", "player_name": "Me", "placement": 3, "legend": "恶灵"})
    conn.close()
    panel_app.app.config.update(DB=path, TESTING=True)
    try:
        result = checks()
    finally:
        telemetry.STATS_URL, panel_app.app.config["DB"] = url, db
        server.shutdown()
        for key in (map_rotation.SETTING_KEY, "update_auto"):
            app_settings.set_value(key, None)
        map_rotation.CACHE_PATH.unlink(missing_ok=True)
    return report(result)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
