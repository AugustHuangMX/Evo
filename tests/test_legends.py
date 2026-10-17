"""传奇识别（legends.py）：认得出自己用的传奇，不是对局画面时不乱认。

- 参照图：config/legends.json 里的 28 个传奇都有参照图
- 认得出：另一次在靶场截的 4 个传奇（不同时间、不同背景）；10-16 录像里敌人用的侵蚀（1080p 视频、观战位置，
  很可能是别的皮肤）；做参照图的那 28 张截图各自认成自己（检查参照图的文件名和截图对得上）
- 不乱认：10-16 晚实战的全部截图（大厅、开局、总结页、经验值页）、录像截图、切出去的桌面、Esc 菜单，都要认不出
截图不在仓库里，见 fake_game.py。

运行：python tests/test_legends.py
"""

import time

from fake_game import MissingShots, SHOTS, load, report, shot  # 要在项目代码之前导入

from legends import LegendDetector

FIRST_TRY = "legends/20261017_154208/"  # 第一次试截（头像位置量错了，但整张截图是好的）
FULL_SET = "legends/20261017_154752/"  # 做参照图的那次：30 张里第 1 张是桌面、第 9 张是 Esc 菜单
FIRST_TRY_LEGENDS = {1: "bangalore", 2: "revenant", 3: "fuse", 4: "mad_maggie"}
FULL_SET_LEGENDS = {
    2: "pathfinder", 3: "bangalore", 4: "fuse", 5: "revenant", 6: "mad_maggie", 7: "ballistic", 8: "wraith",
    10: "octane", 11: "horizon", 12: "ash", 13: "alter", 14: "axle", 15: "bloodhound", 16: "crypto",
    17: "valkyrie", 18: "seer", 19: "vantage", 20: "sparrow", 21: "gibraltar", 22: "lifeline", 23: "mirage",
    24: "loba", 25: "newcastle", 26: "conduit", 27: "caustic", 28: "wattson", 29: "rampart", 30: "catalyst",
}
SPECTATE = "samples/vlcsnap-2026-10-16-18h27m31s652.png"  # 观战敌人 HostageHolder（侵蚀）
SPECTATE_BOX = [93, 887, 62, 76]  # 观战时左下角整块往上移约 65


def main():
    print("传奇识别：认得出自己的传奇，不是对局画面时不乱认")
    detector = LegendDetector()
    try:
        first = {n: load(shot(f"{FIRST_TRY}full_{n:02d}.jpg")) for n in FIRST_TRY_LEGENDS}
        full = {n: shot(f"{FULL_SET}full_{n:02d}.jpg") for n in range(1, 31)}
        spectate = load(shot(SPECTATE))
        night = sorted((SHOTS / "live" / "20261016_211452").glob("*.png"))
        if not night:
            raise MissingShots(SHOTS / "live" / "20261016_211452")
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None

    wrong_first = [n for n, key in FIRST_TRY_LEGENDS.items() if detector.identify(first[n]) != key]
    wrong_full = [n for n, key in FULL_SET_LEGENDS.items() if detector.identify(load(full[n])) != key]
    others = night + sorted((SHOTS / "samples").glob("*.png")) + [full[1], full[9], shot(f"{FIRST_TRY}full_05.jpg")]
    start = time.perf_counter()
    misread = [(p.name, detector.name(k)) for p in others if (k := detector.identify(load(p))) is not None]
    ms = (time.perf_counter() - start) * 1000 / len(others)  # 含读图片的时间
    return report({
        f"28 个传奇都有参照图（缺：{'、'.join(detector.name(k) for k in detector.missing) or '无'}）": not detector.missing,
        f"另一次截的 4 个传奇都认对（认错：{wrong_first or '无'}）": not wrong_first,
        "录像里敌人的侵蚀认对（1080p 视频、观战位置）": detector.identify(spectate, SPECTATE_BOX) == "caustic",
        f"做参照图的 28 张截图各自认成自己（认错的编号：{wrong_full or '无'}）": not wrong_full,
        f"{len(others)} 张不是对局的截图都认不出（乱认的：{misread or '无'}；平均每张 {ms:.0f} 毫秒，含读图）": not misread,
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
