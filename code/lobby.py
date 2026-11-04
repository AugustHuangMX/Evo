"""大厅：读自己的玩家名、段位和段内分数（规则在 config/lobby.json），换算成总分。总分以大厅为准（2026-10-18 用户决定）。

- 段位那一栏整块读一遍，按格式找出段位那一行（"钻石II"）和段内分数那一行（"868/1000 排位点数"）：
  组队、有活动文字时这一栏会上下移动，按格式找就不怕
- 段（I–IV）以段位徽章下面的罗马数字为准：段位那一行的文字常把"钻石 II"读成"钻石I""钻石ⅡI"
- 段内分数那一行的斜杠在 4:3 压扁的画面上常读错（"6817750 RP""436/750.RP"，2026-11-04 测试员的截图）：
  格式对不上时用这一段的满分和右边"▶ 69 RP"（还差多少）拆开（split_progress）
- 玩家名：左上角组队列表第一行（自己）
- 进大厅后每张都读，连续两次读到一样的段位和分数就定下来（总分短时间内不会变），记进 lobby_readings 表，
  然后用它校准前后两次大厅之间各局的 RP（见 calibration.py），这次停在大厅就不再读
- 打完一局通常不回大厅，所以每次回大厅都要抓住

检查截图：python code/lobby.py 截图1.png [截图2.png ...]
"""

import argparse
import re
import sys
from pathlib import Path

import calibration
import game_lang
import ranks
import storage
from hud import Snip, read_name
from ocr import crop, load_config, load_image

CONFIG_NAME = "lobby.json"
ROMAN = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "1": "I", "l": "I", "|": "I", "Ⅴ": "V"}


def roman(text):
    """"ⅡI"、"II"、"Ⅱ" → 罗马数字；不是 I–IV 返回 None。"""
    text = "".join(ROMAN.get(c, c) for c in text.replace(" ", ""))
    return text if text in ranks.DIVISIONS else None


def parse_tier(text, names=None):
    """以段位名开头的一行（"钻石II"、"大师"）→ (段位, 文字里的段)；不是这样的一行返回 None。
    names：画面上的段位名 → 简体段位名（ranks.tier_names，别的语言如 "Diamond"）；不给就是简体。"""
    names = names or ranks.tier_names()
    text = text.replace(" ", "")
    for shown in sorted(names, key=len, reverse=True):
        key = shown.replace(" ", "")
        if text.lower().startswith(key.lower()):
            return names[shown], roman(text[len(key):])
    return None


SUFFIX = r"[^\dA-Za-z]*(?:RP|排位点数)"  # 数字后面的"RP"，中间可能多读出一个点
BODY = re.compile(rf"(\d[\d/\\|lI.]*?){SUFFIX}")  # "436/750"这一串：斜杠可能读成 7、1、l、I、|、点


def split_progress(lines, size, remaining=()):
    """段内分数那一行的斜杠读错了时（4:3 压扁的画面常见："436/750.RP""6817750 RP"），用这一段的满分拆开。
    lines：段位那一栏读到的每一行（去掉空格）；size：这一段满分（白银 750，ranks.division_size）；
    remaining：右边"▶ 314 RP"（离下一段还差多少）读到的数，有的话用来核对：分数 + 还差多少 = 满分。
    拆不出、拆出两种又没法核对时返回 None（宁可读不到，也不要读错）。"""
    if not size:
        return None
    tail = str(size)
    for line in lines:
        m = BODY.fullmatch(line)
        if not m or not m[1].endswith(tail) or len(m[1]) <= len(tail):
            continue
        head = m[1][:-len(tail)]
        # 斜杠没读出来（"436750"），或者读成了一个别的字（"6817750"的 7）
        values = {int(h) for h in (head, head[:-1]) if h.isdigit() and int(h) < size}
        checked = {v for v in values for r in remaining if v + r == size}
        if checked:
            values = checked
        if len(values) == 1:
            return values.pop()
    return None


class LobbyReader:
    def __init__(self, engine, cfg=None):
        self.engine = engine
        self.cfg = cfg or load_config(CONFIG_NAME)

    def _lines(self, frame, spec):
        result = self.engine(crop(frame, spec["roi"], {**self.cfg, "upscale": spec["upscale"]}, spec.get("anchor")))
        return list(zip(result.txts or (), result.scores or ()))

    @staticmethod
    def _spots(spec):
        """主区域，再加上 also 里的几处（只写和主区域不一样的项，如第 30 赛季的位置）：前面的读不到才读后面的。"""
        return [spec, *({**spec, **other} for other in spec.get("also", []))]

    def read(self, frame):
        """读一张大厅截图。返回字典：name、tier、division、progress、score（读不出的为 None），
        以及 text_division、emblem（文字和徽章各自读到的段，写日志用）。"""
        c = self.cfg
        out = {"name": None, "tier": None, "division": None, "progress": None, "score": None,
               "text_division": None, "emblem": None}
        panel, patterns = c["panel"], c["patterns"]
        names = ranks.tier_names(c.get("language"))
        lines = []  # 这一栏读到的每一行（去掉空格）：段内分数那一行格式对不上时再拆一遍，见下面
        wholes = []  # 只有一个分数的那几行（"16523 排位点数""▶ 62 RP"），见下面
        for text, score in self._lines(frame, panel):
            if score < panel["min_score"]:
                continue
            tier = parse_tier(text, names)
            if tier and out["tier"] is None:
                out["tier"] = tier[0]
                out["text_division"] = tier[1] if score >= c["text_division_score"] else None
            compact = text.replace(" ", "")
            lines.append(compact)
            m = re.search(patterns["progress"], compact)
            if m and out["progress"] is None:
                out["progress"] = int(m[1])
            m = re.match(patterns["whole_progress"], compact)
            if m:
                wholes.append(int(m[1]))
        if out["progress"] is None and out["tier"] is not None and not ranks.has_divisions(out["tier"]):
            # 大师、猎杀者不分段，可能只写一个分数。分段的段位不用它：第 31 赛季起"688/750 RP"右边还有
            # "▶ 62 RP"（离下一段还差多少），"▶"没读出来时就是"62RP"
            out["progress"] = wholes[0] if wholes else None
        for emblem in self._spots(c["emblem"]):
            out["emblem"] = next((roman(text) for text, score in self._lines(frame, emblem)
                                  if score >= emblem["min_score"] and roman(text)), None)
            if out["emblem"]:
                break
        if out["tier"] is not None:
            if ranks.has_divisions(out["tier"]):
                out["division"] = out["emblem"] or out["text_division"]
                if out["progress"] is None:  # 斜杠读错了（4:3 常见）：用这一段的满分和右边"还差多少"拆开
                    out["progress"] = split_progress(lines, ranks.division_size(out["tier"], out["division"]),
                                                     wholes)
            out["score"] = ranks.score(out["tier"], out["division"], out["progress"])
        for n in self._spots(c["name"]):
            out["name"] = read_name(self.engine, Snip(frame, n["roi"], c["base_resolution"], n.get("anchor")),
                                    n["left"], n["min_score"])
            if out["name"]:
                break
        return out


def describe(r):
    rank = ranks.rank_name(r["tier"], r["division"]) if r["tier"] else "段位没读到"
    return f"{r['name'] or '名字没读到'}，{rank} {'-' if r['progress'] is None else r['progress']}" \
           f"（总分 {'-' if r['score'] is None else r['score']}）"


class LobbyWatcher:
    """在记录程序里每张截图调用一次 update(画面, 截图, 时间)。"""

    def __init__(self, reader, post, conn, log, max_reads=None):
        self.reader, self.post, self.conn, self.log = reader, post, conn, log
        self.max_reads = max_reads or reader.cfg["max_reads"]
        self.reads = 0  # 一共读了几次（统计用）
        self.saved = 0  # 记下了几次大厅读数（统计用）
        self._leave()

    def _leave(self):
        self.last, self.tries, self.done, self.blank = None, 0, False, 0

    def update(self, screen, frame, now):
        if screen != "lobby":
            if self.last is not None or self.tries:
                self._leave()
            return
        if self.done:
            return
        r = self.reader.read(frame)
        self.reads += 1
        self.tries += 1
        # 选的不是排位（英文按左下角的模式框认大厅，匹配时也认得出）：段位那一栏不在，连续几次什么都没读到就不读了
        self.blank = self.blank + 1 if r["tier"] is None and r["progress"] is None else 0
        give_up = self.reader.cfg.get("give_up_blank_reads")
        if give_up and self.blank >= give_up:
            self.done = True
            self.log(f"[{now:%H:%M:%S}] 大厅：左下角没有段位（选的不是排位），这次不读")
            return
        key = (r["tier"], r["division"], r["progress"]) if r["score"] is not None else None
        if key is not None and self.last is not None and key == self.last[0]:
            self.done = True
            self._save(r if r["name"] else {**r, "name": self.last[1]}, now)
            return
        self.last = (key, r["name"]) if key is not None else None
        if self.tries >= self.max_reads:
            self.done = True
            self.log(f"[{now:%H:%M:%S}] 大厅：读了 {self.tries} 次，段位和分数没有连续两次一样（最后一次：{describe(r)}），"
                     "这次不记，等下次回大厅")

    def _save(self, r, now):
        if r["name"]:
            self.post.on_player_name(r["name"], now, "大厅")
        reading = {"read_at": f"{now:%Y-%m-%d %H:%M:%S}", "player_name": self.post.player_name or r["name"],
                   "rank_tier": r["tier"], "rank_division": r["division"], "rank_progress": r["progress"],
                   "score": r["score"]}
        storage.save_lobby(self.conn, reading)
        self.saved += 1
        note = f"（段位文字读成 {r['text_division']}，以徽章的 {r['emblem']} 为准）" \
            if r["emblem"] and r["text_division"] and r["emblem"] != r["text_division"] else ""
        self.log(f"[{now:%H:%M:%S}] 大厅：{describe({**r, 'name': reading['player_name']})}{note}")
        calibration.calibrate(self.conn, reading, self.log)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="读大厅截图上的玩家名、段位和段内分数")
    parser.add_argument("images", type=Path, nargs="+", help="大厅截图")
    parser.add_argument("--lang", choices=game_lang.languages(), default=game_lang.DEFAULT,
                        help="游戏语言（默认简体中文 zh-CN）")
    args = parser.parse_args()

    from ocr import SMALL_CROP_PARAMS, create_engine
    reader = LobbyReader(create_engine(SMALL_CROP_PARAMS), load_config(CONFIG_NAME, args.lang))
    for path in args.images:
        r = reader.read(load_image(path))
        extra = f"（徽章 {r['emblem'] or '-'}，文字 {r['text_division'] or '-'}）" if r["tier"] else ""
        print(f"{path.parent.name}/{path.name}：{describe(r)}{extra}")


if __name__ == "__main__":
    main()
