"""看匿名统计（用户同意后 Evo 发来的数据，见 code/telemetry.py、server/stats/）：从 Cloudflare D1 数据库 evo-stats
读汇总，打印在终端。只在开发电脑上用，要 wrangler（已经 wrangler login）。

- 日活：每天有多少台电脑开着 Evo（ping）、有多少台打了对局
- 版本：最近 7 天在用的 Evo 版本
- 传奇：自己用的传奇（局数、选用率、平均名次、场均击杀和伤害、冠军数），队友用的传奇
- 武器：自己用的武器（拿过的局数、占拿武器时间的多少；只算每局第一次拿到武器 60 秒以后的，0.4.1 以后的 Evo 才发）
- 段位：排位赛里各段位有多少人、多少局
- 识别情况：待确认的比例、每一项没读出来的比例、记下了武器的比例、用户改过哪几项、RP 从哪来、分辨率

用法：python tools/stats_report.py              最近 30 天
      python tools/stats_report.py --days 7
      python tools/stats_report.py --local      读 wrangler dev 用的本机数据库（测试 worker.js 时用）
"""

import argparse
import json
import shutil
import subprocess
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "stats"
DATABASE = "evo-stats"
EDITABLE = {"placement": "名次", "kills": "击杀", "assists": "助攻", "knockdowns": "击倒", "participation": "老板分",
            "damage": "伤害", "rp_delta": "RP"}
WEAPON_NAMES = {k: v["zh"] for k, v in json.loads((ROOT / "config" / "weapons.json").read_text(encoding="utf-8"))["weapons"].items()}
READ_FIELDS = {"legend": "传奇", "placement": "名次", "kills": "击杀", "damage": "伤害", "rp_delta": "RP",
               "tier": "段位", "map": "地图", "mode": "模式"}


def query(statements, local):
    """一次跑几条 SQL（wrangler 每次要几秒，所以放在一起），返回每条的结果（字典的列表）。"""
    exe = shutil.which("wrangler")
    if not exe:
        sys.exit("找不到 wrangler：先装 Node.js 和 Wrangler（npm install -g wrangler），再运行 wrangler login")
    r = subprocess.run([exe, "d1", "execute", DATABASE, "--local" if local else "--remote", "--json",
                        "--command", "; ".join(statements)],  # 写在一行：wrangler 是 .cmd，参数里的换行会把后面截掉
                       cwd=SERVER, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"读不到统计数据库：\n{r.stdout}\n{r.stderr}")
    return [block["results"] for block in json.loads(r.stdout)]


def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def table(title, header, rows):
    """打印一张表：中文按两个字符宽对齐。"""
    print(f"\n== {title}")
    if not rows:
        print("  （没有数据）")
        return
    cells = [[str(c) for c in header]] + [["-" if c is None else str(c) for c in row] for row in rows]
    widths = [max(width(row[i]) for row in cells) for i in range(len(header))]
    for row in cells:
        print("  " + "  ".join(c + " " * (w - width(c)) for c, w in zip(row, widths)))


def pct(part, whole):
    return f"{part / whole:.0%}" if whole else "-"


def num(value, digits=1):
    return "-" if value is None else f"{value:.{digits}f}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="看 Evo 的匿名统计")
    parser.add_argument("--days", type=int, default=30, help="看最近几天（默认 30）")
    parser.add_argument("--local", action="store_true", help="读本机的测试数据库（wrangler dev）")
    args = parser.parse_args()
    since = (date.today() - timedelta(days=args.days - 1)).isoformat()  # 日期是自己算的，可以直接写进 SQL
    week = (date.today() - timedelta(days=6)).isoformat()
    m = f"FROM matches WHERE day >= '{since}'"

    (pings, players, versions, legends, mates, weapons, tiers, totals, edited, sources, screens) = query([
        f"SELECT day, COUNT(*) AS n FROM pings WHERE day >= '{since}' GROUP BY day",
        f"SELECT day, COUNT(DISTINCT id) AS n, COUNT(*) AS games {m} GROUP BY day",
        f"SELECT version, COUNT(DISTINCT id) AS n FROM pings WHERE day >= '{week}' GROUP BY version ORDER BY n DESC",
        f"SELECT legend, COUNT(*) AS n, AVG(placement) AS place, AVG(kills) AS kills, AVG(damage) AS damage, "
        f"SUM(placement = 1) AS wins {m} AND legend IS NOT NULL GROUP BY legend ORDER BY n DESC",
        f"SELECT legend, COUNT(*) AS n FROM (SELECT mate1 AS legend {m} UNION ALL SELECT mate2 {m}) "
        f"WHERE legend IS NOT NULL GROUP BY legend ORDER BY n DESC",
        f"SELECT w.key AS weapon, COUNT(*) AS n, SUM(w.value) AS s FROM matches, json_each(matches.weapons) AS w "
        f"WHERE day >= '{since}' GROUP BY weapon ORDER BY s DESC",
        f"SELECT tier, COUNT(DISTINCT id) AS players, COUNT(*) AS n {m} AND mode = 'ranked' AND tier IS NOT NULL "
        f"GROUP BY tier ORDER BY n DESC",
        "SELECT COUNT(*) AS n, COUNT(DISTINCT id) AS players, SUM(review) AS review, "
        "SUM(edited IS NOT NULL) AS edited, SUM(weapons IS NOT NULL) AS with_weapons, "
        + ", ".join(f"SUM({f} IS NULL) AS no_{f}" for f in READ_FIELDS) + f" {m}",
        f"SELECT edited, COUNT(*) AS n {m} AND edited IS NOT NULL GROUP BY edited",
        f"SELECT rp_source, COUNT(*) AS n {m} AND mode = 'ranked' GROUP BY rp_source ORDER BY n DESC",
        f"SELECT screen, game_res, game_mode, COUNT(DISTINCT id) AS n FROM pings WHERE day >= '{since}' "
        f"GROUP BY screen, game_res, game_mode ORDER BY n DESC",
    ], args.local)

    print(f"Evo 匿名统计（{'本机测试数据库' if args.local else 'Cloudflare D1'}，{since} 起，日期是用户电脑上的）")
    played = {r["day"]: r for r in players}
    days = sorted({r["day"] for r in pings} | set(played))
    on = {r["day"]: r["n"] for r in pings}
    table("日活", ("日期", "开着 Evo", "打了对局", "对局数"),
          [(d, on.get(d, 0), played.get(d, {}).get("n", 0), played.get(d, {}).get("games", 0)) for d in days])
    table("最近 7 天在用的版本", ("版本", "电脑数"), [(r["version"], r["n"]) for r in versions])

    total = totals[0]["n"] if totals else 0
    picked = sum(r["n"] for r in legends)
    table(f"自己用的传奇（{picked} 局认出了传奇）", ("传奇", "局数", "选用率", "平均名次", "场均击杀", "场均伤害", "冠军"),
          [(r["legend"], r["n"], pct(r["n"], picked), num(r["place"]), num(r["kills"]), num(r["damage"], 0), r["wins"])
           for r in legends])
    seen = sum(r["n"] for r in mates)
    table(f"队友用的传奇（{seen} 人次）", ("传奇", "人次", "占比"), [(r["legend"], r["n"], pct(r["n"], seen)) for r in mates])
    t = totals[0] if totals else {}
    held = sum(r["s"] for r in weapons)
    table(f"自己用的武器（{t.get('with_weapons') or 0} 局记下了武器，一共拿了 {held / 3600:.1f} 小时）",
          ("武器", "拿过的局", "占拿武器的时间"),
          [(WEAPON_NAMES.get(r["weapon"], r["weapon"]), r["n"], pct(r["s"], held)) for r in weapons])
    table("排位赛的段位", ("段位", "人数", "局数"), [(r["tier"], r["players"], r["n"]) for r in tiers])

    print(f"\n== 识别情况（{total} 局，{t.get('players') or 0} 台电脑）")
    if total:
        print(f"  待确认 {pct(t['review'] or 0, total)}，用户改过 {pct(t['edited'] or 0, total)}，"
              f"记下了武器 {pct(t['with_weapons'] or 0, total)}（0.4.1 以前的 Evo 不发武器）")
        print("  没读出来：" + "，".join(f"{label} {pct(t['no_' + f] or 0, total)}" for f, label in READ_FIELDS.items()))
    counts = {}
    for r in edited:
        for f in r["edited"].split(","):
            counts[f] = counts.get(f, 0) + r["n"]
    table("用户改过的项", ("项", "局数", "占全部"),
          [(EDITABLE.get(f, f), n, pct(n, total)) for f, n in sorted(counts.items(), key=lambda kv: -kv[1])])
    ranked = sum(r["n"] for r in sources)
    table("排位赛的 RP 从哪来", ("来源", "局数", "占比"),
          [(r["rp_source"] or "没有 RP", r["n"], pct(r["n"], ranked)) for r in sources])
    table("分辨率（电脑数）", ("屏幕", "Apex 设置", "显示模式", "电脑数"),
          [(r["screen"], r["game_res"], r["game_mode"], r["n"]) for r in screens])


if __name__ == "__main__":
    main()
