"""识别开局画面：本局的模式和地图。

开局画面左上角有横幅"排位赛 - 空降地点"，下一行是地图名（如"奥林匹斯"）。从加载画面、等待玩家到选空降点一直显示。
- 模式：横杠前面的文字，必须和 config/match_start.json 里的已知模式完全一致（名字短，模糊匹配容易猜错）
- 地图：和已知地图名单比较，足够相似就采用（OCR 偶尔把"奥"读成繁体"奧"）；都不像就不填
读不出的项为 None，同时返回原文，方便把新地图、新模式加进配置。

用法：python match_start.py 截图1.png [截图2.png ...]
"""

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from ocr_probe import create_engine
from summary_demo import SMALL_CROP_PARAMS, crop, load_image

MATCH_START_CONFIG_PATH = Path("config/match_start.json")


def read_text(engine, img, spec, cfg):
    result = engine(crop(img, spec["roi"], cfg))
    lines = [(t, s) for t, s in zip(result.txts or (), result.scores or ()) if s >= cfg["min_score"]]
    return " ".join(t for t, _ in lines).strip()


def closest_map(text, cfg):
    """和已知地图最像的那个（相似度够高才算）。"""
    text = text.replace(" ", "")
    if not text:
        return None
    best = max(cfg["maps"], key=lambda m: SequenceMatcher(None, text, m).ratio())
    return best if SequenceMatcher(None, text, best).ratio() >= cfg["map_min_similarity"] else None


def read_match_start(engine, img, cfg):
    """识别一张开局画面。返回 {"mode": "ranked"/None, "map": "奥林匹斯"/None, "raw_mode": 原文, "raw_map": 原文}。"""
    raw_mode = read_text(engine, img, cfg["fields"]["mode"], cfg)
    raw_map = read_text(engine, img, cfg["fields"]["map"], cfg)
    mode_name = re.split(r"\s*[-－—–]\s*", raw_mode.replace(" ", ""))[0]
    return {
        "mode": cfg["modes"].get(mode_name),
        "map": closest_map(raw_map, cfg),
        "raw_mode": mode_name,
        "raw_map": raw_map,
    }


def main():
    parser = argparse.ArgumentParser(description="识别开局画面的模式和地图")
    parser.add_argument("images", type=Path, nargs="+", help="开局画面截图")
    args = parser.parse_args()

    cfg = json.loads(MATCH_START_CONFIG_PATH.read_text(encoding="utf-8"))
    engine = create_engine(SMALL_CROP_PARAMS)
    for path in args.images:
        r = read_match_start(engine, load_image(path), cfg)
        print(f"{path.name:36s} 模式 {r['mode'] or '-':7s}（原文 {r['raw_mode'] or '无'}）  "
              f"地图 {r['map'] or '-'}（原文 {r['raw_map'] or '无'}）")


if __name__ == "__main__":
    main()
