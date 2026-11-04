"""补丁包（增量更新，2026-10-24 用户决定）：一键更新只下载和玩家现在的版本相比变了的文件，以后每次发布都这样。

0.3.7 → 0.3.8 的 354 个文件只变了 8 个（压缩后约 11 MB，完整的安装包 112 MB），大部分是 Evo.exe。

发布时（tools/release.py 调 make）：给最近几个版本各做一个补丁包 Evo-<新版本>-patch-from-<旧版本>.zip，里面有
- patch.json：{"from": 旧版本, "to": 新版本, "files": {"Evo/<路径>": [大小, SHA-256], ……}}，新版本的每个文件都列着
- 新版本里和旧版本不一样（或者旧版本没有）的文件，路径和完整的安装包里一样（Evo/……）
补丁包的地址、SHA-256、大小写进 latest.json 的 download.patches（见 update_check.py）。

Evo 里（updater.py 调 apply）：下载补丁包、按 latest.json 核对 SHA-256，再照 patch.json 拼出完整的新版本：
补丁包里有的从补丁包里取，没有的从程序文件夹里现在的文件复制，每个文件都核对大小和 SHA-256。有一个对不上
（被杀毒软件删了、被改过……）这个补丁包就用不了（Unusable），改下完整的安装包。拼出来的和解压完整安装包
一模一样，之后的安装步骤不变。

Mirror酱 的增量包（国内渠道，2026-11-04 用户决定；updater.py 调 compose）：Mirror酱 自己比较前后两个版本的
完整安装包、做出增量包（新增和改了的文件，加一个 changes.json 写着删了哪些）。我们不靠 changes.json 拼，
而是靠完整安装包里的文件清单 MANIFEST（release.py 写进去的，新版本每个文件的大小和 SHA-256，格式和 patch.json
的 files 一样）：照清单拼出新版本，增量包里有的取增量包里的，没有的从现在的程序文件夹复制，每个都核对，
清单上没有的旧文件自然就没了。对不上同样抛出 Unusable，不装。增量包里的路径带不带最外层的 Evo/ 都认。
"""

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

INFO = "patch.json"  # 补丁包里的文件清单
ROOT = "Evo"  # 安装包里最外层的文件夹
MANIFEST = f"{ROOT}/_internal/evo_files.json"  # 完整安装包里的文件清单：{"version", "files"}，不列它自己（2026-11-04 起）
CHANGES = "changes.json"  # Mirror酱 增量包最外层的变化说明（只核对里面的路径，拼的时候不用）
CHANGE_KEYS = ("added", "modified", "deleted", "added_dir", "deleted_dir")
MAX_FILES = 20000  # 现在约 350 个文件，多得离谱一定是坏了
CHUNK = 1 << 20


class Unusable(Exception):
    """这个补丁包用不了（本机的文件对不上、补丁包里的东西不对），改下完整的安装包。str(e) 写进日志。"""


def patch_name(new_version, old_version):
    return f"Evo-{new_version}-patch-from-{old_version}.zip"


def safe_name(name):
    """安装包里的路径：在 Evo/ 下面，不往外跑（..），不是文件夹。"""
    parts = PurePosixPath(name).parts
    return (isinstance(name, str) and len(parts) >= 2 and parts[0] == ROOT and ".." not in parts
            and "\\" not in name and ":" not in name and not name.endswith("/"))


def _hash_copy(src, out=None):
    """读完 src（边读边写进 out），返回 (大小, SHA-256)。"""
    digest, size = hashlib.sha256(), 0
    for block in iter(lambda: src.read(CHUNK), b""):
        digest.update(block)
        size += len(block)
        if out is not None:
            out.write(block)
    return size, digest.hexdigest()


def manifest(zip_path):
    """压缩包里的每个文件：{路径: (大小, SHA-256)}（大小、SHA-256 是解压后的）。"""
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if not info.is_dir():
                with z.open(info) as f:
                    out[info.filename] = _hash_copy(f)
    return out


# ---------- 发布时 ----------

def make(old_zip, new_zip, target, old_version, new_version):
    """做从 old_version 升到 new_version 的补丁包（target）。返回 (放进去几个文件, 新版本一共几个文件)。"""
    old, new = manifest(old_zip), manifest(new_zip)
    bad = [name for name in new if not safe_name(name)]
    if bad:
        raise ValueError(f"新版本的压缩包里有不在 Evo/ 下的文件：{bad[:3]}")
    changed = [name for name in new if old.get(name) != new[name]]
    info = {"from": old_version, "to": new_version, "files": {name: list(new[name]) for name in new}}
    with zipfile.ZipFile(new_zip) as src, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(INFO, json.dumps(info, ensure_ascii=False, indent=1))
        for name in changed:
            old_info = src.getinfo(name)
            entry = zipfile.ZipInfo(name, date_time=old_info.date_time)
            entry.external_attr = old_info.external_attr
            z.writestr(entry, src.read(name), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return len(changed), len(new)


# ---------- Evo 里 ----------

def file_list(files, what="补丁包"):
    """文件清单 {"Evo/<路径>": [大小, SHA-256]} 对不对：路径在 Evo/ 下面、有 Evo.exe，大小、SHA-256 格式都对。
    返回 {路径: (大小, SHA-256)}；不对抛出 Unusable。"""
    if not isinstance(files, dict) or not 0 < len(files) <= MAX_FILES or f"{ROOT}/Evo.exe" not in files:
        raise Unusable(f"{what}里的文件清单不对")
    out = {}
    for name, value in files.items():
        ok = (safe_name(name) and isinstance(value, list) and len(value) == 2
              and isinstance(value[0], int) and not isinstance(value[0], bool) and value[0] >= 0
              and isinstance(value[1], str) and re.fullmatch(r"[0-9a-f]{64}", value[1]))
        if not ok:
            raise Unusable(f"{what}里的文件清单不对")
        out[name] = (value[0], value[1])
    return out


def _files(info, from_version, to_version):
    """patch.json 对不对：是从现在的版本升到要装的版本，每个文件的路径、大小、SHA-256 格式都对。返回 {路径: (大小, SHA-256)}。"""
    if not isinstance(info, dict) or info.get("from") != from_version or info.get("to") != to_version:
        raise Unusable(f"补丁包不是从 {from_version} 升到 {to_version} 的")
    return file_list(info.get("files"))


def apply(patch_zip, current, target, from_version, to_version):
    """按补丁包把新版本拼进 target（拼好后是 target/Evo/……）。current 是程序文件夹（装着 from_version）。
    对不上就抛出 Unusable；出了错 target 里可能留着拼了一半的文件，由调用的人删掉。返回 (从补丁包取了几个, 从本机复制了几个)。"""
    try:
        z = zipfile.ZipFile(patch_zip)
    except zipfile.BadZipFile as e:
        raise Unusable("补丁包打不开") from e
    with z:
        try:
            info = json.loads(z.read(INFO).decode("utf-8"))
        except (KeyError, ValueError) as e:
            raise Unusable("补丁包里没有文件清单") from e
        files = _files(info, from_version, to_version)
        inside = {name: name for name in z.namelist() if name != INFO}
        if any(name not in files for name in inside):
            raise Unusable("补丁包里有清单上没有的文件")
        return _assemble(z, inside, files, current, target, f" {from_version} ")


def compose(package_zip, current, target, to_version):
    """Mirror酱 的增量包：照里面的文件清单（MANIFEST）把 to_version 拼进 target（拼好后是 target/Evo/……）。
    current 是程序文件夹。增量包里的路径带不带最外层的 Evo/ 都行；changes.json 里的路径也要在 Evo 里面。
    对不上抛出 Unusable（不装）；出了错 target 里可能留着拼了一半的文件，由调用的人删掉。返回 (从增量包取了几个, 从本机复制了几个)。"""
    try:
        z = zipfile.ZipFile(package_zip)
    except zipfile.BadZipFile as e:
        raise Unusable("增量包打不开") from e
    with z:
        inside = {}  # Evo/<路径> → 增量包里的名字
        for name in z.namelist():
            if name == CHANGES or name.endswith("/"):
                continue
            full = _in_root(name)
            if full is None or not safe_name(full) or full in inside:
                raise Unusable("增量包里的文件位置不对")
            inside[full] = name
        _check_changes(z)
        if MANIFEST not in inside:
            raise Unusable("增量包里没有文件清单")
        try:
            info = json.loads(z.read(inside[MANIFEST]).decode("utf-8"))
        except ValueError as e:
            raise Unusable("增量包里的文件清单打不开") from e
        if not isinstance(info, dict) or info.get("version") != to_version:
            raise Unusable(f"增量包不是 {to_version} 的")
        files = file_list(info.get("files"), "增量包")
        if any(name != MANIFEST and name not in files for name in inside):
            raise Unusable("增量包里有清单上没有的文件")
        took, copied = _assemble(z, inside, files, current, target, "现在装着的版本")
        dst = Path(target).joinpath(*PurePosixPath(MANIFEST).parts)  # 清单自己也装上（下次更新还要用）
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(z.read(inside[MANIFEST]))
        return took + 1, copied


def _in_root(name):
    """增量包里的一个路径换成安装包里的写法（Evo/……）；写法本身就不对（绝对路径、盘符、反斜杠、空的一段、..）返回 None。"""
    if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name or ":" in name:
        return None
    parts = name.rstrip("/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return "/".join(parts) if parts[0] == ROOT else "/".join([ROOT, *parts])


def _check_changes(z):
    """changes.json（Mirror酱 写的，可以没有）：每一项都是路径的列表，路径都在 Evo 里面。不对抛出 Unusable。"""
    try:
        raw = z.read(CHANGES)
    except KeyError:
        return
    try:
        changes = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise Unusable("增量包里的 changes.json 打不开") from e
    if not isinstance(changes, dict):
        raise Unusable("增量包里的 changes.json 不对")
    for key in CHANGE_KEYS:
        paths = changes.get(key, [])
        if not isinstance(paths, list) or len(paths) > MAX_FILES:
            raise Unusable("增量包里的 changes.json 不对")
        for path in paths:
            full = _in_root(path)
            if full is None or len(PurePosixPath(full).parts) < 2:
                raise Unusable("增量包里的 changes.json 有不在 Evo 里的路径")


def _assemble(z, inside, files, current, target, from_label):
    """照清单 files 把新版本写进 target：inside（Evo/<路径> → 压缩包里的名字）里有的从压缩包 z 取，
    没有的从程序文件夹 current 复制。每个文件都核对大小和 SHA-256，对不上抛出 Unusable。返回 (取了几个, 复制了几个)。"""
    current, target = Path(current), Path(target)
    took = 0
    for name, (size, digest) in files.items():
        dst = target.joinpath(*PurePosixPath(name).parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if name in inside:
            took += 1
            with z.open(inside[name]) as src, open(dst, "wb") as out:
                got = _hash_copy(src, out)
        else:
            local = current.joinpath(*PurePosixPath(name).parts[1:])
            if not local.is_file() or local.stat().st_size != size:
                raise Unusable(f"本机的 {name.removeprefix(ROOT + '/')} 和{from_label}的不一样（或者没有了）")
            with open(local, "rb") as src, open(dst, "wb") as out:
                got = _hash_copy(src, out)
            shutil.copystat(local, dst)
        if got != (size, digest):
            raise Unusable(f"{name.removeprefix(ROOT + '/')} 的 SHA-256 对不上")
    return took, len(files) - took
