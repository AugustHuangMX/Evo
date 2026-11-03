"""按赛季看（2026-11-03 用户决定：新赛季从 0 开始算，能切到上赛季）：config/seasons.json、code/seasons.py，
总览页、数据页"我的"的范围按钮（本赛季 / 上赛季 / 近 30 局 / 全部，默认本赛季），战绩页的赛季筛选。
"全体玩家"的本赛季见 tests/test_global_stats.py；统计服务器那边（server/stats/worker.js）没有自动测试，见 docs/底座.md。

用的是临时数据库（第 30 赛季打了 3 局、读过一次大厅；第 31 赛季读过一次大厅、打了 2 局），不用截图。
别的测试里只有一个从 2000 年开始的赛季（tests/fake_game.py），这里换成第 30、31 赛季，测完换回去。

运行：python tests/test_season_ranges.py
"""

import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fake_game import TEST_SEASONS, report  # 要在项目代码之前导入（设置临时数据文件夹）

import calibration  # noqa: E402
import panel_app  # noqa: E402
import seasons  # noqa: E402
import storage  # noqa: E402

# 测试用的赛季（电脑上的时间，和配置换算以后一样的写法）：第 31 赛季从 11-03 19:00 起
SEASONS = [(30, "2026-08-04 19:00:00"), (31, "2026-11-03 19:00:00")]
S30_GAMES = [("2026-10-20 21:00:00", "钻石", "II", 400, -20, 12), ("2026-10-24 22:00:00", "钻石", "II", 460, 60, 3),
             ("2026-10-27 00:20:00", None, None, None, -60, 18)]  # 最后一局没进经验值页、没有段位
S31_GAMES = [("2026-11-03 20:10:00", "白银", "I", 707, 19, 11), ("2026-11-04 21:00:00", "黄金", "IV", 30, 73, 2)]


def module_checks():
    out = {}
    s = SEASONS
    expected = datetime(2026, 11, 3, 18, tzinfo=timezone.utc).astimezone().strftime(seasons.STAMP)
    real = seasons.load()
    out[f"配置：第 30、31 赛季，开赛时刻（UTC）换成电脑上的时间（第 31 赛季 {dict(real)[31]}）"] = (
        [n for n, _ in real][:2] == [30, 31] and dict(real)[31] == expected)
    out["现在是第几赛季：开赛那一刻起算新赛季；第一个赛季以前没有"] = (
        seasons.current(datetime(2026, 11, 3, 18, 59, 59), s) == 30 and seasons.current(datetime(2026, 11, 3, 19), s) == 31
        and seasons.current(datetime(2026, 1, 1), s) is None)
    out["上赛季：本赛季前面那个；第一个赛季没有上赛季"] = (
        seasons.previous(datetime(2026, 11, 5), s) == 30 and seasons.previous(datetime(2026, 10, 5), s) is None)
    out["一个赛季到下一个赛季开始为止；最后一个没有结束"] = (
        seasons.bounds(30, s) == ("2026-08-04 19:00:00", "2026-11-03 19:00:00")
        and seasons.bounds(31, s) == ("2026-11-03 19:00:00", None))
    out["每局按结束的时间归到赛季，没有时间的不算"] = (
        seasons.contains(30, "2026-11-03 18:59:59", s) and not seasons.contains(30, "2026-11-03 19:00:00", s)
        and seasons.contains(31, "2026-11-03 19:00:00", s) and not seasons.contains(31, None, s)
        and len(seasons.pick([{"ended_at": t} for t, *_ in S30_GAMES + S31_GAMES], 31, seasons=s)) == 2)
    return out


def calibration_checks():
    """大厅校准（calibration.py）：两次大厅中间换了赛季（段位重置）不核对；上个赛季没有段位的局不补新赛季的段位。"""
    out = {}
    conn = storage.connect(":memory:")
    log = []

    def lobby(when, tier, division, progress):
        from ranks import score
        reading = {"read_at": when, "player_name": "Me", "rank_tier": tier, "rank_division": division,
                   "rank_progress": progress, "score": score(tier, division, progress)}
        storage.save_lobby(conn, reading)
        calibration.calibrate(conn, reading, log.append)

    lobby("2026-11-02 20:00:00", "钻石", "II", 900)
    a = storage.insert_match(conn, {"ended_at": "2026-11-02 20:30:00", "player_name": "Me", "mode": "ranked", "rp_delta": 10,
                                    "rank_tier": "钻石", "rank_division": "II", "rank_progress": 910})
    lobby("2026-11-04 20:00:00", "钻石", "II", 100)  # 假设重置后还在钻石 II
    out[f"两次大厅中间换了赛季（重置后还在同一个大段）：不核对、不标待确认（{log[-1].strip()}）"] = (
        storage.get_match(conn, a)["needs_review"] == 0 and "换了赛季" in log[-1])
    b = storage.insert_match(conn, {"ended_at": "2026-11-03 18:30:00", "player_name": "Me", "mode": "ranked", "rp_delta": -20})
    conn.execute("DELETE FROM lobby_readings")
    lobby("2026-11-03 19:20:00", "白银", "I", 688)  # 开赛后 20 分钟回到大厅（一小时以内）
    row = storage.get_match(conn, b)
    out["开赛前半小时打完、没有段位的那局：不补新赛季重置后的段位"] = row["rank_tier"] is None and any("上个赛季" in l for l in log)
    return out


def make_db(with_s31_games=True):
    path = Path(tempfile.mkdtemp(prefix="evo-seasons-")) / "evo.db"
    conn = storage.connect(path)
    games = S30_GAMES + (S31_GAMES if with_s31_games else [])
    for i, (when, tier, division, progress, rp, place) in enumerate(games):
        storage.insert_match(conn, {"ended_at": when, "player_name": "Me", "mode": "ranked", "map": "风暴点",
                                    "legend": "亡灵", "placement": place, "kills": i, "assists": 1, "knockdowns": 2,
                                    "damage": 500 + i, "rp_delta": rp, "rp_source": "经验值页", "rank_tier": tier,
                                    "rank_division": division, "rank_progress": progress, "needs_review": 0})
    for when, tier, division, progress, score in (("2026-10-26 23:58:30", "钻石", "I", 600, 15600),
                                                  ("2026-11-03 19:52:30", "白银", "I", 688, 5438)):
        storage.save_lobby(conn, {"read_at": when, "player_name": "Me", "rank_tier": tier, "rank_division": division,
                                  "rank_progress": progress, "score": score})
    conn.close()
    panel_app.app.config.update(DB=path, TESTING=True)
    return panel_app.app.test_client()


def stat(page, label):
    m = re.search(rf'<div class="label">{label}</div>\s*<div class="stat-value[^"]*"[^>]*>([^<]*)<', page)
    return m[1] if m else None


def recent_count(page):
    return len(re.findall(r'class="recent-row"', page))


def on(page):
    """选中的范围按钮。"""
    return re.findall(r'aria-current="true"[^>]*>([^<]+)</a>', page)


def panel_checks():
    out = {}
    client = make_db()
    get = lambda url: client.get(url).get_data(as_text=True)  # noqa: E731
    home, last, every, recent = get("/"), get("/?range=last"), get("/?range=all"), get("/?range=30")
    out["总览页：范围按钮本赛季 / 上赛季 / 近 30 局 / 全部，默认本赛季，悬停写着第几赛季、从哪天起"] = (
        re.findall(r'aria-label="统计范围">(.*?)</div>', home, re.S) and
        re.findall(r'>(本赛季|上赛季|近 30 局|全部)</a>', home)[:4] == ["本赛季", "上赛季", "近 30 局", "全部"]
        and on(home) == ["本赛季"] and 'title="第 31 赛季：11/03 起"' in home
        and 'title="第 30 赛季：08/04 起，到 11/03"' in home and 'href="/?range=season"' not in home)
    out[f"本赛季：只算第 31 赛季的 2 局（统计 {stat(home, '统计局数')}、最近对局 {recent_count(home)} 局），段位是本赛季最后读到的"] = (
        stat(home, "统计局数") == "2" and recent_count(home) == 2 and "本赛季 RP" in home and "+92" in home
        and "黄金 IV" in home and "当前段位" in home)
    out[f"上赛季：只算第 30 赛季的 3 局（{stat(last, '统计局数')}），段位卡片是上赛季结束时的（大厅读到的钻石 I）"] = (
        stat(last, "统计局数") == "3" and recent_count(last) == 3 and "上赛季结束时的段位" in last and "钻石 I" in last
        and "上赛季 RP" in last and on(last) == ["上赛季"])
    out[f"全部 5 局、近 30 局 5 局，段位卡片都是现在的（{stat(every, '统计局数')}、{stat(recent, '统计局数')}）"] = (
        stat(every, "统计局数") == "5" and stat(recent, "统计局数") == "5" and "黄金 IV" in every and "黄金 IV" in recent)
    out["分享（用户没要求按赛季）：本赛季时还是\"近 30 局概况\""] = "近 30 局概况" in home and "本赛季概况" not in home
    out["最近对局的\"查看全部\"：本赛季去战绩页的本赛季，上赛季去上赛季，近 30 局、全部去全部"] = (
        'href="/matches">查看全部' in home and 'href="/matches?range=last">查看全部' in last
        and 'href="/matches?range=all">查看全部' in every and 'href="/matches?range=all">查看全部' in recent)

    stats, stats_last = get("/stats"), get("/stats?range=last")
    out["数据页\"我的\"：同样四个按钮，默认本赛季（2 局），上赛季 3 局"] = (
        on(stats)[-1:] == ["本赛季"] and "range-empty" not in stats and 'class="stats-grid"' in stats
        and on(stats_last)[-1:] == ["上赛季"] and "亡灵" in stats_last)

    matches, matches_last, matches_all = get("/matches"), get("/matches?range=last"), get("/matches?range=all")
    total = lambda page: re.search(r"共 (\d+) 局", page)[1] if re.search(r"共 (\d+) 局", page) else None  # noqa: E731
    out[f"战绩页：赛季筛选本赛季 / 上赛季 / 全部（没有近 30 局），默认本赛季（{total(matches)} / {total(matches_last)} / {total(matches_all)} 局）"] = (
        total(matches) == "2" and total(matches_last) == "3" and total(matches_all) == "5"
        and re.findall(r'aria-label="赛季">(.*?)</div>', matches, re.S) and "近 30 局" not in matches
        and on(matches) == ["本赛季"])
    conn = storage.connect(panel_app.app.config["DB"])
    old_id = conn.execute("SELECT match_id FROM matches WHERE ended_at < '2026-11-01' ORDER BY ended_at LIMIT 1").fetchone()[0]
    conn.execute("UPDATE matches SET needs_review = 1, review_note = '请核对' WHERE match_id = ?", (old_id,))
    conn.commit()
    conn.close()
    opened, review = get(f"/matches?open={old_id}"), get("/matches?review=1")
    out["点开上赛季的一局（网址里没写赛季）：自动看上赛季，那一局在列表里，翻页、筛选的链接带着上赛季"] = (
        on(opened) == ["上赛季"] and f'id="m{old_id}"' in opened and "range=last" in opened
        and total(opened) == "3")
    out["只看待确认（顶部提醒按全部赛季算）：看全部赛季，上赛季那一局在里面"] = (
        on(review) == ["全部"] and total(review) == "1" and f'id="m{old_id}"' in review)

    empty_client = make_db(with_s31_games=False)
    get = lambda url: empty_client.get(url).get_data(as_text=True)  # noqa: E731
    fresh, fresh_stats, fresh_matches = get("/"), get("/stats"), get("/matches")
    text = "本赛季（第 31 赛季：11/03 起）还没有对局，打完一局就会算进来"
    out["新赛季刚开始（只读过一次大厅）：总览页段位是大厅读到的白银 I 688，统计都是\"—\"、胜率不标红"] = (
        "白银 I" in fresh and ">688<" in fresh and stat(fresh, "统计局数") == "0" and stat(fresh, "胜率") == "—"
        and 'class="stat-value num accent"' not in fresh)
    out["新赛季刚开始：最近对局、数据页、战绩页都写明本赛季还没有对局，给出去上赛季、全部的链接"] = (
        all(text in page for page in (fresh, fresh_stats, fresh_matches))
        and '看看<a class="text-link" href="/?range=last" title="第 30 赛季：08/04 起，到 11/03">上赛季</a>或' in fresh
        and 'href="/stats?range=all"' in fresh_stats and 'class="stats-grid"' not in fresh_stats
        and 'href="/matches?range=last"' in fresh_matches)

    seasons.SEASONS[:] = [SEASONS[1]]
    only = get("/")
    out["配置里只有一个赛季：没有\"上赛季\"按钮，网址里写了也当作本赛季"] = (
        "上赛季</a>" not in only and on(get("/?range=last")) == ["本赛季"])
    seasons.SEASONS[:] = SEASONS
    return out


def main():
    print("按赛季看（本赛季 / 上赛季，总览页、数据页、战绩页）")
    seasons.SEASONS[:] = SEASONS
    try:
        checks = {**module_checks(), **calibration_checks(), **panel_checks()}
    finally:
        seasons.SEASONS[:] = TEST_SEASONS
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
