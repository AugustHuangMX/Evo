"""判断当前是哪个画面：总结页、经验值页、开局画面、大厅、淘汰画面、冠军横幅、返回大厅按钮（规则在 config/screens.json），都不是返回 None。

分两步，先便宜后贵：
1. 外观检查（几毫秒，不用识别模型）：检查区域里有没有和参照图（config/templates/）一样的文字轮廓。
   只比笔画的边缘，不管背景亮暗；参照图在检查区域里上下左右滑动，取最像的位置（关键字前面的数字、模式名
   长短不同，位置会左右移动）。字会慢慢变大的画面（淘汰画面），参照图按配置里的几种大小各比一次。
   打游戏的大部分时间画面都不是这几种，这一步就排除了，不用跑识别引擎
2. 外观像了，再用识别引擎读这一块，关键字都在才算。所以多了第 1 步也不会认错，只会少跑识别

游戏更新后界面变了，参照图可能就不像了：重新截一张，用下面的命令重新生成。没有参照图的画面每次都直接跑识别（和以前一样）。

检查截图：      python code/screens.py 截图1.png [截图2.png ...]   （打印每种画面的外观相似度和最后的结论）
重新生成参照图：python code/screens.py --save-template summary 截图.png   （按配置里的 template_box 截一块）
"""

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from ocr import SMALL_CROP_PARAMS, create_engine, crop, load_config, load_image, read_lines
from paths import CONFIG_DIR

TEMPLATE_DIR = CONFIG_DIR / "templates"


def gray_crop(img, box, base, anchor=None):
    """按基准坐标裁一块，缩放成基准分辨率下的大小，转成灰度。anchor：这块界面贴哪条边（见 layout.py）。"""
    return cv2.cvtColor(crop(img, box, {"base_resolution": base, "upscale": 1}, anchor), cv2.COLOR_BGR2GRAY)


def outline(gray):
    """文字轮廓：减去模糊后的自己，去掉背景的明暗变化，只留下笔画的边缘。"""
    g = gray.astype(np.float32)
    return g - cv2.GaussianBlur(g, (0, 0), 3)


def load_template(name):
    """读一种画面的参照图（灰度），没有就返回 None。"""
    path = TEMPLATE_DIR / f"{name}.png"
    if not path.is_file():
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def shrunk(gray, factor):
    """缩小到 factor 倍（1 = 不变）。"""
    return gray if factor == 1 else cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)


def template_outlines(template, spec):
    """一种画面的参照图轮廓：按配置里的 scales（默认只有原大小）各缩放一份，再一起缩小 shrink 倍（默认不缩小）。"""
    shrink = spec.get("shrink", 1)
    return [outline(shrunk(template, scale * shrink)) for scale in spec.get("scales", [1])]


class ScreenDetector:
    """detect(画面) 返回画面的名字（如 "summary"），都不是返回 None。
    按配置里的顺序检查，先认出的为准。engine：识别引擎（可以是 ocr.LazyEngine，用到时才加载模型）。"""

    def __init__(self, engine, cfg=None):
        self.engine = engine
        self.cfg = cfg or load_config("screens.json")
        self.templates = {}  # 每种画面的参照图轮廓（几种大小各一份），没有参照图为 None
        for name, spec in self.cfg["screens"].items():
            template = load_template(name)
            self.templates[name] = None if template is None else template_outlines(template, spec)
        self.missing = [name for name, t in self.templates.items() if t is None]  # 没有参照图的画面
        self.looked_alike = 0  # 外观检查通过、跑了识别的次数（统计用）

    def similarity(self, frame, name):
        """检查区域和参照图的外观相似度（-1 到 1，越大越像；几种大小取最像的）。没有参照图返回 None。"""
        templates = self.templates[name]
        if templates is None:
            return None
        spec = self.cfg["screens"][name]
        gray = gray_crop(frame, spec["roi"], self.cfg["base_resolution"], spec.get("anchor"))
        region = outline(shrunk(gray, spec.get("shrink", 1)))
        # 纯色的区域（如黑屏）算不出相似度，当作不像
        return max(float(np.nan_to_num(cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED), nan=-1.0).max())
                   for t in templates)

    def looks_like(self, frame, name):
        """第 1 步：外观像不像。没有参照图的画面一律算像（直接交给识别）。"""
        score = self.similarity(frame, name)
        spec = self.cfg["screens"][name]
        return score is None or score >= spec.get("min_similarity", self.cfg["min_similarity"])

    def has_keywords(self, frame, name):
        """第 2 步：用识别引擎读检查区域，关键字是不是全都在。"""
        spec = self.cfg["screens"][name]
        text = "".join(t for t, _ in read_lines(self.engine, frame, spec["roi"], {**self.cfg, **spec}))
        return all(k in text for k in spec["keywords"])

    def detect(self, frame):
        for name in self.cfg["screens"]:
            if self.looks_like(frame, name):
                self.looked_alike += 1
                if self.has_keywords(frame, name):
                    return name
        return None


def save_template(name, image_path, cfg):
    """按配置里的 template_box，从一张截图上截出参照图，存到 config/templates/<name>.png。"""
    spec = cfg["screens"][name]
    gray = gray_crop(load_image(image_path), spec["template_box"], cfg["base_resolution"], spec.get("anchor"))
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMPLATE_DIR / f"{name}.png"
    cv2.imencode(".png", gray)[1].tofile(path)
    return path


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="判断截图是哪个画面；或者从截图生成参照图")
    parser.add_argument("images", type=Path, nargs="+", help="截图")
    parser.add_argument("--save-template", metavar="画面", help="从这张截图生成这种画面的参照图（如 summary）")
    args = parser.parse_args()

    cfg = load_config("screens.json")
    if args.save_template:
        path = save_template(args.save_template, args.images[0], cfg)
        print(f"已生成参照图：{path}")
        return

    detector = ScreenDetector(create_engine(SMALL_CROP_PARAMS), cfg)
    if detector.missing:
        print(f"⚠ 这些画面还没有参照图，每次都会直接跑识别：{'、'.join(detector.missing)}")
    for path in args.images:
        img = load_image(path)
        start = time.perf_counter()
        scores = {name: detector.similarity(img, name) for name in cfg["screens"]}
        ms = (time.perf_counter() - start) * 1000
        result = detector.detect(img)
        text = "  ".join(f"{name} {'-' if s is None else f'{s:.2f}'}" for name, s in scores.items())
        print(f"{path.name:40s} 外观相似度：{text}（{ms:.1f} ms）→ {result or '都不是'}")


if __name__ == "__main__":
    main()
