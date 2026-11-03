"""游戏里的数据页：认出这一页，读右边一栏（下拉菜单选中的那个赛季的合计）和中间一栏（大逃杀的生涯合计）。

录入历史赛季数据时用（2026-11-01 用户定的：设置页按钮进入录入模式，玩家在游戏里自己一个一个点赛季，原则 1）。
规则在 config/season_stats.json，页面的样子见 docs/识别经验.md"游戏里的数据页"。
- 认出这一页：右边一栏左上角"赛季 / 比赛"两行小字，或者右上角"赛季 / 前5名"（鼠标停在下拉框上弹出的提示一次只挡住一个；
  下拉菜单开着时两个都被挡住，不算），先比外观、像了再读关键字（screens.py）
- 下拉框里选中的那一项："第 30 赛季：诸神烙印" = 这个赛季全部大逃杀（排位 + 匹配），"第 30 赛季：排位" = 只算排位
- 每一栏 15 个数，按块读（一块一次识别，一项一项读要多三倍）：上面一行比赛 / 胜利 / 前 5 名，两个圆圈
  （伤害：最高、平均；击杀：阵亡、击杀/阵亡比），下面六行（最高击杀、击倒、助攻、最长连胜、急救、重生）
- 1000 以上游戏只写"1.03K"：局数用"伤害 ÷ 平均伤害"算出准确的（平均伤害有两位小数，够准）；算不出的（生涯的前 5 名）
  记约数，标出来
- 数字互相核对（season_rules.check）：伤害 ÷ 局数 = 平均、击杀 ÷ 阵亡 = 击杀/阵亡比、胜利 ≤ 前 5 名 ≤ 比赛……对不上的写进问题
- 鼠标停在数字上弹出的说明框会挡住别的数字：读到带"%""："的字，这一张算被挡住（obscured），不用
- 不读的：赛段的段位徽章、账号等级

检查截图：python code/season_stats.py 截图1.png [截图2.png ...]   （读两栏，打印结果和核对）
生成参照图（两个角各一张）：python code/season_stats.py --save-template 截图.png
游戏画面不是 16:9 时（比如拉伸的 16:10）两条命令都加 --aspect 16:10，和记录程序一样换算（见 layout.py）
"""

import argparse
import re
import sys
from pathlib import Path

import cv2
import numpy as np

import game_lang
from game_aspect import ASPECTS
from layout import cut, set_game_aspect
from ocr import load_config, load_image
from player_name import pick_name
from screens import ScreenDetector, save_template
from season_rules import (DECIMALS, FIELDS, KIND_ALL, KIND_CAREER, KIND_RANKED, LABELS,  # noqa: F401  别的文件也从这里用
                          NUMBER, check, empty, parse_number, show)
from storage import name_key
from summary_page import same_piece

CONFIG_NAME = "season_stats.json"
PAGE = "season_stats"  # detect 里这个画面的名字（参照图 config/templates/season_stats.png；另一个角 "as" 这个名字）


class SeasonPage:
    """engine：识别引擎；cfg：config/season_stats.json（不给就读，lang 是游戏语言）。"""

    def __init__(self, engine, cfg=None, lang=None):
        self.engine = engine
        self.cfg = cfg or load_config(CONFIG_NAME, lang)
        self.detector = ScreenDetector(engine, self.cfg["detect"], lang)

    def on_page(self, frame):
        """是数据页，而且右边的下拉菜单收着。"""
        return self.detector.detect(frame) == PAGE

    def _piece(self, frame, box, dx=0):
        """按基准坐标裁一块（往右挪 dx），放大 upscale 倍，转灰度。"""
        cfg = self.cfg
        x, y, w, h = box
        up = cfg["upscale"]
        piece = cut(frame, [x + dx, y, w, h], cfg["base_resolution"], cfg.get("anchor"))  # 16:10 等比例见 layout.py
        piece = cv2.resize(piece, (w * up, h * up), interpolation=cv2.INTER_CUBIC)
        if cfg.get("grayscale"):
            piece = cv2.cvtColor(cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        return piece

    def _texts(self, piece):
        """识别一块，返回 [(文字（去掉空格）, 置信度, 中心 x, 中心 y)]，坐标是基准坐标、相对这一块的左上角。"""
        up = self.cfg["upscale"]
        result = self.engine(piece)
        out = []
        for box, text, score in zip(result.boxes if result.boxes is not None else (), result.txts or (),
                                    result.scores or ()):
            box = np.asarray(box) / up
            out.append((text.replace(" ", ""), float(score), float(box[:, 0].mean()), float(box[:, 1].mean())))
        return out

    def _read_block(self, frame, name, dx, memo):
        """读一块；memo（同一次停在这一页时同一个字典）里有、这一块又没变，就沿用上次的（规则同总结页，
        见 config/summary.json 的 unchanged）。"""
        piece = self._piece(frame, self.cfg["blocks"][name], dx)
        key = (name, dx)
        before = memo.get(key) if memo is not None else None
        if before is not None and same_piece(before[0], piece, self.cfg["unchanged"]):
            return before[1]
        texts = self._texts(piece)
        if memo is not None:
            memo[key] = (piece, texts)
        return texts

    def name(self, frame):
        """页面上方的玩家名，读不到返回 None。"""
        spot = self.cfg["name"]
        result = self.engine(self._piece(frame, spot["roi"]))
        if not result.txts:
            return None
        found = pick_name(result.boxes, result.txts, result.scores, spot["min_score"])
        return found[0] if found and len(name_key(found[0])) >= 2 else None

    def label(self, frame, memo=None):
        """右边下拉框里选中的那一项：(赛季, 全部 / 排位, 赛季名, 原文)；读不出时前三项为 None。
        memo：同 read，下拉框没变就沿用上次的。"""
        spec = self.cfg["label"]
        piece = self._piece(frame, spec["roi"])
        before = memo.get("label") if memo is not None else None
        if before is not None and same_piece(before[0], piece, self.cfg["unchanged"]):
            return before[1]
        raw = "".join(t for t, *_ in self._texts(piece))
        m = re.match(spec["pattern"], raw)
        found = (None, None, None, raw) if not m else \
            (int(m[1]), (KIND_RANKED if m[2] == spec["ranked"] else KIND_ALL), m[2], raw)
        if memo is not None:
            memo["label"] = (piece, found)
        return found

    def _number(self, text):
        """这段文字是不是单独一个数，是就返回去掉"前5名"这类带数字的名字以后的文字，不是返回 None。"""
        for word in self.cfg["not_numbers"]:
            text = text.replace(word, "")
        return text if NUMBER.fullmatch(text.replace("，", ",")) else None

    def _by_line(self, texts, names):
        """一块里的"名字 + 数"：名字和数连在一起读出来的（"最高4,453"）直接拆开；分开读出来的，每个数归到
        同一行、左边最近的那个名字；同一行没有名字的，归到正上方还没有数的那个名字（游戏把数折到了下一行，
        见 config 的 wrap_gap）。names：名字 → 项。返回 ([(项, 数的文字)], [没归上的数 (文字, x, y)])。"""
        found, spots, rest = [], [], []
        for text, _, cx, cy in texts:
            head = next((n for n in names if text.startswith(n) and text != n), None)
            if head is not None and self._number(text[len(head):]):
                found.append((names[head], text[len(head):]))
            elif text in names:
                spots.append((names[text], cx, cy))
            elif self._number(text):
                rest.append((text, cx, cy))
        unsure = []
        for text, cx, cy in rest:
            near = [(cx - lx, field) for field, lx, ly in spots if lx < cx and abs(cy - ly) <= self.cfg["row_gap"]]
            if near:
                found.append((min(near)[1], text))
            else:
                unsure.append((text, cx, cy))
        gap, left = self.cfg["wrap_gap"], []
        for text, cx, cy in unsure:
            done = {field for field, _ in found}
            above = [(cy - ly, field) for field, lx, ly in spots
                     if field not in done and 0 < cy - ly <= gap and abs(cx - lx) <= 2 * gap]
            if above:
                found.append((min(above)[1], text))
            else:
                left.append((text, cx, cy))
        return found, left

    def read(self, frame, panel="season", memo=None):
        """读一栏（"season" 右边选中的赛季，"career" 中间的生涯合计）。返回字典：
        values（{项: 值}，没读到的不在里面）、approx（{项: (下限, 上限)}，游戏只写了约数的）、
        obscured（被弹出的说明框挡住了，这一张不能用）。"""
        cfg = self.cfg
        dx = cfg["panels"][panel]
        values, approx = {}, {}

        def take(field, text):
            value, rough = parse_number(text, integer=field not in DECIMALS)
            if value is None:
                return
            values[field] = value
            if rough:
                approx[field] = rough
            else:
                approx.pop(field, None)

        blocks = {name: self._read_block(frame, name, dx, memo) for name in ("top", "damage", "kills", "rows")}
        obscured = any(mark in t for texts in blocks.values() for t, *_ in texts for mark in cfg["obscured"])

        columns = cfg["top_columns"]
        width = cfg["blocks"]["top"][2] / len(columns)
        for text, _, cx, _ in blocks["top"]:
            number = self._number(text)
            if number:
                take(columns[min(len(columns) - 1, int(cx // width))], number)

        for name, spec in cfg["circles"].items():
            found, rest = self._by_line(blocks[name], spec["lines"])
            for field, text in found:
                take(field, text)
            if rest:  # 最上面那个单独的大数（下面两行前面都带字）
                take(spec["big"], min(rest, key=lambda n: n[2])[0])

        found, _ = self._by_line(blocks["rows"], cfg["rows"])
        for field, text in found:
            take(field, text)
        return {"panel": panel, "values": values, "approx": approx, "obscured": obscured}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="读游戏里数据页的两栏（选中的赛季、生涯合计）；或者生成参照图")
    parser.add_argument("images", type=Path, nargs="+", help="截图")
    parser.add_argument("--lang", choices=game_lang.languages(), default=game_lang.DEFAULT,
                        help="游戏语言（默认简体中文 zh-CN）")
    parser.add_argument("--save-template", action="store_true", help="从第一张截图生成\"数据页\"的参照图（两个角各一张）")
    parser.add_argument("--aspect", choices=tuple(ASPECTS), help="游戏画面的比例（不写 = 和截图一样）")
    args = parser.parse_args()
    if args.aspect:
        set_game_aspect(ASPECTS[args.aspect])
    cfg = load_config(CONFIG_NAME, args.lang)
    if args.save_template:
        for name in cfg["detect"]["screens"]:
            print(f"已生成参照图：{save_template(name, args.images[0], {**cfg['detect'], 'language': args.lang})}")
        return

    from ocr import SMALL_CROP_PARAMS, create_engine
    page = SeasonPage(create_engine(SMALL_CROP_PARAMS), cfg, args.lang)
    for path in args.images:
        frame = load_image(path)
        if not page.on_page(frame):
            print(f"{path.name}：不是数据页（或者下拉菜单开着）")
            continue
        _, _, _, raw = page.label(frame)
        print(f"{path.name}：{page.name(frame) or '名字没读到'}，{raw or '赛季没读到'}")
        for panel in ("season", "career"):
            reading = page.read(frame, panel)
            values, approx, problems = check(reading["values"], reading["approx"])
            print(f"  {'赛季' if panel == 'season' else '生涯'}：{show(values, approx)}"
                  + ("（被弹出的说明框挡住了）" if reading["obscured"] else ""))
            for p in problems:
                print(f"    ⚠ {p}")


if __name__ == "__main__":
    main()
