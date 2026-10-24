"""匿名统计（0.3.7 起，2026-10-22 用户决定）：用户同意了，才把对局数据发给开发者，用来统计有多少人在用、
大家用哪些传奇，以及改进识别。默认不发。

- 第一次打开时页面顶部问一次（在"自动检查新版本""开机自动启动"之后，一次只问一件事）；设置页"匿名统计"里
  可以随时关掉。没回答、点了"不了"、关掉以后，一个请求都不发（0.3.7、0.3.8 设置页还能看发送内容的原样、
  删除已发送的数据，2026-10-24 用户决定去掉）
- 只发同意以后结束的局（同意的时间记在 telemetry_since；关掉再打开，从重新打开时算起）
- 发给 https://stats.evotracker.cn（Cloudflare Worker + D1 数据库，代码在 server/stats/）。托盘程序启动 1 分钟后
  发一次，之后每小时一次（evo_app.start_telemetry；没有新的局、今天的日活也发过了就不联网）；在设置页同意时马上发一次。连不上就等下一次，不提示
- 日活：每天第一次发送时带上一份"今天在用"（ping），一天只算一次。那天没发出去（连不上）就记着，下次连上时补发
  （2026-10-24 用户决定；最多补 MAX_PING_DAYS 天，补发的日活带的画面设置是补发时的）
- 每局结束 SETTLE_MINUTES 分钟以后才发（大厅校准、用户修正多半已经做完）。发过的局记下摘要（data/telemetry_sent.json），
  之后这一局又变了（大厅校准、用户在战绩页改了）就再发一次，服务器上覆盖原来的那一行

发送的内容只有这些（ping_payload、match_payload；python code/telemetry.py 打印原样）：
- 每次：匿名编号、Evo 版本号
- 日活：日期、屏幕分辨率、Apex 视频设置里的分辨率和显示模式（全屏 / 无边框 / 窗口）、设置页选的游戏画面比例
- 每局：对局编号（由匿名编号和本机的局号算出来的一串乱码，只用来覆盖同一局）、日期、记这一局的 Evo 版本号、
  模式、地图、自己的传奇、两名队友的传奇、名次、击杀/助攻/击倒、伤害、RP 变化、段位（只有大段，如"钻石"）、
  RP 从哪个画面来、是否待确认、用户在战绩页改过哪几项（不发改成了什么）；0.3.8 之后的版本还有两名队友这一局的
  击杀/助攻/击倒、伤害（mate_stats，和 mates 一一对应；总结页上读的，没有总结页的局是空的。2026-10-24 用户决定：
  数据页"全体玩家"按全队三个人算，用过的人不用重新问）。没读出来的项是空的（null）
不发：游戏名、ID、队友的名字和 ID、截图、日志、备注（可能有用户自己写的字）、文件路径、电脑信息。
服务器不存 IP 地址（Cloudflare 本身能看到，见隐私政策）。

用到的设置（app_settings）：
- telemetry_consent：True 同意 / False 不同意；还没问过时没有这一项
- telemetry_since：同意的时间（只发这之后结束的局）
- telemetry_id：匿名编号（随机生成，和游戏账号、电脑都无关）
- telemetry_ping_day：上次发送日活的日期
- telemetry_ping_missed：用了 Evo、日活还没发出去的日期（从早到晚）
- telemetry_last：上次发送的时间和结果（只记下来，排查问题用）

测试时把 STATS_URL 换成本机的测试地址（或者设环境变量 EVO_STATS_URL）。
用法：python code/telemetry.py   → 打印会发送的内容（不发送）
"""

import ctypes
import hashlib
import json
import os
import re
import secrets
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import app_settings
import game_aspect
import storage
from paths import DB_PATH

STATS_URL = os.environ.get("EVO_STATS_URL", "https://stats.evotracker.cn").rstrip("/")
CONSENT_KEY, SINCE_KEY, ID_KEY = "telemetry_consent", "telemetry_since", "telemetry_id"
PING_KEY, LAST_KEY = "telemetry_ping_day", "telemetry_last"
MISSED_KEY = "telemetry_ping_missed"
MAX_PING_DAYS = 30  # 没发出去的日活最多补这么多天（更早的不补）
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
SENT_PATH = DB_PATH.parent / "telemetry_sent.json"  # 发过的局：{对局编号: 摘要}。和数据库放在一起，搬数据文件夹时一起搬
SETTLE_MINUTES = 60  # 一局结束这么久以后才发
BATCH = 100  # 一次请求最多带几局（服务器也只收这么多）
TIMEOUT = 15  # 秒：和检查新版本一样，国内有的网络到 Cloudflare 绕得远
MAX_REPLY = 64 * 1024
MATCH_FIELDS = ("placement", "kills", "assists", "knockdowns", "damage", "rp_delta")
MATE_FIELDS = ("kills", "assists", "knockdowns", "damage")  # 队友每局发的数字（没有名字和 ID）
_lock = threading.Lock()  # 托盘的定时发送和设置页的按钮可能同时来，一次只让一个发


class SendError(Exception):
    pass


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 同意 ----------

def consent():
    """True 同意 / False 不同意 / None 还没问过。"""
    value = app_settings.get(CONSENT_KEY)
    return value if isinstance(value, bool) else None


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def install_id():
    """匿名编号：第一次用到时随机生成，之后一直用这个。"""
    value = app_settings.get(ID_KEY)
    if not _valid_id(value):
        value = secrets.token_hex(16)
        app_settings.set_value(ID_KEY, value)
    return value


def set_consent(on):
    """页面顶部的提问、设置页的开关。打开时从现在算起：之前结束的局、之前没发出去的日活都不发。"""
    app_settings.set_value(CONSENT_KEY, bool(on))
    app_settings.set_value(MISSED_KEY, None)
    if on:
        install_id()
        app_settings.set_value(SINCE_KEY, now_text())


# ---------- 发送的内容 ----------

def screen_size():
    """主显示器的实际分辨率（不受 Windows 显示缩放影响，和设置页显示的一样）。读不到返回 None。"""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except (AttributeError, OSError):
        return None


def ping_payload(day):
    """日活：今天在用 Evo，加上和识别有关的画面设置。"""
    cfg = game_aspect.read_video_config() or {}
    w, h = cfg.get("setting.defaultres", ""), cfg.get("setting.defaultresheight", "")
    mode = None
    if "setting.fullscreen" in cfg:  # DX12 全屏时 fullscreen、nowindowborder 都是 1；无边框窗口只有 nowindowborder 是 1
        mode = ("fullscreen" if cfg["setting.fullscreen"] == "1"
                else "borderless" if cfg.get("setting.nowindowborder") == "1" else "windowed")
    size = screen_size()
    return {"day": day, "screen": f"{size[0]}x{size[1]}" if size else None,
            "game_res": f"{int(w)}x{int(h)}" if w.isdigit() and h.isdigit() else None,
            "game_mode": mode, "aspect": game_aspect.choice()}


def match_key(install, row):
    """对局编号：一串乱码，服务器只用它来认出"又发了同一局"。本机的局号和时间不直接发出去。"""
    raw = f"{install}|{row['match_id']}|{row['ended_at'] or row['started_at'] or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def match_payload(conn, row, install):
    """一局要发的内容（只有这些项）。"""
    mates = storage.teammates_of(conn, row["match_id"])[:2]
    edited = sorted({f for f in (row["edited"] or "").split(",") if f})
    return {"k": match_key(install, row), "day": (row["ended_at"] or "")[:10] or None, "version": row["app_version"],
            "mode": row["mode"], "map": row["map"], "legend": row["legend"], "mates": [m["legend"] for m in mates],
            # 队友的数据单独放一项：老的统计服务器不认识它，照样收下这一局（mates 的格式不变）
            "mate_stats": [{f: m[f] for f in MATE_FIELDS} for m in mates],
            **{f: row[f] for f in MATCH_FIELDS},
            "tier": row["rank_tier"], "rp_source": row["rp_source"], "review": bool(row["needs_review"]),
            "edited": edited}


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _load_sent():
    try:
        data = json.loads(SENT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_sent(sent):
    SENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SENT_PATH.write_text(json.dumps(sent, separators=(",", ":")), encoding="utf-8")


def pending(conn, install, sent, now=None):
    """要发的局：同意以后结束、已经结束 SETTLE_MINUTES 分钟、没发过或发过以后又变了。返回 [(摘要, 内容)]。"""
    since = app_settings.get(SINCE_KEY)
    if not isinstance(since, str):
        return []
    cutoff = ((now or datetime.now()) - timedelta(minutes=SETTLE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute("SELECT * FROM matches WHERE ended_at >= ? AND ended_at <= ? ORDER BY ended_at, match_id",
                        (since, cutoff)).fetchall()
    out = []
    for row in rows:
        payload = match_payload(conn, row, install)
        digest = _digest(payload)
        if sent.get(payload["k"]) != digest:
            out.append((digest, payload))
    return out


# ---------- 联网 ----------

def _post(path, body, timeout=TIMEOUT):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(STATS_URL + path, data=data, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": f"Evo/{storage.APP_VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            reply = json.loads(response.read(MAX_REPLY).decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SendError(f"统计服务器拒绝了（{e.code}）") from e
    except OSError as e:
        raise SendError("连不上统计服务器（网络不通，或者服务器暂时不可用）") from e
    except ValueError as e:
        raise SendError("统计服务器的回答格式不对") from e
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise SendError("统计服务器没有收下")
    return reply


def _ping_days(today):
    """要发日活的日期（从早到晚）：以前没发出去的，加上今天（今天还没发过时）。联网之前先记下，连不上也不丢。"""
    stored = app_settings.get(MISSED_KEY)
    days = [d for d in stored if isinstance(d, str) and DAY.fullmatch(d)] if isinstance(stored, list) else []
    if app_settings.get(PING_KEY) != today:
        days.append(today)
    days = sorted(set(days))[-MAX_PING_DAYS:]
    if days != stored and (days or stored is not None):
        app_settings.set_value(MISSED_KEY, days or None)
    return days


def _ping_sent(day):
    days = [d for d in app_settings.get(MISSED_KEY) or [] if d != day]
    app_settings.set_value(MISSED_KEY, days or None)
    last = app_settings.get(PING_KEY)
    if not isinstance(last, str) or day > last:
        app_settings.set_value(PING_KEY, day)


def send(db_path=DB_PATH, now=None):
    """发送一次：今天还没发过日活就带上，加上没发过、或发过以后又变了的局。没同意时什么都不做，返回 None。
    没有要发的也不联网。返回 {"ok", "matches"（发了几局）, "ping"（发了日活没有）, "error", "at"}，有联网时记进设置。"""
    if consent() is not True:
        return None
    with _lock:
        now = now or datetime.now()
        install, today = install_id(), now.strftime("%Y-%m-%d")
        sent = _load_sent()
        conn = storage.connect(db_path)
        try:
            todo = pending(conn, install, sent, now)
        finally:
            conn.close()
        days = _ping_days(today)
        result = {"ok": True, "matches": 0, "ping": False, "error": None, "at": now.strftime("%Y-%m-%d %H:%M")}
        if not days and not todo:
            return result
        batches = [todo[start:start + BATCH] for start in range(0, len(todo), BATCH)]
        batches += [[]] * (len(days) - len(batches))  # 每个请求带一天的日活，日活比局多时多出来的单独发
        ping = ping_payload(today) if days else None
        try:
            for i, batch in enumerate(batches):
                body = {"id": install, "version": storage.APP_VERSION, "matches": [p for _, p in batch]}
                if i < len(days):
                    body["ping"] = {**ping, "day": days[i]}
                _post("/v1/report", body)
                for digest, payload in batch:
                    sent[payload["k"]] = digest
                _save_sent(sent)
                result["matches"] += len(batch)
                if "ping" in body:
                    _ping_sent(days[i])
                    result["ping"] = True
        except SendError as e:
            result.update(ok=False, error=str(e))
        app_settings.set_value(LAST_KEY, result)
        return result


# ---------- 开发时看 ----------

def preview(conn):
    """一次发送的原样（每局的那一份以最近一局为例），python code/telemetry.py 打印。不联网，也不生成匿名编号。"""
    install = app_settings.get(ID_KEY) if _valid_id(app_settings.get(ID_KEY)) else "（同意后随机生成）"
    row = conn.execute("SELECT * FROM matches ORDER BY ended_at DESC, match_id DESC LIMIT 1").fetchone()
    body = {"id": install, "version": storage.APP_VERSION, "ping": ping_payload(datetime.now().strftime("%Y-%m-%d")),
            "matches": [match_payload(conn, row, install)] if row else []}
    return json.dumps(body, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    print(f"同意了没有：{ {True: '同意', False: '不同意', None: '还没问过'}[consent()] }；发送地址：{STATS_URL}")
    connection = storage.connect()
    print(preview(connection))
    connection.close()
