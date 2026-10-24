"""截图留不留（见 screenshots.py）：识别完就删，只留总结页截图（转成 JPG）和"待确认"的局要用的；
"保留全部截图"时和以前一样全部留着（原图）。

- 记录程序：用 2026-10-16 晚的真实截图整晚重放（同 test_replay.py）。截图不在仓库里（见 fake_game.py），找不到就跳过这一部分。
  还重放前三局、在第 2 局中途改"保留全部截图"开关：从下一局开始生效
- 战绩页（用假的截图文件和 Flask 测试客户端，不用真截图）：确认、删除一局时删掉它的截图并提示；总结页截图确认时留着、
  删除这一局时才删；不删演示数据的样本截图、别的局也在用的截图；保存失败不删；保留全部截图时不删、也不提示
- 设置页（同上，用假截图）："保留全部截图"开关；"删除已识别完的截图"按钮：以前存下的也删，
  总结页截图转成 JPG 留着（截图位置跟着改），待确认的局要用的留着，正在记录的文件夹不动；开关打开时也能用；
  用源码运行时开关和按钮都不能用
- 转 JPG：一张小图转完 PNG 删掉、JPG 打得开；不是图片的转不了，PNG 原样留着

运行：python tests/test_screenshots.py
"""

import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fake_game import MissingShots, report, run_collect, shot  # 要在项目代码之前导入（设置临时数据文件夹）

from PIL import Image  # noqa: E402

import app_settings  # noqa: E402
import live_collect  # noqa: E402
import panel_app  # noqa: E402
import screenshots  # noqa: E402
import storage  # noqa: E402
from paths import CAPTURES_DIR, data_absolute, data_relative  # noqa: E402
from test_replay import NIGHT, night_frames, picture, shot_time  # noqa: E402


def run(frames, keep_all):
    """重放一段画面，返回 (对局, 这次记录存过的截图文件名, 截图文件夹, 日志)。"""
    saved = []
    original = live_collect.save_png

    def spy(path, frame):
        saved.append(Path(path).name)
        original(path, frame)

    with mock.patch.object(live_collect, "save_png", spy):
        rows, _, log = run_collect(frames, picture, keep_all=keep_all)
    folder = Path(next(re.search(r"截图保存到: (.+?)（", line)[1] for line in log if line.startswith("截图保存到")))
    return rows, saved, folder, log


def run_switch(frames, start, flip_at):
    """重放时中途在设置页改开关：一开始是 start，喂到 flip_at（截图时间，如 "214000"）以后改成相反的。
    返回 (对局, [(存下的截图, 存的时候正在喂哪个时间的画面)], 日志)。"""
    fed = {"time": "000000"}  # 正在喂的画面的截图时间

    def feed(path):
        if path is not None:
            fed["time"] = shot_time(path)
        return picture(path)

    saved = []
    original = live_collect.save_png

    def spy(path, frame):
        saved.append((Path(path), fed["time"]))
        original(path, frame)

    def switch():
        return start if fed["time"] < flip_at else not start

    with mock.patch.object(live_collect, "save_png", spy), mock.patch.object(screenshots, "keep_all", switch):
        rows, _, log = run_collect(frames, feed, keep_all=None)
    return rows, saved, log


def switch_checks(frames):
    """记录中途改"保留全部截图"开关：从下一局开始生效（2026-10-17 用户要求）。重放前三局，在第 2 局中途改。"""
    games = [f for f in frames if f is None or shot_time(f) < "220834"]  # 第 1–3 局（到第 4 局开局画面之前）
    game2, game3 = "213625", "215021"  # 第 2、3 局开局画面的时间
    screens = ("lobby_", "match_start_")

    def kept(rows, i):
        return rows[i]["evidence_path"] is not None and data_absolute(rows[i]["evidence_path"]).is_file()

    def tidied(rows, i):
        """识别完处理过：截图位置清空，或者是转成 JPG 的总结页截图。"""
        path = rows[i]["evidence_path"]
        return path is None or (path.endswith(".jpg") and screenshots.is_summary(path) and data_absolute(path).is_file())

    rows, saved, log = run_switch(games, start=True, flip_at="214000")
    before = [p for p, t in saved if t < game3]
    after = [p for p, t in saved if t >= game3]
    checks = {
        "记录中途关掉开关：之前存下的（第 1、2 局，包括大厅、开局画面）都不删": all(p.exists() for p in before)
        and any(p.name.startswith(screens) for p in before) and kept(rows, 0) and kept(rows, 1),
        "记录中途关掉开关：从下一局（第 3 局）开始大厅、开局画面不存，识别完就删（总结页截图转成 JPG）": len(rows) == 3
        and after and not any(p.name.startswith(screens) for p in after) and not any(p.exists() for p in after)
        and tidied(rows, 2) and any("关掉了\"保留全部截图\"" in line for line in log),
    }

    rows, saved, log = run_switch(games, start=False, flip_at="214000")
    game1 = [p for p, t in saved if t < game2]
    middle = [p for p, t in saved if game2 <= t < game3]
    after = [p for p, t in saved if t >= game3]
    checks.update({
        "记录中途打开开关：改之前结束的第 1 局照样识别完就删，大厅、开局画面不存": game1 and not any(p.exists() for p in game1)
        and tidied(rows, 0) and not any(p.name.startswith(screens) for p in game1 + middle),
        "记录中途打开开关：改的时候正在打的第 2 局也不删": middle and all(p.exists() for p in middle) and kept(rows, 1),
        "记录中途打开开关：从下一局（第 3 局）开始全部保存": any(p.name.startswith("match_start_") for p in after)
        and all(p.exists() for p in after) and kept(rows, 2) and any("打开了\"保留全部截图\"" in line for line in log),
    })
    return checks


def files_in(folder):
    return {p.name for p in folder.iterdir() if p.suffix in (".png", ".jpg")} if folder.is_dir() else set()


def jpg_name(name):
    return Path(name).with_suffix(".jpg").name


def image(path, seed=0):
    """一张能打开的小 PNG（像截图一样有点内容），测试转 JPG 用。"""
    Image.effect_noise((96, 54), 40 + seed).convert("RGB").save(path, "PNG")
    return path


def jpg_checks():
    folder = Path(tempfile.mkdtemp(prefix="evo-jpg-", dir=storage.DB_PATH.parent.parent))
    png = image(folder / "01_120000.png")
    size = png.stat().st_size
    saved = screenshots.to_jpg(png)
    with Image.open(folder / "01_120000.jpg") as jpg:
        opened = jpg.format == "JPEG" and jpg.size == (96, 54)
    broken = folder / "02_120100.png"
    broken.write_bytes(b"not a png")
    return {
        "总结页截图按文件名认（第几局_时间），经验值页、对局中的不算": screenshots.is_summary("01_213331.png")
        and screenshots.is_summary("x/123_000000.jpg") and not screenshots.is_summary("xp_213328_leave.png")
        and not screenshots.is_summary("hud_213000_last.png") and not screenshots.is_summary(None),
        "转 JPG：PNG 删掉、JPG 打得开，返回省下多少": not png.exists() and opened
        and saved == size - (folder / "01_120000.jpg").stat().st_size,
        "转不了（不是图片）：PNG 原样留着，不留半个 JPG": screenshots.to_jpg(broken) is None and broken.exists()
        and not (folder / "02_120100.jpg").exists() and not list(folder.glob("*.tmp")),
    }


@contextmanager
def keep_setting(value):
    """临时把"保留全部截图"开关设成 value，用完恢复原来的设置。"""
    before = app_settings.load()
    try:
        app_settings.set_value(screenshots.KEEP_ALL_KEY, value)
        yield
    finally:
        app_settings.SETTINGS_PATH.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")


def rule_checks():
    """什么时候保留全部截图。"""
    with keep_setting(False):
        off = screenshots.keep_all()
        with mock.patch.object(screenshots, "PROJECT_CAPTURES", CAPTURES_DIR):  # 假装截图存在项目的 captures/
            project = screenshots.keep_all()
    with keep_setting(True):
        on = screenshots.keep_all()
    return {
        "开关关着（默认）：识别完就删": off is False,
        "开关打开：保留全部": on is True,
        "截图存在项目的 captures/（用源码运行）：不管开关，永远保留全部（项目里的截图是测试集）": project is True,
    }


def panel_checks():
    """战绩页确认、删除一局时删掉它的截图。"""
    live = CAPTURES_DIR / "live" / "20261017_120000"
    live.mkdir(parents=True, exist_ok=True)
    sample = CAPTURES_DIR / "samples" / "sample.png"  # 演示数据用的样本截图（不是记录程序存的）
    sample.parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="evo-shots-", dir=storage.DB_PATH.parent.parent))
    conn = storage.connect(folder / "panel.db")

    def add(evidence):
        """存一局待确认的局，截图是 evidence（不存在就建一个假的）。返回 (编号, 截图)。"""
        evidence.write_bytes(b"png")
        match_id = storage.insert_match(conn, {"ended_at": "2026-10-17 12:00:00", "needs_review": 1,
                                               "review_note": "名次：没读到", "evidence_path": data_relative(evidence)})
        return match_id, evidence

    confirm, sample_m, delete, shared, bad, kept = (add(live / "01.png"), add(sample), add(live / "02.png"),
                                                    add(live / "03.png"), add(live / "04.png"), add(live / "05.png"))
    summary_ok, summary_del = add(live / "06_120500.png"), add(live / "07_120600.png")  # 总结页截图
    for _, path in (summary_ok, summary_del):
        image(path)
    storage.insert_match(conn, {"ended_at": "2026-10-17 12:01:00", "evidence_path": data_relative(shared[1])})  # 也用 03.png
    panel_app.app.config.update(DB=folder / "panel.db", TESTING=True)
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]

    def save(match, placement="5"):
        data = {"token": token, "next": "/matches", "placement": placement}
        return client.post(f"/matches/{match[0]}/update", data=data, follow_redirects=True).get_data(as_text=True)

    def row(match):
        return storage.get_match(conn, match[0])

    with keep_setting(False):
        page = client.get(f"/matches?open={confirm[0]}").get_data(as_text=True)
        saved_page = save(confirm)
        sample_page = client.get(f"/matches?open={sample_m[0]}").get_data(as_text=True)
        save(sample_m)
        deleted_page = client.post(f"/matches/{delete[0]}/delete", data={"token": token, "next": "/matches"},
                                   follow_redirects=True).get_data(as_text=True)
        save(shared)
        save(bad, placement="abc")
        summary_page = client.get(f"/matches?open={summary_ok[0]}").get_data(as_text=True)
        summary_saved = save(summary_ok)
        summary_deleted = client.post(f"/matches/{summary_del[0]}/delete", data={"token": token, "next": "/matches"},
                                      follow_redirects=True).get_data(as_text=True)
    with keep_setting(True):
        kept_page = client.get(f"/matches?open={kept[0]}").get_data(as_text=True)
        save(kept)
    return {
        "战绩页：确认按钮下面提示\"确认后，这一局的截图会删除\"，删除确认框里也写了": "确认后，这一局的截图会删除" in page
        and "它的截图也会一起删除" in page,
        "确认后：截图删掉、截图位置清空、不再待确认，提示\"截图已删除\"": not confirm[1].exists()
        and row(confirm)["evidence_path"] is None and row(confirm)["needs_review"] == 0 and "截图已删除" in saved_page,
        "删除一局：截图一起删掉，提示里写了": not delete[1].exists() and row(delete) is None and "和它的截图" in deleted_page,
        "演示数据的样本截图（不在 captures/live 里）不删，也不提示会删": sample.exists()
        and row(sample_m)["evidence_path"] is not None and "截图会删除" not in sample_page,
        "别的局也在用的截图不删": shared[1].exists() and row(shared)["evidence_path"] is not None,
        "保存失败（名次填错）：截图不删，仍待确认": bad[1].exists() and row(bad)["needs_review"] == 1,
        "保留全部截图时：确认后不删，也不提示": kept[1].exists() and row(kept)["evidence_path"] is not None
        and "截图会删除" not in kept_page and "它的截图也会一起删除" not in kept_page,
        "总结页截图：确认时留着，截图位置不变，不提示确认后删除（2026-10-24 用户决定）": summary_ok[1].exists()
        and row(summary_ok)["evidence_path"] == data_relative(summary_ok[1]) and row(summary_ok)["needs_review"] == 0
        and "截图会删除" not in summary_page and "截图已删除" not in summary_saved,
        "总结页截图：删除这一局时一起删，删除确认框里也写了": not summary_del[1].exists() and row(summary_del) is None
        and "它的截图也会一起删除" in summary_page and "和它的截图" in summary_deleted,
        "总结页截图转成的 JPG：战绩页能打开（image/jpeg）": jpg_served(client, conn, summary_ok),
    }


def jpg_served(client, conn, match):
    """把这一局的总结页截图转成 JPG、改截图位置，再从战绩页的"识别截图"打开。"""
    screenshots.to_jpg(match[1])
    storage.update_match(conn, match[0], {"evidence_path": data_relative(match[1].with_suffix(".jpg"))})
    r = client.get(f"/evidence/{match[0]}")
    return r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:2] == b"\xff\xd8"


def settings_checks():
    """设置页的开关和"删除已识别完的截图"按钮。"""
    assert not screenshots.in_project()  # 只在 fake_game 设的临时数据文件夹里删，绝不碰项目里的测试集
    live = CAPTURES_DIR / "live"
    old, older, now = live / "20261015_200000", live / "20261016_200000", live / "20991231_000000"  # now：最新的文件夹
    for one in (old, older, now):
        one.mkdir(parents=True, exist_ok=True)

    def png(path):
        path.write_bytes(b"x" * 1000)
        return path

    review, done, lobby, rec = (png(old / "xp_review_leave.png"), png(old / "xp_done_leave.png"),
                                png(old / "lobby_1.png"), png(now / "xp_1.png"))
    summary, summary_review = image(old / "03_201500.png", 1), image(old / "04_202000.png", 2)  # 总结页截图
    recording_summary = image(now / "01_235900.png", 3)  # 正在记录的文件夹里的总结页截图：不转
    whole = [png(older / f"match_start_{i}.png") for i in range(3)]  # 这个文件夹整个都可以删
    folder = Path(tempfile.mkdtemp(prefix="evo-clean-", dir=storage.DB_PATH.parent.parent))
    conn = storage.connect(folder / "clean.db")
    review_id = storage.insert_match(conn, {"ended_at": "2026-10-15 20:10:00", "needs_review": 1,
                                            "review_note": "名次：没读到", "evidence_path": data_relative(review)})
    done_id = storage.insert_match(conn, {"ended_at": "2026-10-15 20:40:00", "evidence_path": data_relative(done)})
    summary_id = storage.insert_match(conn, {"ended_at": "2026-10-15 20:15:00", "evidence_path": data_relative(summary)})
    summary_review_id = storage.insert_match(conn, {"ended_at": "2026-10-15 20:20:00", "needs_review": 1,
                                                    "review_note": "名次：没读到",
                                                    "evidence_path": data_relative(summary_review)})
    panel_app.app.config.update(DB=folder / "clean.db", TESTING=True)
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]

    def page():
        return client.get("/settings").get_data(as_text=True)

    def post(url, **data):
        return client.post(url, data={"token": token, **data}, follow_redirects=True)

    def keep_switch(text):
        """页面上"保留全部截图"开关的样子：(开着吗, 能不能点)。"""
        tag = re.search(r'<button[^>]*aria-labelledby="keep-shots-name"[^>]*>', text)[0]
        return 'aria-checked="true"' in tag, "disabled" not in tag

    with keep_setting(False):
        off_page = page()
        on_page = post("/settings/keep-shots", keep="1").get_data(as_text=True)
        turned_on = app_settings.get(screenshots.KEEP_ALL_KEY)
        post("/settings/keep-shots", keep="0")
        turned_off = app_settings.get(screenshots.KEEP_ALL_KEY)

    recording = [SimpleNamespace(name="evo-collect")]  # 假装记录子程序在运行
    with keep_setting(True), mock.patch.object(screenshots.multiprocessing, "active_children", lambda: recording):
        with mock.patch.object(screenshots, "PROJECT_CAPTURES", CAPTURES_DIR):  # 假装截图存在项目的 captures/（用源码运行）
            locked_page = page()
            locked = (post("/settings/clean-shots").status_code, post("/settings/keep-shots", keep="0").status_code)
            locked_files = all(p.exists() for p in [done, lobby, *whole])
        finished = [p for p in live.glob("*/*.png") if p.parent != now]  # 别的检查留下的截图也算
        expected = [p for p in finished if p != review and not screenshots.is_summary(p)]
        converting = [p for p in finished if screenshots.is_summary(p)]
        size = panel_app.size_text(sum(p.stat().st_size for p in expected)
                                   + round(sum(p.stat().st_size for p in converting) * (1 - screenshots.JPG_RATIO)))
        before = page()
        cleaned = post("/settings/clean-shots").get_data(as_text=True)
        after = page()
    return {
        "设置页：开关默认关、能点": keep_switch(off_page) == (False, True),
        "设置页：打开开关存进 settings.json，提示从下一局开始": turned_on is True
        and keep_switch(on_page) == (True, True) and "从下一局开始" in on_page,
        "设置页：关上开关": turned_off is False,
        "设置页：写着大约可以腾出多少、删几张、几张总结页截图转成 JPG，正在记录时写着这一次不删":
        f"可以腾出约 <span class=\"num\">{size}</span>（删掉 <span class=\"num\">{len(expected)}</span> 张，"
        f"<span class=\"num\">{len(converting)}</span> 张总结页截图转成 JPG）" in " ".join(before.split())
        and "正在记录的这一次" in before and len(converting) >= 2,
        "删除已识别完的截图：以前存下的、没用的都删，提示删了几张、转了几张、腾出多少":
        not any(p.exists() for p in [done, lobby, *whole])
        and f"已删除 {len(expected)} 张截图，把 {len(converting)} 张总结页截图转成了 JPG，腾出 " in cleaned,
        "删除已识别完的截图：待确认的局要用的留着，截图位置还在": review.exists()
        and storage.get_match(conn, review_id)["evidence_path"] is not None,
        "删除已识别完的截图：不用确认的局，截图位置清空": storage.get_match(conn, done_id)["evidence_path"] is None,
        "删除已识别完的截图：总结页截图转成 JPG 留着，截图位置改成 JPG（待确认的局也是）": all(
            not p.exists() and p.with_suffix(".jpg").is_file() for p in converting)
        and storage.get_match(conn, summary_id)["evidence_path"] == data_relative(summary.with_suffix(".jpg"))
        and storage.get_match(conn, summary_review_id)["evidence_path"] == data_relative(summary_review.with_suffix(".jpg")),
        "删除已识别完的截图：正在记录的文件夹不动（总结页截图也不转）": rec.exists() and recording_summary.exists(),
        "删除已识别完的截图：删空的文件夹也删掉": not older.exists() and old.exists(),
        "删除已识别完的截图：保留全部截图开着时也能用（2026-10-17 用户决定）；删完写着没有可以删的":
        "没有可以删的截图" in after,
        "用源码运行：开关锁定为开、不能点，按钮不能用，硬提交也不删": keep_switch(locked_page) == (True, False)
        and "用源码运行时不删截图" in locked_page and locked == (400, 400) and locked_files,
    }


def main():
    print("截图留不留")
    checks = jpg_checks()
    checks.update(panel_checks())
    checks.update(settings_checks())
    checks.update(rule_checks())
    try:
        folder = shot(NIGHT + "/01_213331.png").parent
    except MissingShots as e:
        print(f"  ⏭ 记录程序那部分跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return report(checks)
    files, frames = night_frames(folder)

    old = CAPTURES_DIR / "live" / "20200101_000000" / "01_000000.png"  # 以前存下的截图
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"old")

    rows, saved, run_dir, log = run(frames, keep_all=False)
    first = next(n for n in saved if n.startswith("01_"))  # 第 1 局的总结页（文件名是"第几局_存下的时间"）
    at = next(i for i, line in enumerate(log) if "第 1 局（编号" in line)
    left = files_in(run_dir)
    review = [r for r in rows if r["needs_review"]]
    summaries = {jpg_name(n) for n in saved if screenshots.is_summary(n)}
    needed = {Path(r["evidence_path"]).name for r in review if r["evidence_path"]}
    checks.update({
        "大厅、开局画面一张都没存": not any(n.startswith(("lobby_", "match_start_")) for n in saved),
        "经验值页只存离开前的最后一帧": all(n.endswith("_leave.png") for n in saved if n.startswith("xp_")),
        f"留下的截图正好是每一局的总结页截图（转成 JPG，{len(summaries)} 张）和待确认的局要用的":
        left == summaries | needed and len(summaries) >= 5 and len(review) == 1,
        "待确认的局在战绩页还能打开截图": all(data_absolute(r["evidence_path"]).is_file() for r in review),
        "有总结页的局：截图位置改成 JPG、打得开；别的不用确认的局，截图位置已清空": all(
            (r["evidence_path"].endswith(".jpg") and data_absolute(r["evidence_path"]).is_file())
            if r["evidence_path"] and screenshots.is_summary(r["evidence_path"]) else
            (r["needs_review"] or r["evidence_path"] is None) for r in rows)
        and sum(1 for r in rows if r["evidence_path"] and r["evidence_path"].endswith(".jpg")) == len(summaries),
        "第 1 局名次先没读到、经验值页补上后取消待确认：总结页截图转成 JPG 留着": "名次：没读到" in log[at + 1]
        and rows[0]["needs_review"] == 0 and first not in left and jpg_name(first) in left
        and rows[0]["evidence_path"].endswith(jpg_name(first)),
        "日志里写了识别完删了几张、转了几张": any("总结页截图" in line and "转成 JPG" in line for line in log),
        "以前存下的截图不动": old.read_bytes() == b"old",
    })

    first_game = [f for f in frames if f is None or shot_time(f) < "213720"]  # 第 1 局（到第 2 局开局画面之前）
    rows, saved, run_dir, _ = run(first_game, keep_all=True)
    left = files_in(run_dir)
    checks.update({
        "保留全部：大厅、开局画面、经验值页进入和离开都存": all(any(n.startswith(p) for n in saved) for p in (
            "lobby_", "match_start_", "xp_")) and any(n.endswith("_enter.png") for n in saved if n.startswith("xp_")),
        "保留全部：存过的截图一张都没删": left == set(saved) and len(left) > 10,
        "保留全部：每一局的截图位置都还在": len(rows) == 1 and data_absolute(rows[0]["evidence_path"]).is_file(),
    })
    checks.update(switch_checks(frames))
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
