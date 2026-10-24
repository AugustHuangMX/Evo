"""识别开局画面：本局的模式和地图。

开局画面左上角有横幅"排位赛 - 空降地点"（固定跳点制的局）或"排位赛"（跳伞制的局，看段位），下一行是地图名（如"奥林匹斯"）。
从加载画面、等待玩家到选空降点一直显示。
- 模式：横杠前面的文字（没有横杠就是整行），必须和 config/match_start.json 里的已知模式完全一致（名字短，模糊匹配容易猜错）
- 地图：和已知地图名单比较，足够相似就采用（OCR 偶尔把"奥"读成繁体"奧"）；都不像就不填
读不出的项为 None，同时返回原文，方便把新地图、新模式加进配置。

用法：python code/match_start.py 截图1.png [截图2.png ...]
"""

import argparse
import re
from difflib import SequenceMatcher
from pathlib import Path

import game_lang
from ocr import SMALL_CROP_PARAMS, create_engine, load_config, load_image, read_lines

CONFIG_NAME = "match_start.json"


def read_text(engine, img, spec, cfg):
    """一个小块里置信度够高的各行，用空格连起来。"""
    lines = read_lines(engine, img, spec["roi"], cfg, spec.get("anchor"))
    return " ".join(t for t, s in lines if s >= cfg["min_score"]).strip()


def closest_map(text, cfg):
    """和已知地图最像的那个（相似度够高才算），返回简体的地图名。别的游戏语言的地图名单是
    {"画面上的写法": "简体名字"}（见 game_lang.aliases），比较时不管空格和大小写。"""
    text = text.replace(" ", "").lower()
    if not text:
        return None
    maps = game_lang.aliases(cfg["maps"])

    def similarity(shown):
        return SequenceMatcher(None, text, shown.replace(" ", "").lower()).ratio()

    best = max(maps, key=similarity)
    return maps[best] if similarity(best) >= cfg["map_min_similarity"] else None


def read_match_start(engine, img, cfg):
    """识别一张开局画面。返回 {"mode": "ranked"/None, "map": "奥林匹斯"/None, "raw_mode": 原文, "raw_map": 原文}。"""
    raw_mode = read_text(engine, img, cfg["fields"]["mode"], cfg)
    raw_map = read_text(engine, img, cfg["fields"]["map"], cfg)
    mode_name = re.split(r"\s*[-－—–]\s*", raw_mode.replace(" ", ""))[0]
    modes = {name.replace(" ", "").lower(): mode for name, mode in cfg["modes"].items()}  # 不管空格和大小写
    return {
        "mode": modes.get(mode_name.lower()),
        "map": closest_map(raw_map, cfg),
        "raw_mode": mode_name,
        "raw_map": raw_map,
    }


def main():
    parser = argparse.ArgumentParser(description="识别开局画面的模式和地图")
    parser.add_argument("images", type=Path, nargs="+", help="开局画面截图")
    parser.add_argument("--lang", choices=game_lang.languages(), default=game_lang.DEFAULT,
                        help="游戏语言（默认简体中文 zh-CN）")
    args = parser.parse_args()

    cfg = load_config(CONFIG_NAME, args.lang)
    engine = create_engine(SMALL_CROP_PARAMS)
    for path in args.images:
        r = read_match_start(engine, load_image(path), cfg)
        print(f"{path.name:36s} 模式 {r['mode'] or '-':7s}（原文 {r['raw_mode'] or '无'}）  "
              f"地图 {r['map'] or '-'}（原文 {r['raw_map'] or '无'}）")


if __name__ == "__main__":
    main()
