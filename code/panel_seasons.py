"""Evo 窗口的"赛季"页面（2026-11-01 用户选的：单独一页，导航在"数据"和"设置"之间）：录入历史赛季数据。

- 上面：录入模式（season_import.py）。没开时"开始录入"按钮和一句说明；开着时倒计时、读到几项、"结束录入"。
  设置页"识别"一节有同样的一行（_season_import.html 里的 import_box）
- 待核对：记录程序读到的（live_collect.SeasonWatcher 存的 pending），每项一张卡片：15 个数的输入框，
  对不上的格子标出来、下面写原因（season_rules.issues），约数标"约"；保存、删除；上面"保存全部没问题的"
- 历史赛季：保存过的，每个赛季一行（局数、胜率、前 5 率、场均击杀、击杀/阵亡比、场均伤害），全部 / 排位切换，
  生涯合计在最上面一行；点开一行能看全部 15 项、修改、删除
- 多账号：只看选中的账号（按名字，同战绩页）；看演示数据时不能开录入模式（读到的会记进真正的数据）
这个文件不加载识别用的库。模板 panel/templates/seasons.html，规则见 docs/面板.md"赛季页"。
"""

from pathlib import Path

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for

import season_import
import storage
from season_rules import DECIMALS, FIELDS, LABELS, issues, rates

bp = Blueprint("seasons", __name__)
account_names = None  # panel_app 设：现在看的账号用过的名字（None = 不分账号），见 panel_app.account_view
KIND_TEXT = {"all": "全部", "ranked": "排位", "career": "生涯合计"}
GRID = (FIELDS[:5], FIELDS[5:10], FIELDS[10:])  # 输入框三行五列


def _db():
    if "db" not in g:
        g.db = storage.connect(current_app.config["DB"])  # panel_app 在请求结束时关掉
    return g.db


def _is_demo():
    return Path(current_app.config["DB"]).name != storage.DB_PATH.name


def _mine(rows):
    """只留现在看的账号的（按名字，不管空格）。"""
    names = account_names() if account_names else None
    if names is None:
        return rows
    keys = {storage.name_key(n) for n in names}
    return [r for r in rows if storage.name_key(r["player_name"]) in keys]


def title(row):
    """"第 30 赛季 · 排位"、"第 29 赛季 · 超频（全部）"、"生涯合计"。"""
    if row["kind"] == "career":
        return "生涯合计"
    if row["kind"] == "ranked":
        return f"第 {row['season']} 赛季 · 排位"
    return f"第 {row['season']} 赛季 · {row['season_name']}（全部）" if row["season_name"] else f"第 {row['season']} 赛季 · 全部"


def show_number(value, field):
    if value is None:
        return ""
    return f"{value:.2f}" if field in DECIMALS else f"{int(value):,}"  # 千位加逗号，和游戏里一样好对照（保存时去掉）


def card(row):
    """一项（待核对或保存过的）在页面上要显示的：标题、15 个格子（值、约数、对不上的原因编号）、原因。"""
    values = {f: row[f] for f in FIELDS}
    approx = set(filter(None, (row["approx"] or "").split(",")))
    found = issues(values, approx)
    marks = {}
    for i, (fields, _) in enumerate(found):
        for f in fields:
            marks.setdefault(f, []).append(f"why-{row['stat_id']}-{i}")
    cells = [[{"field": f, "label": LABELS[f], "value": show_number(values[f], f), "approx": f in approx,
               "why": marks.get(f, []), "decimal": f in DECIMALS} for f in line] for line in GRID]
    read_at = row["read_at"] or ""
    return {"id": row["stat_id"], "title": title(row), "kind": row["kind"], "player": row["player_name"],
            "read_at": read_at[5:16] if len(read_at) >= 16 else read_at, "cells": cells,
            "issues": [{"id": f"why-{row['stat_id']}-{i}", "text": text} for i, (_, text) in enumerate(found)],
            "edited": bool(row["edited"])}


def table_row(row):
    values = {f: row[f] for f in FIELDS}
    approx = set(filter(None, (row["approx"] or "").split(",")))
    r = rates(values)
    return {"id": row["stat_id"], "title": title(row), "kind": row["kind"], "games": values["games"],
            **r, "approx_top5": "top5" in approx or "games" in approx, "player": row["player_name"]}


def mode_view():
    """录入模式现在的样子（设置页、"赛季"页面都用）：开没开、还剩几秒、待核对几项、能不能开（演示数据时不能）。"""
    pending = len(_mine(storage.season_rows(_db(), "pending")))
    return {"active": season_import.active(), "remaining": season_import.remaining(), "minutes": season_import.MINUTES,
            "pending": pending, "demo": _is_demo()}


@bp.app_context_processor
def season_globals():
    return {"season_mode": mode_view}


@bp.route("/seasons")
def page():
    kind = "ranked" if request.args.get("kind") == "ranked" else "all"
    rows = _mine(storage.season_rows(_db()))
    pending = [card(r) for r in rows if r["status"] == "pending"]
    saved = [r for r in rows if r["status"] == "saved"]
    shown = [table_row(r) for r in saved if r["kind"] == kind]
    career = next((table_row(r) for r in saved if r["kind"] == "career"), None)
    try:
        opened = int(request.args.get("open", ""))
    except ValueError:
        opened = None
    detail = next((card(r) for r in saved if r["stat_id"] == opened), None)
    players = sorted({r["player_name"] for r in rows}, key=storage.name_key)
    return render_template("seasons.html", kind=kind, kinds=KIND_TEXT, pending=pending,
                           clean=sum(1 for c in pending if not c["issues"]), shown=shown, career=career,
                           detail=detail, saved_count=len(saved), players=players, mode=mode_view())


@bp.get("/seasons/status")
def status():
    """录入模式开着时页面每隔几秒问一次：还剩几秒、待核对几项（多了就刷新页面）。"""
    view = mode_view()
    return jsonify({"active": view["active"], "remaining": view["remaining"], "pending": view["pending"]})


def _back(anchor=""):
    """回到原来的页面（设置页或"赛季"页面，可以带筛选条件），只允许这两个。"""
    target = request.form.get("next", "")
    if target.partition("?")[0] not in (url_for("settings"), url_for("seasons.page")):
        target = url_for("seasons.page")
    return redirect(target + anchor)


@bp.post("/seasons/import/start")
def start_import():
    if _is_demo():
        flash("正在看演示数据，不能录入（读到的会记进你真正的数据）", "error")
        return _back()
    end = season_import.start()
    flash(f"开始录入（到 {end:%H:%M}）：在游戏里打开数据页，一个一个点右边下拉菜单里的赛季", "ok")
    return _back("#season-import")


@bp.post("/seasons/import/stop")
def stop_import():
    season_import.stop()
    flash("已结束录入", "ok")
    return _back("#season-import")


def _form_values():
    """表单里的 15 个数：{项: 值}（空着为 None）；有填错的返回 (None, 一句话)。"""
    values = {}
    for f in FIELDS:
        text = request.form.get(f, "").strip().replace(",", "").replace("，", "")
        if not text:
            values[f] = None
            continue
        try:
            number = float(text) if f in DECIMALS else int(text)
        except ValueError:
            return None, f"\"{LABELS[f]}\"要填{'数字' if f in DECIMALS else '整数'}（填的是 {text}）"
        if number < 0:
            return None, f"\"{LABELS[f]}\"不能是负数"
        values[f] = round(number, 2) if f in DECIMALS else number
    return values, None


@bp.post("/seasons/<int:stat_id>/save")
def save(stat_id):
    row = _db().execute("SELECT * FROM season_stats WHERE stat_id = ?", (stat_id,)).fetchone()
    if row is None:
        flash("这一项已经不在了（可能刚被删掉）", "error")
        return _back()
    values, problem = _form_values()
    if problem:
        flash(f"没保存（{title(row)}）：{problem}", "error")
        return _back(f"#season-{stat_id}")
    storage.save_season(_db(), stat_id, values)
    flash(f"已保存：{title(row)}", "ok")
    return _back(f"#season-{stat_id}" if row["status"] == "saved" else "#saved")


@bp.post("/seasons/save-clean")
def save_clean():
    """"保存全部没问题的"：待核对里没有对不上的那些，原样保存。"""
    done = 0
    for row in _mine(storage.season_rows(_db(), "pending")):
        if not card(row)["issues"]:
            storage.save_season(_db(), row["stat_id"])
            done += 1
    flash(f"已保存 {done} 项" if done else "没有可以直接保存的（每一项都有对不上的地方，先改一下）", "ok" if done else "error")
    return _back("#saved")


@bp.post("/seasons/<int:stat_id>/delete")
def delete(stat_id):
    row = _db().execute("SELECT * FROM season_stats WHERE stat_id = ?", (stat_id,)).fetchone()
    if row is not None:
        storage.delete_season(_db(), stat_id)
        flash(f"已删除：{title(row)}", "ok")
    return _back()


def when_text(seconds):
    """还剩几秒 → "9:41"。"""
    return f"{seconds // 60}:{seconds % 60:02d}"


bp.add_app_template_filter(when_text, "mmss")
