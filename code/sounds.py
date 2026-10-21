"""对局结束的提示音（2026-10-21 用户决定，默认开）：一局里最多响三声，每一声每局最多一次（什么时候响见 live_collect.SoundCues）。

- 对局结束（match_end）：小队打完了（淘汰画面、冠军横幅、右下角"返回大厅"按钮），提醒去总结页
- 总结页读完了（summary）：名次、击杀、伤害都读到了才响，听到就可以离开
- 经验值页（xp）：一进经验值页就响（数字有动画，Evo 离开时才读最后一帧，没有"读完了"的那一刻）

声音文件在 assets/sounds/（tools/make_sounds.py 合成，MIT）。用 Python 自带的 winsound 播放，不等它放完，不加依赖。
设置页能关、能试听；音量在 Windows 的"音量合成器"里调 Evo。

试听：python code/sounds.py
"""

import time
import winsound

import app_settings
from paths import ASSETS_DIR

SOUNDS = {"match_end": "对局结束", "summary": "总结页读完了", "xp": "经验值页"}  # 按一局里先后的顺序
SETTING_KEY = "sounds_on"
_output = None  # 测试时换成记下"响了哪一声"的函数，不真的出声（见 tests/fake_game.py）


def path(name):
    return ASSETS_DIR / "sounds" / f"{name}.wav"


def enabled():
    """设置里开着没有（默认开）。"""
    return app_settings.get(SETTING_KEY, True) is not False


def play(name):
    """记录程序用：设置里开着才响。响了返回 True。"""
    return enabled() and preview(name)


def preview(name):
    """响一声，不管开关（设置页的"试听"）。找不到文件、放不出来（如电脑没有声卡）返回 False。"""
    if name not in SOUNDS:
        raise ValueError(f"没有这个提示音：{name}")
    if _output is not None:
        _output(name)
        return True
    if not path(name).is_file():
        return False
    try:
        winsound.PlaySound(str(path(name)), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except RuntimeError:
        return False
    return True


if __name__ == "__main__":
    for key, label in SOUNDS.items():
        print(f"{label}（{path(key).name}）")
        preview(key)
        time.sleep(1.6)
