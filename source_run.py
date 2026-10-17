"""用源码运行 evo_app.py 时，数据放哪里（2026-10-17 用户决定）。

- 战绩（数据库）、设置、日志：记进安装版的数据文件夹，和安装好的 Evo 用同一个数据库，用源码打的局也在战绩里
- 截图：全部存进项目的 captures/（测试集），保留全部、一张都不删（screenshots.in_project）
- 安装版的数据文件夹在哪里：install.py 安装时写进项目里的 installed_data_location.txt（不提交到仓库）。
  没有这个文件（没在这台电脑上安装过）就和以前一样，数据都在项目文件夹
- 只对直接运行 evo_app.py 有效；panel_app.py、demo_data.py、测试等其他脚本照旧用项目文件夹，不碰正式数据
- 设置了 EVO_DATA_DIR（比如测试用的临时文件夹）时不改
- 同一个数据文件夹只能运行一个 Evo，所以源码版和安装版不能同时开（正好防止同一局被记两遍）

做法：在导入 paths 之前设好环境变量 EVO_DATA_DIR、EVO_CAPTURES_DIR（paths.py 一导入就决定路径），
记录子程序、窗口子程序会继承这两个环境变量。这个文件不能 import 项目里的其他代码。
"""

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
POINTER_FILE = PROJECT_DIR / "installed_data_location.txt"  # install.py 写，内容是安装版的数据文件夹


def installed_data_dir(pointer=POINTER_FILE):
    """install.py 记下的安装版数据文件夹；没有就返回 None。"""
    try:
        text = pointer.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    return Path(text) if text else None


def use_installed_data(environ=None, pointer=POINTER_FILE, frozen=None):
    """用源码运行 evo_app.py 时调用（要在导入 paths 之前）：数据用安装版的数据文件夹，截图存进项目。
    改了返回安装版的数据文件夹，没改返回 None。environ、pointer、frozen 是测试时替换用的。"""
    environ = os.environ if environ is None else environ
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen or environ.get("EVO_DATA_DIR"):
        return None
    data = installed_data_dir(pointer)
    if data is None:
        return None
    environ["EVO_DATA_DIR"] = str(data)
    environ["EVO_CAPTURES_DIR"] = str(PROJECT_DIR / "captures")
    return data
