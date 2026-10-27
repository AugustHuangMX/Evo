// Execute the real Worker against SQLite through a small D1-compatible adapter. No network or production data.
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import worker from "../server/stats/worker.js";

const checks = {};
const fixture = JSON.parse(readFileSync(0, "utf8") || "{}");
class D1 {
  constructor(schema) { this.db = new DatabaseSync(":memory:"); this.db.exec(schema); }
  prepare(sql) {
    const db = this.db;
    return {
      sql, args: [],
      bind(...args) { return { ...this, args }; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      async all() { return { results: db.prepare(sql).all(...this.args) }; },
      async run() { return { meta: { changes: Number(db.prepare(sql).run(...this.args).changes) } }; },
    };
  }
  async batch(statements) {
    this.db.exec("BEGIN");
    try {
      const out = statements.map((s) => {
        const statement = this.db.prepare(s.sql);
        const args = /\?\d/.test(s.sql) ? [Object.fromEntries(s.args.map((v,i) => [String(i+1),v]))] : s.args;
        return statement.columns().length ? { results: statement.all(...args), meta: { changes: 0 } }
          : { meta: { changes: Number(statement.run(...args).changes) } };
      });
      this.db.exec("COMMIT"); return out;
    } catch (error) { this.db.exec("ROLLBACK"); throw error; }
  }
  count(table) { return this.db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n; }
}
const schema = readFileSync(new URL("../server/stats/schema.sql", import.meta.url), "utf8");
const migration = readFileSync(new URL("../server/stats/add_match_id.sql", import.meta.url), "utf8");
const a = "a".repeat(32), b = "b".repeat(32), uid = "c".repeat(32);
const ka = "1".repeat(20), kb = "2".repeat(20);
const match = (k, extra = {}) => ({ k, match_id: uid, day: "2026-10-26", mode: "ranked", legend: "恶灵",
  mates: ["命脉", "罗芭"], placement: 3, kills: 4, damage: 1000, ...extra });
async function post(db, id, matches, extra = {}) {
  const response = await worker.fetch(new Request("https://unit.invalid/v1/report", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, version: "0.4.0", matches, ...extra }),
  }), { DB: db });
  return { status: response.status, ...await response.json() };
}
for (const order of [[a,b],[b,a]]) {
  const db = new D1(schema);
  for (const id of order) assert.equal((await post(db, id, [match(id === a ? ka : kb)])).status, 200);
  assert.equal(db.count("matches"), 1); assert.equal(db.count("match_keys"), 2);
  checks["同一全局 match_id 在两种设备到达顺序下均只计一局"] = true;
  const result = await post(db, b, [match(kb, { damage: 1500 })]);
  assert.equal(result.match_id_version, 1);
  assert.equal(db.db.prepare("SELECT damage FROM matches").get().damage, 1500);
  assert.equal(db.count("matches"), 1);
  checks["同步编辑覆盖同一行，返回去重协议确认"] = true;
  // A different local integer/key on another import must not change global identity.
  await post(db, a, [match("3".repeat(20))]); assert.equal(db.count("matches"), 1);
  checks["同一对局的设备内键改变仍按全局 ID 合并"] = true;
  const old = match(kb, { damage: 1700 }); delete old.match_id;
  await post(db, b, [old]); assert.equal(db.count("matches"), 1);
  assert.equal(db.db.prepare("SELECT damage FROM matches").get().damage, 1700);
  checks["已登记别名的旧客户端重发不重新制造重复"] = true;
  const summary = await worker.fetch(new Request("https://unit.invalid/v1/summary"), { DB: db });
  assert.equal(summary.status, 200);
  assert.equal((await summary.json()).slices["all.ranked.all"].matches, 1);
  checks["真实汇总接口只统计去重后的一局"] = true;
  db.db.close();
}
{
  const db = new D1(schema);
  await post(db, a, [match(ka)]);
  await post(db, b, [match(ka, { match_id: "d".repeat(32) })]);
  assert.equal(db.count("matches"), 2);
  checks["不同对局即使设备内键相同也不会误合并"] = true;
  await post(db, a, [match(ka, { match_id: "d".repeat(32) })]);
  assert.equal(db.count("matches"), 2);
  await post(db, a, [match(ka, { match_id: "e".repeat(32) })]);
  assert.equal(db.count("matches"), 3);
  checks["复制过匿名编号的设备即使 id/k 完全相同，仍按不同全局 ID 保留不同局"] = true;
  const previous = JSON.stringify(db.db.prepare("SELECT * FROM matches ORDER BY match_id").all());
  const ambiguous = match(ka, { damage: 9999 }); delete ambiguous.match_id;
  const refused = await post(db, a, [ambiguous]);
  assert.equal(refused.stored, 0); assert.equal(refused.rejected, 1);
  assert.equal(JSON.stringify(db.db.prepare("SELECT * FROM matches ORDER BY match_id").all()), previous);
  checks["旧键对应多局时拒绝没有稳定 ID 的旧格式，不猜测覆盖或生成重复"] = true;
  const before = db.count("matches");
  const bad = await post(db, a, [match(kb, { match_id: 1 }), match(kb, { match_id: "../bad" })]);
  assert.equal(bad.rejected, 2); assert.equal(db.count("matches"), before);
  checks["无效全局 ID 拒绝且不回退为新旧重复记录"] = true;
  db.db.close();
}
{
  // Load the actual publicly released schema, so migration is tested against historical structure.
  const oldSchema = execFileSync("git", ["show", "457a159:server/stats/schema.sql"], {
    cwd: new URL("../", import.meta.url), encoding: "utf8",
  });
  const db = new D1(oldSchema);
  for (const [id,k] of [[a,ka],[b,kb]]) db.db.prepare("INSERT INTO matches(id,k,received_at) VALUES (?,?,?)").run(id,k,"2026-10-26");
  db.db.exec(migration); assert.equal(db.count("matches"), 2);
  checks["服务迁移保留旧匿名行，不盲目按内容删除历史数据"] = true;
  await post(db, a, [match(ka)]); await post(db, b, [match(kb)]);
  assert.equal(db.count("matches"), 1); assert.equal(db.count("match_keys"), 2);
  checks["两端补报稳定 ID 后，已存在的两条旧上报归并成一行"] = true;
  const missing = match("4".repeat(20)); delete missing.match_id;
  await post(db, "e".repeat(32), [missing]); assert.equal(db.count("matches"), 2);
  checks["没有稳定身份的历史/旧客户端行继续兼容且不猜测合并"] = true;
  db.db.exec("CREATE TRIGGER fail_match BEFORE INSERT ON matches WHEN NEW.damage=43210 BEGIN SELECT RAISE(ABORT,'injected'); END");
  const before = [db.count("matches"), db.count("match_keys"), db.count("pings")];
  const failed = await post(db, "f".repeat(32), [match("5".repeat(20), { damage: 43210 })], { ping: { day: "2026-10-26" } });
  assert.equal(failed.status, 500);
  assert.deepEqual([db.count("matches"),db.count("match_keys"),db.count("pings")], before);
  checks["批处理中写入失败时别名、记录与日活共同回滚"] = true;
  db.db.exec("DROP TRIGGER fail_match");
  const removed = await worker.fetch(new Request("https://unit.invalid/v1/delete", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: b }),
  }), { DB: db });
  assert.equal(removed.status, 200);
  assert.equal(db.db.prepare("SELECT COUNT(*) AS n FROM match_keys WHERE id=?").get(b).n, 0);
  checks["旧删除接口同时清理该设备的身份别名"] = true;
  db.db.close();
}
if (fixture.reports) {
  assert.equal(fixture.local_ids_differ, true);
  const db = new D1(schema);
  for (const report of fixture.reports) assert.equal((await post(db,report.id,report.matches)).status, 200);
  assert.equal(db.count("matches"), 2);
  assert.equal(db.count("match_keys"), 4);
  checks["真实 WebDAV 同步后的不同本地整数 ID，上报同一全局 match_id"] = true;
  checks["真实 Python 上报载荷经 Worker 与 SQLite，四次上报仅有两局"] = true;
  db.db.close();
}
console.log(JSON.stringify(checks));
