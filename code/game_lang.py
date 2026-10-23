"""游戏语言：Evo 认得出哪几种语言的游戏画面，每种语言的识别规则和参照图在哪。

- 简体中文（zh-CN）是基准：规则就是 config/ 里的各个文件，参照图在 config/templates/
- 别的语言一个文件：config/lang/<语言>.json，只写和简体不一样的地方（关键字、地图名、段位名、区域……），
  读规则时叠在简体的规则上（_merge：字典逐项合并，列表、数字、文字整个换掉）。参照图放 config/templates/<语言>/，
  同名的那张换掉简体的；没有的（传奇头像、右上角的图标等不带文字的）照用简体的
- 读出来的地图名、段位名、模式统一换成简体存（"Olympus" → "奥林匹斯"），数据库和面板不用分语言
- 游戏开着时没法换语言，所以记录程序每开一次游戏只认一次是哪种语言（见 screens.GameScreens）

- 设置页"游戏语言"（settings.json 里的 game_language）：自动识别（默认）/ 某一种语言（只按它认，不再自己判断）。
  设置页列出 KNOWN 里的几种，还没有规则文件的显示但不能选（2026-10-24 用户决定）；记录程序每隔几秒看一眼设置

加一种语言：截好这种语言的各个画面，写 config/lang/<语言>.json，生成参照图
（python code/screens.py --lang <语言> --save-template ...），跑 python tests/run_all.py。

只用标准库：面板会经 ranks.py 导入它，不能带上识别引擎（见 tests/test_light.py）。
"""

import copy
import json
from pathlib import Path

import app_settings
from paths import CONFIG_DIR

DEFAULT = "zh-CN"
DEFAULT_NAME = "简体中文"
KNOWN = {DEFAULT: DEFAULT_NAME, "zh-TW": "繁體中文", "en": "English"}  # 设置页列出的（有没有做好看 languages()）
LANG_DIR = CONFIG_DIR / "lang"
TEMPLATE_DIR = CONFIG_DIR / "templates"
SETTING_KEY = "game_language"
AUTO = "auto"
CHECK_SECONDS = 5  # 记录程序每隔几秒看一眼设置页的"游戏语言"

_files = {}  # config/lang/<语言>.json 的路径 → 内容（读一次就留着；没有这个文件为 None）


def _lang_file(lang, lang_dir=None):
    path = Path(lang_dir or LANG_DIR) / f"{lang}.json"
    key = str(path)
    if key not in _files:
        _files[key] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    return _files[key]


def languages(lang_dir=None):
    """Evo 认得出的语言：简体排第一，其余按 config/lang/ 里的文件名排。"""
    folder = Path(lang_dir or LANG_DIR)
    others = sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []
    return [DEFAULT] + [lang for lang in others if lang != DEFAULT]


def label(lang, lang_dir=None):
    """语言的名字（写日志、设置页用）：简体中文、繁體中文、English。"""
    if lang in (None, DEFAULT):
        return DEFAULT_NAME
    data = _lang_file(lang, lang_dir)
    return (data or {}).get("name", KNOWN.get(lang, lang))


def choice():
    """设置页选的游戏语言：AUTO（自动识别），或者一种有规则文件的语言（选过、后来规则文件没了的，当作 AUTO）。"""
    value = app_settings.get(SETTING_KEY, AUTO)
    return value if value in languages() else AUTO


def options():
    """设置页"游戏语言"下拉框里的语言：[(语言, 名字, 做好了没有)]。KNOWN 里的几种都列出来（还没做好的显示但不能选），
    config/lang/ 里有、KNOWN 里没有的（社区加的）排在后面。"""
    ready = languages()
    return [(lang, label(lang), lang in ready) for lang in [*KNOWN, *(x for x in ready if x not in KNOWN)]]


def _merge(base, over):
    """把 over 叠在 base 上：两边都是字典就逐项合并，其他（列表、数字、文字）整个换成 over 的。"""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for key, value in over.items():
            out[key] = _merge(base[key], value) if key in base else copy.deepcopy(value)
        return out
    return copy.deepcopy(over)


def load(name, lang=None, lang_dir=None):
    """读一个识别规则文件（如 "summary.json"），叠上这种语言的不同之处。lang 为 None 时是简体。
    返回的字典里多一项 "language"：这份规则是哪种语言的（读段位名等要用，见 ranks.tier_names）。"""
    cfg = json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))
    lang = lang or DEFAULT
    if lang != DEFAULT:
        data = _lang_file(lang, lang_dir)
        if data is None:
            raise ValueError(f"没有这种语言的识别规则：{Path(lang_dir or LANG_DIR) / (lang + '.json')}")
        cfg = _merge(cfg, data.get("configs", {}).get(name, {}))
    cfg["language"] = lang
    return cfg


def template_path(relative, lang=None, template_dir=None):
    """参照图的路径（relative 如 "summary.png"、"hud/spectate.png"）：这种语言有自己的那张就用它，否则用简体的。"""
    folder = Path(template_dir or TEMPLATE_DIR)
    if lang not in (None, DEFAULT):
        own = folder / lang / relative
        if own.is_file():
            return own
    return folder / relative


def own_template_path(relative, lang=None, template_dir=None):
    """生成参照图时存到哪里：简体存 config/templates/，别的语言存 config/templates/<语言>/。"""
    folder = Path(template_dir or TEMPLATE_DIR)
    return folder / relative if lang in (None, DEFAULT) else folder / lang / relative


def aliases(names):
    """画面上的写法 → 存进数据库的简体名字。names 可以是列表（写法就是简体名字，如简体的地图名单），
    也可以是字典 {"Olympus": "奥林匹斯"}（别的语言）。"""
    return {n: n for n in names} if isinstance(names, list) else dict(names)
