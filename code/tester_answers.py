"""测试工具打完以后对答案（2026-11-02 用户定的，见 todo.md"测试工具"）：只读 Evo 的数据库和日志，看这段时间 Evo 记下了什么。

- 打一局：Evo 记下的每局（名次、击杀 / 助攻 / 击倒、伤害、RP 变化）给测试员确认；确认对了的局，从离开大厅到读完
  经验值页的截图不带（prune 删掉）。测试员说没有漏记的局、大厅的段位和分数也对时，对局之间的大厅每 10 秒留一张；
  记错的、没确认的局，截图全留。测试员勾了"全部带上"就一张不删
- 每局的时间段：Evo 的数据库里没有开局时间（started_at 一直是空的），所以用 Evo 日志里的"进入大厅""进入开局画面"
  "对局中"和这一局最后一次读数（readings.read_at）分：从开局前最后一次在大厅，到最后一次读数再过 END_MARGIN 秒
- 赛季数据页：这段时间 Evo 读到的赛季数据（season_stats，待核对的和测试员核对后保存的）
- 录的时候的提醒（Session.watch 用）：Evo 标了待确认的局、这段时间记下了几局

只读：数据库用 SQLite 的只读方式打开（Evo 开着也不影响它写）；不 import storage.py（它会 import paths.py）。
"""

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

FIELDS = (("placement", "名次"), ("kills", "击杀"), ("assists", "助攻"), ("knockdowns", "击倒"), ("damage", "伤害"),
          ("rp_delta", "RP 变化"))  # 给测试员确认的几项（2026-11-02 用户定的）
INFO = ("match_id", "ended_at", "mode", "map", "legend", "needs_review", "review_note", "rp_source", "edited",
        "app_version", "survival_time", "rank_tier", "rank_division", "rank_progress")
SEASON_COLUMNS = ("stat_id", "season", "kind", "season_name", "games", "wins", "top5", "damage", "damage_max",
                  "damage_avg", "kills", "deaths", "kd", "kills_max", "knockdowns", "assists", "win_streak", "revives",
                  "respawns", "approx", "read_at", "status", "review_note", "edited")
END_MARGIN = 20  # 这一局最后一次读数以后再算这么多秒（经验值页的动画、回大厅）
NO_LOG_SECONDS = 180  # 日志里找不到这一局开局时：从最后一次读数往前算这么多秒，再加上生存时间
BEFORE_MATCH_SECONDS = 60  # 找到开局、找不到开局前的大厅时：开局前这么多秒算这一局
LOBBY_EVERY = 10  # 对局之间的大厅每隔这么多秒留一张
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_EVENT = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[\d\d:\d\d:\d\d\] (进入大厅|大厅：|进入开局画面|对局中：)")
FRAME_TIME = re.compile(r"^(?:\d+_)?(\d{2})(\d{2})(\d{2})_")  # frames/0001_213005_…、full/213005_…


def parse_time(text):
    try:
        return datetime.strptime(str(text)[:19], TIME_FORMAT)
    except (TypeError, ValueError):
        return None


def db_path(evo_dirs):
    """Evo 的数据库（数据文件夹里的 data\\evo.db）；没有返回 None。"""
    for folder, _how in evo_dirs:
        path = Path(folder) / "data" / "evo.db"
        if path.is_file():
            return path
    return None


def _connect(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def evo_matches(path, start, end):
    """[start, end] 里 Evo 记下的局（这一局最后一次读数或 ended_at 在里面），按时间从早到晚。
    每局：INFO 里的几项、FIELDS 的值、last_read（最后一次读数的时间，datetime）、readings（{来源: {项: 值}}）。"""
    if path is None:
        return []
    out = []
    with _connect(path) as conn:
        has_readings = "readings" in _tables(conn)
        for row in conn.execute("SELECT * FROM matches ORDER BY match_id"):
            match = dict(row)
            readings, times = {}, [parse_time(match.get("ended_at"))]
            if has_readings:
                for r in conn.execute("SELECT source, field, value, read_at FROM readings WHERE match_id = ?",
                                      (match["match_id"],)):
                    readings.setdefault(r["source"], {})[r["field"]] = r["value"]
                    times.append(parse_time(r["read_at"]))
            times = [t for t in times if t is not None]
            if not times:
                continue
            last = max(times)
            if not start <= last <= end:
                continue
            item = {key: match.get(key) for key in INFO}
            item.update({key: match.get(key) for key, _ in FIELDS})
            item.update(last_read=last, readings=readings)
            out.append(item)
    return sorted(out, key=lambda m: m["last_read"])


def lobby_readings(path, start, end):
    """[start, end] 里 Evo 在大厅读到的段位和分数，按时间从早到晚：[{read_at, rank_tier, rank_division, rank_progress, score}]。"""
    if path is None:
        return []
    with _connect(path) as conn:
        if "lobby_readings" not in _tables(conn):
            return []
        rows = conn.execute("SELECT read_at, rank_tier, rank_division, rank_progress, score FROM lobby_readings "
                            "ORDER BY read_at, reading_id").fetchall()
    return [dict(r) for r in rows if (t := parse_time(r["read_at"])) is not None and start <= t <= end]


def rank_text(row):
    """大厅读数 → "钻石 I 600"（大师、猎杀者没有段）。"""
    parts = [row.get("rank_tier"), row.get("rank_division"), row.get("rank_progress")]
    return " ".join(str(p) for p in parts if p not in (None, ""))


def season_rows(path, start, end):
    """[start, end] 里 Evo 读到的赛季数据（待核对的、核对后保存的），按读到的时间。"""
    if path is None:
        return []
    with _connect(path) as conn:
        if "season_stats" not in _tables(conn):
            return []
        rows = conn.execute("SELECT * FROM season_stats ORDER BY read_at, stat_id").fetchall()
    return [{key: dict(r).get(key) for key in SEASON_COLUMNS} for r in rows
            if (t := parse_time(r["read_at"])) is not None and start <= t <= end]


def log_events(log_dir, start, end):
    """Evo 日志里 [start, end] 的几种事件：[(时间, "lobby" / "match")]，按时间。evo.log 和轮换下来的 evo.log.1 等都看。"""
    events = []
    for path in sorted(Path(log_dir).glob("evo.log*")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = LOG_EVENT.match(line)
            if m and start <= (t := parse_time(m.group(1))) <= end:
                events.append((t, "lobby" if "大厅" in m.group(2) else "match"))
    return sorted(set(events))


def match_windows(matches, events, session_start):
    """每局的截图从几点到几点：[(开始, 结束)]，和 matches 一一对应、不重叠。
    结束 = 最后一次读数 + END_MARGIN；开始 = 上一局结束以后、这一局开局（日志里第一次"进入开局画面""对局中"）前
    最后一次在大厅；日志里找不到开局，就从结束往前算 NO_LOG_SECONDS + 生存时间。"""
    windows = []
    prev_end = session_start
    for match in matches:
        end = max(match["last_read"], parse_time(match.get("ended_at")) or match["last_read"]) + timedelta(
            seconds=END_MARGIN)
        inside = [(t, kind) for t, kind in events if prev_end <= t <= end]
        began = next((t for t, kind in inside if kind == "match"), None)
        if began is not None:
            lobby = [t for t, kind in inside if kind == "lobby" and t <= began]
            start = lobby[-1] if lobby else began - timedelta(seconds=BEFORE_MATCH_SECONDS)
        else:
            start = end - timedelta(seconds=NO_LOG_SECONDS + (match.get("survival_time") or 0))
        start = max(start, prev_end)
        windows.append((start, end))
        prev_end = end
    return windows


def frame_files(folder, session_start):
    """frames/ 和 full/ 里的截图：[(时间, 路径)]，按时间。文件名里只有时分秒：比开始早（过了半夜）就算第二天。"""
    out = []
    for sub in ("frames", "full"):
        for path in sorted((Path(folder) / sub).glob("*.jpg")):
            m = FRAME_TIME.match(path.name)
            if not m:
                continue
            t = datetime.combine(session_start.date(), datetime.min.time()).replace(
                hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3)))
            if t < session_start - timedelta(minutes=1):
                t += timedelta(days=1)
            out.append((t, path))
    return sorted(out)


def prune(folder, session_start, windows, thin_lobby):
    """删截图。windows：[(开始, 结束, 测试员确认对了没有)]；确认对了的局里的全删，别的局全留；
    不在任何一局里的（对局之间的大厅等）：thin_lobby 时每 LOBBY_EVERY 秒留一张，否则全留。
    返回 {"确认对的局里删了": n, "大厅删了": n, "留下": n}。"""
    counts = {"确认对的局里删了": 0, "大厅删了": 0, "留下": 0}
    last_kept = {}  # 每个文件夹：大厅上一张留下的时间
    for t, path in frame_files(folder, session_start):
        window = next((w for w in windows if w[0] <= t <= w[1]), None)
        if window is not None and window[2]:
            path.unlink()
            counts["确认对的局里删了"] += 1
            continue
        if window is None and thin_lobby:
            kept = last_kept.get(path.parent.name)
            if kept is not None and (t - kept).total_seconds() < LOBBY_EVERY:
                path.unlink()
                counts["大厅删了"] += 1
                continue
            last_kept[path.parent.name] = t
        counts["留下"] += 1
    return counts


def apply_match_answers(folder, session_start, matches, events, answers):
    """测试员确认完：算每局的时间段、删截图，返回要写进 answers.json 的内容。
    answers：{"局": {match_id: {"判断": "对" / "不对" / None, "正确的": {项: 值}}}, "漏记": "没有" / "有" / None,
    "大厅": {"Evo 读的", "判断", "正确的"} 或 None, "全部带上": bool}。大厅的"判断"是"对" / "不对" / None，
    或者"还没读到最后一局之后的大厅"（Evo 最后一次读大厅在最后一局之前，不让测试员判断；当作没确认，大厅的截图照样留）。"""
    windows = match_windows(matches, events, session_start)
    judged = answers.get("局", {})
    rows, plan = [], []
    for match, (start, end) in zip(matches, windows):
        mine = judged.get(match["match_id"], {})
        ok = mine.get("判断") == "对"
        plan.append((start, end, ok))
        rows.append({**{k: v for k, v in match.items() if k != "last_read"},
                     "最后一次读数": f"{match['last_read']:{TIME_FORMAT}}",
                     "时间段": [f"{start:{TIME_FORMAT}}", f"{end:{TIME_FORMAT}}"],
                     "测试员": mine.get("判断"), "正确的": mine.get("正确的") or {}})
    lobby = answers.get("大厅")
    thin = answers.get("漏记") == "没有" and (lobby is None or lobby.get("判断") == "对") and bool(matches)
    if answers.get("全部带上"):
        pruned = {"全部带上": True}
    else:
        pruned = prune(folder, session_start, plan, thin)
    return {"局": rows, "漏记": answers.get("漏记"), "大厅": lobby, "全部带上": bool(answers.get("全部带上")),
            "截图": pruned}
