"""合成 Evo 的三段提示音（见 code/sounds.py）：python tools/make_sounds.py  →  assets/sounds/evo/*.wav（"Evo 默认"那套声音包）

自己合成，不用别人的音效（没有版权问题，和代码一样是 AGPL-3.0）。每次运行结果一样，改了下面的音符再运行一次就行。
音色：正弦波加两个泛音，按指数衰减，像木琴 / 小钟；起音快慢、泛音多少每一声可以不一样。44.1 kHz、16 位、单声道。

- 对局结束（match_end）：三个音从高往低（G5 → E5 → C5，大三和弦），柔和，有"结束了、放松"的感觉
- 总结页读完了（summary）：同样三个音同时轻轻响起再散开（C 大三和弦），最柔和
- 经验值页（xp）：一个清脆的音（G5）
2026-10-21 用户试听后选的（第一版的"对局结束"是 B5 → E5 往下跳五度、音色偏亮，用户觉得阴森）。
"""

import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent  # 项目文件夹（本文件在 tools/ 里）
OUT = ROOT / "assets" / "sounds" / "evo"
RATE = 44100
PEAK = 0.55  # 最响处是满幅的 55%：游戏声音里听得到，又不吓人；玩家可以在 Windows 的音量合成器里调
C5, E5, G5 = 523.25, 659.26, 783.99

BRIGHT = {"attack": 0.005, "overtones": (0.35, 0.12)}  # 清脆：起音 5 毫秒，泛音多
SOFT = {"attack": 0.012, "overtones": (0.15, 0.03)}  # 柔和：起音慢一点，泛音少
SWELL = {"attack": 0.06, "overtones": (0.12, 0.02)}  # 慢慢响起：起音 60 毫秒，泛音更少

# 名字 → 音色 + notes：[(从第几秒开始, 频率 Hz, 衰减快慢 秒), ...]
SOUNDS = {
    "match_end": {**SOFT, "notes": [(0.0, G5, 0.3), (0.16, E5, 0.3), (0.32, C5, 0.4)]},
    "summary": {**SWELL, "notes": [(0.0, C5, 0.4), (0.0, E5, 0.4), (0.0, G5, 0.4)]},
    "xp": {**BRIGHT, "notes": [(0.0, G5, 0.2)]},
}


def note(freq, decay, length, attack, overtones):
    t = np.arange(int(RATE * length)) / RATE
    second, third = overtones
    tone = (np.sin(2 * np.pi * freq * t)
            + second * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-t / (decay * 0.5))
            + third * np.sin(2 * np.pi * 3 * freq * t) * np.exp(-t / (decay * 0.3)))
    return (1 - np.exp(-t / attack)) * np.exp(-t / decay) * tone


def render(sound):
    notes = sound["notes"]
    length = max(start + decay * 4 for start, _, decay in notes)  # 衰减 4 倍的时间以后几乎听不见了
    out = np.zeros(int(RATE * length))
    for start, freq, decay in notes:
        begin = int(RATE * start)
        piece = note(freq, decay, length - start, sound["attack"], sound["overtones"])
        out[begin:begin + len(piece)] += piece
    fade = min(len(out), int(RATE * 0.05))
    out[-fade:] *= np.linspace(1, 0, fade)  # 最后 50 毫秒淡出，免得"咔"一声
    return out / np.abs(out).max() * PEAK


def write(path, samples):
    data = (samples * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(data)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, sound in SOUNDS.items():
        path = OUT / f"{name}.wav"
        samples = render(sound)
        write(path, samples)
        print(f"{path.relative_to(ROOT)}：{len(samples) / RATE:.2f} 秒，{path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
