"""Regression checks for local analysis scope, missing values and filter navigation."""

import json
import re
from datetime import datetime, timedelta

from fake_game import report

import analytics
import panel_app
import storage
import telemetry


def combat_checks(row):
    base = {**dict(row), "kills": 2, "assists": 3, "knockdowns": 7, "damage": 900, "survival_time": 600,
            "squad_kills": 10, "rank_tier": "黄金", "mode": "ranked", "review_note": None}
    mates = [{"match_id": base["match_id"], "slot": "left", "name": "甲", "player_id": "1", "kills": 5, "damage": 600},
             {"match_id": base["match_id"], "slot": "right", "name": "乙", "player_id": "2", "kills": 3, "damage": 300}]

    def out(r=base, ms=mates):
        return analytics.payload([r], panel_app.COUNTED_KILLS_NOTES, teammates=ms)["rows"][0]

    normal = out()
    fallback = out({**base, "squad_kills": None})
    partial = out({**base, "squad_kills": None}, mates[:1])
    duplicate = out({**base, "squad_kills": None}, [mates[0], {**mates[1], "player_id": "1"}])
    zeros = out({**base, "kills": 0, "assists": 0, "damage": 0, "squad_kills": 0, "survival_time": 0},
                [{**m, "damage": 0, "kills": 0} for m in mates])
    counted = out({**base, "review_note": panel_app.COUNTED_KILLS_NOTES[0]})
    inconsistent = out({**base, "kills": 11, "assists": 5})
    missing = out({**base, "assists": None})
    return {
        "战斗指标：K+A 不叠加击倒": normal["ka"] == 5 and normal["knockdowns"] == 7,
        "按存活时间归一化：900伤害/10分钟=90；K+A=5；击倒=7": normal["damage_per_min"] == 90 and normal["ka_per10"] == 5 and normal["knocks_per10"] == 7,
        "小队占比：K+A参与50%、击杀占比20%、伤害占比50%": normal["kill_participation"] == 50 and normal["kill_share"] == 20 and normal["damage_share"] == 50,
        "小队击杀缺失：只有本人和两名完整队友才补足分母": fallback["squad_kills"] == 10 and fallback["squad_source"] == "本人及两名队友击杀合计",
        "不完整队友：不估算全队击杀或伤害占比": partial["squad_kills"] is None and partial["damage_share"] is None,
        "重复识别的队友不能当作两个人": duplicate["squad_kills"] is None and duplicate["damage_share"] is None,
        "同名但ID不同的真实队友仍是两个人": out(base, [mates[0], {**mates[1], "name": "甲"}])["damage_share"] == 50,
        "零分母留空，真实零K+A仍保留": zeros["damage_per_min"] is None and zeros["kill_participation"] is None and zeros["damage_share"] is None and zeros["ka"] == 0,
        "经验值页的击杀和助攻计分次数都不作实际战斗数据": counted["kills"] is None and counted["assists"] is None and counted["ka"] is None and counted["knockdowns"] == 7 and panel_app.group_numbers([{**base, "review_note": panel_app.COUNTED_KILLS_NOTES[0]}])["avg_assists"] == "—",
        "矛盾占比留空而不是截断成100%": inconsistent["kill_participation"] is None and inconsistent["kill_share"] is None,
        "助攻缺失时不把它作0，击倒仍可独立展示": missing["ka"] is None and missing["knockdowns"] == 7,
        "记录段位可用于比较，不计算不存在的死亡数和转化率": normal["rank_tier"] == "黄金" and not {"deaths", "kd", "conversion"} & normal.keys(),
        "小队分析不把队友姓名和ID传到浏览器": not {"name", "player_id", "teammates"} & normal.keys(),
        "非排位不沿用历史段位；异常名次不进入名次分析": out({**base, "mode": "pubs", "placement": 0})["rank_tier"] == "非排位 / 未识别模式" and out({**base, "placement": 21})["placement"] is None,
    }


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
    checks.update(combat_checks(rows[0]))
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
