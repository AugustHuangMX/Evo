"""认出自己用的传奇：对局中左下角自己的头像（规则和传奇名称表在 config/legends.json）。

头像是图，不是文字，所以不用识别引擎：把左下角那一小块和每个传奇的参照图（config/templates/legends/<编号>.png，
头像正中间的脸）比轮廓（和 screens.py 的外观检查一样，只比边缘，不管明暗），最像的那个足够像、并且明显比第二像的
更像，才算认出。不加载识别模型，28 个传奇比一遍只要几毫秒。

注意：观战时左下角是被观战的人，不是自己，阵亡后不要在这里读。
队友（左下角自己上方的两行）的头像是同一套美术图，只是小一些：用 scaled() 缩小参照图后比对（见 hud.py）。

检查截图：  python code/legends.py 截图1.png [截图2.png ...]   （打印最像的 3 个传奇、相似度和结论）
生成参照图：python code/legends.py --save-template <传奇编号> 截图.png   （如 caustic；按配置里的 face_box 截脸）
"""

import argparse
import sys
import time
from pathlib import Path

import cv2

from ocr import load_config, load_image
from screens import TEMPLATE_DIR, gray_crop, load_template, match, outline

LEGEND_TEMPLATE_DIR = TEMPLATE_DIR / "legends"


class LegendDetector:
    """identify(画面) 返回认出的传奇编号（如 "caustic"），认不出返回 None。name(编号) 返回中文名。"""

    def __init__(self, cfg=None):
        self.cfg = cfg or load_config("legends.json")
        self.templates = {}  # 编号 → 参照图的轮廓
        self.raw = {}  # 编号 → 参照图（灰度）：认队友的小头像时要先缩小
        for key in self.cfg["legends"]:
            template = load_template(f"legends/{key}")
            if template is not None:
                self.raw[key] = template
                self.templates[key] = outline(template)
        self.missing = [key for key in self.cfg["legends"] if key not in self.templates]  # 还没有参照图的传奇

    def scaled(self, scale):
        """所有参照图缩小到 scale 倍后的轮廓（认队友的小头像用）。"""
        return {key: outline(cv2.resize(t, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
                for key, t in self.raw.items()}

    def scores(self, frame, box=None, templates=None):
        """每个传奇的相似度（-1 到 1，越大越像），从高到低排好 [(编号, 相似度), ...]。
        box：在哪一块里找，默认是配置里的 search_box（自己的头像）；templates：用哪套参照图轮廓，默认原大小（队友用 scaled()）。"""
        region = outline(gray_crop(frame, box or self.cfg["search_box"], self.cfg["base_resolution"], self.cfg.get("anchor")))
        result = []
        for key, template in (templates or self.templates).items():
            if template.shape[0] > region.shape[0] or template.shape[1] > region.shape[1]:
                continue
            result.append((key, float(match(region, template).max())))  # 纯色的区域算不出相似度，当作不像（见 screens.match）
        return sorted(result, key=lambda pair: pair[1], reverse=True)

    def decide(self, ranked, min_similarity=None, min_margin=None):
        """按排好的相似度下结论：最像的够像、并且比第二像的高出足够多，返回它的编号；否则 None。"""
        if not ranked:
            return None
        best, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        low = self.cfg["min_similarity"] if min_similarity is None else min_similarity
        margin = self.cfg["min_margin"] if min_margin is None else min_margin
        if top >= low and top - second >= margin:
            return best
        return None

    def identify(self, frame, box=None, templates=None, min_similarity=None, min_margin=None):
        return self.decide(self.scores(frame, box, templates), min_similarity, min_margin)

    def name(self, key):
        return self.cfg["legends"][key]["zh"]


def save_template(key, image_path, cfg):
    """按配置里的 face_box，从一张截图上截出这个传奇的脸，存到 config/templates/legends/<编号>.png。"""
    if key not in cfg["legends"]:
        raise SystemExit(f"config/legends.json 里没有传奇编号 {key}，先在 legends 里加一行")
    gray = gray_crop(load_image(image_path), cfg["face_box"], cfg["base_resolution"], cfg.get("anchor"))
    LEGEND_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    path = LEGEND_TEMPLATE_DIR / f"{key}.png"
    cv2.imencode(".png", gray)[1].tofile(path)
    return path


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="认出截图里自己用的传奇；或者从截图生成某个传奇的参照图")
    parser.add_argument("images", type=Path, nargs="+", help="截图（对局中、左下角有自己的头像）")
    parser.add_argument("--save-template", metavar="传奇编号", help="从这张截图生成这个传奇的参照图（如 caustic）")
    args = parser.parse_args()

    cfg = load_config("legends.json")
    if args.save_template:
        path = save_template(args.save_template, args.images[0], cfg)
        print(f"已生成参照图：{path}")
        return

    detector = LegendDetector(cfg)
    if detector.missing:
        print(f"⚠ 这些传奇还没有参照图，认不出来：{'、'.join(detector.name(k) for k in detector.missing)}")
    for path in args.images:
        img = load_image(path)
        start = time.perf_counter()
        ranked = detector.scores(img)
        ms = (time.perf_counter() - start) * 1000
        result = detector.decide(ranked)
        top = "  ".join(f"{detector.name(k)} {s:.2f}" for k, s in ranked[:3])
        print(f"{path.name:40s} 最像：{top}（{ms:.1f} ms）→ {detector.name(result) if result else '认不出'}")


if __name__ == "__main__":
    main()
