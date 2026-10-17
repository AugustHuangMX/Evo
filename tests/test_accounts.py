"""多账号：账号对照表（storage.py）和"检测到新的玩家名"弹窗（panel_app.py + base.html）。不用截图。

- 对照表：第一个名字自动成为第一个账号；只差空格的算同一个名字；新名字先单独算一个账号、等回答；
  合并后局数加在一起、显示最近一局用的名字；"另一个账号"保持单独；名字还没登记时回答也有效；
  旧数据库（版本 2）升级后有对照表
- 弹窗（用 Flask 自带的测试客户端，不开浏览器）：有新名字时弹出，名字很像的账号排在前面；没有口令不能提交；
  选"同一个人"后合并、不再弹、有提示；选"另一个账号"后单独一个账号；选了不存在的账号不保存；
  "稍后再问"后同一个窗口里不再弹，新开的窗口（新的会话）照样问；没有新名字时不弹

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


def main():
    print("多账号：账号对照表和新玩家名的弹窗")
    t = table_checks()
    p = panel_checks()
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
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
