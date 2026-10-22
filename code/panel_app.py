"""本地面板：用浏览器查看和修正战绩。只在本机（127.0.0.1）运行；会联网的只有"检查更新"（update_check.py，可以关掉）、
你点了才下载的一键更新（updater.py），和你同意了才发送的匿名统计（telemetry.py）。

运行：python code/panel_app.py                    读正式数据库 data/evo.db
演示：python code/panel_app.py --db data/demo.db   读伪数据（先运行 python tools/demo_data.py 生成）
启动后会自动打开浏览器；在终端按 Ctrl+C 关闭面板。
"""

import argparse
import base64
import ctypes
import functools
import io
import json
import math
import mimetypes
import os
import secrets
import threading
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, session,
                   url_for)

import app_settings
import autostart
import data_move
import game_aspect
import ranks
import screenshots
import sounds
import storage
import telemetry
import update_check
import updater
import paths
from paths import CAPTURES_DIR, CONFIG_DIR, PANEL_DIR, data_absolute
from post_match import COUNTED_KILLS_NOTES, NO_SUMMARY_NOTES

# 面板文件的类型写死：Flask 按 Windows 注册表判断文件类型，有的软件会把 .css 改成 text/plain，
# WebView2 和浏览器就不用这个样式表，面板只剩一堆没排好的文字
for _type, _ext in (("text/css", ".css"), ("text/javascript", ".js"), ("font/woff2", ".woff2"),
                    ("image/webp", ".webp"), ("image/png", ".png")):
    mimetypes.add_type(_type, _ext)

PER_PAGE = 20
# 顶部导航：(页面, 显示名)。顺序也决定切换页面时，选中标签的底色从哪边滑过来
NAV = (("overview", "总览"), ("matches", "战绩"), ("settings", "设置"))
MODES ={"ranked": "排位", "pubs": "匹配", "other": "其他"}
# 修正表单里可以改的字段：(显示名, 最小值, 最大值)
EDITABLE = {
    "placement": ("名次", 1, 20),
    "kills": ("击杀", 0, 99),
    "assists": ("助攻", 0, 99),
    "knockdowns": ("击倒", 0, 99),
    "participation": ("老板分", 0, 99),
    "damage": ("伤害", 0, 99999),
    "rp_delta": ("RP 变化", -999, 999),
}

# 待确认原因里提到的数据项 → 修正表单里要高亮的输入框
REVIEW_FIELDS = {
    "名次": ["placement"],
    "击杀/助攻/击倒": ["kills", "assists", "knockdowns"],
    "伤害": ["damage"],
    "击杀数": ["kills"],  # "击杀数 X 大于小队总击杀 Y"
    "RP": ["rp_delta"],
    "对局中读到的击杀": ["kills"],  # 对局中右上角比总结页多（见 post_match.settle）
    "对局中读到的助攻": ["assists"],
    "对局中读到的伤害": ["damage"],
    "大厅校准": ["rp_delta"],  # 前后两次大厅的总分和各局 RP 对不上（见 calibration.py）
}
RP_SOURCES = {"经验值页": "经验值页", "对局中": "对局中右上角", "大厅校准": "大厅校准"}  # 每局 RP 从哪来

# ---- 总览页 ----
TIER_BADGE = {"菜鸟": ("R", "rookie"), "青铜": ("B", "bronze"), "白银": ("S", "silver"), "黄金": ("G", "gold"),
              "白金": ("P", "platinum"), "钻石": ("D", "diamond"), "大师": ("M", "master"), "Apex 猎杀者": ("A", "predator")}
try:  # 段位图标的铭牌位置（python code/icons.py 生成）；没有图标的段位（菜鸟）用上面的字母方块
    RANK_PLATES = json.loads((PANEL_DIR / "static" / "ranks" / "ranks.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    RANK_PLATES = {}
DAY_START_HOUR = 5  # "今日"从早上 5 点算起：打到凌晨的局仍算前一天
RANGES = {"30": "近 30 局", "all": "全部"}
CHART_W, CHART_H = 600, 200  # 走势图的坐标范围（网页上会按卡片宽度拉伸）

# ---- 传奇头像（战绩页、总览页）----


def load_legend_icons():
    """传奇头像：中文名 → static 里的图标（python tools/legend_icons.py 生成，文件名是 config/legends.json 的编号；
    许可各不相同，见 panel/static/legends/LICENSE.txt）。没有图标的传奇不在里面，面板上只显示名字。"""
    try:
        legends = json.loads((CONFIG_DIR / "legends.json").read_text(encoding="utf-8"))["legends"]
    except (OSError, ValueError, KeyError):
        return {}
    folder = PANEL_DIR / "static" / "legends"
    return {info["zh"]: f"legends/{key}.svg" for key, info in legends.items() if (folder / f"{key}.svg").is_file()}


LEGEND_ICONS = load_legend_icons()

# ---- 设置页 ----
SOURCE_URL = "https://github.com/AugustHuangMX/Evo"
FEEDBACK_URL = "https://qm.qq.com/q/6L2kTbmCWc"  # "意见反馈"：加 QQ 群（和 README、隐私政策里的是同一个群）
FEEDBACK_GROUP = "1128519951"

# ---- 多账号 ----
SIMILAR_NAME = 0.6  # 新名字和某个账号的名字这么像，弹窗里提示"名字很像"（可能是识别误差，也可能是改名）

app = Flask(__name__, template_folder=str(PANEL_DIR / "templates"), static_folder=str(PANEL_DIR / "static"))
app.secret_key = secrets.token_hex(16)  # 每次启动随机生成，用于页面上的提示消息
app.config["TOKEN"] = secrets.token_urlsafe(16)  # 修改、删除时要带上的口令
app.config["DB"] = storage.DB_PATH


# ---------- 数据库连接：每次网页请求打开一次，请求结束时关闭 ----------

def db():
    if "db" not in g:
        g.db = storage.connect(app.config["DB"])
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# ---------- 安全检查 ----------

@app.before_request
def security_checks():
    # 只接受用本机地址访问（防止恶意网页通过"DNS 重绑定"冒充本机来读数据）
    if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
        abort(403)
    # 修改、删除必须带上口令（防止别的网页偷偷替你提交）
    if request.method == "POST" and request.form.get("token") != app.config["TOKEN"]:
        abort(403)
    # 正在搬数据文件夹（data_move.py）：这时改的东西会留在旧位置、被删掉，所以先不让改
    if request.method == "POST" and data_move.busy():
        flash("正在搬数据文件夹，搬完 Evo 会重新打开，到时候再改。", "error")
        return back_to_page()


@app.context_processor
def template_globals():
    offer = update_check.available()
    ask_update = app_settings.get("update_auto") is None
    ask_start = not ask_update and ask_autostart()
    return {
        # 在 Evo 自己的窗口里打开（app_window.py 会在 User-Agent 里带上 EvoApp）：显示自定义标题栏
        "in_app": "EvoApp/" in request.headers.get("User-Agent", ""),
        "token": app.config["TOKEN"],
        "is_demo": Path(app.config["DB"]).name != storage.DB_PATH.name,
        "db_name": Path(app.config["DB"]).name,
        "nav": NAV,
        "switch": page_switch(),
        # 检查更新：还没问过要不要自动检查 → 页面顶部问一次；已知有新版本 → 顶部提示条（一键更新的各个阶段见 updater.state）
        "ask_update": ask_update,
        # 开机自启：回答了上面那个以后再问（一次只问一件事）
        "ask_autostart": ask_start,
        # 匿名统计：前两个都回答了以后再问（默认不发；见 telemetry.py）
        "ask_telemetry": not ask_update and not ask_start and telemetry.consent() is None,
        "update_offer": offer,
        "update_job": updater.state(offer),
        "update_result": updater.take_result(),  # 刚一键更新过：顶部说一次"已更新到 x.y.z"或"没装上"
        # 搬数据文件夹（data_move.py）：正在搬时顶部显示进度；刚搬好时顶部说一次搬到了哪里
        "move_job": data_move.state(),
        "move_result": data_move.take_result(),
        "version": storage.APP_VERSION,
        # 多账号：认出了新的玩家名 → 弹窗问"是不是同一个人"；有 2 个以上账号时，右上角切换账号
        "name_question": name_question(),
        "account_view": account_view(),
    }


def ask_autostart():
    """第一次打开时要不要问"开机自动启动"：能设置（打包好的 Evo）、没问过、现在没开着。"""
    return (autostart.unavailable() is None and not app_settings.get(autostart.ASKED_KEY)
            and not autostart.status()["on"])


def account_view():
    """现在看的是哪个账号（每次网页请求算一次）。返回字典：
    accounts（所有账号）、current（看的账号；None = 不分账号）、all（选了"全部账号"）、
    names（筛选对局用的玩家名；None = 不按账号筛选）。
    只有 1 个账号（或还没认出过玩家名）时不分账号，和以前一样显示全部（包括还没认出玩家名的局）。
    有 2 个以上时：这个窗口里选过就看选的那个；没选过（每次打开窗口）看最近玩过的账号——在小号上打完、打开 Evo 看到的就是小号。"""
    if "account_view" in g:
        return g.account_view
    everyone = storage.accounts(db())
    view = {"accounts": everyone, "current": None, "all": False, "names": None}
    if len(everyone) >= 2:
        choice = session.get("account")
        if choice == "all":
            view["all"] = True
        else:
            current = next((a for a in everyone if a["id"] == choice), None) or max(everyone, key=lambda a: a["last"])
            view.update(current=current, names=current["raw"])
    g.account_view = view
    return view


def name_question():
    """弹窗要问的新玩家名（最早出现的那个）和可以合并到的账号。没有要问的，或者这个窗口里点过"稍后再问"，返回 None。
    新名字的局在回答之前先单独算一个账号，不会丢。"""
    if session.get("names_later"):
        return None
    everyone = storage.accounts(db())
    pending = [a for a in everyone if not a["confirmed"]]
    known = [a for a in everyone if a["confirmed"]]
    if not pending:
        return None
    new = pending[0]
    if not known:  # 原来的账号的局都删光了：没有可合并的，直接当作一个账号
        storage.answer_name(db(), new["name"])
        return None
    choices = [{
        "id": a["id"],
        "name": a["name"],
        "games": a["games"],
        "last": time_text(a["last"]),
        "similar": max(storage.name_similarity(n, new["name"]) for n in a["names"]) >= SIMILAR_NAME,
        "order": a["last"],
    } for a in known]
    choices.sort(key=lambda c: (c["similar"], c["order"]), reverse=True)  # 名字很像的排在前面，其余按最近玩过的排
    return {
        "name": new["name"],
        "games": new["games"],
        "last": time_text(new["last"]),
        "more": len(pending) - 1,  # 后面还有几个新名字要问
        "accounts": choices,
    }


def page_switch():
    """这次是不是从另一个页面切过来的（看浏览器带来的"上一页"地址）。是的话播放切换动效：
    内容从下往上淡入（enter），选中标签的底色从上一页的标签滑过来（shift = 从右边第几格过来，负数是左边）。
    同一页里的操作（展开某一局、筛选、保存……）不播放。"""
    came_from = urlparse(request.referrer or "").path
    if came_from == request.path:
        return {"enter": False, "shift": 0}
    paths = [url_for(endpoint) for endpoint, _ in NAV]
    shift = paths.index(came_from) - paths.index(request.path) if came_from in paths and request.path in paths else 0
    return {"enter": True, "shift": shift}


@app.template_global()
def url_with(**changes):
    """在当前网址的筛选条件基础上，改动其中几项（值为 None 表示去掉这一项）。
    改了展开哪一局（open）时，"详情与修改"（detail）也收起，除非同时指定了 detail。"""
    args = request.args.to_dict()
    if "open" in changes and "detail" not in changes:
        args.pop("detail", None)
    for key, value in changes.items():
        if value is None:
            args.pop(key, None)
        else:
            args[key] = value
    return url_for("matches") + ("?" + urlencode(args) if args else "")


# ---------- 把数据库里的一行，整理成网页上显示的文字 ----------

def time_text(ended_at):
    if not ended_at:
        return "—"
    dt = datetime.strptime(ended_at, "%Y-%m-%d %H:%M:%S")
    if dt.date() == date.today():
        return f"今天 {dt:%H:%M}"
    if dt.date() == date.today() - timedelta(days=1):
        return f"昨天 {dt:%H:%M}"
    return f"{dt:%m-%d %H:%M}"


def badge_class(placement):
    if placement is None:
        return "p-none"
    if placement == 1:
        return "p1"
    if placement <= 3:
        return "p2"
    return "p3" if placement <= 10 else "p4"


def dash(value, fmt="{}"):
    return "—" if value is None else fmt.format(value)


def signed(n):
    """RP 这类有正负的数：+28、−17（用真正的减号，和游戏里一样）。"""
    return f"+{n:,}" if n >= 0 else f"−{-n:,}"


def warn_fields(review_note):
    """从待确认原因（如"名次：没读到（OCR 原文：无）；伤害：……"）里找出有问题的输入框。"""
    fields = set()
    for problem in (review_note or "").split("；"):
        for label, names in REVIEW_FIELDS.items():
            if problem.startswith(label):
                fields.update(names)
    return fields


def view(row):
    rp = row["rp_delta"]
    return {
        "id": row["match_id"],
        "player_name": row["player_name"] or "—",
        "placement": dash(row["placement"], "#{}"),
        "badge": badge_class(row["placement"]),
        "time": time_text(row["ended_at"]),
        "mode": MODES.get(row["mode"], "—"),
        "map": row["map"] or "—",
        "legend": row["legend"] or "—",
        "legend_icon": LEGEND_ICONS.get(row["legend"]),
        # 总览页"最近对局"的标题和副标题：有什么显示什么
        "title": " · ".join(x for x in (row["legend"], row["map"]) if x) or "传奇、地图未识别",
        "subtitle": " · ".join(x for x in (MODES.get(row["mode"]), time_text(row["ended_at"])) if x),
        "kda": " / ".join(dash(row[k]) for k in ("kills", "assists", "knockdowns")),
        "participation": dash(row["participation"]),
        "damage": dash(row["damage"], "{:,}"),
        "survival": storage.mmss(row["survival_time"]).replace("-", "—"),
        "revives": dash(row["revives"]),
        "respawns": dash(row["respawns"]),
        "squad_kills": dash(row["squad_kills"]),
        "rp": "—" if rp is None else signed(rp),
        "rp_class": "none" if rp is None else ("good" if rp >= 0 else "bad"),
        "rp_source": RP_SOURCES.get(row["rp_source"], row["rp_source"]) if rp is not None else None,
        "needs_review": bool(row["needs_review"]),
        "review_note": row["review_note"],
        "no_summary": any(n in (row["review_note"] or "") for n in NO_SUMMARY_NOTES),  # 没有总结页的局：队友只能靠对局中认
        "has_evidence": bool(row["evidence_path"]),
        "evidence_recorded": screenshots.recorded(row["evidence_path"]),  # 记录程序存的截图：确认、删除这一局时会删掉
        "form": {name: "" if row[name] is None else row[name] for name in EDITABLE},
        "warn_fields": warn_fields(row["review_note"]) if row["needs_review"] else set(),
    }


def mate_view(mate):
    """队友表的一行（见 storage.MATE_COLUMNS），整理成战绩页展开后显示的文字。"""
    return {
        "name": mate["name"],
        "player_id": mate["player_id"],
        "legend": mate["legend"] or "—",  # 对局中左下角认的（0.3.0 以前的局没有）
        "legend_icon": LEGEND_ICONS.get(mate["legend"]),
        "kda": " / ".join(dash(mate[k]) for k in ("kills", "assists", "knockdowns")),
        "damage": dash(mate["damage"], "{:,}"),
        "survival": storage.mmss(mate["survival_time"]).replace("-", "—"),
        "revives": dash(mate["revives"]),
        "respawns": dash(mate["respawns"]),
    }


# ---------- 总览页的统计 ----------

def rank_score(row):
    """把段位换算成一条连续的分数线（每段的起点见 config/ranks.json），用来画走势图。没有段位数据返回 None。"""
    return ranks.score(row["rank_tier"], row["rank_division"], row["rank_progress"])


def latest_rank(rows, lobby):
    """最近一次读到的段位：经验值页（每局结束后）和大厅读数里更新的那个。时间一样时以大厅为准
    （2026-10-18 用户决定：总分以大厅为准）。返回 (段位, 段, 段内进度, 时间, 来源)，都没有返回 None。"""
    row = next((r for r in reversed(rows) if rank_score(r) is not None), None)
    last = lobby[-1] if lobby else None
    if last is not None and (row is None or (last["read_at"] or "") >= (row["ended_at"] or "")):
        return last["rank_tier"], last["rank_division"], last["rank_progress"], last["read_at"], "大厅"
    if row is not None:
        return row["rank_tier"], row["rank_division"], row["rank_progress"], row["ended_at"], None
    return None


def rank_card(rows, lobby=()):
    """最近一次读到的段位（见 latest_rank）：段位名和段内的排位点数。不显示分母、进度条和"距下一段还差"
    （2026-10-17 用户决定：游戏里每段要多少分不一样，有的还没核实）。"""
    found = latest_rank(rows, lobby)
    if found is None:
        return None
    tier, division, progress, when, source = found
    letter, css = TIER_BADGE.get(tier, ("?", "unknown"))
    whole = not ranks.has_divisions(tier)  # 大师、猎杀者：只写段位名
    return {
        "badge": letter if whole else f"{letter}{len(ranks.DIVISIONS) - ranks.DIVISIONS.index(division)}",
        "icon": f"ranks/{css}.webp" if css in RANK_PLATES else None,
        "plate": RANK_PLATES.get(css),  # 段数写在徽章底部的铭牌上，和游戏里一样
        "division": None if whole else division,
        "tier_class": css,
        "name": ranks.rank_name(tier, division),
        "progress": progress,
        "as_of": time_text(when) + (f" · {source}" if source else ""),
    }


def rp_chart(rows, lobby=()):
    """RP 走势图：每局结束后的段位换算成分数，连成折线；范围内的段位分界线画成虚线。
    最后一次大厅读数比最后一局晚、分数又不一样时，接在最后（总分以大厅为准）。有段位的局少于 2 局时返回 None。"""
    scored = [r for r in rows if rank_score(r) is not None]
    points = [rank_score(r) for r in scored]
    if len(points) < 2:
        return None
    count = len(points)
    if lobby and lobby[-1]["score"] is not None and (lobby[-1]["read_at"] or "") > (scored[-1]["ended_at"] or "") \
            and lobby[-1]["score"] != points[-1]:
        points.append(lobby[-1]["score"])
    low, high = min(points), max(points)
    bounds = ranks.boundaries()
    target = next((b for b, _ in bounds if b > points[-1]), None)  # 下一段的起点（已经是最高段时没有）
    if target is not None and target - high <= max(200, high - low):  # 快升段了：把下一段的分数线也画进来；离得远就不画，免得折线被压扁
        high = max(high, target)
    pad = max(20, (high - low) * 0.1)
    low, high = low - pad, high + pad

    def y(score):
        return CHART_H - (score - low) / (high - low) * CHART_H

    lines = [{"y": round(y(b), 1), "top": round(y(b) / CHART_H * 100, 1), "name": name}
             for b, name in bounds if low <= b <= high]
    xs = [i / (len(points) - 1) * CHART_W for i in range(len(points))]
    line = " ".join(f"{x:.1f},{y(s):.1f}" for x, s in zip(xs, points))
    return {
        "w": CHART_W,
        "h": CHART_H,
        "line": line,
        "area": f"0,{CHART_H} {line} {CHART_W},{CHART_H}",
        "lines": lines,
        "end_top": round(y(points[-1]) / CHART_H * 100, 1),
        "count": count,  # 有段位的局数（"N 局前"按局数算；接在最后的大厅读数在最后一局之后，不多算一局）
    }


def stat_cards(rows):
    placements = [r["placement"] for r in rows if r["placement"] is not None]
    # 击杀来自经验值页的局（没有总结页，也没读到对局中右上角），击杀是"计分次数"（可能比实际少），不算进场均击杀
    kills = [r["kills"] for r in rows if r["kills"] is not None
             and not any(n in (r["review_note"] or "") for n in COUNTED_KILLS_NOTES)]
    damages = [r["damage"] for r in rows if r["damage"] is not None]

    def avg(values, fmt):
        return fmt.format(sum(values) / len(values)) if values else "—"

    def rate(hit):
        return f"{sum(1 for p in placements if hit(p)) / len(placements):.0%}" if placements else "—"

    return [
        {"label": "场均名次", "value": avg(placements, "{:.1f}")},
        {"label": "场均击杀", "value": avg(kills, "{:.1f}")},
        {"label": "场均伤害", "value": avg(damages, "{:,.0f}")},
        {"label": "前五率", "value": rate(lambda p: p <= 5)},
        {"label": "胜率", "value": rate(lambda p: p == 1), "accent": True},
        {"label": "统计局数", "value": str(len(rows))},
    ]


def rp_sum(rows):
    values = [r["rp_delta"] for r in rows if r["rp_delta"] is not None]
    return {"text": signed(sum(values)), "good": sum(values) >= 0} if values else {"text": "—", "good": None}


def legend_bars(rows, top=3):
    """传奇表现：用得最多的几个传奇，每个一条横条，按名次分段上色。没有传奇数据返回 None。"""
    groups = {}
    for r in rows:
        if r["legend"]:
            groups.setdefault(r["legend"], []).append(r)
    if not groups:
        return None
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    items = ordered[:top]
    rest = [r for _, rs in ordered[top:] for r in rs]
    if rest:
        items.append(("其他", rest))
    widest = max(len(rs) for _, rs in items)

    bars = []
    for name, rs in items:
        places = [r["placement"] for r in rs]
        buckets = [
            ("p1", sum(1 for p in places if p == 1)),
            ("p2", sum(1 for p in places if p is not None and 2 <= p <= 3)),
            ("p3", sum(1 for p in places if p is not None and 4 <= p <= 10)),
            ("p4", sum(1 for p in places if p is None or p > 10)),
        ]
        damages = [r["damage"] for r in rs if r["damage"] is not None]
        bars.append({
            "name": name,
            "icon": LEGEND_ICONS.get(name),
            "games": len(rs),
            "avg_damage": f"{sum(damages) / len(damages):,.0f}" if damages else None,
            "segments": [(css, n / widest * 100) for css, n in buckets if n],
            "other": name == "其他",
        })
    return bars


def day_start(now):
    start = now.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
    return start if now >= start else start - timedelta(days=1)


def today_matches(rows):
    """今天（从早上 5 点算起）打的局。"""
    since = f"{day_start(datetime.now()):%Y-%m-%d %H:%M:%S}"
    return [r for r in rows if (r["ended_at"] or "") >= since]


# ---------- 分享战绩（图片卡片）----------
# 卡片是一张 SVG 图（模板在 panel/templates/share/），字体、传奇头像、段位图标都嵌在里面，不引用别的文件；
# 页面上把它画成 2 倍大小的 PNG，复制或保存（panel/templates/_share.html）。图片只在本机生成，不上传。
# 队友只显示传奇和这一局的数据，不写名字和 ID；自己的游戏名默认显示，分享前可以勾掉（网址里 name=0）
# （2026-10-21 用户决定）

SHARE_SIZE = {"match": (720, 405), "today": (720, 405), "overview": (720, 540)}  # 卡片大小（CSS 像素，PNG 是 2 倍）
SHARE_TODAY_GAMES = 16  # 今日战绩卡片上最多列出几局的名次（更多的只写"前面还有 N 局"）
WEEKDAYS = "一二三四五六日"


@app.template_global()
def embed(filename):
    """static 里的文件 → data: 网址，嵌进分享卡片（SVG 当图片显示时不能引用别的文件）。只用于代码里写定的文件。"""
    return _embedded(filename)


@functools.lru_cache(maxsize=64)
def _embedded(filename):
    path = Path(app.static_folder) / filename
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{kind};base64,{base64.b64encode(path.read_bytes()).decode()}"


@app.template_global()
def embed_icon():
    return "data:image/png;base64," + base64.b64encode(icon_png()).decode()


def share_time(ended_at):
    """分享卡片上的时间：写日期，不写"今天""昨天"（图片发出去以后过几天再看就不对了）。不是今年的局加上年份。"""
    if not ended_at:
        return None
    dt = datetime.strptime(ended_at, "%Y-%m-%d %H:%M:%S")
    year = f"{dt.year}年" if dt.year != date.today().year else ""
    return f"{year}{dt.month}月{dt.day}日 {dt:%H:%M}"


def share_player(rows):
    """今日、概况卡片上写的游戏名：分账号时是这个账号的名字，不分时是最近一局认出的名字。"""
    current = account_view()["current"]
    if current:
        return current["name"]
    return next((r["player_name"] for r in reversed(rows) if r["player_name"]), None)


def share_match_card(row, mates, player):
    """单局战绩卡片。player：要写在卡片上的游戏名（None = 不写）。"""
    p, rp = row["placement"], row["rp_delta"]
    return {
        "player": player,
        "place": dash(p, "#{}"),
        "place_class": badge_class(p),
        "place_text": "名次没读到" if p is None else ("冠军" if p == 1 else "名次"),
        "legend": row["legend"] or "传奇未识别",
        "legend_icon": LEGEND_ICONS.get(row["legend"]),
        "meta": " · ".join(x for x in (MODES.get(row["mode"]), row["map"], share_time(row["ended_at"])) if x),
        "stats": [("击杀", dash(row["kills"])), ("助攻", dash(row["assists"])), ("击倒", dash(row["knockdowns"])),
                  ("伤害", dash(row["damage"], "{:,}")), ("生存", storage.mmss(row["survival_time"]).replace("-", "—"))],
        "ranked": row["mode"] == "ranked",
        "rp": None if rp is None else {"text": signed(rp), "good": rp >= 0},
        "rank": rank_card([row]) if row["mode"] == "ranked" else None,  # 打完这一局的段位（经验值页读的）
        # 队友：只有传奇和这一局的数据，不写名字和 ID
        "mates": [{
            "legend": m["legend"] or "传奇未识别",
            "icon": LEGEND_ICONS.get(m["legend"]),
            "kda": " / ".join(dash(m[k]) for k in ("kills", "assists", "knockdowns")),
            "damage": dash(m["damage"], "{:,}"),
        } for m in mates],
    }


def share_today_card(rows, lobby, player):
    """今日战绩卡片：今天打了几局、每局的名次、加起来的击杀和伤害、RP 一共涨了多少、现在的段位。"""
    today = today_matches(rows)
    start = day_start(datetime.now())
    places = [r["placement"] for r in today if r["placement"] is not None]
    kills = [r["kills"] for r in today if r["kills"] is not None]
    damages = [r["damage"] for r in today if r["damage"] is not None]
    shown = today[-SHARE_TODAY_GAMES:]
    return {
        "player": player,
        "date": f"{start.month}月{start.day}日 · 周{WEEKDAYS[start.weekday()]}",
        "rp": rp_sum(today),
        "rank": rank_card(rows, lobby),
        "stats": [("局数", str(len(today))), ("最好名次", f"#{min(places)}" if places else "—"),
                  ("总击杀", f"{sum(kills):,}" if kills else "—"), ("总伤害", f"{sum(damages):,}" if damages else "—"),
                  ("场均伤害", f"{sum(damages) / len(damages):,.0f}" if damages else "—")],
        "games": [{"text": dash(r["placement"], "#{}"), "css": badge_class(r["placement"])} for r in shown],
        "earlier": len(today) - len(shown),
    }


def share_overview_card(rows, lobby, range_key, player):
    """概况卡片：段位、RP 走势、统计数字、传奇表现，范围和总览页一样（近 30 局 / 全部）。
    以后能按赛季看了，改成按赛季（见 todo.md）。"""
    in_range = rows[-30:] if range_key == "30" else rows
    days = [f"{int(r['ended_at'][5:7])}月{int(r['ended_at'][8:10])}日" for r in in_range if r["ended_at"]]
    period = None  # 第一局到最后一局的日期，如"10月19日 – 10月21日"（同一天只写一个）
    if days:
        period = days[0] if days[0] == days[-1] else f"{days[0]} – {days[-1]}"
    return {
        "player": player,
        "title": f"最近 {len(in_range)} 局" if len(in_range) < len(rows) else f"全部 {len(in_range)} 局",
        "period": period,
        "rank": rank_card(rows, lobby),
        "chart": rp_chart(in_range, lobby),
        "range_rp": rp_sum(in_range),
        "stats": stat_cards(in_range),
        "legends": legend_bars(in_range),
    }


def share_svg(kind, card):
    w, h = SHARE_SIZE[kind]
    svg = render_template(f"share/{kind}.svg", card=card, w=w, h=h)
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "no-store"})


def share_options(kind, **args):
    """分享对话框里的一种卡片（_share.html）：网址、大小、保存时默认的文件名。"""
    w, h = SHARE_SIZE[kind]
    stamp = f"{datetime.now():%Y%m%d_%H%M}"
    name = {"match": "Evo单局战绩", "today": "Evo今日战绩", "overview": "Evo战绩概况"}[kind]
    return {"kind": kind, "url": url_for(f"share_{kind}", **args), "w": w, "h": h, "file": f"{name}_{stamp}.png"}


# ---------- 页面 ----------

@app.route("/")
def overview():
    range_key = request.args.get("range") if request.args.get("range") in RANGES else "30"
    names = account_view()["names"]
    rows = storage.all_matches(db(), names)
    in_range = rows[-30:] if range_key == "30" else rows
    today = today_matches(rows)
    lobby = storage.lobby_readings(db(), names)  # 每次回大厅读到的段位（总分以它为准）
    # 分享：今日战绩、概况（范围和这一页一样）。"全部账号"时不能分享（几个账号的段位混在一起）
    # review：其中有几局待确认（对话框里提醒：确认后图上的数字更准）
    share = None
    if rows and not account_view()["all"]:
        share = {"player": share_player(rows), "options": [
            {**share_options("today"), "label": "今日战绩", "disabled": None if today else "今天还没有对局",
             "review": sum(1 for r in today if r["needs_review"])},
            {**share_options("overview", range=range_key), "label": f"{RANGES[range_key]}概况",
             "review": sum(1 for r in in_range if r["needs_review"])},
        ]}

    return render_template(
        "overview.html",
        range_key=range_key,
        ranges=RANGES,
        rank=rank_card(rows, lobby),
        chart=rp_chart(in_range, lobby),
        range_rp=rp_sum(in_range),
        today_rp=rp_sum(today),
        today_games=len(today),
        stats=stat_cards(in_range),
        recent=[view(r) for r in reversed(rows[-5:])],
        legends=legend_bars(in_range),
        review_count=storage.count_review(db(), names),
        total=len(rows),
        share=share,
    )


@app.route("/matches")
def matches():
    mode = request.args.get("mode") if request.args.get("mode") in MODES else None
    only_review = request.args.get("review") == "1"
    legend = request.args.get("legend") or None
    page = max(1, request.args.get("page", 1, type=int))
    open_id = request.args.get("open", type=int)
    names = account_view()["names"]

    rows, total = storage.query_matches(db(), mode, only_review, legend, PER_PAGE, (page - 1) * PER_PAGE, names)
    rows = [view(r) for r in rows]
    if only_review and open_id is None and rows:
        open_id = rows[0]["id"]  # "只看待确认"时，自动展开第一局
    # 点开一局：先只展开两名队友；识别截图和修改表单要再点"详情与修改"（detail=1）。待确认的局直接全部展开
    # 分享这一局：待确认的局不能分享（数据还没确认）
    share = None
    for r in rows:
        if r["id"] == open_id:
            r["mates"] = [mate_view(m) for m in storage.teammates_of(db(), open_id)]
            r["show_detail"] = r["needs_review"] or request.args.get("detail") == "1"
            if not r["needs_review"]:
                share = {"player": None if r["player_name"] == "—" else r["player_name"],
                         "options": [{**share_options("match", match_id=r["id"]), "label": "单局战绩"}]}

    return render_template(
        "matches.html",
        rows=rows,
        keep_shots=screenshots.keep_all(),  # 保留全部截图时，确认、删除一局不删截图，也不用提示
        total=total,
        page=page,
        pages=max(1, math.ceil(total / PER_PAGE)),
        first=(page - 1) * PER_PAGE + 1 if total else 0,
        last=min(page * PER_PAGE, total),
        mode=mode,
        modes=MODES,
        only_review=only_review,
        legend=legend,
        legends=storage.distinct_values(db(), "legend", names),
        open_id=open_id,
        review_count=storage.count_review(db(), names),
        editable=EDITABLE,
        share=share,
    )


def back_to_list(**changes):
    """修改或删除后回到原来的列表页（保留筛选条件）。只允许跳回本面板的战绩页。"""
    target = request.form.get("next", "")
    if not target.startswith("/matches"):
        target = url_for("matches")
    if changes:
        base, _, query = target.partition("?")
        args = dict(parse_qsl(query))
        args.update({k: str(v) for k, v in changes.items()})
        target = base + "?" + urlencode(args)
    return redirect(target)


@app.post("/matches/<int:match_id>/update")
def update(match_id):
    fields, errors = {}, []
    for name, (label, low, high) in EDITABLE.items():
        raw = request.form.get(name, "").strip().replace(",", "").replace("−", "-")
        if raw == "":
            fields[name] = None
            continue
        try:
            value = int(raw)
        except ValueError:
            errors.append(f"{label}必须是整数")
            continue
        if not low <= value <= high:
            errors.append(f"{label}应在 {low} 到 {high} 之间")
            continue
        fields[name] = value

    if errors:
        flash("没有保存：" + "；".join(errors), "error")
        return back_to_list(open=match_id)

    row = storage.get_match(db(), match_id)
    if row is not None:  # 记下改过哪几项（只记改了哪项，不记原来的值；匿名统计的"识别情况"用，见 telemetry.py）
        edited = {f for f in (row["edited"] or "").split(",") if f} | {k for k in EDITABLE if fields[k] != row[k]}
        if edited:
            fields["edited"] = ",".join(sorted(edited))
    fields["needs_review"] = 0  # 人工确认过了，不再标记待确认（review_note 保留，记录当初为什么待确认）
    deleted = screenshots.discard(db(), row)  # 确认过了，截图不再需要（原则 2）
    if deleted:
        fields["evidence_path"] = None
    storage.update_match(db(), match_id, fields)
    flash(f"已保存第 {match_id} 号对局" + ("，截图已删除" if deleted else ""), "ok")
    return back_to_list()


@app.post("/matches/<int:match_id>/delete")
def delete(match_id):
    deleted = screenshots.discard(db(), storage.get_match(db(), match_id))
    storage.delete_match(db(), match_id)
    flash(f"已删除第 {match_id} 号对局" + ("和它的截图" if deleted else ""), "ok")
    return back_to_list()


@app.route("/evidence/<int:match_id>")
def evidence(match_id):
    """显示某一局的识别依据截图。只能看数据库里登记过的截图，不能随意读电脑上的文件。"""
    row = storage.get_match(db(), match_id)
    if row is None or not row["evidence_path"]:
        abort(404)
    path = data_absolute(row["evidence_path"])  # 相对路径按数据文件夹找
    if not path.is_file():
        abort(404)
    return send_file(path)


# ---------- 分享战绩：卡片图片（SVG）。网址里 name=0 时不写游戏名 ----------

def share_name_on():
    return request.args.get("name") != "0"


@app.route("/share/match/<int:match_id>.svg")
def share_match(match_id):
    row = storage.get_match(db(), match_id)
    if row is None:
        abort(404)
    player = row["player_name"] if share_name_on() else None
    return share_svg("match", share_match_card(row, storage.teammates_of(db(), match_id), player))


@app.route("/share/today.svg")
def share_today():
    if account_view()["all"]:
        abort(404)  # "全部账号"：几个账号的段位混在一起没有意义
    names = account_view()["names"]
    rows = storage.all_matches(db(), names)
    player = share_player(rows) if share_name_on() else None
    return share_svg("today", share_today_card(rows, storage.lobby_readings(db(), names), player))


@app.route("/share/overview.svg")
def share_overview():
    if account_view()["all"]:
        abort(404)
    range_key = request.args.get("range") if request.args.get("range") in RANGES else "30"
    names = account_view()["names"]
    rows = storage.all_matches(db(), names)
    player = share_player(rows) if share_name_on() else None
    return share_svg("overview", share_overview_card(rows, storage.lobby_readings(db(), names), range_key, player))


# ---------- 设置页 ----------

def screen_size():
    """主显示器的实际分辨率（不受 Windows 显示缩放影响）。读不到返回 None。"""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except (AttributeError, OSError):
        return None


def folder_usage(path):
    """文件夹里有几个文件、一共多大。"""
    files = [p for p in path.rglob("*") if p.is_file()] if path.is_dir() else []
    return len(files), sum(p.stat().st_size for p in files)


def size_text(n):
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{n / 1024:.0f} KB"


def db_path():
    return Path(app.config["DB"]).resolve()


@app.route("/settings")
def settings():
    shot_count, shot_bytes = folder_usage(CAPTURES_DIR / "live")
    clean_count, clean_bytes = screenshots.cleanable(db())
    size = screen_size()
    return render_template(
        "settings.html",
        resolution=f"{size[0]} × {size[1]}" if size else None,
        aspect=aspect_view(size),
        move=move_view(),
        autostart={**autostart.status(), "blocked": autostart.unavailable(),
                   "failed": session.pop("autostart_failed", None)},
        sounds_on=sounds.enabled(),
        sound_list=[{"key": key, "label": label, "when": SOUND_WHEN[key]} for key, label in sounds.SOUNDS.items()],
        data_dir=db_path().parent,
        db_file=db_path().name,
        db_size=size_text(db_path().stat().st_size) if db_path().exists() else "0 KB",
        total=storage.query_matches(db(), limit=1)[1],
        review_count=storage.count_review(db()),
        captures_dir=CAPTURES_DIR,
        shot_count=shot_count,
        shot_size=size_text(shot_bytes),
        keep_shots=app_settings.get(screenshots.KEEP_ALL_KEY, False) is True,
        shots_locked=screenshots.in_project(),  # 用源码运行：永远保留全部，开关和删除按钮都不能用
        recording=screenshots.recording_folder() is not None,
        clean_count=clean_count,
        clean_size=size_text(clean_bytes),
        version=storage.APP_VERSION,
        source_url=SOURCE_URL,
        feedback_url=FEEDBACK_URL,
        feedback_group=FEEDBACK_GROUP,
        update_auto=app_settings.get("update_auto") is True,
        stats=telemetry.status(),
        stats_preview=telemetry.preview(db()),
        last_check=app_settings.get("update_last_check"),
        newest=update_check.known_newer(),
        job=updater.state(update_check.known_newer()),
        accounts=[{**a, "last_text": time_text(a["last"]),
                   "name_stats": [{**one, "last_text": time_text(one["last"])} for one in a["name_stats"]]}
                  for a in account_view()["accounts"]],
    )


def aspect_view(size):
    """设置页"游戏画面比例"：选了什么、自动检测出什么（见 game_aspect.py）。"""
    found, why = game_aspect.from_video_config()
    if found is None and size:
        found = size[0] / size[1]  # 没读到游戏设置：和屏幕一样
    return {"choice": game_aspect.choice(), "auto": game_aspect.AUTO, "options": list(game_aspect.ASPECTS),
            "detected": game_aspect.name(found) if found else None, "why": why}


def move_view():
    """设置页"战绩数据"一栏的"更改位置"（data_move.py）：能不能改、上次检查的结果（检查后回到这一页时显示）。"""
    checked = session.pop("move_check", None)
    auto = data_move.auto_target()
    return {"blocked": data_move.blocker(), "check": checked, "name": paths.DATA_NAME,
            "legacy": paths.DATA_SOURCE == "legacy", "auto_target": str(auto) if auto else None,
            "auto_failed": app_settings.get(data_move.AUTO_FAILED_KEY)}


@app.post("/data/check")
def check_data_move():
    """"更改位置"选好文件夹后："下一步"：检查能不能搬（data_move.check），回到设置页显示结果和"开始搬"按钮。"""
    session["move_check"] = data_move.check(request.form.get("folder", ""))
    return redirect(url_for("settings") + "#data-move")


@app.post("/data/move")
def start_data_move():
    """"开始搬"或"改用那份数据"：在后台搬，顶部显示进度；搬好后 Evo 自己重新打开（data_move.start）。"""
    try:
        data_move.start(request.form.get("folder", ""), use_existing=request.form.get("use_existing") == "1")
    except data_move.MoveError as e:
        flash(f"没开始搬：{e}", "error")
        return redirect(url_for("settings") + "#data-move")
    return back_to_page()


@app.post("/data/dismiss")
def dismiss_data_move():
    """搬失败的提示上点了"知道了"。"""
    data_move.dismiss()
    return back_to_page()


@app.get("/data/progress")
def data_move_progress():
    """页面每半秒问一次搬的进度（base.html 里的脚本）；阶段变了，页面就重新打开一次。"""
    return jsonify(data_move.state() or {"phase": "none"})


@app.post("/settings/game-aspect")
def game_aspect_setting():
    """"游戏画面比例"：自动检测 / 16:9 / 16:10 / 4:3。记录程序每隔几秒看一眼设置，所以几秒内生效。"""
    value = request.form.get("aspect")
    if value != game_aspect.AUTO and value not in game_aspect.ASPECTS:
        abort(400)
    app_settings.set_value(game_aspect.SETTING_KEY, value)
    picked = "自动检测" if value == game_aspect.AUTO else value
    flash(f"游戏画面比例已改成\"{picked}\"：正在记录的话，几秒内就按新的比例识别。", "ok")
    return redirect(url_for("settings"))


@app.post("/settings/autostart")
def autostart_setting():
    """"开机自动启动"：设置页的开关，和第一次打开时页面顶部的提问（next 回到原来的页面）。
    没开成 / 没关成：从设置页的开关点的，写在开关下面（inline）；从顶部提问点的，写在页面顶部。"""
    if autostart.unavailable():
        abort(400)
    on = request.form.get("on") == "1"
    app_settings.set_value(autostart.ASKED_KEY, True)
    ok = autostart.enable() if on else autostart.disable()
    if not ok:
        problem = autostart.FAILED_ON if on else autostart.FAILED_OFF
        if request.form.get("inline") == "1":
            session["autostart_failed"] = problem
        else:
            flash(problem, "error")
    elif on:
        flash("已开启开机自动启动：开机后 Evo 会在托盘里运行（不弹出窗口），打游戏时自动记录。", "ok")
    else:
        flash("开机时不会自动启动 Evo。需要时可以在\"设置\"里打开。", "ok")
    return back_to_page()


SOUND_WHEN = {  # 设置页"提示音"一栏：每一声什么时候响（规则见 live_collect.SoundCues）
    "match_end": "小队打完了（淘汰画面、冠军、右下角出现\"返回大厅\"），提醒你去总结页",
    "summary": "Evo 读到了名次、击杀和伤害，可以离开了",
    "xp": "进入经验值页时。等最上面的数字不再跳动再离开",
}


@app.post("/settings/sounds")
def sounds_setting():
    """"提示音"开关（默认开）。记录程序每次要响时看一眼，所以马上生效。"""
    on = request.form.get("on") == "1"
    app_settings.set_value(sounds.SETTING_KEY, on)
    flash("已开启提示音：对局结束、总结页读完了、进入经验值页时各响一声。" if on
          else "已关闭提示音。", "ok")
    return redirect(url_for("settings"))


@app.post("/settings/sound-preview")
def sound_preview():
    """设置页的"试听"：不管开关，响一声。页面用脚本提交（不刷新页面），回答放出来没有；没有脚本时回到设置页。"""
    name = request.form.get("name")
    if name not in sounds.SOUNDS:
        abort(400)
    ok = sounds.preview(name)
    if request.headers.get("X-Evo-Fetch") == "1":
        return jsonify({"ok": ok})
    return redirect(url_for("settings"))


@app.post("/settings/open-folder")
def open_folder():
    """在资源管理器里打开数据或截图文件夹。只能打开这两个固定的文件夹。"""
    target = {"data": db_path().parent, "captures": CAPTURES_DIR}.get(request.form.get("which"))
    if target is None:
        abort(400)
    target.mkdir(parents=True, exist_ok=True)
    os.startfile(target)
    flash(f"已在资源管理器中打开 {target}", "ok")
    return redirect(url_for("settings"))


@app.post("/settings/keep-shots")
def keep_shots_setting():
    """"保留全部截图"开关。记录程序每局结束时看一眼，所以从下一局开始生效（live_collect.collect 里的 round_end）。"""
    if screenshots.in_project():
        abort(400)
    on = request.form.get("keep") == "1"
    app_settings.set_value(screenshots.KEEP_ALL_KEY, on)
    if on:
        flash("已开启\"保留全部截图\"：从下一局开始，每局的截图都会留着。", "ok")
    else:
        flash("已关闭\"保留全部截图\"：从下一局开始识别完就删。之前存下的截图不会自动删。", "ok")
    return redirect(url_for("settings"))


@app.post("/settings/clean-shots")
def clean_shots():
    """"删除已识别完的截图"按钮：只留"待确认"的局要用的截图，正在记录的这一次不动（screenshots.clean_up）。"""
    if screenshots.in_project():
        abort(400)
    deleted, freed = screenshots.clean_up(db())
    left, _ = screenshots.cleanable(db())
    if deleted:
        flash(f"已删除 {deleted} 张截图，腾出 {size_text(freed)}"
              + (f"。还有 {left} 张没删掉（可能正被别的程序打开），关掉后再试一次" if left else ""), "ok")
    elif left:
        flash(f"{left} 张截图没删掉（可能正被别的程序打开），关掉后再试一次", "error")
    else:
        flash("没有可以删的截图", "ok")
    return redirect(url_for("settings"))


@app.post("/settings/clear")
def clear_data():
    """清空所有战绩。清空前先把数据库备份一份；截图不删。"""
    total = storage.query_matches(db(), limit=1)[1]
    if not total:
        flash("没有可清空的战绩", "ok")
        return redirect(url_for("settings"))
    backup = db_path().with_name(f"{db_path().name}.bak-clear-{datetime.now():%Y%m%d-%H%M%S}")
    storage.backup_db(db(), backup)
    storage.delete_all_matches(db())
    flash(f"已清空 {total} 局战绩。清空前的数据备份在 {backup}", "ok")
    return redirect(url_for("settings"))


@app.post("/settings/telemetry")
def telemetry_setting():
    """匿名统计：第一次打开时页面顶部的提问，和设置页的开关（next 回到原来的页面）。打开时马上在后台发一次。"""
    on = request.form.get("on") == "1"
    was = telemetry.consent()
    telemetry.set_consent(on)
    if on:
        threading.Thread(target=telemetry.send, name="telemetry-now", daemon=True).start()
        flash("已开启匿名统计：Evo 开着时每小时发送一次同意以后的新对局。谢谢你帮助改进 Evo！", "ok")
    elif was is None:
        flash("好的，不发送。以后可以在\"设置\"里打开。", "ok")
    else:
        flash("已关闭匿名统计，不会再发送任何数据。已经发送的可以在设置页删除。", "ok")
    return back_to_page()


@app.post("/settings/telemetry/delete")
def telemetry_delete():
    """"关闭并删除已发送的数据"：请服务器删掉这个匿名编号发过的所有数据（要联网，最多等十几秒）。"""
    try:
        telemetry.delete_sent()
    except telemetry.SendError as e:
        flash(f"没删成：{e}。稍后再试一次。", "error")
    else:
        flash("已删除发送过的数据，并关闭了匿名统计。", "ok")
    return redirect(url_for("settings") + "#stats")


# ---------- 检查更新（update_check.py） ----------

def back_to_page():
    """页面顶部的提问、提示条上的按钮点完后，回到原来的页面。只允许跳回本面板的几个页面。"""
    base, _, query = request.form.get("next", "").partition("?")
    if base not in [url_for(endpoint) for endpoint, _ in NAV]:
        return redirect(url_for("overview"))
    return redirect(base + ("?" + query if query else ""))


@app.post("/update/auto")
def update_auto():
    """要不要自动检查新版本：第一次打开时的提问，和设置页的开关。"""
    on = request.form.get("auto") == "1"
    app_settings.set_value("update_auto", on)
    if on:
        # 马上在后台查一次（最多几秒），页面不用等；托盘程序之后每 8 小时查一次
        threading.Thread(target=update_check.check, name="update-check-now", daemon=True).start()
        flash("已开启自动检查：Evo 开着时每 8 小时联网检查一次有没有新版本。", "ok")
    else:
        flash("已关闭自动检查。需要时可以在设置页手动检查更新。", "ok")
    return back_to_page()


@app.post("/update/check")
def check_update():
    """设置页的"检查更新"按钮：现在就查一次（最多等几秒），结果显示在页面顶部。"""
    result = update_check.check()
    if not result["ok"]:
        flash(f"没查到版本信息：{result['error']}。稍后再试一次。", "error")
    elif result["newer"]:
        version = result["latest"]["version"]
        app_settings.set_value("update_dismissed", None)  # 你主动查的：之前点过"以后再说"也重新显示
        app_settings.set_value("update_notified", version)  # 你已经看到了，托盘不用再弹通知
        flash(f"发现新版本 {version}，点\"下载\"去官网下载。", "ok")
    else:
        flash(f"已经是最新版本（{storage.APP_VERSION}）。", "ok")
    return redirect(url_for("settings"))


@app.post("/update/dismiss")
def dismiss_update():
    """提示条上的"以后再说"：这个版本不再提示（出了更新的版本会再提示）。"""
    app_settings.set_value("update_dismissed", request.form.get("version", ""))
    return back_to_page()


@app.post("/update/download")
def download_update():
    """"立即更新"（和下载失败后的"重试"）：在后台下载新版本，页面上显示进度（updater.start_download）。"""
    try:
        updater.start_download(update_check.known_newer())
    except updater.UpdateError as e:
        flash(str(e), "error")
    return back_to_page()


@app.post("/update/cancel")
def cancel_update():
    """下载中点了"取消"。"""
    updater.cancel_download()
    return back_to_page()


@app.post("/update/install")
def install_update():
    """"安装并重新启动"：新版本跑起来后 Evo 自己退出，装好后重新打开（updater.start_install）。Apex 开着时不装。"""
    latest = update_check.known_newer()
    if latest is None:
        flash("已经是最新版本，不用安装", "ok")
        return back_to_page()
    try:
        updater.start_install(latest["version"])
    except updater.UpdateError as e:
        flash(str(e), "error")
        return back_to_page()
    flash(f"正在安装 {latest['version']}：Evo 会关闭几秒钟，装好后自动重新打开。", "ok")
    return back_to_page()


@app.get("/update/progress")
def update_progress():
    """页面每半秒问一次下载进度（base.html 里的脚本）；阶段变了，页面就重新打开一次。"""
    return jsonify(updater.state(update_check.known_newer()) or {"phase": "none"})


# ---------- 多账号：新玩家名的弹窗（base.html） ----------

@app.post("/accounts/answer")
def answer_name():
    """回答弹窗"是不是同一个人"：account = 要合并到的账号编号（同一个人改了名），或 "new"（另一个账号）。"""
    name, choice = request.form.get("name", ""), request.form.get("account", "")
    everyone = storage.accounts(db())
    new = next((a for a in everyone if not a["confirmed"] and storage.name_key(a["name"]) == storage.name_key(name)), None)
    if new is None:  # 已经回答过了（比如开着两个窗口）
        return back_to_page()
    if choice == "new":
        storage.answer_name(db(), name)
        flash(f"已记为另一个账号：{name}（{new['games']} 局）", "ok")
        return back_to_page()
    target = next((a for a in everyone if a["confirmed"] and str(a["id"]) == choice), None)
    if target is None:
        flash("没有保存：请选一项", "error")
        return back_to_page()
    storage.answer_name(db(), name, target["id"])
    flash(f"已合并：{name} 和 {target['name']} 是同一个账号，共 {new['games'] + target['games']} 局", "ok")
    return back_to_page()


@app.post("/account")
def select_account():
    """右上角切换账号：account = 账号编号或 "all"（全部账号）。记在这个窗口的会话里，关掉窗口后下次打开看最近玩过的账号。
    回到原来的页面，去掉页码、展开的那一局和传奇筛选（换了账号，这些可能对不上了）。"""
    choice = request.form.get("account", "")
    session["account"] = "all" if choice == "all" else (int(choice) if choice.isdigit() else None)
    base, _, query = request.form.get("next", "").partition("?")
    if base not in [url_for(endpoint) for endpoint, _ in NAV]:
        base = url_for("overview")
    args = {k: v for k, v in parse_qsl(query) if k not in ("page", "open", "detail", "legend")}
    return redirect(base + ("?" + urlencode(args) if args else ""))


@app.post("/accounts/move")
def move_name():
    """设置页"账号"一栏：把一个名字改到别的账号下（account = 账号编号），或拆成单独一个账号（account = "new"）。
    弹窗里选错了，在这里改回来。"""
    name, choice = request.form.get("name", ""), request.form.get("account", "")
    everyone = storage.accounts(db())
    owner = next((a for a in everyone if any(storage.name_key(n) == storage.name_key(name) for n in a["names"])), None)
    if owner is None:
        abort(400)
    if choice == "new":
        if len(owner["names"]) == 1 and owner["confirmed"]:
            flash(f"{name} 本来就是单独一个账号，没有改动", "ok")
        else:
            storage.answer_name(db(), name)
            flash(f"已把 {name} {'拆成' if owner['confirmed'] else '记为'}单独一个账号", "ok")
        return redirect(url_for("settings"))
    target = next((a for a in everyone if a["confirmed"] and str(a["id"]) == choice), None)  # 只能并到已确认的账号
    if target is None:
        flash("没有保存：要并到的账号不存在", "error")
    elif target["id"] == owner["id"]:
        flash(f"{name} 本来就在这个账号下，没有改动", "ok")
    else:
        storage.answer_name(db(), name, target["id"])
        flash(f"已把 {name} 并到 {target['name']} 的账号下", "ok")
    return redirect(url_for("settings"))


@app.post("/accounts/later")
def later_names():
    """弹窗的"稍后再问"（或按 Esc）：这个窗口开着期间不再弹，关掉窗口、下次打开时再问
    （记在会话 cookie 里，关窗口时就没了）。在回答之前，新名字的局单独算一个账号。"""
    session["names_later"] = True
    return back_to_page()


_icon_png = None


def icon_png():
    """Evo 的图标（64×64 PNG）。和托盘、exe 用的是同一个（见 icons.py）。"""
    global _icon_png
    if _icon_png is None:
        from icons import base_icon

        buf = io.BytesIO()
        base_icon(64)[0].save(buf, "PNG")
        _icon_png = buf.getvalue()
    return _icon_png


@app.route("/app-icon.png")
def app_icon():
    """Evo 的图标（窗口标题栏、浏览器标签页用）。"""
    return Response(icon_png(), mimetype="image/png")


@app.route("/export.csv")
def export():
    buf = io.StringIO()
    storage.write_csv(db(), buf)
    filename = f"evo_matches_{date.today():%Y%m%d}.csv"
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def main():
    parser = argparse.ArgumentParser(description="打开本地战绩面板")
    parser.add_argument("--db", type=Path, default=storage.DB_PATH, help="数据库文件（默认 data/evo.db）")
    parser.add_argument("--port", type=int, default=5000, help="端口（默认 5000）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    app.config["DB"] = args.db
    url = f"http://127.0.0.1:{args.port}/"
    print(f"面板地址: {url}   数据库: {args.db.resolve()}")
    print("在终端按 Ctrl+C 关闭面板。\n")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
