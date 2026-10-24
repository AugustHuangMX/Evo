"""判断当前是哪个画面：总结页、经验值页、开局画面、大厅、淘汰画面、冠军横幅、返回大厅按钮（规则在 config/screens.json），都不是返回 None。

分两步，先便宜后贵：
1. 外观检查（几毫秒，不用识别模型）：检查区域里有没有和参照图（config/templates/）一样的文字轮廓。
   只比笔画的边缘，不管背景亮暗；参照图在检查区域里上下左右滑动，取最像的位置（关键字前面的数字、模式名
   长短不同，位置会左右移动）。字会慢慢变大的画面（淘汰画面），参照图按配置里的几种大小各比一次。
   打游戏的大部分时间画面都不是这几种，这一步就排除了，不用跑识别引擎
2. 外观像了，再用识别引擎读这一块，关键字都在才算。所以多了第 1 步也不会认错，只会少跑识别

游戏更新后界面变了，参照图可能就不像了：重新截一张，用下面的命令重新生成。没有参照图的画面每次都直接跑识别（和以前一样）。

游戏语言（见 game_lang.py）：ScreenDetector 只认一种语言；记录程序用 GameScreens，几种语言一起认，
认出一个画面、只有一种语言的关键字对得上时就认定是这种语言，之后只按它比。

检查截图：      python code/screens.py 截图1.png [截图2.png ...]   （打印每种画面的外观相似度和最后的结论）
重新生成参照图：python code/screens.py --save-template summary 截图.png   （按配置里的 template_box 截一块）
别的游戏语言：  两条命令都加上 --lang <语言>（如 --lang en；参照图存到 config/templates/<语言>/）
"""

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import game_lang
from ocr import SMALL_CROP_PARAMS, create_engine, crop, load_config, load_image, read_lines

TEMPLATE_DIR = game_lang.TEMPLATE_DIR


def gray_crop(img, box, base, anchor=None):
    """按基准坐标裁一块，缩放成基准分辨率下的大小，转成灰度。anchor：这块界面贴哪条边（见 layout.py）。"""
    return cv2.cvtColor(crop(img, box, {"base_resolution": base, "upscale": 1}, anchor), cv2.COLOR_BGR2GRAY)


def outline(gray):
    """文字轮廓：减去模糊后的自己，去掉背景的明暗变化，只留下笔画的边缘。"""
    g = gray.astype(np.float32)
    return g - cv2.GaussianBlur(g, (0, 0), 3)


def load_template(name, lang=None):
    """读一种画面的参照图（灰度），没有就返回 None。lang：游戏语言，这种语言没有自己的那张就用简体的（见 game_lang.py）。"""
    path = game_lang.template_path(f"{name}.png", lang)
    if not path.is_file():
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


FLAT = 1e-3  # 窗口里轮廓的方差小于这个（纯色、没有一点纹理），比出来的相似度不算数
LOW = 0.3  # 相似度比这个低时用不上（认画面、图标、传奇的门槛都在 0.35 以上），不用查平不平


def match(region, template):
    """参照图轮廓在 region（也是轮廓）里每个位置的相似度（TM_CCOEFF_NORMED，-1 到 1），用的人只看最大值。
    纯色、没有一点纹理的位置当作不像（-1）：OpenCV 在这种位置上会算出 0.9、1.0 这样的假高分
    （2026-10-24 繁体截图：桌面上录像软件黑色的播放器，淘汰画面的参照图比出 1.0）。
    最像的都不到 LOW（所有门槛都比它高）就不用管；够像时只查最像的那个位置平不平（几乎总是不平），
    平的话才把所有平的位置去掉。每帧要比一百多次，这样几乎不多花时间。"""
    result = np.nan_to_num(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED), copy=False, nan=-1.0)
    _, best, _, (x, y) = cv2.minMaxLoc(result)
    h, w = template.shape[:2]
    if best < LOW or region[y:y + h, x:x + w].var() >= FLAT:
        return result
    s, sq = cv2.integral2(region, sdepth=cv2.CV_64F, sqdepth=cv2.CV_64F)
    total = s[h:, w:] - s[:-h, w:] - s[h:, :-w] + s[:-h, :-w]
    squares = sq[h:, w:] - sq[:-h, w:] - sq[h:, :-w] + sq[:-h, :-w]
    variance = squares / (h * w) - (total / (h * w)) ** 2
    result[variance < FLAT] = -1.0
    return result


def compact(text):
    """比关键字前：去掉空格、换成小写。"""
    return text.replace(" ", "").lower()


def screen_of(cfg, name):
    """配置里这一项是哪个画面：有 "as" 的是同一个画面的另一种样子（如匹配局的开局横幅 "TRIOS"，当作开局画面），
    各有各的参照图和关键字，认出来以后一样处理。"""
    return cfg["screens"][name].get("as", name)


def shrunk(gray, factor):
    """缩小到 factor 倍（1 = 不变）。"""
    return gray if factor == 1 else cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)


def template_outlines(template, spec):
    """一种画面的参照图轮廓：按配置里的 scales（默认只有原大小）各缩放一份，再一起缩小 shrink 倍（默认不缩小）。"""
    shrink = spec.get("shrink", 1)
    return [outline(shrunk(template, scale * shrink)) for scale in spec.get("scales", [1])]


class ScreenDetector:
    """detect(画面) 返回画面的名字（如 "summary"），都不是返回 None。
    按配置里的顺序检查，先认出的为准。engine：识别引擎（可以是 ocr.LazyEngine，用到时才加载模型）。
    lang：游戏语言（见 game_lang.py），None 是简体。"""

    def __init__(self, engine, cfg=None, lang=None):
        self.engine = engine
        self.lang = lang or game_lang.DEFAULT
        self.cfg = cfg or load_config("screens.json", lang)
        self.templates = {}  # 每种画面的参照图轮廓（几种大小各一份），没有参照图为 None
        for name, spec in self.cfg["screens"].items():
            template = load_template(name, lang)
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
        # 纯色的区域（如黑屏）算不出相似度，当作不像（见 match）
        return max(float(match(region, t).max()) for t in templates)

    def looks_like(self, frame, name):
        """第 1 步：外观像不像。没有参照图的画面一律算像（直接交给识别）。"""
        score = self.similarity(frame, name)
        spec = self.cfg["screens"][name]
        return score is None or score >= spec.get("min_similarity", self.cfg["min_similarity"])

    def has_keywords(self, frame, name):
        """第 2 步：用识别引擎读检查区域，关键字是不是全都在（不管空格和大小写：英文常读成"SQUADELIMINATED"）。"""
        spec = self.cfg["screens"][name]
        text = compact("".join(t for t, _ in read_lines(self.engine, frame, spec["roi"], {**self.cfg, **spec})))
        return all(compact(k) in text for k in spec["keywords"])

    def check(self, frame, name):
        """这一帧是不是这个画面：外观像了，再读关键字。"""
        if not self.looks_like(frame, name):
            return False
        self.looked_alike += 1
        return self.has_keywords(frame, name)

    def detect(self, frame):
        for name in self.cfg["screens"]:
            if self.check(frame, name):
                return screen_of(self.cfg, name)
        return None


class GameScreens:
    """几种游戏语言一起认（记录程序用；有哪几种见 game_lang.languages）。用法和 ScreenDetector 一样：detect(画面)。
    - 还没认定游戏语言时，每种画面按每种语言各比一次。认出一个画面、而且只有一种语言的关键字对得上，
      就认定是这种语言：游戏开着时换不了语言，之后只按它比，不多花 CPU
    - 几种语言的关键字都对得上（比如繁体和简体的"排名第"一样）：画面照样认出，语言先不认定
    - 只有一种语言时，一开始就认定
    - 设置页选了某种语言（use_choice）：只按它认，不再自己判断；改回"自动识别"：认出下一个画面时重新定
    lang：现在按哪种语言读（认定之前是最近一次认出画面的那种，一开始是简体）；locked：认定了没有。
    configs：{语言: screens.json 的规则}（测试用；不给就按 game_lang 读）。"""

    def __init__(self, engine, langs=None, configs=None, log=None):
        langs = list(langs or game_lang.languages())
        configs = configs or {}
        self.detectors = {lang: ScreenDetector(engine, configs.get(lang), lang) for lang in langs}
        self.lang = langs[0]
        self.locked = len(langs) == 1
        self.choice = game_lang.AUTO
        self.log = log or (lambda message: None)

    def use_choice(self, choice):
        """设置页的"游戏语言"（game_lang.choice()）：AUTO 自己认；某种语言 = 只按它认（没有这种语言时当作 AUTO）。"""
        choice = choice if choice in self.detectors else game_lang.AUTO
        if choice == self.choice:
            return
        self.choice = choice
        if choice != game_lang.AUTO:
            self.lang, self.locked = choice, True
            self.log(f"游戏语言：{game_lang.label(choice)}（设置页选的）")
        else:
            self.locked = len(self.detectors) == 1
            self.log("游戏语言：改成自动识别" + ("" if self.locked else "，认出下一个画面时定下来"))

    @property
    def cfg(self):
        return self.detectors[self.lang].cfg

    @property
    def looked_alike(self):
        return sum(d.looked_alike for d in self.detectors.values())

    @property
    def missing(self):
        """没有参照图的画面（别的语言前面带上语言，如 "en:summary"）。"""
        return [name if lang == game_lang.DEFAULT else f"{lang}:{name}"
                for lang, d in self.detectors.items() for name in d.missing]

    def detect(self, frame):
        if self.locked:
            return self.detectors[self.lang].detect(frame)
        order = [self.lang] + [lang for lang in self.detectors if lang != self.lang]
        names = dict.fromkeys(name for lang in order for name in self.detectors[lang].cfg["screens"])  # 有的语言多几项
        for name in names:
            matched = [lang for lang in order if name in self.detectors[lang].cfg["screens"]
                       and self.detectors[lang].check(frame, name)]
            if not matched:
                continue
            self.lang = matched[0]
            if len(matched) == 1:
                self.locked = True
                self.log(f"游戏语言：{game_lang.label(self.lang)}（从{self.cfg['screens'][name]['label']}认出）")
            return screen_of(self.cfg, name)
        return None


def save_template(name, image_path, cfg):
    """按配置里的 template_box，从一张截图上截出参照图，存到 config/templates/<name>.png
    （别的游戏语言存 config/templates/<语言>/，见 game_lang.py）。"""
    spec = cfg["screens"][name]
    gray = gray_crop(load_image(image_path), spec["template_box"], cfg["base_resolution"], spec.get("anchor"))
    path = game_lang.own_template_path(f"{name}.png", cfg.get("language"))
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", gray)[1].tofile(path)
    return path


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="判断截图是哪个画面；或者从截图生成参照图")
    parser.add_argument("images", type=Path, nargs="+", help="截图")
    parser.add_argument("--lang", choices=game_lang.languages(), default=game_lang.DEFAULT,
                        help="游戏语言（默认简体中文 zh-CN）")
    parser.add_argument("--save-template", metavar="画面", help="从这张截图生成这种画面的参照图（如 summary）")
    args = parser.parse_args()

    cfg = load_config("screens.json", args.lang)
    if args.save_template:
        path = save_template(args.save_template, args.images[0], cfg)
        print(f"已生成参照图：{path}")
        return

    detector = ScreenDetector(create_engine(SMALL_CROP_PARAMS), cfg, args.lang)
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
