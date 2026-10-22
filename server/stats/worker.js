// Evo 匿名统计的接收程序（0.3.7 起，2026-10-22 用户决定）：Cloudflare Worker，数据存在 D1 数据库（表见 schema.sql）。
// 地址 https://stats.evotracker.cn。只有用户在 Evo 里同意了才会发过来（code/telemetry.py）。
//
// - POST /v1/report：{"id": 匿名编号, "version": Evo 版本号, "ping": 日活（每天一份，可以没有）, "matches": [每局，最多 100 个]}
//   日活按（编号, 日期）存一行，每局按（编号, 对局编号）存一行，再发同一个就覆盖
// - POST /v1/delete：{"id": 匿名编号}：删掉这个编号发过的所有数据（Evo 设置页的"关闭并删除已发送的数据"）
// 只收认识的项，格式不对的那一局不存（整份格式不对就回 400）。不存 IP 地址、不写日志、不放 Cookie；
// 不回 CORS 头，网页里的脚本没法往这里发。
// 数据不自动删（2026-10-22 用户决定：留着做长期统计，比如看各赛季的变化）。
//
// 部署：cd server/stats && wrangler deploy（第一次：wrangler d1 create evo-stats --location apac，
// 把 database_id 填进 wrangler.toml，再 wrangler d1 execute evo-stats --remote --file schema.sql）
// 看统计：python tools/stats_report.py

const MAX_BODY = 256 * 1024;
const MAX_MATCHES = 100;
const ID = /^[0-9a-f]{32}$/;
const KEY = /^[0-9a-f]{20}$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const SIZE = /^\d{2,5}x\d{2,5}$/;
const GAME_MODES = new Set(["fullscreen", "borderless", "windowed"]);
const ASPECTS = new Set(["auto", "16:9", "16:10", "4:3"]);
// 范围放得比游戏里可能的宽：只挡明显的乱数据，不误删少见的真实数据
const NUMBERS = { placement: [1, 99], kills: [0, 999], assists: [0, 999], knockdowns: [0, 999], damage: [0, 999999],
                  rp_delta: [-9999, 9999] };
const TEXTS = ["mode", "map", "legend", "tier", "rp_source"];
const EDITABLE = new Set(["placement", "kills", "assists", "knockdowns", "participation", "damage", "rp_delta"]);

class Bad extends Error {}

function reply(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

// 可以为空的短文字（传奇、地图……）；太长、不是文字就当作格式不对
function text(value, max = 40) {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || value.length > max) throw new Bad();
  return value;
}

function pattern(value, re) {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || !re.test(value)) throw new Bad();
  return value;
}

function number(value, [low, high]) {
  if (value === null || value === undefined) return null;
  if (!Number.isInteger(value) || value < low || value > high) throw new Bad();
  return value;
}

function ping(p) {
  if (typeof p !== "object" || p === null) throw new Bad();
  const mode = p.game_mode ?? null;
  const aspect = p.aspect ?? null;
  if ((mode !== null && !GAME_MODES.has(mode)) || (aspect !== null && !ASPECTS.has(aspect))) throw new Bad();
  if (typeof p.day !== "string" || !DAY.test(p.day)) throw new Bad();
  return { day: p.day, screen: pattern(p.screen, SIZE), game_res: pattern(p.game_res, SIZE), game_mode: mode, aspect };
}

function match(m) {
  if (typeof m !== "object" || m === null || typeof m.k !== "string" || !KEY.test(m.k)) throw new Bad();
  const mates = m.mates ?? [];
  if (!Array.isArray(mates) || mates.length > 2) throw new Bad();
  const edited = m.edited ?? [];
  if (!Array.isArray(edited) || edited.some((f) => !EDITABLE.has(f))) throw new Bad();
  if (m.review !== undefined && typeof m.review !== "boolean") throw new Bad();
  const out = { k: m.k, day: pattern(m.day, DAY), version: text(m.version, 20),  // 版本号只限长度：旧数据、演示数据可能不是 x.y.z
                mate1: text(mates[0]), mate2: text(mates[1]), review: m.review ? 1 : 0,
                edited: edited.length ? [...new Set(edited)].sort().join(",") : null };
  for (const f of TEXTS) out[f] = text(m[f]);
  for (const [f, range] of Object.entries(NUMBERS)) out[f] = number(m[f], range);
  return out;
}

async function readJson(request) {
  if (!(request.headers.get("Content-Type") || "").startsWith("application/json")) throw new Bad();
  if (Number(request.headers.get("Content-Length") || 0) > MAX_BODY) throw new Bad();
  const raw = await request.text();
  if (raw.length > MAX_BODY) throw new Bad();
  const body = JSON.parse(raw);
  if (typeof body !== "object" || body === null || typeof body.id !== "string" || !ID.test(body.id)) throw new Bad();
  return body;
}

async function report(body, env) {
  const version = text(body.version, 20);
  const now = new Date().toISOString();
  const statements = [];
  if (body.ping !== undefined && body.ping !== null) {
    const p = ping(body.ping);
    statements.push(env.DB.prepare(
      `INSERT INTO pings (id, day, version, screen, game_res, game_mode, aspect, received_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT (id, day) DO UPDATE SET version = excluded.version, screen = excluded.screen,
         game_res = excluded.game_res, game_mode = excluded.game_mode, aspect = excluded.aspect,
         received_at = excluded.received_at`,
    ).bind(body.id, p.day, version, p.screen, p.game_res, p.game_mode, p.aspect, now));
  }
  const matches = body.matches ?? [];
  if (!Array.isArray(matches) || matches.length > MAX_MATCHES) throw new Bad();
  let rejected = 0;
  for (const raw of matches) {
    let m;
    try {
      m = match(raw);
    } catch (e) {
      if (!(e instanceof Bad)) throw e;
      rejected += 1; // 这一局格式不对：不存，别的照收（Evo 那边算发过了，不会一直重发）
      continue;
    }
    statements.push(env.DB.prepare(
      `INSERT INTO matches (id, k, day, version, mode, map, legend, mate1, mate2, placement, kills, assists,
         knockdowns, damage, rp_delta, tier, rp_source, review, edited, received_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT (id, k) DO UPDATE SET day = excluded.day, version = excluded.version, mode = excluded.mode,
         map = excluded.map, legend = excluded.legend, mate1 = excluded.mate1, mate2 = excluded.mate2,
         placement = excluded.placement, kills = excluded.kills, assists = excluded.assists,
         knockdowns = excluded.knockdowns, damage = excluded.damage, rp_delta = excluded.rp_delta,
         tier = excluded.tier, rp_source = excluded.rp_source, review = excluded.review, edited = excluded.edited,
         received_at = excluded.received_at`,
    ).bind(body.id, m.k, m.day, m.version, m.mode, m.map, m.legend, m.mate1, m.mate2, m.placement, m.kills,
           m.assists, m.knockdowns, m.damage, m.rp_delta, m.tier, m.rp_source, m.review, m.edited, now));
  }
  if (statements.length) await env.DB.batch(statements);
  return reply({ ok: true, stored: matches.length - rejected, rejected });
}

async function remove(body, env) {
  const results = await env.DB.batch([
    env.DB.prepare("DELETE FROM pings WHERE id = ?").bind(body.id),
    env.DB.prepare("DELETE FROM matches WHERE id = ?").bind(body.id),
  ]);
  return reply({ ok: true, deleted: results.reduce((n, r) => n + (r.meta?.changes ?? 0), 0) });
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (request.method === "GET" && pathname === "/") {
      return new Response("Evo 匿名统计的接收地址（只收 Evo 用户同意后发送的数据）。说明见 https://evotracker.cn/privacy.html\n",
                          { headers: { "Content-Type": "text/plain; charset=utf-8" } });
    }
    const handler = { "/v1/report": report, "/v1/delete": remove }[pathname];
    if (handler === undefined) return reply({ ok: false, error: "not found" }, 404);
    if (request.method !== "POST") return reply({ ok: false, error: "method not allowed" }, 405);
    try {
      return await handler(await readJson(request), env);
    } catch (e) {
      if (e instanceof Bad || e instanceof SyntaxError) return reply({ ok: false, error: "bad request" }, 400);
      return reply({ ok: false, error: "server error" }, 500);
    }
  },
};
