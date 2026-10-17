"""轻量（原则 3）：打游戏时不跑识别引擎，识别模型用到时才加载、闲置 2 分钟后释放。

画面顺序：5 帧游戏画面 → 一局的总结页 → 200 帧游戏画面（假时钟下约 200 秒）。
"游戏画面"用的是录像里的观战截图（不是任何一种要认的画面）。截图不在仓库里，见 fake_game.py。

运行：python tests/test_light.py
"""

from unittest import mock

from fake_game import FakeCamera, MissingShots, load, report, run_collect, shot  # 要在项目代码之前导入

import ocr

NIGHT = "live/20261016_211452/"


def main():
    print("轻量：打游戏时不跑识别，模型按需加载")
    try:
        images = {"游戏": load(shot("samples/vlcsnap-2026-10-16-18h27m31s652.png")),
                  "总结页": load(shot(NIGHT + "05_222749.png"))}
    except MissingShots as e:
        print(f"  ⏭ 跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return None

    ran_on = []  # 每次识别时屏幕上是哪种画面
    real_create = ocr.create_engine

    def counting_engine(params=None):
        engine = real_create(params)

        def run(img, **kwargs):
            ran_on.append(FakeCamera.showing)
            return engine(img, **kwargs)

        return run

    frames = ["游戏"] * 5 + ["总结页"] * 3 + ["游戏"] * 200
    with mock.patch.object(ocr, "create_engine", counting_engine):
        rows, _, log = run_collect(frames, images.get)
    loads = [line for line in log if line.startswith("加载识别模型")]
    releases = [line for line in log if line.startswith("识别模型闲置")]
    stats = [line for line in log if line.startswith("本次记录")]
    return report({
        f"游戏画面上一次识别都不跑（208 帧里有 205 帧是游戏画面，识别全部发生在总结页上：{len(ran_on)} 次）":
            ran_on and all(frame == "总结页" for frame in ran_on),
        "总结页照常识别并记下（第 8 名，688 伤害）": len(rows) == 1 and (rows[0]["placement"], rows[0]["damage"]) == (8, 688),
        "识别模型只加载了一次，而且是到了总结页才加载": len(loads) == 1 and ran_on[:1] == ["总结页"],
        "离开总结页、闲置 2 分钟后释放了模型，之后没有再加载": len(releases) == 1 and log.index(releases[0]) > log.index(loads[0]),
        "结束时在日志里报告了这次的截屏、识别次数和内存": len(stats) == 1,
    })


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
