"""程序文件和用户数据放在哪里。所有脚本都从这里取路径，不依赖"当前在哪个文件夹里运行"。

- 程序文件（配置 config/、面板网页 panel/）：跟着程序走
  - 用源码运行：项目文件夹
  - 打包后（Evo.exe）：程序文件夹里的 _internal
- 用户数据（数据库 data/、截图 captures/）：
  - 用源码运行：项目文件夹（开发时和以前一样）
  - 打包后：%LOCALAPPDATA%\\Evo
  - 设置环境变量 EVO_DATA_DIR 可以换到别的文件夹（测试时用，免得碰到真实数据）
数据库里记的截图路径是相对于 DATA_DIR 的，整个数据文件夹搬家后照样能找到。
"""

import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)  # 是不是打包后的 Evo.exe
APP_DIR = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent

if os.environ.get("EVO_DATA_DIR"):
    DATA_DIR = Path(os.environ["EVO_DATA_DIR"]).resolve()
elif FROZEN:
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Evo"
else:
    DATA_DIR = APP_DIR

CONFIG_DIR = APP_DIR / "config"
PANEL_DIR = APP_DIR / "panel"
DB_DIR = DATA_DIR / "data"
DB_PATH = DB_DIR / "evo.db"
CAPTURES_DIR = DATA_DIR / "captures"


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
