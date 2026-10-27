"""淘汰画面：小队被淘汰时屏幕中间的"排名第 X"。认得出、读得对、不会把别的画面当成它；
读到的名次和总结页、经验值页的名次互相核对，各来源的读数记进 readings 表；只看到淘汰画面的局也记一局。

用的是 2 张 1080p 录像截图（一张还有"全军覆没"那行，另一张那行已经消失、"排名第"三个字大了约 6%），
把第一张整张放大、缩小（模拟字再大一些或小一些的时候），外加 captures/ 里其他所有截图（总结页、经验值页、开局画面、大厅、观战等）当作"不是淘汰画面"的例子。
记录程序的测试再加一张 1080p 总结页（第 6 名；把名次那块涂黑，就是"总结页没读到名次"）和一张开局画面。
"归到哪一局"的规则另外直接测 PostMatch（不用截图，时间可以随便指定）。
截图不在仓库里，见 fake_game.py。

运行：python tests/test_eliminated.py
"""

from datetime import datetime, timedelta
from pathlib import Path

from fake_game import SHOTS, MissingShots, load, report, run_collect, shot  # 要在项目代码之前导入

import cv2

import fake_game
import storage
from eliminated import read_eliminated
from ocr import SMALL_CROP_PARAMS, LazyEngine, load_config
from post_match import ELIM_ONLY_NOTE, XP_ONLY_NOTE, PostMatch
from screens import ScreenDetector

SAMPLES = {"samples/死亡瞬间.png": 17, "samples/vlcsnap-2026-10-16-18h27m05s111.png": 16}
SPECTATE = "samples/vlcsnap-2026-10-16-18h27m31s652.png"  # 观战画面，不是要认的画面
SUMMARY = "samples/vlcsnap-2026-10-16-18h26m12s173.png"  # 1080p 总结页，第 6 名
MATCH_START = "live/20261016_211452/match_start_213625_enter.png"  # 开局画面：排位、世界尽头
ZOOMS = (0.9, 1.15)  # 字比参照图小 10%、大 15%


def zoom(img, factor):
    """整张图以中心为准放大或缩小 factor 倍，大小不变（"排名第 X"正好在画面中间，相当于只有字变大变小）。"""
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 0, factor)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def no_placement(img):
    """把总结页右上角的名次涂黑（1080p 截图，区域见 config/summary.json），相当于名次还没滑进来。"""
    x, y, w, h = load_config("summary.json")["fields"]["placement"]["roi"]
    out = img.copy()
    out[y:y + h, x:x + w] = 0
    return out


def placement_readings(match_id):
    """这一局 readings 表里各来源读到的名次：{来源: 名次}。"""
    conn = storage.connect(fake_game.last_db)
    got = {r["source"]: r["value"] for r in storage.readings_of(conn, match_id, "placement")}
    conn.close()
    return got


def collect_checks(images):
    """记录程序（用截图）：淘汰画面的名次写进数据库。"""
    pictures = {"观战": images[SPECTATE], "开局": images[MATCH_START], "总结6": images[SUMMARY],
                "总结缺名次": no_placement(images[SUMMARY]),
                **{f"淘汰{v}": images[k] for k, v in SAMPLES.items()}}
    out = {}

    # 只看到淘汰画面的两局；第一局里又出现一次别的名次（只认第一次）
    frames = ["开局", "观战", "淘汰17", "淘汰17", "淘汰17", "观战", "淘汰16", "观战", "开局", "观战", "淘汰16", "观战"]
    rows, _, log = run_collect(frames, pictures.get, keep_all=True)
    out["only"] = [(r["placement"], r["needs_review"], r["review_note"], r["mode"], r["map"]) for r in rows]
    out["only_said"] = [line.split("] ")[1] for line in log if "淘汰画面：" in line or "只认第一次" in line]
    out["only_saved_shot"] = any("进入淘汰画面，已存截图" in line for line in log)
    out["only_readings"] = [placement_readings(r["match_id"]) for r in rows]

    # 总结页没读到名次，用淘汰画面补上；总结页第 6、淘汰画面第 17，对不上。
    # 两局用的是同一张总结页，放在一次记录里会被当成"回到了同一局的总结页"，所以分两次记录
    out["both"], out["both_readings"], out["both_log"] = [], [], []
    for summary in ("总结缺名次", "总结6"):
        rows, _, log = run_collect(["开局", "淘汰17", "观战", summary, summary, "观战"], pictures.get)
        out["both"] += [(r["placement"], r["needs_review"], r["review_note"]) for r in rows]
        out["both_readings"] += [placement_readings(r["match_id"]) for r in rows]
        out["both_log"] += log
    return out


def post(log):
    return PostMatch(storage.connect(":memory:"), log=log.append)


def summary_match(p, placement, when):
    """存一局总结页（只有名次），像记录程序那样先把总结页的读数记进 readings 表，再交给 PostMatch。"""
    match_id = storage.insert_match(p.conn, {"placement": placement, "ended_at": f"{when:%Y-%m-%d %H:%M:%S}"})
    storage.save_readings(p.conn, match_id, "总结页", {"placement": placement}, f"{when:%Y-%m-%d %H:%M:%S}")
    p.on_summary_saved(match_id, {"placement": placement}, when)


def xp_reading(placement):
    return {"rp_delta": 181, "placement": placement, "kills": 2, "assists": 3,
            "rank_tier": "钻石", "rank_division": "I", "rank_progress": 94}


def matches(p):
    return [(r["placement"], r["needs_review"], r["review_note"])
            for r in p.conn.execute("SELECT * FROM matches ORDER BY match_id")]


def rule_checks():
    """归到哪一局的规则（直接测 PostMatch，不用截图）。"""
    t0, out = datetime(2026, 10, 18, 21, 0, 0), {}

    log = []  # 总结页之后 5 分钟又来了淘汰画面：是新的一局（没认出开局画面），上一局先收尾
    p = post(log)
    summary_match(p, 6, t0)
    p.on_eliminated(9, 0.99, t0 + timedelta(minutes=5))
    p.finish()
    out["new_round"] = matches(p)

    log = []  # 总结页之后 20 秒又看到淘汰画面（切回"实况"），名次一样：算同一局，核对通过
    p = post(log)
    summary_match(p, 6, t0)
    p.on_eliminated(6, 0.99, t0 + timedelta(seconds=20))
    p.finish()
    out["late_same"] = (matches(p), [line for line in log if "名次核对" in line])

    log = []  # 只有经验值页（第 2 名），淘汰画面读到第 17：对不上，标待确认，"只有经验值页"的说明还在
    p = post(log)
    p.on_eliminated(17, 0.99, t0)
    p.on_xp(xp_reading(2), [], Path("xp.png"), t0 + timedelta(seconds=30))
    p.finish()
    out["xp_conflict"] = matches(p)

    log = []  # 只有经验值页，和淘汰画面都是第 2：不标待确认
    p = post(log)
    p.on_eliminated(2, 0.99, t0)
    p.on_xp(xp_reading(2), [], Path("xp.png"), t0 + timedelta(seconds=30))
    p.finish()
    out["xp_agree"] = matches(p)

    log = []  # 一局打完回大厅，在大厅里又打开上一局的经验值页（一次读全、一次只读到一部分）：不再记；
    p = post(log)  # 之后来了一张读数不一样的经验值页（这一局别的都没看到）：照常记
    summary_match(p, 2, t0)
    p.on_xp(xp_reading(2), [], Path("xp.png"), t0 + timedelta(seconds=15))
    p.finish()
    p.on_xp(xp_reading(2), [], Path("xp2.png"), t0 + timedelta(minutes=2))
    p.finish()
    partial = {**xp_reading(2), "rp_delta": None, "kills": None}
    p.on_xp(partial, ["RP：明细没读全"], Path("xp3.png"), t0 + timedelta(minutes=3))
    p.finish()
    p.on_xp({**xp_reading(15), "rp_delta": -40, "rank_progress": 54}, [], Path("xp4.png"), t0 + timedelta(minutes=30))
    p.finish()
    out["xp_reopen"] = (matches(p), [line.split("] ")[1] for line in log if "上一局的经验值页" in line])

    log = []  # 下一局真的只有经验值页（看到了淘汰画面），读数碰巧和上一局一样：照常记
    p = post(log)
    summary_match(p, 2, t0)
    p.on_xp(xp_reading(2), [], Path("xp.png"), t0 + timedelta(seconds=15))
    p.finish()
    p.on_eliminated(2, 0.99, t0 + timedelta(minutes=20))
    p.on_xp(xp_reading(2), [], Path("xp2.png"), t0 + timedelta(minutes=20, seconds=30))
    p.finish()
    out["xp_next"] = matches(p)

    log = []  # 下一局只有经验值页，对局中的数据要回到大厅才交来（先读结算画面）、离开经验值页时还没到，读数碰巧和上一局一样：照常记
    p = post(log)
    summary_match(p, 2, t0)
    p.on_xp(xp_reading(2), [], Path("xp.png"), t0 + timedelta(seconds=15))
    p.finish()
    p.on_hud_end(t0 + timedelta(minutes=20))
    p.on_xp(xp_reading(2), [], Path("xp2.png"), t0 + timedelta(minutes=20, seconds=30))
    p.finish()
    out["xp_hud_later"] = matches(p)
    return out


def main():
    print("淘汰画面（排名第 X）")
    try:
        images = {relative: load(shot(relative)) for relative in [*SAMPLES, SPECTATE, SUMMARY, MATCH_START]}
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None

    engine = LazyEngine(SMALL_CROP_PARAMS)
    detector = ScreenDetector(engine)
    cfg = load_config("eliminated.json")
    found = {relative: detector.detect(images[relative]) for relative in SAMPLES}
    read = {relative: read_eliminated(engine, images[relative], cfg)["placement"] for relative in SAMPLES}
    zoomed = [zoom(images["samples/死亡瞬间.png"], f) for f in ZOOMS]
    zoom_ok = [detector.detect(z) == "eliminated" and read_eliminated(engine, z, cfg)["placement"] == 17 for z in zoomed]
    zoom_text = "、".join(f"{f} 倍 {'✓' if ok else '✗'}" for f, ok in zip(ZOOMS, zoom_ok))

    # 其他截图只做外观检查（不跑识别）：一张都不该像淘汰画面
    others = [p for p in SHOTS.rglob("*.png") if p.relative_to(SHOTS).as_posix() not in SAMPLES
              and not {"debug_crops", "legends"} & set(p.relative_to(SHOTS).parts)]  # 这两个文件夹里是小块，不是整屏
    alike = [p.name for p in others if detector.looks_like(load(p), "eliminated")]

    c, r = collect_checks(images), rule_checks()
    mismatch = "名次对不上：总结页第 6、淘汰画面第 17，请核对"
    ok = report({
        "两张样本都认出是淘汰画面（包括\"全军覆没\"已经消失、字变大的那张）": all(v == "eliminated" for v in found.values()),
        f"读出名次 17 和 16（读到：{list(read.values())}）": list(read.values()) == list(SAMPLES.values()),
        f"字小 10%、大 15% 也认得出、读得对（{zoom_text}）": all(zoom_ok),
        f"其他 {len(others)} 张截图没有一张像淘汰画面（像的：{'、'.join(alike) or '无'}）": others and not alike,
        "记录程序：每次停在淘汰画面只读一次名次；同一局又看到别的名次，只认第一次": c["only_said"]
        == ["淘汰画面：排名第 17", "淘汰画面：排名第 16", "这一局又看到\"排名第 16\"（之前是第 17），只认第一次", "淘汰画面：排名第 16"],
        "保留全部截图时，淘汰画面也存截图（攒样本）": c["only_saved_shot"],
        f"只看到淘汰画面的局也记一局：只有名次，带上开局画面的模式、地图，备注说明缺什么，不标待确认（{c['only']}）":
            c["only"] == [(17, 0, ELIM_ONLY_NOTE, "ranked", "世界尽头"), (16, 0, ELIM_ONLY_NOTE, "ranked", "世界尽头")],
        f"总结页没读到名次：用淘汰画面的第 17 名补上，取消待确认（{c['both'][:1]}）": c["both"][:1] == [(17, 0, None)],
        f"总结页和淘汰画面的名次对不上：先记总结页的，标待确认，备注写出两个名次（{c['both'][1:]}）":
            c["both"][1:] == [(6, 1, mismatch)],
        f"各来源的读数都记进 readings 表，没读到的也记（{c['only_readings'] + c['both_readings']}）":
            c["only_readings"] == [{"淘汰画面": 17}, {"淘汰画面": 16}]
            and c["both_readings"] == [{"总结页": None, "淘汰画面": 17}, {"总结页": 6, "淘汰画面": 17}],
        f"总结页之后 5 分钟又来了淘汰画面：算新的一局（{r['new_round']}）":
            r["new_round"] == [(6, 0, None), (9, 0, ELIM_ONLY_NOTE)],
        f"总结页之后 20 秒又看到同样的名次：算同一局，核对通过（{r['late_same']}）":
            r["late_same"] == ([(6, 0, None)], ["           名次核对：总结页、淘汰画面都是第 6（编号 1）"]),
        f"只有经验值页的局：和淘汰画面对不上就标待确认，\"只有经验值页\"的说明还在（{r['xp_conflict']}）":
            r["xp_conflict"] == [(2, 1, f"名次对不上：经验值页第 2、淘汰画面第 17，请核对；{XP_ONLY_NOTE}")],
        "只有经验值页的局：和淘汰画面一样就不标待确认": r["xp_agree"] == [(2, 0, XP_ONLY_NOTE)],
        f"在大厅里重新打开上一局的经验值页（读全的、只读到一部分的）：不再记一局，日志说一声；读数不一样的照常记（{r['xp_reopen']}）":
            r["xp_reopen"] == ([(2, 0, None), (15, 0, XP_ONLY_NOTE)], ["回到了上一局的经验值页（在大厅里打开的），不重复记"] * 2),
        f"下一局真的只有经验值页（前面有淘汰画面），读数碰巧和上一局一样：照常记（{r['xp_next']}）":
            r["xp_next"] == [(2, 0, None), (2, 0, XP_ONLY_NOTE)],
        f"下一局只有经验值页、对局中的数据还没交来（只知道结束了），读数碰巧和上一局一样：照常记（{r['xp_hud_later']}）":
            r["xp_hud_later"] == [(2, 0, None), (2, 0, XP_ONLY_NOTE)],
    })
    if not ok:
        print("\n  记录程序的日志（总结页 + 淘汰画面）：")
        print("\n".join("    " + line for line in c["both_log"]))
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
