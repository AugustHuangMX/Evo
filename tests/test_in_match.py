"""0.3.0 的新流程（docs/对局全流程图.md）：对局中、一局结束时取"结算前最后一次"的数据、冠军、大厅读数和校准、各来源的优先级。

- 读截图（hud.py、lobby.py、summary_page.is_champion）：2026-10-18 用户给的 1080p 录像截图（captures/samples/），
  标准答案逐张看过；对局中.png 的三个传奇用户确认过，另外两张按字幕里的台词核对
- 接进记录程序（用假游戏跑几段，见 fake_game.py）：
  - 自己最后倒地时全队被淘汰：右上角最后一次读数就是最终数据（含 RP），名次用淘汰画面的
  - 自己先成盒、队友后被淘汰：击杀、助攻、老板分、伤害用成盒前的；RP 不记（等经验值页或大厅校准）
  - 成盒后被复活：继续记录，RP 照常记
  - 拿冠军：冠军横幅 → 总结页；名次记第 1；总结页的名次还在滑进来也不用确认；队友按名字对上、补上传奇
  - 停在总结页时只读总结页：右上角回到大厅才读；从经验值页切到总结页时，总结页存下了才读经验值页
  - 靶场（没有开局画面、也没有结算画面）：不记；中途停止记录：记下，但标待确认
  - 对局中截图每 0.3 秒一张，结算后回到每秒一张；交火时不用识别引擎
  - 大厅：连续两次读到一样的段位和分数才记
- 规则（直接测 post_match.settle、calibration.calibrate，不用截图）
截图不在仓库里，见 fake_game.py。

运行：python tests/test_in_match.py
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import fake_game
from fake_game import MissingShots, load, report, run_collect, shot  # 要在项目代码之前导入

import cv2

import calibration
import live_collect
import ocr
import storage
from hud import Hud
from lobby import LobbyReader
from ocr import SMALL_CROP_PARAMS, create_engine, load_config
from post_match import HUD_ONLY_NOTE, NO_END_NOTE, REQUEUE_NOTE, SOURCE_HUD, SOURCE_SUMMARY, settle
from screens import ScreenDetector
from summary_page import is_champion

S = "samples/"
PLAYING, KNOCKED, MATES_DOWN = S + "对局中.png", S + "玩家倒地队友依然存活.png", S + "玩家存活队友倒地.png"
SPECTATING, CHAMP_MOMENT, CHAMP_SUMMARY = S + "玩家成盒（观战队友视角）.png", S + "捍卫者瞬间界面.png", S + "捍卫者结算界面.png"
ELIMINATED, SUMMARY = S + "死亡瞬间.png", S + "vlcsnap-2026-10-16-18h26m12s173.png"
SPECT_ENEMY = S + "vlcsnap-2026-10-16-18h27m31s652.png"  # 观战对手（侵蚀）：全队被淘汰以后
START = "live/20261016_211452/match_start_213625_enter.png"  # 开局画面：排位、世界尽头
LOBBY = "live/20261016_211452/lobby_211506_enter.png"  # 大厅：ImperialHzd，钻石 II 944
XP5, SUMMARY5 = "live/20261016_211452/xp_222758_leave.png", "live/20261016_211452/05_222749.png"  # 同一局：第 8 名，3 杀，688 伤害
RANGE = "legends/20261017_154752/full_27.jpg"  # 靶场
FIELDS = ("squads_left", "kills", "assists", "participation", "damage", "rp_delta")

# 每张样本的标准答案：(自己的传奇, 倒地, 观战, [两名队友的传奇], 右上角)
ANSWERS = {
    PLAYING: ("罗芭", False, False, ["地平线", "疯玛吉"],
              {"squads_left": 18, "kills": None, "assists": None, "participation": None, "damage": None, "rp_delta": -65}),
    MATES_DOWN: ("希尔", False, False, ["罗芭", "动力小子"],
                 {"squads_left": 16, "kills": 0, "assists": 1, "participation": 0, "damage": 48, "rp_delta": -59}),
    KNOCKED: ("罗芭", True, False, ["地平线", "疯玛吉"],
              {"squads_left": 12, "kills": None, "assists": None, "participation": None, "damage": 557, "rp_delta": -60}),
}
# 队友的名字是别的玩家的名字，和截图一样只留在本机（.gitignore）：{样本: [上面那行, 下面那行]}，"冠军总结页右边": 名字。
# 找不到时只检查读到了名字
NAMES_FILE = Path(__file__).with_name("in_match_teammates.json")
NAMES = json.loads(NAMES_FILE.read_text(encoding="utf-8")) if NAMES_FILE.is_file() else {}


def name_ok(got, want):
    """名字读到了；有本机的答案时还要很像（名字里游戏显示不了的字，如"□"，可能读成 o 或干脆没有）。"""
    return bool(got) and (want is None or storage.name_similarity(got, want) >= 0.85)


def screenshot_checks(images):
    engine = create_engine(SMALL_CROP_PARAMS)
    hud = Hud(engine)
    left = hud.cfg["teammates"]["name_left"]
    out, wrong = {}, []
    for path, (legend, knocked, spectating, mates, top) in ANSWERS.items():
        img = images[path]
        own = hud.own_legend(img)
        legends = [k and hud.legends.name(k) for k in hud.mate_legends(img)]
        names = [hud.read_name(hud.mate_name_snip(img, i), left) for i in range(2)]
        values, _, _ = hud.read_top(hud.top_snip(img))
        got = (own and hud.legends.name(own), hud.knocked(img), hud.spectating(img), legends, values)
        want = (legend, knocked, spectating, mates, top)
        want_names = NAMES.get(path, [None, None])
        if got != want or not all(name_ok(n, w) for n, w in zip(names, want_names)):
            wrong.append(f"{path}：应为 {want}，读到 {got}，队友名字 {names}")
    out["hud_wrong"] = wrong
    # 0/0/0 时击杀那一行不显示：背后画面上的一个"7"不能当成老板分（2026-10-18 实战）
    fake = images[PLAYING].copy()
    cv2.putText(fake, "7", (1636, 119), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    top = hud.cfg["top_right"]
    snip = hud.top_snip(fake)
    lone = [1631, 95, 32, 28]  # 老板分数字平时在的地方（"7"画在这里，先确认单独读这一块确实读得出 7）
    raw = hud._number(snip.crop(lone, top["upscale"], pad=top["pad"]), r"^(\d{1,3})$", [0, 60], top)[0]
    values, _, shown = hud.read_top(snip)
    out["lone_kp"] = (raw, values["participation"], "participation" in shown)
    out["spectating"] = hud.own_legend(images[SPECTATING]) is None and hud.spectating(images[SPECTATING])
    out["not_spectating"] = [p for p in (PLAYING, KNOCKED, MATES_DOWN, CHAMP_MOMENT, LOBBY, START) if hud.spectating(images[p])]
    detector = ScreenDetector(engine)
    out["champion_screens"] = (detector.detect(images[CHAMP_MOMENT]), detector.detect(images[CHAMP_SUMMARY]))
    cfg = load_config("summary.json")
    out["champion_banner"] = (is_champion(engine, images[CHAMP_SUMMARY], cfg), is_champion(engine, images[SUMMARY], cfg))
    lobby = LobbyReader(engine).read(images[LOBBY])
    out["lobby"] = (lobby["name"], lobby["tier"], lobby["division"], lobby["progress"], lobby["score"])
    return out


def same_mates(got, want):
    """队友一样：位置、传奇一样，名字读到了（有本机的答案时还要很像）。got、want：[(位置, 名字, 传奇)]。"""
    return len(got) == len(want) and all(g[0] == w[0] and g[2] == w[2] and name_ok(g[1], w[1]) for g, w in zip(got, want))


def mates_of(match_id):
    conn = storage.connect(fake_game.last_db)
    rows = [(r["slot"], r["name"], r["legend"]) for r in storage.teammates_of(conn, match_id)]
    conn.close()
    return rows


def readings_of(match_id, field):
    conn = storage.connect(fake_game.last_db)
    got = {r["source"]: r["value"] for r in storage.readings_of(conn, match_id, field)}
    conn.close()
    return got


def lobby_rows():
    conn = storage.connect(fake_game.last_db)
    rows = [(r["player_name"], r["rank_tier"], r["rank_division"], r["rank_progress"], r["score"])
            for r in storage.lobby_readings(conn)]
    conn.close()
    return rows


def pick(row, *keys):
    return tuple(row[k] for k in keys)


def collect_checks(images):
    out = {}
    blank = images[MATES_DOWN].copy()
    blank[95:123, 1510:1538] = 0  # 1080p 截图：右上角击杀那个数字涂黑（助攻、老板分照样显示）
    row = blank.copy()
    row[95:123, 1573:1600] = 0
    row[95:123, 1631:1663] = 0  # 三个数字都涂黑，只剩骷髅等图标
    images = {**images, "击杀涂黑": blank, "整行数字涂黑": row}
    ran_on = []  # 每次用识别引擎时屏幕上是哪一帧
    real_create = ocr.create_engine

    def counting_engine(params=None):
        engine = real_create(params)

        def run(img, **kwargs):
            ran_on.append(fake_game.FakeCamera.showing)
            return engine(img, **kwargs)
        return run

    def run(name, frames, **options):
        ran_on.clear()
        with mock.patch.object(ocr, "create_engine", counting_engine):
            rows, sleeps, log = run_collect(frames, images.get, **options)
        out[name] = {"rows": rows, "sleeps": sleeps, "log": log, "ran_on": list(ran_on),
                     "mates": [mates_of(r["match_id"]) for r in rows]}
        return out[name]

    keys = ("placement", "kills", "assists", "participation", "damage", "rp_delta", "rp_source", "needs_review",
            "review_note", "mode", "legend")
    # 自己最后倒地时全队被淘汰，直接回大厅（没进总结页、经验值页）
    r = run("last_down", [START, PLAYING, PLAYING, KNOCKED, KNOCKED, KNOCKED, ELIMINATED, SPECTATING, LOBBY, LOBBY])
    r["got"] = [pick(row, *keys) for row in r["rows"]]
    r["placement_sources"] = readings_of(r["rows"][0]["match_id"], "placement") if r["rows"] else {}
    # 自己先成盒、队友后被淘汰
    r = run("died_first", [START, MATES_DOWN, MATES_DOWN, MATES_DOWN, SPECTATING, SPECTATING, ELIMINATED, SPECTATING])
    r["got"] = [pick(row, *keys) for row in r["rows"]]
    r["rp_at_death"] = readings_of(r["rows"][0]["match_id"], "rp_at_death") if r["rows"] else {}
    # 成盒后被复活，最后倒地时全队被淘汰
    r = run("revived", [START, MATES_DOWN, MATES_DOWN, SPECTATING, SPECTATING, KNOCKED, KNOCKED, ELIMINATED],
            keep_all=True)
    r["got"] = [pick(row, "rp_delta", "rp_source", "damage") for row in r["rows"]]
    # 自己是小队最后一个：成盒后观战的不是队友（这里观战的动力小子不在这局的队友里），然后直接进了下一局（2026-10-18 实战 #45）
    r = run("last_one", [START, PLAYING, PLAYING, PLAYING, SPECTATING, SPECTATING, START, START])
    r["got"] = [pick(row, *keys) for row in r["rows"]]
    # 全队被淘汰后观战对手：右下角有"返回大厅"（2026-10-19 用户决定用它判断小队结束了），淘汰画面没认出来也能收尾
    r = run("squad_out", [START, PLAYING, PLAYING, PLAYING, SPECT_ENEMY, SPECT_ENEMY, START])
    r["got"] = [pick(row, *keys) for row in r["rows"]]
    r["said"] = [line for line in r["log"] if "返回大厅按钮" in line]
    # 停在总结页时只读总结页（2026-10-27 用户决定）：小队打完了 → 经验值页 → 总结页 → 大厅。右上角回到大厅才读；
    # 从经验值页切到总结页时，经验值页等总结页存下了再读
    read_on = []  # (读的是什么, 当时屏幕上是哪一帧)
    real_top, real_xp, real_summary = Hud.read_top, live_collect.read_xp, live_collect.read_summary

    def spy(kind, real):
        def wrapped(*args, **kwargs):
            read_on.append((kind, fake_game.FakeCamera.showing))
            return real(*args, **kwargs)
        return wrapped

    with mock.patch.object(Hud, "read_top", spy("右上角", real_top)), \
            mock.patch.object(live_collect, "read_xp", spy("经验值页", real_xp)), \
            mock.patch.object(live_collect, "read_summary", spy("总结页", real_summary)):
        r = run("pages_first", [START, PLAYING, PLAYING, PLAYING, SPECT_ENEMY, XP5, SUMMARY5, SUMMARY5, SUMMARY5,
                                LOBBY, LOBBY])
    r["read_on"] = read_on
    r["got"] = [pick(row, "placement", "kills", "damage", "rp_source", "legend") for row in r["rows"]]
    r["end_screen"] = readings_of(r["rows"][0]["match_id"], "end_screen") if r["rows"] else {}
    # 拿冠军
    r = run("champion", [START, PLAYING, PLAYING, PLAYING, CHAMP_MOMENT, CHAMP_MOMENT, CHAMP_SUMMARY, CHAMP_SUMMARY,
                         CHAMP_SUMMARY])
    r["got"] = [pick(row, "placement", "kills", "damage", "rp_delta", "rp_source", "needs_review", "review_note")
                for row in r["rows"]]
    r["placement_sources"] = readings_of(r["rows"][0]["match_id"], "placement") if r["rows"] else {}
    # 右上角那一行显示着、击杀那个数字没读出来（2026-10-18 第一局实战就遇到了）：留空，不当作 0
    r = run("kills_unread", [START, "击杀涂黑", "击杀涂黑", ELIMINATED])
    r["got"] = [pick(row, "kills", "assists", "participation", "damage") for row in r["rows"]]
    r = run("row_unread", [START, "整行数字涂黑", "整行数字涂黑", ELIMINATED])
    r["got"] = [pick(row, "kills", "assists", "participation", "damage") for row in r["rows"]]
    # 靶场：不记；中途停止记录：记下但标待确认
    run("range", [RANGE, RANGE, RANGE, LOBBY])
    r = run("stopped", [START, PLAYING, PLAYING, PLAYING])
    r["got"] = [pick(row, "needs_review", "review_note", "rp_delta") for row in r["rows"]]
    # 大厅只看到一帧：不记；两帧一样：记一次
    run("lobby_once", [LOBBY])
    out["lobby_once"]["lobby"] = lobby_rows()
    run("lobby_twice", [LOBBY, LOBBY, LOBBY])
    out["lobby_twice"]["lobby"] = lobby_rows()
    return out


def rule_checks():
    """直接测规则（不用截图）。"""
    out = {}
    conn = storage.connect(":memory:")
    log = []
    t = datetime(2026, 10, 18, 21, 0)

    def stamp(minutes):
        return f"{t + timedelta(minutes=minutes):%Y-%m-%d %H:%M:%S}"

    def game(minutes, rp=None, mode="ranked", name="Me"):
        return storage.insert_match(conn, {"ended_at": stamp(minutes), "rp_delta": rp, "mode": mode, "player_name": name})

    def lobby(minutes, progress, tier="钻石", division="II", name="Me"):
        from ranks import score
        reading = {"read_at": stamp(minutes), "player_name": name, "rank_tier": tier, "rank_division": division,
                   "rank_progress": progress, "score": score(tier, division, progress)}
        storage.save_lobby(conn, reading)
        calibration.calibrate(conn, reading, log.append)

    # 只缺一局的 RP：用差值补上
    lobby(0, 900)
    a, b, c = game(10, -20), game(30), game(50, 35)
    lobby(60, 870)
    out["fill"] = pick(storage.get_match(conn, b), "rp_delta", "rp_source")
    # 都有 RP 但对不上：标待确认
    d = game(70, 10)
    lobby(80, 900)
    out["mismatch"] = pick(storage.get_match(conn, d), "needs_review")
    out["mismatch_note"] = storage.get_match(conn, d)["review_note"] or ""
    # 对上了：不标
    e = game(90, 5)
    lobby(100, 905)
    out["agree"] = pick(storage.get_match(conn, e), "needs_review")
    # 跨大段：不核对
    f = game(110, 40)
    lobby(120, 10, "大师", None)
    out["cross_tier"] = pick(storage.get_match(conn, f), "needs_review")
    # 别的账号的局不算进来
    g = game(130, 99, name="Alt")
    lobby(140, 30, "大师", None)
    out["other_account"] = pick(storage.get_match(conn, g), "needs_review")

    # 各来源的优先级
    def match_with(readings, note=None):
        match_id = storage.insert_match(conn, {"ended_at": stamp(200), "review_note": note, "needs_review": int(bool(note))})
        for source, values in readings.items():
            storage.save_readings(conn, match_id, source, values, stamp(200))
        settle(conn, match_id, log.append)
        return storage.get_match(conn, match_id)

    row = match_with({SOURCE_SUMMARY: {"placement": 5, "kills": 3, "assists": 1, "damage": 900},
                      SOURCE_HUD: {"kills": 2, "assists": 1, "participation": 2, "damage": 850, "rp_delta": 30}})
    out["hud_less"] = pick(row, "kills", "damage", "participation", "rp_delta", "rp_source", "needs_review")
    row = match_with({SOURCE_SUMMARY: {"placement": 5, "kills": 3, "assists": 1, "damage": 900},
                      SOURCE_HUD: {"kills": 4, "assists": 1, "damage": 900}})
    out["hud_more"] = (row["kills"], row["needs_review"], row["review_note"])
    row = match_with({SOURCE_SUMMARY: {"placement": None, "kills": None, "assists": None, "damage": None},
                      SOURCE_HUD: {"placement": 7, "kills": 2, "assists": 0, "participation": 1, "damage": 640}},
                     "名次：没读到（OCR 原文：无）；击杀/助攻/击倒：没读到（OCR 原文：无）；伤害：没读到（OCR 原文：无）；"
                     "总结页停留太短，名次等数据可能没读全，请核对")
    out["hud_fills"] = pick(row, "placement", "kills", "damage", "needs_review", "review_note")
    return out


def panel_checks():
    """战绩页：点开一局显示队友的传奇；详情里有老板分、RP 从哪来；修改表单能改老板分。"""
    import panel_app
    path = fake_game._TEMP / "panel_0_3.db"
    conn = storage.connect(path)
    match_id = storage.insert_match(conn, {"ended_at": "2026-10-18 21:00:00", "placement": 3, "kills": 2, "assists": 1,
                                           "participation": 4, "damage": 800, "rp_delta": 25, "rp_source": "对局中",
                                           "review_note": HUD_ONLY_NOTE})
    storage.save_teammates(conn, match_id, [{"slot": "hud1", "name": "MateA", "legend": "地平线"},
                                            {"slot": "hud2", "name": "MateB", "legend": None}])
    conn.close()
    panel_app.app.config.update(DB=path, TESTING=True)
    client = panel_app.app.test_client()
    opened = client.get(f"/matches?open={match_id}").get_data(as_text=True)
    detail = client.get(f"/matches?open={match_id}&detail=1").get_data(as_text=True)
    token = panel_app.app.config["TOKEN"]
    form = {"token": token, "placement": "3", "kills": "2", "assists": "1", "knockdowns": "", "participation": "6",
            "damage": "800", "rp_delta": "25"}
    client.post(f"/matches/{match_id}/update", data=form)
    conn = storage.connect(path)
    saved = storage.get_match(conn, match_id)["participation"]
    conn.close()
    return {
        "战绩页：点开一局，队友那一行显示对局中认出的传奇": ">地平线<" in opened and "MateB" in opened,
        "详情：有老板分、RP 从哪来（对局中右上角）": "老板分 4" in detail and "RP 来自对局中右上角" in detail,
        "修改表单：能改老板分": 'name="participation"' in detail and saved == 6,
    }


def main():
    print("0.3.0 新流程：对局中、一局结束、冠军、大厅校准、各来源的优先级")
    try:
        paths = [PLAYING, KNOCKED, MATES_DOWN, SPECTATING, CHAMP_MOMENT, CHAMP_SUMMARY, ELIMINATED, SUMMARY, START,
                 LOBBY, RANGE, SPECT_ENEMY, XP5, SUMMARY5]
        images = {p: load(shot(p)) for p in paths}
    except MissingShots as e:
        print(f"  ⏭ 截图部分跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        images = None
    r = rule_checks()
    checks = {
        **panel_checks(),
        f"大厅校准：只缺一局的 RP，用差值补上（{r['fill']}）": r["fill"] == (-45, "大厅校准"),
        f"大厅校准：都有 RP 但对不上，标待确认（{r['mismatch_note']}）": r["mismatch"] == (1,) and "大厅校准" in r["mismatch_note"],
        "大厅校准：对上了，不标": r["agree"] == (0,),
        "大厅校准：跨了大段（升段奖励、降段保护），不核对": r["cross_tier"] == (0,),
        "大厅校准：别的账号的局不算进来": r["other_account"] == (0,),
        f"对局中比总结页少（最后一秒没截到）：用总结页的，不标；老板分、RP 用对局中的（{r['hud_less']}）":
            r["hud_less"] == (3, 900, 2, 30, "对局中", 0),
        f"对局中比总结页多：用总结页的，标待确认（{r['hud_more']}）":
            r["hud_more"][:2] == (3, 1) and "对局中读到的击杀 4 多于总结页的 3" in r["hud_more"][2],
        f"总结页没读全（停留太短）：缺的项用对局中的补上，不用确认（{r['hud_fills']}）":
            r["hud_fills"] == (7, 2, 640, 0, None),
    }
    if images is None:
        return report(checks)
    s, c = screenshot_checks(images), collect_checks(images)
    last, first, revived, champ = c["last_down"], c["died_first"], c["revived"], c["champion"]
    pages = c["pages_first"]
    tops = [frame for kind, frame in pages["read_on"] if kind == "右上角"]
    order = [kind for kind, _ in pages["read_on"] if kind != "右上角"]
    match_sleeps = [sec for frame, sec in last["sleeps"] if frame in (START, PLAYING, KNOCKED)]
    checks.update({
        f"对局中的 3 张样本：自己的传奇、倒地、队友的名字和传奇、右上角各项都读对（错的：{s['hud_wrong'] or '无'}）":
            not s["hud_wrong"],
        "成盒观战：认不出自己的界面，认得出在观战": s["spectating"],
        f"其他画面都不算观战（算成观战的：{s['not_spectating'] or '无'}）": not s["not_spectating"],
        f"冠军横幅、冠军的总结页都认得出（{s['champion_screens']}）": s["champion_screens"] == ("champion", "summary"),
        f"总结页顶部：冠军是\"捍卫者\"，被淘汰的不是（{s['champion_banner']}）": s["champion_banner"] == (True, False),
        f"大厅：读出 ImperialHzd、钻石 II 944，总分 14944（{s['lobby']}）":
            s["lobby"] == ("ImperialHzd", "钻石", "II", 944, 14944),
        f"自己最后倒地时全队被淘汰：右上角最后一次读数就是最终数据，名次用淘汰画面的（{last['got']}）":
            last["got"] == [(17, 0, 0, 0, 557, -60, "对局中", 0, HUD_ONLY_NOTE, "ranked", "罗芭")],
        f"  名次各来源都记下：淘汰画面 17，对局中的剩余小队 12（{last['placement_sources']}）":
            last["placement_sources"] == {"淘汰画面": 17, "对局中": 12},
        f"  队友：对局中认出的名字和传奇（{last['mates']}）":
            len(last["mates"]) == 1 and same_mates(last["mates"][0], [("hud1", NAMES.get(PLAYING, [None, None])[0], "地平线"),
                                                                  ("hud2", NAMES.get(PLAYING, [None, None])[1], "疯玛吉")]),
        "  对局中每 0.3 秒一张，之后回到每秒一张": match_sleeps and max(match_sleeps) <= 0.3
                                       and last["sleeps"][-1][1] == 1.0,
        "  交火时不用识别引擎（识别只发生在开局画面、淘汰画面和大厅上）":
            last["ran_on"] and set(last["ran_on"]) <= {START, ELIMINATED, LOBBY},
        f"自己先成盒、队友后被淘汰：击杀、助攻、老板分、伤害用成盒前的，RP 不记（{first['got']}；成盒时的 RP {first['rp_at_death']}）":
            first["got"] == [(17, 0, 1, 0, 48, None, None, 0, HUD_ONLY_NOTE, "ranked", "希尔")]
            and first["rp_at_death"] == {"对局中": -59},
        f"自己是最后一个（成盒后观战的是对手），然后直接进了下一局：名次、RP 用右上角最后的，传奇没丢，不用确认（{c['last_one']['got']}）":
            c["last_one"]["got"] == [(18, 0, 0, 0, 0, -65, "对局中", 0, f"{HUD_ONLY_NOTE}；{REQUEUE_NOTE}", "ranked", "罗芭")],
        f"全队被淘汰后观战对手（右下角\"返回大厅\"）：这一局结束了，自己一直活到最后，名次、RP 用右上角最后的，不用确认（{c['squad_out']['got']}）":
            c["squad_out"]["got"] == [(18, 0, 0, 0, 0, -65, "对局中", 0, HUD_ONLY_NOTE, "ranked", "罗芭")]
            and bool(c["squad_out"]["said"]),
        f"停在总结页时只读总结页：右上角回到大厅才读（{tops}）": tops and set(tops) == {LOBBY},
        f"  从经验值页切到总结页：总结页读完、存下以后才读经验值页（{order}）":
            "经验值页" in order and order[:order.index("经验值页")].count("总结页") >= 2,
        f"  对局中的数据照样补进这一局：传奇、怎么结束的；RP 用经验值页的（{pages['got']}；{pages['end_screen']}）":
            pages["got"] == [(8, 3, 688, "经验值页", "罗芭")] and pages["end_screen"] == {"对局中": "squad_out"},
        f"0/0/0 时那一行不显示：背后画面上读出的单独一个数不算老板分（读到 {s['lone_kp'][0]}，记下 {s['lone_kp'][1]}）":
            s["lone_kp"] == (7, None, False),
        f"成盒后被复活：继续记录，RP 照常记（{revived['got']}）":
            revived["got"] == [(-60, "对局中", 557)] and any("被复活了" in line for line in revived["log"]),
        "保留全部截图时：一局结束存下最后一张自己的界面（用来校准对局中的区域）；平时不存":
            any("存下最后一张自己的界面" in line for line in revived["log"])
            and not any("存下最后一张自己的界面" in line for line in last["log"]),
        f"拿冠军：记第 1 名；总结页名次没读到也不用确认；RP 不用对局中的（冠军的名次分结束后才加）（{champ['got']}）":
            champ["got"] == [(1, 6, 1733, None, None, 0, None)],
        f"  名次来源：冠军横幅（{champ['placement_sources']}）": champ["placement_sources"].get("冠军横幅") == 1,
        f"  队友：总结页和对局中按名字对上，补上传奇（{champ['mates']}）":
            len(champ["mates"]) == 1 and any(same_mates([m], [("right", NAMES.get("冠军总结页右边"), "地平线")]) for m in champ["mates"][0]),
        f"右上角那一行显示着、击杀没读出来：击杀留空，不当作 0；助攻、老板分、伤害照常（{c['kills_unread']['got']}）":
            c["kills_unread"]["got"] == [(None, 1, 0, 48)],
        f"那一行显示着（骷髅图标在）、三个数字一个都没读出来：三项都留空（{c['row_unread']['got']}）":
            c["row_unread"]["got"] == [(None, None, None, 48)],
        "靶场（没有开局画面、也没有结算画面）：不记": c["range"]["rows"] == [],
        f"中途停止记录：记下，标待确认（{c['stopped']['got']}）":
            c["stopped"]["got"] == [(1, f"{NO_END_NOTE}；{HUD_ONLY_NOTE}", -65)],
        f"大厅只看到一帧：不记（{c['lobby_once']['lobby']}）": c["lobby_once"]["lobby"] == [],
        f"大厅连续两帧一样：记一次（{c['lobby_twice']['lobby']}）":
            c["lobby_twice"]["lobby"] == [("ImperialHzd", "钻石", "II", 944, 14944)],
    })
    ok = report(checks)
    if not ok:
        for name in ("last_down", "died_first", "champion"):
            print(f"\n  记录程序的日志（{name}）：")
            print("\n".join("    " + line for line in c[name]["log"]))
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
