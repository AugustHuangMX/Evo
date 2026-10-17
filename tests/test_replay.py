"""整晚重放：把 2026-10-16 晚实战时存下的截图按时间顺序喂给记录程序，检查最后记下的每一局对不对。

测的是整条流程：认画面 → 读总结页 → 读经验值页 → 读开局画面的模式和地图 → 把同一局的数据归到一起。
正确答案在 replay_20261016.csv（来自截图人工核对；RP 和段位进度前后能串起来：942 → 939 → 967 → … → 964）。
截图在 captures/live/20261016_211452/（不在仓库里，见 fake_game.py）。

运行：python tests/test_replay.py
"""

import csv
import re
from functools import lru_cache
from pathlib import Path

from fake_game import BLACK, MissingShots, load, run_collect, shot  # 要在项目代码之前导入（设置临时数据文件夹）

NIGHT = "live/20261016_211452"
ANSWERS = Path(__file__).with_name("replay_20261016.csv")
LABELS = {"placement": "名次", "kills": "击杀", "assists": "助攻", "knockdowns": "击倒", "damage": "伤害",
          "rp_delta": "RP", "rank_tier": "段位", "rank_division": "段", "rank_progress": "段内进度",
          "needs_review": "待确认", "mode": "模式", "map": "地图"}


def shot_time(path):
    """截图文件名里的时间：01_213331.png、xp_213412_enter.png → "213331"、"213412"。"""
    return re.search(r"(?:^|_)(\d{6})(?:_|$)", path.stem)[1]


def night_frames(folder):
    """按时间顺序排好的画面。总结页喂两帧（数字连续两次一致才保存）；离开一个画面后插一帧"别的画面"。"""
    files = sorted(folder.glob("*.png"), key=lambda p: (shot_time(p), "leave" in p.stem))  # 同一秒里先 enter 后 leave
    frames = []
    for path in files:
        is_summary = path.stem[0].isdigit()  # 总结页的文件名是 "编号_时间"
        frames += [path, path] if is_summary else [path]
        if is_summary or path.stem.endswith("_leave"):
            frames.append(None)
    return files, frames


@lru_cache(maxsize=2)  # 同一张图连着喂两次时不用重读
def picture(path):
    return BLACK if path is None else load(path)


def text(value):
    return "" if value is None else str(value)


def main():
    print("整晚重放（2026-10-16 晚，10 局）")
    try:
        folder = shot(NIGHT + "/01_213331.png").parent
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None
    files, frames = night_frames(folder)
    rows, _, log = run_collect(frames, picture)
    with ANSWERS.open(encoding="utf-8-sig") as f:
        answers = list(csv.DictReader(f))

    print(f"  喂了 {len(files)} 张截图（{len(frames)} 帧），记下 {len(rows)} 局")
    ok = len(rows) == len(answers)
    if not ok:
        print(f"  ❌ 局数不对：应为 {len(answers)} 局")
    for row, answer in zip(rows, answers):
        wrong = [f"{LABELS[k]} 应为 {answer[k] or '空'}，实际 {text(row[k]) or '空'}"
                 for k in LABELS if text(row[k]) != answer[k]]
        ok &= not wrong
        note = f"（{answer['说明']}）" if answer["说明"] and not wrong else ""
        print(f"  {'✅' if not wrong else '❌'} 第 {answer['局']:>2} 局 {'；'.join(wrong) or '全部正确'}{note}")
    if not ok:
        print("\n  记录程序的日志：")
        print("\n".join("    " + line for line in log))
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
