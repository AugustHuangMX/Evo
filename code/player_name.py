"""读自己的玩家名（游戏里显示的名字），用来区分账号。

游戏里看不到自己的固定 ID（大厅左下角的 SID 是会话编号，每次登录都变；总结页卡片下面的 ID 只显示队友的），
所以按名字区分账号。Steam 改名后游戏里的名字也会变，到时在面板里把新名字合并到原来的账号。
两个地方能读到（区域写在 config/player_name.json）：
- 总结页中间卡片（自己的卡片永远在中间）
- 对局中左下角头像旁边：只在认出自己传奇的那一帧读（观战时左下角是被观战的人，认不出自己的传奇）
只读自己的名字，队友的名字和 ID 一律不读（原则 7）。

检查截图：python code/player_name.py 截图1.png [截图2.png ...]   （两个位置都读一遍，打印结果）
"""

import argparse
from pathlib import Path

from ocr import SMALL_CROP_PARAMS, create_engine, crop, load_config, load_image
from storage import name_key

CONFIG_NAME = "player_name.json"


def pick_name(boxes, txts, scores, min_score):
    """从一块里读到的几段文字中挑出名字，返回 (名字, 置信度)。
    名字左对齐：从最左边那段读起；紧挨着的下一段接上（名字里有空格时可能被分成两段），
    隔得远的（卡片图案、语音图标）不算。最左边那段置信度不够就返回 None（宁可读不到，也不要读错）。"""
    parts = sorted(zip(boxes, txts, scores), key=lambda part: part[0][:, 0].min())
    box, text, score = parts[0]
    if score < min_score:
        return None
    pieces, right, height = [text], box[:, 0].max(), box[:, 1].max() - box[:, 1].min()
    for box, text, piece_score in parts[1:]:
        if box[:, 0].min() - right > height or piece_score < min_score:  # 隔了超过一个字高，不是名字的一部分
            break
        pieces.append(text)
        right, score = box[:, 0].max(), min(score, piece_score)
    return " ".join(pieces).strip(), score


class PlayerNameReader:
    """read(画面, 位置) 读名字。位置是 "summary"（总结页中间卡片）或 "hud"（对局中头像旁边）。"""

    def __init__(self, engine, cfg=None):
        self.engine, self.cfg = engine, cfg or load_config(CONFIG_NAME)

    def read(self, frame, place):
        """返回 (名字, 置信度)；没读到、置信度不够或长度不像名字时返回 None。"""
        cfg = self.cfg
        result = self.engine(crop(frame, cfg["places"][place]["roi"], cfg))
        if not result.txts:
            return None
        found = pick_name(result.boxes, result.txts, result.scores, cfg["min_score"])
        if found is None or not cfg["min_length"] <= len(name_key(found[0])) <= cfg["max_length"]:
            return None
        return found


def main():
    parser = argparse.ArgumentParser(description="读截图上自己的玩家名（总结页中间卡片、对局中头像旁边）")
    parser.add_argument("images", type=Path, nargs="+", help="截图")
    args = parser.parse_args()

    cfg = load_config(CONFIG_NAME)
    reader = PlayerNameReader(create_engine(SMALL_CROP_PARAMS), cfg)
    for path in args.images:
        img = load_image(path)
        texts = []
        for place, spec in cfg["places"].items():
            found = reader.read(img, place)
            texts.append(f"{spec['label']} {f'{found[0]}（置信度 {found[1]:.2f}）' if found else '-'}")
        print(f"{path.name:40s} " + "  ".join(texts))


if __name__ == "__main__":
    main()
