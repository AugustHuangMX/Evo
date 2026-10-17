"""给以前记下的局补上队友：从每一局的总结页截图上重新读左右两张卡片（见 code/teammates.py）。

只在开发时用。默认只预览、不改数据库；加 --write 才写，写之前先备份数据库（evo.db.bak-时间-before-teammates）。
数据文件夹按 paths.py 的规则找；补安装版的数据时用环境变量 EVO_DATA_DIR 指过去：
  预览：$env:EVO_DATA_DIR = "E:\\Evo Tracker Data"; python tools/backfill_teammates.py
  写入：$env:EVO_DATA_DIR = "E:\\Evo Tracker Data"; python tools/backfill_teammates.py --write

怎么找截图：先看数据库里记的截图位置；不在了（识别完删掉了）就按这一局的结束时间，在数据文件夹和项目的 captures/live/ 里
找同一秒存的总结页截图（文件名"编号_时分秒.png"，文件夹名是开始记录的日期，打到凌晨的局在前一天的文件夹里）。
防止找错截图：截图上自己的击杀和伤害要和数据库里的一样，对不上就跳过。
已经有队友的局、只有经验值页的局（没有总结页）不补。
"""

import argparse
import re
import sqlite3
from datetime import datetime, timedelta

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
import storage
from ocr import SMALL_CROP_PARAMS, create_engine, load_config, load_image
from paths import APP_DIR, CAPTURES_DIR, DB_PATH, data_absolute
from process_info import set_low_priority
from summary_page import CONFIG_NAME, read_summary
from teammates import describe, read_teammates

SEARCH_DIRS = list(dict.fromkeys([CAPTURES_DIR / "live", APP_DIR / "captures" / "live"]))  # 去掉重复的（源码运行时是同一个）


def find_screenshot(row):
    """这一局的总结页截图，找不到返回 None。只有经验值页的局记的是经验值页截图（xp_…），不算。"""
    if row["evidence_path"]:
        path = data_absolute(row["evidence_path"])
        if path.is_file() and re.fullmatch(r"\d\d_\d{6}\.png", path.name):
            return path
    if not row["ended_at"]:
        return None
    ended = datetime.strptime(row["ended_at"], "%Y-%m-%d %H:%M:%S")
    days = {f"{ended:%Y%m%d}", f"{ended - timedelta(days=1):%Y%m%d}"}
    for folder in SEARCH_DIRS:
        for path in sorted(folder.glob(f"*/[0-9][0-9]_{ended:%H%M%S}.png")):
            if path.parent.name[:8] in days:
                return path
    return None


def same_player(results, row):
    """截图上自己的击杀和伤害和数据库里一样（数据库里空着的项不比）。"""
    kda, damage = results["kda"]["value"], results["damage"]["value"]
    pairs = [(kda[0] if kda else None, row["kills"]), (damage, row["damage"])]
    return all(a == b for a, b in pairs if a is not None and b is not None) and any(
        a is not None and b is not None for a, b in pairs)


def main():
    parser = argparse.ArgumentParser(description="用总结页截图给以前的局补上队友")
    parser.add_argument("--write", action="store_true", help="真的写进数据库（先自动备份）；不加只预览")
    args = parser.parse_args()
    set_low_priority()  # 可能边打游戏边补：CPU 紧张时让游戏优先

    print(f"数据库：{DB_PATH}\n截图找这几处：{'、'.join(str(d) for d in SEARCH_DIRS)}\n")
    if args.write:  # 先备份，再打开（旧数据库在 storage.connect 里自动升级、加上队友表）
        backup = DB_PATH.with_name(f"{DB_PATH.name}.bak-{datetime.now():%Y%m%d-%H%M%S}-before-teammates")
        raw = sqlite3.connect(DB_PATH)
        storage.backup_db(raw, backup)
        raw.close()
        conn = storage.connect(DB_PATH)
    else:  # 预览：只读打开，连升级都不做
        conn = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM matches ORDER BY ended_at, match_id").fetchall()
    has_mates = set()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'teammates'").fetchone():
        has_mates = {r[0] for r in conn.execute("SELECT DISTINCT match_id FROM teammates")}
    cfg, engine = load_config(CONFIG_NAME), create_engine(SMALL_CROP_PARAMS)

    found, skipped = {}, []
    for row in rows:
        label = f"编号 {row['match_id']:>3}（{row['ended_at']}）"
        if row["match_id"] in has_mates:
            skipped.append(f"{label} 已经有队友")
            continue
        path = find_screenshot(row)
        if path is None:
            skipped.append(f"{label} 没有总结页截图" + ("（只有经验值页的局）" if row["damage"] is None else ""))
            continue
        img = load_image(path)
        results, _ = read_summary(engine, img, cfg)
        if not same_player(results, row):
            skipped.append(f"{label} 截图 {path.name} 上自己的击杀/伤害和数据库对不上，可能不是这一局")
            continue
        mates = read_teammates(engine, img, cfg)
        found[row["match_id"]] = mates
        print(f"{label}  {path.parent.name}/{path.name}")
        for mate in mates:
            print(f"    {'左' if mate['slot'] == 'left' else '右'} {describe(mate)}")

    print(f"\n能补上 {len(found)} 局；不补 {len(skipped)} 局：")
    for line in skipped:
        print(f"    {line}")
    if not args.write:
        print("\n这是预览，没有改数据库。确认没问题后加 --write 再运行一次。")
        return
    for match_id, mates in found.items():
        storage.save_teammates(conn, match_id, mates)
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"\n已补上 {len(found)} 局的队友。写之前的备份：{backup}；完整性检查：{ok}")


if __name__ == "__main__":
    main()
