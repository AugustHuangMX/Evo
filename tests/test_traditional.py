"""繁体中文游戏画面（config/lang/zh-TW.json）：用用户 2026-10-24 截的繁体截图（captures/frames/繁体，2560x1440、游戏 16:10 拉伸，
匹配三排两局 + 排位一局，每秒一张，共 872 张）整段喂给记录程序，看它认不认得出繁体、三局记得对不对；再单独读几张。

- 认语言：开游戏后第一个画面（匹配的大厅"普通對戰"）就认定是繁体
- 匹配两局：开局横幅"三排"记成匹配、奧林匹斯 → 奥林匹斯；第 17 名（0/0/0、伤害 33）、第 13 名（1/0/2、伤害 807）
- 排位局："積分賽"、殘月 → 残月；第 16 名、0/0/1、伤害 381、RP −48（经验值页，和对局中一致）、白金 II 258
  （"下個排名：白金1"的"1"常被漏掉，只框住那一行才稳；漏掉时不猜，见 xp_page.current_rank）
- 大厅：排位时读出白金 II 306（"白金Ⅱ""306/1000RP"）；匹配时左下角没有段位，写一行就不读
- 单张：经验值页、淘汰画面"排名#17"、观战标签"觀戰"；桌面上录像软件黑色的播放器不会被当成淘汰画面（screens.match）

队友的名字是别的玩家的，这里不写。截图不在仓库里，找不到时跳过（见 fake_game.py）。

运行：python tests/test_traditional.py
"""

from fake_game import MissingShots, SHOTS, load, report, run_collect  # 要在项目代码之前导入

import fake_game
import layout
import storage
from eliminated import read_eliminated
from hud import Hud
from lobby import LobbyReader
from ocr import SMALL_CROP_PARAMS, LazyEngine, load_config
from screens import GameScreens, ScreenDetector
from xp_page import read_xp

FOLDER = SHOTS / "frames" / "繁体"
COUNT = 872
FIELDS = ("mode", "map", "legend", "placement", "squad_kills", "kills", "assists", "knockdowns", "damage", "survival_time",
          "revives", "respawns", "rp_delta", "rp_source", "rank_tier", "rank_division", "rank_progress", "needs_review")
WANT = [
    dict(zip(FIELDS, ("pubs", "奥林匹斯", "罗芭", 17, 0, 0, 0, 0, 33, 80, 0, 0, None, None, None, None, None, 0))),
    dict(zip(FIELDS, ("pubs", "奥林匹斯", "罗芭", 13, 3, 1, 0, 2, 807, 178, 0, 0, None, None, None, None, None, 0))),
    dict(zip(FIELDS, ("ranked", "残月", "罗芭", 16, 0, 0, 0, 1, 381, 181, 0, 0, -48, "经验值页", "白金", "II", 258, 0))),
]


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
    """单独读几张（按 16:10 换算）。"""
    engine = LazyEngine(SMALL_CROP_PARAMS)
    layout.set_game_aspect(16 / 10)
    try:
        xp, problems = read_xp(engine, frame(files, 862), load_config("xp.json", "zh-TW"))
        elim = [read_eliminated(engine, frame(files, n), load_config("eliminated.json", "zh-TW"))["placement"]
                for n in (238, 534, 850)]
        lobby = LobbyReader(engine, load_config("lobby.json", "zh-TW")).read(frame(files, 600))
        hud = Hud(engine, lang="zh-TW")
        spectate = [hud.spectating(frame(files, n)) for n in (205, 400, 700)]
        screens = GameScreens(engine, ["zh-CN", "en", "zh-TW"])
        seen = [screens.detect(frame(files, n)) for n in (45, 600, 73, 629, 538, 862, 541, 238, 205, 871)]
        flat = ScreenDetector(engine, lang="zh-TW").similarity(frame(files, 871), "eliminated")
    finally:
        layout.set_game_aspect(None)
    return {"xp": ({k: xp[k] for k in ("rp_delta", "placement", "kills", "assists", "rank_tier", "rank_division",
                                       "rank_progress")}, problems),
            "elim": elim, "lobby": (lobby["tier"], lobby["division"], lobby["progress"]), "spectate": spectate,
            "seen": seen, "lang": (screens.lang, screens.locked), "flat": flat}


def collect_checks(files):
    rows, _, log = run_collect(files, load, game_aspect="16:10", keep_all=False)
    conn = storage.connect(fake_game.last_db)
    mates = [len(storage.teammates_of(conn, r["match_id"])) for r in rows]
    lobbies = [(r["rank_tier"], r["rank_division"], r["rank_progress"]) for r in storage.lobby_readings(conn)]
    conn.close()
    return {"rows": [{k: r[k] for k in FIELDS} for r in rows], "log": log, "mates": mates, "lobbies": lobbies}


def main():
    print("繁体中文游戏画面（三局整段重放 + 单张）")
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
        "经验值页（排位）：RP −48、第 16 名、计分击杀 0 助攻 0、白金 II 258（'下個排名：白金1'读到了段数），明细核对通过":
            s["xp"] == ({"rp_delta": -48, "placement": 16, "kills": 0, "assists": 0, "rank_tier": "白金",
                         "rank_division": "II", "rank_progress": 258}, []),
        f"淘汰画面：'排名 #17''#13''#16'（读到 {s['elim']}）": s["elim"] == [17, 13, 16],
        f"大厅：白金Ⅱ、306/1000RP → 白金 II 306（读到 {s['lobby']}）": s["lobby"] == ("白金", "II", 306),
        "观战标签'觀戰'：观战时认得出，对局中不会认错": s["spectate"] == [True, False, False],
        f"认画面：匹配大厅'普通對戰'和排位大厅'積分賽'都是大厅，'三排''積分賽'横幅都是开局画面，总结页，"
        f"排位的经验值页；匹配的经验值页不算（底部有'返回大廳'，算小队已淘汰）；淘汰画面；观战队友、桌面都不是"
        f"（认到 {s['seen']}）":
            s["seen"] == ["lobby", "lobby", "match_start", "match_start", "summary", "xp", "squad_out", "eliminated",
                          None, None],
        "三种语言一起认时，第一张（匹配大厅）就认定繁体": s["lang"] == ("zh-TW", True),
        f"桌面上黑色的播放器不像淘汰画面（纯色的位置不算，相似度 {s['flat']:.2f}）": s["flat"] < 0.35,
        "记录程序：开游戏后在大厅就认定繁体": "游戏语言：繁體中文（从大厅（匹配）认出）" in said,
        f"记下 3 局（{len(rows)}）": len(rows) == 3,
        f"三局全对（对不上的：{[w for w in wrong if w] or '无'}）": len(rows) == 3 and not any(wrong),
        "开局画面读到两次匹配、奥林匹斯和一次排位、残月":
            said.count("开局画面：读到 匹配、奥林匹斯") == 2 and said.count("开局画面：读到 排位、残月") == 1,
        f"每局 2 名队友（{c['mates']}）": c["mates"] == [2, 2, 2],
        f"大厅：排位时读出白金 II 306（{c['lobbies']}）": c["lobbies"] == [("白金", "II", 306)],
        "匹配大厅：左下角没有段位，写一行就不读": said.count("大厅：左下角没有段位（选的不是排位），这次不读") == 1,
        "排位局的 RP 和对局中最后读到的一致": "RP 核对：经验值页、对局中都是 -48（编号 3）" in said,
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
