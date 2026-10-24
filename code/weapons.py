"""对局中右下角手上那把武器（规则在 config/weapons.json，参照图在 config/templates/weapons/）：数据页"最常用武器"用。

只比外观、不用识别引擎（交火时不用引擎）：
- icon_mask：右下角手上那把的大图标那块，缩放成基准尺寸，挑出白色的像素（图标是白色的，武器栏的底色是各种弹药颜色）
- WeaponDetector.identify：每把武器的参照图（白色像素，裁到边）在这块里滑动，找最像的位置；最像的够像、又比第二像的
  （另一把武器）高出一截才算认出。参照图是滑着比的，所以这块里多出来的东西（上面的提示文字、下面的弹药小图标）不影响
- 一把武器可以有几张参照图（<编号>.png、<编号>-2.png……）：比如莫桑比克、P2020 单拿和双持的样子不一样
- WeaponTimer：一局里每一段拿在手上的武器。图标没变就不重新认（大多数截图不用比，每张只多花约 0.01 毫秒）；
  认不出的（打药、拿手雷、空手、没见过的东西）不计时；同一把武器隔太久没看到（开地图、观战）算两段

参照图怎么来：python tools/weapon_templates.py 靶场截图文件夹（见那里的说明）
检查截图：python code/weapons.py 截图1.png [截图2.png ...]   （每张打印认出的武器和相似度）
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from layout import cut
from ocr import load_config, load_image
from paths import CONFIG_DIR

CONFIG_NAME = "weapons.json"
TEMPLATE_DIR = CONFIG_DIR / "templates" / "weapons"


def template_key(path):
    """参照图文件名 → 武器编号（"mozambique-2.png" → "mozambique"）。"""
    return Path(path).stem.split("-")[0]


def icon_mask(frame, cfg):
    """右下角手上那把的大图标那块：缩放成基准尺寸，白色的像素为 255，其余为 0。"""
    box = cfg["icon_box"]
    w, h = box[2], box[3]
    piece = cut(frame, box, cfg["base_resolution"], cfg.get("anchor"))
    if piece.size == 0:
        return np.zeros((h, w), np.uint8)
    piece = cv2.resize(piece, (w, h), interpolation=cv2.INTER_AREA).astype(np.int16)
    lo, hi = piece.min(axis=2), piece.max(axis=2)
    mask = (((lo >= cfg["white_min"]) & (hi - lo < cfg["white_spread"])) * 255).astype(np.uint8)
    for x, y, bw, bh in cfg.get("ignore_boxes", []):  # 配件格、弹药小图标：挖掉不看
        x0, y0 = max(0, x - box[0]), max(0, y - box[1])
        mask[y0:y0 + bh, x0:x0 + bw] = 0
    return mask


class WeaponDetector:
    def __init__(self, cfg=None, folder=TEMPLATE_DIR):
        self.cfg = cfg or load_config(CONFIG_NAME)
        self.weapons = self.cfg["weapons"]
        self.templates = []  # [(编号, 模糊过的参照图)]：名单里没有的编号不用
        for path in sorted(Path(folder).glob("*.png")):
            key = template_key(path)
            img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if key in self.weapons and img is not None:
                self.templates.append((key, self._blur(img)))

    def _blur(self, mask):
        """比之前轻轻模糊一下：拉伸过的画面、JPG 截图里图标常常只剩轮廓，模糊以后实心的参照图也对得上。"""
        k = self.cfg.get("blur", 0)
        return cv2.GaussianBlur(mask, (k, k), 0) if k else mask

    def name(self, key):
        """面板上显示的名字（名单里没有就显示编号）。"""
        return self.weapons.get(key, {}).get("zh", key)

    def mask(self, frame):
        return icon_mask(frame, self.cfg)

    def scores(self, mask):
        """每把武器最像的相似度：{编号: 相似度}（一把武器有几张参照图时取最高的）。"""
        out = {}
        mask = self._blur(mask)
        for key, template in self.templates:
            if template.shape[0] > mask.shape[0] or template.shape[1] > mask.shape[1]:
                continue
            result = np.nan_to_num(cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED), nan=0.0, posinf=0.0, neginf=0.0)
            out[key] = max(out.get(key, -1.0), float(result.max()))
        return out

    def identify(self, mask):
        """手上那把武器：(编号, 相似度)；认不出返回 (None, 最高的相似度)。"""
        if int(np.count_nonzero(mask)) < self.cfg["min_pixels"]:
            return None, 0.0  # 没拿武器
        ranked = sorted(self.scores(mask).items(), key=lambda kv: -kv[1])
        if not ranked:
            return None, 0.0
        key, score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        if score >= self.cfg["min_similarity"] and score - second >= self.cfg["min_margin"]:
            return key, score
        return None, score

    def changed(self, a, b):
        """两张图标（白色像素）是不是不一样：一样就不用重新认。"""
        if a is None or b is None or a.shape != b.shape:
            return True
        return float(np.mean(cv2.absdiff(a, b))) >= self.cfg["change"]


class WeaponTimer:
    """一局里每一段拿在手上的武器。每张自己的界面（左下角认得出自己）调用一次 update(截图, 时间)。"""

    def __init__(self, detector):
        self.detector = detector
        self.gap = detector.cfg["gap_seconds"]
        self.reset()

    def reset(self):
        self.holds = []  # [[编号, 开始, 结束]]（时间）
        self.last_mask = None  # 上一次认的那张图标
        self.current = None  # 上一次认出的武器（认不出为 None）
        self.identified = 0  # 这一局认了几次（统计用）

    def update(self, frame, now):
        mask = self.detector.mask(frame)
        if self.detector.changed(mask, self.last_mask):
            self.current, _ = self.detector.identify(mask)
            self.last_mask = mask
            self.identified += 1
        if self.current is None:
            return
        last = self.holds[-1] if self.holds else None
        if last is not None and last[0] == self.current and (now - last[2]).total_seconds() <= self.gap:
            last[2] = now
        else:
            self.holds.append([self.current, now, now])

    def result(self):
        """[(编号, 开始秒, 结束秒)]，从这一局第一次拿到武器算起；一把都没认出返回 []。"""
        if not self.holds:
            return []
        t0 = self.holds[0][1]
        return [(key, round((a - t0).total_seconds(), 1), round((b - t0).total_seconds(), 1)) for key, a, b in self.holds]


def main():
    parser = argparse.ArgumentParser(description="认截图右下角手上那把武器")
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()
    detector = WeaponDetector()
    print(f"参照图 {len(detector.templates)} 张（{len({k for k, _ in detector.templates})} 把武器）")
    for path in args.images:
        mask = detector.mask(load_image(path))
        key, score = detector.identify(mask)
        print(f"{path.name}：{detector.name(key) if key else '认不出'}（相似度 {score:.2f}，白色像素 {np.count_nonzero(mask)}）")


if __name__ == "__main__":
    sys.exit(main())
