"""看"赛季"页面的样子（开发工具）：用假数据开一个面板，不碰正式数据。

在一个临时文件夹里生成演示战绩（同 tools/feature_cards.py，游戏名是编的 EvoPlayer）和假的历史赛季：
保存过的第 20–29 赛季（全部、排位各一行）和生涯合计；待核对的第 30 赛季两项（"全部"那项的击杀/阵亡比故意写错，
看对不上的格子怎么标）。然后开面板、用浏览器打开"赛季"页面。页面上的按钮都能点（开始 / 结束录入、保存、删除），
改的都是临时文件夹里的数据；这里没有记录程序，开始录入以后不会真的读到东西。数字是随机编的（固定了随机种子）。

运行：python tools/demo_seasons.py   （按 Ctrl+C 关掉，临时文件夹自动删掉）
"""

import os
import random
import shutil
import socket
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）

NAMES = {30: "诸神烙印", 29: "超频", 28: "全面击破", 27: "超能强化", 26: "决战", 25: "金童降世", 24: "侵略",
         23: "源自裂隙", 22: "震撼波动", 21: "扭转乾坤", 20: "突破"}


def season(rng, games):
    """一个赛季（或一项）的 15 个数，像真的：胜率 5–9%、前 5 率 25–32%、场均伤害 400–560……互相对得上。"""
    wins = round(games * rng.uniform(0.05, 0.09))
    kills = round(games * rng.uniform(0.9, 1.4))
    deaths = round(games * rng.uniform(0.9, 1.05))
    damage = round(games * rng.uniform(400, 560))
    return {"games": games, "wins": wins, "top5": round(games * rng.uniform(0.25, 0.32)), "damage": damage,
            "damage_max": rng.randint(2000, 4500), "damage_avg": round(damage / games, 2), "kills": kills,
            "deaths": deaths, "kd": round(kills / deaths, 2), "kills_max": rng.randint(7, 15),
            "knockdowns": round(kills * rng.uniform(1.2, 1.4)), "assists": round(kills * rng.uniform(0.8, 1.0)),
            "win_streak": rng.randint(1, 4), "revives": round(games * rng.uniform(0.25, 0.4)),
            "respawns": round(games * rng.uniform(0.08, 0.15))}


def fill(db, player):
    import storage

    rng = random.Random(30)
    conn = storage.connect(db)
    read_at = "2026-11-01 16:12:32"
    total = {}
    for number in range(29, 19, -1):
        games = rng.randint(300, 900)
        for kind, count in (("all", games), ("ranked", games - rng.randint(0, 40))):
            values = season(rng, count)
            if kind == "all":
                for k, v in values.items():
                    total[k] = total.get(k, 0) + v
            row = {"player_name": player, "season": number, "kind": kind, "read_at": read_at,
                   "season_name": NAMES[number] if kind == "all" else "排位", **values}
            storage.save_season(conn, storage.save_season_pending(conn, row))
    career = season(rng, total["games"] + 812)
    career["top5"] = round(career["top5"], -1)  # 游戏只写了"2.82K"这样的约数
    storage.save_season(conn, storage.save_season_pending(conn, {
        "player_name": player, "season": None, "kind": "career", "season_name": "大逃杀", "read_at": read_at,
        **career, "approx": ["top5"]}))
    s30 = season(rng, 821)
    storage.save_season_pending(conn, {"player_name": player, "season": 30, "kind": "ranked", "season_name": "排位",
                                       "read_at": read_at, **s30})
    storage.save_season_pending(conn, {"player_name": player, "season": 30, "kind": "all", "season_name": NAMES[30],
                                       "read_at": read_at, **s30, "kd": round(s30["kd"] - 0.18, 2)})  # 故意读错
    conn.close()


def free_port():
    for port in range(5050, 5100):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    sys.exit("5050–5099 都被占了")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="evo-demo-seasons-"))
    data = tmp / "data"
    data.mkdir()
    os.environ["EVO_DATA_DIR"] = str(data)  # 在 import 程序代码之前设好：数据、设置都在临时文件夹里
    import feature_cards as fc
    os.environ["EVO_VIDEOCONFIG"] = str(tmp / "videoconfig.txt")
    (tmp / "videoconfig.txt").write_text(fc.VIDEOCONFIG, encoding="utf-8")
    (data / "settings.json").write_text('{"update_auto": true, "telemetry_consent": false}', encoding="utf-8")

    import panel_app
    import storage

    db, _ = fc.make_demo_data()
    conn = storage.connect(db)
    player = conn.execute("SELECT player_name FROM matches WHERE player_name IS NOT NULL LIMIT 1").fetchone()[0]
    conn.close()
    fill(db, player)
    panel_app.app.config["DB"] = str(db)
    url = f"http://127.0.0.1:{free_port()}/seasons"
    print(f"\n假数据的\"赛季\"页面：{url}\n（临时文件夹 {tmp}，不碰正式数据；按 Ctrl+C 关掉）\n")
    threading.Timer(1.0, webbrowser.open, [url]).start()
    try:
        panel_app.app.run(host="127.0.0.1", port=int(url.split(":")[2].split("/")[0]), debug=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
