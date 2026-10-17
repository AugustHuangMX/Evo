"""记录时存下的截图留不留（原则 2"本地优先"：识别完就删，只留"待确认"的局要用的截图）。

- 默认"识别完就删"：大厅、开局画面不存；总结页、经验值页先存一张，一局彻底结束时（下一局开局，或停止记录），
  不需要确认的删掉、数据库里的截图位置清空；"待确认"的局留着，在战绩页对着改
  （为什么要等一局结束：总结页常常名次还没滑进来就先存下、标待确认，过一会儿经验值页补上名次才取消待确认）
- "保留全部截图"（数据文件夹 settings.json 里的 keep_all_screenshots）：全部留着，和以前一样，用来攒测试集
- 数据文件夹就是项目文件夹时（用源码运行、没设 EVO_DATA_DIR）：不管开关，永远保留全部——项目里的 captures/ 就是测试集
- 只删这次记录的文件夹里的截图，以前存下的不动
- 战绩页确认（保存）或删除一局时，删掉这一局的截图（discard）
"""

import app_settings
import storage
from paths import APP_DIR, CAPTURES_DIR, DATA_DIR, FROZEN, data_absolute

KEEP_ALL_KEY = "keep_all_screenshots"


def keep_all():
    """这次记录要不要保留全部截图。"""
    if not FROZEN and DATA_DIR == APP_DIR:
        return True
    return bool(app_settings.get(KEEP_ALL_KEY, False))


def tidy(conn, folder):
    """一局彻底结束时调用：folder（这次记录的截图文件夹）里，除了"待确认"的局要用的截图，全部删掉；
    不待确认的局，数据库里的截图位置清空（战绩页就不再显示"识别截图"）。
    删不掉的（比如正被别的程序打开）留着，下一局结束时再试。返回 (删了几张, 腾出多少字节)。"""
    folder = folder.resolve()
    needed = set()
    for row in conn.execute("SELECT match_id, needs_review, evidence_path FROM matches "
                            "WHERE evidence_path IS NOT NULL").fetchall():
        path = data_absolute(row["evidence_path"]).resolve()
        if path.parent != folder:
            continue
        if row["needs_review"]:
            needed.add(path)
        else:
            storage.update_match(conn, row["match_id"], {"evidence_path": None})
    deleted = freed = 0
    for path in folder.glob("*.png"):
        if path in needed:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        deleted += 1
        freed += size
    return deleted, freed


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
