"""识别淘汰画面：小队被淘汰时屏幕中间弹出的"全军覆没 / 排名第 X"，读出名次。

- "全军覆没"那行几秒后会消失，只读"排名第 X"（怎么认出这个画面见 config/screens.json）
- 字很大、背后是深色横条，比总结页右上角的"第 X"好读；只有一行字，读一次约 50 毫秒
- 格式要严格："排名第"后面紧跟 1–2 位数字，并且在合理范围内（config/eliminated.json），否则当作没读到
- 冠军不会被淘汰，所以没有这个画面

用法：python code/eliminated.py 截图1.png [截图2.png ...]
"""

import argparse
import re
import sys
from pathlib import Path

import game_lang
from ocr import SMALL_CROP_PARAMS, create_engine, load_config, load_image, read_lines

CONFIG_NAME = "eliminated.json"


def read_eliminated(engine, img, cfg):
    """识别一张淘汰画面。返回 {"placement": 17 或 None, "raw": 读到的原文, "score": 置信度或 None}。"""
    spec = cfg["placement"]
    lines = read_lines(engine, img, spec["roi"], cfg, spec.get("anchor"))
    low, high = spec["range"]
    for text, score in lines:
        for wrong, right in cfg["replace"].items():
            text = text.replace(wrong, right)
        m = re.search(spec["pattern"], text.replace(" ", ""))
        if m and score >= cfg["min_score"] and low <= int(m[1]) <= high:
            return {"placement": int(m[1]), "raw": text, "score": score}
    return {"placement": None, "raw": " ".join(t for t, _ in lines), "score": max((s for _, s in lines), default=None)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="识别淘汰画面上的名次（排名第 X）")
    parser.add_argument("images", type=Path, nargs="+", help="淘汰画面截图")
    parser.add_argument("--lang", choices=game_lang.languages(), default=game_lang.DEFAULT,
                        help="游戏语言（默认简体中文 zh-CN）")
    args = parser.parse_args()

    cfg = load_config(CONFIG_NAME, args.lang)
    engine = create_engine(SMALL_CROP_PARAMS)
    for path in args.images:
        r = read_eliminated(engine, load_image(path), cfg)
        score = "-" if r["score"] is None else f"{r['score']:.3f}"
        print(f"{path.name:40s} 名次 {r['placement'] or '-'}（原文 {r['raw'] or '无'}，置信度 {score}）")


if __name__ == "__main__":
    main()
