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
"""

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

INFO = "patch.json"  # 补丁包里的文件清单
ROOT = "Evo"  # 安装包里最外层的文件夹
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

def _files(info, from_version, to_version):
    """patch.json 对不对：是从现在的版本升到要装的版本，每个文件的路径、大小、SHA-256 格式都对。返回 {路径: (大小, SHA-256)}。"""
    if not isinstance(info, dict) or info.get("from") != from_version or info.get("to") != to_version:
        raise Unusable(f"补丁包不是从 {from_version} 升到 {to_version} 的")
    files = info.get("files")
    if not isinstance(files, dict) or not 0 < len(files) <= MAX_FILES or f"{ROOT}/Evo.exe" not in files:
        raise Unusable("补丁包里的文件清单不对")
    out = {}
    for name, value in files.items():
        ok = (safe_name(name) and isinstance(value, list) and len(value) == 2
              and isinstance(value[0], int) and not isinstance(value[0], bool) and value[0] >= 0
              and isinstance(value[1], str) and re.fullmatch(r"[0-9a-f]{64}", value[1]))
        if not ok:
            raise Unusable("补丁包里的文件清单不对")
        out[name] = (value[0], value[1])
    return out


def apply(patch_zip, current, target, from_version, to_version):
    """按补丁包把新版本拼进 target（拼好后是 target/Evo/……）。current 是程序文件夹（装着 from_version）。
    对不上就抛出 Unusable；出了错 target 里可能留着拼了一半的文件，由调用的人删掉。返回 (从补丁包取了几个, 从本机复制了几个)。"""
    current, target = Path(current), Path(target)
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
        inside = {name for name in z.namelist() if name != INFO}
        if any(name not in files for name in inside):
            raise Unusable("补丁包里有清单上没有的文件")
        took = 0
        for name, (size, digest) in files.items():
            dst = target.joinpath(*PurePosixPath(name).parts)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if name in inside:
                took += 1
                with z.open(name) as src, open(dst, "wb") as out:
                    got = _hash_copy(src, out)
            else:
                local = current.joinpath(*PurePosixPath(name).parts[1:])
                if not local.is_file() or local.stat().st_size != size:
                    raise Unusable(f"本机的 {name.removeprefix(ROOT + '/')} 和 {from_version} 的不一样（或者没有了）")
                with open(local, "rb") as src, open(dst, "wb") as out:
                    got = _hash_copy(src, out)
                shutil.copystat(local, dst)
            if got != (size, digest):
                raise Unusable(f"{name.removeprefix(ROOT + '/')} 的 SHA-256 对不上")
    return took, len(files) - took
