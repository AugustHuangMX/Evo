"""对局中的界面（HUD）：左下角自己和两名队友、右上角的数据（规则在 config/hud.json）。

这里只管"看一张截图能看出什么"，不记状态；一局里怎么用（什么时候存、什么时候读）见 in_match.py。
- 自己的界面：左下角认得出自己的传奇（legends.py）。观战时左下角是被观战的人、位置也往上移，认不出，
  所以"认得出自己的传奇" = 这一张截图是自己的界面，右上角的数据是自己的
- 倒地：左下角自己那一行变红（knocked）；成盒观战：顶部出现"观战"标签（spectating）。都只看颜色、外观，不用识别引擎
- 队友：自己上方两行的小头像，用缩小的传奇参照图认（mate_legends）；名字先裁下来（snip），一局结束时再读
- 右上角：先整块存下来（snip），一局结束时再读（read_top）：剩余小队数、击杀、助攻、老板分（参与分）、伤害、本局 RP

检查截图：python code/hud.py 截图1.png [截图2.png ...]   （每张都读一遍，打印结果；会加载识别引擎）
生成参照图：python code/hud.py --save-template spectate|damage_icon|kp_icon 截图.png   （按配置里的 template_box 截一块）
"""

import argparse
import re
import sys
from pathlib import Path

import cv2
import numpy as np

from legends import LegendDetector
from ocr import load_config, load_image
from player_name import pick_name
from screens import TEMPLATE_DIR, gray_crop, outline
from storage import name_key

CONFIG_NAME = "hud.json"
HUD_TEMPLATE_DIR = TEMPLATE_DIR / "hud"
TOP_FIELDS = ("squads_left", "kills", "assists", "participation", "damage", "rp_delta")
KP_ROW = ("kills", "assists", "participation")  # 右上角同一行：全是 0 时整行不显示
TOP_LABELS = {"squads_left": "剩余小队", "kills": "击杀", "assists": "助攻", "participation": "老板分",
              "damage": "伤害", "rp_delta": "RP"}


class Snip:
    """截图上的一小块，按原分辨率存着（不放大，省内存），以后再按基准坐标裁其中的更小块去识别。
    Stata 类比：先 preserve 一份子样本，用到时再 keep if 一部分。"""

    def __init__(self, frame, box, base):
        h, w = frame.shape[:2]
        self.sx, self.sy = w / base[0], h / base[1]
        self.box, self.base = box, base
        x, y, bw, bh = box
        self.x0, self.y0 = round(x * self.sx), round(y * self.sy)
        self.img = frame[self.y0:round((y + bh) * self.sy), self.x0:round((x + bw) * self.sx)].copy()

    def crop(self, roi, upscale=1, gray=True, pad=0):
        """按基准坐标裁出 roi（要在 box 里面），放大到"基准尺寸 × upscale"，转灰度（识别引擎要三通道），四周加 pad 像素的边。"""
        x, y, w, h = roi
        piece = self.img[round(y * self.sy) - self.y0:round((y + h) * self.sy) - self.y0,
                         round(x * self.sx) - self.x0:round((x + w) * self.sx) - self.x0]
        piece = cv2.resize(piece, (round(w * upscale), round(h * upscale)),
                           interpolation=cv2.INTER_CUBIC if upscale * self.sx > 1 else cv2.INTER_AREA)
        if gray:
            piece = cv2.cvtColor(cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        if pad:
            piece = cv2.copyMakeBorder(piece, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        return piece

    def gray(self, roi):
        """roi 这一块缩放成基准尺寸的灰度图（比外观用）。"""
        return cv2.cvtColor(self.crop(roi, 1, gray=False), cv2.COLOR_BGR2GRAY)

    def thumb(self):
        """缩小的灰度图，用来判断"和上一次存的比，内容变了没有"。"""
        g = cv2.cvtColor(self.img, cv2.COLOR_BGR2GRAY)
        return cv2.resize(g, (max(1, g.shape[1] // 4), max(1, g.shape[0] // 4)), interpolation=cv2.INTER_AREA)


def load_hud_template(name):
    path = HUD_TEMPLATE_DIR / f"{name}.png"
    if not path.is_file():
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def best_match(region, template):
    """参照图在 region 里最像的位置：(相似度, x, y)。纯色区域算不出相似度，当作不像。"""
    if template is None or template.shape[0] > region.shape[0] or template.shape[1] > region.shape[1]:
        return -1.0, 0, 0
    result = np.nan_to_num(cv2.matchTemplate(outline(region), outline(template), cv2.TM_CCOEFF_NORMED), nan=-1.0)
    _, score, _, (x, y) = cv2.minMaxLoc(result)
    return float(score), x, y


def red_ratio(frame, box, base):
    """box 里"红色"像素的占比（倒地时左下角自己那一行变红）。"""
    h, w = frame.shape[:2]
    sx, sy = w / base[0], h / base[1]
    x, y, bw, bh = box
    px = frame[round(y * sy):round((y + bh) * sy), round(x * sx):round((x + bw) * sx)].reshape(-1, 3).astype(np.int32)
    if not len(px):
        return 0.0
    b, g, r = px[:, 0], px[:, 1], px[:, 2]
    return float(np.mean((r > 150) & (r > g * 1.8) & (r > b * 1.8)))


def clean_number(text, replace):
    for wrong, right in replace.items():
        text = text.replace(wrong, right)
    return text.replace(" ", "").replace(",", "")


class Hud:
    """engine：识别引擎（ocr.LazyEngine，读数时才用到）；legends：LegendDetector（不给就新建一个）。"""

    def __init__(self, engine, legends=None, cfg=None):
        self.engine = engine
        self.cfg = cfg or load_config(CONFIG_NAME)
        self.base = self.cfg["base_resolution"]
        self.legends = legends or LegendDetector()
        mates = self.cfg["teammates"]
        self.mate_templates = self.legends.scaled(mates["scale"])
        self.spectate_template = load_hud_template("spectate")
        self.icon_template = load_hud_template("damage_icon")
        self.kp_template = load_hud_template("kp_icon")
        self.missing = [n for n, t in (("spectate", self.spectate_template), ("damage_icon", self.icon_template),
                                       ("kp_icon", self.kp_template)) if t is None]  # 没有参照图的（认不出观战、读不出伤害等）

    # ---------- 只看外观、不用识别引擎 ----------

    def own_legend(self, frame):
        """左下角自己的传奇（编号），认不出返回 None。认得出 = 这一张是自己的界面（不是观战）。"""
        return self.legends.identify(frame)

    def knocked(self, frame):
        """自己倒地了没有（左下角自己那一行的边框和名字变红）。只在认得出自己传奇的截图上问。"""
        k = self.cfg["knocked"]
        return red_ratio(frame, k["border"], self.base) >= k["border_red"] and \
            red_ratio(frame, k["name"], self.base) >= k["name_red"]

    def spectating(self, frame):
        """是不是在观战（成盒以后）：顶部有"观战"标签。"""
        s = self.cfg["spectate"]
        score, _, _ = best_match(gray_crop(frame, s["roi"], self.base), self.spectate_template)
        return score >= s["min_similarity"]

    def spectated_legend(self, frame):
        """观战时被观战的人用的传奇（编号），认不出返回 None。只在 spectating() 为真时问。"""
        return self.legends.identify(frame, self.cfg["spectate"]["player_box"])

    def mate_legends(self, frame):
        """两名队友的传奇（编号），每个位置一个，认不出（或没有这个队友）为 None。"""
        m = self.cfg["teammates"]
        return [self.legends.identify(frame, slot["face"], self.mate_templates, m["min_similarity"], m["min_margin"])
                for slot in m["slots"]]

    def top_snip(self, frame):
        return Snip(frame, self.cfg["top_right"]["box"], self.base)

    def mate_name_snip(self, frame, slot):
        return Snip(frame, self.cfg["teammates"]["slots"][slot]["name"], self.base)

    def own_name_snip(self, frame, roi):
        return Snip(frame, roi, self.base)

    def changed(self, a, b):
        """两块右上角的内容是不是不一样（只存变了的，读的时候少读几张）。"""
        if a is None or b is None:
            return True
        ta, tb = a.thumb(), b.thumb()
        if ta.shape != tb.shape:
            return True
        return float(np.mean(cv2.absdiff(ta, tb))) >= self.cfg["top_right"]["change"]

    # ---------- 用识别引擎读（一局结束时才调用） ----------

    def _texts(self, piece):
        result = self.engine(piece)
        return list(zip(result.txts or (), result.scores or ()))

    def _number(self, piece, pattern, bounds, top):
        """读一小块里的一个整数（格式和范围都对、置信度够高才算），读不到返回 (None, 置信度或 None)。"""
        best = (None, None)
        for text, score in self._texts(piece):
            m = re.search(pattern, clean_number(text, top["replace"]))
            if not m:
                continue
            value = int(m[1])
            if score >= top["min_score"] and bounds[0] <= value <= bounds[1]:
                if best[1] is None or score > best[1]:
                    best = (value, score)
        return best

    def read_top(self, snip):
        """读一块右上角。返回 {项: 值}（没显示或读不出为 None）、{项: 置信度}，和"显示着"的项（集合）。
        显示着但读不出 ≠ 没显示：击杀、助攻、老板分那一行看最左边的骷髅图标在不在（或者读出了其中一个）；伤害看图标在不在。
        （2026-10-18 实战：那一行显示着，数字有时只读出一部分、有时一个都没读出来——都不能当成 0）"""
        top = self.cfg["top_right"]
        values, scores = {}, {}
        for name, spec in top["fields"].items():
            piece = snip.crop(spec["roi"], top["upscale"], pad=top["pad"])
            pattern = r"^([+-]?\d{1,3})(?:排位.*)?$" if name == "rp_delta" else r"^(\d{1,3})$"
            values[name], scores[name] = self._number(piece, pattern, spec["range"], top)
        values["damage"], scores["damage"], icon = self._damage(snip, top)
        kp = top["kp_icon"]
        kp_reads = [k for k in KP_ROW if values[k] is not None]
        icon_seen = best_match(snip.gray(kp["area"]), self.kp_template)[0] >= kp["min_similarity"]
        if not icon_seen and len(kp_reads) < 2:
            # 图标不在、只读出一个数：多半是背后的画面（2026-10-18 实战：0/0/0 时这一行不显示，老板分那一格读出了"7"）
            for k in KP_ROW:
                values[k] = scores[k] = None
        shown = {k for k in values if values[k] is not None}
        if icon_seen or len(kp_reads) >= 2:  # 图标在（参照图万一在别的分辨率下认不出：读出两个以上也算）
            shown |= set(KP_ROW)
        if icon:
            shown.add("damage")
        return values, scores, shown

    def _damage(self, snip, top):
        """伤害：先找到前面的图标，只读图标右边的数字。返回 (值, 置信度, 图标在不在)。
        找不到图标 = 这一格没显示（还没造成伤害）。"""
        d = top["damage"]
        cell = d["cell"]
        score, x, _ = best_match(snip.gray(cell), self.icon_template)
        if score < d["min_similarity"]:
            return None, None, False
        left = cell[0] + x + d["template_box"][2] + 1
        right = cell[0] + cell[2]
        if right - left < 6:
            return None, None, True
        piece = snip.crop([left, cell[1], right - left, cell[3]], top["upscale"], pad=top["pad"])
        return (*self._number(piece, r"^(\d{1,5})$", d["range"], top), True)

    def read_name(self, snip, left_range=None):
        return read_name(self.engine, snip, left_range)


def read_name(engine, snip, left_range=None, min_score=0.9, length=(2, 32)):
    """读一块里的玩家名（左对齐：从最左边那段文字读起，见 player_name.pick_name）。
    left_range：最左边那段文字的左边（基准坐标）要落在这个范围里，否则不算（比如被裁掉一半的别的文字）。读不到返回 None。"""
    result = engine(snip.crop(snip.box, 2))
    if not result.txts:
        return None
    found = pick_name(result.boxes, result.txts, result.scores, min_score)
    if found is None or not length[0] <= len(name_key(found[0])) <= length[1]:
        return None
    if left_range is not None:
        left = min(float(b[:, 0].min()) for b in result.boxes) / 2 + snip.box[0]
        if not left_range[0] <= left <= left_range[1]:
            return None
    return found[0]


def save_template(name, image_path, cfg):
    """按配置里的 template_box 从截图上截出参照图，存到 config/templates/hud/<name>.png。"""
    box = {"spectate": cfg["spectate"], "damage_icon": cfg["top_right"]["damage"],
           "kp_icon": cfg["top_right"]["kp_icon"]}[name]["template_box"]
    gray = gray_crop(load_image(image_path), box, cfg["base_resolution"])
    HUD_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    path = HUD_TEMPLATE_DIR / f"{name}.png"
    cv2.imencode(".png", gray)[1].tofile(path)
    return path


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="读对局中截图的左下角和右上角；或者生成参照图")
    parser.add_argument("images", type=Path, nargs="+", help="截图")
    parser.add_argument("--save-template", choices=("spectate", "damage_icon", "kp_icon"), help="从这张截图生成参照图")
    args = parser.parse_args()

    cfg = load_config(CONFIG_NAME)
    if args.save_template:
        print(f"已生成参照图：{save_template(args.save_template, args.images[0], cfg)}")
        return

    from ocr import SMALL_CROP_PARAMS, create_engine
    hud = Hud(create_engine(SMALL_CROP_PARAMS), cfg=cfg)
    names = load_config("player_name.json")
    for path in args.images:
        img = load_image(path)
        own = hud.own_legend(img)
        if own is None:
            print(f"{path.name}：认不出自己的传奇（不是自己的界面）{'，正在观战' if hud.spectating(img) else ''}")
            continue
        values, _, _ = hud.read_top(hud.top_snip(img))
        mates = hud.mate_legends(img)
        mate_text = []
        for i, key in enumerate(mates):
            name = hud.read_name(hud.mate_name_snip(img, i), cfg["teammates"]["name_left"])
            mate_text.append(f"{name or '名字没读到'}（{hud.legends.name(key) if key else '传奇认不出'}）")
        own_name = hud.read_name(hud.own_name_snip(img, names["places"]["hud"]["roi"]))
        top = "，".join(f"{TOP_LABELS[k]} {'-' if values[k] is None else values[k]}" for k in TOP_FIELDS)
        print(f"{path.name}：自己 {own_name or '名字没读到'}（{hud.legends.name(own)}）"
              f"{'，倒地' if hud.knocked(img) else ''}；队友 {'、'.join(mate_text)}；右上角 {top}")


if __name__ == "__main__":
    main()
