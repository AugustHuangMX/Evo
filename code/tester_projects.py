"""测试工具（evo_tester.py）的测试项目和测试员档案。

- 测试项目写在 config/tester.json（原则 5：给测试员看的文字、截法、要确认什么都在里面），窗口里按顺序列出来
- 测试员档案（昵称、游戏语言、启动项）：第一次打开时填，存在 exe 旁边的 tester_profile.json，以后不再问
  （2026-11-02 用户定的）。exe 旁边写不进去（如放在 C:\\Program Files）就存在 %LOCALAPPDATA%\\Evo；用源码运行时存在项目的 data/
- 压缩包的名字：Evo测试_<项目>_<昵称>_<时间>

不 import paths.py（它一 import 就会在程序旁边建"Evo Tracker Data"文件夹）。
"""

import json
import os
import re
import sys
from pathlib import Path

PROFILE_NAME = "tester_profile.json"
PROFILE_FIELDS = ("nickname", "game_language", "launch_options")  # 昵称、游戏语言、启动项
LOCAL_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Evo"  # 同 paths.LOCAL_DIR


def config_dir():
    """config 文件夹：打包成 exe 时在 exe 里面（PyInstaller 运行时解到临时文件夹），用源码运行时在项目文件夹。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "config"
    return Path(__file__).resolve().parent.parent / "config"


def load_config(name, folder=None):
    return json.loads((Path(folder or config_dir()) / name).read_text(encoding="utf-8"))


def load_projects(folder=None):
    """{项目编号: 项目}，按 config/tester.json 里的顺序；每一项多一个 "key"。"""
    projects = load_config("tester.json", folder)["projects"]
    return {key: {**spec, "key": key} for key, spec in projects.items()}


def languages(folder=None):
    return load_config("tester.json", folder)["languages"]


def weapon_list(folder=None):
    """靶场的武器名单：[(编号, 中文名, 英文名)]，按 config/weapons.json 里的顺序。"""
    return [(key, w.get("zh", key), w.get("en", key)) for key, w in load_config("weapons.json", folder)["weapons"].items()]


def profile_dirs():
    """档案可以放的地方，先试前面的：exe 旁边（用源码运行时是项目的 data/）、%LOCALAPPDATA%\\Evo。"""
    if getattr(sys, "frozen", False):
        first = Path(sys.executable).resolve().parent
    else:
        first = Path(__file__).resolve().parent.parent / "data"
    return [first, LOCAL_DIR]


def load_profile(dirs=None):
    """读档案：{"nickname", "game_language", "launch_options"}；没填过返回 {}。"""
    for folder in dirs or profile_dirs():
        try:
            data = json.loads((Path(folder) / PROFILE_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return {key: str(data.get(key) or "").strip() for key in PROFILE_FIELDS}
    return {}


def save_profile(profile, dirs=None):
    """存档案，返回存在哪个文件；哪里都写不进去返回 None（不影响测试，只是下次还要再填）。"""
    data = {key: str(profile.get(key) or "").strip() for key in PROFILE_FIELDS}
    for folder in dirs or profile_dirs():
        path = Path(folder) / PROFILE_NAME
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return path
        except OSError:
            continue
    return None


def _clean(text, limit=20):
    """放进文件名的文字：去掉 Windows 文件名里不能用的字符和空白，最长 limit 个字。"""
    return re.sub(r'[\\/:*?"<>|\s]+', "", text or "")[:limit]


def package_name(project, nickname, stamp):
    """压缩包（和打包前的文件夹）的名字：Evo测试_打一局_昵称_20261102_213000。没填昵称就不写。"""
    parts = ["Evo测试", _clean(project["name"].replace("：", "-")), _clean(nickname), f"{stamp:%Y%m%d_%H%M%S}"]
    return "_".join(p for p in parts if p)
