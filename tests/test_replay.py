"""整晚重放：把 2026-10-16 晚实战时存下的截图按时间顺序喂给记录程序，检查最后记下的每一局对不对。

测的是整条流程：认画面 → 读总结页（含自己的玩家名、两名队友）→ 读经验值页 → 读开局画面的模式和地图 → 把同一局的数据归到一起。
正确答案在 replay_20261016.csv（来自截图人工核对；RP 和段位进度前后能串起来：942 → 939 → 967 → … → 964）；
队友的正确答案在 replay_20261016_teammates.csv（逐张看过截图；第 7 局只有经验值页，没有队友）。
这个文件有队友的名字和 ID，和截图一样不提交，只在开发者电脑上；找不到时队友这项跳过。
截图在 captures/live/20261016_211452/（不在仓库里，见 fake_game.py）。

运行：python tests/test_replay.py
"""

import csv
import re
from functools import lru_cache
from pathlib import Path

import fake_game  # 要在项目代码之前导入（设置临时数据文件夹）
from fake_game import BLACK, MissingShots, load, run_collect, shot

import storage

NIGHT = "live/20261016_211452"
ANSWERS = Path(__file__).with_name("replay_20261016.csv")
MATE_ANSWERS = Path(__file__).with_name("replay_20261016_teammates.csv")
LABELS = {"placement": "名次", "kills": "击杀", "assists": "助攻", "knockdowns": "击倒", "damage": "伤害",
          "rp_delta": "RP", "rank_tier": "段位", "rank_division": "段", "rank_progress": "段内进度",
          "needs_review": "待确认", "mode": "模式", "map": "地图", "player_name": "玩家名"}


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


def mate_text(mate, key):
    """队友的一项写成和答案一样的文字：名字不管空格（"[ABC] Name" 读成 "[ABC]Name" 也算对），生存时间写成 14:10。"""
    if key == "name":
        return storage.name_key(mate[key] or "")
    if key == "survival_time" and isinstance(mate[key], int):  # 数据库里存的是秒，答案里写的是 14:10
        return storage.mmss(mate[key])
    return text(mate[key])


def check_teammates(rows):
    """每一局记下的队友（左、右）和答案逐项比较；没有总结页的局不能有队友。"""
    if not MATE_ANSWERS.is_file():
        print(f"  ⏭ 队友：跳过，找不到答案 {MATE_ANSWERS.name}（有队友的名字和 ID，不在仓库里）")
        return True
    with MATE_ANSWERS.open(encoding="utf-8-sig") as f:
        answers = {}
        for a in csv.DictReader(f):
            answers.setdefault(int(a["局"]), {})[a["slot"]] = a
    conn = storage.connect(fake_game.last_db)
    wrong = []
    for i, row in enumerate(rows, 1):
        got = {m["slot"]: m for m in storage.teammates_of(conn, row["match_id"])}
        want = answers.get(i, {})
        if set(got) != set(want):
            wrong.append(f"第 {i} 局应有队友 {sorted(want) or '无'}，实际 {sorted(got) or '无'}")
            continue
        for slot, a in want.items():
            # 答案里没有的列（如传奇：当晚的截图里没有对局中的画面）不比
            bad = [f"{k} 应为 {mate_text(a, k) or '空'}，实际 {mate_text(got[slot], k) or '空'}"
                   for k in storage.MATE_COLUMNS if k in a and mate_text(got[slot], k) != mate_text(a, k)]
            if bad:
                wrong.append(f"第 {i} 局{'左' if slot == 'left' else '右'}边队友：{'；'.join(bad)}")
    conn.close()
    total = sum(len(v) for v in answers.values())
    if wrong:
        print("  ❌ 队友：\n" + "\n".join("     " + w for w in wrong))
    else:
        print(f"  ✅ 队友：{total} 人次逐项正确，只有经验值页的局没有队友")
    return not wrong


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
    ok &= check_teammates(rows)
    if not ok:
        print("\n  记录程序的日志：")
        print("\n".join("    " + line for line in log))
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
