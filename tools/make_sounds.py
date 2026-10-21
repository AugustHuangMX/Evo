"""合成 Evo 的三段提示音（见 code/sounds.py）：python tools/make_sounds.py  →  assets/sounds/*.wav

自己合成，不用别人的音效（没有版权问题，和代码一样是 MIT）。每次运行结果一样，改了下面的音符再运行一次就行。
音色：正弦波加两个泛音，起音 5 毫秒、按指数衰减，像木琴 / 小钟。44.1 kHz、16 位、单声道。

- 对局结束（match_end）：两个音往下走（B5 → E5）
- 总结页读完了（summary）：同样两个音往上走（E5 → B5），和"对局结束"正好相反，听起来像"好了"
- 经验值页（xp）：一个音（G5）
"""

import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent  # 项目文件夹（本文件在 tools/ 里）
OUT = ROOT / "assets" / "sounds"
RATE = 44100
PEAK = 0.55  # 最响处是满幅的 55%：游戏声音里听得到，又不吓人；玩家可以在 Windows 的音量合成器里调
E5, G5, B5 = 659.26, 783.99, 987.77

# 名字 → [(从第几秒开始, 频率 Hz, 衰减快慢 秒), ...]
SOUNDS = {
    "match_end": [(0.0, B5, 0.16), (0.15, E5, 0.22)],
    "summary": [(0.0, E5, 0.16), (0.15, B5, 0.22)],
    "xp": [(0.0, G5, 0.2)],
}


def note(freq, decay, length):
    t = np.arange(int(RATE * length)) / RATE
    attack = 1 - np.exp(-t / 0.005)
    tone = (np.sin(2 * np.pi * freq * t)
            + 0.35 * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-t / (decay * 0.5))
            + 0.12 * np.sin(2 * np.pi * 3 * freq * t) * np.exp(-t / (decay * 0.3)))
    return attack * np.exp(-t / decay) * tone


def render(notes):
    length = max(start + decay * 5 for start, _, decay in notes)  # 衰减 5 倍的时间以后几乎听不见了
    out = np.zeros(int(RATE * length))
    for start, freq, decay in notes:
        begin = int(RATE * start)
        piece = note(freq, decay, length - start)
        out[begin:begin + len(piece)] += piece
    fade = min(len(out), int(RATE * 0.02))
    out[-fade:] *= np.linspace(1, 0, fade)  # 最后 20 毫秒淡出，免得"咔"一声
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
    for name, notes in SOUNDS.items():
        path = OUT / f"{name}.wav"
        samples = render(notes)
        write(path, samples)
        print(f"{path.relative_to(ROOT)}：{len(samples) / RATE:.2f} 秒，{path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
