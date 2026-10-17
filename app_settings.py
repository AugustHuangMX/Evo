"""Evo 自己的小设置（如"关窗口的提示是否已经显示过"），存在数据文件夹的 settings.json 里。

读不到或文件坏了就当作空设置，不影响程序运行。
"""

import json

from paths import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"


def load():
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(key, default=None):
    return load().get(key, default)


def set_value(key, value):
    data = load()
    data[key] = value
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
