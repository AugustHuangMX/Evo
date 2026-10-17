"""记录时存下的截图留不留（原则 2"本地优先"：识别完就删，只留"待确认"的局要用的截图）。

- 默认"识别完就删"：大厅、开局画面不存；总结页、经验值页先存一张，一局彻底结束时（下一局开局，或停止记录），
  不需要确认的删掉、数据库里的截图位置清空；"待确认"的局留着，在战绩页对着改
  （为什么要等一局结束：总结页常常名次还没滑进来就先存下、标待确认，过一会儿经验值页补上名次才取消待确认）
- "保留全部截图"（数据文件夹 settings.json 里的 keep_all_screenshots）：全部留着，和以前一样，用来攒测试集
- 截图存在项目的 captures/ 时（用源码运行；源码运行 evo_app.py 时战绩记进安装版、截图仍存进项目，见 source_run.py）：
  不管开关，永远保留全部——项目里的 captures/ 就是测试集
- 只删这次记录的文件夹里的截图，以前存下的不动
- 战绩页确认（保存）或删除一局时，删掉这一局的截图（discard）
- 设置页"删除已识别完的截图"按钮（clean_up）：以前存下的也按同样的规则删，正在记录的文件夹不动
"""

import multiprocessing

import app_settings
import storage
from paths import APP_DIR, CAPTURES_DIR, FROZEN, data_absolute

KEEP_ALL_KEY = "keep_all_screenshots"
PROJECT_CAPTURES = APP_DIR / "captures"  # 用源码运行时项目里的截图文件夹（测试集）


def in_project():
    """截图存在项目的 captures/（用源码运行）：那里是测试集，一张都不删。"""
    return not FROZEN and CAPTURES_DIR.resolve() == PROJECT_CAPTURES.resolve()


def keep_all():
    """这次记录要不要保留全部截图。"""
    if in_project():
        return True
    return bool(app_settings.get(KEEP_ALL_KEY, False))


def removable(conn, folders):
    """这些文件夹里可以删的截图：除了"待确认"的局要用的，全部。"""
    needed = {data_absolute(row[0]).resolve() for row in conn.execute(
        "SELECT evidence_path FROM matches WHERE needs_review AND evidence_path IS NOT NULL")}
    return [path for folder in folders for path in sorted(folder.glob("*.png")) if path.resolve() not in needed]


def _delete(conn, folders):
    """删掉这些文件夹里可以删的截图；不待确认的局，截图在这些文件夹里的，数据库里的截图位置清空
    （战绩页就不再显示"识别截图"）。删不掉的（比如正被别的程序打开）留着。返回 (删了几张, 腾出多少字节)。"""
    folders = {folder.resolve() for folder in folders}
    for row in conn.execute("SELECT match_id, evidence_path FROM matches "
                            "WHERE NOT needs_review AND evidence_path IS NOT NULL").fetchall():
        if data_absolute(row["evidence_path"]).resolve().parent in folders:
            storage.update_match(conn, row["match_id"], {"evidence_path": None})
    deleted = freed = 0
    for path in removable(conn, folders):
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        deleted += 1
        freed += size
    return deleted, freed


def tidy(conn, folder):
    """一局彻底结束时调用：folder（这次记录的截图文件夹）里，除了"待确认"的局要用的截图，全部删掉。
    删不掉的下一局结束时再试。返回 (删了几张, 腾出多少字节)。"""
    return _delete(conn, [folder])


def recording_folder():
    """正在记录的截图文件夹，没在记录返回 None。
    记录子程序（evo_app.py 里名叫 evo-collect 的进程）和面板都在托盘程序里，所以面板看得到它在不在运行；
    在运行时，它的文件夹就是 captures/live/ 里最新的那个（文件夹名是开始记录的时间）。
    单独运行面板（python panel_app.py）时看不到，当作没在记录。"""
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
    """设置页"删除已识别完的截图"：以前存下的截图也按 tidy() 的规则删（只留"待确认"的局要用的），
    删空的文件夹也删掉；正在记录的文件夹不动。用源码运行时一张都不删。返回 (删了几张, 腾出多少字节)。"""
    if in_project():
        return 0, 0
    folders = finished_folders(skip=recording_folder())
    result = _delete(conn, folders)
    for folder in folders:
        try:
            folder.rmdir()  # 只有空文件夹才删得掉
        except OSError:
            pass
    return result


def cleanable(conn):
    """设置页显示用：按 clean_up() 的规则，能删几张、能腾出多少字节（不真的删）。"""
    if in_project():
        return 0, 0
    sizes = []
    for path in removable(conn, finished_folders(skip=recording_folder())):
        try:
            sizes.append(path.stat().st_size)
        except OSError:  # 刚好被删了
            pass
    return len(sizes), sum(sizes)


def recorded(evidence_path):
    """是不是记录程序存的截图（captures/live/ 里的）。只有这种会被删；演示数据用的样本截图等不碰。"""
    if not evidence_path:
        return False
    return (CAPTURES_DIR / "live").resolve() in data_absolute(evidence_path).resolve().parents


def discard(conn, row):
    """战绩页确认（保存）或删除一局时调用：删掉这一局的截图。row：这一局（storage.get_match 读出的）。
    保留全部截图时不删；只删记录程序存的截图（captures/live/ 里的，演示数据用的样本截图等不碰）；
    别的局也在用的不删。删了（或者文件本来就不在了）返回 True，调用的地方再把数据库里的截图位置清空。"""
    if keep_all() or row is None or not recorded(row["evidence_path"]):
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
