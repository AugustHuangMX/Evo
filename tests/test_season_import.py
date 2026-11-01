"""录入历史赛季数据：新表 season_stats（storage.py）、录入模式的开关（season_import.py）、记录程序里读数据页
（live_collect.SeasonWatcher）。

- 新表（不用截图）：读到的存成待核对，同一项再读到就换掉；保存时换掉原来保存的那一行、记下改过哪几项；删除；
  旧数据库（版本 8）打开时自动加上这张表；Evo 认识的名字 = 对局里出现过的
- 录入模式：按一次开 10 分钟，还剩几秒，到时间、点"结束"就关
- 记录程序（用假游戏喂用户 2026-11-01 截的数据页，游戏是 16:10 拉伸，和实际打游戏一样按 16:10 换算，见 test_season_stats.py）：
  - 录入模式开着：S30 全部、S30 排位、S29 全部、生涯合计存成待核对，数字和答案一样；下拉菜单开着的不读；
    被鼠标弹出的说明框挡住的（S27）不存、日志里提醒；0 局的（S13）不存；同一项再点回来、数字没变不再存；
    存一个赛季响一声（生涯合计不响）；停在数据页时每 0.3 秒一张
  - 录入模式关着：一张都不读，数据页上一次识别都不跑
  - 页面上的名字不是 Evo 认识的：不读，日志说一次
  找不到截图时这部分跳过

运行：python tests/test_season_import.py
"""

import csv
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import fake_game
from fake_game import SOUNDS_PLAYED, SHOTS, load, report, run_collect  # 要在项目代码之前导入（设置临时数据文件夹）

import live_collect
import ocr
import season_import
import storage

FOLDER = SHOTS / "frames" / "20261101_161135"
ANSWERS = Path(__file__).with_name("season_stats_20261101.csv")
TEMP = Path(tempfile.mkdtemp(prefix="evo-seasons-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里


def row(season, kind, games, name="ImperialHzd", **more):
    return {"player_name": name, "season": season, "kind": kind, "season_name": "排位" if kind == "ranked" else "X",
            "games": games, "read_at": "2026-11-01 16:12:32", **more}


def table_checks():
    out = {}
    conn = storage.connect(TEMP / "seasons.db")
    first = storage.save_season_pending(conn, row(30, "ranked", 800))
    again = storage.save_season_pending(conn, row(30, "ranked", 821, name="Imperial Hzd"))  # 同一项：名字空格不同也算
    other = storage.save_season_pending(conn, row(30, "all", 821))
    career = storage.save_season_pending(conn, row(None, "career", 10146, approx=["top5"]))
    pending = storage.season_rows(conn, "pending")
    out["同一项再读到：换掉原来待核对的那一行（不管名字里的空格）"] = (
        [r["stat_id"] for r in pending] == [other, again, career] and first not in [r["stat_id"] for r in pending])
    out["排序：赛季从新到旧，全部在排位前面，生涯合计在最后；约数的项记下来"] = (
        [(r["season"], r["kind"]) for r in pending] == [(30, "all"), (30, "ranked"), (None, "career")]
        and pending[2]["approx"] == "top5")
    storage.save_season(conn, again)
    newer = storage.save_season_pending(conn, row(30, "ranked", 830))
    storage.save_season(conn, newer, {"games": 831, "wins": None, "season_name": "不能改"})
    saved = storage.season_rows(conn, "saved")
    out["保存：换掉原来保存的那一行，记下改过哪几项（只记 15 个数）"] = (
        [(r["stat_id"], r["games"], r["edited"], r["season_name"]) for r in saved] == [(newer, 831, "games", "排位")]
        and not storage.save_season(conn, 9999))
    storage.delete_season(conn, other)
    out["删除一行"] = [r["stat_id"] for r in storage.season_rows(conn)] == [newer, career]
    storage.insert_match(conn, {"player_name": "Imperial Hzd", "ended_at": "2026-11-01 16:00:00"})
    out["Evo 认识的名字：对局里出现过的（去掉空格）"] = storage.known_names(conn) == {"ImperialHzd"}
    conn.close()

    old = TEMP / "old.db"
    raw = sqlite3.connect(old)
    raw.execute("CREATE TABLE matches (match_id INTEGER PRIMARY KEY)")
    raw.execute("PRAGMA user_version = 8")
    raw.commit()
    raw.close()
    conn = storage.connect(old)
    out["旧数据库（版本 8）打开时加上历史赛季表"] = (
        conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
        and storage.season_rows(conn) == [])
    conn.close()
    return out


def mode_checks():
    now = datetime(2026, 11, 1, 16, 0, 0)
    until = season_import.start(now)
    out = {
        "按一次：开 10 分钟": until == now + timedelta(minutes=10) and season_import.until() == until,
        "还剩几秒；到时间就关": season_import.remaining(now + timedelta(minutes=3)) == 420
        and season_import.active(now + timedelta(minutes=9)) and not season_import.active(now + timedelta(minutes=10)),
    }
    season_import.stop()
    out["点\"结束\"：马上关"] = not season_import.active(now) and season_import.until() is None
    return out


def collect_checks():
    """返回检查结果；没有截图返回 None。"""
    if not FOLDER.is_dir():
        return None
    names = {f.name[:4]: f for f in FOLDER.glob("*.jpg")}
    images = {}

    def picture(key):
        if key not in images:
            images[key] = load(names[key])
        return images[key]

    with ANSWERS.open(encoding="utf-8-sig") as f:
        answers = {r["frame"][:4]: r for r in csv.DictReader(f)}
    # S30 全部 → 下拉菜单开着 → S30 排位 → S27 全部（被说明框挡住）→ S13（0 局）→ S29 全部 → 又点回 S30 全部
    frames = ["0036", "0036", "0037", "0038", "0038", "0049", "0049", "0103", "0103", "0040", "0040", "0039",
              "0036", "0036"]
    ran_on = []
    real_create = ocr.create_engine

    def counting_engine(params=None):
        engine = real_create(params)

        def run(img, **kwargs):
            ran_on.append(fake_game.FakeCamera.showing)
            return engine(img, **kwargs)
        return run

    def rows_of(db):
        conn = storage.connect(db)
        out = storage.season_rows(conn)
        conn.close()
        return out

    out = {}
    season_import.start()
    SOUNDS_PLAYED.clear()
    with mock.patch.object(ocr, "create_engine", counting_engine):
        _, sleeps, log = run_collect(frames, picture, game_aspect="16:10")
    got = rows_of(fake_game.last_db)
    sounds = list(SOUNDS_PLAYED)
    want = {(30, "all"): "0036", (30, "ranked"): "0038", (29, "all"): "0040"}
    wrong = []
    for r in got:
        if r["kind"] == "career":
            if (r["games"], r["damage"], r["approx"]) != (10146, 4678720, "top5"):
                wrong.append(f"生涯合计 {r['games']} 局")
            continue
        answer = answers[want.pop((r["season"], r["kind"]), "????")] if (r["season"], r["kind"]) in want else None
        if answer is None:
            wrong.append(f"多存了第 {r['season']} 赛季 {r['kind']}")
            continue
        bad = [k for k in storage.SEASON_FIELDS if str(r[k]) != answer[k]]
        if bad or r["status"] != "pending" or r["player_name"] != "ImperialHzd" or r["review_note"]:
            wrong.append(f"第 {r['season']} 赛季 {r['kind']}：{bad or r['review_note']}")
    out[f"录入模式开着：S30 全部、S30 排位、S29 全部、生涯合计存成待核对，数字都对（{len(got)} 行；错的：{wrong or want or '无'}）"] = (
        len(got) == 4 and not wrong and not want)
    out["  被说明框挡住的 S27 不存，日志里提醒移开鼠标；0 局的 S13 不存，日志里说一句"] = (
        any("被鼠标弹出的说明框挡住" in line and "第 27 赛季" in line for line in log)
        and any("第 13 赛季 全部 0 局，不记" in line for line in log))
    out[f"  存一个赛季响一声，生涯合计不响，点回 S30 不再响（{sounds}）"] = sounds == ["summary"] * 3
    out["  日志写了开始、存了几项"] = (any("录入历史赛季数据：开始" in line for line in log)
                                and any("这次存了 4 项待核对" in line for line in log))
    stats_sleeps = [s for frame, s in sleeps if frame in ("0036", "0038", "0040")]
    out[f"  停在数据页时每 0.3 秒一张（{sorted(set(stats_sleeps))}）"] = stats_sleeps and max(stats_sleeps) <= 0.3

    season_import.stop()
    ran_on.clear()
    with mock.patch.object(ocr, "create_engine", counting_engine):
        run_collect(frames, picture, game_aspect="16:10")
    out[f"录入模式关着：一项都不存，数据页上一次识别都不跑（{len(ran_on)} 次）"] = (
        rows_of(fake_game.last_db) == [] and not ran_on)

    season_import.start()
    with mock.patch.object(live_collect.storage, "known_names", lambda conn: {"SomeoneElse"}):
        _, _, log = run_collect(frames[:5], picture, game_aspect="16:10")
    season_import.stop()
    told = [line for line in log if "不是 Evo 认识的你的名字" in line]
    out[f"页面上的名字不是 Evo 认识的（好友资料页）：不读，日志只说一次（{len(told)} 次）"] = (
        rows_of(fake_game.last_db) == [] and len(told) == 1)
    return out


def main():
    print("录入历史赛季数据（新表、录入模式、记录程序读数据页）")
    checks = {**table_checks(), **mode_checks()}
    shots = collect_checks()
    if shots is None:
        print(f"  ⏭ 截图部分跳过：找不到 {FOLDER}（截图只在开发者电脑上，不在仓库里）")
    else:
        checks.update(shots)
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
