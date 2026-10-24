"""简体中文的匹配局（config/screens.json 的 match_start_trios、lobby_pubs）：用用户 2026-10-24 截的一局匹配三人赛
（captures/frames/20261024_165228，2560x1440、游戏 16:10 拉伸，每秒一张，共 317 张，前后有桌面）整段喂给记录程序，
看它认不认得出、记得对不对；再单独认几张。

- 开局横幅"三人赛"记成匹配，下一行奥林匹斯；匹配大厅左下角"未上榜"认作大厅（匹配时没有段位那一栏，读 2 次读不到就不读）
- 认语言：开游戏后第一个画面（匹配大厅）就认定是简体（英文不继承简体的"未上榜"，繁体是"普通對戰"）
- 这一局：罗芭，第 19 名，小队 1 杀，0/0/0，伤害 36，生存 0:51；两名队友暴雷 1/0/1、伤害 85，动力小子 0/0/0、伤害 42
  （和总结页一致）；淘汰画面"排名第 19"和总结页核对一致
- 桌面、对局中不会认成别的画面

队友的名字、玩家名是别的玩家的或用户的小号，这里不写。截图不在仓库里，找不到时跳过（见 fake_game.py）。

运行：python tests/test_simplified_pubs.py
"""

from fake_game import MissingShots, SHOTS, load, report, run_collect  # 要在项目代码之前导入

import fake_game
import layout
import storage
from match_start import CONFIG_NAME as START_CONFIG, read_match_start
from ocr import SMALL_CROP_PARAMS, LazyEngine, load_config
from screens import GameScreens, ScreenDetector

FOLDER = SHOTS / "frames" / "20261024_165228"
COUNT = 317
FIELDS = ("mode", "map", "legend", "placement", "squad_kills", "kills", "assists", "knockdowns", "damage", "survival_time",
          "revives", "respawns", "rp_delta", "rp_source", "rank_tier", "rank_division", "rank_progress", "needs_review")
WANT = [dict(zip(FIELDS, ("pubs", "奥林匹斯", "罗芭", 19, 1, 0, 0, 0, 36, 51, 0, 0, None, None, None, None, None, 0)))]
WANT_MATES = [("暴雷", 1, 0, 1, 85, 131), ("动力小子", 0, 0, 0, 42, 130)]


def frames():
    found = sorted(FOLDER.glob("*.jpg"))
    if len(found) != COUNT:
        raise MissingShots(f"{FOLDER}（要 {COUNT} 张，找到 {len(found)} 张）")
    return found


def frame(files, n):
    path = files[n - 1]
    assert path.name.startswith(f"{n:04d}_"), path.name
    return load(path)


def single_checks(files):
    """单独认几张（按 16:10 换算）。"""
    engine = LazyEngine(SMALL_CROP_PARAMS)
    layout.set_game_aspect(16 / 10)
    try:
        screens = GameScreens(engine, ["zh-CN", "en", "zh-TW"])
        # 匹配大厅、开局横幅两张、总结页、淘汰画面、桌面、对局中
        seen = [screens.detect(frame(files, n)) for n in (71, 101, 103, 281, 276, 1, 181)]
        english = ScreenDetector(engine, lang="en").check(frame(files, 71), "lobby_pubs")
        start = read_match_start(engine, frame(files, 103), load_config(START_CONFIG))
    finally:
        layout.set_game_aspect(None)
    return {"seen": seen, "lang": (screens.lang, screens.locked), "english": english,
            "start": (start["mode"], start["map"], start["raw_mode"])}


def collect_checks(files):
    rows, _, log = run_collect(files, load, game_aspect="16:10", keep_all=False)
    conn = storage.connect(fake_game.last_db)
    mates = [[(m["legend"], m["kills"], m["assists"], m["knockdowns"], m["damage"], m["survival_time"])
              for m in storage.teammates_of(conn, r["match_id"])] for r in rows]
    lobbies = storage.lobby_readings(conn)
    conn.close()
    return {"rows": [{k: r[k] for k in FIELDS} for r in rows], "log": log, "mates": mates, "lobbies": lobbies}


def main():
    print("简体中文的匹配局（一局整段重放 + 单张）")
    try:
        files = frames()
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None
    s, c = single_checks(files), collect_checks(files)
    rows = c["rows"]
    said = [line.split("] ", 1)[-1].strip() for line in c["log"]]
    wrong = [{k: (row[k], want[k]) for k in FIELDS if row[k] != want[k]} for row, want in zip(rows, WANT)]
    return report({
        f"认画面：匹配大厅'未上榜'是大厅，'三人赛'横幅（两张）是开局画面，总结页，淘汰画面；桌面、对局中都不是"
        f"（认到 {s['seen']}）":
            s["seen"] == ["lobby", "match_start", "match_start", "summary", "eliminated", None, None],
        "三种语言一起认时，第一张（匹配大厅）就认定简体": s["lang"] == ("zh-CN", True),
        "英文不继承简体的'未上榜'（不然简体的匹配大厅两种语言都对得上）": s["english"] is False,
        f"开局横幅：'三人赛' → 匹配，奥林匹斯（读到 {s['start']}）": s["start"] == ("pubs", "奥林匹斯", "三人赛"),
        "记录程序：开游戏后在大厅就认定简体": "游戏语言：简体中文（从大厅（匹配）认出）" in said,
        f"记下 1 局（{len(rows)}）": len(rows) == 1,
        f"这一局全对（对不上的：{[w for w in wrong if w] or '无'}）": len(rows) == 1 and not any(wrong),
        "开局画面读到匹配、奥林匹斯": said.count("开局画面：读到 匹配、奥林匹斯") == 1,
        f"两名队友的传奇和数据和总结页一致（{c['mates']}）": c["mates"] == [WANT_MATES],
        "名次：总结页、淘汰画面都是第 19": "名次核对：总结页、淘汰画面都是第 19（编号 1）" in said,
        "匹配大厅：左下角没有段位，读 2 次就不读了（不再每次读满 6 次）":
            "大厅：左下角没有段位（选的不是排位），这次不读" in said
            and not any("段位和分数没有连续两次一样" in line for line in said),
        f"匹配大厅不记段位（{len(c['lobbies'])} 条）": not c["lobbies"],
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
