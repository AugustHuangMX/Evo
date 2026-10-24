"""记录时存下的截图留不留（原则 2"本地优先"：识别完就删，只留总结页截图和"待确认"的局要用的截图）。

- 默认"识别完就删"：大厅、开局画面不存；总结页、经验值页先存一张，一局彻底结束时（下一局开局，或停止记录），
  不需要确认的删掉、数据库里的截图位置清空；"待确认"的局留着，在战绩页对着改
  （为什么要等一局结束：总结页常常名次还没滑进来就先存下、标待确认，过一会儿经验值页补上名次才取消待确认）
- 总结页截图（自己和队友的击杀、伤害、名次那一页，live_collect.SummaryWatcher 存的"第几局_时间.png"）一直留着
  （2026-10-24 用户决定），战绩页随时能看；识别完转成 JPG（质量 85，2560×1440 的约 0.45 MB，PNG 约 2.5 MB），
  数据库里的截图位置跟着改。战绩页确认一局时也留着，删除这一局时才一起删
- "保留全部截图"（数据文件夹 settings.json 里的 keep_all_screenshots）：全部留着，和以前一样，用来攒测试集。
  记录程序每局结束时看一眼这个开关，改了从下一局开始生效（2026-10-17 用户要求）：打开时，刚结束的这一局也不删；
  关掉时，这次记录里之前存下的都不删（以前存下的不自动删），之后的局识别完就删
- 截图存在项目的 captures/ 时（用源码运行；源码运行 evo_app.py 时战绩记进安装版、截图仍存进项目，见 source_run.py）：
  不管开关，永远保留全部——项目里的 captures/ 就是测试集
- 只删这次记录的文件夹里的截图，以前存下的不动
- 战绩页确认（保存）或删除一局时，删掉这一局的截图（discard）
- 设置页"删除已识别完的截图"按钮（clean_up）：以前存下的也按同样的规则删（总结页截图转成 JPG），正在记录的文件夹不动
"""

import multiprocessing
import os
import re
from collections import namedtuple
from pathlib import Path

from PIL import Image  # 托盘程序本来就载入了（托盘图标），转 JPG 不用另外载入 OpenCV

import app_settings
import storage
from paths import APP_DIR, CAPTURES_DIR, FROZEN, data_absolute, data_relative

KEEP_ALL_KEY = "keep_all_screenshots"
PROJECT_CAPTURES = APP_DIR / "captures"  # 用源码运行时项目里的截图文件夹（测试集）
SUMMARY_NAME = re.compile(r"\d{2,}_\d{6}\.(png|jpg)", re.IGNORECASE)  # 总结页截图：第几局_存下的时间，如 01_213331.png
JPG_QUALITY = 85  # 名字、数字和原图看不出差别（2026-10-24 对比过）
JPG_RATIO = 0.18  # 设置页估算能腾出多少用：JPG 大约是 PNG 的这么多
Cleaned = namedtuple("Cleaned", "deleted converted freed")  # 删了几张、几张总结页截图转成了 JPG、腾出多少字节


def is_summary(path):
    """是不是总结页截图（按文件名）。"""
    return bool(path) and SUMMARY_NAME.fullmatch(Path(path).name) is not None


def in_project():
    """截图存在项目的 captures/（用源码运行）：那里是测试集，一张都不删。"""
    return not FROZEN and CAPTURES_DIR.resolve() == PROJECT_CAPTURES.resolve()


def keep_all():
    """这次记录要不要保留全部截图。"""
    if in_project():
        return True
    return bool(app_settings.get(KEEP_ALL_KEY, False))


def removable(conn, folders, protect=frozenset()):
    """这些文件夹里可以删的截图：除了总结页截图、"待确认"的局要用的、protect 里的（绝对路径），全部。"""
    needed = {data_absolute(row[0]).resolve() for row in conn.execute(
        "SELECT evidence_path FROM matches WHERE needs_review AND evidence_path IS NOT NULL")} | set(protect)
    return [path for folder in folders for path in sorted(folder.glob("*.png"))
            if path.resolve() not in needed and not is_summary(path)]


def convertible(folders, protect=frozenset()):
    """这些文件夹里还没转成 JPG 的总结页截图（protect 里的不转）。"""
    return [path for folder in folders for path in sorted(folder.glob("*.png"))
            if is_summary(path) and path.resolve() not in protect]


def to_jpg(path):
    """一张 PNG 转成同名的 JPG，删掉 PNG。返回省下的字节数；转不了、PNG 删不掉（比如正被别的程序打开）返回 None，
    PNG 原样留着，下次再试。"""
    path = Path(path)
    jpg, tmp = path.with_suffix(".jpg"), path.with_name(path.stem + ".jpg.tmp")
    try:
        before = path.stat().st_size
        with Image.open(path) as image:
            image.convert("RGB").save(tmp, "JPEG", quality=JPG_QUALITY, optimize=True)
        os.replace(tmp, jpg)
    except (OSError, ValueError, Image.DecompressionBombError):
        tmp.unlink(missing_ok=True)
        return None
    try:
        path.unlink()
    except OSError:
        jpg.unlink(missing_ok=True)
        return None
    return before - jpg.stat().st_size


def _delete(conn, folders, protect=frozenset()):
    """删掉这些文件夹里可以删的截图，总结页截图转成 JPG（protect 里的都不动）；不待确认的局，截图在这些文件夹里、
    不是总结页截图、又不在 protect 里的，数据库里的截图位置清空（战绩页就不再显示"识别截图"）；转成 JPG 的，
    截图位置改成 JPG。删不掉、转不了的（比如正被别的程序打开）留着。返回 Cleaned。"""
    folders = {folder.resolve() for folder in folders}
    used = {}  # 截图（绝对路径）→ 用它的局
    for row in conn.execute("SELECT match_id, evidence_path, needs_review FROM matches "
                            "WHERE evidence_path IS NOT NULL").fetchall():
        path = data_absolute(row["evidence_path"]).resolve()
        used.setdefault(path, []).append(row["match_id"])
        if not row["needs_review"] and path.parent in folders and path not in protect and not is_summary(path):
            storage.update_match(conn, row["match_id"], {"evidence_path": None})
    deleted = converted = freed = 0
    for path in removable(conn, folders, protect):
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        deleted += 1
        freed += size
    for path in convertible(folders, protect):
        saved = to_jpg(path)
        if saved is None:
            continue
        converted += 1
        freed += saved
        for match_id in used.get(path.resolve(), []):
            storage.update_match(conn, match_id, {"evidence_path": data_relative(path.with_suffix(".jpg"))})
    return Cleaned(deleted, converted, freed)


def tidy(conn, folder, protect=frozenset()):
    """一局彻底结束时调用：folder（这次记录的截图文件夹）里，除了总结页截图（转成 JPG）和"待确认"的局要用的截图，
    全部删掉。protect：不动的截图（绝对路径）——这次记录中途关掉"保留全部截图"时，关之前存下的。
    删不掉、转不了的下一局结束时再试。返回 Cleaned。"""
    return _delete(conn, [folder], protect)


def recording_folder():
    """正在记录的截图文件夹，没在记录返回 None。
    记录子程序（evo_app.py 里名叫 evo-collect 的进程）和面板都在托盘程序里，所以面板看得到它在不在运行；
    在运行时，它的文件夹就是 captures/live/ 里最新的那个（文件夹名是开始记录的时间）。
    单独运行面板（python code/panel_app.py）时看不到，当作没在记录。"""
    if not any(p.name == "evo-collect" for p in multiprocessing.active_children()):
        return None
    folders = finished_folders()
    return folders[-1] if folders else None


def finished_folders(skip=None):
    """captures/live/ 里的截图文件夹，按开始记录的时间排；skip（正在记录的文件夹）不算。"""
    live = CAPTURES_DIR / "live"
    folders = sorted(p for p in live.iterdir() if p.is_dir()) if live.is_dir() else []
    return [p for p in folders if skip is None or p.resolve() != skip.resolve()]


def clean_up(conn):
    """设置页"删除已识别完的截图"：以前存下的截图也按 tidy() 的规则删（留总结页截图，转成 JPG；留"待确认"的局要用的），
    删空的文件夹也删掉；正在记录的文件夹不动。用源码运行时一张都不动。返回 Cleaned。"""
    if in_project():
        return Cleaned(0, 0, 0)
    folders = finished_folders(skip=recording_folder())
    result = _delete(conn, folders)
    for folder in folders:
        try:
            folder.rmdir()  # 只有空文件夹才删得掉
        except OSError:
            pass
    return result


def cleanable(conn):
    """设置页显示用：按 clean_up() 的规则，能删几张、几张总结页截图要转成 JPG、大约能腾出多少字节（不真的删、不真的转；
    转 JPG 省下的按 JPG_RATIO 估算）。返回 Cleaned。"""
    if in_project():
        return Cleaned(0, 0, 0)
    folders = finished_folders(skip=recording_folder())

    def sizes(paths):
        out = []
        for path in paths:
            try:
                out.append(path.stat().st_size)
            except OSError:  # 刚好被删了
                pass
        return out

    deleting, converting = sizes(removable(conn, folders)), sizes(convertible(folders))
    return Cleaned(len(deleting), len(converting), sum(deleting) + round(sum(converting) * (1 - JPG_RATIO)))


def recorded(evidence_path):
    """是不是记录程序存的截图（captures/live/ 里的）。只有这种会被删；演示数据用的样本截图等不碰。"""
    if not evidence_path:
        return False
    return (CAPTURES_DIR / "live").resolve() in data_absolute(evidence_path).resolve().parents


def discard(conn, row, removing=False):
    """战绩页确认（保存）或删除一局时调用：删掉这一局的截图。row：这一局（storage.get_match 读出的）。
    总结页截图只在删除这一局（removing）时删，确认时留着（2026-10-24 用户决定）；
    保留全部截图时不删；只删记录程序存的截图（captures/live/ 里的，演示数据用的样本截图等不碰）；
    别的局也在用的不删。删了（或者文件本来就不在了）返回 True，调用的地方再把数据库里的截图位置清空。"""
    if keep_all() or row is None or not recorded(row["evidence_path"]):
        return False
    if is_summary(row["evidence_path"]) and not removing:
        return False
    shared = conn.execute("SELECT COUNT(*) FROM matches WHERE evidence_path = ? AND match_id != ?",
                          (row["evidence_path"], row["match_id"])).fetchone()[0]
    if shared:
        return False
    try:
        data_absolute(row["evidence_path"]).unlink(missing_ok=True)
    except OSError:  # 比如正被别的程序打开：这次不删，截图位置也留着
        return False
    return True
