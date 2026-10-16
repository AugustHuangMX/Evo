"""本地数据库：把每局数据存进 SQLite（Python 自带，不用安装）。

数据库文件在 data/evo.db（已被 .gitignore 排除，不会上传）。
用 Stata 类比：数据库 ≈ 硬盘上的一个 .dta 文件，matches 表的每一行 = 一局。

查看最近的记录：python storage.py
导出成 CSV：    python storage.py --export data/matches.csv
"""

import argparse
import csv
import sqlite3
from pathlib import Path

DB_PATH = Path("data/evo.db")
APP_VERSION = "0.0.1"
SCHEMA_VERSION = 1  # 以后表结构变了就加 1，程序据此自动升级旧数据库

# 列名和类型，顺序也是导出 CSV 时的列顺序
COLUMNS = {
    "match_id": "INTEGER PRIMARY KEY AUTOINCREMENT",  # 每局的编号，自动递增
    "started_at": "TEXT",  # 开局时间（等状态机做好才有）
    "ended_at": "TEXT",  # 识别到总结页的时间，如 2026-10-16 21:03:15
    "mode": "TEXT",
    "map": "TEXT",
    "legend": "TEXT",
    "placement": "INTEGER",
    "squad_kills": "INTEGER",
    "kills": "INTEGER",
    "assists": "INTEGER",
    "knockdowns": "INTEGER",
    "damage": "INTEGER",
    "survival_time": "INTEGER",  # 秒
    "revives": "INTEGER",
    "respawns": "INTEGER",
    "rp_delta": "INTEGER",
    "rp_source": "TEXT",
    "rank_tier": "TEXT",
    "rank_division": "TEXT",
    "rank_progress": "INTEGER",
    "needs_review": "INTEGER NOT NULL DEFAULT 0",  # 1 = 待确认
    "review_note": "TEXT",  # 为什么待确认
    "evidence_path": "TEXT",  # 总结页截图的路径
    "app_version": "TEXT",
}


def connect(db_path=DB_PATH):
    """打开数据库；第一次用时自动建表。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 让查询结果可以按列名取值，比如 row["kills"]
    if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
        cols = ",\n  ".join(f"{name} {kind}" for name, kind in COLUMNS.items())
        conn.execute(f"CREATE TABLE IF NOT EXISTS matches (\n  {cols}\n)")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    return conn


def insert_match(conn, match):
    """存一局。match 是字典，键是列名，没给的列留空。返回这一局的 match_id。"""
    data = {k: v for k, v in match.items() if k in COLUMNS and k != "match_id"}
    data.setdefault("app_version", APP_VERSION)
    names = ", ".join(data)
    # 用 ? 占位、把值单独传进去，而不是直接拼进 SQL 文本：数据里有引号等特殊字符也不会出错
    marks = ", ".join("?" for _ in data)
    cur = conn.execute(f"INSERT INTO matches ({names}) VALUES ({marks})", list(data.values()))
    conn.commit()
    return cur.lastrowid


def recent_matches(conn, limit=20):
    return conn.execute("SELECT * FROM matches ORDER BY match_id DESC LIMIT ?", (limit,)).fetchall()


def _where(mode=None, only_review=False, legend=None):
    """把筛选条件拼成 SQL 的 WHERE 部分。值用 ? 占位，单独传入。"""
    clauses, params = [], []
    if mode:
        clauses.append("mode = ?")
        params.append(mode)
    if only_review:
        clauses.append("needs_review = 1")
    if legend:
        clauses.append("legend = ?")
        params.append(legend)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def query_matches(conn, mode=None, only_review=False, legend=None, limit=20, offset=0):
    """按条件查询，最新的在前。返回 (这一页的记录, 符合条件的总局数)。"""
    where, params = _where(mode, only_review, legend)
    rows = conn.execute(
        f"SELECT * FROM matches{where} ORDER BY ended_at DESC, match_id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM matches{where}", params).fetchone()[0]
    return rows, total


def all_matches(conn):
    """所有对局，按时间从早到晚（总览页算统计、画走势图用）。"""
    return conn.execute("SELECT * FROM matches ORDER BY ended_at, match_id").fetchall()


def count_review(conn):
    return conn.execute("SELECT COUNT(*) FROM matches WHERE needs_review = 1").fetchone()[0]


def distinct_values(conn, column):
    """某一列出现过的所有不同取值（比如所有用过的传奇），用于筛选下拉框。"""
    if column not in COLUMNS:
        raise ValueError(column)
    rows = conn.execute(f"SELECT DISTINCT {column} FROM matches WHERE {column} IS NOT NULL ORDER BY {column}")
    return [r[0] for r in rows]


def get_match(conn, match_id):
    return conn.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone()


def update_match(conn, match_id, fields):
    """修改一局的若干列。fields 是字典，键必须是列名。"""
    data = {k: v for k, v in fields.items() if k in COLUMNS and k != "match_id"}
    if not data:
        return
    sets = ", ".join(f"{k} = ?" for k in data)
    conn.execute(f"UPDATE matches SET {sets} WHERE match_id = ?", [*data.values(), match_id])
    conn.commit()


def delete_match(conn, match_id):
    conn.execute("DELETE FROM matches WHERE match_id = ?", (match_id,))
    conn.commit()


def backup_db(conn, path):
    """把整个数据库备份到 path（用 SQLite 自带的备份功能，数据库正在使用时也能安全备份）。"""
    target = sqlite3.connect(path)
    try:
        conn.backup(target)
    finally:
        target.close()


def delete_all_matches(conn):
    """删除所有对局，返回删了几局。"""
    n = conn.execute("DELETE FROM matches").rowcount
    conn.commit()
    return n


def write_csv(conn, f):
    """把全部数据按 CSV 格式写进一个已打开的文件（或内存里的文本缓冲）。"""
    rows = conn.execute("SELECT * FROM matches ORDER BY match_id").fetchall()
    writer = csv.writer(f)
    writer.writerow(COLUMNS)
    writer.writerows([tuple(r) for r in rows])
    return len(rows)


def export_csv(conn, path):
    """导出全部数据成 CSV 文件。用 utf-8-sig 编码，Excel 和 Stata 打开中文都不乱码。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        return write_csv(conn, f)


def mmss(seconds):
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds is not None else "-"


def dash(value):
    """空值显示成 -（注意 0 是有效值，不能当成空）。"""
    return "-" if value is None else value


def main():
    parser = argparse.ArgumentParser(description="查看或导出本地战绩数据库")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="数据库文件（默认 data/evo.db）")
    parser.add_argument("--export", type=Path, help="导出全部数据到这个 CSV 文件")
    args = parser.parse_args()

    conn = connect(args.db)
    if args.export:
        n = export_csv(conn, args.export)
        print(f"已导出 {n} 局到 {args.export.resolve()}")
        return

    rows = recent_matches(conn)
    total = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"数据库: {args.db.resolve()}  共 {total} 局，显示最近 {len(rows)} 局\n")
    print(f"{'编号':>4}  {'时间':<19}  {'名次':>4}  {'击杀/助攻/击倒':<10}  {'伤害':>6}  {'生存':>6}  待确认")
    for r in rows:
        kda = "/".join(str(dash(r[k])) for k in ("kills", "assists", "knockdowns"))
        flag = f"⚠ {r['review_note']}" if r["needs_review"] else ""
        print(f"{r['match_id']:>4}  {dash(r['ended_at']):<19}  {dash(r['placement']):>4}  {kda:<10}  "
              f"{dash(r['damage']):>6}  {mmss(r['survival_time']):>6}  {flag}")


if __name__ == "__main__":
    main()
