"""WebDAV 同步（webdav_sync.py、storage.py 的同步列、设置页）。

- 数据库第 9 版：matches / lobby_readings 加 sync_uid、sync_updated，新增 sync_tombstones；旧库自动升级
- 旧数据第一次同步时补编号；同一个数据库复制成两份后编号一样（合并时不会变成两局）
- 快照：每台电脑一个 gzip JSON；导出/读回来；格式不对、坏文件跳过
- 合并：只加、只更新；两边都改了后改的为准（时间一样时结果固定）；删除用墓碑，墓碑优先；
  队友、各来源读数、武器跟着一起；大厅读数、账号名；本机有的截图路径不被远端空的/不存在的路径覆盖
- WebDAV：建文件夹、上传下载、ETag 缓存（没变化不重传）、密码错、地址错、服务器出错
- 两台电脑 + 新电脑：双向合并、编辑传播、删除传播、缓存、自己的文件被删掉后重新上传、坏文件跳过
- 自动同步线程：开启了才会跑
- 设置页：保存、测试连接、立即同步、自动同步开关、断开、清空数据的提醒、密码不回显

全部用本机的小 WebDAV 服务器，不联网。运行：python tests/test_webdav_sync.py
"""

import atexit
import base64
import csv
import copy
import email.utils
import gzip
import hashlib
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape
from unittest.mock import patch

# 这里不用 fake_game：它要加载 dxcam / 识别引擎，WebDAV 同步不需要。只照它那样先设好临时数据文件夹，
# 再导入项目代码（paths.py 一导入就决定数据文件夹）。在 run_all.py 里跑时 fake_game 已经设过了，沿用它的。
_ROOT = Path(__file__).resolve().parent.parent
if "EVO_DATA_DIR" not in os.environ:
    _TEMP = Path(tempfile.mkdtemp(prefix="evo-test-"))
    os.environ["EVO_DATA_DIR"] = str(_TEMP)
    os.environ.pop("EVO_CAPTURES_DIR", None)
    os.environ["EVO_VIDEOCONFIG"] = str(_TEMP / "videoconfig.txt")
    atexit.register(shutil.rmtree, _TEMP, ignore_errors=True)
else:
    _TEMP = Path(os.environ["EVO_DATA_DIR"])
sys.path.insert(0, str(_ROOT / "code"))
sys.stdout.reconfigure(encoding="utf-8")

import app_settings  # noqa: E402
import panel_app  # noqa: E402
import storage  # noqa: E402
import webdav_sync  # noqa: E402

os.environ["no_proxy"] = "127.0.0.1,localhost"  # 电脑上设了代理时，本机的测试服务器也直接连
ROOT = Path(tempfile.mkdtemp(prefix="evo-webdav-", dir=_TEMP))
USER, PASSWORD = "tester", "s3cret-密码"


def report(checks):
    """打印每一项检查的结果（✅ / ❌），全部通过返回 True。"""
    for text, ok in checks.items():
        print(f"  {'✅' if ok else '❌'}  {text}")
    return all(checks.values())


# ---------- 本机的小 WebDAV 服务器 ----------

class DavState:
    def __init__(self):
        self.files = {}  # path -> bytes
        self.modified = {}  # path -> HTTP date
        self.dirs = {"/dav/"}
        self.requests = []  # (method, path)
        self.fail = {}  # (method, path 或 None) -> 状态码


class DavHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state = None

    def log_message(self, *_args):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _path(self):
        return urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)

    def _auth(self):
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode("utf-8")).decode("ascii")
        if self.headers.get("Authorization") != expected:
            self._send(401, headers={"WWW-Authenticate": 'Basic realm="evo"'})
            return False
        return True

    def _failing(self):
        path = self._path()
        code = self.state.fail.get((self.command, path)) or self.state.fail.get((self.command, None))
        if code:
            self._send(code)
            return True
        return False

    def _etag(self, path):
        return '"' + hashlib.md5(self.state.files[path]).hexdigest() + '"'

    def _prop(self, path):
        is_dir = path in self.state.dirs
        if is_dir:
            return ('<d:resourcetype><d:collection/></d:resourcetype>'
                    '<d:getcontentlength>0</d:getcontentlength>')
        data = self.state.files[path]
        return (f'<d:resourcetype/><d:getcontentlength>{len(data)}</d:getcontentlength>'
                f'<d:getetag>{self._etag(path)}</d:getetag>'
                f'<d:getlastmodified>{self.state.modified.get(path, "")}</d:getlastmodified>')

    def do_PROPFIND(self):  # noqa: N802
        self.state.requests.append((self.command, self._path()))
        if not self._auth() or self._failing():
            return
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        path = self._path()
        if path not in self.state.dirs and path not in self.state.files:
            self._send(404)
            return
        items = [path]
        if self.headers.get("Depth", "0") == "1" and path in self.state.dirs:
            prefix = path if path.endswith("/") else path + "/"
            for item in sorted([*self.state.dirs, *self.state.files]):
                if item == path or not item.startswith(prefix):
                    continue
                rest = item[len(prefix):].rstrip("/")
                if rest and "/" not in rest:
                    items.append(item)
        body = ['<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">']
        for item in items:
            href = item if item in self.state.dirs or "/" in item else item
            body.append(f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>"
                        f"{self._prop(item)}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")
        body.append("</d:multistatus>")
        data = "".join(body).encode("utf-8")
        self._send(207, data, {"Content-Type": "application/xml; charset=utf-8"})

    def do_MKCOL(self):  # noqa: N802
        self.state.requests.append((self.command, self._path()))
        if not self._auth() or self._failing():
            return
        path = self._path().rstrip("/") + "/"
        if path in self.state.dirs:
            self._send(405)
            return
        parent = path.rstrip("/").rsplit("/", 1)[0] + "/"
        if parent not in self.state.dirs:
            self._send(409)
            return
        self.state.dirs.add(path)
        self._send(201)

    def do_PUT(self):  # noqa: N802
        self.state.requests.append((self.command, self._path()))
        if not self._auth() or self._failing():
            return
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        path = self._path()
        parent = path.rsplit("/", 1)[0] + "/"
        if parent not in self.state.dirs:
            self._send(409)
            return
        existed = path in self.state.files
        self.state.files[path] = data
        self.state.modified[path] = email.utils.formatdate(time.time(), usegmt=True)
        self._send(204 if existed else 201, headers={"ETag": self._etag(path)})

    def do_GET(self):  # noqa: N802
        self.state.requests.append((self.command, self._path()))
        if not self._auth() or self._failing():
            return
        path = self._path()
        if path not in self.state.files:
            self._send(404)
            return
        self._send(200, self.state.files[path],
                   {"Content-Type": "application/octet-stream", "ETag": self._etag(path)})

    def do_DELETE(self):  # noqa: N802
        self.state.requests.append((self.command, self._path()))
        if not self._auth() or self._failing():
            return
        path = self._path()
        if path in self.state.files:
            del self.state.files[path]
            self.state.modified.pop(path, None)
            self._send(204)
        elif path in self.state.dirs and path != "/dav/":
            prefix = path.rstrip("/") + "/"
            self.state.files = {p: d for p, d in self.state.files.items() if not p.startswith(prefix)}
            self.state.dirs = {p for p in self.state.dirs if p != path and not p.startswith(prefix)}
            self._send(204)
        elif path in self.state.dirs:
            self._send(403)
        else:
            self._send(404)


class Server:
    def __init__(self):
        self.state = DavState()
        handler = type("Handler", (DavHandler,), {"state": self.state})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/dav"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def config(server, folder="Evo", password=PASSWORD, auto=False):
    return {"url": server.base, "username": USER, "password": password, "folder": folder, "auto": auto}


# ---------- 小工具 ----------

def fresh(name):
    folder = ROOT / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    return folder


def open_db(folder):
    return storage.connect(Path(folder) / "data" / "evo.db")


def add_match(conn, ended_at, player_name="玩家甲", **extra):
    data = {"ended_at": ended_at, "started_at": ended_at, "player_name": player_name, "mode": "ranked",
            "map": "世界边缘", "legend": "恶灵", "placement": 3, "squad_kills": 6, "kills": 4, "assists": 1,
            "knockdowns": 5, "participation": 2, "damage": 1234, "survival_time": 900, "revives": 1,
            "respawns": 0, "rp_delta": 56, "rp_source": "经验值页", "rank_tier": "钻石", "rank_division": "II",
            "rank_progress": 868, "needs_review": 0, "evidence_path": "captures/live/x/01.png", **extra}
    mid = storage.insert_match(conn, data)
    storage.save_teammates(conn, mid, [
        {"slot": "left", "name": "队友甲", "player_id": "0011223344", "legend": "命脉", "kills": 2,
         "assists": 1, "knockdowns": 3, "damage": 640, "survival_time": 880, "revives": 1, "respawns": 0},
        {"slot": "right", "name": "队友乙", "player_id": "5566778899", "legend": "地平线", "kills": 0}])
    storage.save_readings(conn, mid, "总结页", {"placement": 3, "kills": 4, "damage": 1234},
                          ended_at, {"placement": 0.98, "kills": 0.95, "damage": 0.99})
    storage.save_weapon_holds(conn, mid, [("r301", 0.0, 300.0), ("wingman", 300.0, 600.0)])
    storage.save_lobby(conn, {"read_at": ended_at, "player_name": player_name, "rank_tier": "钻石",
                              "rank_division": "II", "rank_progress": 868, "score": 14868})
    storage.sync_names(conn)
    return mid


def match_by_uid(conn, uid):
    return conn.execute("SELECT * FROM matches WHERE sync_uid = ?", (uid,)).fetchone()


def set_updated(conn, uid, updated):
    conn.execute("UPDATE matches SET sync_updated = ? WHERE sync_uid = ?", (updated, uid))
    conn.commit()


def snapshot_of(conn, folder):
    return webdav_sync.export_snapshot(conn, data_dir=folder)


def merge(conn, snapshot_or_list, folder):
    snapshots = snapshot_or_list if isinstance(snapshot_or_list, list) else [snapshot_or_list]
    return webdav_sync.merge_snapshots(conn, snapshots, data_dir=folder)


# ---------- 数据库和编号 ----------

def database_checks():
    out = {}
    folder = fresh("db/fresh")
    conn = open_db(folder)
    match_cols = {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
    lobby_cols = {r[1] for r in conn.execute("PRAGMA table_info(lobby_readings)")}
    out["新数据库是第 9 版：两张表有同步列，新增 sync_tombstones"] = (
        conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION == 9
        and {"sync_uid", "sync_updated"} <= match_cols
        and {"sync_uid", "sync_updated"} <= lobby_cols
        and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_tombstones'").fetchone())
    mid = add_match(conn, "2026-10-26 10:00:00")
    row = storage.get_match(conn, mid)
    out["新局有 32 位编号和 UTC 修改时间"] = (
        bool(re.fullmatch(r"[0-9a-f]{32}", row["sync_uid"])) and row["sync_updated"].endswith("Z"))
    before = row["sync_updated"]
    time.sleep(0.01)
    storage.update_match(conn, mid, {"damage": 2000})
    out["改一局会更新 sync_updated"] = storage.get_match(conn, mid)["sync_updated"] > before
    one = storage.insert_match(conn, {"ended_at": "2026-10-26 10:00:00", "damage": 1})
    two = storage.insert_match(conn, {"ended_at": "2026-10-26 10:00:00", "damage": 1})
    out["内容一模一样的两局编号也不同（新局用随机编号）"] = (
        storage.get_match(conn, one)["sync_uid"] != storage.get_match(conn, two)["sync_uid"])
    storage.delete_match(conn, mid)
    out["删掉的局留一条墓碑（同步时让别的电脑也删）"] = any(
        r["kind"] == "match" and r["uid"] == row["sync_uid"] for r in storage.tombstones(conn))
    buf = io.StringIO()
    storage.write_csv(conn, buf)
    buf.seek(0)
    out["CSV 导出还是原来的列（不含同步用的两列）"] = next(csv.reader(buf)) == list(storage.COLUMNS)
    conn.close()

    # 第 8 版的旧数据库：没有同步列、有新表；升级后原来的数据不动
    legacy = fresh("db/legacy")
    db = legacy / "data" / "evo.db"
    db.parent.mkdir(parents=True)
    raw = sqlite3.connect(db)
    cols = ",\n  ".join(f"{k} {v}" for k, v in storage.COLUMNS.items())
    raw.execute(f"CREATE TABLE matches (\n  {cols}\n)")
    raw.execute("""CREATE TABLE lobby_readings (
      reading_id INTEGER PRIMARY KEY AUTOINCREMENT, read_at TEXT NOT NULL, player_name TEXT,
      rank_tier TEXT, rank_division TEXT, rank_progress INTEGER, score INTEGER)""")
    raw.execute(storage.NAME_TABLE)
    raw.execute(storage.MATE_TABLE)
    raw.execute(storage.READING_TABLE)
    raw.execute(storage.WEAPON_TABLE)
    raw.execute("INSERT INTO matches (ended_at, player_name, placement, kills, damage) "
                "VALUES ('2026-10-01 20:00:00', '老玩家', 2, 3, 800)")
    raw.execute("INSERT INTO lobby_readings (read_at, player_name, rank_tier, rank_division, rank_progress, score) "
                "VALUES ('2026-10-01 20:30:00', '老玩家', '钻石', 'II', 100, 13000)")
    raw.execute("PRAGMA user_version = 8")
    raw.commit()
    raw.close()
    conn = storage.connect(db)
    out["第 8 版的旧库自动升级：原来的数据不动，两列都补上"] = (
        conn.execute("PRAGMA user_version").fetchone()[0] == 9
        and tuple(conn.execute("SELECT placement, player_name FROM matches").fetchone()) == (2, "老玩家")
        and {"sync_uid", "sync_updated"} <= {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
        and {"sync_uid", "sync_updated"} <= {r[1] for r in conn.execute("PRAGMA table_info(lobby_readings)")}
        and conn.execute("SELECT COUNT(*) FROM sync_tombstones").fetchone()[0] == 0)
    out["升级后索引也在（编号不会重复）"] = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='matches_sync_uid'").fetchone())
    conn.close()

    # 同一个旧库复制成两份：第一次同步时补的编号要一样
    copy1, copy2 = fresh("db/copy1"), fresh("db/copy2")
    for target in (copy1, copy2):
        (target / "data").mkdir(parents=True)
        shutil.copy2(db, target / "data" / "evo.db")
    c1, c2 = open_db(copy1), open_db(copy2)
    added1, added2 = storage.ensure_sync_ids(c1), storage.ensure_sync_ids(c2)
    uid1 = c1.execute("SELECT sync_uid FROM matches").fetchone()[0]
    uid2 = c2.execute("SELECT sync_uid FROM matches").fetchone()[0]
    out["旧数据补编号：一局 + 一次大厅读数，两份复制出来的编号一样"] = (
        added1 == added2 == 2 and uid1 == uid2 and re.fullmatch(r"[0-9a-f]{32}", uid1 or "") is not None)
    out["再补一次不变（幂等）"] = (storage.ensure_sync_ids(c1) == 0
                                  and c1.execute("SELECT sync_uid FROM matches").fetchone()[0] == uid1)
    c1.close()
    c2.close()
    return out


# ---------- 快照 ----------

def snapshot_checks():
    out = {}
    folder = fresh("snapshot")
    conn = open_db(folder)
    mid = add_match(conn, "2026-10-26 10:00:00")
    snap = snapshot_of(conn, folder)
    out["快照有格式、版本、设备编号和四类数据"] = (
        snap["format"] == webdav_sync.FORMAT and snap["app"] == "Evo" and snap["version"] == storage.APP_VERSION
        and re.fullmatch(r"[0-9a-f]{16}", snap["device"]) is not None and len(snap["matches"]) == 1
        and len(snap["names"]) >= 1 and len(snap["lobby"]) == 1 and snap["tombstones"] == [])
    raw1, raw2 = webdav_sync.encode_snapshot(snap), webdav_sync.encode_snapshot(snap)
    out["同一份数据压出来完全一样（没变化就不重传）"] = raw1 == raw2 and len(raw1) < 20000
    clean, skipped = webdav_sync.decode_snapshot(raw1)
    out["快照能读回来：一局、队友、读数、武器、大厅都在"] = (
        skipped == 0 and len(clean["matches"]) == 1 and len(clean["matches"][0]["mates"]) == 2
        and len(clean["matches"][0]["readings"]) == 3 and len(clean["matches"][0]["weapon_holds"]) == 2
        and len(clean["lobby"]) == 1)
    bad = 0
    for data in (b"not gzip", gzip.compress(b'{"format": 999}', mtime=0), gzip.compress(b"[]", mtime=0)):
        try:
            webdav_sync.decode_snapshot(data)
        except webdav_sync.SyncError:
            bad += 1
    out["坏文件、别的格式、不是对象的 JSON 都报错"] = bad == 3

    # 不认识的字段丢掉；同一个编号的重复记录留更新的那条
    dirty = snapshot_of(conn, folder)
    dirty["matches"][0]["fields"]["不认识的项"] = "不要"
    dirty["matches"][0]["fields"]["damage"] = 5000
    dup = dict(dirty["matches"][0])
    dup["updated"] = "2099-01-01T00:00:00.000000Z"
    dup["fields"] = dict(dup["fields"], damage=9999)
    dirty["matches"].append(dup)
    clean, skipped = webdav_sync.decode_snapshot(webdav_sync.encode_snapshot(dirty))
    out["不认识的字段丢掉，重复的编号留更新的"] = (
        skipped == 0 and len(clean["matches"]) == 1 and clean["matches"][0]["fields"]["damage"] == 9999
        and "不认识的项" not in clean["matches"][0]["fields"])
    conn.close()
    return out


# ---------- 合并 ----------

def merge_checks():
    out = {}
    a_folder, b_folder = fresh("merge/a"), fresh("merge/b")
    a, b = open_db(a_folder), open_db(b_folder)
    mid = add_match(a, "2026-10-26 10:00:00")
    uid = storage.get_match(a, mid)["sync_uid"]
    snap = snapshot_of(a, a_folder)
    stats = merge(b, snap, b_folder)
    row = match_by_uid(b, uid)
    out["空数据库收到一局：字段、队友、读数、武器、大厅都对"] = (
        stats["matches_new"] == 1 and stats["lobby_new"] == 1 and row is not None
        and row["placement"] == 3 and row["damage"] == 1234 and row["sync_uid"] == uid
        and len(storage.teammates_of(b, row["match_id"])) == 2
        and len(storage.readings_of(b, row["match_id"])) == 3
        and len(storage.all_weapon_holds(b)) == 2 and len(storage.lobby_readings(b)) == 1)
    out["队友的 ID、读数、武器的秒数都原样过来"] = (
        storage.teammates_of(b, row["match_id"])[0]["player_id"] == "0011223344"
        and float(storage.all_weapon_holds(b)[0]["start_s"]) == 0.0
        and storage.readings_of(b, row["match_id"], "damage")[0]["score"] == 0.99)

    # 两边都改了：后改的为准
    b_mid = row["match_id"]
    storage.update_match(b, b_mid, {"damage": 2000})
    set_updated(b, uid, "2026-10-26T12:01:00.000000Z")
    a.execute("UPDATE matches SET damage = 1000, sync_updated = ? WHERE sync_uid = ?",
              ("2026-10-26T12:00:00.000000Z", uid))
    a.commit()
    merge(b, snapshot_of(a, a_folder), b_folder)
    out["两边都改了：B 的 12:01 比 A 的 12:00 新，B 保留自己的"] = match_by_uid(b, uid)["damage"] == 2000
    merge(a, snapshot_of(b, b_folder), a_folder)
    out["再合并回 A：A 也变成 B 的值"] = match_by_uid(a, uid)["damage"] == 2000

    # 时间一样、内容不一样：两边算出的赢家要一样
    a.execute("UPDATE matches SET damage = 1111, sync_updated = ? WHERE sync_uid = ?",
              ("2026-10-26T13:00:00.000000Z", uid))
    b.execute("UPDATE matches SET damage = 2222, sync_updated = ? WHERE sync_uid = ?",
              ("2026-10-26T13:00:00.000000Z", uid))
    a.commit()
    b.commit()
    merge(b, snapshot_of(a, a_folder), b_folder)
    merge(a, snapshot_of(b, b_folder), a_folder)
    out["时间一样：两边最后是同一个值（结果固定，不会来回改）"] = (
        match_by_uid(a, uid)["damage"] == match_by_uid(b, uid)["damage"])

    # 队友、武器跟着更新
    storage.update_teammate(a, storage.get_match(a, match_by_uid(a, uid)["match_id"])["match_id"], "left",
                            {"kills": 9})
    set_updated(a, uid, "2026-10-26T14:00:00.000000Z")
    merge(b, snapshot_of(a, a_folder), b_folder)
    out["远端更新后队友的数据跟着换"] = (
        storage.teammates_of(b, match_by_uid(b, uid)["match_id"])[0]["kills"] == 9)

    # 本机截图：远端没有/不存在的路径不覆盖本机
    local_shot = b_folder / "captures" / "live" / "x" / "01.png"
    local_shot.parent.mkdir(parents=True, exist_ok=True)
    local_shot.write_bytes(b"png")
    b.execute("UPDATE matches SET evidence_path = 'captures/live/x/01.png' WHERE sync_uid = ?", (uid,))
    b.commit()
    change = snapshot_of(a, a_folder)
    change["matches"][0]["updated"] = "2026-10-26T15:00:00.000000Z"
    change["matches"][0]["fields"] = dict(change["matches"][0]["fields"],
                                          evidence_path="captures/live/remote/99.png")
    merge(b, change, b_folder)
    out["本机有截图时保留本机的路径（别的电脑上的截图没同步）"] = (
        match_by_uid(b, uid)["evidence_path"] == "captures/live/x/01.png")
    b.execute("UPDATE matches SET evidence_path = 'captures/live/missing.png' WHERE sync_uid = ?", (uid,))
    b.commit()
    change["matches"][0]["updated"] = "2026-10-26T15:01:00.000000Z"
    merge(b, change, b_folder)
    out["本机截图不在了也不采信远端文件路径"] = (
        match_by_uid(b, uid)["evidence_path"] == "captures/live/missing.png")

    # 删除：墓碑优先；删除时间晚/早都以墓碑为准
    storage.delete_match(a, storage.get_match(a, match_by_uid(a, uid)["match_id"])["match_id"])
    merge(b, snapshot_of(a, a_folder), b_folder)
    out["删除同步到另一台：那一局没了，并留下墓碑"] = (
        match_by_uid(b, uid) is None
        and any(r["kind"] == "match" and r["uid"] == uid for r in storage.tombstones(b)))
    newer = dict(change["matches"][0], updated="2099-01-01T00:00:00.000000Z")
    merge(b, {"matches": [newer], "lobby": [], "names": [],
              "tombstones": [{"kind": "match", "uid": uid, "deleted_at": "2026-10-26T16:00:00.000000Z"}]},
          b_folder)
    out["墓碑优先：就算有一份更新的这一局，也不会复活"] = match_by_uid(b, uid) is None

    # 大厅读数：更新、删除
    lobby_uid = storage.lobby_readings(a)[0]["sync_uid"] if storage.lobby_readings(a) else None
    # A 的删掉了这一局但大厅读数还在；B 里也有。改 A 的分数再合并到 B
    lobby_row = storage.lobby_readings(a)[0]
    a.execute("UPDATE lobby_readings SET score = 15000, sync_updated = ? WHERE sync_uid = ?",
              ("2026-10-26T17:00:00.000000Z", lobby_row["sync_uid"]))
    a.commit()
    merge(b, snapshot_of(a, a_folder), b_folder)
    out["大厅读数按编号更新"] = storage.lobby_readings(b)[0]["score"] == 15000
    storage.delete_all_matches(a)
    merge(b, snapshot_of(a, a_folder), b_folder)
    out["清空数据时大厅读数也同步删掉"] = (
        len(storage.lobby_readings(b)) == 0
        and any(r["kind"] == "lobby" for r in storage.tombstones(b)))

    # 账号名：本地已有的分组不动；远端多出来的名字按远端分组加进来
    names_folder = fresh("merge/names")
    names_conn = open_db(names_folder)
    add_match(names_conn, "2026-10-26 18:00:00", player_name="玩家甲")
    add_match(names_conn, "2026-10-26 18:30:00", player_name="玩家甲的小号")
    storage.sync_names(names_conn)
    main_id = names_conn.execute("SELECT account_id FROM player_names WHERE name_key = '玩家甲'").fetchone()[0]
    storage.answer_name(names_conn, "玩家甲的小号", main_id)
    b2_folder = fresh("merge/b2")
    b2 = open_db(b2_folder)
    add_match(b2, "2026-10-26 19:00:00", player_name="玩家甲")
    merge(b2, snapshot_of(names_conn, names_folder), b2_folder)
    names = {r["name_key"]: r for r in b2.execute("SELECT * FROM player_names")}
    out["远端多出来的名字按远端的账号分组加进来，本机已有分组不动"] = (
        "玩家甲的小号" in names and "玩家甲" in names
        and names["玩家甲的小号"]["account_id"] == names["玩家甲"]["account_id"]
        and names["玩家甲的小号"]["confirmed"] == 1
        and "队友甲" not in names)
    merge(b2, {"matches": [], "lobby": [], "tombstones": [],
               "names": [{"name_key": "重复名", "account": "a" * 24, "confirmed": 0},
                         {"name_key": "重复名", "account": "b" * 24, "confirmed": 1}]}, b2_folder)
    duplicate = b2.execute("SELECT * FROM player_names WHERE name_key = '重复名'").fetchall()
    out["同一个名字在两个远端账号里：只留一行，不报错，确认状态合起来"] = (
        len(duplicate) == 1 and duplicate[0]["confirmed"] == 1)
    a.close()
    b.close()
    b2.close()
    names_conn.close()
    return out


# ---------- WebDAV 客户端 ----------

def webdav_checks():
    out = {}
    with Server() as server:
        cfg = config(server, folder="Evo/Backup")
        message = webdav_sync.test_connection(cfg)
        out["测试连接：建好两层文件夹、能上传下载、测试文件删掉"] = (
            "连接成功" in message and "/dav/Evo/Backup/" in server.state.dirs
            and not any(".evo-sync-test" in p for p in server.state.files))
        files, folder_url = webdav_sync.list_remote(cfg)
        out["文件夹空的时候没有同步文件"] = files == [] and folder_url.endswith("/Evo/Backup/")
        folder = fresh("dav")
        conn = open_db(folder)
        add_match(conn, "2026-10-26 10:00:00")
        payload = webdav_sync.encode_snapshot(snapshot_of(conn, folder))
        name = webdav_sync.device_file(folder)
        webdav_sync.upload(cfg, folder_url + name, payload)
        files, _ = webdav_sync.list_remote(cfg)
        out["上传后能看到自己的文件（名字是 evo-16 位编号.json.gz）"] = (
            len(files) == 1 and files[0]["name"] == name and re.fullmatch(r"evo-[0-9a-f]{16}\.json\.gz", name)
            and webdav_sync.download(cfg, files[0]["url"]) == payload)
        cache = webdav_sync._Cache(cfg, folder)
        out["缓存：第一次没有，写进去以后按 ETag 认出没变化"] = (
            not cache.has(files[0]) and cache.write(files[0]["name"], payload, files[0]["etag"],
                                                    files[0]["size"], files[0]["modified"]) is None
            and cache.has(files[0]) and cache.read(files[0]["name"]) == payload)
        changed = dict(files[0], etag='"别的"')
        out["ETag 变了就重新下载"] = not cache.has(changed)

        bad = dict(cfg, password="wrong")
        try:
            webdav_sync.test_connection(bad)
            wrong = None
        except webdav_sync.SyncError as e:
            wrong = str(e)
        out["密码不对：写清楚是用户名/密码的问题"] = bool(wrong and "401" in wrong and "密码" in wrong)
        try:
            webdav_sync.ensure_folder(dict(cfg, url=server.base + "/missing"))
            missing = None
        except webdav_sync.SyncError as e:
            missing = str(e)
        out["服务器地址不对：写清楚找不到地址"] = bool(missing and ("地址不对" in missing or "404" in missing))
        server.state.fail[("PROPFIND", "/dav/Evo/Backup/")] = 500
        try:
            webdav_sync.list_remote(cfg)
            server_error = None
        except webdav_sync.SyncError as e:
            server_error = str(e)
        server.state.fail.clear()
        out["服务器 500：报错，不把远端当成空"] = bool(server_error and "500" in server_error)

        # 断开：本机设置和缓存删掉，服务器上的文件不动
        app_settings.set_value(webdav_sync.SETTINGS_KEY, cfg)
        cache.write(name, payload, "x", len(payload), "")
        cache_dir = webdav_sync._cache_dir(cfg, folder)
        webdav_sync.clear_config(data_dir=folder)
        out["断开同步：删掉本机设置和缓存，服务器上的文件还在"] = (
            app_settings.get(webdav_sync.SETTINGS_KEY) is None and not cache_dir.exists()
            and urllib.parse.urlsplit(folder_url + name).path in server.state.files)
        conn.close()
    return out


# ---------- 两台电脑 + 新电脑 ----------

def sync_checks():
    out = {}
    with Server() as server:
        cfg = config(server)
        a_folder, b_folder, c_folder = fresh("sync/a"), fresh("sync/b"), fresh("sync/c")
        a, b, c = open_db(a_folder), open_db(b_folder), open_db(c_folder)
        a_mid = add_match(a, "2026-10-26 10:00:00")
        b_mid = add_match(b, "2026-10-26 11:00:00", player_name="玩家乙")
        set_updated(b, storage.get_match(b, b_mid)["sync_uid"], "2026-10-25T11:30:00.000000Z")

        first = webdav_sync.sync(conn=a, cfg=cfg, data_dir=a_folder)
        out["第一台同步：上传自己的快照"] = (
            first["uploaded"] and first["matches_new"] == 0 and len(server.state.files) == 1)
        second = webdav_sync.sync(conn=b, cfg=cfg, data_dir=b_folder)
        out["第二台同步：收到第一台的一局，再上传自己的"] = (
            second["matches_new"] == 1 and second["uploaded"] and len(server.state.files) == 2)
        third = webdav_sync.sync(conn=a, cfg=cfg, data_dir=a_folder)
        out["第一台再同步：收到第二台的一局"] = (
            third["matches_new"] == 1 and a.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2)
        out["两台电脑现在有一样的两局（编号相同）"] = (
            {r[0] for r in a.execute("SELECT sync_uid FROM matches")}
            == {r[0] for r in b.execute("SELECT sync_uid FROM matches")})

        # 没有变化：不重复上传
        puts = [r for r in server.state.requests if r[0] == "PUT"]
        again = webdav_sync.sync(conn=a, cfg=cfg, data_dir=a_folder)
        out["没有变化：不重复上传，也不重复下载"] = (
            again["uploaded"] is False and again["downloaded"] == 0
            and len([r for r in server.state.requests if r[0] == "PUT"]) == len(puts))

        # 编辑从 A 传到 B
        uid_b = a.execute("SELECT sync_uid FROM matches WHERE ended_at LIKE '2026-10-26 11%'").fetchone()[0]
        storage.update_match(a, a.execute("SELECT match_id FROM matches WHERE sync_uid = ?",
                                          (uid_b,)).fetchone()["match_id"], {"damage": 4321})
        webdav_sync.sync(conn=a, cfg=cfg, data_dir=a_folder)
        webdav_sync.sync(conn=b, cfg=cfg, data_dir=b_folder)
        out["编辑从 A 同步到 B"] = match_by_uid(b, uid_b)["damage"] == 4321

        # 删除从 B 传到 A
        storage.delete_match(b, match_by_uid(b, uid_b)["match_id"])
        webdav_sync.sync(conn=b, cfg=cfg, data_dir=b_folder)
        webdav_sync.sync(conn=a, cfg=cfg, data_dir=a_folder)
        out["删除从 B 同步到 A"] = match_by_uid(a, uid_b) is None

        # 新电脑：收到全部
        fresh_result = webdav_sync.sync(conn=c, cfg=cfg, data_dir=c_folder)
        out["新电脑（空数据库）同步：收到全部战绩"] = (
            fresh_result["matches_new"] == 1 and c.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1)

        # 自己的文件被删掉：重新上传
        own_name = webdav_sync.device_file(a_folder)
        own_path = next(p for p in server.state.files if p.endswith(own_name))
        del server.state.files[own_path]
        restored = webdav_sync.sync(conn=a, cfg=cfg, data_dir=a_folder)
        out["自己的同步文件被删掉后：会重新上传"] = (
            restored["uploaded"] and any(p.endswith(own_name) for p in server.state.files))

        # 坏文件、别的格式：跳过，不影响别的
        server.state.files["/dav/Evo/evo-0000000000000000.json.gz"] = b"not gzip"
        server.state.files["/dav/Evo/evo-1111111111111111.json.gz"] = gzip.compress(b'{"format": 999}', mtime=0)
        bad = webdav_sync.sync(conn=a, cfg=cfg, data_dir=a_folder)
        out["服务器上有坏文件/别的格式：跳过并说明，其他的照常同步"] = (
            len(bad["bad_files"]) == 2 and bad["matches_new"] == 0
            and a.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1)
        server.state.files.pop("/dav/Evo/evo-0000000000000000.json.gz", None)
        server.state.files.pop("/dav/Evo/evo-1111111111111111.json.gz", None)

        # 没有设置：明确报错
        app_settings.set_value(webdav_sync.SETTINGS_KEY, None)
        try:
            webdav_sync.sync(conn=a, cfg=None)
            no_config = None
        except webdav_sync.SyncError as e:
            no_config = str(e)
        out["没有设置就同步：写清楚要先设置"] = bool(no_config and "还没有设置" in no_config)

        # 自动同步：开了才会跑
        out["自动同步默认关闭，start 不启动线程"] = (
            webdav_sync.start(lambda _m: None, threading.Event()) is None)
        auto_cfg = config(server, folder="AutoTest", auto=True)
        app_settings.set_value(webdav_sync.SETTINGS_KEY, auto_cfg)
        lines, stop = [], threading.Event()

        def log(message):
            lines.append(message)

        thread = webdav_sync.start(log, stop, first_delay=0, interval=60)
        deadline = time.time() + 10
        while not lines and time.time() < deadline:
            time.sleep(0.05)
        stop.set()
        if thread is not None:
            thread.join(5)
        out[f"开了自动同步：后台同步了一次（{lines[0] if lines else '没有日志'}）"] = (
            thread is not None and bool(lines) and "WebDAV 同步" in lines[0])
        # 在设置页刚打开自动同步：不用重启 Evo，马上把后台线程开起来（不会重复开）
        app_settings.set_value(webdav_sync.SETTINGS_KEY, dict(auto_cfg, folder="AutoTest2"))
        late = webdav_sync.ensure_started(lambda _m: None, first_delay=60, interval=60)
        same = webdav_sync.ensure_started(lambda _m: None, first_delay=60, interval=60)
        out["设置页打开自动同步：马上启动后台线程，不会重复开"] = (
            late is not None and late.is_alive() and same is late)
        webdav_sync.stop_auto()
        late.join(2)
        out["设置页关掉自动同步：线程退出"] = not late.is_alive()
        app_settings.set_value(webdav_sync.SETTINGS_KEY, None)
        a.close()
        b.close()
        c.close()
    return out


# ---------- 设置页 ----------

def panel_checks():
    out = {}
    with Server() as server:
        cfg = config(server, folder="Panel")
        db_folder = fresh("panel")
        db_file = db_folder / "data" / "evo.db"
        conn = storage.connect(db_file)
        add_match(conn, "2026-10-26 20:00:00")
        conn.close()
        # 另一台电脑先传一局上去，设置页"立即同步"应该收下来
        other_folder = fresh("panel-other")
        other = open_db(other_folder)
        add_match(other, "2026-10-26 21:00:00", player_name="玩家乙")
        webdav_sync.sync(conn=other, cfg=cfg, data_dir=other_folder)
        other.close()

        old_db, old_testing = panel_app.app.config["DB"], panel_app.app.config.get("TESTING", False)
        panel_app.app.config["DB"] = str(db_file)
        panel_app.app.config["TESTING"] = True
        client = panel_app.app.test_client()
        token = panel_app.app.config["TOKEN"]

        page = client.get("/settings")
        text = page.data.decode("utf-8")
        out["设置页有 WebDAV 一栏，还没设置时也能填"] = (
            page.status_code == 200 and "WebDAV 同步" in text and 'id="webdav-url"' in text)
        out["还没保存过时不显示\"立即同步\"按钮"] = 'action="/settings/webdav/sync"' not in text

        saved = client.post("/settings/webdav", data={
            "token": token, "url": server.base, "username": USER, "password": PASSWORD, "folder": "Panel"},
            follow_redirects=True)
        out["保存设置：回到设置页并提示已保存"] = (
            saved.status_code == 200 and "WebDAV 设置已保存" in saved.data.decode("utf-8")
            and webdav_sync.config()["folder"] == "Panel")
        page = client.get("/settings").data.decode("utf-8")
        out["保存后显示状态、两个按钮和自动同步开关，密码不回显"] = (
            'action="/settings/webdav/sync"' in page and 'action="/settings/webdav/test"' in page
            and "自动同步" in page and PASSWORD not in page)

        kept = client.post("/settings/webdav", data={
            "token": token, "url": server.base, "username": USER, "password": "", "folder": "Panel"},
            follow_redirects=True)
        out["密码留空：沿用原来的密码"] = (
            kept.status_code == 200 and webdav_sync.config()["password"] == PASSWORD)

        tested = client.post("/settings/webdav/test", data={"token": token}, follow_redirects=True)
        out["测试连接按钮：显示连接成功"] = "连接成功" in tested.data.decode("utf-8")

        synced = client.post("/settings/webdav/sync", data={"token": token}, follow_redirects=True)
        panel_db = storage.connect(db_file)
        out["立即同步按钮：收下另一台的一局，本机上传上去"] = (
            synced.status_code == 200 and "同步完成" in synced.data.decode("utf-8")
            and panel_db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2
            and len([p for p in server.state.files if p.startswith("/dav/Panel/")]) == 2)
        panel_db.close()

        off = client.post("/settings/webdav/auto", data={"token": token, "on": "0"}, follow_redirects=True)
        out["关掉自动同步：设置里变 false"] = (
            off.status_code == 200 and webdav_sync.config()["auto"] is False)

        bad = client.post("/settings/webdav", data={
            "token": "wrong", "url": server.base, "username": USER, "password": PASSWORD, "folder": "Panel"})
        out["口令不对：403，改不了设置"] = bad.status_code == 403

        page = client.get("/settings").data.decode("utf-8")
        out["清空战绩的确认框会提醒其他电脑也会删（开着同步时）"] = "其他电脑上的这些战绩也会被删除" in page

        cleared = client.post("/settings/webdav/clear", data={"token": token}, follow_redirects=True)
        out["断开：设置删掉，页面回到没设置的样子，服务器文件不动"] = (
            cleared.status_code == 200 and webdav_sync.config() is None
            and 'action="/settings/webdav/sync"' not in cleared.data.decode("utf-8")
            and len([p for p in server.state.files if p.startswith("/dav/Panel/")]) == 2)
        panel_app.app.config["DB"] = old_db
        panel_app.app.config["TESTING"] = old_testing
    return out


def review_checks():
    """审阅中发现的真实边界：凭据外发、旧库编辑、并发写入、损坏行及缓存隔离。"""
    out = {}
    webdav_sync.save_config("https://old.example/dav", USER, PASSWORD, "Evo", auto=False)
    for field, value in (("url", "https://other.example/dav"), ("username", "other-user")):
        form = {"url": "https://old.example/dav", "username": USER, "password": "", field: value}
        try:
            webdav_sync.config_from_form(form)
        except webdav_sync.SyncError:
            out[f"测试连接改了 {field} 时不复用旧密码"] = True
        else:
            out[f"测试连接改了 {field} 时不复用旧密码"] = False
    webdav_sync.clear_config()
    webdav_sync.save_form({"url": "https://old.example/dav", "username": USER, "password": PASSWORD})
    out["首次保存设置不会自动开启联网同步"] = not webdav_sync.auto_enabled()
    webdav_sync.clear_config()

    # 真实 HTTP 重定向：目标服务器不应收到携带密码的请求。
    with Server() as target:
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):  # noqa: N802
                self.send_response(302)
                self.send_header("Location", target.base + "/stolen")
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/dav"
            try:
                webdav_sync.download({"url": url, "username": USER, "password": PASSWORD}, url)
            except webdav_sync.SyncError:
                pass
            out["跨服务器重定向不会携带认证信息访问目标"] = not target.state.requests
        finally:
            server.shutdown()
            server.server_close()

    folder = fresh("review/legacy")
    conn = open_db(folder)
    mid = add_match(conn, "2026-10-01 10:00:00")
    conn.execute("UPDATE matches SET sync_uid=NULL, sync_updated=NULL WHERE match_id=?", (mid,))
    conn.commit()
    original_uid = storage._match_hash(storage.get_match(conn, mid))
    storage.update_match(conn, mid, {"damage": 999})
    out["旧局首次同步前编辑仍按编辑前身份生成编号"] = storage.get_match(conn, mid)["sync_uid"] == original_uid
    record = snapshot_of(conn, folder)["matches"][0]
    other = copy.deepcopy(record)
    other["fields"]["evidence_path"] = "C:/private/local.png"
    out["本机截图路径不改变冲突胜负顺序"] = webdav_sync._record_order(record) == webdav_sync._record_order(other)
    out["上传快照不包含本机截图路径"] = "evidence_path" not in record["fields"]
    before = storage.get_match(conn, mid)["sync_updated"]
    storage.update_match(conn, mid, {"evidence_path": "local-only.jpg"})
    out["整理本机截图不会抬高整局的同步修改时间"] = storage.get_match(conn, mid)["sync_updated"] == before
    conn.close()

    # 在取得合并写锁的一瞬间由第二个连接提交较新的编辑/删除。
    for operation in ("edit", "delete"):
        folder = fresh(f"review/race-{operation}")
        conn = open_db(folder)
        mid = add_match(conn, "2026-10-01 10:00:00")
        uid = storage.get_match(conn, mid)["sync_uid"]
        set_updated(conn, uid, "2026-10-01T10:00:00.000000Z")
        remote = snapshot_of(conn, folder)
        remote["matches"][0]["updated"] = "2026-10-01T11:00:00.000000Z"
        remote["matches"][0]["fields"]["damage"] = 1111
        writer = open_db(folder)

        class Interleave:
            armed = False

            def execute(self, sql, *args):
                if self.armed and sql == "BEGIN IMMEDIATE":
                    self.armed = False
                    if operation == "edit":
                        storage.update_match(writer, mid, {"damage": 2222})
                        set_updated(writer, uid, "2026-10-01T12:00:00.000000Z")
                    else:
                        storage.delete_match(writer, mid)
                result = conn.execute(sql, *args)
                if sql == "SELECT * FROM player_names":
                    self.armed = True
                return result

            def __getattr__(self, name):
                return getattr(conn, name)

        # sync_names 是合并前最后的准备步骤；此后才允许注入并发写入。
        proxy = Interleave()
        original_names = storage.sync_names

        def names_then_arm(db):
            original_names(db)
            proxy.armed = True

        with patch.object(storage, "sync_names", names_then_arm):
            merge(proxy, remote, folder)
        row = match_by_uid(conn, uid)
        out[f"合并写锁前发生的并发{operation}不会被旧快照覆盖或复活"] = (
            row is None if operation == "delete" else row is not None and row["damage"] == 2222)
        writer.close()
        conn.close()

    # 导出主行之后另一个连接修改主行和子表：快照必须仍来自同一个事务。
    folder = fresh("review/export-race")
    conn = open_db(folder)
    conn.execute("PRAGMA journal_mode=WAL")
    mid = add_match(conn, "2026-10-01 10:00:00")
    writer = open_db(folder)
    original_record = webdav_sync._match_record

    def change_children(db, row):
        storage.update_match(writer, mid, {"damage": 9999})
        storage.update_teammate(writer, mid, "left", {"kills": 9})
        return original_record(db, row)

    with patch.object(webdav_sync, "_match_record", change_children):
        snap = snapshot_of(conn, folder)
    out["导出的主行和子表来自同一数据库快照"] = (
        snap["matches"][0]["fields"]["damage"] == 1234
        and snap["matches"][0]["mates"][0]["kills"] == 2)
    writer.close()
    conn.close()

    folder = fresh("review/invalid")
    conn = open_db(folder)
    add_match(conn, "2026-10-01 10:00:00")
    snap = snapshot_of(conn, folder)
    invalid = copy.deepcopy(snap["matches"][0])
    invalid["uid"] = "f" * 32
    invalid["fields"]["damage"] = {"bad": "value"}
    snap["matches"].append(invalid)
    try:
        clean, skipped = webdav_sync.decode_snapshot(webdav_sync.encode_snapshot(snap))
        dst_folder = fresh("review/invalid-dst")
        dst = open_db(dst_folder)
        try:
            merge(dst, clean, dst_folder)
            out["一条损坏记录不会阻止其他正常记录合并"] = skipped == 1 and len(storage.all_matches(dst)) == 1
        finally:
            dst.close()
    except Exception:
        out["一条损坏记录不会阻止其他正常记录合并"] = False
    conn.close()
    a = {"url": "https://dav.example/dav", "folder": "Evo", "username": "a"}
    b = dict(a, username="b")
    out["不同 WebDAV 账号不共用下载缓存"] = webdav_sync._cache_dir(a, folder) != webdav_sync._cache_dir(b, folder)
    cache = webdav_sync._Cache(a, folder)
    name = "evo-" + "a" * 16 + ".json.gz"
    cache.write(name, b"same-size")
    out["没有 ETag 和修改时间时不能只按大小复用缓存"] = not cache.has({"name": name, "size": 9, "modified": ""})
    rejected = 0
    for bad in ("https://user:secret@example.com/dav", "https://[bad", "https://example.com:bad/dav"):
        try:
            webdav_sync._clean_url(bad)
        except webdav_sync.SyncError:
            rejected += 1
    out["嵌入凭据和无效主机/端口地址会显示表单错误"] = rejected == 3
    try:
        webdav_sync._same_server(a, "https://dav.example/dav/Evo/../private/evo-" + "a" * 16 + ".json.gz")
    except webdav_sync.SyncError:
        out["PROPFIND 的路径穿越地址不会被下载"] = True
    else:
        out["PROPFIND 的路径穿越地址不会被下载"] = False
    with Server() as server:
        cfg = config(server)
        webdav_sync.ensure_folder(cfg)
        for number in range(webdav_sync.MAX_FILES + 1):
            server.state.files[f"/dav/Evo/evo-{number:016x}.json.gz"] = b"x"
        try:
            webdav_sync.list_remote(cfg)
        except webdav_sync.SyncError:
            out["设备文件超过上限时明确报错，不静默遗漏设备"] = True
        else:
            out["设备文件超过上限时明确报错，不静默遗漏设备"] = False
    with patch.object(webdav_sync, "MAX_ITEMS", 1):
        try:
            webdav_sync.decode_snapshot(webdav_sync.encode_snapshot({"format": 1, "matches": [{}, {}]}))
        except webdav_sync.SyncError:
            out["超量列表会报错，不静默截断记录"] = True
        else:
            out["超量列表会报错，不静默截断记录"] = False
    webdav_sync.save_config("https://old.example/dav", USER, PASSWORD, "Evo", auto=True)
    tray_stop = threading.Event()
    first = webdav_sync.start(lambda _m: None, tray_stop, first_delay=60)
    webdav_sync.stop_auto()
    second = webdav_sync.ensure_started(lambda _m: None, first_delay=60)
    first.join(2)
    out["快速关闭再打开自动同步会启动有效线程"] = second is not first and second.is_alive() and not first.is_alive()
    webdav_sync.stop_auto()
    second.join(2)
    out["设置页能停止托盘启动的同步线程且不影响其他后台任务"] = not second.is_alive() and not tray_stop.is_set()
    webdav_sync.clear_config()
    return out


def main():
    checks = {}
    for part in (database_checks, snapshot_checks, merge_checks, webdav_checks, sync_checks, panel_checks,
                 review_checks):
        checks.update(part())
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
