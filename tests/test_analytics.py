"""Regression checks for local analysis scope, missing values and filter navigation."""

import json
import re
from datetime import datetime, timedelta

from fake_game import report

import analytics
import panel_app
import storage
import telemetry


def main():
    path = storage.DB_PATH.parent / "analytics.db"
    conn = storage.connect(path)
    for i in range(45):
        storage.insert_match(conn, {
            "player_name": "AnalysisPlayer", "ended_at": str(datetime(2026, 10, 1) + timedelta(days=i)),
            "mode": "ranked" if i < 35 else "pubs", "legend": "恶灵" if i % 2 == 0 else "罗芭",
            "map": "世界尽头" if i < 35 else "奥林匹斯", "damage": None if i == 44 else i * 100,
            "kills": i % 6, "placement": None if i == 44 else i % 20 + 1,
            "rp_delta": 10 if i < 35 else None, "needs_review": int(i == 43),
        })
    rows = storage.all_matches(conn)
    conn.close()
    selected, _, _, errors = analytics.select(rows, {"mode": "ranked", "map": "世界尽头", "legend": "恶灵"})
    dated, _, _, _ = analytics.select(rows, {"since": "2026-11-01", "until": "2026-11-02"})
    invalid, _, _, bad = analytics.select(rows, {"since": "invalid"})
    reversed_rows, _, _, reversed_errors = analytics.select(rows, {"since": "2026-11-01", "until": "2026-10-01"})
    sample = dict(rows[-1])
    sample.update(kills=8, rp_delta=200, review_note=panel_app.COUNTED_KILLS_NOTES[0], mode="pubs")
    data = analytics.payload([sample], panel_app.COUNTED_KILLS_NOTES)
    panel_app.app.config.update(DB=path, TESTING=True)
    client = panel_app.app.test_client()
    with client.session_transaction() as session:
        session["account"] = "all"
    page = client.get("/stats?mode=ranked&map=世界尽头").get_data(as_text=True)
    parsed = json.loads(re.search(r'<script type="application/json" data-analysis-json>(.*?)</script>', page, re.S)[1])
    details = client.get("/stats/legends?mode=ranked&map=世界尽头&legend=恶灵&since=2026-10-01&until=2026-10-31").get_data(as_text=True)
    empty = client.get("/stats?since=2099-01-01").get_data(as_text=True)
    checks = {
        "模式、地图、传奇可叠加": len(selected) == 18 and not errors,
        "日期首尾均包含": len(dated) == 2,
        "无效日期明确提示并停止统计": not invalid and bool(bad),
        "逆序日期明确提示并停止统计": not reversed_rows and bool(reversed_errors),
        "经验值页计分次数不当作击杀；匹配不算 RP": data["rows"][0]["kills"] is None and data["rows"][0]["rp_delta"] is None,
        "缺失伤害保留 null": data["rows"][0]["damage"] is None,
        "模式修正后的非排位 RP 不进入表格与总览合计": panel_app.group_numbers([sample])["avg_rp"] == "—" and panel_app.rp_sum([sample])["good"] is None,
        "图表数据不包含名字、原始备注和截图": not ({"player_name", "review_note", "evidence_path"} & data["rows"][0].keys()),
        "先筛选排位，再取近 30 局": len(parsed["rows"]) == 30 and all(r["mode"] == "排位" for r in parsed["rows"]) and parsed["rows"][0]["damage"] == 500,
        "历史对局的图表跳转定位到正确分页": parsed["rows"][0]["page"] == 2 and f'id="m{parsed["rows"][0]["id"]}"' in client.get(f'/matches?page=2&open={parsed["rows"][0]["id"]}').get_data(as_text=True),
        "卡片明细链接保留筛选": 'mode=ranked' in page and 'map=' in page and '/stats/legends?' in page,
        "完整表格返回链接保留全部筛选": re.search(r'class="back-link"', details) is not None and all(f'{key}=' in details for key in ("mode", "map", "legend", "since", "until")),
        "筛选无结果保留控件和空状态": '这个筛选下还没有对局' in empty and 'name="since"' in empty and 'data-analysis' in empty,
        "既有总览筛选仍可使用": client.get('/?mode=ranked&legend=恶灵').status_code == 200,
    }
    conn = storage.connect(path)
    first = storage.get_match(conn, rows[0]["match_id"])
    form = {key: "" if first[key] is None else str(first[key]) for key in panel_app.EDITABLE}
    form["token"] = panel_app.app.config["TOKEN"]
    client.post(f'/matches/{first["match_id"]}/update', data={**form, "mode": "pubs", "map": "奥林匹斯"})
    changed = storage.get_match(conn, first["match_id"])
    checks["允许手动修正模式、地图并保留其他指标"] = changed["mode"] == "pubs" and changed["map"] == "奥林匹斯" and changed["damage"] == first["damage"]
    checks["模式、地图修正不向旧统计协议发送未知字段名"] = telemetry.match_payload(conn, changed, "test")["edited"] == []
    client.post(f'/matches/{first["match_id"]}/update', data={**form, "map": "不存在的地图"})
    checks["拒绝不在配置中的新地图选项"] = storage.get_match(conn, first["match_id"])["map"] == "奥林匹斯"
    storage.update_match(conn, first["match_id"], {"map": "历史地图"})
    client.post(f'/matches/{first["match_id"]}/update', data={**form, "map": "历史地图"})
    checks["配置名单之外的历史值可原样保存"] = storage.get_match(conn, first["match_id"])["map"] == "历史地图"
    legacy_page = client.get(f'/matches?page=3&open={first["match_id"]}&detail=1').get_data(as_text=True)
    checks["修改其他字段时不会清空历史地图"] = '<option value="历史地图" selected>' in legacy_page
    conn.close()
    line = {"key": "test", "name": "样本", "spark": True, "rp_steps": [{"total": i + 1, "delta": 1, "when": ""} for i in range(5)],
            "rp_total": "+5", "rp_total_class": "good"}
    chart = panel_app.curve_chart([line], [{"mode": "ranked", "rp_delta": 500}, {"mode": "pubs", "rp_delta": -9999}], ["test"])
    checks["队友参照曲线仅使用排位且不会超出纵轴"] = chart["ref"]["avg"] == "+500" and 0 <= chart["ref"]["y2"] <= chart["h"]
    # Explicit scope check: another account's huge values never reach the local chart.
    conn = storage.connect(path)
    account = storage.accounts(conn)[0]["id"]
    storage.insert_match(conn, {"player_name": "AnotherPlayer", "ended_at": "2026-11-20 00:00:00", "damage": 999999})
    storage.answer_name(conn, "AnotherPlayer")
    conn.close()
    with client.session_transaction() as session:
        session["account"] = account
    scoped = client.get('/stats?range=all').get_data(as_text=True)
    checks["账号边界：其他账号的对局不进入图表"] = '999999' not in scoped
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
