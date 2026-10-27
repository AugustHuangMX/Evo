"""Evo 自己的小设置（如"关窗口的提示是否已经显示过"），存在数据文件夹的 settings.json 里。

读不到或文件坏了就当作空设置，不影响程序运行。
"""

import json
import os
import secrets
import threading

from paths import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"
_lock = threading.RLock()  # 读取也与原子替换串行，避免 Windows 文件共享冲突；set_value 内可重入 load


def load():
    with _lock:
        try:
            value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}


def get(key, default=None):
    return load().get(key, default)


def set_value(key, value):
    with _lock:
        data = load()
        data[key] = value
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        part = SETTINGS_PATH.with_name(SETTINGS_PATH.name + "." + secrets.token_hex(6) + ".tmp")
        try:
            with part.open("x", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(part, SETTINGS_PATH)
        finally:
            part.unlink(missing_ok=True)
