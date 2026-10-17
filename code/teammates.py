"""读总结页上两名队友的数据：名字、ID、击杀/助攻/击倒、伤害、生存时间、急救、重生。

总结页三张卡片版式相同，自己永远在中间，队友在左右两张（2026-10-17 用户决定记录队友，见 CLAUDE.md 原则 7）。
区域写在 config/summary.json 的 "teammates"：数据项用中间卡片同名项的区域左右平移，读法和自己的一样（summary_page.read_field）；
名字用读自己玩家名的规则（player_name.pick_name）。
读不准的项（置信度低、格式不对、超出范围）留空，不写错的数字，也不因为队友把这一局标成待确认（原则 5）。

检查截图：python code/teammates.py 截图1.png [截图2.png ...]
会顺便读自己那张卡片和小队总击杀，核对"三个人的击杀加起来 = 小队总击杀"。
"""

import argparse
import re
from pathlib import Path

from ocr import SMALL_CROP_PARAMS, create_engine, crop, load_config, load_image
from player_name import pick_name
from storage import mmss, name_key
from summary_page import CONFIG_NAME, clean, read_field, read_summary, show, to_seconds

NAME_CONFIG = "player_name.json"  # 名字的置信度门槛、长度和读自己名字时一样


def shift(roi, dx):
    x, y, w, h = roi
    return [x + dx, y, w, h]


def read_name(engine, img, roi, cfg, name_cfg):
    """读卡片上的名字，没读到或读不准返回 None。"""
    result = engine(crop(img, roi, cfg))
    if not result.txts:
        return None
    found = pick_name(result.boxes, result.txts, result.scores, name_cfg["min_score"])
    if found is None or not name_cfg["min_length"] <= len(name_key(found[0])) <= name_cfg["max_length"]:
        return None
    return found[0]


def read_player_id(engine, img, roi, cfg, length):
    """读卡片下面的"ID：016045…"，返回数字串（开头的 0 要留着，所以不转成整数）。读不准返回 None。"""
    result = engine(crop(img, roi, cfg))
    for text, score in zip(result.txts or (), result.scores or ()):
        if score < cfg["min_score"]:
            continue
        # "ID" 里的 I 可能被修正成 1，所以取最长的那串数字
        digits = max(re.findall(r"\d+", clean(text, cfg["replace"])), key=len, default="")
        if length[0] <= len(digits) <= length[1]:
            return digits
    return None


def read_teammates(engine, img, cfg):
    """返回队友列表（左边的在前），每人一个字典：slot（left / right）、name、player_id、kills、assists、knockdowns、
    damage、survival_time（秒）、revives、respawns，读不准的为 None；problems：哪几项没读准（写日志用）。
    名字、ID、击杀/助攻/击倒一项都没读到的卡片（可能没有这个队友）不算。"""
    team, name_cfg = cfg["teammates"], load_config(NAME_CONFIG)
    mates = []
    for slot, dx in team["offsets"].items():
        values, problems = {}, []
        for name in team["fields"]:
            spec = {**cfg["fields"][name], "roi": shift(cfg["fields"][name]["roi"], dx)}
            r = read_field(engine, img, name, spec, cfg, False)
            values[name] = r["value"] if r["status"] == "ok" else None
            if r["status"] != "ok":
                problems.append(spec["label"])
        kills, assists, knockdowns = values["kda"] or (None, None, None)
        mate = {
            "slot": slot,
            "name": read_name(engine, img, shift(team["name"]["roi"], dx), cfg, name_cfg),
            "player_id": read_player_id(engine, img, shift(team["player_id"]["roi"], dx), cfg, team["player_id"]["length"]),
            "kills": kills,
            "assists": assists,
            "knockdowns": knockdowns,
            "damage": values["damage"],
            "survival_time": to_seconds(values["survival_time"]),
            "revives": values["revives"],
            "respawns": values["respawns"],
        }
        mate["problems"] = problems + [label for key, label in (("name", "名字"), ("player_id", "ID")) if mate[key] is None]
        if mate["name"] or mate["player_id"] or values["kda"]:
            mates.append(mate)
    return mates


def describe(mate):
    """日志里的一行，如：队友名（ID 0160…1234）0/3/1，伤害 613，生存 9:22，急救 0，重生 0"""
    pid = f"{mate['player_id'][:4]}…{mate['player_id'][-4:]}" if mate["player_id"] else "-"
    kda = "/".join(show(mate[k]) for k in ("kills", "assists", "knockdowns"))
    text = (f"{mate['name'] or '名字没读到'}（ID {pid}）{kda}，伤害 {show(mate['damage'])}，"
            f"生存 {mmss(mate['survival_time'])}，急救 {show(mate['revives'])}，重生 {show(mate['respawns'])}")
    return text + (f"；没读准：{'、'.join(mate['problems'])}" if mate["problems"] else "")


def main():
    parser = argparse.ArgumentParser(description="读总结页截图上两名队友的数据")
    parser.add_argument("images", type=Path, nargs="+", help="总结页截图")
    args = parser.parse_args()

    cfg = load_config(CONFIG_NAME)
    engine = create_engine(SMALL_CROP_PARAMS)
    for path in args.images:
        img = load_image(path)
        own, _ = read_summary(engine, img, cfg)
        mates = read_teammates(engine, img, cfg)
        print(f"{path.parent.name}/{path.name}")
        for mate in mates:
            print(f"  {'左' if mate['slot'] == 'left' else '右'}  {describe(mate)}")
        if not mates:
            print("  没有读到队友")
        # 核对：三个人的击杀加起来应该等于小队总击杀
        kills = [own["kda"]["value"][0] if own["kda"]["value"] else None] + [m["kills"] for m in mates]
        squad = own["squad_kills"]["value"]
        if squad is not None and None not in kills:
            mark = "✓" if sum(kills) == squad else "✗ 对不上"
            print(f"  小队总击杀 {squad}，三人击杀 {' + '.join(map(str, kills))} = {sum(kills)}  {mark}")


if __name__ == "__main__":
    main()
