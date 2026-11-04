"""生成伪数据，用来演示面板。写到 data/demo.db，不碰正式数据库 data/evo.db。

数字是随机生成的，但尽量像真的：伤害和击杀相关、名次越好生存时间越长、
排位局按"名次分 + 击杀分 − 入场费"算 RP。每次运行结果都一样（固定了随机种子）。

运行：python tools/demo_data.py
然后：python code/panel_app.py --db data/demo.db
"""

import random
from datetime import datetime, timedelta

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
import storage
from paths import DB_DIR, data_absolute

DEMO_DB = DB_DIR / "demo.db"
SAMPLE_SUMMARY = "captures/samples/vlcsnap-2026-10-16-18h26m12s173.png"  # 有就用作"识别依据"截图（相对于数据文件夹）

LEGENDS = ["恶灵", "恶灵", "恶灵", "寻血猎犬", "寻血猎犬", "动力小子", "罗芭", "地平线"]
MAPS = ["世界尽头", "风暴点", "奥林匹斯", "诸王峡谷", "残月"]
ENTRY_COST = 65
PLACEMENT_RP = {1: 125, 2: 95, 3: 70, 4: 55, 5: 45, 6: 30, 7: 20, 8: 20, 9: 10, 10: 10}
DIVISIONS = ["IV", "III", "II", "I"]


def fake_match(rng, ended_at):
    placement = min(20, max(1, int(rng.triangular(1, 20, 8))))
    kills = max(0, int(rng.gauss(7 - placement * 0.3, 2.2)))
    assists = max(0, int(rng.gauss(kills * 0.8, 1.5)))
    knockdowns = max(kills, int(rng.gauss(kills * 1.2, 1)))
    damage = max(0, int(rng.gauss(180 + kills * 260, 180)))
    survival = int(max(90, rng.gauss(1250 - placement * 55, 120)))
    ranked = rng.random() < 0.8
    return {
        "ended_at": f"{ended_at:%Y-%m-%d %H:%M:%S}",
        "mode": "ranked" if ranked else "pubs",
        "map": rng.choice(MAPS),
        "legend": rng.choice(LEGENDS),
        "placement": placement,
        "squad_kills": kills + rng.randint(0, 8),
        "kills": kills,
        "assists": assists,
        "knockdowns": knockdowns,
        "damage": damage,
        "survival_time": survival,
        "revives": rng.randint(0, 3),
        "respawns": rng.choice([0, 0, 0, 1]),
        "rp_delta": PLACEMENT_RP.get(placement, 0) + min(kills + assists // 2, 6) * 14 - ENTRY_COST if ranked else None,
        "rp_source": "演示数据" if ranked else None,
    }


def main():
    if DEMO_DB.exists():
        DEMO_DB.unlink()  # 只删除演示数据库，每次重新生成
    conn = storage.connect(DEMO_DB)
    rng = random.Random(2026)

    # 最近 4 天，每天打一段 8–14 局，每局间隔 20–30 分钟；最后一段刚结束不久（总览页的"今日"有数据）
    now = datetime.now().replace(second=0, microsecond=0)
    matches = []
    for days_ago in range(3, -1, -1):
        games = rng.randint(8, 14)
        t = now - timedelta(days=days_ago, minutes=10 + games * 30)
        for _ in range(games):
            t += timedelta(minutes=rng.randint(20, 30))
            matches.append(fake_match(rng, t))

    # 段位：从 钻石 IV 149 分开始，按每局 RP 累加（每段 1000 分）
    total = 149
    for m in matches:
        if m["rp_delta"] is not None:
            total = max(0, total + m["rp_delta"])
        m.update(rank_tier="钻石", rank_division=DIVISIONS[min(total // 1000, 3)], rank_progress=total % 1000)

    # 挑 3 局做成"待确认"，演示面板里的修正流程
    has_sample = data_absolute(SAMPLE_SUMMARY).is_file()
    for m, note in zip(
        rng.sample(matches, 3),
        ["击杀/助攻/击倒：格式不对（OCR 原文：5/717）", "伤害：置信度低（OCR 原文：1,2S0）", "名次：没读到（OCR 原文：无）"],
    ):
        m.update(needs_review=1, review_note=note, evidence_path=SAMPLE_SUMMARY if has_sample else None)
        field = note.split("：")[0]
        if field == "击杀/助攻/击倒":
            m.update(kills=None, assists=None, knockdowns=None)
        elif field == "伤害":
            m["damage"] = None
        elif field == "名次":
            m["placement"] = None

    for m in matches:
        storage.insert_match(conn, {**m, "app_version": "demo"})
    print(f"已生成 {len(matches)} 局伪数据（其中 3 局待确认）→ {DEMO_DB.resolve()}")


if __name__ == "__main__":
    main()
