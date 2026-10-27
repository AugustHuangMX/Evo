"""运行真实统计 Worker 和 SQLite 去重/迁移测试。需要支持 node:sqlite 的 Node，仅用于开发测试。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys

def synchronized_reports():
    import test_webdav_sync as t
    import telemetry
    with t.Server() as server:
        root_a, root_b = t.fresh("stats-identity/a"), t.fresh("stats-identity/b")
        a, b = t.open_db(root_a), t.open_db(root_b)
        try:
            original = t.add_match(a, "2026-10-20 20:00:00")
            t.add_match(b, "2026-10-20 21:00:00")  # 本地整数 1 已用于另一局
            uid = t.storage.get_match(a, original)["sync_uid"]
            cfg = t.config(server)
            t.webdav_sync.sync(a, cfg=cfg, data_dir=root_a)
            t.webdav_sync.sync(b, cfg=cfg, data_dir=root_b)
            imported = b.execute("SELECT match_id FROM matches WHERE sync_uid=?", (uid,)).fetchone()[0]
            assert original != imported
            t.webdav_sync.sync(a, cfg=cfg, data_dir=root_a)
            reports = [{"id": "a"*32, "matches": [telemetry.match_payload(a,r,"a"*32) for r in a.execute("SELECT * FROM matches")]},
                       {"id": "b"*32, "matches": [telemetry.match_payload(b,r,"b"*32) for r in b.execute("SELECT * FROM matches")]}]
            assert {m['match_id'] for m in reports[0]['matches']} == {m['match_id'] for m in reports[1]['matches']}
            return {"reports": reports, "local_ids_differ": True}
        finally:
            a.close(); b.close()

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    node = shutil.which("node")
    if not node:
        print("  ⏭ 需要支持 node:sqlite 的 Node，跳过统计服务测试")
        return None
    probe = subprocess.run([node, "--input-type=module", "-e", "import 'node:sqlite'"], capture_output=True)
    if probe.returncode:
        print("  ⏭ 当前 Node 不支持 node:sqlite，跳过统计服务测试")
        return None
    result = subprocess.run([node, str(Path(__file__).with_name("stats_worker_checks.mjs"))],
                            input=json.dumps(synchronized_reports()), capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        print(result.stdout, result.stderr)
        return False
    checks = json.loads(result.stdout)
    for text, ok in checks.items():
        print(f"  {'✅' if ok else '❌'}  {text}")
    return all(checks.values())

if __name__ == "__main__":
    raise SystemExit(1 if main() is False else 0)
