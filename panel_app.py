"""本地面板：用浏览器查看和修正战绩。只在本机（127.0.0.1）运行；唯一会联网的是"检查更新"（update_check.py，可以关掉）。

运行：python panel_app.py                    读正式数据库 data/evo.db
演示：python panel_app.py --db data/demo.db   读伪数据（先运行 python demo_data.py 生成）
启动后会自动打开浏览器；在终端按 Ctrl+C 关闭面板。
"""

import argparse
import ctypes
import io
import json
import math
import os
import secrets
import threading
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

from flask import Flask, Response, abort, flash, g, redirect, render_template, request, send_file, url_for

import app_settings
import storage
import update_check
from paths import CAPTURES_DIR, CONFIG_DIR, PANEL_DIR, data_absolute
from post_match import XP_ONLY_NOTE

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
}

# ---- 总览页 ----
_XP_CFG = json.loads((CONFIG_DIR / "xp.json").read_text(encoding="utf-8"))
TIERS, DIVISIONS = _XP_CFG["tiers"], _XP_CFG["divisions"]  # 段位从低到高；每个段位里的段从低到高（IV → I）
DIVISION_POINTS = 1000  # 每段的排位点数（经验值页进度条的终点）
TIER_BADGE = {"菜鸟": ("R", "rookie"), "青铜": ("B", "bronze"), "白银": ("S", "silver"), "黄金": ("G", "gold"),
              "白金": ("P", "platinum"), "钻石": ("D", "diamond"), "大师": ("M", "master"), "Apex 猎杀者": ("A", "predator")}
DAY_START_HOUR = 5  # "今日"从早上 5 点算起：打到凌晨的局仍算前一天
RANGES = {"30": "近 30 局", "all": "全部"}
CHART_W, CHART_H = 600, 200  # 走势图的坐标范围（网页上会按卡片宽度拉伸）

# ---- 设置页 ----
SOURCE_URL = "https://github.com/AugustHuangMX/Evo"

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


@app.context_processor
def template_globals():
    return {
        # 在 Evo 自己的窗口里打开（app_window.py 会在 User-Agent 里带上 EvoApp）：显示自定义标题栏
        "in_app": "EvoApp/" in request.headers.get("User-Agent", ""),
        "token": app.config["TOKEN"],
        "is_demo": Path(app.config["DB"]).name != storage.DB_PATH.name,
        "db_name": Path(app.config["DB"]).name,
        "nav": NAV,
        "switch": page_switch(),
        # 检查更新：还没问过要不要自动检查 → 页面顶部问一次；已知有新版本 → 顶部提示条
        "ask_update": app_settings.get("update_auto") is None,
        "update_offer": update_check.available(),
        "version": storage.APP_VERSION,
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
    """在当前网址的筛选条件基础上，改动其中几项（值为 None 表示去掉这一项）。"""
    args = request.args.to_dict()
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
        "placement": dash(row["placement"], "#{}"),
        "badge": badge_class(row["placement"]),
        "time": time_text(row["ended_at"]),
        "mode": MODES.get(row["mode"], "—"),
        "map": row["map"] or "—",
        "legend": row["legend"] or "—",
        # 总览页"最近对局"的标题和副标题：有什么显示什么
        "title": " · ".join(x for x in (row["legend"], row["map"]) if x) or "传奇、地图未识别",
        "subtitle": " · ".join(x for x in (MODES.get(row["mode"]), time_text(row["ended_at"])) if x),
        "kda": " / ".join(dash(row[k]) for k in ("kills", "assists", "knockdowns")),
        "damage": dash(row["damage"], "{:,}"),
        "survival": storage.mmss(row["survival_time"]).replace("-", "—"),
        "revives": dash(row["revives"]),
        "respawns": dash(row["respawns"]),
        "squad_kills": dash(row["squad_kills"]),
        "rp": "—" if rp is None else signed(rp),
        "rp_class": "none" if rp is None else ("good" if rp >= 0 else "bad"),
        "needs_review": bool(row["needs_review"]),
        "review_note": row["review_note"],
        "has_evidence": bool(row["evidence_path"]),
        "form": {name: "" if row[name] is None else row[name] for name in EDITABLE},
        "warn_fields": warn_fields(row["review_note"]) if row["needs_review"] else set(),
    }


# ---------- 总览页的统计 ----------

def rank_score(row):
    """把段位换算成一条连续的分数线（每段 1000 分），用来画走势图。没有段位数据返回 None。"""
    tier, division, progress = row["rank_tier"], row["rank_division"], row["rank_progress"]
    if tier not in TIERS or division not in DIVISIONS or progress is None:
        return None
    return (TIERS.index(tier) * len(DIVISIONS) + DIVISIONS.index(division)) * DIVISION_POINTS + progress


def next_division(tier, division):
    """下一段的名字：钻石 II → 钻石 I；钻石 I → 大师。"""
    d = DIVISIONS.index(division)
    if d + 1 < len(DIVISIONS):
        return f"{tier} {DIVISIONS[d + 1]}"
    t = TIERS.index(tier)
    return TIERS[t + 1] if t + 1 < len(TIERS) else None


def rank_card(rows):
    """最近一次读到的段位。"""
    row = next((r for r in reversed(rows) if rank_score(r) is not None), None)
    if row is None:
        return None
    letter, css = TIER_BADGE.get(row["rank_tier"], ("?", "unknown"))
    progress = row["rank_progress"]
    return {
        "badge": f"{letter}{len(DIVISIONS) - DIVISIONS.index(row['rank_division'])}",
        "tier_class": css,
        "name": f"{row['rank_tier']} {row['rank_division']}",
        "progress": progress,
        "division_points": DIVISION_POINTS,
        "percent": min(100, progress / DIVISION_POINTS * 100),
        "next_name": next_division(row["rank_tier"], row["rank_division"]),
        "to_next": max(0, DIVISION_POINTS - progress),
        "as_of": time_text(row["ended_at"]),
    }


def division_name(score):
    """分数线上从 score 开始的是哪一段：如 钻石 IV 的起点 → "钻石 IV"。"""
    t, d = divmod(score // DIVISION_POINTS, len(DIVISIONS))
    return f"{TIERS[t]} {DIVISIONS[d]}" if 0 <= t < len(TIERS) else ""


def rp_chart(rows):
    """RP 走势图：每局结束后的段位换算成分数，连成折线；范围内的段位分界线画成虚线。
    有段位的局少于 2 局时返回 None。"""
    points = [s for s in (rank_score(r) for r in rows) if s is not None]
    if len(points) < 2:
        return None
    low, high = min(points), max(points)
    target = (points[-1] // DIVISION_POINTS + 1) * DIVISION_POINTS  # 下一段的起点
    if target - high <= max(200, high - low):  # 快升段了：把下一段的分数线也画进来；离得远就不画，免得折线被压扁
        high = max(high, target)
    pad = max(20, (high - low) * 0.1)
    low, high = low - pad, high + pad

    def y(score):
        return CHART_H - (score - low) / (high - low) * CHART_H

    first_line = math.ceil(low / DIVISION_POINTS) * DIVISION_POINTS
    lines = [{"y": round(y(b), 1), "top": round(y(b) / CHART_H * 100, 1), "name": division_name(b)}
             for b in range(first_line, int(high) + 1, DIVISION_POINTS)]
    xs = [i / (len(points) - 1) * CHART_W for i in range(len(points))]
    line = " ".join(f"{x:.1f},{y(s):.1f}" for x, s in zip(xs, points))
    return {
        "w": CHART_W,
        "h": CHART_H,
        "line": line,
        "area": f"0,{CHART_H} {line} {CHART_W},{CHART_H}",
        "lines": lines,
        "end_top": round(y(points[-1]) / CHART_H * 100, 1),
        "count": len(points),
    }


def stat_cards(rows):
    placements = [r["placement"] for r in rows if r["placement"] is not None]
    # 只有经验值页的局，击杀是"计分次数"（可能比实际少），不算进场均击杀
    kills = [r["kills"] for r in rows if r["kills"] is not None and XP_ONLY_NOTE not in (r["review_note"] or "")]
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
            "games": len(rs),
            "avg_damage": f"{sum(damages) / len(damages):,.0f}" if damages else None,
            "segments": [(css, n / widest * 100) for css, n in buckets if n],
            "other": name == "其他",
        })
    return bars


def day_start(now):
    start = now.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
    return start if now >= start else start - timedelta(days=1)


# ---------- 页面 ----------

@app.route("/")
def overview():
    range_key = request.args.get("range") if request.args.get("range") in RANGES else "30"
    rows = storage.all_matches(db())
    in_range = rows[-30:] if range_key == "30" else rows
    since = f"{day_start(datetime.now()):%Y-%m-%d %H:%M:%S}"
    today = [r for r in rows if (r["ended_at"] or "") >= since]

    return render_template(
        "overview.html",
        range_key=range_key,
        ranges=RANGES,
        rank=rank_card(rows),
        chart=rp_chart(in_range),
        range_rp=rp_sum(in_range),
        today_rp=rp_sum(today),
        today_games=len(today),
        stats=stat_cards(in_range),
        recent=[view(r) for r in reversed(rows[-5:])],
        legends=legend_bars(in_range),
        review_count=storage.count_review(db()),
        total=len(rows),
    )


@app.route("/matches")
def matches():
    mode = request.args.get("mode") if request.args.get("mode") in MODES else None
    only_review = request.args.get("review") == "1"
    legend = request.args.get("legend") or None
    page = max(1, request.args.get("page", 1, type=int))
    open_id = request.args.get("open", type=int)

    rows, total = storage.query_matches(db(), mode, only_review, legend, PER_PAGE, (page - 1) * PER_PAGE)
    rows = [view(r) for r in rows]
    if only_review and open_id is None and rows:
        open_id = rows[0]["id"]  # "只看待确认"时，自动展开第一局

    return render_template(
        "matches.html",
        rows=rows,
        total=total,
        page=page,
        pages=max(1, math.ceil(total / PER_PAGE)),
        first=(page - 1) * PER_PAGE + 1 if total else 0,
        last=min(page * PER_PAGE, total),
        mode=mode,
        modes=MODES,
        only_review=only_review,
        legend=legend,
        legends=storage.distinct_values(db(), "legend"),
        open_id=open_id,
        review_count=storage.count_review(db()),
        editable=EDITABLE,
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

    fields["needs_review"] = 0  # 人工确认过了，不再标记待确认（review_note 保留，记录当初为什么待确认）
    storage.update_match(db(), match_id, fields)
    flash(f"已保存第 {match_id} 号对局", "ok")
    return back_to_list()


@app.post("/matches/<int:match_id>/delete")
def delete(match_id):
    storage.delete_match(db(), match_id)
    flash(f"已删除第 {match_id} 号对局", "ok")
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
    size = screen_size()
    return render_template(
        "settings.html",
        resolution=f"{size[0]} × {size[1]}" if size else None,
        data_dir=db_path().parent,
        db_file=db_path().name,
        db_size=size_text(db_path().stat().st_size) if db_path().exists() else "0 KB",
        total=storage.query_matches(db(), limit=1)[1],
        review_count=storage.count_review(db()),
        captures_dir=CAPTURES_DIR,
        shot_count=shot_count,
        shot_size=size_text(shot_bytes),
        version=storage.APP_VERSION,
        source_url=SOURCE_URL,
        update_auto=app_settings.get("update_auto") is True,
        last_check=app_settings.get("update_last_check"),
        newest=update_check.known_newer(),
    )


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


# ---------- 检查更新（update_check.py；Evo 唯一会联网的功能） ----------

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
        # 马上在后台查一次（最多几秒），页面不用等；托盘程序之后每天查一次
        threading.Thread(target=update_check.check, name="update-check-now", daemon=True).start()
        flash("已开启自动检查：Evo 每天联网检查一次有没有新版本。", "ok")
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


_icon_png = None


@app.route("/app-icon.png")
def app_icon():
    """Evo 的图标（窗口标题栏、浏览器标签页用）。和托盘、exe 用的是同一个（见 icons.py）。"""
    global _icon_png
    if _icon_png is None:
        from icons import base_icon

        buf = io.BytesIO()
        base_icon(64)[0].save(buf, "PNG")
        _icon_png = buf.getvalue()
    return Response(_icon_png, mimetype="image/png")


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
