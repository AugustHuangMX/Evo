"""看玩家发来的诊断压缩包（开发工具；压缩包由 Evo诊断工具.exe 生成，见 code/evo_diag.py）。

用法：python tools/diag_report.py Evo诊断_xxx.zip [--csv 每张的结果.csv]

1. 打印 info.txt：电脑、显示器（HDR、缩放、主显示器）、Evo 截的是哪个显示器、Apex 的设置、开着的软件、截屏结果
2. 看 captures.csv：游戏开着时有没有"没有新画面"（截屏卡住）、最长连续多久画面一点没变
3. 用现在的识别规则认 frames/ 里的每一张（和记录程序一样几种游戏语言一起认）：是哪个画面、左下角认不认得出自己的传奇。
   游戏画面比例按玩家 Evo 的设置；自动检测时按 Apex 视频设置（全屏时用里面的分辨率，同 game_aspect.from_video_config）
4. 最后按上面的结果列几条线索（只是提示，要看截图确认）

截图里有其他玩家的名字：压缩包和解出来的截图都不要提交到仓库（放 captures/ 里）。
"""

import argparse
import csv
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
import game_aspect
import game_lang
import layout
from legends import LegendDetector
from ocr import SMALL_CROP_PARAMS, create_engine
from screens import GameScreens


def read_image(zf, name):
    return cv2.imdecode(np.frombuffer(zf.read(name), np.uint8), cv2.IMREAD_COLOR)


def aspect_of(info):
    """按玩家的设置定游戏画面比例：(宽高比或 None, 说明)。None = 和截图一样。"""
    picked = info.get("Evo", {}).get("设置", {}).get(game_aspect.SETTING_KEY, game_aspect.AUTO)
    if picked in game_aspect.ASPECTS:
        return game_aspect.ASPECTS[picked], f"玩家在 Evo 设置页选的 {picked}"
    video = info.get("Apex", {}).get("视频设置")
    if isinstance(video, dict) and video.get("全屏") == "1":
        try:
            w, h = int(video["宽"]), int(video["高"])
            if w > 0 and h > 0:
                return w / h, f"Apex 视频设置：全屏 {w}×{h}"
        except (KeyError, TypeError, ValueError):
            pass
    return None, "和截图一样"


def capture_stats(rows):
    """captures.csv 里游戏开着的那些行：截屏次数、没有新画面几次、最长连续"没变"几次。"""
    playing = [r for r in rows if r["游戏开着"] == "是"]
    results = Counter(r["结果"].split("：")[0] for r in playing)
    longest = run = 0
    for r in playing:
        run = run + 1 if r["结果"] == "没变" else 0
        longest = max(longest, run)
    return len(playing), results, longest


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="看玩家发来的诊断压缩包：打印诊断信息，用现在的识别规则认一遍截图")
    parser.add_argument("zip", type=Path, help="Evo诊断_xxx.zip")
    parser.add_argument("--csv", type=Path, help="每张截图的结果写到这个文件")
    args = parser.parse_args()

    zf = zipfile.ZipFile(args.zip)
    names = set(zf.namelist())
    info = json.loads(zf.read("info.json"))
    print(zf.read("info.txt").decode("utf-8-sig"))

    rows = list(csv.DictReader(zf.read("captures.csv").decode("utf-8-sig").splitlines()))
    playing, results, longest = capture_stats(rows)
    print("[截屏记录（captures.csv，游戏开着时）]")
    print(f"  截屏 {playing} 次：" + "，".join(f"{k} {v} 次" for k, v in results.most_common()) if playing
          else "  游戏一直没开")
    if playing:
        print(f"  最长连续\"没变\"{longest} 次（约 {longest * 2} 秒）")

    aspect, why = aspect_of(info)
    layout.set_game_aspect(aspect)
    frames = sorted(n for n in names if n.startswith("frames/") and n.endswith(".jpg"))
    print(f"\n[用现在的识别规则认 frames/ 里的 {len(frames)} 张]（游戏画面比例：{why}）")
    langs = []
    detector = GameScreens(create_engine(SMALL_CROP_PARAMS), log=langs.append)
    legends = LegendDetector()
    screens, legend_votes, first = Counter(), Counter(), {}
    out = []
    for i, name in enumerate(frames, 1):
        img = read_image(zf, name)
        screen = detector.detect(img)
        own = legends.identify(img)
        if screen:
            screens[screen] += 1
            first.setdefault(screen, name)
        if own:
            legend_votes[legends.name(own)] += 1
        out.append({"文件": name, "画面": screen or "", "自己的传奇": legends.name(own) if own else ""})
        if i % 100 == 0:
            print(f"  ……认了 {i} 张")
    labels = {key: spec["label"] for key, spec in detector.cfg["screens"].items()}
    found = "、".join(f"{labels.get(k, k)} {n} 张" for k, n in screens.most_common()) or "一个都没有"
    print(f"  认出的画面：{found}")
    for key, name in first.items():
        print(f"    {labels.get(key, key)} 第一张：{name}")
    print(f"  {langs[-1]}" if langs else f"  游戏语言：{game_lang.label(detector.lang)}（没认定）")
    print(f"  外观像了、跑过识别的：{detector.looked_alike} 次")
    own_total = sum(legend_votes.values())
    print(f"  左下角认出自己的传奇：{own_total} 张" + (f"（{'、'.join(f'{k} {v}' for k, v in legend_votes.most_common())}）"
                                               if own_total else ""))
    neither = sum(1 for r in out if not r["画面"] and not r["自己的传奇"])
    print(f"  画面、传奇都没认出：{neither} 张")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, ["文件", "画面", "自己的传奇"])
            writer.writeheader()
            writer.writerows(out)
        print(f"  每张的结果：{args.csv}")

    print("\n[线索]（只是提示，要看截图确认）")
    hints = []
    if not info.get("截屏结果", {}).get("看到的游戏进程"):
        hints.append("这次没看到游戏开着：截到的只是桌面，请玩家开着游戏再截一次")
    if results.get("没有新画面"):
        hints.append(f"游戏开着时有 {results['没有新画面']} 次没截到新画面（最长连续 "
                     f"{info.get('截屏结果', {}).get('游戏开着时最长连续没有新画面', '?')} 次）：玩家一直在游戏里的话，"
                     "就是截屏卡住了（dxcam 拿不到新画面时 Evo 用的是缓存的旧图）；切出游戏、桌面不动时也会这样。"
                     "看 captures.csv 是哪一段、tool_log.txt 里 dxcam 的警告、Apex 是不是勾了\"禁用全屏优化\"")
    if longest >= 30:
        hints.append(f"游戏开着时画面最长连续 {longest} 次一点没变：可能截屏卡住了，或者一直停在菜单里，看那段截图")
    monitors = info.get("显示器")
    if isinstance(monitors, list):
        if len(monitors) > 1:
            hints.append(f"接了 {len(monitors)} 个显示器，Evo 只截主显示器：看 monitors/ 的缩略图，游戏在不在主显示器上")
        if any(m.get("hdr") == "开" for m in monitors):
            hints.append("有显示器开着 HDR：截图颜色可能发白（模拟过影响不大，看截图确认）")
    flags = info.get("Apex", {}).get("兼容性设置")
    if isinstance(flags, list) and any("DISABLEDXMAXIMIZEDWINDOWEDMODE" in v.upper() for _, _, v in flags):
        hints.append("Apex 勾了\"禁用全屏优化\"：独占全屏时截屏可能拿不到游戏画面")
    if frames and not screens and not own_total:
        hints.append("画面、传奇一张都没认出：截到的可能不是游戏画面（看 frames/ 和 monitors/），或者分辨率、界面完全不一样")
    elif own_total and not screens:
        hints.append("认得出传奇、但大厅 / 总结页等一个都没认出：可能是游戏语言还不支持，或者这几个画面没截到（看 frames/）")
    elif frames and own_total < len(frames) * 0.1 and not results.get("没有新画面"):
        hints.append("传奇很少认得出：看对局中的截图，左下角头像是不是被悬浮窗挡住、或者界面不一样")
    print("\n".join(f"  - {h}" for h in hints) or "  （没看出明显的问题：对照 Evo 的日志 evo_logs/ 看这段时间 Evo 认出了什么）")


if __name__ == "__main__":
    main()
