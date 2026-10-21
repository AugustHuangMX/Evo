"""提示音（sounds.py、live_collect.SoundCues）：对局结束、总结页读完了、进入经验值页，每局每一声最多一次。

- 声音文件：三个都在，格式对，不超过 2 秒、不爆音，和 tools/make_sounds.py 合成出来的一模一样
- 开关：默认开；关了不响；试听不管开关
- 什么时候响（直接喂画面，不用截图）：淘汰画面 / 冠军横幅 / "返回大厅"按钮响"对局结束"，已经到过总结页、经验值页就不响；
  总结页读完了才响；进经验值页响；来回切页不重复；下一局重新算
- 用截图跑真正的记录程序：一局从开局到大厅，响了哪几声、顺序对不对；总结页停留太短不响"读完了"
- 设置页：开关、三个"试听"（脚本提交，不刷新页面）
测试时不真的出声（fake_game 把声音换成记一笔）。

运行：python tests/test_sounds.py
"""

import sys
import wave
from datetime import datetime, timedelta
from unittest import mock

import fake_game
from fake_game import ROOT, SOUNDS_PLAYED, MissingShots, load, report, run_collect, shot  # 要在项目代码之前导入

import numpy as np  # noqa: E402

import app_settings  # noqa: E402
import panel_app  # noqa: E402
import sounds  # noqa: E402
import storage  # noqa: E402
from live_collect import SoundCues  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import make_sounds  # noqa: E402

S = "samples/"
PLAYING, SPECTATING, ELIMINATED = S + "对局中.png", S + "玩家成盒（观战队友视角）.png", S + "死亡瞬间.png"
CHAMP_MOMENT, CHAMP_SUMMARY = S + "捍卫者瞬间界面.png", S + "捍卫者结算界面.png"
SUMMARY, SPECT_ENEMY = S + "vlcsnap-2026-10-16-18h26m12s173.png", S + "vlcsnap-2026-10-16-18h27m31s652.png"
START = "live/20261016_211452/match_start_213625_enter.png"
XP = "live/20261016_211452/xp_213328_leave.png"
LOBBY = "live/20261016_211452/lobby_211506_enter.png"


def file_checks():
    out = {}
    ok = []
    for name in sounds.SOUNDS:
        path = sounds.path(name)
        if not path.is_file():
            ok.append(False)
            continue
        with wave.open(str(path)) as f:
            fmt = (f.getnchannels(), f.getsampwidth(), f.getframerate())
            seconds = f.getnframes() / f.getframerate()
            data = np.frombuffer(f.readframes(f.getnframes()), "<i2")
        made = (make_sounds.render(make_sounds.SOUNDS[name]) * 32767).astype("<i2")
        ok.append(fmt == (1, 2, 44100) and seconds <= 2.0 and np.abs(data).max() / 32767 <= 0.6
                  and np.array_equal(data, made))
    out["三个声音文件都在：单声道 16 位 44.1 kHz、不超过 2 秒、不爆音，和 tools/make_sounds.py 合成的一样"] = all(ok)
    out["按一局里的先后排：对局结束、总结页读完了、经验值页"] = list(sounds.SOUNDS) == ["match_end", "summary", "xp"]
    return out


def setting_checks():
    out = {}
    app_settings.set_value(sounds.SETTING_KEY, None)
    SOUNDS_PLAYED.clear()
    out["默认开：响"] = sounds.enabled() and sounds.play("xp") and SOUNDS_PLAYED == ["xp"]
    app_settings.set_value(sounds.SETTING_KEY, False)
    SOUNDS_PLAYED.clear()
    out["关了：不响"] = not sounds.play("xp") and SOUNDS_PLAYED == []
    out["试听不管开关"] = sounds.preview("summary") and SOUNDS_PLAYED == ["summary"]
    try:
        sounds.preview("nothing")
        out["不认识的声音：报错"] = False
    except ValueError:
        out["不认识的声音：报错"] = True
    app_settings.set_value(sounds.SETTING_KEY, None)
    real = sounds._output
    sounds._output = None
    with mock.patch.object(sounds, "path", lambda name: ROOT / "nothing.wav"):
        out["找不到声音文件：不出错，返回没响"] = sounds.preview("xp") is False
    sounds._output = real
    return out


def cue(steps):
    """直接喂画面给 SoundCues：steps 里是画面名（None = 都不是），"read" = 总结页读完了。返回响了哪几声。"""
    played, log = [], []
    cues = SoundCues(log.append, play=lambda name: played.append(name) or True)
    now = datetime(2026, 10, 21, 20, 0)
    for i, step in enumerate(steps):
        when = now + timedelta(seconds=i)
        if step == "read":
            cues.summary_read(when)
        else:
            cues.update(step, when)
    return played, log


def rule_checks():
    out = {}
    played, log = cue(["match_start", None, None, "eliminated", None, "summary", "summary", "read", "xp", "lobby"])
    out[f"淘汰 → 总结页读完了 → 经验值页：三声按顺序（{played}）"] = played == ["match_end", "summary", "xp"]
    out["日志里写了每一声"] = [line.split("] ")[1] for line in log] == ["提示音：对局结束", "提示音：总结页读完了", "提示音：经验值页"]
    played, _ = cue(["match_start", None, "squad_out", None, "squad_out", None, "squad_out"])
    out[f"观战对手时\"返回大厅\"按钮一会儿有一会儿没有：只响一次（{played}）"] = played == ["match_end"]
    played, _ = cue(["match_start", None, "champion", None, "summary", "read", "xp"])
    out[f"冠军：横幅响\"对局结束\"，再响总结页、经验值页（{played}）"] = played == ["match_end", "summary", "xp"]
    played, _ = cue(["match_start", None, "summary", "read", "xp"])
    out[f"没看到淘汰画面就进了总结页：不响\"对局结束\"（{played}）"] = played == ["summary", "xp"]
    played, _ = cue(["match_start", None, "summary", "read", None, "squad_out", "eliminated"])
    out[f"从总结页切回\"实况\"又看到\"返回大厅\"、淘汰画面：不再响\"对局结束\"（{played}）"] = played == ["summary"]
    played, _ = cue(["match_start", None, "summary", "read", "xp", "summary", "read", "xp", "summary", "xp"])
    out[f"总结页和经验值页来回切：每一声只响一次（{played}）"] = played == ["summary", "xp"]
    played, _ = cue(["match_start", None, None, None, "match_start", None, "lobby"])
    out["自己先成盒、队友还活着，提前回大厅或直接进下一局：一声都不响"] = played == []
    played, _ = cue(["match_start", None, "eliminated", "summary", "read", "match_start", None, "eliminated", "summary",
                     "read", "lobby", "match_start", None, "squad_out"])
    out[f"下一局开局、回到大厅后重新算（{played}）"] = played == ["match_end", "summary", "match_end", "summary", "match_end"]
    log = []
    cues = SoundCues(log.append, play=lambda name: False)  # 设置里关着
    cues.update("eliminated", datetime(2026, 10, 21))
    cues.summary_read(datetime(2026, 10, 21))
    out["关着时：不响，日志不写"] = log == []
    return out


def collect_checks():
    """用截图跑真正的记录程序（假摄像头、假时钟）。"""
    images = {p: load(shot(p)) for p in (PLAYING, SPECTATING, ELIMINATED, CHAMP_MOMENT, CHAMP_SUMMARY, SUMMARY,
                                         SPECT_ENEMY, START, XP, LOBBY)}
    out = {}

    def run(frames):
        SOUNDS_PLAYED.clear()
        _, _, log = run_collect(frames, images.get)
        return list(SOUNDS_PLAYED), [line.split("] ")[1] for line in log if "提示音：" in line]

    played, said = run([START, PLAYING, PLAYING, ELIMINATED, SPECTATING, SUMMARY, SUMMARY, SUMMARY, XP, XP, LOBBY])
    out[f"一局：淘汰画面 → 总结页读全 → 经验值页 → 大厅，三声按顺序（{played}）"] = (
        played == ["match_end", "summary", "xp"] and said == ["提示音：对局结束", "提示音：总结页读完了", "提示音：经验值页"])
    played, _ = run([START, PLAYING, PLAYING, CHAMP_MOMENT, CHAMP_MOMENT, CHAMP_SUMMARY, CHAMP_SUMMARY, CHAMP_SUMMARY])
    out[f"冠军：横幅 → 总结页读全（{played}）"] = played == ["match_end", "summary"]
    played, _ = run([START, PLAYING, PLAYING, ELIMINATED, SUMMARY, SPECTATING, SPECTATING])
    out[f"总结页只停了一下、没读全：不响\"读完了\"（{played}）"] = played == ["match_end"]
    played, _ = run([START, PLAYING, PLAYING, PLAYING, SPECT_ENEMY, SPECT_ENEMY, START])
    out[f"全队被淘汰后观战对手（右下角\"返回大厅\"）：响\"对局结束\"（{played}）"] = played == ["match_end"]
    played, _ = run([START, PLAYING, PLAYING, SPECTATING, SPECTATING, START])
    out[f"成盒后观战队友、直接进了下一局：不响（{played}）"] = played == []
    return out


def panel_checks():
    out = {}
    panel_app.app.config["DB"] = str(fake_game._TEMP / "sounds-panel.db")
    storage.connect(panel_app.app.config["DB"]).close()
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]

    def page():
        return " ".join(client.get("/settings").get_data(as_text=True).split())

    app_settings.set_value(sounds.SETTING_KEY, None)
    s = page()
    out["设置页有\"提示音\"一栏，默认开着"] = (
        '<h2 id="h-sounds">提示音</h2>' in s and 'aria-checked="true" aria-labelledby="sounds-name"' in s)
    order = [s.find(f'name="name" value="{k}"') for k in ("match_end", "summary", "xp")]
    out["三个\"试听\"按一局里的先后排"] = -1 not in order and order == sorted(order) and s.count(">试听</button>") == 3
    got = " ".join(client.post("/settings/sounds", data={"token": token, "on": "0"},
                               follow_redirects=True).get_data(as_text=True).split())
    out["关掉：记进设置，开关显示关"] = (
        app_settings.get(sounds.SETTING_KEY) is False and 'aria-checked="false" aria-labelledby="sounds-name"' in got
        and "已关闭提示音" in got)
    SOUNDS_PLAYED.clear()
    r = client.post("/settings/sound-preview", data={"token": token, "name": "match_end"}, headers={"X-Evo-Fetch": "1"})
    out["关着时也能试听：脚本提交，回答放出来了，不刷新页面"] = r.get_json() == {"ok": True} and SOUNDS_PLAYED == ["match_end"]
    r = client.post("/settings/sound-preview", data={"token": token, "name": "xp"})
    out["没有脚本时：试听后回到设置页"] = r.status_code == 302 and SOUNDS_PLAYED == ["match_end", "xp"]
    out["不认识的声音：拒绝"] = client.post("/settings/sound-preview", data={"token": token, "name": "x"}).status_code == 400
    out["没带口令：拒绝"] = client.post("/settings/sound-preview", data={"name": "xp"}).status_code == 403
    client.post("/settings/sounds", data={"token": token, "on": "1"})
    out["再打开：记进设置"] = app_settings.get(sounds.SETTING_KEY) is True
    app_settings.set_value(sounds.SETTING_KEY, None)
    return out


def main():
    print("提示音（声音文件、开关、什么时候响、记录程序、设置页）")
    checks = {**file_checks(), **setting_checks(), **rule_checks(), **panel_checks()}
    try:
        checks.update(collect_checks())
    except MissingShots as e:
        print(f"  ⏭ 截图部分跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
