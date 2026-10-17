"""程序文件和用户数据放在哪里。所有脚本都从这里取路径，不依赖"当前在哪个文件夹里运行"。

- 程序文件（配置 config/、面板网页 panel/）：跟着程序走
  - 用源码运行：项目文件夹
  - 打包后（Evo.exe）：程序文件夹里的 _internal
- 用户数据（数据库 data/、截图 captures/），按顺序：
  - 设置了环境变量 EVO_DATA_DIR：用它（测试时用，免得碰到真实数据）
  - 打包后，Evo.exe 旁边有 data_location.txt：用里面写的文件夹（安装时写入，见 install.py）
  - 打包后，没有这个文件：%LOCALAPPDATA%\\Evo
  - 用源码运行：项目文件夹（开发时和以前一样）
  - 例外：用源码运行 evo_app.py、这台电脑上装过 Evo 时，用安装版的数据文件夹（见 source_run.py，靠 EVO_DATA_DIR）
- 截图 captures/ 一般在数据文件夹里；设置了环境变量 EVO_CAPTURES_DIR 时用它
  （用源码运行 evo_app.py 时设成项目的 captures/，见 source_run.py）
数据库里记的截图路径是相对于 DATA_DIR 的，整个数据文件夹搬家后照样能找到（不在数据文件夹里的截图记完整路径）。
"""

import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)  # 是不是打包后的 Evo.exe
APP_DIR = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent.parent  # 源码：本文件在 code/ 里，上一层是项目文件夹
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else APP_DIR  # Evo.exe 所在的文件夹
DATA_LOCATION_FILE = EXE_DIR / "data_location.txt"


def _data_location():
    """打包后：Evo.exe 旁边的 data_location.txt 里写的数据文件夹；没有这个文件（或是空的）返回 None。"""
    try:
        text = DATA_LOCATION_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    return Path(text) if text else None


if os.environ.get("EVO_DATA_DIR"):
    DATA_DIR = Path(os.environ["EVO_DATA_DIR"]).resolve()
elif FROZEN:
    DATA_DIR = _data_location() or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Evo"
else:
    DATA_DIR = APP_DIR

CONFIG_DIR = APP_DIR / "config"
PANEL_DIR = APP_DIR / "panel"
ASSETS_DIR = APP_DIR / "assets"  # 图标等
DB_DIR = DATA_DIR / "data"
DB_PATH = DB_DIR / "evo.db"
CAPTURES_DIR = (Path(os.environ["EVO_CAPTURES_DIR"]).resolve() if os.environ.get("EVO_CAPTURES_DIR")
                else DATA_DIR / "captures")
LOG_DIR = DATA_DIR / "logs"  # 托盘程序的运行记录（没有终端窗口，出了问题看这里）


def data_relative(path):
    """截图路径存进数据库前：在数据文件夹里的，改成相对路径（如 captures\\live\\...\\01.png）。"""
    path = Path(path)
    try:
        return str(path.resolve().relative_to(DATA_DIR))
    except ValueError:
        return str(path)


def data_absolute(path):
    """从数据库读出的截图路径：相对路径按数据文件夹找。"""
    path = Path(path)
    return path if path.is_absolute() else DATA_DIR / path
