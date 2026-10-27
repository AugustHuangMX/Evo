"""WebDAV 同步：把本机的战绩通过 WebDAV 服务器在多台电脑之间合并。

2026-10-26 用户决定新增：在设置页填自己的 WebDAV 服务器（如坚果云、Nextcloud、群晖），
几台电脑上的战绩合在一起看。不经过开发者服务器。

怎么同步（为什么是"每台电脑一个文件"）：
- WebDAV 服务器不一定支持 If-Match（坚果云就忽略它），共用一个文件时两台电脑同时同步会互相覆盖、丢数据。
  所以每台电脑只往服务器写自己的那个文件：evo-v2-<16 位编号>.json.gz（gzip 压缩的 JSON 快照）。
  同步时把服务器上所有设备的文件下载下来，合并进本机的数据库（只加、只更新，不删本机独有的局），
  再把自己的快照传上去。几台电脑同一时间同步也不会丢数据：各自写各自的文件，下一次同步再合并。
- 快照里包括：每局（matches）、队友、各来源读数、武器、大厅读数、账号名（player_names）。
  不含本机截图路径、设置和日志；开启截图同步时另传内容寻址的图片，快照只带哈希引用。
- 每局、每次大厅读数有一个跨电脑的编号（storage.sync_uid）。删除会记一条删除标记
  （storage.sync_tombstones），同步到别的电脑时那边也删掉；同一局两边都改了，按逻辑计数器、
  设备号和内容哈希固定顺序收敛。同计数器不同设备的落选副本保留；不以电脑时间判断先后。
- 旧数据第一次同步时才补编号（storage.ensure_sync_ids），编号由内容算出来：把数据文件夹复制到
  另一台电脑后，同一局算出的编号一样，合并时不会变成两局。

什么时候联网：
- 默认关闭；设置页保存了服务器地址、用户名、密码并开启"自动同步"后，Evo 开着时启动 30 秒后同步一次，
  之后每 30 分钟一次；没有变化时不重复上传。也可以随时在设置页手动同步。
- 只连用户填的服务器；密码用 Windows 当前用户 DPAPI 加密后存进 settings.json。
- 同步文件里有玩家名、队友名和 ID；这些会上传到用户自己的 WebDAV 服务器。设置页有说明。

测试：tests/test_webdav_sync.py（本机的小 WebDAV 服务器，不联网）；python code/webdav_sync.py --show
"""

import argparse
import base64
import gzip
import hashlib
import io
import json
import math
import posixpath
import re
import secrets
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import app_settings
import paths
import storage
import secure_secret
import webdav_media

SETTINGS_KEY = "webdav"  # app_settings：服务器地址、用户名、密码、同步文件夹、是否自动同步
LAST_KEY = "webdav_last"  # app_settings：上次同步的时间和结果（设置页显示）
UPLOAD_KEY = "webdav_upload"  # app_settings：上次上传的摘要/ETag（没有变化时不重复上传）

FORMAT = 2  # 使用逻辑版本；v1 文件隔离，避免旧客户端按物理时间覆盖 v2 数据
DEFAULT_FOLDER = "Evo"  # 服务器上的同步文件夹（用户可改）
FILE_PREFIX = "evo-v2-"
FILE_SUFFIX = ".json.gz"
FILE_RE = re.compile(r"evo-v2-([0-9a-f]{16})\.json\.gz")
AUTO_INTERVAL = 30 * 60  # 秒：自动同步的间隔
FIRST_DELAY = 30  # 秒：Evo 启动后第一次自动同步等多久
TIMEOUT = 20  # 秒：单次请求最多等多久
MAX_COMPRESSED = 32 * 1024 * 1024  # 同步文件压缩后最大 32 MB
MAX_UNCOMPRESSED = 256 * 1024 * 1024  # 解开后最大 256 MB（防止压缩炸弹）
MAX_FILES = 50  # 服务器上最多认这么多个设备文件
MAX_ITEMS = 200_000  # 每个列表最多处理这么多条
MAX_REPLY = 64 * 1024  # PROPFIND 的回答最多读这么多
USER_AGENT = f"Evo/{storage.APP_VERSION} WebDAV-Sync"

_lock = threading.Lock()  # 手动同步和自动同步同时来时一次只让一个跑
_auto_thread = None  # 自动同步的后台线程（托盘启动时、或在设置页打开自动同步时创建）
_auto_stop = None  # 只停止 WebDAV，不停止托盘的检查更新线程
_auto_guard = threading.Lock()
PF_BODY = ('<?xml version="1.0" encoding="utf-8"?>'
           '<d:propfind xmlns:d="DAV:"><d:prop>'
           '<d:resourcetype/><d:getetag/><d:getcontentlength/><d:getlastmodified/><d:displayname/>'
           '</d:prop></d:propfind>').encode("utf-8")


class SyncError(Exception):
    """同步没成功，str(e) 是给用户看的原因。"""


# ---------- 设置 ----------

def _clean_url(url):
    """服务器地址：只要 http/https，去掉末尾的 /。"""
    url = (url or "").strip().rstrip("/")
    if not url:
        raise SyncError("请填 WebDAV 服务器地址")
    try:
        parts = urllib.parse.urlsplit(url)
        _ = parts.port  # 同时校验端口格式
    except ValueError as e:
        raise SyncError("服务器地址格式不对，请检查主机名和端口") from e
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SyncError("服务器地址要以 http:// 或 https:// 开头")
    if parts.query or parts.fragment:
        raise SyncError("服务器地址里不要带 ? 或 #")
    if parts.username is not None or parts.password is not None:
        raise SyncError("请在用户名和密码输入框填写凭据，不要写在服务器地址里")
    return url


def _clean_folder(folder):
    """同步文件夹：可以有几层（如 Evo/战绩），不能有 .. 和 Windows 不认的字符。"""
    parts = [p for p in (folder or "").strip().strip("/").split("/") if p]
    if not parts:
        return DEFAULT_FOLDER
    for part in parts:
        if part in (".", "..") or re.search(r'[\\:*?"<>|\x00-\x1f]', part):
            raise SyncError("同步文件夹的名字不对：不能有 ..、\\ 或 : * ? \" < > | 这些字符")
    return "/".join(parts)


def config():
    """保存的 WebDAV 设置（都填了才算有）。没有或者不完整返回 None。"""
    saved = app_settings.get(SETTINGS_KEY)
    if not isinstance(saved, dict):
        return None
    url = str(saved.get("url") or "").strip().rstrip("/")
    username = str(saved.get("username") or "").strip()
    try:
        if saved.get("password"):
            protected = dict(saved)
            protected["password_dpapi"] = secure_secret.protect(saved["password"])
            protected.pop("password", None)
            app_settings.set_value(SETTINGS_KEY, protected)
            saved = protected
        password = secure_secret.unprotect(saved["password_dpapi"]) if saved.get("password_dpapi") else None
    except secure_secret.SecretError:
        return None
    if not url or not username or not isinstance(password, str) or not password:
        return None
    try:
        folder = _clean_folder(saved.get("folder"))
    except SyncError:
        folder = DEFAULT_FOLDER
    return {"url": url, "username": username, "password": password, "folder": folder,
            "auto": saved.get("auto", False) is True, "screenshots": saved.get("screenshots") is True}


def save_config(url, username, password, folder, auto=False, screenshots=False):
    """保存（设置页的"保存"）。返回保存后的设置。"""
    url = _clean_url(url)
    username = (username or "").strip()
    if not username:
        raise SyncError("请填用户名")
    if not isinstance(password, str) or not password:
        raise SyncError("请填密码（已经保存过时留空表示不修改）")
    cfg = {"url": url, "username": username, "password": password, "folder": _clean_folder(folder),
           "auto": bool(auto), "screenshots": bool(screenshots)}
    try:
        saved = {k: v for k, v in cfg.items() if k != "password"}
        saved["password_dpapi"] = secure_secret.protect(password)
        app_settings.set_value(SETTINGS_KEY, saved)
    except secure_secret.SecretError as e:
        raise SyncError(str(e)) from e
    return cfg


def save_form(form):
    """设置页提交的表单：密码留空时沿用已保存的（改地址或用户名时要求重填）。"""
    old = config()
    url = _clean_url(form.get("url", ""))
    username = (form.get("username", "") or "").strip()
    password = form.get("password", "") or ""
    if not password and old and old["url"] == url and old["username"] == username:
        password = old["password"]
    return save_config(url, username, password, form.get("folder", ""), auto=(old["auto"] if old else False),
                       screenshots=(old["screenshots"] if old else False))


def config_from_form(form):
    """"测试连接"用：表单里的值；密码留空时沿用已保存的。不保存。"""
    old = config()
    url = _clean_url(form.get("url", ""))
    username = (form.get("username", "") or "").strip()
    password = form.get("password", "") or ""
    if not password and old and old["url"] == url and old["username"] == username:
        password = old["password"]
    if not username or not password:
        raise SyncError("请先填用户名和密码")
    return {"url": url, "username": username, "password": password,
            "folder": _clean_folder(form.get("folder", "")), "auto": False}


def set_auto(on):
    """设置页"自动同步"开关。"""
    if not config():
        raise SyncError("请先保存 WebDAV 设置")
    saved = app_settings.get(SETTINGS_KEY)
    saved = dict(saved)
    saved["auto"] = bool(on)
    app_settings.set_value(SETTINGS_KEY, saved)


def set_screenshots(on):
    saved = app_settings.get(SETTINGS_KEY)
    if not config():
        raise SyncError("请先保存有效的 WebDAV 设置")
    saved = dict(app_settings.get(SETTINGS_KEY))  # config() 可能已迁移旧明文密码
    saved["screenshots"] = bool(on)
    app_settings.set_value(SETTINGS_KEY, saved)


def clear_config(data_dir=None):
    """设置页"断开"：删掉本机保存的地址、密码和下载缓存；服务器上的数据不动。"""
    stop_auto()
    cfg = config()
    app_settings.set_value(SETTINGS_KEY, None)
    app_settings.set_value(LAST_KEY, None)
    app_settings.set_value(UPLOAD_KEY, None)
    if cfg is not None:
        shutil.rmtree(_cache_dir(cfg, data_dir), ignore_errors=True)


def view():
    """设置页显示的内容（不含密码本身）。"""
    saved = app_settings.get(SETTINGS_KEY)
    saved = saved if isinstance(saved, dict) else {}
    cfg = config()
    last = app_settings.get(LAST_KEY)
    return {"configured": cfg is not None,
            "url": saved.get("url", ""),
            "username": saved.get("username", ""),
            "folder": saved.get("folder") or DEFAULT_FOLDER,
            "has_password": cfg is not None,
            "credential_error": bool(saved.get("password_dpapi")) and cfg is None,
            "screenshots": bool(cfg and cfg["screenshots"]),
            "auto": cfg is not None and cfg["auto"],
            "last": last if isinstance(last, dict) else None}


def auto_enabled():
    cfg = config()
    return bool(cfg and cfg["auto"])


# ---------- 服务器地址 ----------

def _folder_url(cfg):
    """同步文件夹的完整地址，以 / 结尾。文件夹里的中文等按 URL 编码。"""
    path = "/".join(urllib.parse.quote(p, safe="") for p in cfg["folder"].split("/"))
    return cfg["url"].rstrip("/") + "/" + path + "/"


def _auth(cfg):
    token = base64.b64encode(f"{cfg['username']}:{cfg['password']}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _read_limited(stream, limit):
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise SyncError("WebDAV 服务器的回答太大")
    return data


def _http_error(code, method=""):
    """服务器返回的状态码 → 给用户看的一句话。"""
    if code == 401:
        return "用户名或密码不对（服务器返回 401）"
    if code == 403:
        return "服务器不让访问这个地址（403）：检查账号权限，或者换一个同步文件夹"
    if code == 404:
        return "服务器上找不到这个地址（404）：检查服务器地址"
    if code == 405:
        return "服务器不支持这个操作（405）"
    if code == 409:
        return "服务器上没有这个文件夹（409）：检查同步文件夹的名字"
    if code == 507:
        return "WebDAV 服务器空间不足（507）"
    if code >= 500:
        return f"WebDAV 服务器出错了（{code}）"
    return f"WebDAV 服务器返回了 {code}"


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """urllib 默认会把 Authorization 带到重定向目标；绝不让密码换服务器或降级到 HTTP。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source, target = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if (source.scheme, source.netloc) != (target.scheme, target.netloc):
            raise SyncError("服务器重定向到了其他地址，已停止连接；请填写最终的 WebDAV 地址")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request(method, url, cfg, data=None, headers=None, allow=()):
    hdrs = {"User-Agent": USER_AGENT, "Authorization": _auth(cfg)}
    if headers:
        hdrs.update(headers)
    request = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.build_opener(_SafeRedirect()).open(request, timeout=TIMEOUT) as response:
            return response.status, response.headers, _read_limited(response, MAX_COMPRESSED)
    except urllib.error.HTTPError as e:
        body = e.read(MAX_REPLY)
        if e.code in allow:
            return e.code, e.headers, body
        raise SyncError(_http_error(e.code, method)) from e
    except urllib.error.URLError as e:
        reason = e.reason
        text = str(reason)
        if isinstance(reason, socket.timeout) or "timed out" in text.lower():
            raise SyncError("连接超时：检查服务器地址和网络") from e
        if isinstance(reason, OSError) and getattr(reason, "strerror", None):
            raise SyncError(f"连不上 WebDAV 服务器：{reason.strerror}") from e
        raise SyncError(f"连不上 WebDAV 服务器：{text}") from e
    except (TimeoutError, OSError) as e:
        raise SyncError(f"连不上 WebDAV 服务器：{e}") from e


def _local(tag):
    return str(tag).rsplit("}", 1)[-1]


def _href_name(href):
    path = urllib.parse.urlsplit(href).path.rstrip("/")
    return urllib.parse.unquote(path.rsplit("/", 1)[-1])


def _parse_multistatus(body, base):
    """PROPFIND 的回答 → [{href, url, name, is_dir, etag, size, modified}]。"""
    if not body:
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise SyncError("WebDAV 服务器的回答看不懂（不是标准的 PROPFIND 格式）") from e
    out, seen = [], set()
    for response in root.iter():
        if _local(response.tag) != "response":
            continue
        href, status, prop = "", "", None
        for child in response:
            name = _local(child.tag)
            if name == "href":
                href = (child.text or "").strip()
            elif name == "status":
                status = (child.text or "").strip()
            elif name == "propstat":
                part_status, part_prop = "", None
                for part in child:
                    if _local(part.tag) == "status":
                        part_status = (part.text or "").strip()
                    elif _local(part.tag) == "prop":
                        part_prop = part
                # 有的服务器会把找不到的属性单独放在一个 404 的 propstat 里，那个不能用
                if part_prop is not None and ("200" in part_status or not part_status):
                    prop, status = part_prop, (part_status or status)
        if not href:
            continue
        if "404" in status:
            continue
        url = urllib.parse.urljoin(base, href)
        if url in seen:
            continue
        seen.add(url)
        entry = {"href": href, "url": url, "name": _href_name(href), "is_dir": False,
                 "etag": "", "size": 0, "modified": ""}
        if prop is not None:
            for element in prop.iter():
                name = _local(element.tag)
                if name == "getetag":
                    entry["etag"] = (element.text or "").strip()
                elif name == "getcontentlength":
                    try:
                        entry["size"] = int(element.text or 0)
                    except ValueError:
                        entry["size"] = 0
                elif name == "getlastmodified":
                    entry["modified"] = (element.text or "").strip()
                elif name == "resourcetype":
                    entry["is_dir"] = any(_local(c.tag) == "collection" for c in element.iter())
        out.append(entry)
    return out


def propfind(cfg, url, depth="0", missing_parent=False):
    """PROPFIND；目标不存在返回 None。建目录时可把缺少父目录的 409 视为不存在。"""
    status, _headers, body = _request("PROPFIND", url, cfg, data=PF_BODY,
                                     headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
                                     allow=(404, 409) if missing_parent else (404,))
    if status == 404 or (missing_parent and status == 409):
        return None
    return _parse_multistatus(body, url)


def _same_server(cfg, url):
    """防止服务器返回别的地址（SSRF / 读错别的地方）。"""
    base = urllib.parse.urlsplit(_folder_url(cfg))
    target = urllib.parse.urlsplit(url)
    if (target.scheme, target.netloc) != (base.scheme, base.netloc):
        raise SyncError("服务器返回了别的地址，为了安全已停止同步")
    base_path = posixpath.normpath(urllib.parse.unquote(base.path)).rstrip("/") + "/"
    target_path = posixpath.normpath(urllib.parse.unquote(target.path))
    if not target_path.startswith(base_path):
        raise SyncError("服务器返回了同步文件夹之外的地址，为了安全已停止同步")


def ensure_folder(cfg):
    """确保同步文件夹存在（不存在就一层层创建），返回它的地址。"""
    base_url = cfg["url"].rstrip("/") + "/"
    if propfind(cfg, base_url, "0") is None:
        raise SyncError("服务器地址不对：找不到这个地址，检查一下服务器地址")
    folder_url = _folder_url(cfg)
    if propfind(cfg, folder_url, "0", missing_parent=True) is not None:
        return folder_url
    parts = cfg["folder"].split("/")
    for i in range(len(parts)):
        url = base_url + "/".join(urllib.parse.quote(p, safe="") for p in parts[:i + 1]) + "/"
        _request("MKCOL", url, cfg, allow=(200, 201, 204, 405))
    if propfind(cfg, folder_url, "0") is None:
        raise SyncError("同步文件夹建好了，但服务器上还是找不到它（检查账号权限）")
    return folder_url


def list_remote(cfg):
    """服务器上本应用的同步文件（每个设备一个）。顺手清掉多余的缓存。"""
    folder_url = _folder_url(cfg)
    entries = propfind(cfg, folder_url, "1")
    if entries is None:  # 文件夹还没建：建好，这次没有文件
        ensure_folder(cfg)
        return [], folder_url
    files = []
    for entry in entries:
        if entry["is_dir"] or not FILE_RE.fullmatch(entry["name"]):
            continue
        _same_server(cfg, entry["url"])
        files.append(entry)
    files.sort(key=lambda e: e["name"])
    if len(files) > MAX_FILES:
        raise SyncError(f"同步设备文件超过 {MAX_FILES} 个，已停止同步；请先整理服务器上的旧设备文件")
    return files, folder_url


def download(cfg, url):
    status, _headers, body = _request("GET", url, cfg, allow=(200,))
    if status != 200:
        raise SyncError(_http_error(status, "GET"))
    return body


def upload(cfg, url, data):
    return _request("PUT", url, cfg, data=data, headers={"Content-Type": "application/octet-stream"},
                    allow=(200, 201, 204))


def delete(cfg, url):
    _request("DELETE", url, cfg, allow=(200, 204, 404))


def test_connection(cfg):
    """设置页"测试连接"：建文件夹、上传一个小文件、下载回来核对、删掉。返回一句话。"""
    folder_url = ensure_folder(cfg)
    name = f".evo-sync-test-{secrets.token_hex(8)}.txt"
    url = folder_url + name
    payload = f"Evo WebDAV test {storage.now_sync_time()}".encode("utf-8")
    upload(cfg, url, payload)
    try:
        got = download(cfg, url)
        if got != payload:
            raise SyncError("能上传，但下载回来的内容不对")
    finally:
        try:
            delete(cfg, url)
        except SyncError:
            pass
    return "连接成功：能读写这个同步文件夹"


# ---------- 本机编号 ----------

def _machine_id():
    """这台 Windows 电脑的编号（注册表里的 MachineGuid，只用它的哈希）。读不到就用电脑名。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "MachineGuid")
            if value:
                return str(value)
    except (ImportError, OSError):
        pass
    return socket.gethostname()


def device_id(data_dir=None):
    """这台电脑 + 这个数据文件夹的编号（16 位十六进制），也是服务器上的文件名。
    同一台电脑换了数据文件夹会算成另一个，复制数据文件夹到别的电脑也不会互相覆盖。"""
    folder = str(Path(data_dir or paths.DATA_DIR).resolve()).lower()
    raw = f"evo-webdav|{_machine_id()}|{folder}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def device_file(data_dir=None):
    return f"{FILE_PREFIX}{device_id(data_dir)}{FILE_SUFFIX}"


# ---------- 快照：数据库 ←→ JSON ----------

def _lobby_record(row):
    keys = ("read_at", "player_name", "rank_tier", "rank_division", "rank_progress", "score")
    return {"uid": row["sync_uid"], "updated": row["sync_updated"] or storage.LEGACY_SYNC_TIME,
            "fields": {k: row[k] for k in keys}}


def _match_record(conn, row):
    """一局 → 快照里的那一项（带队友、各来源读数、武器）。"""
    mid = row["match_id"]
    mates = [{k: m[k] for k in ("slot", *storage.MATE_COLUMNS)}
             for m in conn.execute("SELECT * FROM teammates WHERE match_id = ? ORDER BY slot", (mid,))]
    readings = [{k: r[k] for k in ("field", "source", "value", "score", "read_at")}
                for r in conn.execute("SELECT * FROM readings WHERE match_id = ? ORDER BY field, source", (mid,))]
    weapons = [{k: w[k] for k in ("weapon", "start_s", "end_s")}
               for w in conn.execute("SELECT * FROM weapon_holds WHERE match_id = ? ORDER BY start_s, weapon", (mid,))]
    return {"uid": row["sync_uid"], "updated": row["sync_updated"] or storage.LEGACY_SYNC_TIME,
            "revision": _revision(conn, "match", row["sync_uid"]),
            "evidence": webdav_media.reference(conn, row["sync_uid"]),
            "fields": {k: row[k] for k in storage.COLUMNS if k not in ("match_id", "evidence_path")},
            "mates": mates, "readings": readings, "weapon_holds": weapons}


def _name_records(conn):
    """账号名：同一个账号的几个名字用同一个 account 编号（按名字算出来，不存进数据库）。"""
    groups = {}
    for row in conn.execute("SELECT name_key, account_id, confirmed FROM player_names ORDER BY name_key"):
        groups.setdefault(row["account_id"], []).append(row)
    out = []
    for rows in groups.values():
        key = hashlib.sha256("|".join(sorted(r["name_key"] for r in rows)).encode("utf-8")).hexdigest()[:24]
        for row in rows:
            out.append({"name_key": row["name_key"], "account": key, "confirmed": int(row["confirmed"])})
    return sorted(out, key=lambda r: (r["account"], r["name_key"]))


def export_snapshot(conn, data_dir=None):
    """本机数据库 → 同步快照（dict）。"""
    storage.ensure_sync_ids(conn)
    storage.sync_names(conn)
    conn.execute("BEGIN IMMEDIATE")  # 更新同步附加表并读取一致快照；编码和联网不持锁
    try:
        _refresh_revisions(conn, data_dir)
        matches = [_match_record(conn, row) for row in conn.execute("SELECT * FROM matches ORDER BY sync_uid")]
        lobby = [_with_revision(conn, "lobby", _lobby_record(row))
                 for row in conn.execute("SELECT * FROM lobby_readings ORDER BY sync_uid")]
        tombs = [{"kind": row["kind"], "uid": row["uid"], "deleted_at": row["deleted_at"]}
                 for row in storage.tombstones(conn)]
        result = {"format": FORMAT, "app": "Evo", "version": storage.APP_VERSION, "device": device_id(data_dir),
                "matches": matches, "names": _name_records(conn), "lobby": lobby,
                "tombstones": sorted(tombs, key=lambda t: (t["kind"], t["uid"]))}
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def encode_snapshot(snapshot):
    """快照 → gzip 文件内容。同一份数据每次压出来的字节一样（没有时间字段），用来判断要不要重传。"""
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0)


def _valid_uid(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _clean_rows(rows, keys):
    out = []
    if isinstance(rows, list):
        for row in rows[:MAX_ITEMS]:
            if isinstance(row, dict):
                out.append({k: row.get(k) for k in keys})
    return out


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _clean_weapons(rows):
    out = []
    if isinstance(rows, list):
        for row in rows[:MAX_ITEMS]:
            if isinstance(row, dict):
                out.append({"weapon": row.get("weapon"), "start_s": _as_float(row.get("start_s")),
                            "end_s": _as_float(row.get("end_s"))})
    return out


def _valid_fields(fields, integers=(), texts=(), required=()):
    """远端值在写 SQLite 前校验；坏的一整条跳过，不能破坏同文件的其他记录。"""
    if not isinstance(fields, dict) or any(fields.get(k) is None for k in required):
        return False
    for key, value in fields.items():
        if value is None:
            continue
        if key in integers and (not isinstance(value, int) or not -(2**63) <= value < 2**63):
            return False
        if key in texts and not isinstance(value, str):
            return False
        if not isinstance(value, (str, int, float)):
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
        if isinstance(value, int) and not -(2**63) <= value < 2**63:
            return False
    return True


def _valid_match(item):
    fields = item.get("fields")
    if not isinstance(fields, dict):
        return False
    fields = {k: v for k, v in fields.items() if k in storage.COLUMNS and k not in ("match_id", "evidence_path")}
    integers = {k for k, kind in storage.COLUMNS.items() if kind.startswith("INTEGER")}
    texts = {k for k, kind in storage.COLUMNS.items() if kind == "TEXT"}
    if not _valid_fields(fields, integers, texts) or ("needs_review" in fields and fields["needs_review"] is None):
        return False
    for key in ("mates", "readings", "weapon_holds"):
        rows = item.get(key, [])
        if not isinstance(rows, list) or len(rows) > MAX_ITEMS:
            return False
        for row in rows:
            if key == "mates":
                if not _valid_fields(row, integers, ("slot", "name", "player_id", "legend"), ("slot",)):
                    return False
            elif key == "readings":
                if not _valid_fields(row, texts=("field", "source", "read_at"), required=("field", "source")):
                    return False
                if row.get("score") is not None and not isinstance(row["score"], (int, float)):
                    return False
            else:
                if not _valid_fields(row, texts=("weapon",), required=("weapon", "start_s", "end_s")):
                    return False
                if any(not isinstance(row[k], (int, float)) for k in ("start_s", "end_s")):
                    return False
    return True


def _valid_time(value):
    if value is None:
        return True  # 老格式缺时间时按 LEGACY_SYNC_TIME 处理
    if not isinstance(value, str):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").strftime("%Y-%m-%dT%H:%M:%S.%fZ") == value
    except ValueError:
        return False


def _valid_revision(value):
    return (isinstance(value, dict) and isinstance(value.get("counter"), int)
            and 0 <= value["counter"] < 2**53
            and isinstance(value.get("device"), str)
            and re.fullmatch(r"[0-9a-f]{16}", value["device"]) is not None)


def _clean_snapshot(snapshot):
    """检查服务器上的快照：只认认识的项，坏的那条跳过。返回 (清理后的快照, 跳过了几条)。"""
    if not isinstance(snapshot, dict) or snapshot.get("format") != FORMAT:
        raise SyncError("同步文件的格式不认识（可能是更新版本的 Evo 写的）")
    for key in ("matches", "lobby", "names", "tombstones"):
        if key in snapshot and (not isinstance(snapshot[key], list) or len(snapshot[key]) > MAX_ITEMS):
            raise SyncError("同步文件的数据列表格式不对或条目太多，已跳过")
    skipped = 0
    matches = {}
    for item in snapshot.get("matches") if isinstance(snapshot.get("matches"), list) else []:
        if (not isinstance(item, dict) or not _valid_uid(item.get("uid"))
                or not _valid_revision(item.get("revision")) or not _valid_match(item)):
            skipped += 1
            continue
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        fields = {k: v for k, v in fields.items() if k in storage.COLUMNS and k not in ("match_id", "evidence_path")}
        record = {"uid": item["uid"],
                  "revision": item["revision"],
                  "evidence": item.get("evidence") if webdav_media.valid(item.get("evidence")) else None,
                  "updated": item["updated"] if isinstance(item.get("updated"), str) else storage.LEGACY_SYNC_TIME,
                  "fields": fields,
                  "mates": sorted(_clean_rows(item.get("mates"), ("slot", *storage.MATE_COLUMNS)),
                                  key=lambda r: str(r.get("slot"))),
                  "readings": sorted(_clean_rows(item.get("readings"),
                                                 ("field", "source", "value", "score", "read_at")),
                                     key=lambda r: (str(r.get("field")), str(r.get("source")))),
                  "weapon_holds": sorted(_clean_weapons(item.get("weapon_holds")),
                                         key=lambda r: (r["start_s"], str(r.get("weapon"))))}
        old = matches.get(record["uid"])
        if old is None or _record_order(record) > _record_order(old):
            matches[record["uid"]] = record
    lobby = {}
    keys = ("read_at", "player_name", "rank_tier", "rank_division", "rank_progress", "score")
    for item in snapshot.get("lobby") if isinstance(snapshot.get("lobby"), list) else []:
        if (not isinstance(item, dict) or not _valid_uid(item.get("uid"))
                or not _valid_revision(item.get("revision"))
                or not _valid_fields(item.get("fields"), ("rank_progress", "score"),
                                     ("read_at", "player_name", "rank_tier", "rank_division"), ("read_at",))):
            skipped += 1
            continue
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        record = {"uid": item["uid"],
                  "revision": item["revision"],
                  "updated": item["updated"] if isinstance(item.get("updated"), str) else storage.LEGACY_SYNC_TIME,
                  "fields": {k: fields.get(k) for k in keys}}
        if not record["fields"]["read_at"]:
            skipped += 1
            continue
        old = lobby.get(record["uid"])
        if old is None or _record_order(record) > _record_order(old):
            lobby[record["uid"]] = record
    names = {}
    for item in snapshot.get("names") if isinstance(snapshot.get("names"), list) else []:
        if not isinstance(item, dict):
            continue
        name_key, account = item.get("name_key"), item.get("account")
        if not isinstance(name_key, str) or not name_key.strip():
            continue
        if not isinstance(account, str) or not re.fullmatch(r"[0-9a-f]{8,64}", account):
            continue
        names.setdefault(account, {})[name_key.strip()] = 1 if _as_int(item.get("confirmed")) else 0
    names_out = [{"name_key": k, "account": a, "confirmed": c}
                 for a, group in names.items() for k, c in group.items()]
    tombs = {}
    for item in snapshot.get("tombstones") if isinstance(snapshot.get("tombstones"), list) else []:
        if not isinstance(item, dict) or item.get("kind") not in ("match", "lobby"):
            continue
        if not _valid_uid(item.get("uid")):
            continue
        if not _valid_time(item.get("deleted_at")):
            skipped += 1
            continue
        at = item.get("deleted_at") if isinstance(item.get("deleted_at"), str) else storage.LEGACY_SYNC_TIME
        key = (item["kind"], item["uid"])
        if key not in tombs or at > tombs[key]:
            tombs[key] = at
    tombs_out = [{"kind": k, "uid": u, "deleted_at": at} for (k, u), at in sorted(tombs.items())]
    return {"matches": list(matches.values()), "lobby": list(lobby.values()), "names": names_out,
            "tombstones": tombs_out}, skipped


def decode_snapshot(data):
    """gzip 文件内容 → (快照, 跳过了几条)。"""
    if len(data) > MAX_COMPRESSED:
        raise SyncError("同步文件太大，已跳过")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
            raw = f.read(MAX_UNCOMPRESSED + 1)
    except (OSError, EOFError) as e:
        raise SyncError("同步文件读不出来（可能坏了，或者不是 Evo 的同步文件）") from e
    if len(raw) > MAX_UNCOMPRESSED:
        raise SyncError("同步文件解开后太大，已跳过")
    try:
        snapshot = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SyncError("同步文件读不出来（可能坏了）") from e
    return _clean_snapshot(snapshot)


def _content_fingerprint(record):
    payload = {"fields": {k: v for k, v in (record.get("fields") or {}).items() if k != "evidence_path"}}
    for key in ("mates", "readings", "weapon_holds"):
        if key in record:
            payload[key] = record[key]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _record_order(record):
    """逻辑版本与设备号确定顺序；电脑日期和时区完全不参与合并。"""
    revision = record.get("revision") or {"counter": 0, "device": ""}
    return (revision["counter"], revision["device"], _content_fingerprint(record))


def _revision(conn, kind, uid):
    row = conn.execute("SELECT counter, device FROM sync_revisions WHERE kind=? AND uid=?", (kind, uid)).fetchone()
    return {"counter": row[0], "device": row[1]} if row else {"counter": 0, "device": ""}


def _with_revision(conn, kind, record):
    return {**record, "revision": _revision(conn, kind, record["uid"])}


def _save_revision(conn, kind, record):
    revision = record["revision"]
    conn.execute("INSERT OR REPLACE INTO sync_revisions VALUES (?, ?, ?, ?, ?)",
                 (kind, record["uid"], revision["counter"], revision["device"], _content_fingerprint(record)))


def _refresh_revisions(conn, data_dir):
    """在写事务中检测业务内容变化。旧 0.4.0 写入不懂同步列，也能在下一次同步时被发现。
    同步版本只写附加表，不改变原业务表的列、ID 或写入接口。
    """
    writer = device_id(data_dir)
    for kind, table in (("match", "matches"), ("lobby", "lobby_readings")):
        present = set()
        for row in conn.execute(f"SELECT * FROM {table}").fetchall():
            record = _match_record(conn, row) if kind == "match" else _lobby_record(row)
            uid = record["uid"]
            if not uid:  # 旧版在同步准备期间新增的行
                uid = storage._free_uid(conn, table, storage._match_hash(row) if kind == "match" else storage._lobby_hash(row))
                conn.execute(f"UPDATE {table} SET sync_uid=? WHERE rowid=?",
                             (uid, row["match_id"] if kind == "match" else row["reading_id"]))
                record["uid"] = uid
            present.add(uid)
            old = conn.execute("SELECT * FROM sync_revisions WHERE kind=? AND uid=?", (kind, uid)).fetchone()
            fingerprint = _content_fingerprint(record)
            if old is None or old["fingerprint"] != fingerprint:
                record["revision"] = {"counter": old["counter"] + 1 if old else 1, "device": writer}
                _save_revision(conn, kind, record)
        for row in conn.execute("SELECT uid FROM sync_revisions WHERE kind=?", (kind,)).fetchall():
            if row[0] not in present and not conn.execute(
                    "SELECT 1 FROM sync_tombstones WHERE kind=? AND uid=?", (kind, row[0])).fetchone():
                storage.record_tombstone(conn, kind, row[0])  # 旧客户端删除的局也必须留下墓碑


def _preserve_conflict(conn, kind, local, remote):
    a, b = _record_order(local), _record_order(remote)
    if a[0] != b[0] or a[1] == b[1] or a[2] == b[2]:
        return 0
    loser = local if b > a else remote
    cur = conn.execute("INSERT OR IGNORE INTO sync_conflicts VALUES (?, ?, ?, ?)",
                       (kind, loser["uid"], _content_fingerprint(loser),
                        json.dumps(loser, ensure_ascii=False, sort_keys=True)))
    return cur.rowcount


# ---------- 合并 ----------

def _delete_match_rows(conn, match_id):
    for table in ("matches", "teammates", "readings", "weapon_holds"):
        conn.execute(f"DELETE FROM {table} WHERE match_id = ?", (match_id,))


def _insert_children(conn, match_id, record):
    for mate in record["mates"]:
        data = {"slot": mate.get("slot"), **{k: mate.get(k) for k in storage.MATE_COLUMNS}}
        conn.execute(f"INSERT OR REPLACE INTO teammates (match_id, {', '.join(data)}) "
                     f"VALUES (?, {', '.join('?' for _ in data)})", [match_id, *data.values()])
    for reading in record["readings"]:
        conn.execute("INSERT OR REPLACE INTO readings (match_id, field, source, value, score, read_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (match_id, reading.get("field"), reading.get("source"), reading.get("value"),
                      reading.get("score"), reading.get("read_at")))
    for hold in record["weapon_holds"]:
        conn.execute("INSERT OR REPLACE INTO weapon_holds (match_id, weapon, start_s, end_s) "
                     "VALUES (?, ?, ?, ?)",
                     (match_id, hold.get("weapon"), hold.get("start_s"), hold.get("end_s")))


def _insert_match(conn, record):
    data = {k: v for k, v in record["fields"].items()
            if k in storage.COLUMNS and k not in ("match_id", "evidence_path")}
    data["sync_uid"] = record["uid"]
    data["sync_updated"] = record["updated"]
    cur = conn.execute(f"INSERT INTO matches ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})",
                       list(data.values()))
    _insert_children(conn, cur.lastrowid, record)
    return cur.lastrowid


def _update_match(conn, row, record, data_dir):
    # 截图及其路径是本机数据；绝不把服务器上的路径交给面板的文件读取/删除逻辑。
    fields = {k: v for k, v in record["fields"].items()
              if k in storage.COLUMNS and k not in ("match_id", "evidence_path")}
    if not fields:
        return
    fields["sync_updated"] = record["updated"]
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE matches SET {sets} WHERE match_id = ?", [*fields.values(), row["match_id"]])
    conn.execute("DELETE FROM teammates WHERE match_id = ?", (row["match_id"],))
    conn.execute("DELETE FROM readings WHERE match_id = ?", (row["match_id"],))
    conn.execute("DELETE FROM weapon_holds WHERE match_id = ?", (row["match_id"],))
    _insert_children(conn, row["match_id"], record)


def _insert_lobby(conn, record):
    keys = ("read_at", "player_name", "rank_tier", "rank_division", "rank_progress", "score")
    data = {k: record["fields"].get(k) for k in keys}
    data["sync_uid"] = record["uid"]
    data["sync_updated"] = record["updated"]
    conn.execute(f"INSERT INTO lobby_readings ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})",
                 list(data.values()))


def _update_lobby(conn, uid, record):
    keys = ("read_at", "player_name", "rank_tier", "rank_division", "rank_progress", "score")
    data = {k: record["fields"].get(k) for k in keys}
    data["sync_updated"] = record["updated"]
    sets = ", ".join(f"{k} = ?" for k in data)
    conn.execute(f"UPDATE lobby_readings SET {sets} WHERE sync_uid = ?", [*data.values(), uid])


def _merge_names(conn, snapshots):
    """把别的电脑上的账号名并进来：本地已有的名字保持原来的账号分组（以本机为准），
    没见过的新名字按远端的分组加进来。返回新加了几个名字。"""
    local = {r["name_key"]: r for r in conn.execute("SELECT * FROM player_names")}
    groups = {}
    for snapshot in snapshots:
        for item in snapshot["names"]:
            groups.setdefault(item["account"], {})[item["name_key"]] = int(item.get("confirmed") or 0)
    added = 0
    for account in sorted(groups):
        members = groups[account]
        local_ids = [local[key]["account_id"] for key in members if key in local]
        if local_ids:
            target = min(local_ids)
        else:
            target = conn.execute("SELECT COALESCE(MAX(account_id), 0) + 1 FROM player_names").fetchone()[0]
        for key in sorted(members):
            confirmed = 1 if members[key] else 0
            row = local.get(key)
            if row is None:
                conn.execute("INSERT INTO player_names (name_key, account_id, confirmed) VALUES (?, ?, ?)",
                             (key, target, confirmed))
                local[key] = {"account_id": target, "confirmed": confirmed}  # 后面的分组也看得到
                added += 1
            elif confirmed and not row["confirmed"]:
                conn.execute("UPDATE player_names SET confirmed = 1 WHERE name_key = ?", (key,))
    return added


def merge_snapshots(conn, snapshots, data_dir=None):
    """把几份快照合并进本机数据库。返回每类数据新增/更新/删掉了几条。"""
    storage.ensure_sync_ids(conn)
    storage.sync_names(conn)
    matches, lobby, tombs = {}, {}, {}
    for snapshot in snapshots:
        for record in snapshot["matches"]:
            old = matches.get(record["uid"])
            if old is None or _record_order(record) > _record_order(old):
                matches[record["uid"]] = ({**record, "evidence": old.get("evidence")}
                                           if old and not record.get("evidence") else record)
            elif record.get("evidence") and not old.get("evidence"):
                matches[record["uid"]] = {**old, "evidence": record["evidence"]}
        for record in snapshot["lobby"]:
            old = lobby.get(record["uid"])
            if old is None or _record_order(record) > _record_order(old):
                lobby[record["uid"]] = record
        for tomb in snapshot["tombstones"]:
            key = (tomb["kind"], tomb["uid"])
            at = tomb.get("deleted_at") or storage.LEGACY_SYNC_TIME
            if key not in tombs or at > tombs[key]:
                tombs[key] = at

    stats = {"matches_new": 0, "matches_updated": 0, "matches_deleted": 0,
             "lobby_new": 0, "lobby_updated": 0, "lobby_deleted": 0, "names_new": 0, "conflicts": 0}
    conn.execute("BEGIN IMMEDIATE")
    try:
        _refresh_revisions(conn, data_dir)
        local_matches = {r["sync_uid"]: r for r in conn.execute("SELECT * FROM matches") if r["sync_uid"]}
        local_lobby = {r["sync_uid"]: r for r in conn.execute("SELECT * FROM lobby_readings") if r["sync_uid"]}
        local_tombs = {(r["kind"], r["uid"]) for r in storage.tombstones(conn)}
        for (kind, uid), at in sorted(tombs.items()):
            storage.record_tombstone(conn, kind, uid, at)  # 已有的保留较晚的时间
            if kind == "match" and uid in local_matches:
                _delete_match_rows(conn, local_matches[uid]["match_id"])
                stats["matches_deleted"] += 1
            elif kind == "lobby" and uid in local_lobby:
                conn.execute("DELETE FROM lobby_readings WHERE sync_uid = ?", (uid,))
                stats["lobby_deleted"] += 1
        blocked = local_tombs | set(tombs)
        for uid, record in matches.items():
            if ("match", uid) in blocked:
                continue
            if record.get("evidence"):
                webdav_media.remember(conn, uid, record["evidence"])
            row = local_matches.get(uid)
            if row is None:
                _insert_match(conn, record)
                _save_revision(conn, "match", record)
                stats["matches_new"] += 1
            else:
                local = _match_record(conn, row)
                stats["conflicts"] += _preserve_conflict(conn, "match", local, record)
                if _record_order(record) > _record_order(local):
                    _update_match(conn, row, record, data_dir)
                    _save_revision(conn, "match", record)
                    stats["matches_updated"] += 1
        for uid, record in lobby.items():
            if ("lobby", uid) in blocked:
                continue
            row = local_lobby.get(uid)
            if row is None:
                _insert_lobby(conn, record)
                _save_revision(conn, "lobby", record)
                stats["lobby_new"] += 1
            else:
                local = _with_revision(conn, "lobby", _lobby_record(row))
                stats["conflicts"] += _preserve_conflict(conn, "lobby", local, record)
                if _record_order(record) > _record_order(local):
                    _update_lobby(conn, uid, record)
                    _save_revision(conn, "lobby", record)
                    stats["lobby_updated"] += 1
        stats["names_new"] = _merge_names(conn, snapshots)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return stats


# ---------- 下载缓存 ----------

def _cache_dir(cfg, data_dir=None):
    key = hashlib.sha256(json.dumps([cfg['url'], cfg['username'], cfg['folder']]).encode("utf-8")).hexdigest()[:12]
    return Path(data_dir or paths.DATA_DIR) / "data" / "webdav_cache" / key


class _Cache:
    """服务器上每台设备的快照在本机的副本：ETag/大小没变就不重新下载。"""

    def __init__(self, cfg, data_dir=None):
        self.folder = _cache_dir(cfg, data_dir)
        self.index_path = self.folder / "index.json"
        try:
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
            self.index = index if isinstance(index, dict) else {}
        except (OSError, ValueError):
            self.index = {}

    def _path(self, name):
        return self.folder / name

    def has(self, entry):
        saved = self.index.get(entry["name"])
        if not isinstance(saved, dict) or not self._path(entry["name"]).is_file():
            return False
        if entry.get("etag") and saved.get("etag"):
            return entry["etag"] == saved["etag"]
        return bool(entry.get("modified")) and (saved.get("size") == entry.get("size")
                                                and saved.get("modified") == entry.get("modified"))

    def read(self, name):
        try:
            return self._path(name).read_bytes()
        except OSError:
            return None

    def write(self, name, raw, etag="", size=None, modified=""):
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        part = path.with_name(path.name + ".tmp")
        part.write_bytes(raw)
        part.replace(path)
        self.index[name] = {"etag": etag or "", "size": len(raw) if size is None else size,
                            "modified": modified or ""}
        self._save()

    def prune(self, names):
        changed = False
        for name in list(self.index):
            if name not in names:
                self.index.pop(name, None)
                self._path(name).unlink(missing_ok=True)
                changed = True
        if changed:
            self._save()

    def _save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.index, ensure_ascii=False, sort_keys=True, indent=1),
                                   encoding="utf-8")


# ---------- 同步 ----------

def _remote_meta(cfg, url):
    """上传后问一下服务器这个文件的 ETag / 大小（下次没变化就不重传）。"""
    try:
        entries = propfind(cfg, url, "0")
    except SyncError:
        return "", 0, ""
    if not entries:
        return "", 0, ""
    entry = entries[0]
    return entry.get("etag", ""), entry.get("size", 0), entry.get("modified", "")


def _already_uploaded(entry, digest, payload):
    if entry is None:
        return False
    saved = app_settings.get(UPLOAD_KEY)
    if not isinstance(saved, dict) or saved.get("digest") != digest:
        return False
    if entry.get("etag") and saved.get("etag"):
        return entry["etag"] == saved["etag"]
    return (bool(entry.get("modified")) and saved.get("size") == len(payload) and entry.get("size") == len(payload)
            and saved.get("modified") == entry.get("modified"))


def _save_last(ok, result=None, error=None):
    at = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = {"ok": bool(ok), "at": at, "error": error}
    if result:
        data.update({k: v for k, v in result.items() if k not in ("ok", "error")})
    app_settings.set_value(LAST_KEY, data)


def result_text(result):
    """一次同步的结果 → 设置页/日志里的一句话。"""
    parts = []
    if result.get("matches_new"):
        parts.append(f"收到 {result['matches_new']} 局")
    if result.get("matches_updated"):
        parts.append(f"更新 {result['matches_updated']} 局")
    if result.get("matches_deleted"):
        parts.append(f"删除 {result['matches_deleted']} 局")
    if result.get("lobby_new"):
        parts.append(f"收到 {result['lobby_new']} 次大厅读数")
    if result.get("names_new"):
        parts.append(f"新账号名 {result['names_new']} 个")
    if result.get("images_uploaded") or result.get("images_downloaded"):
        parts.append(f"截图上传 {result.get('images_uploaded', 0)} 张、下载 {result.get('images_downloaded', 0)} 张")
    if result.get("conflicts"):
        parts.append(f"已保留 {result['conflicts']} 份并发修改的副本")
    if result.get("uploaded"):
        size = result.get("size") or 0
        parts.append(f"已上传本机数据（{size / 1024:.0f} KB）" if size >= 1024 else "已上传本机数据")
    if not parts:
        parts.append("没有新变化")
    text = "同步完成：" + "，".join(parts)
    if result.get("bad_files"):
        text += f"；有 {len(result['bad_files'])} 个同步文件读不出来，已跳过"
    if result.get("image_errors"):
        text += f"；有 {result['image_errors']} 张截图未能同步，请检查文件和网络后重试"
    return text


def _sync(conn, cfg, data_dir=None, now=None):
    own_name = device_file(data_dir)
    folder_url = ensure_folder(cfg)
    entries, folder_url = list_remote(cfg)
    cache = _Cache(cfg, data_dir)
    snapshots, downloaded, bad = [], 0, []
    for entry in entries:
        raw = cache.read(entry["name"]) if cache.has(entry) else None
        if raw is None:
            raw = download(cfg, entry["url"])
            downloaded += 1
            cache.write(entry["name"], raw, entry.get("etag", ""), entry.get("size"), entry.get("modified", ""))
        try:
            snapshot, skipped = decode_snapshot(raw)
        except SyncError:
            bad.append(entry["name"])
            continue
        if skipped:
            bad.append(f"{entry['name']}（{skipped} 条）")
        snapshots.append(snapshot)
    stats = merge_snapshots(conn, snapshots, data_dir=data_dir)
    stats.update(webdav_media.sync(conn, cfg, data_dir))
    snapshot = export_snapshot(conn, data_dir=data_dir)
    payload = encode_snapshot(snapshot)
    digest = hashlib.sha256(payload).hexdigest()
    own_entry = next((e for e in entries if e["name"] == own_name), None)
    uploaded = False
    if not _already_uploaded(own_entry, digest, payload):
        own_url = folder_url + own_name
        upload(cfg, own_url, payload)
        uploaded = True
        etag, size, modified = _remote_meta(cfg, own_url)
        cache.write(own_name, payload, etag, size or len(payload), modified)
        app_settings.set_value(UPLOAD_KEY, {"digest": digest, "etag": etag, "size": len(payload),
                                           "modified": modified})
    elif cache.read(own_name) is None:
        cache.write(own_name, payload, own_entry.get("etag", ""), own_entry.get("size"),
                    own_entry.get("modified", ""))
    cache.prune({*[e["name"] for e in entries], own_name})
    result = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "files": len(entries),
              "downloaded": downloaded, "uploaded": uploaded, "size": len(payload),
              "bad_files": bad, **stats}
    _save_last(True, result=result)
    return result


def sync(conn=None, cfg=None, data_dir=None, now=None):
    """同步一次（下载所有设备的快照 → 合并 → 上传本机的快照）。失败抛出 SyncError，原因给用户看。"""
    cfg = cfg or config()
    if cfg is None:
        raise SyncError("还没有设置 WebDAV 同步（在设置页填服务器地址、用户名和密码）")
    if not _lock.acquire(blocking=False):
        raise SyncError("正在同步，请稍等")
    own_conn = conn is None
    try:
        conn = conn or storage.connect()
        try:
            return _sync(conn, cfg, data_dir=data_dir, now=now)
        finally:
            if own_conn:
                conn.close()
    except SyncError as e:
        _save_last(False, error=str(e))
        raise
    except Exception as e:  # noqa: BLE001  数据库坏了、磁盘满了等：记下原因，别让托盘线程挂掉
        _save_last(False, error=f"出错了：{e}")
        raise SyncError(f"同步时出错：{e}") from e
    finally:
        _lock.release()


def start(log, stop, first_delay=FIRST_DELAY, interval=AUTO_INTERVAL):
    """托盘或设置页开启自动同步；两种入口共用线程，WebDAV 有独立停止信号。"""
    global _auto_thread, _auto_stop
    with _auto_guard:
        if not auto_enabled():
            return None
        if (_auto_thread is not None and _auto_thread.is_alive()
                and _auto_stop is not None and not _auto_stop.is_set()):
            return _auto_thread
        local_stop = threading.Event()
        _auto_stop = local_stop

        def wait(delay):
            deadline = time.monotonic() + delay
            while not stop.is_set() and not local_stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                local_stop.wait(min(remaining, 1.0))
            return True

        def loop():
            if wait(first_delay):
                return
            while not stop.is_set() and not local_stop.is_set():
                if not auto_enabled():
                    return
                try:
                    import data_move
                    busy = data_move.busy()
                except ImportError:
                    busy = False
                if busy:
                    log("WebDAV 同步：正在搬数据文件夹，这次先不同步")
                else:
                    try:
                        result = sync()
                        log(f"WebDAV 同步：{result_text(result)}")
                    except SyncError as e:
                        log(f"WebDAV 同步：没成功（{e}）")
                if wait(interval):
                    return

        thread = threading.Thread(target=loop, name="webdav-sync", daemon=True)
        _auto_thread = thread
        thread.start()
        return thread


def ensure_started(log, first_delay=FIRST_DELAY, interval=AUTO_INTERVAL):
    """设置页开启自动同步时启动后台线程，无需重启。"""
    return start(log, threading.Event(), first_delay, interval)


def stop_auto():
    """关开关或断开时立即唤醒停止等待，不影响检查更新。已开始的网络请求会完成。"""
    with _auto_guard:
        if _auto_stop is not None:
            _auto_stop.set()


# ---------- 开发时看 ----------

def main():
    parser = argparse.ArgumentParser(description="WebDAV 同步：查看快照、测试连接、同步一次")
    parser.add_argument("--show", action="store_true", help="打印本机快照的统计（不联网）")
    parser.add_argument("--test", action="store_true", help="用保存的设置测试连接")
    parser.add_argument("--sync", action="store_true", help="用保存的设置同步一次")
    args = parser.parse_args()
    if args.show:
        conn = storage.connect()
        try:
            snapshot = export_snapshot(conn)
        finally:
            conn.close()
        payload = encode_snapshot(snapshot)
        print(f"本机快照：{len(snapshot['matches'])} 局、{len(snapshot['names'])} 个账号名、"
              f"{len(snapshot['lobby'])} 次大厅读数、{len(snapshot['tombstones'])} 条删除标记；"
              f"压缩后 {len(payload) / 1024:.1f} KB")
        return 0
    cfg = config()
    if cfg is None:
        print("还没有设置 WebDAV 同步（在 Evo 设置页填写）")
        return 1
    if args.test:
        print(test_connection(cfg))
        return 0
    if args.sync:
        print(result_text(sync()))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
