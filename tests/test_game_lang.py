"""游戏语言（game_lang.py、screens.GameScreens）：别的语言的规则叠在简体上、读出来的段位名和地图名换成简体、
记录程序认出游戏语言后各部分换成这种语言的规则。还有匹配局不补大厅段位（calibration.py）。

现在只有简体的截图，所以别的语言用"测试语言"代替：规则和简体一样，只是关键字换成画面上不会出现的字
（= 这种语言的画面一个都对不上），或者完全不改（= 和简体分不出来）。
- 规则：字典逐项合并、列表整个换掉；没有这种语言的文件就报错；参照图没有自己的就用简体的
- 段位名、地图名：画面上的写法（"DIAMOND II"、"OLYMPUS"）→ 简体名字；简体照旧
- 认语言：只有一种语言的关键字对得上就认定，之后只按它比；几种都对得上（分不出来）先不认定；只有一种语言时一开始就认定
- 记录程序：一开始按测试语言（总结页的区域故意挪走），认出简体的总结页后换成简体的规则，读出来和只用简体时一样
- 大厅校准：大厅之前最近的那一局是匹配局时不补段位，排位局、不知道模式的局照旧补

截图不在仓库里，见 fake_game.py；找不到截图时只跑不用截图的部分。

运行：python tests/test_game_lang.py
"""

import copy
import json
import tempfile
from pathlib import Path
from unittest import mock

from fake_game import BLACK, MissingShots, load, report, run_collect, shot  # 要在项目代码之前导入

import calibration
import game_lang
import ranks
import storage
from lobby import parse_tier
from match_start import closest_map
from ocr import SMALL_CROP_PARAMS, LazyEngine, load_config
from screens import GameScreens
from xp_page import current_rank

SUMMARY = "samples/vlcsnap-2026-10-16-18h26m12s173.png"  # 1080p 总结页，第 6 名
NOWHERE = ["不会出现的字"]
EN_TIERS = {"Rookie": "菜鸟", "Bronze": "青铜", "Silver": "白银", "Gold": "黄金", "Platinum": "白金",
            "Diamond": "钻石", "Master": "大师", "Apex Predator": "Apex 猎杀者"}
FOLDER = Path(tempfile.mkdtemp(prefix="evo-lang-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里


def unreadable_screens():
    """简体的画面规则，只是每种画面的关键字都换成画面上不会出现的字。"""
    cfg = copy.deepcopy(load_config("screens.json"))
    for spec in cfg["screens"].values():
        spec["keywords"] = NOWHERE
    return cfg


def write_lang(folder, lang, data):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{lang}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def rule_checks():
    """规则怎么叠、段位名和地图名怎么换（不用截图）。"""
    out = {}
    base = {"a": 1, "d": {"x": 1, "y": [1, 2]}}
    out["merge"] = game_lang._merge(base, {"d": {"y": [3]}, "e": 2}) == {"a": 1, "d": {"x": 1, "y": [3]}, "e": 2} \
        and base == {"a": 1, "d": {"x": 1, "y": [1, 2]}}

    raw = json.loads((game_lang.CONFIG_DIR / "summary.json").read_text(encoding="utf-8"))
    out["default"] = load_config("summary.json") == {**raw, "language": "zh-CN"}

    langs = FOLDER / "lang"
    write_lang(langs, "xx", {"name": "测试语言", "configs": {
        "summary.json": {"banner": {"champion": "CHAMPION"}, "fields": {"kda": {"roi": [1, 2, 3, 4]}}}}})
    xx = game_lang.load("summary.json", "xx", langs)
    out["languages"] = game_lang.languages(langs) == ["zh-CN", "xx"] and game_lang.label("xx", langs) == "测试语言" \
        and game_lang.label(None) == "简体中文"
    out["overlay"] = (xx["banner"]["champion"] == "CHAMPION" and xx["banner"]["roi"] == raw["banner"]["roi"]
                      and xx["fields"]["kda"]["roi"] == [1, 2, 3, 4] and xx["fields"]["kda"]["label"] == raw["fields"]["kda"]["label"]
                      and xx["fields"]["damage"] == raw["fields"]["damage"] and xx["language"] == "xx")
    try:
        game_lang.load("summary.json", "yy", langs)
        out["unknown"] = False
    except ValueError:
        out["unknown"] = True

    templates = FOLDER / "templates"
    (templates / "xx" / "hud").mkdir(parents=True, exist_ok=True)
    (templates / "xx" / "hud" / "spectate.png").write_bytes(b"")
    out["templates"] = (game_lang.template_path("summary.png", "xx", templates) == templates / "summary.png"
                        and game_lang.template_path("hud/spectate.png", "xx", templates) == templates / "xx" / "hud" / "spectate.png"
                        and game_lang.template_path("hud/spectate.png", None, templates) == templates / "hud" / "spectate.png"
                        and game_lang.own_template_path("summary.png", "xx", templates) == templates / "xx" / "summary.png")

    # 段位名：简体照旧；英文的写法换成简体
    out["tiers_zh"] = (ranks.tier_names() == {t: t for t in ranks.TIERS} and parse_tier("钻石II") == ("钻石", "II")
                       and parse_tier("Apex 猎杀者") == ("Apex 猎杀者", None) and current_rank("下一段位:钻石 I") == ("钻石", "II"))
    write_lang(langs, "en", {"name": "English", "configs": {"ranks.json": {"names": EN_TIERS}}})
    ranks.tier_names.cache_clear()
    with mock.patch.object(game_lang, "LANG_DIR", langs):
        en = ranks.tier_names("en")
    ranks.tier_names.cache_clear()
    out["tiers_en"] = (parse_tier("DIAMOND II", en) == ("钻石", "II") and parse_tier("Apex Predator", en) == ("Apex 猎杀者", None)
                       and parse_tier("Diamond", en) == ("钻石", None) and parse_tier("868/1000 RP", en) is None
                       and current_rank("NEXT RANK: Diamond I", en) == ("钻石", "II")
                       and current_rank("Next Rank: Master", en) == ("钻石", "I")
                       and current_rank("Next Rank: Apex Predator", en) == ("大师", None))

    # 地图名：简体照旧（"奧"读成繁体也认）；别的语言按 {写法: 简体名字}
    cfg = load_config("match_start.json")
    en_maps = {**cfg, "maps": {"Olympus": "奥林匹斯", "World's Edge": "世界尽头"}}
    out["maps"] = (closest_map("奧林匹斯", cfg) == "奥林匹斯" and closest_map("OLYMPUS", en_maps) == "奥林匹斯"
                   and closest_map("WORLDS EDGE", en_maps) == "世界尽头" and closest_map("LOBBY", en_maps) is None)
    game_lang._files.clear()
    return out


def calibration_checks():
    """大厅之前最近的那一局没有段位：排位局、不知道模式的局补上大厅读到的段位，匹配局不补。"""
    out = {}
    for mode in ("ranked", None, "pubs"):
        conn = storage.connect(":memory:")
        match_id = storage.insert_match(conn, {"ended_at": "2026-10-24 20:00:00", "mode": mode, "placement": 5})
        reading = {"read_at": "2026-10-24 20:05:00", "player_name": None, "rank_tier": "白金",
                   "rank_division": "II", "rank_progress": 300, "score": 10300}
        storage.save_lobby(conn, reading)
        calibration.calibrate(conn, reading, log=lambda line: None)
        out[mode] = storage.get_match(conn, match_id)["rank_tier"]
        conn.close()
    return out


def detect_checks(images):
    """GameScreens 认语言（用简体的截图，测试语言见 unreadable_screens）。"""
    engine = LazyEngine(SMALL_CROP_PARAMS)
    summary, other = images[SUMMARY], BLACK
    out, said = {}, []

    one = GameScreens(engine, ["zh-CN"])
    out["single"] = one.locked and one.detect(summary) == "summary"

    s = GameScreens(engine, ["xx", "zh-CN"], {"xx": unreadable_screens()}, log=said.append)
    first = (s.lang, s.locked)
    found = s.detect(summary)
    before = s.detectors["xx"].looked_alike
    later = [s.detect(summary), s.detect(other)]
    out["switch"] = (first == ("xx", False) and found == "summary" and (s.lang, s.locked) == ("zh-CN", True)
                     and later == ["summary", None] and s.detectors["xx"].looked_alike == before)
    out["said"] = said

    same = GameScreens(engine, ["zh-CN", "yy"], {"yy": load_config("screens.json")})
    out["ambiguous"] = same.detect(summary) == "summary" and (same.lang, same.locked) == ("zh-CN", False)
    return out


def collect_checks(images):
    """记录程序：一开始按测试语言（总结页的击杀、伤害区域挪到左上角），认出简体的总结页后换成简体的规则。"""
    pictures = {"黑": BLACK, "总结": images[SUMMARY]}
    frames = ["黑", "总结", "总结", "总结", "黑"]

    def summary_row(rows):
        return [(r["placement"], r["kills"], r["assists"], r["knockdowns"], r["damage"]) for r in rows]

    plain, _, _ = run_collect(frames, pictures.get)
    langs = FOLDER / "lang-collect"
    screens = {name: {"keywords": NOWHERE} for name in load_config("screens.json")["screens"]}
    write_lang(langs, "xx", {"name": "测试语言", "configs": {
        "screens.json": {"screens": screens},
        "summary.json": {"fields": {"kda": {"roi": [0, 0, 180, 42]}, "damage": {"roi": [0, 0, 180, 46]}}},
        "ranks.json": {"names": {t: t for t in ranks.TIERS}}}})
    game_lang._files.clear()
    with mock.patch.object(game_lang, "LANG_DIR", langs), mock.patch.object(game_lang, "languages", lambda: ["xx", "zh-CN"]):
        rows, _, log = run_collect(frames, pictures.get)
    game_lang._files.clear()
    return {"plain": summary_row(plain), "switched": summary_row(rows),
            "log": [line for line in log if "游戏语言" in line]}


def main():
    print("游戏语言（规则按语言分开、认出游戏语言）")
    r, cal = rule_checks(), calibration_checks()
    checks = {
        "规则叠法：字典逐项合并、列表整个换掉，不改动简体的规则": r["merge"],
        "简体：读出来就是 config/ 里的文件（多一项 language）": r["default"],
        "认得出的语言：简体排第一，加上 config/lang/ 里的；语言名字从文件里读": r["languages"],
        "别的语言只写不一样的地方，其余照用简体的": r["overlay"],
        "没有这种语言的文件：报错，不悄悄当成简体": r["unknown"],
        "参照图：这种语言有自己的就用它，没有就用简体的；生成时存到这种语言的文件夹": r["templates"],
        "段位名（简体）：和以前一样（钻石II、Apex 猎杀者、由下一段位推当前段位）": r["tiers_zh"],
        "段位名（英文写法）：DIAMOND II、Apex Predator 换成简体；由 Next Rank 推当前段位": r["tiers_en"],
        "地图名：简体照旧；别的语言按 {写法: 简体名字}，不管大小写、空格；都不像就不填": r["maps"],
        f"大厅校准：最近一局没有段位时，排位局和不知道模式的局补上、匹配局不补（{cal}）":
            cal == {"ranked": "白金", None: "白金", "pubs": None},
    }
    try:
        images = {SUMMARY: load(shot(SUMMARY))}
    except MissingShots as e:
        print(f"  ⏭ 用截图的部分跳过：找不到截图 {e}（截图只在开发者电脑上，不在仓库里）")
        return report(checks)
    d, c = detect_checks(images), collect_checks(images)
    checks.update({
        "只有一种语言：一开始就认定": d["single"],
        f"一开始按测试语言，认出简体的总结页（测试语言对不上）就认定简体，之后不再按测试语言比（日志：{d['said']}）":
            d["switch"] and d["said"] == ["游戏语言：简体中文（从总结页认出）"],
        "两种语言的关键字都对得上：画面照样认出，语言先不认定": d["ambiguous"],
        f"记录程序：认定简体后换成简体的规则，总结页读出来和只用简体时一样（{c['switched']}）":
            c["switched"] == c["plain"] and len(c["plain"]) == 1 and c["plain"][0][0] == 6,
        f"记录程序的日志：开头写认得出哪几种语言，认定时写一行（{c['log']}）":
            len(c["log"]) == 2 and c["log"][0] == "认得出的游戏语言：测试语言、简体中文（认出第一个画面时定下来）"
            and c["log"][1].endswith("游戏语言：简体中文（从总结页认出）"),
    })
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
