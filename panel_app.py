"""本地面板：用浏览器查看和修正战绩。只在本机（127.0.0.1）运行，不联网。

运行：python panel_app.py                    读正式数据库 data/evo.db
演示：python panel_app.py --db data/demo.db   读伪数据（先运行 python demo_data.py 生成）
启动后会自动打开浏览器；在终端按 Ctrl+C 关闭面板。
"""

import argparse
import io
import math
import secrets
import threading
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from flask import Flask, Response, abort, flash, g, redirect, render_template, request, send_file, url_for

import storage

PER_PAGE = 20
MODES = {"ranked": "排位", "pubs": "匹配", "other": "其他"}
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

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, template_folder="panel/templates", static_folder="panel/static")
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
        "token": app.config["TOKEN"],
        "is_demo": Path(app.config["DB"]).name != storage.DB_PATH.name,
        "db_name": Path(app.config["DB"]).name,
    }


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
        "kda": " / ".join(dash(row[k]) for k in ("kills", "assists", "knockdowns")),
        "damage": dash(row["damage"], "{:,}"),
        "survival": storage.mmss(row["survival_time"]).replace("-", "—"),
        "revives": dash(row["revives"]),
        "respawns": dash(row["respawns"]),
        "squad_kills": dash(row["squad_kills"]),
        "rp": "—" if rp is None else (f"+{rp}" if rp >= 0 else f"−{-rp}"),
        "rp_class": "none" if rp is None else ("good" if rp >= 0 else "bad"),
        "needs_review": bool(row["needs_review"]),
        "review_note": row["review_note"],
        "has_evidence": bool(row["evidence_path"]),
        "form": {name: "" if row[name] is None else row[name] for name in EDITABLE},
        "warn_fields": warn_fields(row["review_note"]) if row["needs_review"] else set(),
    }


# ---------- 页面 ----------

@app.route("/")
def index():
    return redirect(url_for("matches"))


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
    path = Path(row["evidence_path"])
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        abort(404)
    return send_file(path)


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
