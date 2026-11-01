"""总结页停留时的几种情况：名次晚出现要等它；提前离开、等太久、停止记录时，用读得最全的一次存下并标记待确认；
在总结页和别的画面之间来回切换，同一局不重复存。
同一次停留里没变的项不再识别、沿用上次的（2026-11-01，summary_page.read_field 的 memo）：同一张读第二遍一次都不识别；
名次滑进来时只重读名次、小队总击杀；伤害换了一个数时只重读伤害；配置里去掉 unchanged 就每遍都全读。

用的是 2026-10-16 晚的真实截图，外加一张拼出来的"名次还没滑进来"的画面（见 late_placement）。
截图不在仓库里，见 fake_game.py。

运行：python tests/test_summary_page.py
"""

from fake_game import MissingShots, load, report, run_collect, shot  # 要在项目代码之前导入（设置临时数据文件夹）

import re

import ocr
from summary_page import read_summary

NIGHT = "live/20261016_211452/"


def late_placement(full, blank):
    """拼一张"名次还没滑进来"的总结页：把右上角（名次、小队总击杀）换成另一张截图里还是空白的同一块。
    坐标是 1920×1080 下的 [1400, 140, 480, 100]，换算到 2560×1440。"""
    frame = full.copy()
    frame[187:320, 1867:2507] = blank[187:320, 1867:2507]
    return frame


def other_damage(full, other):
    """拼一张"伤害换了一个数"的总结页：中间卡片上伤害那一格换成另一张截图的（1920×1080 下 [748, 472, 180, 46]，
    换算到 2560×1440 再放宽一点）。"""
    frame = full.copy()
    frame[620:700, 990:1245] = other[620:700, 990:1245]
    return frame


def memo_checks(images):
    """直接读几遍总结页（同一个 memo），数每一遍识别了几次。"""
    real = ocr.create_engine(ocr.SMALL_CROP_PARAMS)
    calls = [0]

    def engine(img, **kwargs):
        calls[0] += 1
        return real(img, **kwargs)

    cfg = ocr.load_config("summary.json")

    def reads(names, config=cfg):
        """按顺序读 names 里的几张（同一个 memo），返回每一遍 (识别了几次, 读到的值)。"""
        memo, out = {}, []
        for name in names:
            before = calls[0]
            results, _ = read_summary(engine, images[name], config, memo=memo)
            out.append((calls[0] - before, {k: r["value"] for k, r in results.items()}))
        return out

    f5 = reads(["F5", "F5"])
    late = reads(["B5", "F5"])
    damage = reads(["F5", "F5改伤害"])
    plain = reads(["F5", "F5"], {k: v for k, v in cfg.items() if k != "unchanged"})
    return {
        f"同一张读第二遍：一次都不识别，读到的一样（{f5[0][0]} → {f5[1][0]} 次）":
            f5[0][0] == 7 and f5[1][0] == 0 and f5[0][1] == f5[1][1],
        f"名次滑进来：只重读名次、小队总击杀，读到第 8 名（{late[1][0]} 次）":
            late[0][1]["placement"] is None and late[1][0] == 2 and late[1][1] == f5[0][1],
        f"伤害换了一个数：只重读伤害（{damage[1][0]} 次，{damage[0][1]['damage']} → {damage[1][1]['damage']}）":
            damage[1][0] == 1 and (damage[0][1]["damage"], damage[1][1]["damage"]) == (688, 599)
            and {k: v for k, v in damage[1][1].items() if k != "damage"} == {k: v for k, v in f5[0][1].items() if k != "damage"},
        f"配置里去掉 unchanged：每遍都全读（{[n for n, _ in plain]}）": [n for n, _ in plain] == [7, 7],
    }


def main():
    print("总结页停留")
    try:
        f5 = load(shot(NIGHT + "05_222749.png"))  # 第 8 名，3 杀，688 伤害
        images = {
            "观战": load(shot("samples/vlcsnap-2026-10-16-18h27m31s652.png")),  # 不是总结页的画面
            "F5": f5,
            "B5": late_placement(f5, load(shot(NIGHT + "01_213331.png"))),  # 01 那张保存时名次还没滑进来
            "M9": load(shot(NIGHT + "09_233033.png")),  # 名次没滑进来就离开了：0 伤害
            "M8": load(shot(NIGHT + "08_231728.png")),  # 95 伤害
            "M7": load(shot(NIGHT + "07_231013.png")),  # 599 伤害
        }
        images["F5改伤害"] = other_damage(f5, images["M7"])  # 第 8 名、3 杀，伤害换成 599
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None

    frames = ["观战", "B5", "B5", "B5", "F5", "F5", "F5", "观战", "F5", "F5", "观战", "M9", "M9", "观战", "M8"]
    rows, sleeps, log = run_collect(frames, images.get)
    stats = next((line for line in log if line.startswith("本次记录")), "")
    used = re.search(r"总结页读了 (\d+) 项（没变沿用 (\d+) 项）", stats)
    first, second, third = (rows + [None] * 3)[:3]
    checks = {
        "名次晚出现：等到第 8 名才存，不标待确认": first is not None and (
            first["placement"], first["squad_kills"], first["kills"], first["damage"], first["needs_review"]) == (8, 3, 3, 688, 0),
        "等名次时保持快速检查（每 0.3 秒一次）": all(s <= 0.3 for frame, s in sleeps if frame == "B5"),
        "切回同一局的总结页不重复存：一共 3 局": len(rows) == 3,
        "名次没滑进来就离开、其他数字已连续两次一致：存下，只标\"名次没读到\"（经验值页能补）": second is not None and (
            second["placement"], second["kills"], second["damage"], second["needs_review"], second["review_note"])
        == (None, 0, 0, 1, "名次：没读到（OCR 原文：无）"),
        "停止记录时总结页只读了一次：也存下，标待确认并注明停留太短": third is not None and (
            third["placement"] is None and third["damage"] == 95 and third["needs_review"] == 1
            and "停留太短" in (third["review_note"] or "")),
        f"日志写了总结页读了几项、沿用了几项（{used and used[0]}）": used is not None and int(used[2]) > 0,
    }
    checks.update(memo_checks(images))

    frames = ["观战"] + ["M7"] * 8
    rows, sleeps, _ = run_collect(frames, images.get, summary_timeout=1)
    m7_sleeps = [s for frame, s in sleeps if frame == "M7"]
    checks.update({
        "名次一直不出现：等满 1 秒先存下，标待确认，只存一次": len(rows) == 1 and rows[0]["damage"] == 599
        and rows[0]["needs_review"] == 1 and (rows[0]["review_note"] or "").startswith("名次：没读到"),
        "存下以后不再快速检查（回到每秒一次）": m7_sleeps[-1] == 1.0 and min(m7_sleeps) <= 0.3,
    })
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
