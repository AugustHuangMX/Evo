"""Evo 自己的小设置（如"关窗口的提示是否已经显示过"），存在数据文件夹的 settings.json 里。

读不到或文件坏了就当作空设置，不影响程序运行。
"""

import json
import threading

from paths import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"
_lock = threading.Lock()  # 托盘的后台线程和面板会同时写（如检查更新），一次只让一个改，免得互相覆盖


def load():
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(key, default=None):
    return load().get(key, default)


def set_value(key, value):
    with _lock:
        data = load()
        data[key] = value
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
