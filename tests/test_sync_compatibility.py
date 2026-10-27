"""使用历史存储实现验证迁移、旧版往返、DPAPI 和可选截图。只使用临时数据。"""

import sqlite3
import subprocess
import types
from unittest.mock import patch
from PIL import Image
import test_webdav_sync as t
import secure_secret
import webdav_media

s, w = t.storage, t.webdav_sync


def historical_storage(ref):
    result = subprocess.run(["git", "show", f"{ref}:code/storage.py"], cwd=t._ROOT,
                            capture_output=True, text=True, encoding="utf-8", check=True)
    module = types.ModuleType("historical_storage")
    exec(compile(result.stdout, "historical_storage.py", "exec"), module.__dict__)
    return module


def migration_checks():
    out = {}
    old = historical_storage("457a159")
    for version in range(1, 9):
        folder = t.fresh(f"compat/v{version}")
        db = folder / "data" / "evo.db"; db.parent.mkdir(parents=True)
        raw = sqlite3.connect(db)
        cols = {k: v for k, v in old.COLUMNS.items() if k not in ("player_name", "participation", "edited")}
        raw.execute("CREATE TABLE matches (" + ",".join(f"{k} {v}" for k, v in cols.items()) + ")")
        for number in range(2, version + 1):
            for step in old.MIGRATIONS[number]:
                old._migrate(raw, step)
        raw.execute("INSERT INTO matches(match_id, ended_at, kills, damage, evidence_path) VALUES (7, ?, 4, NULL, ?)",
                    ("2026-10-01 12:00:00", "captures/original.png"))
        raw.execute(f"PRAGMA user_version={version}"); raw.commit(); raw.close()
        conn = s.connect(db)
        backup = sqlite3.connect(next(db.parent.glob("evo.db.before-*.bak")))
        row = s.get_match(conn, 7)
        out[f"schema {version} → 10：备份完整，原行 ID、数值、NULL、路径不变"] = (
            conn.execute("PRAGMA user_version").fetchone()[0] == 10 and row["kills"] == 4
            and row["damage"] is None and row["evidence_path"] == "captures/original.png"
            and backup.execute("PRAGMA user_version").fetchone()[0] == version
            and backup.execute("SELECT match_id,kills,damage,evidence_path FROM matches").fetchone()
            == (7, 4, None, "captures/original.png")
            and backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
        backup.close()
        old_conn = old.connect(db)
        old.update_match(old_conn, 7, {"damage": 9876})
        old.insert_match(old_conn, {"ended_at": "2026-10-02 12:00:00", "kills": 9}); old_conn.close()
        snap = w.export_snapshot(conn, folder)
        out[f"schema {version} 升级后旧 0.4.0 仍可读写，再回新版完整"] = (
            s.get_match(conn, 7)["damage"] == 9876 and len(snap["matches"]) == 2
            and conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
        conn.close()
    folder = t.fresh("compat/v9")
    prior = historical_storage("64d9157")
    conn = prior.connect(folder / "data" / "evo.db")
    mid = prior.insert_match(conn, {"damage": 123}); uid = prior.get_match(conn, mid)["sync_uid"]; conn.close()
    conn = t.open_db(folder); w.export_snapshot(conn, folder)
    before = w._revision(conn, "match", uid)["counter"]
    old_conn = old.connect(folder / "data" / "evo.db")
    old.update_match(old_conn, mid, {"damage": 456}); snap = w.export_snapshot(conn, folder)
    out["v9 升级后经旧版编辑，可检测并递增逻辑版本"] = (
        snap["matches"][0]["fields"]["damage"] == 456
        and snap["matches"][0]["revision"]["counter"] == before + 1)
    old.delete_match(old_conn, mid); snap = w.export_snapshot(conn, folder)
    out["旧版删除后回新版补墓碑，远端不能复活"] = not snap["matches"] and any(r["uid"] == uid for r in snap["tombstones"])
    old_conn.close(); conn.close()
    folder = t.fresh("compat/rollback"); db = folder / "data" / "evo.db"
    conn = old.connect(db); old.insert_match(conn, {"damage": 321}); conn.close()
    original = s._migrate
    def fail(conn, step):
        if step == s.SYNC_VERSION_TABLE:
            raise RuntimeError("injected migration failure")
        return original(conn, step)
    try:
        with patch.object(s, "_migrate", fail):
            s.connect(db)
    except RuntimeError:
        pass
    raw = sqlite3.connect(db)
    out["迁移失败完整回滚 v8，战绩和备份保留"] = (
        raw.execute("PRAGMA user_version").fetchone()[0] == 8
        and raw.execute("SELECT damage FROM matches").fetchone()[0] == 321
        and "sync_uid" not in {r[1] for r in raw.execute("PRAGMA table_info(matches)")}
        and bool(list(db.parent.glob("evo.db.before-*.bak"))))
    raw.execute("PRAGMA user_version=999"); raw.commit(); raw.close()
    try:
        s.connect(db)
    except RuntimeError:
        raw = sqlite3.connect(db)
        out["未来版本数据库拒绝打开，不降级不改数据"] = raw.execute("PRAGMA user_version").fetchone()[0] == 999
        raw.close()
    else:
        out["未来版本数据库拒绝打开，不降级不改数据"] = False
    return out


def credential_checks():
    out = {}
    cfg = w.save_config("https://dav.example/dav", t.USER, t.PASSWORD, "Evo")
    saved = t.app_settings.get(w.SETTINGS_KEY)
    out["DPAPI 密文可解密，settings.json 无明文密码键或密码"] = (
        "password" not in saved and t.PASSWORD not in t.app_settings.SETTINGS_PATH.read_text(encoding="utf-8")
        and secure_secret.unprotect(saved["password_dpapi"]) == t.PASSWORD)
    t.app_settings.set_value(w.SETTINGS_KEY, cfg)
    out["旧明文配置自动加密并移除明文键"] = w.config()["password"] == t.PASSWORD and "password" not in t.app_settings.get(w.SETTINGS_KEY)
    with patch.object(secure_secret, "unprotect", side_effect=secure_secret.SecretError("different user")):
        out["换 Windows 账号解密失败时停止联网并提示重填"] = w.config() is None and w.view()["credential_error"]
    t.app_settings.set_value(w.SETTINGS_KEY, dict(t.app_settings.get(w.SETTINGS_KEY), password_dpapi="broken!"))
    out["密文损坏时不回退明文、不启动同步"] = w.config() is None and not w.auto_enabled()
    w.clear_config()
    return out


def media_checks():
    out = {}
    with t.Server() as server:
        cfg = t.config(server); af, bf = t.fresh("media/a"), t.fresh("media/b")
        a, b = t.open_db(af), t.open_db(bf)
        image = af / "captures" / "real.png"; image.parent.mkdir(parents=True)
        Image.new("RGB", (120, 80), (220, 30, 50)).save(image)
        mid = t.add_match(a, "2026-10-26 10:00:00", evidence_path="captures/real.png"); uid = s.get_match(a, mid)["sync_uid"]
        w.sync(a, cfg, af); w.sync(b, cfg, bf)
        out["截图默认关闭，不发图片请求或传本机路径"] = (
            not any("/screenshots/" in p for _, p in server.state.requests) and t.match_by_uid(b, uid)["evidence_path"] is None)
        on = dict(cfg, screenshots=True); result = w.sync(a, on, af); w.sync(b, cfg, bf)
        out["关闭截图的设备仍收战绩但不下载图片"] = result["images_uploaded"] == 1 and t.match_by_uid(b, uid)["evidence_path"] is None
        result = w.sync(b, on, bf); target = bf / t.match_by_uid(b, uid)["evidence_path"]
        out["开启后补下载，图片哈希一致且使用安全本机路径"] = (
            result["images_downloaded"] == 1 and target.read_bytes() == image.read_bytes()
            and target.parent == bf / "captures" / "webdav")
        result = w.sync(b, on, bf)
        out["截图无变化不重复上传下载"] = result["images_uploaded"] == result["images_downloaded"] == 0
        ref = webdav_media.reference(b, uid); target.unlink()
        server.state.files["/dav/Evo/screenshots/" + ref["digest"] + "." + ref["ext"]] = b"corrupt image"
        result = w.sync(b, on, bf)
        out["损坏图片拒绝落盘并报告，正常战绩保留"] = result["image_errors"] == 1 and not target.exists() and t.match_by_uid(b, uid) is not None
        out["截图引用拒绝路径穿越、超大和任意文件格式"] = all(not webdav_media.valid(ref) for ref in (
            {"digest": "../file", "size": 1, "ext": "png"}, {"digest": "a"*64, "size": webdav_media.MAX_IMAGE+1, "ext": "png"},
            {"digest": "a"*64, "size": 10, "ext": "exe"}))
        a.close(); b.close()
    return out


def main():
    checks = {}
    for part in (migration_checks, credential_checks, media_checks):
        checks.update(part())
    return t.report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
