"""Evo 窗口的"赛季"页面（panel_seasons.py、seasons.html、_season_import.html）和设置页上的录入按钮。
不用截图，不开浏览器（Flask 自带的测试客户端）。

- 导航里有"赛季"，在"数据"和"设置"之间；还没有数据时写怎么开始
- 录入模式：开始 / 结束（要带口令；只能回到设置页或"赛季"页面）；看演示数据时不能开；开着时显示倒计时、读到几项、
  "结束录入"；设置页"识别"一节有同样一行，有待核对时有"去核对"
- 待核对：每项一张卡片；对不上的格子标出来（aria-invalid，连到写原因的那一行）；约数标"约"；
  "保存全部没问题的"只保存没问题的；保存（数字带逗号也行，改过的记下来）、填错不保存、删除
- 历史赛季：全部 / 排位切换，生涯合计只在"全部"里、在第一行；胜率、前 5 率、场均击杀等算对；点开一行有 15 个格子
- 多账号：只看选中的账号

运行：python tests/test_seasons_page.py
"""

import html as htmllib
import re
import tempfile
from pathlib import Path

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import panel_app
import season_import
import storage

FOLDER = Path(tempfile.mkdtemp(prefix="evo-seasons-page-", dir=storage.DB_PATH.parent.parent))
BASE = {"read_at": "2026-11-01 16:12:32", "wins": 26, "top5": 81, "damage": 169379, "damage_max": 2751,
        "damage_avg": 537.71, "kills": 429, "deaths": 353, "kd": 1.22, "kills_max": 10, "knockdowns": 557,
        "assists": 426, "win_streak": 4, "revives": 127, "respawns": 73}


def new_db(folder_name, file_name="evo.db"):
    """大号 ImperialHzd、小号 AltAccount 各打过一局。大号：待核对 S30 排位（没问题）、S30 全部（击杀/阵亡比读错）；
    保存过 S29 全部、S29 排位、生涯合计（前 5 名是约数）。小号：保存过 S29 全部。"""
    path = FOLDER / folder_name / file_name
    path.parent.mkdir()
    conn = storage.connect(path)
    for name in ("ImperialHzd", "AltAccount"):
        storage.insert_match(conn, {"player_name": name, "ended_at": "2026-11-01 15:00:00", "mode": "ranked"})
    storage.answer_name(conn, "AltAccount")
    ids = {}

    def add(key, name="ImperialHzd", **row):
        ids[key] = storage.save_season_pending(conn, {**BASE, "player_name": name, **row})

    add("s30r", season=30, kind="ranked", season_name="排位", games=315)
    add("s30a", season=30, kind="all", season_name="诸神烙印", games=315, kd=0.68)
    for key, row in (("s29a", dict(season=29, kind="all", season_name="超频", games=315)),
                     ("s29r", dict(season=29, kind="ranked", season_name="排位", games=315)),
                     ("career", dict(season=None, kind="career", season_name="大逃杀", games=10146, wins=606,
                                     top5=2820, damage=4678720, damage_avg=461.14, kills=10256, deaths=10920, kd=0.94,
                                     approx=["top5"]))):
        add(key, **row)
        storage.save_season(conn, ids[key])
    add("alt", name="AltAccount", season=29, kind="all", season_name="超频", games=315)
    storage.save_season(conn, ids["alt"])
    accounts = {a["name"]: a["id"] for a in storage.accounts(conn)}
    conn.close()
    return path, ids, accounts


def text(html):
    return htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))


def flashes(html):
    return re.findall(r'class="flash flash-(\w+)" role="status">([^<]+)<', html)


def main():
    print("\"赛季\"页面（录入历史赛季数据）")
    out = {}
    token = panel_app.app.config["TOKEN"]
    season_import.stop()

    empty_db = FOLDER / "empty" / "evo.db"
    empty_db.parent.mkdir()
    storage.connect(empty_db).close()
    panel_app.app.config["DB"] = str(empty_db)
    client = panel_app.app.test_client()
    home = client.get("/").get_data(as_text=True)
    nav = re.findall(r'<nav class="nav".*?</nav>', home, re.S)[0]
    out["导航里有\"赛季\"，在\"数据\"和\"设置\"之间"] = text(nav).split() == ["总览", "战绩", "数据", "赛季", "设置"]
    raw = client.get("/seasons").get_data(as_text=True)
    page = text(raw)
    out["还没有数据：写怎么开始，有\"开始录入\"按钮，没有\"待核对\"一节"] = (
        "还没有保存过历史赛季。点上面的\"开始录入\"" in page and "开始录入" in page and 'id="pending"' not in raw)

    db, ids, accounts = new_db("main")
    panel_app.app.config["DB"] = str(db)
    client = panel_app.app.test_client()
    with client.session_transaction() as s:
        s["account"] = accounts["ImperialHzd"]
    out["开始录入不带口令：拒绝"] = client.post("/seasons/import/start").status_code == 403 and not season_import.active()
    r = client.post("/seasons/import/start", data={"token": token, "next": "/settings"})
    out["开始录入：开 10 分钟，回到设置页这一行"] = (
        season_import.active() and r.headers["Location"].endswith("/settings#season-import")
        and 590 <= season_import.remaining() <= 600)
    settings = client.get("/settings").get_data(as_text=True)
    out["设置页\"识别\"一节：正在录入、还剩几分、读到 2 项、\"结束录入\"、\"去核对\""] = (
        settings.find('id="season-import"') > settings.find('id="h-rec"') > 0
        and "正在录入" in text(settings) and "已读到 2 项待核对" in text(settings) and "结束录入" in settings
        and "去核对 →" in settings)
    status = client.get("/seasons/status").get_json()
    out[f"录入中页面每隔几秒问的：开着、还剩几秒、2 项待核对（{status}）"] = (
        status["active"] and status["pending"] == 2 and status["remaining"] > 500)
    r = client.post("/seasons/import/stop", data={"token": token, "next": "https://example.com/"})
    out["结束录入；next 不是这两页时回到\"赛季\"页面"] = (
        not season_import.active() and r.headers["Location"].endswith("/seasons#season-import"))

    html = client.get("/seasons").get_data(as_text=True)
    cards = re.findall(r'<article class="card season-card[^"]*" id="season-(\d+)".*?</article>', html, re.S)
    bad_card = re.search(rf'<article[^>]*id="season-{ids["s30a"]}".*?</article>', html, re.S)[0]
    invalid = re.findall(r'name="(\w+)"[^>]*aria-invalid="true" aria-describedby="([^"]+)"', bad_card)
    reasons = dict(re.findall(r'<li id="(why-[^"]+)">([^<]+)</li>', bad_card))
    out[f"待核对：每项一张卡片（{len(cards)} 张）"] = sorted(map(int, cards)) == sorted([ids["s30r"], ids["s30a"]])
    out[f"对不上的格子：击杀、阵亡、击杀/阵亡比标出来，读屏软件连着念原因（{invalid}）"] = (
        [f for f, _ in invalid] == ["kills", "deaths", "kd"]
        and all(reasons.get(why, "").startswith("击杀 429 ÷ 阵亡 353 = 1.22") for _, why in invalid))
    out["数字带千位逗号（和游戏里一样）"] = 'value="169,379"' in bad_card
    out["\"保存全部没问题的（1 项）\""] = "保存全部没问题的（1 项）" in html

    r = client.post("/seasons/save-clean", data={"token": token, "next": "/seasons"})
    conn = storage.connect(db)
    status_of = {r["stat_id"]: r["status"] for r in storage.season_rows(conn)}
    conn.close()
    out["\"保存全部没问题的\"：只保存了没问题的那项"] = (
        status_of[ids["s30r"]] == "saved" and status_of[ids["s30a"]] == "pending")

    form = {"token": token, "next": "/seasons", **{k: str(v) for k, v in BASE.items() if k != "read_at"},
            "games": "315", "damage": "169,379", "kd": "1.22"}
    client.post(f"/seasons/{ids['s30a']}/save", data={**form, "kills": "abc"})
    after_bad = client.get("/seasons").get_data(as_text=True)
    conn = storage.connect(db)
    still = {r["stat_id"]: (r["status"], r["kd"], r["edited"]) for r in storage.season_rows(conn)}
    conn.close()
    out[f"填错（击杀填了 abc）：不保存，说清楚哪一格（{flashes(after_bad)}）"] = (
        still[ids["s30a"]] == ("pending", 0.68, None)
        and any(kind == "error" and "击杀" in msg and "整数" in msg for kind, msg in flashes(after_bad)))
    client.post(f"/seasons/{ids['s30a']}/save", data=form)
    conn = storage.connect(db)
    saved = {r["stat_id"]: (r["status"], r["kd"], r["damage"], r["edited"]) for r in storage.season_rows(conn)}
    conn.close()
    out["改好保存：数字带逗号也行，改过的项记下来"] = saved[ids["s30a"]] == ("saved", 1.22, 169379, "kd")
    client.post(f"/seasons/{ids['s30r']}/delete", data={"token": token, "next": "/seasons"})
    conn = storage.connect(db)
    out["删除"] = ids["s30r"] not in {r["stat_id"] for r in storage.season_rows(conn)}
    conn.close()

    all_view = client.get("/seasons").get_data(as_text=True)
    rows = re.findall(r'<tr class="season-row[^"]*" id="season-(\d+)">\s*<th scope="row">([^<]+)</th>(.*?)</tr>',
                      all_view, re.S)
    cells = {title: re.findall(r'<td class="right num">\s*([^<]*?)\s*(?:<|$)', rest) for _, title, rest in rows}
    out[f"历史赛季\"全部\"：生涯合计在第一行，然后赛季从新到旧（{[t for _, t, _ in rows]}）"] = (
        [t for _, t, _ in rows] == ["生涯合计", "第 30 赛季 · 诸神烙印（全部）", "第 29 赛季 · 超频（全部）"])
    out[f"  第 29 赛季：局数 315、胜率 8.3%、前 5 率 25.7%、场均击杀 1.36、击杀/阵亡比 1.22、场均伤害 538（{cells.get('第 29 赛季 · 超频（全部）')}）"] = (
        cells.get("第 29 赛季 · 超频（全部）") == ["315", "8.3%", "25.7%", "1.36", "1.22", "538"])
    out["  生涯合计的前 5 率标\"约\"（游戏只写了 2.82K）"] = "approx-tag" in re.search(
        rf'id="season-{ids["career"]}".*?</tr>', all_view, re.S)[0]
    ranked_view = client.get("/seasons?kind=ranked").get_data(as_text=True)
    out["\"排位\"：只有排位的赛季，没有生涯合计"] = (
        re.findall(r'<th scope="row">([^<]+)</th>', ranked_view) == ["第 29 赛季 · 排位"])
    opened = client.get(f"/seasons?open={ids['s29a']}").get_data(as_text=True)
    detail = re.search(r'<tr class="detail-row">.*?</tr>', opened, re.S)
    out["点开一行：15 个格子、\"保存修改\"、\"删除\""] = (
        detail is not None and len(re.findall(r'<input type="text"', detail[0])) == 15
        and "保存修改" in detail[0] and "删除" in detail[0])
    out["只看选中的账号（小号的 S29 不在大号这里）"] = all_view.count("第 29 赛季 · 超频（全部）") == 1 and \
        ids["alt"] not in [int(i) for i, _, _ in rows]
    with client.session_transaction() as s:
        s["account"] = accounts["AltAccount"]
    alt_view = client.get("/seasons").get_data(as_text=True)
    out["换到小号：只有小号的 S29"] = re.findall(r'<th scope="row">([^<]+)</th>', alt_view) == ["第 29 赛季 · 超频（全部）"]

    demo, _, _ = new_db("demo", "demo.db")
    panel_app.app.config["DB"] = str(demo)
    client = panel_app.app.test_client()
    page = client.get("/seasons").get_data(as_text=True)
    client.post("/seasons/import/start", data={"token": token, "next": "/seasons"})
    out["看演示数据：\"开始录入\"不能点，点了也不开"] = (
        re.search(r'<button type="submit" class="btn btn-primary"[^>]*disabled>开始录入', page) is not None
        and not season_import.active())

    panel_app.app.config["DB"] = storage.DB_PATH
    season_import.stop()
    return report(out)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
