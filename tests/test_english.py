"""英文游戏画面（config/lang/en.json）：用用户 2026-10-24 截的英文截图（captures/frames/英文，2560x1440、游戏 16:10 拉伸，
排位一局 + 匹配三人一局，每秒一张，共 1515 张）整段喂给记录程序，看它认不认得出英文、两局记得对不对；再单独读几张。

- 认语言：开游戏后第一个画面（大厅）就认定是英文
- 排位局：第 9 名、小队 2 杀、1/0/2、伤害 527、生存 8:15、急救 1、RP −20（经验值页，和对局中最后读到的一致）、白金 II 306、
  世界尽头、罗芭，名次三处（总结页、淘汰画面、经验值页）一致，不用确认
- 匹配局：开局横幅"TRIOS"记成匹配、奥林匹斯；第 3 名、10 杀、10/0/13、伤害 2,064；没有 RP、段位；
  匹配的经验值页（"Combat XP Total"）不当成排位的；对局中最后的伤害读到 2064（四位数时伤害图标往左移，见 config/hud.json）
- 大厅：排位时读出白金 II 326；匹配时左下角没有段位，写一行就不读
- 单张：经验值页明细（"Kills 1 12 RP"、"Placement #9 10 RP"、"O RP"）、淘汰画面"PLACED #3"、观战标签"SPECTATE"

队友的名字是别的玩家的，这里不写。截图不在仓库里，找不到时跳过（见 fake_game.py）。

运行：python tests/test_english.py
"""

from fake_game import MissingShots, SHOTS, load, report, run_collect  # 要在项目代码之前导入

import cv2  # noqa: F401

import fake_game
import layout
import storage
from eliminated import read_eliminated
from hud import Hud
from lobby import LobbyReader
from ocr import SMALL_CROP_PARAMS, LazyEngine, load_config
from screens import GameScreens
from xp_page import read_xp

FOLDER = SHOTS / "frames" / "英文"
COUNT = 1515
RANKED = {"mode": "ranked", "map": "世界尽头", "legend": "罗芭", "placement": 9, "squad_kills": 2, "kills": 1, "assists": 0,
          "knockdowns": 2, "participation": 1, "damage": 527, "survival_time": 495, "revives": 1, "respawns": 0,
          "rp_delta": -20, "rp_source": "经验值页", "rank_tier": "白金", "rank_division": "II", "rank_progress": 306,
          "needs_review": 0}
PUBS = {"mode": "pubs", "map": "奥林匹斯", "legend": "罗芭", "placement": 3, "squad_kills": 10, "kills": 10, "assists": 0,
        "knockdowns": 13, "damage": 2064, "survival_time": 637, "revives": 2, "respawns": 0, "rp_delta": None,
        "rank_tier": None, "needs_review": 0}


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
        xp, problems = read_xp(engine, frame(files, 667), load_config("xp.json", "en"))
        elim = [read_eliminated(engine, frame(files, n), load_config("eliminated.json", "en"))["placement"] for n in (652, 1491)]
        lobby = LobbyReader(engine, load_config("lobby.json", "en")).read(frame(files, 50))
        hud = Hud(engine, lang="en")
        spectate = [hud.spectating(frame(files, n)) for n in (1476, 600, 1200)]
        screens = GameScreens(engine, ["zh-CN", "en"])
        seen = [screens.detect(frame(files, n)) for n in (50, 106, 781, 658, 667, 1501, 1491, 1476)]
    finally:
        layout.set_game_aspect(None)
    return {"xp": ({k: xp[k] for k in ("rp_delta", "placement", "kills", "assists", "rank_tier", "rank_division",
                                       "rank_progress")}, problems),
            "elim": elim, "lobby": (lobby["tier"], lobby["division"], lobby["progress"]), "spectate": spectate,
            "seen": seen, "lang": (screens.lang, screens.locked)}


def collect_checks(files):
    rows, _, log = run_collect(files, load, game_aspect="16:10", keep_all=False)
    conn = storage.connect(fake_game.last_db)
    hud_damage = [r["value"] for r in storage.readings_of(conn, rows[-1]["match_id"], "damage") if r["source"] == "对局中"] \
        if rows else []
    mates = [len(storage.teammates_of(conn, r["match_id"])) for r in rows]
    lobbies = [(r["rank_tier"], r["rank_division"], r["rank_progress"]) for r in storage.lobby_readings(conn)]
    conn.close()
    return {"rows": [{k: r[k] for k in (RANKED.keys() | PUBS.keys())} for r in rows], "log": log,
            "hud_damage": hud_damage, "mates": mates, "lobbies": lobbies}


def differs(row, want):
    return {k: (row[k], v) for k, v in want.items() if row[k] != v}


def main():
    print("英文游戏画面（两局整段重放 + 单张）")
    try:
        files = frames()
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None
    s, c = single_checks(files), collect_checks(files)
    rows, log = c["rows"], c["log"]
    said = [line.split("] ", 1)[-1] for line in log]
    ranked = differs(rows[0], RANKED) if rows else "没记下"
    pubs = differs(rows[1], PUBS) if len(rows) > 1 else "没记下"
    return report({
        "经验值页（排位）：RP −20、第 9 名、计分击杀 1 助攻 0、白金 II 306，明细核对通过（'O RP'、'#9  10 RP' 分成几段也读对）":
            s["xp"] == ({"rp_delta": -20, "placement": 9, "kills": 1, "assists": 0, "rank_tier": "白金",
                         "rank_division": "II", "rank_progress": 306}, []),
        f"淘汰画面：'PLACED #9'、'PLACED #3'（读到 {s['elim']}）": s["elim"] == [9, 3],
        f"大厅：PLATINUM II、326/1000 RP → 白金 II 326（读到 {s['lobby']}）": s["lobby"] == ("白金", "II", 326),
        "观战标签'SPECTATE'：观战时认得出，对局中（排位、匹配）不会认错": s["spectate"] == [True, False, False],
        f"认画面：大厅、排位横幅、匹配横幅（当作开局画面）、总结页、经验值页；匹配的经验值页不算经验值页"
        f"（底部有'Return to Lobby'，算小队已淘汰）；淘汰画面；观战队友时不是任何画面（认到 {s['seen']}）":
            s["seen"] == ["lobby", "match_start", "match_start", "summary", "xp", "squad_out", "eliminated", None],
        "两种语言一起认时，第一张就认定英文": s["lang"] == ("en", True),
        "记录程序：开游戏后在大厅就认定英文": "游戏语言：English（从大厅认出）" in said,
        f"记下 2 局（{len(rows)}）": len(rows) == 2,
        f"排位局全对（对不上的：{ranked or '无'}）": ranked == {},
        f"匹配局全对：开局横幅 TRIOS 记成匹配，没有 RP、段位（对不上的：{pubs or '无'}）": pubs == {},
        f"匹配局对局中最后的伤害读到四位数 2064，不是 0（{c['hud_damage']}）": c["hud_damage"] == [2064],
        "开局画面读到排位、世界尽头和匹配、奥林匹斯":
            "开局画面：读到 排位、世界尽头" in said and "开局画面：读到 匹配、奥林匹斯" in said,
        "匹配的经验值页不当成排位的（只进过排位局的经验值页）": sum("进入经验值页" in x for x in said) == 2
            and not any("经验值页没读全" in x for x in said),
        f"每局 2 名队友（{c['mates']}）": c["mates"] == [2, 2],
        f"大厅：排位时读出白金 II 326（{c['lobbies']}）": bool(c["lobbies"]) and set(c["lobbies"]) == {("白金", "II", 326)},
        "匹配大厅：左下角没有段位，写一行就不读": said.count("大厅：左下角没有段位（选的不是排位），这次不读") == 1,
        "排位局的 RP 和对局中最后读到的一致": "RP 核对：经验值页、对局中都是 -20（编号 1）" in [x.strip() for x in said],
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
