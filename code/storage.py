"""本地数据库：把每局数据存进 SQLite（Python 自带，不用安装）。

数据库文件在数据文件夹的 data/evo.db（位置见 paths.py；在项目里时已被 .gitignore 排除，不会上传）。
用 Stata 类比：数据库 ≈ 硬盘上的一个 .dta 文件，matches 表的每一行 = 一局。
另有一张账号对照表 player_names（玩家名 → 账号），见下面"账号"一节；一张队友表 teammates（每局最多两行）；
一张各来源读数表 readings（同一项在几个画面上读到的数，每个来源一行）；一张大厅读数表 lobby_readings（每次回大厅读到的段位，总分以它为准）；一张武器表 weapon_holds（每一段拿在手上的武器）；
一张历史赛季表 season_stats（游戏里数据页上每个赛季的合计，录入历史赛季数据时读的，见下面"历史赛季"一节）。

查看最近的记录：python code/storage.py
导出成 CSV：    python code/storage.py --export data/matches.csv
"""

import argparse
import csv
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path

from paths import DB_PATH

APP_VERSION = "0.5.3"
SCHEMA_VERSION = 9  # 表结构变了就加 1，并在 MIGRATIONS 里写上怎么从旧版本升级

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
    "participation": "INTEGER",  # 老板分（参与分）：队友击杀、自己完全没参与的次数（对局中右上角第三个数，2026-10-18）
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
    "edited": "TEXT",  # 用户在战绩页改过哪几项（列名，逗号分隔，如 placement,damage；只记改了哪项，不记原来的值）
}

# 账号对照表：一个名字一行，同一个账号的几个名字（改过名）account_id 相同。
# Stata 类比：一份 crosswalk（名字 → 账号编号），按名字 merge m:1 到每一局
NAME_TABLE = """CREATE TABLE IF NOT EXISTS player_names (
  name_key TEXT PRIMARY KEY,  -- 去掉空格后的名字（见 name_key）
  account_id INTEGER NOT NULL,  -- 属于哪个账号
  confirmed INTEGER NOT NULL DEFAULT 0  -- 1 = 已确认；0 = 新名字，等你在弹窗里回答"是不是同一个人"
)"""

# 队友表：一局最多两行。名字和传奇以对局中左下角为准（见 in_match.py），ID 和这一局的数据来自总结页左右两张卡片
# （见 teammates.py）。读不准的项为空。
# Stata 类比：一份长格式数据（每局 × 每个队友一行），按 match_id merge m:1 到每一局
MATE_COLUMNS = ("name", "player_id", "legend", "kills", "assists", "knockdowns", "damage", "survival_time", "revives",
                "respawns")
MATE_TABLE = """CREATE TABLE IF NOT EXISTS teammates (
  match_id INTEGER NOT NULL,  -- 哪一局（matches.match_id）
  slot TEXT NOT NULL,  -- left / right：总结页上左边还是右边的卡片；没有总结页的局是 hud1 / hud2（对局中左下角上、下两行）
  name TEXT,  -- 队友的游戏名
  player_id TEXT,  -- 卡片下面的 ID（数字串，开头的 0 要留着）
  legend TEXT,  -- 用的传奇（中文名，对局中左下角认的）
  kills INTEGER,
  assists INTEGER,
  knockdowns INTEGER,
  damage INTEGER,
  survival_time INTEGER,  -- 秒
  revives INTEGER,
  respawns INTEGER,
  PRIMARY KEY (match_id, slot)
)"""

# 各来源的读数：同一项（如名次）在几个画面上都能读到，每个来源各记一行，不只记最后采用的那个（2026-10-18 用户决定）。
# 用来互相核对（对不上就标待确认，见 post_match.py），以后也能统计每个来源有多准。
# Stata 类比：长格式数据（每局 × 每项 × 每个来源一行）；reshape wide 以后每局一行，各来源读到的名次并排放着
READING_TABLE = """CREATE TABLE IF NOT EXISTS readings (
  match_id INTEGER NOT NULL,  -- 哪一局（matches.match_id）
  field TEXT NOT NULL,  -- 哪一项，和 matches 的列名一样（placement、kills、rp_delta……）
  source TEXT NOT NULL,  -- 从哪个画面读的：总结页、经验值页、淘汰画面
  value,  -- 读到的值（数字或文字，所以不写类型）；没读到为空
  score REAL,  -- 识别的置信度（0–1）；没有为空
  read_at TEXT,  -- 读的时间
  PRIMARY KEY (match_id, field, source)
)"""

# 大厅读数：每次回大厅读到的段位和段内分数（连续两次一样才记），总分以它为准（2026-10-18 用户决定，见 lobby.py）。
# 前后两次之间各局的 RP 加起来应该等于两次总分之差，用来校准（见 calibration.py）
LOBBY_TABLE = """CREATE TABLE IF NOT EXISTS lobby_readings (
  reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
  read_at TEXT NOT NULL,  -- 读到的时间
  player_name TEXT,  -- 左上角组队列表第一行的名字（读不到时用这次记录认出的名字）
  rank_tier TEXT,  -- 段位，如 钻石
  rank_division TEXT,  -- 段，如 II（大师、猎杀者没有）
  rank_progress INTEGER,  -- 段内的排位点数，如 868
  score INTEGER  -- 换算成的总分（见 ranks.score），如 14868
)"""

# 武器：一局里每一段拿在手上的武器（对局中右下角的图标认的，见 weapons.py），一段一行（2026-10-24 用户决定）。
# 时间从这一局第一次拿到武器算起（秒）。数据页"最常用武器"只算第一次拿到武器多少秒以后的（config/weapons.json 的
# count_after_seconds）：每一段都存着，这个规则以后改了，旧的局也能直接重算。
# Stata 类比：长格式数据（每局 × 每一段一行），按 match_id merge m:1 到每一局
WEAPON_TABLE = """CREATE TABLE IF NOT EXISTS weapon_holds (
  match_id INTEGER NOT NULL,  -- 哪一局（matches.match_id）
  weapon TEXT NOT NULL,  -- 武器编号（config/weapons.json，如 r301、vantage_sniper）
  start_s REAL NOT NULL,  -- 这一段从第几秒开始拿在手上（从这一局第一次拿到武器算起）
  end_s REAL NOT NULL,  -- 到第几秒
  PRIMARY KEY (match_id, start_s)
)"""

# 历史赛季：游戏里数据页上每个赛季的合计（2026-11-01 用户要的"录入历史赛季数据"，见 season_stats.py、live_collect.SeasonWatcher）。
# 每个账号 × 每个赛季 × 全部 / 排位一行，生涯合计另一行（season 为空）。和每局的数据分开，不混在一起。
# 记录程序读到的先存成"待核对"（status = pending），玩家在"赛季"页面核对、改好点保存才是 saved；同一项最多一行待核对、
# 一行已保存（再录一次当前赛季，数字变了：新的待核对，保存时换掉旧的）。
# Stata 类比：一份面板数据（账号 × 赛季），和 matches 那份每局一行的数据分开存
SEASON_FIELDS = ("games", "wins", "top5", "damage", "damage_max", "damage_avg", "kills", "deaths", "kd",
                 "kills_max", "knockdowns", "assists", "win_streak", "revives", "respawns")
SEASON_TABLE = """CREATE TABLE IF NOT EXISTS season_stats (
  stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
  player_name TEXT NOT NULL,  -- 数据页上的玩家名（按名字区分账号，同 matches.player_name）
  season INTEGER,  -- 第几赛季；生涯合计为空
  kind TEXT NOT NULL,  -- all = 这个赛季全部大逃杀（排位 + 匹配）；ranked = 只算排位；career = 大逃杀生涯合计
  season_name TEXT,  -- 游戏里的赛季名，如 诸神烙印（ranked 写"排位"，career 写"大逃杀"）
  games INTEGER,  -- 比赛（局数；游戏写"1.03K"时用伤害 ÷ 平均伤害算出的准确数）
  wins INTEGER,
  top5 INTEGER,  -- 前 5 名的次数
  damage INTEGER,
  damage_max INTEGER,  -- 一局最高伤害
  damage_avg REAL,  -- 平均伤害（游戏里写的，两位小数）
  kills INTEGER,
  deaths INTEGER,  -- 阵亡
  kd REAL,  -- 击杀/阵亡比（游戏里写的）
  kills_max INTEGER,  -- 一局最高击杀
  knockdowns INTEGER,
  assists INTEGER,
  win_streak INTEGER,  -- 最长连胜
  revives INTEGER,  -- 急救队友次数
  respawns INTEGER,  -- 重生队友次数
  approx TEXT,  -- 游戏只写了约数的项（逗号分隔，如 top5：生涯的前 5 名只写"2.82K"）
  read_at TEXT NOT NULL,  -- 读的时间
  status TEXT NOT NULL,  -- pending = 待核对；saved = 已保存
  review_note TEXT,  -- 读的时候对不上的地方（如"伤害 ÷ 比赛 ≠ 平均伤害"），一句一行
  edited TEXT  -- 核对时改过哪几项（列名，逗号分隔）
)"""

# 旧数据库怎么升级：版本号 → 升到这个版本要做的事（SQL，或者 ("加列", 表, 列, 类型)）。只加列、加表，已有的数据不动
# （Stata 类比：给已有的 .dta 文件 generate 一个空的新变量）。加列时已经有这一列就跳过
MIGRATIONS = {
    2: [("加列", "matches", "player_name", "TEXT")],  # 2026-10-17 多账号：每局的玩家名
    3: [NAME_TABLE],  # 2026-10-17 多账号：账号对照表
    4: [MATE_TABLE],  # 2026-10-17 队友：总结页上两名队友的数据
    5: [READING_TABLE],  # 2026-10-18 各来源的读数（淘汰画面、总结页、经验值页读到的名次等）
    6: [("加列", "matches", "participation", "INTEGER"),  # 2026-10-18 0.3.0：老板分
        ("加列", "teammates", "legend", "TEXT"),  # 队友的传奇
        LOBBY_TABLE],  # 大厅读数
    7: [("加列", "matches", "edited", "TEXT")],  # 2026-10-22 0.3.7：用户改过哪几项（匿名统计里的"识别情况"用）
    8: [WEAPON_TABLE],  # 2026-10-24 武器：每一段拿在手上的武器（数据页"最常用武器"）
    9: [SEASON_TABLE],  # 2026-11-01 历史赛季：游戏里数据页上每个赛季的合计
}


def _version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _migrate(conn, step):
    if isinstance(step, str):
        conn.execute(step)
        return
    _, table, column, kind = step
    if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


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
            conn.execute(MATE_TABLE)
            conn.execute(READING_TABLE)
            conn.execute(LOBBY_TABLE)
            conn.execute(WEAPON_TABLE)
            conn.execute(SEASON_TABLE)
        else:
            for v in range(version + 1, SCHEMA_VERSION + 1):
                for step in MIGRATIONS[v]:
                    _migrate(conn, step)
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


def _where(mode=None, only_review=False, legend=None, names=None, between=None):
    """把筛选条件拼成 SQL 的 WHERE 部分。值用 ? 占位，单独传入。
    names：只要这几个玩家名的局（一个账号用过的所有写法，见 accounts 的 raw）；None = 不按账号分。
    between：(开始, 结束) 只要这段时间里结束的局（结束为 None = 到现在；战绩页按赛季筛选，见 seasons.bounds）。"""
    clauses, params = [], []
    if between is not None:
        clauses.append("ended_at >= ?")
        params.append(between[0])
        if between[1] is not None:
            clauses.append("ended_at < ?")
            params.append(between[1])
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


def query_matches(conn, mode=None, only_review=False, legend=None, limit=20, offset=0, names=None, between=None):
    """按条件查询，最新的在前。返回 (这一页的记录, 符合条件的总局数)。between：见 _where。"""
    where, params = _where(mode, only_review, legend, names, between)
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


def all_teammates(conn, names=None):
    """所有对局的队友（names：只要这几个玩家名的局），按对局时间从早到晚（数据页"常一起玩的队友"用）。"""
    where, params = _where(names=names)
    return conn.execute(f"SELECT teammates.* FROM teammates JOIN matches USING (match_id){where} "
                        "ORDER BY ended_at, match_id", params).fetchall()


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
    conn.execute("DELETE FROM teammates WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM readings WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM weapon_holds WHERE match_id = ?", (match_id,))
    conn.commit()


def save_teammates(conn, match_id, mates):
    """存一局的队友（覆盖这一局原来的）。mates：teammates.read_teammates 的结果，每人一个字典，键是 slot 和 MATE_COLUMNS。"""
    conn.execute("DELETE FROM teammates WHERE match_id = ?", (match_id,))
    for mate in mates:
        conn.execute(f"INSERT INTO teammates (match_id, slot, {', '.join(MATE_COLUMNS)}) "
                     f"VALUES (?, ?, {', '.join('?' for _ in MATE_COLUMNS)})",
                     [match_id, mate["slot"], *(mate.get(k) for k in MATE_COLUMNS)])
    conn.commit()


def teammates_of(conn, match_id):
    """一局的队友：没有总结页的局是 hud1、hud2（对局中左下角上、下两行），有总结页的局左边的在前。
    没有记录队友（加队友功能以前的局、对局中和总结页都没读到的局）返回空列表。"""
    return conn.execute("SELECT * FROM teammates WHERE match_id = ? ORDER BY slot", (match_id,)).fetchall()


def update_teammate(conn, match_id, slot, fields):
    """修改一名队友的若干项（如对局中认出的传奇、名字）。fields 的键必须是 MATE_COLUMNS 里的。"""
    data = {k: v for k, v in fields.items() if k in MATE_COLUMNS}
    if not data:
        return
    sets = ", ".join(f"{k} = ?" for k in data)
    conn.execute(f"UPDATE teammates SET {sets} WHERE match_id = ? AND slot = ?", [*data.values(), match_id, slot])
    conn.commit()


def save_weapon_holds(conn, match_id, holds):
    """存一局拿过的武器（覆盖这一局原来的）。holds：[(武器编号, 开始秒, 结束秒)]（见 weapons.WeaponTimer.result）。"""
    conn.execute("DELETE FROM weapon_holds WHERE match_id = ?", (match_id,))
    conn.executemany("INSERT OR REPLACE INTO weapon_holds (match_id, weapon, start_s, end_s) VALUES (?, ?, ?, ?)",
                     [(match_id, weapon, start, end) for weapon, start, end in holds])
    conn.commit()


def weapon_holds_of(conn, match_id):
    """一局拿过的武器（每一段一行，按时间先后）。没记下武器（0.4.0 以前的局、一把都没认出）返回空列表。"""
    return conn.execute("SELECT * FROM weapon_holds WHERE match_id = ? ORDER BY start_s", (match_id,)).fetchall()


def all_weapon_holds(conn, names=None):
    """所有对局拿过的武器（names：只要这几个玩家名的局），按对局、时间先后（数据页"最常用武器"用）。"""
    where, params = _where(names=names)
    return conn.execute(f"SELECT weapon_holds.* FROM weapon_holds JOIN matches USING (match_id){where} "
                        "ORDER BY ended_at, match_id, start_s", params).fetchall()


def counted_weapon_seconds(holds, after):
    """每局每把武器算进"最常用武器"的秒数：只算这一局第一次拿到武器 after 秒以后的（config/weapons.json 的
    count_after_seconds）。数据页"最常用武器"、匿名统计、"全体玩家"都按这个算。
    holds：weapon_holds 表的行。返回 {局号: {武器编号: 秒数}}，一秒都没算进去的武器不列。"""
    out = {}
    for h in holds:
        counted = h["end_s"] - max(h["start_s"], after)
        if counted > 0:
            weapons = out.setdefault(h["match_id"], {})
            weapons[h["weapon"]] = weapons.get(h["weapon"], 0) + counted
    return out


def weapon_totals(holds, after, match_ids):
    """这几局（match_ids）合在一起：每把武器算进去的秒数 {武器编号: 秒数}，和拿过它的局 {武器编号: {局号}}。
    规则同 counted_weapon_seconds（数据页"最常用武器"、"全体玩家"里的"你"用）。"""
    seconds, played = {}, {}
    for match_id, weapons in counted_weapon_seconds(holds, after).items():
        if match_id in match_ids:
            for weapon, counted in weapons.items():
                seconds[weapon] = seconds.get(weapon, 0) + counted
                played.setdefault(weapon, set()).add(match_id)
    return seconds, played


# ---------- 大厅读数（见 lobby.py、calibration.py） ----------

def save_lobby(conn, reading):
    """记下一次大厅读数。reading：read_at、player_name、rank_tier、rank_division、rank_progress、score。返回编号。"""
    keys = ("read_at", "player_name", "rank_tier", "rank_division", "rank_progress", "score")
    cur = conn.execute(f"INSERT INTO lobby_readings ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
                       [reading.get(k) for k in keys])
    conn.commit()
    return cur.lastrowid


def lobby_readings(conn, names=None):
    """大厅读数，按时间从早到晚。names：只要这几个玩家名的（一个账号用过的所有写法）；None = 全部。"""
    rows = conn.execute("SELECT * FROM lobby_readings ORDER BY read_at, reading_id").fetchall()
    if names is None:
        return rows
    keys = {name_key(n) for n in names}
    return [r for r in rows if r["player_name"] and name_key(r["player_name"]) in keys]


def save_readings(conn, match_id, source, values, read_at, scores=None):
    """记下一个来源（如"淘汰画面"）对这一局读到的各项，同一来源同一项原来有的就覆盖。
    values：{项: 读到的值}，没读到的值为 None（"没读到"本身也记下）；scores：{项: 置信度}，没有的项不写。"""
    scores = scores or {}
    conn.executemany("INSERT OR REPLACE INTO readings (match_id, field, source, value, score, read_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     [(match_id, field, source, value, scores.get(field), read_at) for field, value in values.items()])
    conn.commit()


def readings_of(conn, match_id, field=None):
    """一局各来源的读数（field：只要这一项，如 "placement"）。"""
    sql, params = "SELECT * FROM readings WHERE match_id = ?", [match_id]
    if field is not None:
        sql, params = sql + " AND field = ?", params + [field]
    return conn.execute(sql + " ORDER BY field, source", params).fetchall()


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
    conn.execute("DELETE FROM teammates")
    conn.execute("DELETE FROM readings")
    conn.execute("DELETE FROM weapon_holds")
    conn.execute("DELETE FROM lobby_readings")  # 段位卡片、走势图也要跟着清空
    conn.commit()
    return n


# ---------- 历史赛季（游戏里数据页上每个赛季的合计） ----------

def _season_key(row):
    """同一项：同一个账号名（不管空格）、同一个赛季、同一种（全部 / 排位 / 生涯）。"""
    return name_key(row["player_name"]), row["season"], row["kind"]


def save_season_pending(conn, row):
    """记录程序读到一项：存成待核对（这一项已经有待核对的就换掉）。row 的键：player_name、season、kind、season_name、
    SEASON_FIELDS 里的项、approx（项的列表）、read_at、review_note。返回 stat_id。"""
    key = _season_key(row)
    for old in conn.execute("SELECT stat_id, player_name, season, kind FROM season_stats WHERE status = 'pending'"):
        if _season_key(old) == key:
            conn.execute("DELETE FROM season_stats WHERE stat_id = ?", (old["stat_id"],))
    data = {k: row.get(k) for k in ("player_name", "season", "kind", "season_name", "read_at", "review_note",
                                     *SEASON_FIELDS)}
    data["approx"] = ",".join(row.get("approx") or ()) or None
    data["status"] = "pending"
    cur = conn.execute(f"INSERT INTO season_stats ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})",
                       list(data.values()))
    conn.commit()
    return cur.lastrowid


def season_rows(conn, status=None):
    """历史赛季的行（status 为 pending / saved 时只要那一种），按账号、赛季从新到旧、全部在排位前面、生涯在最后。"""
    rows = conn.execute("SELECT * FROM season_stats" + (" WHERE status = ?" if status else ""),
                        (status,) if status else ()).fetchall()
    order = {"all": 0, "ranked": 1, "career": 2}
    return sorted(rows, key=lambda r: (name_key(r["player_name"]), r["season"] is None, -(r["season"] or 0),
                                       order.get(r["kind"], 3)))


def save_season(conn, stat_id, fields=None):
    """核对好了：这一项改成已保存（fields 是改过的项 {列名: 值}，记进 edited），同一项原来保存的那一行删掉。"""
    row = conn.execute("SELECT * FROM season_stats WHERE stat_id = ?", (stat_id,)).fetchone()
    if row is None:
        return False
    changed = {k: v for k, v in (fields or {}).items() if k in SEASON_FIELDS and v != row[k]}
    for old in conn.execute("SELECT stat_id, player_name, season, kind FROM season_stats WHERE status = 'saved'"):
        if _season_key(old) == _season_key(row) and old["stat_id"] != stat_id:
            conn.execute("DELETE FROM season_stats WHERE stat_id = ?", (old["stat_id"],))
    edited = sorted(set(filter(None, (row["edited"] or "").split(","))) | set(changed))
    sets = {**changed, "status": "saved", "edited": ",".join(edited) or None}
    conn.execute(f"UPDATE season_stats SET {', '.join(f'{k} = ?' for k in sets)} WHERE stat_id = ?",
                 [*sets.values(), stat_id])
    conn.commit()
    return True


def delete_season(conn, stat_id):
    conn.execute("DELETE FROM season_stats WHERE stat_id = ?", (stat_id,))
    conn.commit()


# ---------- 账号：按玩家名区分，改过名的合并成一个账号 ----------

def name_key(name):
    """比较玩家名时用的写法：去掉空格。同一个名字，识别时有时读出空格、有时没有（如 "[DOGE] Dogga" 和 "[DOGE]Dogga"）。"""
    return "".join(name.split())


def name_similarity(a, b):
    """两个玩家名有多像（0 到 1，不管空格）。"""
    return SequenceMatcher(None, name_key(a), name_key(b)).ratio()


def truncated_of(a, b):
    """一个名字是另一个的开头（不管空格，短的至少 2 个字）：其中一个被截短了，是同一个人。返回长的那个，不是返回 None。
    截短的来历（2026-11-04 测试员的截图）：对局中队友名字刚出现时是逐字出来的动画，只读到前半截；
    总结页卡片、名字太长时游戏自己截短（"Asuna[dkaldad"）。"""
    ka, kb = name_key(a), name_key(b)
    if ka == kb:
        return None
    short, full = (a, b) if len(ka) < len(kb) else (b, a)
    return full if len(name_key(short)) >= 2 and name_key(full).startswith(name_key(short)) else None


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


def known_names(conn):
    """Evo 认识的、自己的玩家名（去掉空格的写法，见 name_key）：对局里出现过的所有名字。录入历史赛季数据时
    用它分出数据页是不是自己的（好友资料页也是这一页）。"""
    sync_names(conn)
    return {r[0] for r in conn.execute("SELECT name_key FROM player_names")}


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


def delete_account(conn, account_id):
    """删除一个账号的所有记录（设置页"账号"一栏的"删除"，比如不想要的小号，2026-11-05 用户要的）：这个账号所有名字的局
    （连同队友、各来源读数、武器）、回大厅读到的段位、历史赛季的数据，再把这些名字从对照表里去掉：以后在这个号上再打，
    当成新名字，弹窗问是不是同一个人。名字不管空格（见 name_key）；还没认出玩家名的局不动。截图由调用的地方删。
    返回 (删掉的局（行，删截图用）, {"matches": 局数, "lobby": 大厅读数, "seasons": 历史赛季的行})。"""
    sync_names(conn)  # 名字刚出现、还没登记进对照表时，先登记，不然它的局删不到
    keys = {r[0] for r in conn.execute("SELECT name_key FROM player_names WHERE account_id = ?", (account_id,))}

    def mine(table, id_column):
        return [r for r in conn.execute(f"SELECT * FROM {table} WHERE player_name IS NOT NULL ORDER BY {id_column}")
                if name_key(r["player_name"]) in keys]

    matches, lobby, seasons = mine("matches", "match_id"), mine("lobby_readings", "reading_id"), mine("season_stats", "stat_id")
    match_ids = [(r["match_id"],) for r in matches]
    for table in ("matches", "teammates", "readings", "weapon_holds"):
        conn.executemany(f"DELETE FROM {table} WHERE match_id = ?", match_ids)
    conn.executemany("DELETE FROM lobby_readings WHERE reading_id = ?", [(r["reading_id"],) for r in lobby])
    conn.executemany("DELETE FROM season_stats WHERE stat_id = ?", [(r["stat_id"],) for r in seasons])
    conn.execute("DELETE FROM player_names WHERE account_id = ?", (account_id,))
    conn.commit()
    return matches, {"matches": len(matches), "lobby": len(lobby), "seasons": len(seasons)}


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
