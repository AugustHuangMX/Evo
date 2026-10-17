"""多账号：账号对照表（storage.py）和"检测到新的玩家名"弹窗（panel_app.py + base.html）。不用截图。

- 对照表：第一个名字自动成为第一个账号；只差空格的算同一个名字；新名字先单独算一个账号、等回答；
  合并后局数加在一起、显示最近一局用的名字；"另一个账号"保持单独；名字还没登记时回答也有效；
  旧数据库（版本 2）升级后有对照表
- 弹窗（用 Flask 自带的测试客户端，不开浏览器）：有新名字时弹出，名字很像的账号排在前面；没有口令不能提交；
  选"同一个人"后合并、不再弹、有提示；选"另一个账号"后单独一个账号；选了不存在的账号不保存；
  "稍后再问"后同一个窗口里不再弹，新开的窗口（新的会话）照样问；没有新名字时不弹
- 切换账号：只有 1 个账号时不显示切换、全部照常显示（包括没认出玩家名的局）；2 个以上时默认看最近玩过的账号，
  总览、战绩、传奇筛选、待确认数都只算这个账号；切换后换页面还记得，去掉页码和展开的那一局；
  "全部账号"时段位和走势图提示选一个账号；每局详情里有玩家名
- 设置页"账号"一栏：把名字并到别的账号、再拆回来；并到不存在的账号不保存

运行：python tests/test_accounts.py
"""

import re
import sqlite3
import tempfile
from pathlib import Path

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import panel_app
import storage

FOLDER = Path(tempfile.mkdtemp(prefix="evo-accounts-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里


def new_db(name, games):
    """新建一个数据库，按顺序存几局。games：[(玩家名, 结束时间), ...]。"""
    path = FOLDER / f"{name}.db"
    path.unlink(missing_ok=True)  # 同一个程序里跑第二遍时，从空的开始
    conn = storage.connect(path)
    for player, ended in games:
        storage.insert_match(conn, {"player_name": player, "ended_at": ended, "placement": 5})
    return path, conn


def summary(conn):
    """[(显示的名字, 局数, 是否已确认), ...]"""
    return [(a["name"], a["games"], a["confirmed"]) for a in storage.accounts(conn)]


def table_checks():
    out = {}
    _, conn = new_db("table", [("ImperialHzd", "2026-10-16 21:00:00"), ("Imperial Hzd", "2026-10-16 22:00:00")])
    out["first"] = summary(conn)
    storage.insert_match(conn, {"player_name": "Yukinoshita", "ended_at": "2026-10-17 20:00:00"})
    out["pending"] = summary(conn)
    storage.answer_name(conn, "Yukinoshita", storage.accounts(conn)[0]["id"])
    out["merged"] = summary(conn)
    out["names"] = storage.accounts(conn)[0]["names"]
    storage.insert_match(conn, {"player_name": "Alt2nd", "ended_at": "2026-10-17 21:00:00"})
    storage.answer_name(conn, "Alt2nd")  # 还没调用过 accounts()：名字还没登记进对照表，回答也要有效
    out["separate"] = summary(conn)
    conn.close()

    path = FOLDER / "v2.db"  # 版本 2 的数据库：有 player_name 列，没有对照表
    path.unlink(missing_ok=True)
    old = sqlite3.connect(path)
    old.execute(f"CREATE TABLE matches ({', '.join(f'{k} {v}' for k, v in storage.COLUMNS.items())})")
    old.execute("PRAGMA user_version = 2")
    old.execute("INSERT INTO matches (player_name, ended_at) VALUES ('ImperialHzd', '2026-10-17 17:00:00')")
    old.commit()
    old.close()
    conn = storage.connect(path)
    out["upgraded"] = (conn.execute("PRAGMA user_version").fetchone()[0], summary(conn))
    conn.close()
    return out


def panel_checks():
    """用测试客户端走一遍弹窗。返回每一步看到的结果。"""
    path, conn = new_db("panel", [("ImperialHzd", "2026-10-16 21:00:00"), ("ImperialHzd", "2026-10-16 22:00:00"),
                                  ("Yukino2nd", "2026-10-17 12:00:00")])
    storage.answer_name(conn, "Yukino2nd")  # 已确认的另一个账号（最近玩过）
    storage.insert_match(conn, {"player_name": "Imperial_Hzd", "ended_at": "2026-10-17 14:40:00"})  # 新名字，和 ImperialHzd 很像
    conn.close()
    panel_app.app.config.update(DB=path, TESTING=True)
    token = panel_app.app.config["TOKEN"]
    out = {}

    def ids(page):
        return re.findall(r'name="account" value="(\w+)"', page)

    client = panel_app.app.test_client()
    page = client.get("/").get_data(as_text=True)
    out["shown"] = 'id="name-dialog"' in page and "Imperial_Hzd" in page
    conn = storage.connect(path)
    by_name = {a["name"]: a["id"] for a in storage.accounts(conn)}
    out["order"] = ids(page) == [str(by_name["ImperialHzd"]), str(by_name["Yukino2nd"]), "new"]
    out["hint"] = page.count("名字很像") == 1

    out["no_token"] = client.post("/accounts/answer", data={"name": "Imperial_Hzd", "account": "new"}).status_code
    form = {"token": token, "name": "Imperial_Hzd", "next": "/matches"}
    bad = client.post("/accounts/answer", data={**form, "account": "999"}, follow_redirects=True).get_data(as_text=True)
    out["bad_kept"] = "请选一项" in bad and summary(conn)[-1] == ("Imperial_Hzd", 1, False)

    other = panel_app.app.test_client()  # 另一个窗口：先点"稍后再问"
    other.post("/accounts/later", data={"token": token, "next": "/"})
    out["later_same"] = 'id="name-dialog"' not in other.get("/matches").get_data(as_text=True)
    out["later_new"] = 'id="name-dialog"' in panel_app.app.test_client().get("/").get_data(as_text=True)

    done = client.post("/accounts/answer", data={**form, "account": str(by_name["ImperialHzd"])}, follow_redirects=True)
    text = done.get_data(as_text=True)
    out["merged"] = summary(conn)
    out["merged_page"] = done.request.path == "/matches" and "已合并" in text and 'id="name-dialog"' not in text

    storage.insert_match(conn, {"player_name": "Third", "ended_at": "2026-10-17 23:00:00"})
    client.post("/accounts/answer", data={**form, "name": "Third", "account": "new"})
    out["separate"] = summary(conn)
    out["quiet"] = 'id="name-dialog"' not in client.get("/").get_data(as_text=True)
    conn.close()
    return out


def switch_checks():
    """切换账号和设置页的"账号"一栏。返回每一步看到的结果。"""
    token = panel_app.app.config["TOKEN"]
    out = {}

    def total(page):
        found = re.search(r"共 (\d+) 局", page)
        return int(found[1]) if found else None

    # 只有一个账号，另有一局没认出玩家名：不分账号
    path, conn = new_db("single", [("ImperialHzd", "2026-10-16 21:00:00"), (None, "2026-10-16 22:00:00")])
    conn.close()
    panel_app.app.config.update(DB=path)
    client = panel_app.app.test_client()
    page = client.get("/matches").get_data(as_text=True)
    out["single"] = ('id="account-select"' not in page, total(page))

    # 主号 3 局（侵蚀，1 局待确认），小号 2 局（恶灵，最近玩的），1 局没认出玩家名
    path, conn = new_db("two", [])
    for player, ended, legend, review in [("Main", "2026-10-16 20:00:00", "侵蚀", 1), ("Main", "2026-10-16 21:00:00", "侵蚀", 0),
                                          ("Main", "2026-10-16 22:00:00", "侵蚀", 0), (None, "2026-10-16 23:00:00", None, 0),
                                          ("Alt", "2026-10-17 20:00:00", "恶灵", 0), ("Alt", "2026-10-17 21:00:00", "恶灵", 0)]:
        storage.insert_match(conn, {"player_name": player, "ended_at": ended, "legend": legend, "needs_review": review,
                                    "placement": 3, "rank_tier": "钻石", "rank_division": "II", "rank_progress": 500})
    storage.accounts(conn)
    storage.answer_name(conn, "Alt")  # 两个都是已确认的账号
    ids = {a["name"]: a["id"] for a in storage.accounts(conn)}
    panel_app.app.config.update(DB=path)
    client = panel_app.app.test_client()

    def legends(page):
        return re.findall(r'<option value="([^"]+)"\s*>', page.split('name="legend"')[1].split("</select>")[0]) \
            if 'name="legend"' in page else []

    page = client.get("/matches").get_data(as_text=True)
    out["default"] = ('id="account-select"' in page, total(page), legends(page), "需要你确认" in page)
    moved = client.post("/account", data={"token": token, "account": str(ids["Main"]),
                                          "next": "/matches?page=2&open=5&legend=恶灵&mode=ranked"})
    out["redirect"] = moved.headers["Location"]
    page = client.get("/matches").get_data(as_text=True)
    out["main"] = (total(page), legends(page), "有 1 局识别结果不确定" in page)
    first = storage.query_matches(conn, names=["Main"])[0][0]["match_id"]
    out["detail"] = "玩家名 Main" in client.get(f"/matches?open={first}&detail=1").get_data(as_text=True)
    overview = client.get("/").get_data(as_text=True)
    out["main_overview"] = 'class="rank-name"' in overview and "段位按账号分开" not in overview
    client.post("/account", data={"token": token, "account": "all", "next": "/"})
    overview = client.get("/").get_data(as_text=True)
    out["all"] = (total(client.get("/matches").get_data(as_text=True)),
                  "段位按账号分开" in overview and "RP 走势按账号分开" in overview)
    out["fresh_window"] = total(panel_app.app.test_client().get("/matches").get_data(as_text=True))  # 新窗口：又是最近玩的小号

    # 设置页：把小号的名字并到主号，再拆回来；并到不存在的账号
    settings = client.get("/settings").get_data(as_text=True)
    out["settings_card"] = 'id="h-accounts"' in settings and settings.count('action="/accounts/move"') == 2
    client.post("/accounts/move", data={"token": token, "name": "Alt", "account": str(ids["Main"])})
    out["moved"] = summary(conn)
    client.post("/accounts/move", data={"token": token, "name": "Alt", "account": "new"})
    out["split"] = summary(conn)
    bad = client.post("/accounts/move", data={"token": token, "name": "Alt", "account": "999"}, follow_redirects=True)
    out["bad"] = "要并到的账号不存在" in bad.get_data(as_text=True) and summary(conn) == out["split"]
    conn.close()
    return out


def main():
    print("多账号：账号对照表、新玩家名的弹窗、切换账号")
    t = table_checks()
    p = panel_checks()
    s = switch_checks()
    return report({
        f"第一个名字自动成为账号，只差空格的算同一个名字（{t['first']}）": t["first"] == [("Imperial Hzd", 2, True)],
        f"新名字先单独算一个账号，等回答（{t['pending']}）":
            t["pending"] == [("Imperial Hzd", 2, True), ("Yukinoshita", 1, False)],
        f"合并后局数相加，显示最近一局的名字（{t['merged']}，用过的名字 {t['names']}）":
            t["merged"] == [("Yukinoshita", 3, True)] and t["names"] == ["Yukinoshita", "Imperial Hzd"],
        f"名字还没登记时回答\"另一个账号\"也有效（{t['separate']}）":
            t["separate"] == [("Yukinoshita", 3, True), ("Alt2nd", 1, True)],
        f"版本 2 的数据库升级后有对照表（{t['upgraded']}）":
            t["upgraded"] == (storage.SCHEMA_VERSION, [("ImperialHzd", 1, True)]),
        "面板：有新名字时弹窗": p["shown"],
        "弹窗：名字很像的账号排在最前，最后是\"另一个账号\"；只有很像的那个有提示": p["order"] and p["hint"],
        f"没有口令不能提交（状态码 {p['no_token']}）": p["no_token"] == 403,
        "选了不存在的账号：不保存，提示\"请选一项\"": p["bad_kept"],
        "\"稍后再问\"后同一个窗口里换页面也不再弹；新开的窗口照样问": p["later_same"] and p["later_new"],
        f"选\"同一个人\"后合并（{p['merged']}），回到原来的页面，有提示，不再弹": p["merged"] == [
            ("Imperial_Hzd", 3, True), ("Yukino2nd", 1, True)] and p["merged_page"],
        f"选\"另一个账号\"后单独一个账号，没有新名字时不弹（{p['separate']}）":
            p["separate"][-1] == ("Third", 1, True) and p["quiet"],
        f"只有 1 个账号：不显示切换，没认出玩家名的局也照常显示（没有切换、局数：{s['single']}）": s["single"] == (True, 2),
        f"2 个账号：显示切换，默认看最近玩的小号，局数、传奇筛选、待确认都只算它（{s['default']}）":
            s["default"] == (True, 2, ["恶灵"], False),
        f"切到主号：换页面后还记得（局数、传奇、待确认提示：{s['main']}），每局详情有玩家名":
            s["main"] == (3, ["侵蚀"], True) and s["detail"] and s["main_overview"],
        f"切换后回到原来的页面，去掉页码、展开的局和传奇筛选（{s['redirect']}）": s["redirect"] == "/matches?mode=ranked",
        f"全部账号：所有局（含没认出玩家名的），段位和走势图提示选一个账号（{s['all']}）": s["all"] == (6, True),
        f"新开的窗口又默认看最近玩的账号（局数 {s['fresh_window']}）": s["fresh_window"] == 2,
        "设置页有\"账号\"一栏，每个名字一行": s["settings_card"],
        f"设置页：把小号并到主号（{s['moved']}），再拆回来（{s['split']}）":
            s["moved"] == [("Alt", 5, True)] and s["split"] == [("Main", 3, True), ("Alt", 2, True)],
        "设置页：并到不存在的账号，不保存": s["bad"],
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
