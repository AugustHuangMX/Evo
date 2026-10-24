"""从靶场截图生成武器参照图（开发工具）：python tools/weapon_templates.py 截图文件夹 [截图文件夹 ...]

新赛季出了新武器：先在 config/weapons.json 里加上它（编号、名字），再进靶场把每把武器都拿一遍截样本
（python tools/frame_capture.py --png，拿到手后慢慢转视角 2–3 秒，免得画面没变不存），然后运行这个工具。
1. 每张截图：右下角手上那把的大图标挑出白色像素（weapons.icon_mask），去掉碰到这块边上的（拾取动画、提示文字、
   弹药小图标），裁到边
2. 用识别引擎读武器栏下面两把武器的名字：两把都有名字时，底色亮的那把在手上（另一把底色暗、名字发灰）；
   名字对到 config/weapons.json 里的武器（对不上的不要，比如"手感卓越的刀刃"）
3. 同一把武器按图标长相分组（单拿、双持……），至少 MIN_FRAMES 张一样的才算（拾取时的动画只有一两张）；
   每组挑最有代表性的一张存成参照图 config/templates/weapons/<编号>.png（第二组起 <编号>-2.png ……）。
   原来的参照图先全部删掉
4. 写一张总览图（--sheet，默认在截图文件夹旁边的 weapon_templates.png）：每张参照图、对到的武器、几张截图，人工核对用
5. 用新的参照图把这些截图再认一遍：打印认对、认错、认不出各几张
名字读错、手上那把判断错了：在 config/weapons.json 的 game 里补上读出来的写法，或者用 --skip 去掉那几张截图再运行。

只在开发时用：要加载识别引擎，每张截图读两次名字（约 0.1 秒）。
"""

import argparse
import sys
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
from layout import cut
from ocr import SMALL_CROP_PARAMS, create_engine, load_image
from weapons import TEMPLATE_DIR, WeaponDetector, icon_mask, template_key

MIN_FRAMES = 2  # 同一个样子至少几张截图才做参照图
# 图标那块里不属于武器、但又不碰边的东西（1920×1080 基准，看 2026-10-24 靶场截图定的）：
BELOW = 1001  # 整块都在这条线以下的：配件格、"+"、弹药小图标（武器的握把连着枪身，不会整块在下面）
RIGHT_OF = 1703  # 整块都在这条线右边的：三位数弹药（塞拉的 135）的第一位
SAME_SHAPE = 0.9  # 两张图标这么像算同一个样子
MAX_PER_WEAPON = 4  # 一把武器最多几张参照图
NAME_MATCH = 0.7  # 读出来的名字和名单里的名字这么像才算对上


def clean_icon(mask, box):
    """去掉碰到边上的白色块（拾取动画、透过来的亮背景）、零星的点、不属于武器的小图标（BELOW、RIGHT_OF），裁到边；
    剩下的白色像素太少返回 None。box：图标那块的基准坐标（config 的 icon_box）。"""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    h, w = mask.shape
    below, right_of = BELOW - box[1], RIGHT_OF - box[0]
    keep = np.zeros_like(mask)
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 6 or x == 0 or y == 0 or x + bw >= w or y + bh >= h or y >= below or x >= right_of:
            continue
        keep[labels == i] = 255
    ys, xs = np.nonzero(keep)
    if len(xs) < 150:
        return None
    return keep[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def shape_similarity(a, b):
    """两张裁好的图标像不像（大小差太多就不像；小的在大的上面滑一下找最像的位置）。"""
    if abs(a.shape[0] - b.shape[0]) > 4 or abs(a.shape[1] - b.shape[1]) > 6:
        return 0.0
    big = cv2.copyMakeBorder(a, 4, 4, 6, 6, cv2.BORDER_CONSTANT, value=0)
    result = cv2.matchTemplate(big, b, cv2.TM_CCOEFF_NORMED)
    return float(np.nan_to_num(result).max())


def normalize(name):
    return "".join(name.split()).replace("“", '"').replace("”", '"').replace("「", '"').replace("」", '"').lower()


def match_name(text, weapons):
    """读出来的名字 → 武器编号（对不上返回 None）。比 zh、game、en 几种写法，取最像的。"""
    text = normalize(text).lstrip("r")  # 另一把旁边的"R"提示有时会读进来
    if not text:
        return None
    best, best_score = None, 0.0
    for key, info in weapons.items():
        for name in (info.get("game"), info["zh"], info.get("en")):
            if name:
                score = SequenceMatcher(None, text, normalize(name)).ratio()
                if score > best_score:
                    best, best_score = key, score
    return best if best_score >= NAME_MATCH else None


BADGE = (3, 80)  # "R"提示头上的橙色小三角有几个像素（多了是整片橙色的底色）


def _piece(frame, box, cfg):
    piece = cut(frame, box, cfg["base_resolution"], cfg.get("anchor"))
    return cv2.resize(piece, (box[2], box[3]), interpolation=cv2.INTER_AREA)


def badge_pixels(frame, box, cfg):
    """box 里亮橙色的像素有几个（"R"提示头上的小三角）。"""
    p = _piece(frame, box, cfg).astype(np.int16)
    b, g, r = p[..., 0], p[..., 1], p[..., 2]
    return int(np.count_nonzero((r > 200) & (g > 90) & (g < 200) & (b < 100) & (r - b > 120)))


def held_name(engine, frame, cfg):
    """手上那把武器的名字（读不出、拿不准返回 ""）。两把都有名字时用两种办法判断哪把在手上，一致才算（拿不准的截图
    扔掉不要紧，每把武器有几张干净的就够做参照图）：手上那把底色亮；没拿在手上的那把旁边有"R"提示（橙色小三角，
    轻型弹药那种橙色底色会让它认错，所以只当核对用）。"""
    found = {}
    for side, box in cfg["name_rows"].items():
        piece = _piece(frame, box, cfg)
        result = engine(cv2.resize(piece, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
        text = "".join(result.txts) if result.txts else ""
        if text.strip():
            found[side] = (float(np.median(cv2.cvtColor(piece, cv2.COLOR_BGR2HSV)[:, :, 2])), text)
    if len(found) < 2:
        return next(iter(found.values()))[1] if found else ""
    brighter = max(found, key=lambda side: found[side][0])
    badges = {side: badge_pixels(frame, box, cfg) for side, box in cfg["badge_rows"].items()}
    marked = [side for side, n in badges.items() if BADGE[0] <= n <= BADGE[1]]
    if marked and max(marked, key=badges.get) == brighter:
        return ""  # 两种办法说的不一样：拿不准
    return found[brighter][1]


def medoid(icons):
    """一组图标里和其他几张最像的那张。"""
    if len(icons) <= 2:
        return icons[0]
    totals = [sum(shape_similarity(a, b) for b in icons) for a in icons]
    return icons[int(np.argmax(totals))]


def main():
    parser = argparse.ArgumentParser(description="从靶场截图生成武器参照图")
    parser.add_argument("folders", nargs="+", type=Path, help="截图文件夹（frame_capture.py 存的）")
    parser.add_argument("--out", type=Path, default=TEMPLATE_DIR, help="参照图存到哪里（默认 config/templates/weapons）")
    parser.add_argument("--sheet", type=Path, default=None, help="总览图存到哪里（默认第一个截图文件夹旁边的 weapon_templates.png）")
    parser.add_argument("--skip", nargs="*", default=[], help="不用的截图（文件名，如 0116_171003_2560x1440.png）")
    args = parser.parse_args()

    detector = WeaponDetector(folder=args.out)
    cfg, weapons = detector.cfg, detector.weapons
    engine = create_engine(SMALL_CROP_PARAMS)
    files = [p for folder in args.folders for p in sorted(folder.iterdir())
             if p.suffix.lower() in (".png", ".jpg") and p.name not in args.skip]
    print(f"{len(files)} 张截图")

    samples = []  # [(文件, 编号, 裁好的图标, 整块白色像素)]
    unknown = {}  # 读出来但对不上名单的名字 → 张数
    for path in files:
        frame = load_image(path)
        mask = icon_mask(frame, cfg)
        icon = clean_icon(mask, cfg["icon_box"])
        if icon is None:
            continue
        text = held_name(engine, frame, cfg)
        key = match_name(text, weapons)
        if key is None:
            if text:
                unknown[text] = unknown.get(text, 0) + 1
            continue
        samples.append((path, key, icon, mask))
    print(f"对上名单的 {len(samples)} 张；对不上的名字：" + ("、".join(f"{t}（{n} 张）" for t, n in unknown.items()) or "没有"))

    # 每把武器按长相分组
    groups = {}  # 编号 → [[图标, ...], ...]
    for _, key, icon, _ in samples:
        for group in groups.setdefault(key, []):
            if shape_similarity(group[0], icon) >= SAME_SHAPE:
                group.append(icon)
                break
        else:
            groups[key].append([icon])

    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("*.png"):
        old.unlink()
    made = {}  # 编号 → [(文件名, 张数, 图标)]
    for key, gs in groups.items():
        gs = sorted((g for g in gs if len(g) >= MIN_FRAMES), key=len, reverse=True)[:MAX_PER_WEAPON]
        for i, g in enumerate(gs):
            name = f"{key}.png" if i == 0 else f"{key}-{i + 1}.png"
            icon = medoid(g)
            cv2.imencode(".png", icon)[1].tofile(str(args.out / name))
            made.setdefault(key, []).append((name, len(g), icon))
    missing = [weapons[k]["zh"] for k in weapons if k not in made]
    print(f"参照图 {sum(len(v) for v in made.values())} 张、{len(made)} 把武器；名单里没做出参照图的：{'、'.join(missing) or '没有'}")

    # 用新的参照图再认一遍
    check = WeaponDetector(cfg=cfg, folder=args.out)
    right, wrong, none = 0, [], []
    for path, key, _, mask in samples:
        got, score = check.identify(mask)
        if got == key:
            right += 1
        elif got is None:
            none.append(f"{path.name}（{weapons[key]['zh']}，{score:.2f}）")
        else:
            wrong.append(f"{path.name}（{weapons[key]['zh']} 认成 {weapons[got]['zh']}，{score:.2f}）")
    print(f"再认一遍：认对 {right} 张，认错 {len(wrong)} 张，认不出 {len(none)} 张")
    for line in wrong + none:
        print("    " + line)

    # 总览图：每张参照图放大两倍，下面写编号和张数（中文写不上去，名字看终端里的表）
    tiles = []
    for key, items in made.items():
        for name, count, icon in items:
            tile = np.full((90, 230, 3), 40, np.uint8)
            h, w = icon.shape
            scale = min(200 / w, 56 / h, 2.0)
            small = cv2.resize(icon, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_NEAREST)
            tile[6:6 + small.shape[0], 10:10 + small.shape[1]] = cv2.cvtColor(small, cv2.COLOR_GRAY2BGR)
            cv2.putText(tile, f"{name[:-4]} ({count})", (8, 82), 0, 0.45, (0, 255, 255), 1)
            tiles.append(tile)
            print(f"    {name:22s} {weapons[key]['zh']}：{count} 张截图")
    cols = 6
    while tiles and len(tiles) % cols:
        tiles.append(np.full_like(tiles[0], 40))
    sheet_path = args.sheet or args.folders[0].parent / "weapon_templates.png"
    if tiles:
        sheet = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
        cv2.imencode(".png", sheet)[1].tofile(str(sheet_path))
        print(f"总览图：{sheet_path}")
    return 0 if not wrong else 1


if __name__ == "__main__":
    sys.exit(main())
