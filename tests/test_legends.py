"""传奇识别（legends.py）：认得出自己用的传奇，不是对局画面时不乱认。

- 参照图：config/legends.json 里的 28 个传奇都有参照图
- 认得出：10-16 录像里敌人用的侵蚀（1080p 视频、观战位置，很可能是别的皮肤）；
  做参照图的那 28 张截图各自认成自己（检查参照图的文件名和截图对得上）
- 不乱认：10-16 晚实战的全部截图（大厅、开局、总结页、经验值页）、录像截图、切出去的桌面、Esc 菜单，都要认不出
- 接进记录程序（live_collect.LegendWatcher）：用假游戏跑几段，检查记下的这一局的传奇
  - 开局 → 对局中（侵蚀）→ 总结页：记为侵蚀；之前在靶场里认出的班加罗尔不算数
  - 开局 → 阵亡后观战（录像里敌人的侵蚀）→ 总结页：传奇留空，不认成被观战的人
  - 开局 → 对局中（侵蚀）→ 只有经验值页（第 2 名、冠军时没有总结页）：单独记的这一局也有传奇
截图不在仓库里，见 fake_game.py。

运行：python tests/test_legends.py
"""

import re
import time

from fake_game import BLACK, MissingShots, SHOTS, load, report, run_collect, shot  # 要在项目代码之前导入

from legends import LegendDetector

FULL_SET = "legends/20261017_154752/"  # 做参照图的那次：30 张里第 1 张是桌面、第 9 张是 Esc 菜单
FULL_SET_LEGENDS = {
    2: "pathfinder", 3: "bangalore", 4: "fuse", 5: "revenant", 6: "mad_maggie", 7: "ballistic", 8: "wraith",
    10: "octane", 11: "horizon", 12: "ash", 13: "alter", 14: "axle", 15: "bloodhound", 16: "crypto",
    17: "valkyrie", 18: "seer", 19: "vantage", 20: "sparrow", 21: "gibraltar", 22: "lifeline", 23: "mirage",
    24: "loba", 25: "newcastle", 26: "conduit", 27: "caustic", 28: "wattson", 29: "rampart", 30: "catalyst",
}
SPECTATE = "samples/vlcsnap-2026-10-16-18h27m31s652.png"  # 观战敌人 HostageHolder（侵蚀）
SPECTATE_BOX = [93, 887, 62, 76]  # 观战时左下角整块往上移约 65
NIGHT = "live/20261016_211452/"


def collect_checks():
    """用假游戏跑记录程序，检查每一局记下的传奇。每帧之间假时钟走 1 秒，每 3 秒看一次头像。"""
    images = {
        "靶场 班加罗尔": load(shot(f"{FULL_SET}full_03.jpg")),
        "对局中 侵蚀": load(shot(f"{FULL_SET}full_27.jpg")),
        "观战": load(shot(SPECTATE)),
        "开局": load(shot(NIGHT + "match_start_213716_leave.png")),
        "总结页": load(shot(NIGHT + "05_222749.png")),  # 第 8 名
        "经验值页": load(shot(NIGHT + "xp_214926_leave.png")),  # 第 7 名，RP -3（这次运行里没有总结页）
        "别的": BLACK,
    }
    runs = {
        "靶场后开局": ["靶场 班加罗尔"] * 6 + ["开局"] + ["对局中 侵蚀"] * 6 + ["总结页"] * 3,
        "观战": ["开局"] + ["观战"] * 10 + ["总结页"] * 3,
        "只有经验值页": ["开局"] + ["对局中 侵蚀"] * 6 + ["经验值页", "别的"],
    }
    out = {}
    for name, frames in runs.items():
        rows, _, log = run_collect(frames, images.get)
        stats = next((line for line in log if line.startswith("本次记录")), "")
        checks = re.search(r"认传奇 (\d+) 次", stats)
        out[name] = ([(r["placement"], r["legend"]) for r in rows], int(checks[1]) if checks else None)
    return out


def main():
    print("传奇识别：认得出自己的传奇，不是对局画面时不乱认")
    detector = LegendDetector()
    try:
        full = {n: shot(f"{FULL_SET}full_{n:02d}.jpg") for n in range(1, 31)}
        spectate = load(shot(SPECTATE))
        night = sorted((SHOTS / "live" / "20261016_211452").glob("*.png"))
        if not night:
            raise MissingShots(SHOTS / "live" / "20261016_211452")
        runs = collect_checks()
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None

    wrong_full = [n for n, key in FULL_SET_LEGENDS.items() if detector.identify(load(full[n])) != key]
    others = night + sorted((SHOTS / "samples").glob("*.png")) + [full[1], full[9]]
    start = time.perf_counter()
    misread = [(p.name, detector.name(k)) for p in others if (k := detector.identify(load(p))) is not None]
    ms = (time.perf_counter() - start) * 1000 / len(others)  # 含读图片的时间
    return report({
        f"28 个传奇都有参照图（缺：{'、'.join(detector.name(k) for k in detector.missing) or '无'}）": not detector.missing,
        "录像里敌人的侵蚀认对（1080p 视频、观战位置）": detector.identify(spectate, SPECTATE_BOX) == "caustic",
        f"做参照图的 28 张截图各自认成自己（认错的编号：{wrong_full or '无'}）": not wrong_full,
        f"{len(others)} 张不是对局的截图都认不出（乱认的：{misread or '无'}；平均每张 {ms:.0f} 毫秒，含读图）": not misread,
        f"记录程序：开局 → 侵蚀 → 总结页，记为侵蚀，靶场里的班加罗尔不算数（记下：{runs['靶场后开局'][0]}）":
            runs["靶场后开局"][0] == [(8, "侵蚀")],
        f"记录程序：认出后就不再看（16 帧里看了 {runs['靶场后开局'][1]} 次：靶场 2 次、对局中 2 次）":
            runs["靶场后开局"][1] == 4,
        f"记录程序：阵亡后观战不认成被观战的人，传奇留空（记下：{runs['观战'][0]}）":
            runs["观战"][0] == [(8, None)],
        f"记录程序：只有经验值页的局也有传奇（记下：{runs['只有经验值页'][0]}）":
            runs["只有经验值页"][0] == [(7, "侵蚀")],
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
