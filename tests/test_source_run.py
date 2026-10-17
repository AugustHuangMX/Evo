"""用源码运行 evo_app.py 时数据放哪里（source_run.py）：战绩记进安装版的数据文件夹，截图全部存进项目的 captures/。

- 什么时候改：有 installed_data_location.txt（install.py 写的）、没设 EVO_DATA_DIR、不是打包版，而且是直接运行 evo_app.py
- 改了以后（在另一个 Python 进程里看，因为 paths.py 一导入就决定路径）：数据库在安装版的数据文件夹，截图在项目里、保留全部
- 启动前备份正式数据库，只留最近几份
全部用临时文件夹，不碰正式数据，也不往项目的 captures/ 里写东西。

运行：python tests/test_source_run.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fake_game import report  # 要在项目代码之前导入（设置临时数据文件夹）

import evo_app  # noqa: E402
import source_run  # noqa: E402
import storage  # noqa: E402

PROJECT = source_run.PROJECT_DIR


def redirect_checks(folder):
    """use_installed_data()：什么时候改、改成什么。"""
    installed = folder / "Evo Tracker Data"
    pointer, empty, missing = folder / "pointer.txt", folder / "empty.txt", folder / "missing.txt"
    pointer.write_text(f"{installed}\n", encoding="utf-8")
    empty.write_text("\n", encoding="utf-8")

    def run(environ, path, frozen=False):
        result = source_run.use_installed_data(environ, path, frozen)
        return result, environ

    changed, env = run({}, pointer)
    return {
        "装过 Evo（有 installed_data_location.txt）：数据用安装版的数据文件夹，截图用项目的 captures/":
        changed == installed and env == {"EVO_DATA_DIR": str(installed), "EVO_CAPTURES_DIR": str(PROJECT / "captures")},
        "没装过（没有这个文件）：不改，和以前一样": run({}, missing) == (None, {}),
        "文件是空的：不改": run({}, empty) == (None, {}),
        "已经设了 EVO_DATA_DIR（比如测试）：不改": run({"EVO_DATA_DIR": "X"}, pointer) == (None, {"EVO_DATA_DIR": "X"}),
        "打包版（Evo.exe）：不改": run({}, pointer, frozen=True) == (None, {}),
        "被别的代码 import 时不改（只有直接运行 evo_app.py 才改）": imported_evo_app() == [None, str(PROJECT), None],
    }


def imported_evo_app():
    """在另一个 Python 进程里 import evo_app（不设 EVO_DATA_DIR，和平时用源码运行一样），看数据文件夹改了没有。
    这台电脑装过 Evo 时项目里有 installed_data_location.txt，所以这一项真能看出有没有乱改。只读，不写文件。"""
    code = ("import json, os, evo_app, paths; "
            "print(json.dumps([evo_app.INSTALLED_DATA, str(paths.DATA_DIR), os.environ.get('EVO_DATA_DIR')], default=str))")
    env = {k: v for k, v in os.environ.items() if k not in ("EVO_DATA_DIR", "EVO_CAPTURES_DIR")}
    out = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, env={**env, "PYTHONIOENCODING": "utf-8"},
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


PROBE = """
import json, paths, screenshots
print(json.dumps({"data": str(paths.DATA_DIR), "db": str(paths.DB_PATH), "captures": str(paths.CAPTURES_DIR),
                  "in_project": screenshots.in_project(), "keep_all": screenshots.keep_all(),
                  "shot_path": paths.data_relative(paths.CAPTURES_DIR / "live" / "x" / "01.png")}))
"""


def probe(environ):
    """在另一个 Python 进程里看路径（paths.py 一导入就决定，同一个进程里改不了）。只读，不写文件。"""
    env = {k: v for k, v in os.environ.items() if k not in ("EVO_DATA_DIR", "EVO_CAPTURES_DIR")}
    env.update(environ, PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=PROJECT, env=env, capture_output=True, text=True,
                         encoding="utf-8", check=True)
    return json.loads(out.stdout)


def paths_checks(folder):
    """改了以后各个路径在哪里。"""
    installed = folder / "Evo Tracker Data"
    env = {}
    source_run.use_installed_data(env, _pointer(folder, installed))
    on = probe(env)
    off = probe({"EVO_DATA_DIR": str(installed)})  # 只设数据文件夹（测试的做法）：截图跟着数据文件夹
    return {
        "源码运行 evo_app.py：数据库在安装版的数据文件夹": Path(on["db"]) == installed.resolve() / "data" / "evo.db",
        "源码运行 evo_app.py：截图在项目的 captures/，保留全部": Path(on["captures"]) == PROJECT / "captures"
        and on["in_project"] is True and on["keep_all"] is True,
        "源码运行 evo_app.py：截图不在数据文件夹里，数据库里记完整路径（安装版的战绩页也找得到）":
        Path(on["shot_path"]).is_absolute(),
        "只设 EVO_DATA_DIR：截图跟着数据文件夹，不算项目里的测试集": Path(off["captures"]) == installed.resolve() / "captures"
        and off["in_project"] is False,
    }


def _pointer(folder, installed):
    path = folder / "installed_data_location.txt"
    path.write_text(f"{installed}\n", encoding="utf-8")
    return path


def backup_checks(folder):
    """启动前备份正式数据库，只留最近几份。"""
    db = folder / "data" / "evo.db"
    db.parent.mkdir(parents=True)
    conn = storage.connect(db)
    storage.insert_match(conn, {"ended_at": "2026-10-17 21:00:00", "placement": 3})
    conn.close()
    older = [db.with_name(f"evo.db.bak-source-20200101-00000{i}") for i in range(6)]
    for path in older:
        path.write_bytes(b"old")
    logs = []
    with mock.patch.object(evo_app, "DB_PATH", db):
        backup = evo_app.backup_before_source_run(logs.append)
    with mock.patch.object(evo_app, "DB_PATH", folder / "nothing" / "evo.db"):
        nothing = evo_app.backup_before_source_run(logs.append)
    left = sorted(db.parent.glob("evo.db.bak-source-*"))
    copy = sqlite3.connect(backup)
    try:
        rows = copy.execute("SELECT placement FROM matches").fetchall()
    finally:
        copy.close()
    return {
        "启动前备份：备份里有原来的战绩": rows == [(3,)] and "已备份" in logs[0],
        f"启动前备份：只留最近 {evo_app.SOURCE_BACKUPS} 份（删掉最旧的）": len(left) == evo_app.SOURCE_BACKUPS
        and backup in left and not older[0].exists() and not older[1].exists(),
        "还没有数据库：不备份": nothing is None,
    }


def main():
    print("源码运行 evo_app.py：战绩记进安装版，截图存进项目")
    folder = Path(tempfile.mkdtemp(prefix="evo-source-", dir=storage.DB_PATH.parent.parent))
    checks = redirect_checks(folder)
    checks.update(paths_checks(folder))
    checks.update(backup_checks(folder))
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() is not False else 1)
