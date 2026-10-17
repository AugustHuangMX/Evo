"""截图留不留（见 screenshots.py）：识别完就删，只留"待确认"的局要用的；"保留全部截图"时和以前一样全部留着。

- 记录程序：用 2026-10-16 晚的真实截图整晚重放（同 test_replay.py）。截图不在仓库里（见 fake_game.py），找不到就跳过这一部分
- 战绩页（用假的截图文件和 Flask 测试客户端，不用真截图）：确认、删除一局时删掉它的截图并提示；
  不删演示数据的样本截图、别的局也在用的截图；保存失败不删；保留全部截图时不删、也不提示

运行：python tests/test_screenshots.py
"""

import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fake_game import MissingShots, report, run_collect, shot  # 要在项目代码之前导入（设置临时数据文件夹）

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


def files_in(folder):
    return {p.name for p in folder.glob("*.png")} if folder.is_dir() else set()


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
        with mock.patch.object(screenshots, "DATA_DIR", screenshots.APP_DIR):
            project = screenshots.keep_all()
    with keep_setting(True):
        on = screenshots.keep_all()
    return {
        "开关关着（默认）：识别完就删": off is False,
        "开关打开：保留全部": on is True,
        "数据文件夹就是项目文件夹（用源码运行）：不管开关，永远保留全部（项目里的截图是测试集）": project is True,
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
    }


def main():
    print("截图留不留")
    checks = panel_checks()
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
    needed = {Path(r["evidence_path"]).name for r in review if r["evidence_path"]}
    checks.update({
        "大厅、开局画面一张都没存": not any(n.startswith(("lobby_", "match_start_")) for n in saved),
        "经验值页只存离开前的最后一帧": all(n.endswith("_leave.png") for n in saved if n.startswith("xp_")),
        "留下的截图正好是待确认的局要用的（第 4 局的总结页）": left == needed and len(left) == 1 == len(review),
        "待确认的局在战绩页还能打开截图": all(data_absolute(r["evidence_path"]).is_file() for r in review),
        "不用确认的局，截图位置已清空": all(r["evidence_path"] is None for r in rows if not r["needs_review"]),
        "第 1 局名次先没读到、经验值页补上后取消待确认：截图也删了": "名次：没读到" in log[at + 1]
        and rows[0]["needs_review"] == 0 and first not in left,
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
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
