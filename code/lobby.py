"""大厅：读自己的玩家名、段位和段内分数（规则在 config/lobby.json），换算成总分。总分以大厅为准（2026-10-18 用户决定）。

- 段位那一栏整块读一遍，按格式找出段位那一行（"钻石II"）和段内分数那一行（"868/1000 排位点数"）：
  组队、有活动文字时这一栏会上下移动，按格式找就不怕
- 段（I–IV）以段位徽章下面的罗马数字为准：段位那一行的文字常把"钻石 II"读成"钻石I""钻石ⅡI"
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
import ranks
import storage
from hud import Snip, read_name
from ocr import crop, load_config, load_image

CONFIG_NAME = "lobby.json"
ROMAN = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "1": "I", "l": "I", "|": "I", "Ⅴ": "V"}
PROGRESS = re.compile(r"(\d{1,5})\s*/\s*\d{2,5}\s*排位点数")  # "868/1000 排位点数"（"前5名排位点数：1571"没有斜杠，不算）
WHOLE_PROGRESS = re.compile(r"^(\d{1,6})\s*排位点数$")  # 大师、猎杀者不分段，可能只写"16523 排位点数"（还没见过样本）


def roman(text):
    """"ⅡI"、"II"、"Ⅱ" → 罗马数字；不是 I–IV 返回 None。"""
    text = "".join(ROMAN.get(c, c) for c in text.replace(" ", ""))
    return text if text in ranks.DIVISIONS else None


def parse_tier(text):
    """以段位名开头的一行（"钻石II"、"大师"）→ (段位, 文字里的段)；不是这样的一行返回 None。"""
    text = text.replace(" ", "")
    for tier in sorted(ranks.TIERS, key=len, reverse=True):
        key = tier.replace(" ", "")
        if text.lower().startswith(key.lower()):
            return tier, roman(text[len(key):])
    return None


class LobbyReader:
    def __init__(self, engine, cfg=None):
        self.engine = engine
        self.cfg = cfg or load_config(CONFIG_NAME)

    def _lines(self, frame, roi, upscale):
        result = self.engine(crop(frame, roi, {**self.cfg, "upscale": upscale}))
        return list(zip(result.txts or (), result.scores or ()))

    def read(self, frame):
        """读一张大厅截图。返回字典：name、tier、division、progress、score（读不出的为 None），
        以及 text_division、emblem（文字和徽章各自读到的段，写日志用）。"""
        c = self.cfg
        out = {"name": None, "tier": None, "division": None, "progress": None, "score": None,
               "text_division": None, "emblem": None}
        panel = c["panel"]
        for text, score in self._lines(frame, panel["roi"], panel["upscale"]):
            if score < panel["min_score"]:
                continue
            tier = parse_tier(text)
            if tier and out["tier"] is None:
                out["tier"] = tier[0]
                out["text_division"] = tier[1] if score >= c["text_division_score"] else None
            m = PROGRESS.search(text.replace(" ", "")) or WHOLE_PROGRESS.match(text.replace(" ", ""))
            if m and out["progress"] is None:
                out["progress"] = int(m[1])
        emblem = c["emblem"]
        for text, score in self._lines(frame, emblem["roi"], emblem["upscale"]):
            if score >= emblem["min_score"] and roman(text):
                out["emblem"] = roman(text)
                break
        if out["tier"] is not None:
            if ranks.has_divisions(out["tier"]):
                out["division"] = out["emblem"] or out["text_division"]
            out["score"] = ranks.score(out["tier"], out["division"], out["progress"])
        n = c["name"]
        out["name"] = read_name(self.engine, Snip(frame, n["roi"], c["base_resolution"]), n["left"], n["min_score"])
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
        self.last, self.tries, self.done = None, 0, False

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
    args = parser.parse_args()

    from ocr import SMALL_CROP_PARAMS, create_engine
    reader = LobbyReader(create_engine(SMALL_CROP_PARAMS))
    for path in args.images:
        r = reader.read(load_image(path))
        extra = f"（徽章 {r['emblem'] or '-'}，文字 {r['text_division'] or '-'}）" if r["tier"] else ""
        print(f"{path.parent.name}/{path.name}：{describe(r)}{extra}")


if __name__ == "__main__":
    main()
