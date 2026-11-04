"""第 31 赛季（2026-11-03 开始）的新界面：简体中文、游戏 16:10 拉伸，一局排位整段重放。

新赛季界面改了（见 docs/识别经验.md"第 31 赛季"）：大厅左下角的段位栏、经验值页上面那一块换了样子，
"排位点数"都改写成"RP"。用户 2026-11-03 在 2560×1440 显示器上用 DX12 全屏 2560×1600 打了一局排位，
用 tools/frame_capture.py 每秒截一张：captures/frames/20261103_195224（不在仓库里，见 fake_game.py；找不到就跳过）。
这一局：大厅（组队，白银 I 688/750 RP）→ 开局（排位赛、风暴点）→ 亡灵打了约 4 分钟 → 全军覆没、排名第 11 →
总结页、经验值页（+19，第 11 名，707RP，43 RP ▶ 黄金 IV）来回切 → 没回大厅，直接和队友排了下一局（开局画面）→ 桌面。
- 关键截图：每种画面认得出、读得对（大厅、经验值页、右上角的 RP 是新写法）；射击场的大厅也认得出、没有段位
- 整局重放：记下一局，数据和截图上的一样，不待确认；大厅读到段位和分数，从经验值页补上 RP 和之后的段位
- 经验值页：数字停下来才读、读全了才响提示音；动画没播完就离开不响（2026-11-04）

运行：python tests/test_season31.py
"""

import glob

from fake_game import SHOTS, SOUNDS_PLAYED, _TEMP, load, report, run_collect  # 要在项目代码之前导入

import fake_game
import layout
import ranks
import storage
from eliminated import read_eliminated
from hud import Hud
from lobby import LobbyReader
from match_start import read_match_start
from ocr import SMALL_CROP_PARAMS, create_engine, load_config
from screens import ScreenDetector
from summary_page import read_summary
from xp_page import current_rank, read_xp

FOLDER = SHOTS / "frames" / "20261103_195224"
RANGE_LOBBY = SHOTS / "frames" / "20261103_192948"  # 同一天开局前停在射击场的大厅（一人）
VIDEO_CONFIG = _TEMP / "videoconfig.txt"  # fake_game 把 EVO_VIDEOCONFIG 指到这里

SCREENS = {"0010": "lobby", "0016": "match_start", "0284": "eliminated", "0302": "summary", "0298": "xp",
           "0320": "xp", "0250": None, "0305": "squad_out"}  # 0250 对局中；0305 新的"排位"标签页（右下角有返回大厅）
SUMMARY = ("0302", {"placement": 11, "squad_kills": 3, "kda": (2, 1, 3), "damage": 504, "survival_time": "3:53",
                    "revives": 0, "respawns": 0})
XP = {"0298": {"rp_delta": 19, "placement": 11, "kills": 2, "assists": 1, "rank_tier": "白银", "rank_division": "I",
               "rank_progress": 707}}
LOBBY = ("0010", {"name": "ImperialHzd", "tier": "白银", "division": "I", "progress": 688, "emblem": "I"})
HUD = ("0281", "revenant", {"squads_left": 11, "kills": 2, "assists": 1, "participation": 0, "rp_delta": 19})
ROW = {"placement": 11, "squad_kills": 3, "kills": 2, "assists": 1, "knockdowns": 3, "participation": 0,
       "damage": 504, "survival_time": 233, "revives": 0, "respawns": 0, "rp_delta": 19, "rp_source": "经验值页",
       "rank_tier": "白银", "rank_division": "I", "rank_progress": 707, "legend": "亡灵", "mode": "ranked",
       "map": "风暴点", "player_name": "ImperialHzd", "needs_review": 0}


def frame_of(prefix, folder=FOLDER):
    return load(glob.glob(str(folder / f"{prefix}_*.jpg"))[0])


def text_checks():
    """不用截图：新写法的段位行、分数线。"""
    return {
        "下一段位'43 RP ▶ 黄金 IV' → 现在是白银 I": current_rank("43 RP ▶ 黄金IV") == ("白银", "I"),
        "'▶'没读出来也行：'43RP黄金IV' → 白银 I": current_rank("43RP黄金IV") == ("白银", "I"),
        "旧写法照样认：'下一段位：白金Ⅱ' → 白金 III": current_rank("下一段位:白金Ⅱ") == ("白金", "III"),
        "白银 I 一段 750 分（到黄金 IV 的 5500）：688 分是总分 5438": ranks.score("白银", "I", 688) == 5438,
    }


def screenshot_checks(engine):
    out = {}
    layout.set_game_aspect(16 / 10)
    detector, hud, lobby = ScreenDetector(engine), Hud(engine), LobbyReader(engine)
    got = {p: detector.detect(frame_of(p)) for p in SCREENS}
    out[f"画面判断（{got}）"] = got == SCREENS
    prefix, want = SUMMARY
    results, _ = read_summary(engine, frame_of(prefix), load_config("summary.json"))
    got = {k: results[k]["value"] for k in want}
    out[f"总结页 {prefix}：{got}"] = got == want
    for prefix, want in XP.items():
        xp, problems = read_xp(engine, frame_of(prefix), load_config("xp.json"))
        got = {k: xp[k] for k in want}
        out[f"经验值页 {prefix}（'30 RP''第11 5 RP''707RP''43 RP ▶ 黄金 IV'）：{got}，核对 {problems or '没问题'}"] = \
            got == want and not problems
    prefix, want = LOBBY
    r = lobby.read(frame_of(prefix))
    got = {k: r[k] for k in want}
    out[f"大厅 {prefix}（'688/750 RP'，右边的'▶ 62 RP'不算；名字左边多了段位徽章）：{got}"] = got == want
    r = lobby.read(frame_of("0500", RANGE_LOBBY))
    out[f"射击场的大厅：认得出，没有段位那一栏（{r['tier']}、{r['progress']}），名字照样读（{r['name']}）"] = (
        detector.detect(frame_of("0500", RANGE_LOBBY)) == "lobby" and r["tier"] is None and r["progress"] is None
        and r["name"] == "ImperialHzd")
    out["淘汰画面：排名第 11"] = read_eliminated(engine, frame_of("0284"), load_config("eliminated.json"))["placement"] == 11
    prefix, legend, want = HUD
    img = frame_of(prefix)
    values, _, _ = hud.read_top(hud.top_snip(img))
    got = (hud.own_legend(img), {k: values[k] for k in want})
    out[f"对局中 {prefix}：自己的传奇、右上角（'+19 RP'）{got}"] = got == (legend, want)
    start = read_match_start(engine, frame_of("0016"), load_config("match_start.json"))
    out[f"开局横幅：排位赛、风暴点（{start['mode']}、{start['map']}）"] = (start["mode"], start["map"]) == ("ranked", "风暴点")
    layout.set_game_aspect(None)
    return out


def replay_checks():
    VIDEO_CONFIG.write_text('"VideoConfig"\n{\n\t"setting.fullscreen"\t\t"1"\n\t"setting.defaultres"\t\t"2560"\n'
                            '\t"setting.defaultresheight"\t\t"1600"\n}\n', encoding="utf-16")
    rows, _, log = run_collect(sorted(glob.glob(str(FOLDER / "*.jpg"))), load)
    VIDEO_CONFIG.unlink()
    out = {f"记下 1 局（{len(rows)}）": len(rows) == 1}
    if rows:
        got = {k: rows[0][k] for k in ROW}
        out[f"这一局：{got}"] = got == ROW
    conn = storage.connect(fake_game.last_db)
    lobbies = [tuple(r) for r in conn.execute("SELECT player_name, rank_tier, rank_division, rank_progress, score "
                                              "FROM lobby_readings")]
    teammates = [(bool(r["name"]), r["legend"]) for r in conn.execute("SELECT name, legend FROM teammates ORDER BY slot")]
    conn.close()
    out[f"开局前的大厅：{lobbies}"] = lobbies == [("ImperialHzd", "白银", "I", 688, 5438)]
    # 队友的名字不写进测试（别的玩家的名字不进仓库），只看读到了名字、传奇对
    out[f"两名队友都读到名字，传奇是密客、命脉（{teammates}）"] = teammates == [(True, "密客"), (True, "命脉")]
    out["结束后直接排了下一局（没回大厅）：认出第二次开局画面"] = sum("进入开局画面" in line for line in log) == 2
    return out


def xp_still_checks():
    """经验值页：数字停下来就读那一张，读全了才响"经验值页"（2026-11-04 用户决定，以前一进页面就响，数字还在跳就说"已记录"）。
    假时钟下经验值页每 0.3 秒一张。0294–0297 动画还在播（相邻两张那几格差 4.5–70；0296 起明细已经读得全，
    后面几行还在淡入），0298 停下了（差 2.5 以内）。"""
    VIDEO_CONFIG.write_text('"VideoConfig"\n{\n\t"setting.fullscreen"\t\t"1"\n\t"setting.defaultres"\t\t"2560"\n'
                            '\t"setting.defaultresheight"\t\t"1600"\n}\n', encoding="utf-16")
    path = {p: glob.glob(str(FOLDER / f"{p}_*.jpg"))[0] for p in ("0294", "0295", "0296", "0297", "0298", "0300")}
    try:
        SOUNDS_PLAYED.clear()
        rows, _, log = run_collect([path[p] for p in ["0294", "0295", "0296", "0297"] + ["0298"] * 5 + ["0300"]], load)
        played = list(SOUNDS_PLAYED)
        SOUNDS_PLAYED.clear()
        _, _, early_log = run_collect([path[p] for p in ("0294", "0295", "0300")], load)  # 动画没播完（0295 明细还没出来）就离开
        early = list(SOUNDS_PLAYED)
    finally:
        VIDEO_CONFIG.unlink()

    def first(lines, text):
        return next((i for i, line in enumerate(lines) if text in line), None)

    read_at, cue_at, left_at = first(log, "经验值页的数字停下了"), first(log, "提示音：经验值页"), first(log, "小队已淘汰")
    return {
        f"动画播完、数字停下（和上一张差不多）才读，RP 对上了马上响，还在经验值页上（日志第 {read_at}、{cue_at}、{left_at} 行）":
            None not in (read_at, cue_at, left_at) and read_at < cue_at < left_at,
        f"读的是停下来的那一张：RP +19（{[r['rp_delta'] for r in rows]}）": [r["rp_delta"] for r in rows] == [19],
        f"只响一次\"经验值页\"（{played}）": played.count("xp") == 1,
        f"动画没播完就离开：数字没停过、不提前读，最后一帧没读全，不响（{early}）":
            "xp" not in early and first(early_log, "经验值页的数字停下了") is None,
    }


def main():
    print("第 31 赛季的新界面（简体、16:10，一局排位整段重放）")
    checks = text_checks()
    if not FOLDER.is_dir() or not RANGE_LOBBY.is_dir():
        print(f"  ⏭ 跳过截图部分：找不到 {FOLDER} 或 {RANGE_LOBBY}（截图不在仓库里）")
        return report(checks)
    checks.update(screenshot_checks(create_engine(SMALL_CROP_PARAMS)))
    checks.update({f"整局重放：{k}": v for k, v in replay_checks().items()})
    checks.update({f"经验值页：{k}": v for k, v in xp_still_checks().items()})
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
