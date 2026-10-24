"""识别赛后总结页：从截图里读出名次、小队总击杀、击杀/助攻/击倒、伤害、生存时间、急救、重生。

识别规则（区域坐标、格式、合理范围）写在 config/summary.json 里，不写死在代码中。

单独试：python code/summary_page.py <截图路径>
例如：  python code/summary_page.py captures/samples/vlcsnap-2026-10-16-18h26m12s173.png
加上 --save-crops，会把裁出来的小块存到 captures/debug_crops/，可以看看 OCR 实际看到了什么。
"""

import argparse
import re
import time
from pathlib import Path

import cv2

import game_lang
from ocr import SMALL_CROP_PARAMS, create_engine, crop, load_config, load_image
from paths import CAPTURES_DIR, data_relative
from process_info import memory_mb

CONFIG_NAME = "summary.json"
CROPS_DIR = CAPTURES_DIR / "debug_crops"
KEY_FIELDS = ("placement", "kda", "damage")  # 这几项有任何问题，整局标记"待确认"

STATUS_TEXT = {
    "ok": "正常",
    "low_score": "置信度低",
    "not_found": "没读到",
    "parse_fail": "格式不对",
    "out_of_range": "超出合理范围",
}


def clean(text, replace):
    """修正常见误读（比如字母 O 读成数字 0），去掉空格和千位逗号。"""
    for wrong, right in replace.items():
        text = text.replace(wrong, right)
    return text.replace(" ", "").replace(",", "")


def parse(expect, text):
    """把文字转成数据。格式不对时返回 None。"""
    if expect == "kda":
        # 必须正好是"数字/数字/数字"，少一个斜杠都不算（不去猜）
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{1,2})", text)
        return tuple(int(g) for g in m.groups()) if m else None
    if expect == "duration":
        m = re.search(r"(\d{1,2}):(\d{2})", text)
        return f"{m[1]}:{m[2]}" if m else None
    m = re.search(r"\d+", text)  # integer：取出其中的数字，比如"第6"取出 6
    return int(m[0]) if m else None


def in_range(value, bounds):
    if bounds is None:
        return True
    values = value if isinstance(value, tuple) else (value,)
    return all(bounds[0] <= v <= bounds[1] for v in values)


def read_field(engine, img, name, spec, cfg, save_crops):
    """识别一个数据项，返回结果字典。"""
    piece = crop(img, spec["roi"], cfg, spec.get("anchor"))
    if save_crops:
        cv2.imencode(".png", piece)[1].tofile(CROPS_DIR / f"{name}.png")

    start = time.perf_counter()
    result = engine(piece)
    ms = (time.perf_counter() - start) * 1000
    lines = list(zip(result.txts, result.scores)) if result.txts else []

    out = {"name": name, "label": spec["label"], "value": None, "raw": "", "score": None, "ms": ms}
    if not lines:
        out["status"] = "not_found"
        return out

    # 小块里可能读到不止一行（比如边上带进来半个标签），挑格式对得上、置信度最高的那行
    candidates = []
    for txt, score in lines:
        value = parse(spec["expect"], clean(txt, cfg["replace"]))
        if value is not None:
            candidates.append((score, txt, value))

    if not candidates:
        best_txt, best_score = max(lines, key=lambda t: t[1])
        out.update(raw=best_txt, score=best_score, status="parse_fail")
        return out

    score, txt, value = max(candidates)
    out.update(value=value, raw=txt, score=score)
    if not in_range(value, spec.get("range")):
        out["status"] = "out_of_range"
    elif score < cfg["min_score"]:
        out["status"] = "low_score"
    else:
        out["status"] = "ok"
    return out


def read_summary(engine, img, cfg, save_crops=False):
    results = {name: read_field(engine, img, name, spec, cfg, save_crops) for name, spec in cfg["fields"].items()}

    # 常识检查：自己的击杀数不可能超过小队总击杀
    problems = []
    kda, squad = results["kda"], results["squad_kills"]
    if kda["status"] == "ok" and squad["status"] == "ok" and kda["value"][0] > squad["value"]:
        problems.append(f"击杀数 {kda['value'][0]} 大于小队总击杀 {squad['value']}")

    for name in KEY_FIELDS:
        r = results[name]
        if r["status"] != "ok":
            problems.append(f"{r['label']}：{STATUS_TEXT[r['status']]}（OCR 原文：{r['raw'] or '无'}）")
    return results, problems


def is_champion(engine, img, cfg):
    """总结页顶部的大字是不是"捍卫者"（拿冠军；被淘汰时是"全军覆没"）。配置里可以写几种（别的语言还没核实时）。"""
    banner = cfg["banner"]
    names = [banner["champion"]] if isinstance(banner["champion"], str) else banner["champion"]
    result = engine(crop(img, banner["roi"], cfg, banner.get("anchor")))
    return any(name in text for text in result.txts or () for name in names)


def to_seconds(duration):
    """"16:03" → 963 秒。"""
    if duration is None:
        return None
    minutes, seconds = duration.split(":")
    return int(minutes) * 60 + int(seconds)


def summary_to_match(results, problems, ended_at, evidence_path=None):
    """把总结页的识别结果整理成数据库里"一局"的格式（键和 storage.COLUMNS 的列名一致）。"""
    v = {name: r["value"] for name, r in results.items()}
    kills, assists, knockdowns = v["kda"] if v["kda"] else (None, None, None)
    return {
        "ended_at": ended_at,
        "placement": v["placement"],
        "squad_kills": v["squad_kills"],
        "kills": kills,
        "assists": assists,
        "knockdowns": knockdowns,
        "damage": v["damage"],
        "survival_time": to_seconds(v["survival_time"]),
        "revives": v["revives"],
        "respawns": v["respawns"],
        "needs_review": int(bool(problems)),
        "review_note": "；".join(problems) or None,
        "evidence_path": data_relative(evidence_path) if evidence_path else None,
    }


READING_FIELDS = ("placement", "squad_kills", "kills", "assists", "knockdowns", "damage", "survival_time", "revives", "respawns")


def summary_readings(results):
    """总结页读到的各项，整理成 (值, 置信度) 两个字典，键是 storage.COLUMNS 的列名（存进各来源读数表 readings 用）。
    击杀/助攻/击倒是一个小块里读出来的，三项用同一个置信度。"""
    match = summary_to_match(results, [], None)
    values = {k: match[k] for k in READING_FIELDS}
    scores = {}
    for name, r in results.items():
        for key in (("kills", "assists", "knockdowns") if name == "kda" else (name,)):
            scores[key] = r["score"]
    return values, scores


def show(value):
    if value is None:
        return "-"
    if isinstance(value, tuple):
        return "/".join(str(v) for v in value)
    return str(value)


def main():
    parser = argparse.ArgumentParser(description="从赛后总结页截图里读出本局数据")
    parser.add_argument("image", type=Path, help="截图路径")
    parser.add_argument("--save-crops", action="store_true", help="把裁出来的小块存到 captures/debug_crops/")
    parser.add_argument("--lang", choices=game_lang.languages(), default=game_lang.DEFAULT,
                        help="游戏语言（默认简体中文 zh-CN）")
    args = parser.parse_args()

    cfg = load_config(CONFIG_NAME, args.lang)
    img = load_image(args.image)
    if args.save_crops:
        CROPS_DIR.mkdir(parents=True, exist_ok=True)

    mem_start, _ = memory_mb()
    engine = create_engine(SMALL_CROP_PARAMS)

    # 跑两遍：第一遍包含加载模型的时间，第二遍才是实际识别耗时
    start = time.perf_counter()
    read_summary(engine, img, cfg)
    first_s = time.perf_counter() - start
    start = time.perf_counter()
    results, problems = read_summary(engine, img, cfg, args.save_crops)
    second_s = time.perf_counter() - start
    mem_now, mem_peak = memory_mb()

    print(f"图片: {args.image}  尺寸: {img.shape[1]}x{img.shape[0]}\n")
    print(f"{'数据':<10}{'结果':<12}{'OCR 原文':<14}{'置信度':>6}  {'耗时':>7}  状态")
    for r in results.values():
        score = f"{r['score']:.3f}" if r["score"] is not None else "-"
        print(f"{r['label']:<10}{show(r['value']):<12}{r['raw']:<14}{score:>6}  {r['ms']:>5.0f}ms  {STATUS_TEXT[r['status']]}")

    print()
    if problems:
        print("⚠ 需要人工确认：")
        for p in problems:
            print(f"  - {p}")
    else:
        k, a, d = results["kda"]["value"]
        print(f"✅ 第 {results['placement']['value']} 名，{k} 杀 / {a} 助攻 / {d} 击倒，伤害 {results['damage']['value']}")

    print("\n===== 性能 =====")
    print(f"第一遍（含加载模型）: {first_s:.2f} 秒")
    print(f"第二遍（识别 {len(results)} 个小块）: {second_s:.2f} 秒")
    print(f"内存: 启动时 {mem_start:.0f} MB，现在 {mem_now:.0f} MB，峰值 {mem_peak:.0f} MB")
    if args.save_crops:
        print(f"\n裁出的小块已存到: {CROPS_DIR.resolve()}")


if __name__ == "__main__":
    main()
