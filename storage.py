"""本地数据库：把每局数据存进 SQLite（Python 自带，不用安装）。

数据库文件在数据文件夹的 data/evo.db（位置见 paths.py；在项目里时已被 .gitignore 排除，不会上传）。
用 Stata 类比：数据库 ≈ 硬盘上的一个 .dta 文件，matches 表的每一行 = 一局。
另有一张账号对照表 player_names（玩家名 → 账号），见下面"账号"一节。

查看最近的记录：python storage.py
导出成 CSV：    python storage.py --export data/matches.csv
"""

import argparse
import csv
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path

from paths import DB_PATH

APP_VERSION = "0.2.0"
SCHEMA_VERSION = 3  # 表结构变了就加 1，并在 MIGRATIONS 里写上怎么从旧版本升级

# 列名和类型，顺序也是导出 CSV 时的列顺序
COLUMNS = {
    "match_id": "INTEGER PRIMARY KEY AUTOINCREMENT",  # 每局的编号，自动递增
    "started_at": "TEXT",  # 开局时间（等状态机做好才有）
    "ended_at": "TEXT",  # 识别到总结页的时间，如 2026-10-16 21:03:15
    "player_name": "TEXT",  # 游戏里显示的自己的名字，区分账号用（见 player_name.py）
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

# 账号对照表：一个名字一行，同一个账号的几个名字（改过名）account_id 相同。
# Stata 类比：一份 crosswalk（名字 → 账号编号），按名字 merge m:1 到每一局
NAME_TABLE = """CREATE TABLE IF NOT EXISTS player_names (
  name_key TEXT PRIMARY KEY,  -- 去掉空格后的名字（见 name_key）
  account_id INTEGER NOT NULL,  -- 属于哪个账号
  confirmed INTEGER NOT NULL DEFAULT 0  -- 1 = 已确认；0 = 新名字，等你在弹窗里回答"是不是同一个人"
)"""

# 旧数据库怎么升级：版本号 → 升到这个版本要执行的 SQL。只加列、加表，已有的数据不动
# （Stata 类比：给已有的 .dta 文件 generate 一个空的新变量）
MIGRATIONS = {
    2: ["ALTER TABLE matches ADD COLUMN player_name TEXT"],  # 2026-10-17 多账号：每局的玩家名
    3: [NAME_TABLE],  # 2026-10-17 多账号：账号对照表
}


def _version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _upgrade(conn):
    """新数据库建表；旧数据库按 MIGRATIONS 一步步升级到 SCHEMA_VERSION。
    先锁住数据库再看版本号：面板和记录程序同时打开一个旧数据库时，只会升级一次；中途出错就整个撤回。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = _version(conn)
        if version == 0:
            cols = ",\n  ".join(f"{name} {kind}" for name, kind in COLUMNS.items())
            conn.execute(f"CREATE TABLE IF NOT EXISTS matches (\n  {cols}\n)")
            conn.execute(NAME_TABLE)
        else:
            for v in range(version + 1, SCHEMA_VERSION + 1):
                for sql in MIGRATIONS[v]:
                    conn.execute(sql)
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def connect(db_path=DB_PATH):
    """打开数据库；第一次用时自动建表，旧版本的数据库自动升级。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 让查询结果可以按列名取值，比如 row["kills"]
    if _version(conn) < SCHEMA_VERSION:
        _upgrade(conn)
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


def _where(mode=None, only_review=False, legend=None, names=None):
    """把筛选条件拼成 SQL 的 WHERE 部分。值用 ? 占位，单独传入。
    names：只要这几个玩家名的局（一个账号用过的所有写法，见 accounts 的 raw）；None = 不按账号分。"""
    clauses, params = [], []
    if names is not None:
        clauses.append(f"player_name IN ({', '.join('?' for _ in names)})" if names else "0")
        params.extend(names)
    if mode:
        clauses.append("mode = ?")
        params.append(mode)
    if only_review:
        clauses.append("needs_review = 1")
    if legend:
        clauses.append("legend = ?")
        params.append(legend)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def query_matches(conn, mode=None, only_review=False, legend=None, limit=20, offset=0, names=None):
    """按条件查询，最新的在前。返回 (这一页的记录, 符合条件的总局数)。"""
    where, params = _where(mode, only_review, legend, names)
    rows = conn.execute(
        f"SELECT * FROM matches{where} ORDER BY ended_at DESC, match_id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM matches{where}", params).fetchone()[0]
    return rows, total


def all_matches(conn, names=None):
    """所有对局（names：只要这几个玩家名的），按时间从早到晚（总览页算统计、画走势图用）。"""
    where, params = _where(names=names)
    return conn.execute(f"SELECT * FROM matches{where} ORDER BY ended_at, match_id", params).fetchall()


def count_review(conn, names=None):
    where, params = _where(only_review=True, names=names)
    return conn.execute(f"SELECT COUNT(*) FROM matches{where}", params).fetchone()[0]


def distinct_values(conn, column, names=None):
    """某一列出现过的所有不同取值（比如所有用过的传奇），用于筛选下拉框。names：只看这几个玩家名的局。"""
    if column not in COLUMNS:
        raise ValueError(column)
    where, params = _where(names=names)
    rows = conn.execute(f"SELECT DISTINCT {column} FROM matches{where}{' AND' if where else ' WHERE'} "
                        f"{column} IS NOT NULL ORDER BY {column}", params)
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


# ---------- 账号：按玩家名区分，改过名的合并成一个账号 ----------

def name_key(name):
    """比较玩家名时用的写法：去掉空格。同一个名字，识别时有时读出空格、有时没有（如 "[DOGE] Dogga" 和 "[DOGE]Dogga"）。"""
    return "".join(name.split())


def name_similarity(a, b):
    """两个玩家名有多像（0 到 1，不管空格）。"""
    return SequenceMatcher(None, name_key(a), name_key(b)).ratio()


def sync_names(conn):
    """把对局里出现过、对照表里还没有的名字登记进去，每个新名字先单独算一个账号。
    还没有任何已确认的账号时，最早出现的那个名字直接确认（第一个账号不用问）；
    之后的新名字等你在弹窗里回答"是不是同一个人"。"""
    known = {r[0] for r in conn.execute("SELECT name_key FROM player_names")}
    new = []
    for r in conn.execute("SELECT player_name, MIN(ended_at) AS first FROM matches WHERE player_name IS NOT NULL "
                          "GROUP BY player_name ORDER BY first"):
        key = name_key(r["player_name"])
        if key and key not in known:
            known.add(key)
            new.append(key)
    if not new:
        return
    next_id = conn.execute("SELECT COALESCE(MAX(account_id), 0) + 1 FROM player_names").fetchone()[0]
    have_confirmed = conn.execute("SELECT COUNT(*) FROM player_names WHERE confirmed = 1").fetchone()[0] > 0
    for key in new:
        conn.execute("INSERT INTO player_names (name_key, account_id, confirmed) VALUES (?, ?, ?)",
                     (key, next_id, int(not have_confirmed)))
        next_id, have_confirmed = next_id + 1, True
    conn.commit()


def accounts(conn):
    """所有账号（会先 sync_names），按第一次出现的时间排。每个账号是一个字典：
    id、name（显示的名字 = 最近一局用的名字）、names（用过的名字，最近的在前）、games（局数）、last（最近一局的时间）、
    confirmed（False = 新名字，还没回答"是不是同一个人"）、
    name_stats（每个名字的 {name, games, last}，最近的在前）、raw（数据库里出现过的所有写法，按账号筛选对局时用）。
    还没认出玩家名的局不算在任何账号下。"""
    sync_names(conn)
    table = {r["name_key"]: (r["account_id"], bool(r["confirmed"])) for r in conn.execute("SELECT * FROM player_names")}
    found = {}
    for r in conn.execute("SELECT player_name, COUNT(*) AS games, MIN(ended_at) AS first, MAX(ended_at) AS last "
                          "FROM matches WHERE player_name IS NOT NULL GROUP BY player_name"):
        key = name_key(r["player_name"])
        if key not in table:
            continue
        account_id, confirmed = table[key]
        last = r["last"] or ""
        a = found.setdefault(account_id, {"id": account_id, "confirmed": confirmed, "per_name": {}, "raw": [],
                                          "games": 0, "first": r["first"] or "", "last": ""})
        a["games"] += r["games"]
        a["first"] = min(a["first"], r["first"] or "")
        a["last"] = max(a["last"], last)
        a["raw"].append(r["player_name"])
        one = a["per_name"].setdefault(key, {"name": r["player_name"], "games": 0, "last": ""})
        one["games"] += r["games"]
        if last >= one["last"]:  # 同一个名字有几种写法（空格不同）时，显示最近的写法
            one["name"], one["last"] = r["player_name"], last
    result = []
    for a in sorted(found.values(), key=lambda a: a["first"]):
        stats = sorted(a.pop("per_name").values(), key=lambda one: one["last"], reverse=True)
        result.append({**a, "name": stats[0]["name"], "names": [one["name"] for one in stats], "name_stats": stats})
    return result


def answer_name(conn, name, account_id=None):
    """确定一个名字属于哪个账号（弹窗里回答"是不是同一个人"、设置页里改）：
    account_id = 并到这个账号（同一个人改了名）；None = 单独一个账号（如果和别的名字在同一个账号里，就拆出来）。"""
    sync_names(conn)  # 名字刚出现、还没登记进对照表时，先登记，不然下面改不到
    key = name_key(name)
    if account_id is None:
        current = conn.execute("SELECT account_id FROM player_names WHERE name_key = ?", (key,)).fetchone()
        if current is None:
            return
        shared = conn.execute("SELECT COUNT(*) FROM player_names WHERE account_id = ?", (current[0],)).fetchone()[0]
        account_id = current[0]
        if shared > 1:
            account_id = conn.execute("SELECT MAX(account_id) + 1 FROM player_names").fetchone()[0]
    conn.execute("UPDATE player_names SET account_id = ?, confirmed = 1 WHERE name_key = ?", (account_id, key))
    conn.commit()


def write_csv(conn, f):
    """把全部数据按 CSV 格式写进一个已打开的文件（或内存里的文本缓冲）。
    按 COLUMNS 的顺序取列：升级过的旧数据库里，新加的列排在最后，SELECT * 的顺序会和列名对不上。"""
    rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM matches ORDER BY match_id").fetchall()
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
