"""分享战绩：卡片图片（panel_app.py 的"分享战绩"、panel/templates/share/）、对话框（_share.html）、保存图片（app_window.py）。
不用截图，不开浏览器（用 Flask 自带的测试客户端）。

- 三种卡片（单局、今日、概况）都是写对了的 SVG（能按 XML 解析），不引用别的文件（字体、图标都嵌在里面）
- 队友的名字和 ID 不出现在任何卡片上；自己的游戏名默认写，name=0 时不写
- 读不到的项写"—"，不写成 0；匹配的局没有 RP 那一块
- 今日战绩只算今天（早上 5 点起）的局，写日期不写"今天"；概况的范围和总览页一样（近 30 局 / 全部）
- 战绩页：展开的局有"分享这局"，待确认的局没有；总览页有"分享"，今天没打时"今日战绩"不能选；
  "全部账号"时没有分享按钮，卡片网址也打不开
- 保存图片只接受 PNG

运行：python tests/test_share.py
"""

import base64
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import app_window
import panel_app
import storage

FOLDER = Path(tempfile.mkdtemp(prefix="evo-share-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里
MATES = [("夜航灯塔", "0012345"), ("SilverFox_77", "9876543")]  # 队友的名字和 ID：不能出现在卡片上


def new_db(name):
    """一个账号 ImperialHzd：40 局以前的（不在今天）+ 今天 2 局（1 局待确认）+ 1 局匹配、伤害没读到。都有两名队友。"""
    path = FOLDER / f"{name}.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)
    old = datetime.now() - timedelta(days=3)
    games = [{"ended_at": f"{old + timedelta(minutes=25 * i):%Y-%m-%d %H:%M:%S}", "mode": "ranked", "placement": 1 + i % 15,
              "kills": i % 5, "assists": 2, "knockdowns": i % 5, "damage": 300 + i * 10, "rp_delta": 10 - i % 7,
              "rank_tier": "钻石", "rank_division": "IV", "rank_progress": 100 + i * 5, "legend": "恶灵", "map": "风暴点"}
             for i in range(40)]
    now = datetime.now()
    start = panel_app.day_start(now)  # 今天的 3 局放在"今天"开始以后（凌晨、早上 5 点刚过时跑也对）

    def today(share):
        return f"{start + (now - start) * share:%Y-%m-%d %H:%M:%S}"

    games += [
        {"ended_at": today(0.2), "mode": "ranked", "placement": 1, "kills": 9,
         "assists": 3, "knockdowns": 10, "damage": 2468, "rp_delta": 210, "rank_tier": "钻石", "rank_division": "III",
         "rank_progress": 120, "legend": "罗芭", "map": "世界尽头"},
        {"ended_at": today(0.5), "mode": "ranked", "placement": 7, "kills": None,
         "needs_review": 1, "review_note": "击杀/助攻/击倒：格式不对", "legend": "恶灵", "map": "风暴点"},
        {"ended_at": today(0.9), "mode": "pubs", "placement": 12, "kills": 0,
         "assists": 0, "knockdowns": 0, "damage": None, "legend": "动力小子", "map": "奥林匹斯"},
    ]
    for game in games:
        match_id = storage.insert_match(conn, {"player_name": "ImperialHzd", **game})
        storage.save_teammates(conn, match_id, [
            {"slot": slot, "name": mate, "player_id": pid, "legend": legend, "kills": 2, "assists": 1, "knockdowns": 2,
             "damage": 777} for slot, (mate, pid), legend in zip(("left", "right"), MATES, ("沃特森", "直布罗陀"))])
    ids = [r["match_id"] for r in conn.execute("SELECT match_id FROM matches ORDER BY match_id")]
    conn.close()
    return path, ids


def svg_checks(client, url):
    """一张卡片：能不能按 XML 解析、引用了哪些外部文件、有没有队友的名字和 ID、有没有自己的名字（name=1 和 name=0）。"""
    out = {}
    response = client.get(url)
    out["type"] = response.mimetype
    text = response.get_data(as_text=True)
    try:
        ET.fromstring(text)
        out["xml"] = True
    except ET.ParseError as e:
        out["xml"] = str(e)
    refs = re.findall(r'(?:src|href)="([^"]*)"|url\(([^)]*)\)', text)
    out["external"] = [r for pair in refs for r in pair if r and not r.strip("'\"").startswith("data:")]
    out["mates"] = [x for name, pid in MATES for x in (name, pid) if x in text]
    out["name_on"] = "ImperialHzd" in text
    out["name_off"] = "ImperialHzd" in client.get(url + ("&" if "?" in url else "?") + "name=0").get_data(as_text=True)
    out["text"] = text
    return out


def main():
    print("分享战绩：卡片图片、对话框、保存图片")
    path, ids = new_db("share")
    panel_app.app.config.update(DB=path, TESTING=True)
    client = panel_app.app.test_client()
    win, review, pubs = ids[40], ids[41], ids[42]

    cards = {"单局": svg_checks(client, f"/share/match/{win}.svg"), "今日": svg_checks(client, "/share/today.svg"),
             "概况（近 30 局）": svg_checks(client, "/share/overview.svg?range=30"),
             "概况（全部）": svg_checks(client, "/share/overview.svg?range=all")}
    pubs_card = client.get(f"/share/match/{pubs}.svg?name=0").get_data(as_text=True)
    today = cards["今日"]["text"]
    today_label = f"{panel_app.day_start(datetime.now()).month}月{panel_app.day_start(datetime.now()).day}日"

    matches_page = client.get(f"/matches?open={win}").get_data(as_text=True)
    review_page = client.get(f"/matches?open={review}").get_data(as_text=True)
    overview = client.get("/").get_data(as_text=True)

    # 今天没打：只有以前的局
    quiet_path, _ = new_db("quiet")
    conn = storage.connect(quiet_path)
    conn.execute("DELETE FROM matches WHERE match_id > 40")
    conn.commit()
    conn.close()
    panel_app.app.config["DB"] = quiet_path
    quiet = panel_app.app.test_client().get("/").get_data(as_text=True)
    today_option = re.search(r'<label class="([^"]*)"[^>]*>\s*<input type="radio" name="share-kind"[^>]*data-url="/share/today.svg"', quiet)

    # 全部账号：几个账号混在一起，不能分享
    conn = storage.connect(path)
    storage.insert_match(conn, {"player_name": "AltAccount", "ended_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}"})
    storage.answer_name(conn, "AltAccount")
    conn.close()
    panel_app.app.config["DB"] = path
    everyone = panel_app.app.test_client()
    with everyone.session_transaction() as s:
        s["account"] = "all"
    all_overview = everyone.get("/").get_data(as_text=True)
    all_today = everyone.get("/share/today.svg").status_code

    # 名字里有 & < > " '：卡片仍然是写对了的 XML（模板会转义）
    odd = "A&B<C>\"D'名"
    conn = storage.connect(path)
    odd_id = storage.insert_match(conn, {"player_name": odd, "ended_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
                                         "mode": "ranked", "placement": 3, "legend": "恶灵"})
    conn.close()
    odd_svg = client.get(f"/share/match/{odd_id}.svg").get_data(as_text=True)
    try:
        odd_ok = odd in "".join(ET.fromstring(odd_svg).itertext())
    except ET.ParseError:
        odd_ok = False

    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
    saved = app_window._png_bytes("data:image/png;base64," + base64.b64encode(png).decode()) == png
    rejected = []
    for bad in ("data:image/svg+xml;base64,PHN2Zy8+", "data:image/png;base64," + base64.b64encode(b"GIF89a").decode(), None):
        try:
            app_window._png_bytes(bad)
        except ValueError:
            rejected.append(bad)

    checks = {}
    for name, c in cards.items():
        checks[f"{name}：是 SVG 图片，能按 XML 解析（{c['type']}，{c['xml']}）"] = c["type"] == "image/svg+xml" and c["xml"] is True
        checks[f"{name}：不引用别的文件，字体和图标都嵌在里面（外部引用 {c['external'][:3]}）"] = not c["external"]
        checks[f"{name}：没有队友的名字和 ID（出现了 {c['mates']}）"] = not c["mates"]
        checks[f"{name}：默认写自己的游戏名，name=0 时不写（{c['name_on']}，{c['name_off']}）"] = c["name_on"] and not c["name_off"]
    single = cards["单局"]["text"]
    checks["单局：冠军、传奇、地图、击杀、伤害、RP、打完这局的段位、队友的传奇"] = all(
        x in single for x in ("冠军", "罗芭", "世界尽头", ">9<", "2,468", "+210", "钻石 III", "沃特森", "直布罗陀"))
    checks["单局：写日期，不写\"今天\""] = "今天" not in single and re.search(r"\d+月\d+日 \d\d:\d\d", single) is not None
    checks["匹配的局：没有 RP 那一块；伤害没读到写\"—\"，不写 0"] = "排位点数" not in pubs_card and re.search(
        r'伤害</span><span class="num">—<', pubs_card) is not None
    checks["今日：只算今天的 3 局，写日期；最好名次 #1"] = (re.search(r'局数</span><span class="num">3<', today) is not None
                                              and today_label in today and "#1" in today)
    checks["今日：总伤害只加读到的（匹配那局伤害没读到，不当作 0 也不报错）"] = re.search(r'总伤害</span><span class="num">2,468<', today) is not None
    checks["概况：近 30 局写\"最近 30 局\"，全部写\"全部 43 局\""] = ("最近 30 局" in cards["概况（近 30 局）"]["text"]
                                                        and "全部 43 局" in cards["概况（全部）"]["text"])
    checks["战绩页：展开的局有\"分享这局\"和分享对话框"] = "分享这局" in matches_page and 'id="share-dialog"' in matches_page
    checks["战绩页：待确认的局不能分享"] = "分享这局" not in review_page and 'id="share-dialog"' not in review_page
    checks["总览页：有\"分享\"，对话框里有今日战绩和近 30 局概况，提醒有待确认的局"] = (
        'data-share-open' in overview and "今日战绩" in overview and "近 30 局概况" in overview
        and 'data-review="1"' in overview)
    checks["总览页：今天没打时\"今日战绩\"不能选"] = today_option is not None and "disabled" in today_option.group(1)
    checks[f"全部账号：没有分享按钮，卡片网址打不开（{all_today}）"] = "data-share-open" not in all_overview and all_today == 404
    checks["名字里有 & < > \" '：卡片照样能按 XML 解析，名字原样显示"] = odd_ok
    checks[f"保存图片：PNG 能存，别的不收（拒绝了 {len(rejected)}/3）"] = saved and len(rejected) == 3
    checks[f"不存在的局：404（{client.get('/share/match/99999.svg').status_code}）"] = client.get("/share/match/99999.svg").status_code == 404
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
