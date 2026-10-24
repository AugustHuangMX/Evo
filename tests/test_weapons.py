"""最常用武器（2026-10-24）：对局中右下角认手上那把武器（weapons.py、config/weapons.json、config/templates/weapons/），
记下每一段拿在手上的时间（in_match.py → post_match.py → weapon_holds 表），数据页"最常用武器"（panel_app.weapon_table）。

- 认武器：2026-10-24 用户在靶场截的 16:9 截图，加上 16:10、4:3 那两局（captures/aspect/），答案在 tests/weapons_answers.csv
  （每张截图应该认成哪把武器，"-" = 不是武器：刀刃、死亡回放等）。答案是这样来的：截图下面的武器名用识别引擎读出来，
  和图标认出的一致；不一致的 22 张逐张看过（都是开发工具判断"哪把在手上"判断错了，图标认的是对的）
- 每一张都比外观、不用识别引擎；图标没变不重新认
- 计时：同一把武器连着看到算一段；认不出的不计时；隔太久没看到（开地图、观战）算两段；时间从第一次拿到武器算起
- 接进记录程序（假游戏）：靶场里兰伯特那一段（塞拉 → 刀刃 → 辅助手枪），一局结束后记进 weapon_holds 表
- 数据库：旧数据库升级时加上这张表；删除一局、清空战绩时一起删
- 数据页：只算每局第一次拿到武器 1 分钟以后的；按拿在手上的时间排；占比、局数；页面上不写这条规则；没有武器数据时写明
截图不在仓库里，找不到就跳过那一部分（见 fake_game.py）。

运行：python tests/test_weapons.py
"""

import csv
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import fake_game
from fake_game import SHOTS, MissingShots, load, report, run_collect, shot  # 要在项目代码之前导入

import numpy as np

import layout
import panel_app
import storage
import weapons
from weapons import WeaponDetector, WeaponTimer

HERE = Path(__file__).parent
ANSWERS = HERE / "weapons_answers.csv"
FOLDER = Path(tempfile.mkdtemp(prefix="evo-weapons-", dir=storage.DB_PATH.parent.parent))
RANGE = "frames/20261024_170054/"
START = "live/20261016_211452/match_start_213625_enter.png"  # 开局画面（同 test_in_match.py）
ELIMINATED = "samples/死亡瞬间.png"
LOBBY = "live/20261016_211452/lobby_211506_enter.png"
ASPECTS = {"16x10_20261020": 16 / 10, "4x3_20261020": 4 / 3}


def recognition_checks(detector):
    """答案里的每一张截图都认一遍。"""
    rows = list(csv.DictReader(ANSWERS.open(encoding="utf-8")))
    for r in rows:
        shot(r["screenshot"])  # 少了截图就整块跳过
    results = {}  # 哪一组 → [对, 错的例子]
    mask_ms, identify_ms = [], []
    for r in rows:
        group = next((name for name in ASPECTS if name in r["screenshot"]), "16:9 靶场")
        layout.set_game_aspect(ASPECTS.get(group))
        frame = load(SHOTS / r["screenshot"])
        t = time.perf_counter()
        mask = detector.mask(frame)
        detector.changed(mask, None)
        mask_ms.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        got, score = detector.identify(mask)
        identify_ms.append((time.perf_counter() - t) * 1000)
        want = None if r["weapon"] == "-" else r["weapon"]
        kind = "不是武器" if want is None else "武器"
        right, wrong = results.setdefault((group, kind), [0, []])
        if got == want:
            results[(group, kind)][0] += 1
        else:
            wrong.append(f"{Path(r['screenshot']).name[:4]} 应为 {want} 认成 {got}（{score:.2f}）")
    layout.set_game_aspect(None)
    out = {}
    for (group, kind), (right, wrong) in results.items():
        label = "认对" if kind == "武器" else "认不出（不计时）"
        out[f"{group}：{kind} {right + len(wrong)} 张都{label}" + (f"（错的：{wrong[:3]}）" if wrong else "")] = not wrong
    out[f"每张截图看图标变没变很快（平均 {np.mean(mask_ms):.2f} 毫秒），认一次也不慢（平均 {np.mean(identify_ms):.1f} 毫秒）"] = \
        np.mean(mask_ms) < 2 and np.mean(identify_ms) < 30
    return out


class FakeDetector:
    """计时规则用：每张"截图"就是要认成的武器（"-" = 认不出），图标变了才算重新认（和真的一样：还没认过也算变了）。"""

    cfg = {"gap_seconds": 2.0}

    def __init__(self):
        self.calls = 0

    def mask(self, frame):
        return frame

    def changed(self, a, b):
        return b is None or a != b

    def identify(self, mask):
        self.calls += 1
        return (None if mask == "-" else mask), 1.0

    def name(self, key):
        return key


def timer_checks():
    det = FakeDetector()
    timer = WeaponTimer(det)
    t0 = datetime(2026, 10, 24, 20, 0, 0)
    seq = [(0, "-"), (1, "r301"), (1.3, "r301"), (1.6, "-"), (1.9, "r301"), (2.2, "r301"),  # 中间一张认不出：还是一段
           (2.5, "wingman"), (2.8, "wingman"),
           (6.0, "wingman"), (6.3, "wingman"),  # 隔了 3.2 秒没看到（开地图）：另一段
           (6.6, "r301")]
    for sec, key in seq:
        timer.update(key, t0 + timedelta(seconds=sec))
    got = timer.result()
    timer.reset()
    return {
        f"计时：连着看到算一段、认不出的不计时、隔太久算两段，从第一次拿到武器算起（{got}）":
            got == [("r301", 0.0, 1.2), ("wingman", 1.5, 1.8), ("wingman", 5.0, 5.3), ("r301", 5.6, 5.6)],
        f"图标没变不重新认（11 张截图认了 {det.calls} 次）": det.calls == 6,
        "新的一局清空": timer.result() == [],
    }


def collect_checks():
    """假游戏：开局画面 → 靶场兰伯特那一段（0229–0248）→ 淘汰画面 → 大厅。
    假游戏只把"等待"拨快，记录程序取的"现在几点"是真的时间，所以这里每张截图之间只隔十几毫秒：只看顺序和有没有记下来
    （计时规则见 timer_checks）。"""
    names = sorted(p.name for p in (SHOTS / RANGE).glob("*.png"))[228:248]
    frames = [START, *(RANGE + n for n in names), ELIMINATED, ELIMINATED, LOBBY, LOBBY]
    images = {f: load(shot(f)) for f in frames}
    rows, sleeps, log = run_collect(frames, images.get)
    conn = storage.connect(fake_game.last_db)
    holds = [tuple(r) for r in conn.execute("SELECT match_id, weapon, start_s, end_s FROM weapon_holds ORDER BY start_s")]
    conn.close()
    said = [line for line in log if "对局结束" in line]
    sheila = next((h for h in holds if h[1] == "sheila"), None)
    wingman = next((h for h in holds if h[1] == "wingman"), None)
    return {
        f"接进记录程序：一局记下来，拿过塞拉和辅助手枪（{holds}）":
            len(rows) == 1 and [h[1] for h in holds] == ["sheila", "wingman"] and all(h[0] == rows[0]["match_id"] for h in holds),
        "塞拉从第 0 秒开始；中间认不出的刀刃不计时，辅助手枪在塞拉之后另起一段":
            sheila is not None and wingman is not None and sheila[2] == 0.0 and sheila[3] > 0
            and wingman[2] >= sheila[3] and wingman[3] >= wingman[2],
        f"日志里写了这局拿了哪些武器（{said[-1] if said else '没有'}）": bool(said) and "塞拉" in said[-1] and "辅助手枪" in said[-1],
    }


def storage_checks():
    path = FOLDER / "old.db"
    conn = storage.connect(path)
    conn.execute("DROP TABLE weapon_holds")
    conn.execute("PRAGMA user_version = 7")  # 0.3.8 的数据库：还没有这张表
    conn.commit()
    conn.close()
    conn = storage.connect(path)
    upgraded = conn.execute("SELECT name FROM sqlite_master WHERE name = 'weapon_holds'").fetchone() is not None
    a = storage.insert_match(conn, {"ended_at": "2026-10-24 20:00:00", "player_name": "Me"})
    b = storage.insert_match(conn, {"ended_at": "2026-10-24 20:30:00", "player_name": "Other"})
    storage.save_weapon_holds(conn, a, [("r301", 0.0, 10.0), ("wingman", 11.0, 20.0)])
    storage.save_weapon_holds(conn, a, [("r301", 0.0, 12.0)])  # 同一局再存一次：覆盖
    storage.save_weapon_holds(conn, b, [("eva8", 0.0, 5.0)])
    mine = [tuple(r)[1:] for r in storage.all_weapon_holds(conn, ["Me"])]
    everyone = len(storage.all_weapon_holds(conn))
    storage.delete_match(conn, a)
    after_delete = len(storage.all_weapon_holds(conn))
    storage.delete_all_matches(conn)
    after_clear = conn.execute("SELECT COUNT(*) FROM weapon_holds").fetchone()[0]
    conn.close()
    return {
        "旧数据库（第 7 版）升级时加上武器表": upgraded,
        f"同一局再存一次是覆盖；只看一个账号时只有它的（{mine}）": mine == [("r301", 0.0, 12.0)] and everyone == 2,
        "删除一局、清空战绩时武器也一起删": after_delete == 1 and after_clear == 0,
    }


def panel_checks():
    path = FOLDER / "panel.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)
    start = datetime(2026, 10, 1, 20, 0, 0)
    ids = [storage.insert_match(conn, {"ended_at": f"{start + timedelta(minutes=30 * i):%Y-%m-%d %H:%M:%S}",
                                       "player_name": "Me", "placement": 5}) for i in range(32)]
    # 最早的一局（不在"近 30 局"里）：克雷贝尔拿了很久
    storage.save_weapon_holds(conn, ids[0], [("kraber", 0.0, 1000.0)])
    # 最近的两局：第一次拿到武器 60 秒以后才算
    storage.save_weapon_holds(conn, ids[-2], [("r301", 0.0, 100.0), ("wingman", 100.0, 160.0), ("r301", 170.0, 400.0)])
    storage.save_weapon_holds(conn, ids[-1], [("eva8", 0.0, 50.0), ("flatline", 55.0, 125.0)])
    conn.close()
    panel_app.app.config.update(DB=path, TESTING=True)
    client = panel_app.app.test_client()
    recent = client.get("/stats").get_data(as_text=True)
    everything = client.get("/stats?range=all").get_data(as_text=True)
    empty_path = FOLDER / "no_weapons.db"
    empty_path.unlink(missing_ok=True)
    conn = storage.connect(empty_path)
    storage.insert_match(conn, {"ended_at": "2026-10-01 20:00:00", "placement": 3})
    conn.close()
    panel_app.app.config["DB"] = empty_path
    empty = panel_app.app.test_client().get("/stats").get_data(as_text=True)

    def table(page):
        found = re.search(r'<table class="stats-table weapons-table">.*?<tbody>(.*?)</tbody>', page, re.S)
        if not found:
            return []
        return [[" ".join(re.sub(r"<[^>]+>", " ", c).split()) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
                for row in re.findall(r"<tr[^>]*>(.*?)</tr>", found.group(1), re.S)]

    rows = table(recent)
    section = re.search(r'aria-labelledby="h-weapons">(.*?)</section>', recent, re.S)
    section = section.group(1) if section else ""
    return {
        f"最常用武器：只算第一次拿到武器 1 分钟以后的，按时间排（{rows}）": rows == [
            ["合计", "6 分 35 秒", "100%", "2"],
            ["R-301", "4 分 30 秒", "68%", "1"],
            ["平行步枪", "1 分 5 秒", "16%", "1"],
            ["辅助手枪", "1 分 0 秒", "15%", "1"]],
        "1 分钟以内的不算（EVA-8 只在前 50 秒拿过，不出现）": "EVA-8" not in section,
        "近 30 局：更早的局不算；全部：算上": "克雷贝尔" not in section and "克雷贝尔" in everything,
        "数据页最上面是最常用武器": recent.find('id="h-weapons"') < recent.find('id="h-mates"'),
        "页面上不写怎么统计的（用户决定）": not re.search(r"1 分钟|60 秒|第一次拿到", section),
        "没有武器数据时写明，不出空表": "这个范围里还没有记下武器" in empty and "weapons-table" not in empty,
    }


def template_checks(detector):
    names = set(detector.weapons)
    have = {k for k, _ in detector.templates}
    files = sorted(p.name for p in weapons.TEMPLATE_DIR.glob("*.png"))
    return {
        f"名单里的 {len(names)} 把武器都有参照图（共 {len(files)} 张）": names == have,
        "参照图的文件名都对得上名单（<编号>.png、<编号>-2.png）": all(weapons.template_key(f) in names for f in files),
        "\"手感卓越的刀刃\"不在名单里（用户决定不算）": not any("刀刃" in w["zh"] for w in detector.weapons.values()),
        "万蒂奇的大招显示成\"万蒂奇的狙击枪\"": detector.name("vantage_sniper") == "万蒂奇的狙击枪",
    }


def main():
    print("最常用武器：认武器、计时、记进数据库、数据页")
    detector = WeaponDetector()
    checks = {**template_checks(detector), **timer_checks(), **storage_checks(), **panel_checks()}
    try:
        checks.update(recognition_checks(detector))
        checks.update(collect_checks())
    except MissingShots as e:
        print(f"  ⏭ 截图部分跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
