"""玩家名（player_name.py）：读得出自己的名字，用来区分账号。

- 读名字：所有总结页的中间卡片（2560x1440 实战 + 1080p 录像）、靶场里对局中头像旁边，都读成 ImperialHzd；
  不是这两种画面的截图（大厅、开局、经验值页、观战、桌面、Esc 菜单）在对局中的位置上读不出名字
- 接进记录程序（用假游戏跑几段，每帧之间假时钟走 1 秒）：
  - 开局 → 对局中 → 总结页：记在 ImperialHzd 下；总结页先读到了名字，对局中存下的不用再读（右上角回到大厅才读，2026-10-27）
  - 开局 → 对局中 → 只有经验值页（第 2 名、冠军）：用对局中读到的名字
  - 开局 → 观战（认不出自己的传奇，不读）→ 只有经验值页 → 开局 → 总结页：第一局先空着，读到总结页后补上
  - 总结页认出名字后，下一局对局中不再读（打游戏时不加载识别模型）
- 名字的判断（不用截图）：不管空格算同一个名字；很像的当作识别误差；完全不同的当作换了账号
- 旧数据库自动升级：加上 player_name 列，原来的数据不变，再打开不会重复升级；导出 CSV 的列名和数据对得上
截图不在仓库里，见 fake_game.py。

运行：python tests/test_player_name.py
"""

import csv
import io
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from fake_game import BLACK, MissingShots, SHOTS, load, report, run_collect, shot  # 要在项目代码之前导入

import storage
from ocr import SMALL_CROP_PARAMS, create_engine
from player_name import PlayerNameReader
from post_match import PostMatch

ME = "ImperialHzd"
FULL_SET = "legends/20261017_154752/"  # 靶场：30 张里第 1 张是桌面、第 9 张是 Esc 菜单，其余是对局中自己的画面
SPECTATE = "samples/vlcsnap-2026-10-16-18h27m31s652.png"  # 观战敌人
NIGHT = "live/20261016_211452/"


def summary_pages():
    """所有总结页截图：实战的文件名是"编号_时间"，录像里的那张是 18h26m12s。"""
    pages = [p for p in sorted((SHOTS / "live").glob("*/*.png")) if re.fullmatch(r"\d\d_\d{6}", p.stem)]
    return pages + [shot("samples/vlcsnap-2026-10-16-18h26m12s173.png")]


def collect_checks():
    """用假游戏跑记录程序。返回 {这一段的名字: ([(名次, 玩家名), ...], 对局中读了几次名字)}。"""
    images = {
        "开局": load(shot(NIGHT + "match_start_213716_leave.png")),
        "对局中": load(shot(f"{FULL_SET}full_27.jpg")),  # 自己用侵蚀
        "观战": load(shot(SPECTATE)),
        "总结页": load(shot(NIGHT + "05_222749.png")),  # 第 8 名
        "经验值页": load(shot(NIGHT + "xp_214926_leave.png")),  # 第 7 名（这几段里没有对应的总结页）
        "别的": BLACK,
    }
    runs = {
        "对局中读": ["开局"] + ["对局中"] * 6 + ["总结页"] * 3,
        "只有经验值页": ["开局"] + ["对局中"] * 6 + ["经验值页", "别的"],
        "先空着后补上": ["开局"] + ["观战"] * 6 + ["经验值页", "别的", "开局"] + ["总结页"] * 3,
        "知道后不再读": ["开局"] + ["总结页"] * 3 + ["别的", "开局"] + ["对局中"] * 6 + ["经验值页", "别的"],
    }
    out = {}
    for name, frames in runs.items():
        rows, _, log = run_collect(frames, images.get)
        stats = next((line for line in log if line.startswith("本次记录")), "")
        hud = re.search(r"对局中 (\d+) 次", stats)
        out[name] = ([(r["placement"], r["player_name"]) for r in rows], int(hud[1]) if hud else None)
    return out


def rule_checks():
    """不用截图，直接检查 PostMatch 对名字的判断。返回每一局最后记在谁名下。"""
    conn = storage.connect(":memory:")
    post = PostMatch(conn, log=lambda line: None)
    when = datetime(2026, 10, 17, 20, 0)
    names = [None, "Imperial Hzd", "lmperialHzd", "Yukinoshita"]  # 没读到、多了空格、I 读成 l、另一个账号
    ids = []
    for name in names:
        match_id = storage.insert_match(conn, {"ended_at": f"{when:%Y-%m-%d %H:%M:%S}"})
        ids.append(match_id)
        post.on_summary_saved(match_id, {}, when, name)
        if name is None:  # 第一局没读到名字；之后在对局中读到
            post.on_player_name(ME, when, "对局中")
    return [storage.get_match(conn, i)["player_name"] for i in ids]


def migration_checks():
    """做一个旧版（版本 1，没有 player_name 列）的数据库，用新代码打开。"""
    old_columns = {k: v for k, v in storage.COLUMNS.items() if k != "player_name"}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "old.db"
        old = sqlite3.connect(path)
        old.execute(f"CREATE TABLE matches ({', '.join(f'{k} {v}' for k, v in old_columns.items())})")
        old.execute("PRAGMA user_version = 1")
        old.execute("INSERT INTO matches (ended_at, placement, damage, app_version) VALUES ('2026-10-16 21:33:31', 4, 476, '0.1.0')")
        old.commit()
        old.close()

        conn = storage.connect(path)
        conn.close()
        conn = storage.connect(path)  # 第二次打开：不能再升级一次（会报"列已存在"）
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        columns = [r[1] for r in conn.execute("PRAGMA table_info(matches)")]
        row = storage.get_match(conn, 1)
        storage.update_match(conn, 1, {"player_name": ME})
        buf = io.StringIO()
        storage.write_csv(conn, buf)
        conn.close()
    exported = list(csv.DictReader(io.StringIO(buf.getvalue())))
    return {
        "version": version,
        "has_column": "player_name" in columns,
        "kept": (row["placement"], row["damage"], row["player_name"]) == (4, 476, None),
        "csv": exported and (exported[0]["placement"], exported[0]["damage"], exported[0]["player_name"]) == ("4", "476", ME),
    }


def main():
    print("玩家名：读得出自己的名字，用来区分账号")
    try:
        pages = summary_pages()
        own = [shot(f"{FULL_SET}full_{n:02d}.jpg") for n in range(2, 31) if n != 9]
        others = [shot(f"{FULL_SET}full_01.jpg"), shot(f"{FULL_SET}full_09.jpg"), shot(SPECTATE)]
        others += sorted((SHOTS / NIGHT).glob("*_enter.png"))  # 大厅、开局、经验值页
        runs = collect_checks()
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None

    reader = PlayerNameReader(create_engine(SMALL_CROP_PARAMS))
    wrong_pages = [(p.name, found) for p in pages if (found := reader.read(load(p), "summary")) is None or found[0] != ME]
    wrong_own = [(p.name, found) for p in own if (found := reader.read(load(p), "hud")) is None or found[0] != ME]
    misread = [(p.name, found[0]) for p in others if (found := reader.read(load(p), "hud")) is not None]
    rules = rule_checks()
    migration = migration_checks()
    return report({
        f"{len(pages)} 张总结页的中间卡片都读成 {ME}（读错的：{wrong_pages or '无'}）": not wrong_pages,
        f"{len(own)} 张对局中的截图，头像旁边都读成 {ME}（读错的：{wrong_own or '无'}）": not wrong_own,
        f"{len(others)} 张不是对局中的截图，在头像旁边的位置读不出名字（读出的：{misread or '无'}）": not misread,
        f"记录程序：开局 → 对局中 → 总结页，记在 {ME} 下；总结页先读到了名字，对局中存下的不用再读（{runs['对局中读']}）":
            runs["对局中读"] == ([(8, ME)], 0),
        f"记录程序：只有经验值页的局，用对局中读到的名字（{runs['只有经验值页']}）":
            runs["只有经验值页"] == ([(7, ME)], 1),
        f"记录程序：观战时不读；第一局先空着，读到总结页后补上（{runs['先空着后补上']}）":
            runs["先空着后补上"] == ([(7, ME), (8, ME)], 0),
        f"记录程序：总结页认出名字后，下一局对局中不再读（{runs['知道后不再读']}）":
            runs["知道后不再读"] == ([(8, ME), (7, ME)], 0),
        f"名字的判断：后补、不管空格、I 读成 l 当作误差、另一个名字算换账号（{rules}）":
            rules == [ME, ME, ME, "Yukinoshita"],
        f"旧数据库升级到版本 {storage.SCHEMA_VERSION}，加上 player_name 列（{migration['version']}）":
            migration["version"] == storage.SCHEMA_VERSION and migration["has_column"],
        "升级后原来的数据不变，再打开一次不会出错": migration["kept"],
        "升级过的数据库导出 CSV，列名和数据对得上（新列排在最后也不错位）": migration["csv"],
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
