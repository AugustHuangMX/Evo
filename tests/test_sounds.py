"""提示音（sounds.py、live_collect.SoundCues）：对局结束、总结页读完了、进入经验值页，每局每一声最多一次。

- 声音文件：Evo 默认的三个都在，格式对，不超过 2 秒、不爆音，和 tools/make_sounds.py 合成出来的一模一样；
  每套声音包都有 pack.json（名字、许可），WAV 是 16 位、不超过 10 秒
- 开关：默认开；关了不响；试听不管开关
- 声音包（整套切换）：默认 Evo 的；选的那套缺哪一声用 Evo 默认的；选的那套没有了用回 Evo 默认的
- 音量：默认 100% 放原文件；调小了放调小的副本（振幅按音量的平方），同一音量不重做，换音量删掉旧的
- 自己的文件：核对格式、长度后存进数据文件夹；读不出、格式不对、太长、空的都说原因；恢复默认
- 什么时候响（直接喂画面，不用截图）：淘汰画面 / 冠军横幅 / "返回大厅"按钮响"对局结束"，已经到过总结页、经验值页就不响；
  总结页读完了才响；进经验值页响；来回切页不重复；下一局重新算
- 用截图跑真正的记录程序：一局从开局到大厅，响了哪几声、顺序对不对；总结页停留太短不响"读完了"
- 设置页：开关、声音包下拉框、音量滑块、三个"试听"（脚本提交，不刷新页面）；选"自己的文件"时的选文件、恢复默认
测试时不真的出声（fake_game 把声音换成记一笔）。

运行：python tests/test_sounds.py
"""

import io
import json
import shutil
import sys
import wave
from datetime import datetime, timedelta
from unittest import mock

import fake_game
from fake_game import ROOT, SOUNDS_PLAYED, MissingShots, load, report, run_collect, shot  # 要在项目代码之前导入

import numpy as np  # noqa: E402

import app_settings  # noqa: E402
import panel_app  # noqa: E402
import paths  # noqa: E402
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
    out["Evo 默认的三个声音文件都在：单声道 16 位 44.1 kHz、不超过 2 秒、不爆音，和 tools/make_sounds.py 合成的一样"] = all(ok)
    out["按一局里的先后排：对局结束、总结页读完了、经验值页"] = list(sounds.SOUNDS) == ["match_end", "summary", "xp"]
    bad = []
    for folder in sorted(p for p in sounds.PACKS_DIR.iterdir() if p.is_dir()):
        try:
            info = json.loads((folder / "pack.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bad.append(f"{folder.name}：没有 pack.json")
            continue
        if not info.get("name") or not info.get("license"):
            bad.append(f"{folder.name}：pack.json 缺名字或许可")
        files = [folder / f"{name}.wav" for name in sounds.SOUNDS if (folder / f"{name}.wav").is_file()]
        if not files:
            bad.append(f"{folder.name}：一个声音都没有")
        for f in files:
            with wave.open(str(f)) as w:
                if w.getsampwidth() != 2 or w.getnchannels() not in (1, 2) or w.getnframes() / w.getframerate() > 10:
                    bad.append(f"{folder.name}/{f.name}：要 16 位、单声道或立体声、不超过 10 秒")
        extra = {p.name for p in folder.iterdir()} - {"pack.json"} - {f"{name}.wav" for name in sounds.SOUNDS}
        if extra:
            bad.append(f"{folder.name}：不认识的文件 {sorted(extra)}")
    out[f"每套声音包：有 pack.json（名字、许可），WAV 是 16 位、不超过 10 秒（{bad or '都对'}）"] = not bad
    keys = [one["key"] for one in sounds.packs()]
    out[f"设置页的顺序：Evo 默认第一，自己的文件最后（{keys}）"] = keys[0] == "evo" and keys[-1] == "custom"
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
    with mock.patch.object(sounds, "path", lambda name, pack="evo": ROOT / "nothing.wav"):
        out["找不到声音文件：不出错，返回没响"] = sounds.preview("xp") is False
    sounds._output = real
    return out


def wav_bytes(seconds=1.0, rate=44100, channels=1, width=2, value=8000):
    """一段 WAV（每个采样都是同一个数），测试用。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(width)
        f.setframerate(rate)
        f.writeframes(value.to_bytes(width, "little", signed=True) * int(rate * seconds) * channels)
    return buf.getvalue()


def samples(file):
    with wave.open(str(file)) as f:
        return np.frombuffer(f.readframes(f.getnframes()), "<i2"), f.getparams()


def reset():
    for key in (sounds.PACK_KEY, sounds.VOLUME_KEY, sounds.CUSTOM_NAMES_KEY):
        app_settings.set_value(key, None)
    shutil.rmtree(sounds.CUSTOM_DIR.parent, ignore_errors=True)


def pack_checks():
    """声音包：用临时的声音包文件夹（Evo 默认的那套照抄，另加一套只有一声的）。"""
    out = {}
    reset()
    packs = fake_game._TEMP / "sound-packs"
    shutil.rmtree(packs, ignore_errors=True)
    shutil.copytree(sounds.PACKS_DIR / "evo", packs / "evo")
    (packs / "voice").mkdir()
    (packs / "voice" / "pack.json").write_text(json.dumps({"name": "语音", "license": "MIT"}), encoding="utf-8")
    (packs / "voice" / "summary.wav").write_bytes(wav_bytes())
    (packs / "broken").mkdir()  # 没有 pack.json：不算
    with mock.patch.object(sounds, "PACKS_DIR", packs):
        out["没选过：用 Evo 默认的"] = sounds.pack() == "evo" and sounds.source("xp") == packs / "evo" / "xp.wav"
        out["下拉框：Evo 默认、语音、自己的文件（没有 pack.json 的不算）"] = (
            [one["name"] for one in sounds.packs()] == ["Evo 默认", "语音", "自己的文件"])
        sounds.set_pack("voice")
        out["选了语音包：有的那一声用它的"] = sounds.source("summary") == packs / "voice" / "summary.wav"
        out["语音包缺的那一声：用 Evo 默认的"] = sounds.source("xp") == packs / "evo" / "xp.wav"
        try:
            sounds.set_pack("../evo")
            out["不认识的声音包：拒绝"] = False
        except ValueError:
            out["不认识的声音包：拒绝"] = sounds.pack() == "voice"
        shutil.rmtree(packs / "voice")
        out["选的那套没有了（换版本时删掉了）：用回 Evo 默认的"] = sounds.pack() == "evo"
    reset()
    return out


def volume_checks():
    out = {}
    reset()
    original = sounds.path("xp")
    out["默认 100%：放原文件，不做副本"] = sounds.volume() == 100 and sounds.playable("xp") == original
    out["音量对齐到 5 的倍数，超出范围拉回来"] = (
        sounds.set_volume("52") == 50 and sounds.set_volume(0) == 5 and sounds.set_volume(250) == 100)
    sounds.set_volume(50)
    copy = sounds.playable("xp")
    made = list(sounds.VOLUME_DIR.glob("*.wav"))
    out[f"调到 50%：设置改了就把三声的副本做好（{len(made)} 个）"] = len(made) == 3 and copy.parent == sounds.VOLUME_DIR
    quiet, params = samples(copy)
    loud, loud_params = samples(original)
    out["副本：格式不变，振幅是原来的 25%（50% 的平方）"] = (
        params == loud_params and np.array_equal(quiet, (loud * 0.25).astype("<i2")))
    stamp = copy.stat().st_mtime_ns
    out["同一音量再放：用做好的副本，不重做"] = sounds.playable("xp") == copy and copy.stat().st_mtime_ns == stamp
    sounds.set_volume(20)
    out["换了音量：旧副本删掉"] = not copy.exists() and len(list(sounds.VOLUME_DIR.glob("*.wav"))) == 3
    real = sounds._output
    sounds._output = None
    with mock.patch.object(sounds.winsound, "PlaySound") as play:
        sounds.preview("xp")
    sounds._output = real
    out["试听放的是调小了的副本"] = play.call_args[0][0] == str(sounds.playable("xp"))
    sounds.set_volume(100)
    out["调回 100%：放原文件，副本全删掉"] = (
        sounds.playable("xp") == original and not list(sounds.VOLUME_DIR.glob("*.wav")))
    app_settings.set_value(sounds.VOLUME_KEY, "很大")
    out["设置里的音量坏了：当作 100%"] = sounds.volume() == 100
    try:
        sounds.set_volume("abc")
        out["音量不是数字：拒绝"] = False
    except ValueError:
        out["音量不是数字：拒绝"] = True
    reset()
    return out


def custom_checks():
    out = {}
    reset()
    sounds.set_pack("custom")
    out["选了自己的文件、还没选文件：都用 Evo 默认的"] = (
        all(sounds.source(name) == sounds.path(name) for name in sounds.SOUNDS) and sounds.custom_files() == {})
    length = sounds.save_custom("match_end", wav_bytes(2.5, channels=2), "我的 声音.mp3")
    out["存好：在数据文件夹里，用它"] = (
        round(length, 2) == 2.5 and sounds.source("match_end") == sounds.custom_path("match_end")
        and sounds.custom_path("match_end").is_relative_to(paths.DATA_DIR / "sounds"))
    out["设置页显示原来的文件名和秒数"] = sounds.custom_files() == {"match_end": {"file": "我的 声音.mp3", "seconds": 2.5}}
    out["别的两声：还是 Evo 默认的"] = sounds.source("xp") == sounds.path("xp")
    sounds.set_volume(50)
    quiet, _ = samples(sounds.playable("match_end"))
    out["调了音量：自己的文件也调小"] = quiet.max() == 2000
    sounds.set_pack("evo")
    out["换回 Evo 默认：不用自己的文件（文件留着）"] = (
        sounds.source("match_end") == sounds.path("match_end") and sounds.custom_path("match_end").is_file())
    sounds.set_pack("custom")

    def why(data):
        try:
            sounds.save_custom("xp", data, "x.mp3")
            return None
        except sounds.SoundFileError as e:
            return str(e)

    out["读不出来（不是 WAV）：说原因"] = why(b"ID3 not a wav") == "读不出这个文件。请换一个 MP3 或 WAV 文件再试"
    out["24 位 WAV：放不了，说原因"] = why(wav_bytes(width=3)) == "这种 WAV 格式放不了。请换一个 MP3 文件再试"
    out["太长：写出几秒、最长几秒"] = why(wav_bytes(10.5)) == "这段声音有 10.5 秒，最长 10 秒。请剪短一些再选"
    out["空的：说原因"] = why(wav_bytes(0)) == "文件是空的或者不完整。请换一个文件再试"
    out["太大：说原因"] = why(b"x" * (sounds.MAX_BYTES + 1)) == "文件太大。请选一段不超过 10 秒的声音"
    out["不合格的都不存"] = not sounds.custom_path("xp").exists()
    sounds.clear_custom("match_end")
    out["恢复默认：删掉文件，用回 Evo 默认的"] = (
        not sounds.custom_path("match_end").exists() and sounds.custom_files() == {}
        and sounds.source("match_end") == sounds.path("match_end"))
    reset()
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

    reset()
    s = page()
    out["声音包下拉框：选着 Evo 默认，最后一项是自己的文件；没选自己的文件时没有选文件的按钮"] = (
        '<option value="evo" selected>Evo 默认</option>' in s and '<option value="custom" >自己的文件</option>' in s
        and ">选择文件…</button>" not in s)
    out["音量滑块：5–100，默认 100%"] = (
        'type="range" id="sound-volume" name="volume" min="5" max="100" step="5" value="100"' in s
        and ">100%</output>" in s)
    r = client.post("/settings/sound-pack", data={"token": token, "pack": "custom"})
    out["换声音包：记进设置，回到提示音这一栏"] = (
        sounds.pack() == "custom" and r.status_code == 302 and r.headers["Location"].endswith("/settings#h-sounds"))
    out["不认识的声音包：拒绝"] = client.post("/settings/sound-pack", data={"token": token, "pack": "x"}).status_code == 400
    s = page()
    out["选了自己的文件：三声各有\"选择文件…\"，写着还没选、响的是默认的"] = (
        s.count(">选择文件…</button>") == 3 and s.count("还没选文件，响的是 Evo 默认的声音") == 3 and "恢复默认" not in s)

    def upload(name, data, fetch=True):
        return client.post("/settings/sound-file", headers={"X-Evo-Fetch": "1"} if fetch else {}, data={
            "token": token, "name": name, "filename": "提示.mp3", "file": (io.BytesIO(data), f"{name}.wav")})

    r = upload("summary", wav_bytes(1.5))
    s = page()
    out["选文件：存好，页面上写着文件名、秒数，有\"恢复默认\""] = (
        r.get_json() == {"ok": True, "error": None} and sounds.custom_path("summary").is_file()
        and '你的文件：<span class="sound-file-name">提示.mp3</span>（<span class="num">1.5</span> 秒）' in s
        and s.count(">恢复默认</button>") == 1)
    r = upload("xp", wav_bytes(12))
    out["太长：回答原因（页面写在这一行下面），不存"] = (
        r.get_json() == {"ok": False, "error": "这段声音有 12.0 秒，最长 10 秒。请剪短一些再选"}
        and not sounds.custom_path("xp").exists())
    r = upload("xp", b"not a wav", fetch=False)
    s = page()
    out["没有脚本时不合格：回到提示音这一栏，原因写在那一行下面（只显示一次）"] = (
        r.headers["Location"].endswith("#h-sounds")
        and 'id="sound-error-xp" role="alert">读不出这个文件。请换一个 MP3 或 WAV 文件再试</div>' in s
        and 'id="sound-error-xp" role="alert" hidden></div>' in page())
    out["选文件：不认识的那一声拒绝"] = upload("x", wav_bytes()).status_code == 400
    out["选文件：没带口令拒绝"] = client.post("/settings/sound-file", data={"name": "xp"}).status_code == 403
    client.post("/settings/sound-file/clear", data={"token": token, "name": "summary"})
    out["恢复默认：删掉文件"] = not sounds.custom_path("summary").exists() and "恢复默认" not in page()
    r = client.post("/settings/sound-volume", data={"token": token, "volume": "35"}, headers={"X-Evo-Fetch": "1"})
    out["音量：脚本提交，回答存下的值，页面显示"] = (
        r.get_json() == {"ok": True, "volume": 35} and sounds.volume() == 35 and 'value="35"' in page())
    out["音量不是数字：拒绝"] = client.post("/settings/sound-volume", data={"token": token, "volume": "x"}).status_code == 400
    reset()
    return out


def main():
    print("提示音（声音文件、开关、声音包、音量、自己的文件、什么时候响、记录程序、设置页）")
    checks = {**file_checks(), **setting_checks(), **pack_checks(), **volume_checks(), **custom_checks(),
              **rule_checks(), **panel_checks()}
    try:
        checks.update(collect_checks())
    except MissingShots as e:
        print(f"  ⏭ 截图部分跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
