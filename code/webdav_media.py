"""可选的 WebDAV 截图传输。远端只提供哈希/大小/类型，不能指定本机文件路径。"""

import hashlib
import io
import re
from pathlib import Path

from PIL import Image

import paths

MAX_IMAGE = 20 * 1024 * 1024


def valid(ref):
    return (isinstance(ref, dict) and isinstance(ref.get("digest"), str)
            and re.fullmatch(r"[0-9a-f]{64}", ref["digest"]) is not None
            and isinstance(ref.get("size"), int) and 0 < ref["size"] <= MAX_IMAGE
            and ref.get("ext") in ("png", "jpg"))


def reference(conn, uid):
    row = conn.execute("SELECT digest, size, ext FROM sync_media WHERE uid=?", (uid,)).fetchone()
    return dict(row) if row else None


def remember(conn, uid, ref):
    if valid(ref):
        conn.execute("INSERT OR REPLACE INTO sync_media VALUES (?, ?, ?, ?)",
                     (uid, ref["digest"], ref["size"], ref["ext"]))


def _image(raw, expected=None):
    if not 0 < len(raw) <= MAX_IMAGE:
        raise ValueError("截图超过 20 MB 或内容为空")
    with Image.open(io.BytesIO(raw)) as image:
        ext = {"PNG": "png", "JPEG": "jpg"}.get(image.format)
        if ext is None or image.width * image.height > 40_000_000:
            raise ValueError("不是支持的截图或尺寸太大")
        image.verify()
    ref = {"digest": hashlib.sha256(raw).hexdigest(), "size": len(raw), "ext": ext}
    if expected is not None and ref != expected:
        raise ValueError("截图哈希、大小或类型校验失败")
    return ref


def sync(conn, cfg, data_dir=None):
    import webdav_sync as dav

    stats = {"images_uploaded": 0, "images_downloaded": 0, "image_errors": 0}
    if not cfg.get("screenshots"):
        return stats
    root = Path(data_dir or paths.DATA_DIR)
    image_cfg = {**cfg, "folder": cfg["folder"] + "/screenshots"}
    image_url = dav.ensure_folder(image_cfg)
    for row in conn.execute("SELECT match_id, sync_uid, evidence_path FROM matches").fetchall():
        try:
            local = Path(row["evidence_path"]) if row["evidence_path"] else None
            if local is not None and not local.is_absolute():
                local = root / local
            if local is not None and local.is_file():
                if local.stat().st_size > MAX_IMAGE:
                    raise ValueError("截图超过 20 MB")
                raw = local.read_bytes()
                ref = _image(raw)
                url = image_url + ref["digest"] + "." + ref["ext"]
                entries = dav.propfind(cfg, url)
                if not entries or entries[0].get("size") != ref["size"]:
                    dav.upload(cfg, url, raw)
                    stats["images_uploaded"] += 1
                remember(conn, row["sync_uid"], ref)
                conn.commit()
                continue
            ref = reference(conn, row["sync_uid"])
            if ref is None:
                continue
            name = ref["digest"] + "." + ref["ext"]
            target = root / "captures" / "webdav" / name
            if target.is_file():
                _image(target.read_bytes(), expected=ref)
            else:
                raw = dav.download(cfg, image_url + name)
                _image(raw, expected=ref)
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_suffix(target.suffix + ".tmp")
                try:
                    part.write_bytes(raw)
                    part.replace(target)
                finally:
                    part.unlink(missing_ok=True)
                stats["images_downloaded"] += 1
            conn.execute("UPDATE matches SET evidence_path=? WHERE match_id=?",
                         (str(target.relative_to(root)), row["match_id"]))
            conn.commit()
        except (dav.SyncError, OSError, ValueError, Image.DecompressionBombError):
            conn.rollback()
            stats["image_errors"] += 1
    return stats
