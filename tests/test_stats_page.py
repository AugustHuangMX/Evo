"""数据页：常一起玩的队友、传奇表现、传奇组合（panel_app.py 的"数据页的统计"、panel/templates/stats.html）。
不用截图，不开浏览器（用 Flask 自带的测试客户端）。

- 导航里有"数据"，在"战绩"和"设置"之间；没有对局时显示空白提示
- 两列卡片（2026-10-28）：每张先列 5 行、只放几列，点卡片打开完整的表格（/stats/<part>，范围跟着带过去，
  "← 数据"回来）；下面这些表格上的检查都在完整的表格里
- 队友按 ID 认人：改了名也算同一个人，显示用得最多的名字；没有 ID 的行（没有总结页的局）按名字对上 ID；
  同一局读出两次只算一次；只一起打过 1 局的不列；表下只有一句"*将鼠标停在队友名字上可以看到ID"（2026-10-22 用户定的）
- 队友的名次、冠军、场均 RP 是自己这几局的；RP 只算排位局，读不到的写"—"不写 0
- 传奇表现列出每个传奇（不是前 3 个 + 其他），按局数从多到少；没认出传奇的局写明几局
- 第一行"所有对局"（参照）和总览页的统计数字一样
- 范围（近 30 局 / 全部）、多账号都和总览页一样筛选

运行：python tests/test_stats_page.py
"""

import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import panel_app
import storage

FOLDER = Path(tempfile.mkdtemp(prefix="evo-stats-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里


def new_db(name):
    """一个账号 ImperialHzd 打了 40 局（都是罗芭）+ 3 局恶灵 + 1 局没认出传奇；另一个账号 AltAccount 1 局。
    队友：
    - 阿亮（ID 111）：前 12 局（都在"近 30 局"以外），第 7 局起改名叫"阿亮亮"（第 10 局读成"阿 亮"）
    - 小白（ID 222）：最后 4 局；其中 1 局没有总结页（hud1，只有名字没有 ID）；另 1 局两张卡片都读成了小白
    - 路人（ID 333）：只打过 1 局
    - 小号的队友 小黑（ID 444）：只在小号的局里"""
    path = FOLDER / f"{name}.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)
    start = datetime.now() - timedelta(days=5)
    games = []
    for i in range(40):  # 第 1 局冠军，其余按 2–15 名轮流；排位 RP 10 − i % 7；伤害第 0 局没读到
        games.append({"mode": "ranked", "placement": 1 if i == 0 else 2 + i % 14, "kills": i % 4,
                      "damage": None if i == 0 else 500 + i, "rp_delta": 10 - i % 7, "legend": "罗芭"})
    games += [{"mode": "pubs", "placement": 1, "kills": 5, "damage": 1500, "legend": "恶灵"},  # 匹配：没有 RP
              {"mode": "pubs", "placement": 3, "kills": None, "damage": 900, "legend": "恶灵"},  # 击杀没读到
              {"mode": "pubs", "placement": None, "kills": 2, "damage": 300, "legend": "恶灵"},  # 名次没读到
              {"mode": "ranked", "placement": 8, "kills": 1, "damage": 200, "rp_delta": -20, "legend": None}]
    ids = []
    for i, game in enumerate(games):
        ids.append(storage.insert_match(conn, {"player_name": "ImperialHzd",
                                               "ended_at": f"{start + timedelta(minutes=30 * i):%Y-%m-%d %H:%M:%S}",
                                               **game}))

    def mates(match_id, *people):
        storage.save_teammates(conn, match_id, [{"slot": slot, "name": n, "player_id": pid}
                                                for slot, (n, pid) in zip(("left", "right"), people)])

    for i in range(12):
        name = "阿亮" if i < 6 else ("阿 亮" if i == 9 else "阿亮亮")
        mates(ids[i], (name, "111"), ("路人", "333") if i == 5 else (None, None))
    mates(ids[40], ("小白", "222"))
    storage.save_teammates(conn, ids[41], [{"slot": "hud1", "name": "小白", "player_id": None}])  # 没有总结页
    mates(ids[42], ("小白", "222"), ("小白", "222"))  # 两张卡片读成同一个人
    mates(ids[43], ("小白", "222"))

    alt = storage.insert_match(conn, {"player_name": "AltAccount", "mode": "ranked", "placement": 2, "legend": "希尔",
                                      "ended_at": f"{start + timedelta(minutes=30 * 50):%Y-%m-%d %H:%M:%S}"})
    mates(alt, ("小黑", "444"), ("小白", "222"))
    storage.answer_name(conn, "AltAccount")
    conn.close()
    return path


def table_rows(page, table):
    """一张表（mates-table / legends-table）表身的每一行：各格的文字（去掉标签、多余空白）。第一行是"所有对局"。"""
    found = re.search(rf'<table class="stats-table {table}">.*?<tbody>(.*?)</tbody>', page, re.S)
    if not found:
        return []
    return [[" ".join(re.sub(r"<[^>]+>", " ", c).split()) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", found.group(1), re.S)]


def peek_rows(page, heading):
    """卡片（标题 id 是 heading）里表格的每一行：各格的文字。"""
    found = re.search(rf'aria-labelledby="{heading}">.*?<tbody>(.*?)</tbody>', page, re.S)
    if not found:
        return []
    return [[" ".join(re.sub(r"<[^>]+>", " ", c).split()) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", found.group(1), re.S)]


def cells(page, name, table="mates-table"):
    """表里名字是 name 的那一行（第一格去掉"上次一起：……"、"参照"）；没有返回 None。"""
    for row in table_rows(page, table):
        if row[0].split(" 上次一起")[0].removesuffix(" 参照") == name:
            return row
    return None


def combo_checks():
    """传奇组合：另建一个数据库。全队传奇都认出的局 8 局：
    {恶灵, 命脉, 直布罗陀} 5 局（自己用恶灵 3 局、用命脉 2 局，算同一个组合；名次 1、5、9、3、没读到 → 4.5）、
    {罗芭, 寻血猎犬, 沃特森} 2 局（名次 2、4）、{希尔, 密客, 幻象} 1 局（不列）。
    不算的：队友传奇没认出、没有队友、同一局两个恶灵（认错了）、自己的传奇没认出。"""
    path = FOLDER / "combos.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)
    games = [("恶灵", ["命脉", "直布罗陀"], 1), ("恶灵", ["直布罗陀", "命脉"], 5), ("恶灵", ["命脉", "直布罗陀"], 9),
             ("命脉", ["恶灵", "直布罗陀"], 3), ("命脉", ["直布罗陀", "恶灵"], None),
             ("罗芭", ["寻血猎犬", "沃特森"], 2), ("罗芭", ["沃特森", "寻血猎犬"], 4),
             ("希尔", ["密客", "幻象"], 6),
             ("恶灵", ["命脉", None], 1), ("恶灵", [], 1), ("恶灵", ["恶灵", "命脉"], 1), (None, ["命脉", "直布罗陀"], 1)]
    for i, (mine, mates, place) in enumerate(games):
        match_id = storage.insert_match(conn, {"ended_at": f"2026-10-20 {10 + i}:00:00", "legend": mine, "placement": place})
        if mates:
            storage.save_teammates(conn, match_id, [{"slot": slot, "name": f"队友{slot}", "legend": legend}
                                                    for slot, legend in zip(("left", "right"), mates)])
    conn.close()
    single = FOLDER / "combo_single.db"
    single.unlink(missing_ok=True)
    conn = storage.connect(single)
    storage.save_teammates(conn, storage.insert_match(conn, {"ended_at": "2026-10-20 10:00:00", "legend": "恶灵"}),
                           [{"slot": "left", "legend": "命脉"}, {"slot": "right", "legend": "直布罗陀"}])
    conn.close()

    pages = {}
    for name, db in (("combos", path), ("single", single)):
        panel_app.app.config["DB"] = db
        client = panel_app.app.test_client()
        pages[name] = client.get("/stats/combos?range=all").get_data(as_text=True)
        pages[name + " 卡片"] = client.get("/stats?range=all").get_data(as_text=True)
    rows = table_rows(pages["combos"], "combos-table")
    big = " ".join(sorted(["恶灵", "命脉", "直布罗陀"]))
    small = " ".join(sorted(["罗芭", "寻血猎犬", "沃特森"]))
    card = peek_rows(pages["combos 卡片"], "h-combos")
    return {
        "卡片上的组合：只放头像（名字在悬停和读屏里）、局数、场均名次":
            card == [[big.replace(" ", "、"), "5", "4.5"], [small.replace(" ", "、"), "2", "3.0"]]
            and f'title="{big.replace(" ", "、")}"' in pages["combos 卡片"],
        "传奇组合：不管谁用的哪个算同一个组合，按局数从多到少；只出现过 1 局的不列":
            [r[0] for r in rows[1:]] == [big, small],
        "选取率 = 局数 ÷ 全队传奇都认出的局数（没认全、没有队友、两个一样的、自己没认出的都不算）":
            rows[1:] and rows[1][1:3] == ["5", f"{5 / 8:.0%}"] and rows[2][1:3] == ["2", f"{2 / 8:.0%}"],
        "组合的场均名次不算没读到名次的局（1、5、9、3 → 4.5）": len(rows) > 1 and rows[1][3] == "4.5",
        "参照行：所有组合 8 局、100%": rows[:1] and rows[0][:3] == ["所有组合 参照", "8", "100%"],
        "只有 1 局的组合：写明，不出空表（卡片上也一样）": all(
            "还没有出现过 2 局以上的组合" in pages[k] and "<table" not in pages[k].split('id="h-combos"')[1]
            for k in ("single", "single 卡片")),
    }


def main():
    print("数据页：常一起玩的队友、传奇表现、传奇组合")
    path = new_db("stats")
    panel_app.app.config.update(DB=path, TESTING=True)
    conn = storage.connect(path)
    main_id = next(a["id"] for a in storage.accounts(conn) if "ImperialHzd" in a["raw"])
    rows = storage.all_matches(conn, ["ImperialHzd"])
    conn.close()
    client = panel_app.app.test_client()
    with client.session_transaction() as s:
        s["account"] = main_id  # 大号（没选时默认看最近玩过的小号）
    recent = client.get("/stats").get_data(as_text=True)
    cards = client.get("/stats?range=all").get_data(as_text=True)
    everything = "".join(client.get(f"/stats/{part}?range=all").get_data(as_text=True) for part in ("mates", "legends"))
    recent_mates = client.get("/stats/mates").get_data(as_text=True)
    overview_all = client.get("/?range=all").get_data(as_text=True)
    unknown = client.get("/stats/x").status_code
    nav = re.findall(r'<nav class="nav".*?</nav>', recent, re.S)[0]

    liang, bai = cells(everything, "阿亮亮"), cells(everything, "小白")
    ref = panel_app.group_numbers(rows)
    stat_values = {s["label"]: s["value"] for s in panel_app.stat_cards(rows)}
    legends = table_rows(everything, "legends-table")[1:]  # [名字, 局数, 横条, 胜率, 前五率, 场均名次, 场均击杀, 场均伤害, 场均 RP]

    # 别的账号："全部账号"时小号的局也算；只看小号时没有大号的队友
    everyone = panel_app.app.test_client()
    with everyone.session_transaction() as s:
        s["account"] = "all"
    all_accounts = everyone.get("/stats/mates?range=all").get_data(as_text=True)

    # 没有对局、没有一起打过 2 局的队友
    empty_path = FOLDER / "empty.db"
    storage.connect(empty_path).close()
    panel_app.app.config["DB"] = empty_path
    empty = panel_app.app.test_client().get("/stats").get_data(as_text=True)
    lonely_path = FOLDER / "lonely.db"
    conn = storage.connect(lonely_path)
    storage.save_teammates(conn, storage.insert_match(conn, {"ended_at": "2026-10-01 20:00:00", "legend": "恶灵"}),
                           [{"slot": "left", "name": "路人", "player_id": "333"}])
    conn.close()
    panel_app.app.config["DB"] = lonely_path
    lonely = panel_app.app.test_client().get("/stats").get_data(as_text=True)
    lonely_mates = panel_app.app.test_client().get("/stats/mates").get_data(as_text=True)
    # 卡片最多列 5 行：7 个传奇各 1 局
    many_path = FOLDER / "many.db"
    many_path.unlink(missing_ok=True)
    conn = storage.connect(many_path)
    seven = ["恶灵", "命脉", "直布罗陀", "罗芭", "寻血猎犬", "沃特森", "希尔"]
    for i, name in enumerate(seven):
        storage.insert_match(conn, {"ended_at": f"2026-10-01 {10 + i}:00:00", "legend": name, "placement": i + 1})
    conn.close()
    panel_app.app.config["DB"] = many_path
    many = panel_app.app.test_client().get("/stats").get_data(as_text=True)
    many_full = panel_app.app.test_client().get("/stats/legends").get_data(as_text=True)
    combos = combo_checks()
    panel_app.app.config["DB"] = path

    checks = {
        "导航：总览、战绩、数据、设置": re.findall(r">\s*([^<>\s]+)\s*</a>", nav) == ["总览", "战绩", "数据", "设置"],
        "数据页打开时导航里\"数据\"是选中的": 'class="active"' in re.findall(r'<a href="/stats"[^>]*>', nav)[0],
        "没有对局：显示\"还没有任何对局记录\"": "还没有任何对局记录" in empty,
        "两列卡片：最常用武器、常一起玩的队友、传奇表现、传奇组合": re.search(
            r'<div class="stats-grid">.*id="h-weapons".*id="h-mates".*id="h-legends".*id="h-combos"', cards, re.S) is not None,
        "卡片上的队友：一起打几局、场均名次；下面一行是所有对局的场均名次": peek_rows(cards, "h-mates") == [
            ["阿亮亮", "12 局", liang[3] if liang else None], ["小白", "4 局", "4.0"]]
            and f'所有对局：场均名次 <span class="num">{ref["avg_place"]}</span>' in cards,
        "卡片上的传奇：局数、名次分布、场均名次": [r[:2] + r[3:] for r in peek_rows(cards, "h-legends")] == [
            [r[0], r[1], r[5]] for r in legends],
        "行都列出来了：右下角写\"查看完整表格\"，链接带着范围": (
            '<a class="peek-more" href="/stats/mates?range=all">' in cards
            and '<a class="peek-more" href="/stats/legends?range=all">' in cards and "查看完整表格" in cards),
        "没有武器的范围：卡片上写明，没有链接": "这个范围里还没有记下武器" in cards and "/stats/weapons" not in cards,
        "卡片最多列 5 行，\"查看全部 7 个传奇\"打开完整的表格（7 个）": (
            [r[0] for r in peek_rows(many, "h-legends")] == sorted(seven)[:5]  # 局数一样时按名字排
            and "查看全部 <span class=\"num\">7</span> 个传奇" in many
            and [r[0] for r in table_rows(many_full, "legends-table")[1:]] == sorted(seven)),
        "完整的表格：\"← 数据\"回到卡片那页（带范围），没有\"我的 / 全体玩家\"切换，导航里\"数据\"还是选中的": (
            '<a class="back-link" href="/stats?range=all">' in everything and "stats-tabs" not in everything
            and 'class="active"' in re.findall(r'<a href="/stats"[^>]*>', everything)[0]),
        "完整的表格：范围按钮留在这一页": 'href="/stats/mates"' in everything and 'href="/stats/mates?range=all"' in everything,
        "不认识的完整表格：404": unknown == 404,
        "没有一起打过 2 局的队友：写明，不出空表（卡片和完整的表格都是）": all(
            "还没有一起打过 2 局以上的队友" in p and "mates-table" not in p and "<table" not in p.split('id="h-mates"')[1].split("</section>")[0]
            for p in (lonely, lonely_mates)),
        "改了名按 ID 算同一个人（12 局），显示最近用的名字": liang is not None and liang[1] == "12 局"
                                                  and cells(everything, "阿亮") is None and cells(everything, "阿 亮") is None,
        "鼠标停在名字上能看到 ID": 'title="ID 111"' in everything,
        "没有 ID 的行按名字对上 ID；同一局读出两次只算一次（小白 4 局）": bai is not None and bai[1] == "4 局",
        "只一起打过 1 局的不列": cells(everything, "路人") is None,
        "队友表下只有一句提示（悬停看 ID），没有别的说明": re.findall(r'<p class="stats-note">(.*?)</p>', everything, re.S)
                                                 == ["*将鼠标停在队友名字上可以看到ID"],
        "没有可列的队友时不显示这句提示": "stats-note" not in lonely,
        "队友的冠军是自己的名次（阿亮那 12 局有 1 次冠军）": liang is not None and liang[2] == "1 次 8%",
        "队友的场均 RP 只算排位局（小白 4 局里只有 1 局排位 −20）": bai is not None and bai[4] == "−20",
        "队友的场均名次不算没读到名次的局（小白：1、3、8 → 4.0）": bai is not None and bai[3] == "4.0",
        "近 30 局：前 12 局的队友不在里面": cells(recent_mates, "阿亮亮") is None and cells(recent_mates, "小白") is not None
                                         and [r[0] for r in peek_rows(recent, "h-mates")] == ["小白"],
        "参照行（全部）和总览页的统计数字一样": cells(everything, "所有对局") == [
            "所有对局 参照", f"{ref['games']} 局", f"{ref['wins']} 次 {ref['win_rate']}", ref["avg_place"], ref["avg_rp"]]
                                         and stat_values["场均名次"] == ref["avg_place"]
                                         and f">{stat_values['场均伤害']}<" in overview_all,
        "传奇表现：每个传奇都列出（不合并成\"其他\"），按局数从多到少": [r[0] for r in legends] == ["罗芭", "恶灵"],
        "没认出传奇的局写明几局": "另有 1 局没认出传奇" in everything,
        "读不到写\"—\"不写 0：恶灵都是匹配，没有 RP": len(legends) > 1 and legends[1][8] == "—",
        "恶灵的场均击杀不算没读到的局（5、2 → 3.5）": len(legends) > 1 and legends[1][6] == "3.5",
        "名次分布的横条有文字说明（读屏软件、悬停）": re.search(r'aria-label="冠军 1 局，第 2–3 名 \d+ 局', everything) is not None,
        "只看大号时没有小号的队友": "小黑" not in everything,
        "\"全部账号\"时算上小号的局（小白 5 局）": (cells(all_accounts, "小白") or [None, None])[1] == "5 局",
        "传奇组合：没有认出全队传奇的局时写明": "还没有认出全队传奇的对局" in lonely,
        **combos,
    }
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
