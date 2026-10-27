"""程序文件和用户数据放在哪里。所有脚本都从这里取路径，不依赖"当前在哪个文件夹里运行"。

- 程序文件（配置 config/、面板网页 panel/）：跟着程序走
  - 用源码运行：项目文件夹
  - 打包后（Evo.exe）：程序文件夹里的 _internal
- 用户数据（数据库 data/、截图 captures/、日志 logs/、设置 settings.json），按顺序（resolve_data_dir）：
  - 设置了环境变量 EVO_DATA_DIR：用它（测试时用，免得碰到真实数据）
  - 用源码运行：项目文件夹（开发时和以前一样）
    例外：用源码运行 evo_app.py、这台电脑上装过 Evo 时，用安装版的数据文件夹（见 source_run.py，靠 EVO_DATA_DIR）
  - 打包后：
    1. Evo.exe 旁边的 data_location.txt 里写的文件夹（install.py 安装时、设置页"更改位置"时写）
    2. "指路文件" %LOCALAPPDATA%\\Evo\\data_location.txt 里写的文件夹（要存在）：数据不在 C 盘默认位置时都写一份。
       玩家照使用说明"删掉旧程序文件夹、解压新的"换版本时，第 1 个会被一起删掉，靠它还能找到数据
    3. %LOCALAPPDATA%\\Evo 里有数据库：0.3.4 及以前的默认位置，先接着用，Evo 会自动搬到程序旁边（见 data_move.py）
    4. 程序旁边的 Evo Tracker Data（2026-10-20 用户决定的默认位置，如程序在 D:\\Evo，数据在 D:\\Evo Tracker Data）。
       程序在临时文件夹里（在压缩包里直接双击打开）或这里写不进去（如 C:\\Program Files）：用 %LOCALAPPDATA%\\Evo
- 截图 captures/ 一般在数据文件夹里；设置了环境变量 EVO_CAPTURES_DIR 时用它
  （用源码运行 evo_app.py 时设成项目的 captures/，见 source_run.py）
数据库里记的截图路径是相对于 DATA_DIR 的，整个数据文件夹搬家后照样能找到（不在数据文件夹里的截图记完整路径）。
"""

import os
import sys
import tempfile
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)  # 是不是打包后的 Evo.exe
APP_DIR = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent.parent  # 源码：本文件在 code/ 里，上一层是项目文件夹
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else APP_DIR  # Evo.exe 所在的文件夹
DATA_NAME = "Evo Tracker Data"  # 程序旁边的默认位置、设置页里自己选的位置，都叫这个名字（2026-10-20 用户决定）
POINTER_NAME = "data_location.txt"
DATA_LOCATION_FILE = EXE_DIR / POINTER_NAME
LOCAL_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Evo"  # 0.3.4 及以前的默认位置；现在放"指路文件"


def read_pointer(path):
    """读一个 data_location.txt，返回里面写的文件夹；没有这个文件（或是空的）返回 None。"""
    try:
        text = Path(path).read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    if not text:
        return None
    target = Path(text)
    return target if target.is_absolute() else (Path(path).resolve().parent / target).resolve()


def has_data(folder):
    """这个文件夹里有没有 Evo 的数据库。"""
    return (Path(folder) / "data" / "evo.db").is_file()


def _in_temp(path):
    try:
        Path(path).resolve().relative_to(Path(tempfile.gettempdir()).resolve())
        return True
    except ValueError:
        return False


def default_data_dir(exe_dir=EXE_DIR):
    """程序旁边的 Evo Tracker Data（如 D:\\Evo → D:\\Evo Tracker Data）。
    程序在临时文件夹里（在压缩包里直接双击）、在磁盘根目录、或者那里写不进去：返回 None。"""
    exe_dir = Path(exe_dir)
    if exe_dir.parent == exe_dir or _in_temp(exe_dir):
        return None
    folder = exe_dir.parent / DATA_NAME
    if folder.is_dir():
        return folder
    try:
        folder.mkdir()
        return folder
    except OSError:
        return None


def resolve_data_dir(environ=None, frozen=FROZEN, exe_dir=EXE_DIR, local_dir=LOCAL_DIR, app_dir=APP_DIR):
    """数据文件夹在哪里，返回 (文件夹, 来源)。来源："env" 环境变量 / "project" 用源码运行 / "program" 程序旁边的
    data_location.txt / "pointer" 指路文件 / "legacy" C 盘旧的默认位置（有数据，等着搬）/ "default" 程序旁边的
    Evo Tracker Data / "local" 程序旁边用不了，只好用 C 盘。参数都是测试时替换用的。"""
    environ = os.environ if environ is None else environ
    if environ.get("EVO_DATA_DIR"):
        return Path(environ["EVO_DATA_DIR"]).resolve(), "env"
    if not frozen:
        return Path(app_dir), "project"
    found = read_pointer(Path(exe_dir) / POINTER_NAME)
    if found is not None:
        return found, "program"
    found = read_pointer(Path(local_dir) / POINTER_NAME)
    if found is not None and found.is_dir():
        return found, "pointer"
    if has_data(local_dir):
        return Path(local_dir), "legacy"
    folder = default_data_dir(exe_dir)
    return (folder, "default") if folder is not None else (Path(local_dir), "local")


DATA_DIR, DATA_SOURCE = resolve_data_dir()

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
