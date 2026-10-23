"""对局结束的提示音（2026-10-21 用户决定，默认开）：一局里最多响三声，每一声每局最多一次（什么时候响见 live_collect.SoundCues）。

- 对局结束（match_end）：小队打完了（淘汰画面、冠军横幅、右下角"返回大厅"按钮），提醒去总结页
- 总结页读完了（summary）：名次、击杀、伤害都读到了才响，听到就可以离开
- 经验值页（xp）：一进经验值页就响（数字有动画，Evo 离开时才读最后一帧，没有"读完了"的那一刻）

用 Python 自带的 winsound 播放，不等它放完，不加依赖。设置页能关、能试听。

声音包（2026-10-24 用户决定：整套切换，三声一起换）：
- 随 Evo 发的：assets/sounds/<名字>/ 一个文件夹一套，里面 pack.json（name 显示的名字、by 作者、license 许可、
  order 排第几）和 match_end.wav、summary.wav、xp.wav（16 位 PCM，每段最长 MAX_SECONDS 秒）。
  evo 是 Evo 自己合成的那套（tools/make_sounds.py，AGPL-3.0），永远排第一；缺哪一声就用 evo 的那一声
- 自己的文件（custom）：设置页给每一声选一个音频文件（MP3、WAV 等），窗口先转成 WAV
  （panel/templates/settings.html），这里核对后存进数据文件夹的 sounds/custom/；没选的那一声用 evo 的
音量（VOLUME_MIN–100%，默认 100）：winsound 不能调音量，所以另存一份调小了的副本（数据文件夹的 sounds/volume/）
再放。设置改了以后面板先做好（prepare），记录程序找不到时自己做（几十毫秒）。

试听：python code/sounds.py
"""

import array
import hashlib
import io
import json
import os
import re
import time
import wave
import winsound
from pathlib import Path

import app_settings
from paths import ASSETS_DIR, DATA_DIR

SOUNDS = {"match_end": "对局结束", "summary": "总结页读完了", "xp": "经验值页"}  # 按一局里先后的顺序
SETTING_KEY = "sounds_on"
PACK_KEY = "sound_pack"  # 选的声音包
VOLUME_KEY = "sound_volume"  # 音量（百分数）
CUSTOM_NAMES_KEY = "sound_custom_files"  # 自己的文件：每一声原来叫什么（设置页显示）
DEFAULT_PACK = "evo"
CUSTOM = "custom"
CUSTOM_LABEL = "自己的文件"
PACKS_DIR = ASSETS_DIR / "sounds"
CUSTOM_DIR = DATA_DIR / "sounds" / "custom"
VOLUME_DIR = DATA_DIR / "sounds" / "volume"
VOLUME_MIN, VOLUME_MAX, VOLUME_STEP = 5, 100, 5
MAX_SECONDS = 10
MAX_BYTES = 4 * 1024 * 1024  # 转好的 WAV：10 秒立体声 48 kHz 不到 2 MB
_output = None  # 测试时换成记下"响了哪一声"的函数，不真的出声（见 tests/fake_game.py）


class SoundFileError(Exception):
    """自己的文件不能用，str(e) 是给用户看的原因和怎么办。"""


def path(name, pack=DEFAULT_PACK):
    """随 Evo 发的声音包里这一声的文件（不一定有）。"""
    return PACKS_DIR / pack / f"{name}.wav"


def custom_path(name):
    return CUSTOM_DIR / f"{name}.wav"


def enabled():
    """设置里开着没有（默认开）。"""
    return app_settings.get(SETTING_KEY, True) is not False


def _pack_info(folder):
    try:
        info = json.loads((folder / "pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and info.get("name") else None


def packs():
    """能选的声音包，按设置页下拉框的顺序：[{"key", "name", "by"}]。Evo 默认排第一，"自己的文件"排最后。"""
    found = []
    for folder in sorted(PACKS_DIR.iterdir()) if PACKS_DIR.is_dir() else []:
        info = _pack_info(folder)
        if info and re.fullmatch(r"[a-z0-9_-]+", folder.name) and folder.name != CUSTOM:
            first = folder.name == DEFAULT_PACK
            found.append(((not first, info.get("order", 99)),
                          {"key": folder.name, "name": info["name"], "by": info.get("by")}))
    return [one for _, one in sorted(found, key=lambda x: x[0])] + [{"key": CUSTOM, "name": CUSTOM_LABEL, "by": None}]


def pack():
    """现在选的声音包；没选过、选的那套没有了（换版本时删掉了）都用 Evo 默认的。"""
    choice = app_settings.get(PACK_KEY)
    return choice if choice in {one["key"] for one in packs()} else DEFAULT_PACK


def set_pack(key):
    if key not in {one["key"] for one in packs()}:
        raise ValueError(f"没有这个声音包：{key}")
    app_settings.set_value(PACK_KEY, key)
    prepare()


def volume():
    value = app_settings.get(VOLUME_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return VOLUME_MAX
    return int(min(VOLUME_MAX, max(VOLUME_MIN, round(value))))


def set_volume(value):
    """存音量（对齐到 VOLUME_STEP，超出范围的拉回来），做好副本。返回存下的值。"""
    value = int(min(VOLUME_MAX, max(VOLUME_MIN, round(float(value) / VOLUME_STEP) * VOLUME_STEP)))
    app_settings.set_value(VOLUME_KEY, value)
    prepare()
    return value


def source(name, choice=None):
    """这一声用哪个文件（还没调音量）：选的那套里有就用它，没有用 Evo 默认的。"""
    choice = choice or pack()
    mine = custom_path(name) if choice == CUSTOM else path(name, choice)
    return mine if mine.is_file() else path(name)


def playable(name):
    """真正要放的文件：音量 100% 就是原文件，不然是调小了的副本（没有就现做）。找不到文件返回 None。"""
    src = source(name)
    if not src.is_file():
        return None
    level = volume()
    if level >= VOLUME_MAX:
        return src
    try:
        return _quieter(name, src, level)
    except (OSError, EOFError, wave.Error):
        return src  # 做不出副本（数据文件夹写不进去）：照原样放，总比不响好


def _quieter(name, src, level):
    st = src.stat()
    tag = hashlib.sha1(f"{src.resolve()}|{st.st_size}|{st.st_mtime_ns}|{level}".encode()).hexdigest()[:12]
    out = VOLUME_DIR / f"{name}-{tag}.wav"
    if out.is_file():
        return out
    with wave.open(str(src)) as f:
        params = f.getparams()
        frames = f.readframes(params.nframes)
    if params.sampwidth != 2:
        return src
    samples = array.array("h")
    samples.frombytes(frames[:len(frames) // 2 * 2])
    gain = (level / 100) ** 2  # 听起来的响度不是和振幅成正比：50% 约小 12 dB，和常见的音量滑块差不多
    quiet = array.array("h", [int(s * gain) for s in samples])
    VOLUME_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")  # 面板和记录程序可能同时在做：各写各的再换上
    with wave.open(str(tmp), "wb") as f:
        f.setparams(params)
        f.writeframes(quiet.tobytes())
    os.replace(tmp, out)
    for old in VOLUME_DIR.glob(f"{name}-*.wav"):
        if old != out:
            _remove(old)
    return out


def _remove(file):
    try:
        file.unlink(missing_ok=True)
    except OSError:  # 正在放（被占着）：下次再删
        pass


def prepare():
    """设置改了以后（面板调用）：先把要放的副本做好，删掉用不到的，免得对局里现做。"""
    keep = {playable(name) for name in SOUNDS}
    for old in VOLUME_DIR.glob("*.wav") if VOLUME_DIR.is_dir() else []:
        if old not in keep:
            _remove(old)


def seconds(file):
    """一个 WAV 有几秒；读不出来返回 None。"""
    try:
        with wave.open(str(file)) as f:
            return f.getnframes() / f.getframerate()
    except (OSError, EOFError, wave.Error, ZeroDivisionError):
        return None


def custom_files():
    """自己的文件：{这一声: {"file": 原来的文件名, "seconds": 几秒}}，没选的不在里面。"""
    names = app_settings.get(CUSTOM_NAMES_KEY)
    names = names if isinstance(names, dict) else {}
    return {name: {"file": names.get(name) or custom_path(name).name, "seconds": seconds(custom_path(name))}
            for name in SOUNDS if custom_path(name).is_file()}


def save_custom(name, data, filename=None):
    """设置页"选择文件…"：data 是窗口转好的 WAV。核对格式和长度后存进数据文件夹，返回几秒。
    不能用时抛 SoundFileError（原因和怎么办，给用户看）。"""
    if name not in SOUNDS:
        raise ValueError(f"没有这个提示音：{name}")
    if len(data) > MAX_BYTES:
        raise SoundFileError(f"文件太大。请选一段不超过 {MAX_SECONDS} 秒的声音")
    try:
        with wave.open(io.BytesIO(data)) as f:
            channels, width, rate, count = f.getnchannels(), f.getsampwidth(), f.getframerate(), f.getnframes()
            frames = f.readframes(count)
    except (EOFError, wave.Error):
        raise SoundFileError("读不出这个文件。请换一个 MP3 或 WAV 文件再试") from None
    if width != 2 or channels not in (1, 2) or not 8000 <= rate <= 48000:
        raise SoundFileError("这种 WAV 格式放不了。请换一个 MP3 文件再试")
    if count == 0 or len(frames) < count * channels * width:
        raise SoundFileError("文件是空的或者不完整。请换一个文件再试")
    length = count / rate
    if length > MAX_SECONDS + 0.05:
        raise SoundFileError(f"这段声音有 {length:.1f} 秒，最长 {MAX_SECONDS} 秒。请剪短一些再选")
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = custom_path(name).with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, custom_path(name))
    names = app_settings.get(CUSTOM_NAMES_KEY)
    names = names if isinstance(names, dict) else {}
    names[name] = Path(filename).name[:120] if filename else None
    app_settings.set_value(CUSTOM_NAMES_KEY, names)
    prepare()
    return length


def clear_custom(name):
    """"恢复默认"：删掉这一声自己的文件，用回 Evo 默认的。"""
    if name not in SOUNDS:
        raise ValueError(f"没有这个提示音：{name}")
    custom_path(name).unlink(missing_ok=True)
    names = app_settings.get(CUSTOM_NAMES_KEY)
    if isinstance(names, dict) and name in names:
        del names[name]
        app_settings.set_value(CUSTOM_NAMES_KEY, names)
    prepare()


def play(name):
    """记录程序用：设置里开着才响。响了返回 True。"""
    return enabled() and preview(name)


def preview(name):
    """按现在选的声音包和音量响一声，不管开关（设置页的"试听"）。找不到文件、放不出来（如电脑没有声卡）返回 False。"""
    if name not in SOUNDS:
        raise ValueError(f"没有这个提示音：{name}")
    if _output is not None:
        _output(name)
        return True
    target = playable(name)
    if target is None:
        return False
    try:
        winsound.PlaySound(str(target), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except RuntimeError:
        return False
    return True


if __name__ == "__main__":
    print(f"声音包：{pack()}，音量 {volume()}%")
    for key, label in SOUNDS.items():
        print(f"{label}（{source(key)}）")
        preview(key)
        time.sleep(max(1.6, (seconds(source(key)) or 0) + 0.4))
