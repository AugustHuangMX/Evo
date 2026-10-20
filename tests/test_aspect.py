"""游戏画面比例（16:9 / 16:10 / 4:3）：界面摆法的换算（layout.py）、自动检测（game_aspect.py）、设置页的选项，
以及用 16:10、4:3 实战截图把画面判断、读数、整局记录都走一遍。

2026-10-20 用户在 2560×1440（16:9）显示器上用 DX12 全屏 + 启动项 -w 2560 -h 1600 / -w 2560 -h 1920 各打了排位
（16:10 一局、4:3 两局），用 tools/frame_capture.py 每秒截一张整屏：截图都是 2560×1440，里面的界面按 16:10、4:3 摆好再压扁。
截图在 captures/aspect/（不在仓库里，见 fake_game.py；找不到就跳过这部分）。
- 换算和自动检测不用截图：16:9 时和以前逐像素一样；16:10、4:3 时按 anchor 往下移；压扁过的截图横竖各自拉伸
- 关键截图：告诉 Evo 游戏的比例后，每种画面都认得出、读得对；当作 16:9 时认不出（说明比例确实要紧）
- 整局重放：假游戏把截图按顺序喂给记录程序，视频设置文件（假的，放在临时文件夹）写着 2560×1600 / 2560×1920 全屏，
  记录程序自己检测出比例，存下的每一局和经验值页、大厅对得上
  4:3 的第二局不查传奇和老板分：这两局开局横幅只有"排位赛"、没有"空降地点"，开局画面认不出（见 todo.md），
  只能靠"上一局结束后过了 60 秒又看到自己的界面"认出新的一局；这 60 秒按真实时钟算，重放太快到不了（真打时隔了 90 秒）

运行：python tests/test_aspect.py
"""

import glob
import random
import re

from fake_game import SHOTS, _TEMP, load, report, run_collect  # 要在项目代码之前导入

import numpy as np

import app_settings
import game_aspect
import layout
import panel_app
import storage
from eliminated import read_eliminated
from hud import KP_ROW, Hud
from lobby import LobbyReader
from ocr import SMALL_CROP_PARAMS, create_engine, load_config
from screens import ScreenDetector
from summary_page import read_summary
from xp_page import read_xp

ASPECT_16_10 = SHOTS / "aspect" / "16x10_20261020"
ASPECT_4_3 = SHOTS / "aspect" / "4x3_20261020"
VIDEO_CONFIG = _TEMP / "videoconfig.txt"  # fake_game 把 EVO_VIDEOCONFIG 指到这里

# 关键截图（文件名前 4 位）→ 应该认成的画面和读数
KEY_16_10 = {
    "screens": {"0008": "lobby", "0299": "eliminated", "0305": "summary", "0320": "xp", "0326": "lobby"},
    "summary": ("0305", {"placement": 16, "squad_kills": 1, "kda": (1, 0, 2), "damage": 770, "survival_time": "2:53",
                         "revives": 0, "respawns": 0}),
    "xp": ("0320", {"rp_delta": -42, "placement": 16, "kills": 1, "assists": 0, "rank_tier": "白金",
                    "rank_division": "III", "rank_progress": 120}),
    "lobby": {"0008": ("白金", "III", 162), "0326": ("白金", "III", 120)},
    "eliminated": {"0299": 16},
    "hud": ("0271", "loba", True, {"squads_left": 16, "kills": 1, "assists": 0, "participation": 0, "damage": 770,
                                   "rp_delta": -42}),
}
KEY_4_3 = {
    "screens": {"0041": "lobby", "0283": "eliminated", "0291": "summary", "0361": "xp", "0681": "eliminated",
                "0688": "summary", "0700": "xp", "0703": "lobby"},
    "summary": ("0688", {"placement": 11, "squad_kills": 5, "kda": (2, 1, 3), "damage": 735, "survival_time": "4:38",
                         "revives": 1, "respawns": 0}),
    "xp": ("0700", {"rp_delta": -3, "placement": 11, "kills": 2, "assists": 1, "rank_tier": "白金",
                    "rank_division": "III", "rank_progress": 69}),
    "lobby": {"0041": ("白金", "III", 120), "0703": ("白金", "III", 69)},
    "eliminated": {"0283": 19, "0681": 11},
    "hud": ("0676", "loba", True, {"squads_left": 11, "kills": 2, "assists": 1, "participation": 2, "damage": 735,
                                   "rp_delta": -3}),
}
# 整局重放后数据库里每一局应有的数（截图上看得到的；经验值页、大厅前后之差都对得上）
ROWS_16_10 = [{"placement": 16, "squad_kills": 1, "kills": 1, "assists": 0, "knockdowns": 2, "participation": 0,
               "damage": 770, "survival_time": 173, "revives": 0, "respawns": 0, "rp_delta": -42, "rank_tier": "白金",
               "rank_division": "III", "rank_progress": 120, "legend": "罗芭", "needs_review": 0}]
ROWS_4_3 = [{"placement": 19, "squad_kills": 0, "kills": 0, "assists": 0, "knockdowns": 1, "participation": 0,
             "damage": 200, "survival_time": 109, "revives": 0, "respawns": 0, "rp_delta": -48, "rank_tier": "白金",
             "rank_division": "III", "rank_progress": 72, "legend": "罗芭", "needs_review": 0},
            {"placement": 11, "squad_kills": 5, "kills": 2, "assists": 1, "knockdowns": 3,
             "damage": 735, "survival_time": 278, "revives": 1, "respawns": 0, "rp_delta": -3, "rank_tier": "白金",
             "rank_division": "III", "rank_progress": 69, "needs_review": 0}]


def old_box(box, w, h, base=(1920, 1080)):
    """以前的换算：横竖各自按比例缩放。"""
    sx, sy = w / base[0], h / base[1]
    x, y, bw, bh = box
    return round(x * sx), round(y * sy), round((x + bw) * sx), round((y + bh) * sy)


def layout_checks():
    out = {}
    rng, base, same, total = random.Random(1), (1920, 1080), 0, 0
    for w, h in ((1920, 1080), (2560, 1440), (1280, 720), (1366, 768), (1360, 768), (3840, 2160)):
        for anchor in (None, "top", "center", "bottom", 0.87):
            m = layout.Mapping((h, w), base, anchor)
            for _ in range(500):
                box = [rng.randint(0, 1800), rng.randint(0, 1000), rng.randint(5, 400), rng.randint(5, 200)]
                total += 1
                same += m.box(box) == old_box(box, w, h)
    out[f"16:9 各种分辨率、各种 anchor：和以前逐像素一样（{same}/{total}）"] = same == total
    box = [100, 1000, 20, 10]
    ys = {a: layout.Mapping((1200, 1920), base, a).box(box)[1] for a in ("top", "center", "bottom", 0.87)}
    out[f"1920×1200（16:10）：贴上边 / 居中 / 贴下边 / 0.87 往下移 0 / 60 / 120 / 104（{ys}）"] = \
        ys == {"top": 1000, "center": 1060, "bottom": 1120, 0.87: 1104}
    ys43 = layout.Mapping((1080, 1440), base, "bottom").box(box)
    out[f"1440×1080（4:3）：按宽缩放 0.75 倍，贴下边往下移 360（{ys43}）"] = ys43 == (75, 1020, 90, 1028)
    squeezed = layout.Mapping((1440, 2560), base, "bottom", aspect=16 / 10).box(box)
    out[f"2560×1440 的截图、游戏是 16:10（DX12 拉伸）：先按 16:10 摆、再压扁（{squeezed}）"] = \
        squeezed == (133, 1344, 160, 1356)
    out["比 16:9 更宽的（21:9，还没校准）：照旧横竖各自拉伸"] = \
        layout.Mapping((1440, 3440), base, "bottom").box(box) == old_box(box, 3440, 1440)
    return out


def write_video_config(fullscreen, w, h, encoding="utf-16"):
    VIDEO_CONFIG.write_text('"VideoConfig"\n{\n'
                            f'\t"setting.fullscreen"\t\t"{fullscreen}"\n\t"setting.nowindowborder"\t\t"1"\n'
                            f'\t"setting.defaultres"\t\t"{w}"\n\t"setting.defaultresheight"\t\t"{h}"\n}}\n',
                            encoding=encoding)


def detect_checks():
    out = {}
    write_video_config(1, 2560, 1600)
    out["视频设置文件（UTF-16）：全屏 2560×1600 → 16:10"] = game_aspect.from_video_config()[0] == 1.6
    write_video_config(1, 2560, 1920, encoding="utf-8")
    out["视频设置文件（UTF-8）：全屏 2560×1920 → 4:3"] = game_aspect.name(game_aspect.from_video_config()[0]) == "4:3"
    write_video_config(0, 2560, 1600)
    out["不是全屏（文件里的分辨率不一定在用）：按截图的比例"] = game_aspect.from_video_config()[0] is None
    VIDEO_CONFIG.write_text("乱写的内容", encoding="utf-8")
    out["文件里没有分辨率：按截图的比例"] = game_aspect.from_video_config()[0] is None
    VIDEO_CONFIG.unlink()
    out["没有这个文件：按截图的比例"] = game_aspect.from_video_config()[0] is None

    frame, log = np.zeros((1440, 2560, 3), np.uint8), []
    write_video_config(1, 2560, 1920)
    clock = [0.0]
    watcher = game_aspect.AspectWatcher(log.append, clock=lambda: clock[0])
    watcher.check(frame)
    auto = layout.game_aspect()
    app_settings.set_value(game_aspect.SETTING_KEY, "16:10")
    watcher.check(frame)
    too_soon = layout.game_aspect()
    clock[0] += game_aspect.CHECK_SECONDS
    watcher.check(frame)
    manual = layout.game_aspect()
    app_settings.set_value(game_aspect.SETTING_KEY, "auto")
    clock[0] += game_aspect.CHECK_SECONDS
    watcher.check(frame)
    back = layout.game_aspect()
    watcher.reset()
    out[f"记录程序：自动检测 4:3；设置页改成 16:10，下一次看（每 {game_aspect.CHECK_SECONDS} 秒）就换；改回自动又是 4:3；"
        f"每次变了写一行日志（{len(log)} 行）"] = (
        (auto, too_soon, manual, back) == (4 / 3, 4 / 3, 1.6, 4 / 3) and len(log) == 3 and "4:3" in log[0]
        and "设置页手动选的 16:10" in log[1])
    out["停止记录后换算回\"和截图一样\""] = layout.game_aspect() is None
    VIDEO_CONFIG.unlink()
    return out


def panel_checks():
    out = {}
    panel_app.app.config["DB"] = str(_TEMP / "panel.db")
    storage.connect(panel_app.app.config["DB"]).close()
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]
    write_video_config(1, 2560, 1600)
    page = client.get("/settings").get_data(as_text=True)
    out["设置页：有\"游戏画面比例\"下拉框（默认自动检测），写出自动检测的结果"] = (
        'for="aspect-select"' in page and re.search(r'<option value="auto" selected>', page) is not None
        and "自动检测结果：" in page and "全屏 2560×1600" in page)
    saved = client.post("/settings/game-aspect", data={"token": token, "aspect": "4:3"}, follow_redirects=True)
    out["选 4:3 保存：记进设置，页面上选中 4:3，顶部提示"] = (
        app_settings.get(game_aspect.SETTING_KEY) == "4:3" and '<option value="4:3" selected>' in saved.get_data(as_text=True)
        and "游戏画面比例已改成" in saved.get_data(as_text=True))
    out["不认识的值（21:9）：拒绝"] = client.post("/settings/game-aspect", data={"token": token, "aspect": "21:9"}).status_code == 400
    out["没带口令：拒绝"] = client.post("/settings/game-aspect", data={"aspect": "auto"}).status_code != 302
    app_settings.set_value(game_aspect.SETTING_KEY, "auto")
    VIDEO_CONFIG.unlink()
    return out


def frame_of(folder, prefix):
    return load(glob.glob(str(folder / f"{prefix}_*.jpg"))[0])


def screenshot_checks(engine, folder, aspect, key):
    out = {}
    detector, hud, lobby = ScreenDetector(engine), Hud(engine), LobbyReader(engine)
    images = {p: frame_of(folder, p) for p in {*key["screens"], *key["lobby"], *key["eliminated"], key["hud"][0]}}
    layout.set_game_aspect(None)
    as_16_9 = [p for p, want in key["screens"].items() if detector.detect(images[p]) == want]
    out[f"当作 16:9 时认不出（{len(as_16_9)}/{len(key['screens'])} 张认得出）"] = not as_16_9
    layout.set_game_aspect(aspect)
    got = {p: detector.detect(img) for p, img in images.items() if p in key["screens"]}
    out[f"画面判断：大厅、淘汰画面、总结页、经验值页都认得出（{got}）"] = got == key["screens"]
    prefix, want = key["summary"]
    results, _ = read_summary(engine, frame_of(folder, prefix), load_config("summary.json"))
    got = {k: results[k]["value"] for k in want}
    out[f"总结页 {prefix}：{got}"] = got == want
    prefix, want = key["xp"]
    xp, _ = read_xp(engine, frame_of(folder, prefix), load_config("xp.json"))
    got = {k: xp[k] for k in want}
    out[f"经验值页 {prefix}：{got}"] = got == want
    got = {p: (r["tier"], r["division"], r["progress"]) for p in key["lobby"] for r in [lobby.read(images[p])]}
    out[f"大厅段位和段内分数：{got}"] = got == key["lobby"]
    got = {p: read_eliminated(engine, images[p], load_config("eliminated.json"))["placement"] for p in key["eliminated"]}
    out[f"淘汰画面名次：{got}"] = got == key["eliminated"]
    prefix, legend, knocked, want = key["hud"]
    img = images[prefix]
    values, _, _ = hud.read_top(hud.top_snip(img))
    got = (hud.own_legend(img), hud.knocked(img), {k: values[k] for k in want})
    out[f"对局中 {prefix}：自己的传奇、倒地、右上角 {got}"] = got == (legend, knocked, want)
    layout.set_game_aspect(None)
    return out


def kp_row_checks(engine):
    """击杀 / 助攻 / 老板分那一行（先找图标再读数字）：16:10 这局第一次击杀以后，每张自己的界面都应读出 1 / 0 / 0。
    以前按固定位置读，"1"比"0"窄、后面的数字往左挪，助攻一次都没读出来。"""
    hud = Hud(engine)
    layout.set_game_aspect(16 / 10)
    counts = {"对": 0, "留空": 0, "错": 0, "没显示": 0}
    for path in sorted(glob.glob(str(ASPECT_16_10 / "*.jpg")))[159:271]:  # 第一次击杀（0160）以后到成盒
        img = load(path)
        if hud.own_legend(img) is None:
            continue
        values, _, shown = hud.read_top(hud.top_snip(img))
        got = [values[k] for k in KP_ROW]
        if not set(KP_ROW) <= shown:
            counts["没显示"] += 1
        elif got == [1, 0, 0]:
            counts["对"] += 1
        elif all(v in (want, None) for v, want in zip(got, (1, 0, 0))):
            counts["留空"] += 1
        else:
            counts["错"] += 1
    layout.set_game_aspect(None)
    total = sum(counts.values())
    return {f"16:10 这局击杀那一行：{counts}（读错的为 0，读出全部三项的至少 90%）":
            counts["错"] == 0 and counts["没显示"] == 0 and counts["对"] >= 0.9 * total}


def replay_checks(folder, w, h, want_rows, ratio):
    write_video_config(1, w, h)
    rows, _, log = run_collect(sorted(glob.glob(str(folder / "*.jpg"))), load)
    VIDEO_CONFIG.unlink()
    out = {f"自动检测出 {ratio}（日志：{next((l for l in log if '游戏画面比例' in l), '没有')}）":
           any(l.startswith(f"游戏画面比例：{ratio}（自动检测") for l in log)}
    out[f"记下 {len(want_rows)} 局（{len(rows)}）"] = len(rows) == len(want_rows)
    for i, (row, want) in enumerate(zip(rows, want_rows), 1):
        got = {k: row[k] for k in want}
        out[f"第 {i} 局：{got}"] = got == want
    out["每局都认出了玩家名"] = bool(rows) and all(r["player_name"] for r in rows)
    out["大厅校准：前后两次大厅总分之差和中间各局 RP 加起来对得上"] = any("对上了" in l for l in log)
    return out


def main():
    print("游戏画面比例（16:9 / 16:10 / 4:3）")
    checks = {**layout_checks(), **detect_checks(), **panel_checks()}
    if not (ASPECT_16_10.is_dir() and ASPECT_4_3.is_dir()):
        print(f"  ⏭ 跳过截图部分：找不到 {ASPECT_16_10} 或 {ASPECT_4_3}（截图不在仓库里）")
        return report(checks)
    engine = create_engine(SMALL_CROP_PARAMS)
    for name, folder, aspect, key in (("16:10", ASPECT_16_10, 16 / 10, KEY_16_10), ("4:3", ASPECT_4_3, 4 / 3, KEY_4_3)):
        checks.update({f"{name}：{k}": v for k, v in screenshot_checks(engine, folder, aspect, key).items()})
    checks.update(kp_row_checks(engine))
    checks.update({f"16:10 整局重放：{k}": v for k, v in replay_checks(ASPECT_16_10, 2560, 1600, ROWS_16_10, "16:10").items()})
    checks.update({f"4:3 两局重放：{k}": v for k, v in replay_checks(ASPECT_4_3, 2560, 1920, ROWS_4_3, "4:3").items()})
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
