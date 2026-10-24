// Evo 匿名统计的接收程序（0.3.7 起，2026-10-22 用户决定）：Cloudflare Worker，数据存在 D1 数据库（表见 schema.sql）。
// 地址 https://stats.evotracker.cn。只有用户在 Evo 里同意了才会发过来（code/telemetry.py）。
//
// - POST /v1/report：{"id": 匿名编号, "version": Evo 版本号, "ping": 日活（每天一份，可以没有）, "matches": [每局，最多 100 个]}
//   日活按（编号, 日期）存一行，每局按（编号, 对局编号）存一行，再发同一个就覆盖
// - POST /v1/delete：{"id": 匿名编号}：删掉这个编号发过的所有数据（Evo 0.3.7、0.3.8 设置页的"关闭并删除已发送的数据"；
//   之后的版本去掉了这个按钮，2026-10-24 用户决定。老版本还在用，这里留着）
// - GET /v1/summary：全体玩家的汇总（Evo 数据页"全体玩家"，2026-10-24 用户决定；格式见 makeSummary）。
//   定时任务每小时算一次存进 summary 表（有新数据或换了一天才重算），这里只读出来；还没算过就现算一次。
//   只有汇总的数字，没有任何一个编号的数据。Evo 里只有开了匿名统计的人才下载（限制在 Evo 那边，这个地址本身是公开的）
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
const MATE_NUMBERS = ["kills", "assists", "knockdowns", "damage"];  // 队友每局的数字（mate_stats，0.3.8 之后的 Evo 才发）
const EDITABLE = new Set(["placement", "kills", "assists", "knockdowns", "participation", "damage", "rp_delta"]);

const MATE_COLUMNS = [1, 2].flatMap((i) => MATE_NUMBERS.map((f) => `mate${i}_${f}`));

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
  const stats = m.mate_stats ?? [];  // 和 mates 一一对应；老版本的 Evo 没有这一项
  if (!Array.isArray(stats) || stats.length > 2) throw new Bad();
  for (const i of [0, 1]) {
    const one = stats[i] ?? {};
    if (typeof one !== "object" || Array.isArray(one)) throw new Bad();
    for (const f of MATE_NUMBERS) out[`mate${i + 1}_${f}`] = number(one[f], NUMBERS[f]);
  }
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
         knockdowns, damage, rp_delta, tier, rp_source, review, edited, received_at, ${MATE_COLUMNS.join(", ")})
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ${MATE_COLUMNS.map(() => "?").join(", ")})
       ON CONFLICT (id, k) DO UPDATE SET day = excluded.day, version = excluded.version, mode = excluded.mode,
         map = excluded.map, legend = excluded.legend, mate1 = excluded.mate1, mate2 = excluded.mate2,
         placement = excluded.placement, kills = excluded.kills, assists = excluded.assists,
         knockdowns = excluded.knockdowns, damage = excluded.damage, rp_delta = excluded.rp_delta,
         tier = excluded.tier, rp_source = excluded.rp_source, review = excluded.review, edited = excluded.edited,
         received_at = excluded.received_at, ${MATE_COLUMNS.map((c) => `${c} = excluded.${c}`).join(", ")}`,
    ).bind(body.id, m.k, m.day, m.version, m.mode, m.map, m.legend, m.mate1, m.mate2, m.placement, m.kills,
           m.assists, m.knockdowns, m.damage, m.rp_delta, m.tier, m.rp_source, m.review, m.edited, now,
           ...MATE_COLUMNS.map((c) => m[c])));
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

// ---------- 全体玩家的汇总（GET /v1/summary）----------
// 范围（近 7 天 / 全部）× 模式（排位 / 匹配）× 段位（排位才分：全部 / 白金及以下 / 钻石 / 大师以上），共 10 份，
// 键是 "范围.模式.段位"，如 "all.ranked.all"、"7d.ranked.diamond"、"all.pubs.all"。
// "匹配"是不是排位的局：匹配、其他模式，和没认出模式的局（简体中文的匹配开局画面还没加进识别规则，
// 匹配局的模式都是空的；排位局有段位分数，几乎都认得出）。每份里：
// - players、matches：多少个编号、多少局
// - legends：每个传奇一行，按出场次数从多到少。全队三个人都算（队友多半是随机匹配到的；名次全队一样）：
//   n 出场次数、pick 选用率（n ÷ 所有出场次数）、win 胜率、top5 前五率、place 场均名次、kills 场均击杀、
//   damage 场均伤害。表现只算不是待确认的局；击杀、伤害只算读到了的（队友的要 0.3.8 之后的 Evo 才发）
// - combos：全队三个传奇（不管谁用的哪个）每种一行，局数最多的 TOP_COMBOS 种：legends（按名字排好）、
//   n 局数、rate 选取率（n ÷ 全队传奇都认出的局数）、place 场均名次。同一局两个一样的传奇（认错了）不算
// - people：每个编号（自己的局，打了 MIN_PLAYER_GAMES 局以上）的场均名次 place、胜率 win、场均击杀 kills、
//   场均伤害 damage，各自从小到大排好；人多时只留 MAX_POINTS 个等间隔的点。Evo 拿自己的数字和它比，算"超过 X% 的玩家"
// tiers（只分范围）：每个编号最近一局排位的段位，各段位多少人
// "近 7 天"按用户电脑上的日期，从 UTC 的今天往前数 6 天算起（recent_from）
// 读数据库：每算一次大约把 matches 表读 6 遍（D1 免费额度每天读 500 万行；局数上了几万、每小时都有新数据时要改成几小时算一次）
const SUMMARY_NAME = "v1";
const RECENT_DAYS = 7;
const TOP_COMBOS = 30;
const MIN_PLAYER_GAMES = 3;
const MAX_POINTS = 201;
const TIER_ORDER = ["菜鸟", "青铜", "白银", "黄金", "白金", "钻石", "大师", "Apex 猎杀者"];
const MODE = "CASE WHEN mode = 'ranked' THEN 'ranked' ELSE 'pubs' END";
const TIER_GROUP = `CASE WHEN tier IN ('菜鸟', '青铜', '白银', '黄金', '白金') THEN 'low' WHEN tier = '钻石' THEN 'diamond'
  WHEN tier IN ('大师', 'Apex 猎杀者') THEN 'master' END`;
// 表现：不算待确认的局
const PERFORMANCE = `SUM(review = 0 AND placement IS NOT NULL) AS np, SUM(CASE WHEN review = 0 THEN placement END) AS sp,
  SUM(review = 0 AND placement = 1) AS wins`;

function round(value, digits) {
  const f = 10 ** digits;
  return Math.round(value * f) / f;
}

function ratio(part, whole, digits) {
  return whole ? round(part / whole, digits) : null;
}

// 这一行（某个模式 m、段位组 g、是不是近 7 天）算进哪几份汇总
function sliceKeys(row) {
  const ranges = row.recent ? ["all", "7d"] : ["all"];
  const groups = row.m === "ranked" && row.g ? ["all", row.g] : ["all"];
  return ranges.flatMap((r) => groups.map((g) => `${r}.${row.m}.${g}`));
}

function emptySlices() {
  const out = {};
  for (const r of ["all", "7d"]) {
    for (const g of ["all", "low", "diamond", "master"]) out[`${r}.ranked.${g}`] = {};
    out[`${r}.pubs.all`] = {};
  }
  return out;
}

function addTo(target, row, fields) {
  for (const f of fields) target[f] = (target[f] ?? 0) + (row[f] ?? 0);
}

function points(values, digits) {
  const sorted = values.map((v) => round(v, digits)).sort((a, b) => a - b);
  if (sorted.length <= MAX_POINTS) return sorted;
  return Array.from({ length: MAX_POINTS }, (_, i) => sorted[Math.round((i * (sorted.length - 1)) / (MAX_POINTS - 1))]);
}

async function makeSummary(env, now) {
  const today = Date.parse(now.toISOString().slice(0, 10) + "T00:00:00Z");
  const recentFrom = new Date(today - (RECENT_DAYS - 1) * 86400000).toISOString().slice(0, 10);
  const [legendRows, comboRows, peopleRows, tierRows] = (await env.DB.batch([
    env.DB.prepare(
      `WITH s AS (
         SELECT mode, tier, day, review, placement, legend, kills, damage FROM matches
         UNION ALL SELECT mode, tier, day, review, placement, mate1, mate1_kills, mate1_damage FROM matches
         UNION ALL SELECT mode, tier, day, review, placement, mate2, mate2_kills, mate2_damage FROM matches)
       SELECT ${MODE} AS m, ${TIER_GROUP} AS g, day >= ?1 AS recent, legend, COUNT(*) AS n, ${PERFORMANCE},
         SUM(review = 0 AND placement <= 5) AS top5,
         SUM(review = 0 AND kills IS NOT NULL) AS nk, SUM(CASE WHEN review = 0 THEN kills END) AS sk,
         SUM(review = 0 AND damage IS NOT NULL) AS nd, SUM(CASE WHEN review = 0 THEN damage END) AS sd
       FROM s WHERE legend IS NOT NULL GROUP BY m, g, recent, legend`,
    ).bind(recentFrom),
    // 三个传奇按名字排好：最小、中间、最大（三个都不一样，中间那个就是既不是最小也不是最大的）
    env.DB.prepare(
      `WITH c AS (
         SELECT mode, tier, day, review, placement, legend, mate1, mate2,
           min(legend, mate1, mate2) AS a, max(legend, mate1, mate2) AS z FROM matches
         WHERE legend IS NOT NULL AND mate1 IS NOT NULL AND mate2 IS NOT NULL AND legend <> mate1 AND legend <> mate2
           AND mate1 <> mate2)
       SELECT ${MODE} AS m, ${TIER_GROUP} AS g, day >= ?1 AS recent, a,
         CASE WHEN legend NOT IN (a, z) THEN legend WHEN mate1 NOT IN (a, z) THEN mate1 ELSE mate2 END AS b, z,
         COUNT(*) AS n, ${PERFORMANCE}
       FROM c GROUP BY m, g, recent, a, b, z`,
    ).bind(recentFrom),
    env.DB.prepare(
      `SELECT id, ${MODE} AS m, ${TIER_GROUP} AS g, day >= ?1 AS recent, COUNT(*) AS n, ${PERFORMANCE},
         SUM(review = 0 AND kills IS NOT NULL) AS nk, SUM(CASE WHEN review = 0 THEN kills END) AS sk,
         SUM(review = 0 AND damage IS NOT NULL) AS nd, SUM(CASE WHEN review = 0 THEN damage END) AS sd
       FROM matches GROUP BY id, m, g, recent`,
    ).bind(recentFrom),
    env.DB.prepare(
      `SELECT id, tier, MAX(day) AS last FROM matches WHERE mode = 'ranked' AND tier IS NOT NULL AND day IS NOT NULL
       GROUP BY id, tier`,
    ),
  ])).map((r) => r.results);

  const legends = emptySlices();
  for (const row of legendRows) {
    for (const key of sliceKeys(row)) {
      addTo((legends[key][row.legend] ??= {}), row, ["n", "np", "sp", "wins", "top5", "nk", "sk", "nd", "sd"]);
    }
  }
  const combos = emptySlices();
  for (const row of comboRows) {
    for (const key of sliceKeys(row)) addTo((combos[key][`${row.a}|${row.b}|${row.z}`] ??= {}), row, ["n", "np", "sp"]);
  }
  const people = emptySlices();
  for (const row of peopleRows) {
    for (const key of sliceKeys(row)) {
      addTo((people[key][row.id] ??= {}), row, ["n", "np", "sp", "wins", "nk", "sk", "nd", "sd"]);
    }
  }

  const slices = {};
  for (const key of Object.keys(legends)) {
    const picks = Object.values(legends[key]).reduce((sum, l) => sum + l.n, 0);
    const squads = Object.values(combos[key]).reduce((sum, c) => sum + c.n, 0);
    const ids = Object.values(people[key]);
    const enough = (n) => n >= MIN_PLAYER_GAMES;
    slices[key] = {
      players: ids.length,
      matches: ids.reduce((sum, p) => sum + p.n, 0),
      legends: Object.entries(legends[key])
        .sort(([a, x], [b, y]) => y.n - x.n || (a < b ? -1 : 1))
        .map(([name, l]) => ({
          name, n: l.n, pick: ratio(l.n, picks, 4), win: ratio(l.wins, l.np, 4), top5: ratio(l.top5, l.np, 4),
          place: ratio(l.sp, l.np, 2), kills: ratio(l.sk, l.nk, 2), damage: ratio(l.sd, l.nd, 0),
        })),
      combos: Object.entries(combos[key])
        .map(([names, c]) => ({ legends: names.split("|"), n: c.n, rate: ratio(c.n, squads, 4), place: ratio(c.sp, c.np, 2) }))
        .sort((x, y) => y.n - x.n || (x.place ?? 99) - (y.place ?? 99))
        .slice(0, TOP_COMBOS),
      people: {
        place: points(ids.filter((p) => enough(p.np)).map((p) => p.sp / p.np), 2),
        win: points(ids.filter((p) => enough(p.np)).map((p) => p.wins / p.np), 4),
        kills: points(ids.filter((p) => enough(p.nk)).map((p) => p.sk / p.nk), 2),
        damage: points(ids.filter((p) => enough(p.nd)).map((p) => p.sd / p.nd), 0),
      },
    };
  }

  // 段位分布：每个编号最近一局排位的段位（同一天有两个段位时算高的那个）
  const rank = (tier) => TIER_ORDER.indexOf(tier);
  const tiers = {};
  for (const [range, from] of [["all", ""], ["7d", recentFrom]]) {
    const latest = {};
    for (const row of tierRows) {
      if (row.last < from) continue;
      const best = latest[row.id];
      if (!best || row.last > best.last || (row.last === best.last && rank(row.tier) > rank(best.tier))) latest[row.id] = row;
    }
    const counts = {};
    for (const row of Object.values(latest)) counts[row.tier] = (counts[row.tier] ?? 0) + 1;
    tiers[range] = { players: Object.keys(latest).length, counts };
  }
  return { v: 1, made_at: now.toISOString(), recent_from: recentFrom, slices, tiers };
}

// 算一次汇总存起来。force 为 false 时（定时任务）：没有新数据、也没换天，就不重算（少读数据库）
async function refreshSummary(env, force) {
  const now = new Date();
  const newest = (await env.DB.prepare("SELECT MAX(received_at) AS t FROM matches").first())?.t ?? null;
  if (!force) {
    const last = await env.DB.prepare("SELECT made_at, data_at FROM summary WHERE name = ?").bind(SUMMARY_NAME).first();
    if (last && last.data_at === newest && last.made_at.slice(0, 10) === now.toISOString().slice(0, 10)) return null;
  }
  const body = JSON.stringify(await makeSummary(env, now));
  await env.DB.prepare(
    `INSERT INTO summary (name, body, made_at, data_at) VALUES (?, ?, ?, ?)
     ON CONFLICT (name) DO UPDATE SET body = excluded.body, made_at = excluded.made_at, data_at = excluded.data_at`,
  ).bind(SUMMARY_NAME, body, now.toISOString(), newest).run();
  return body;
}

async function summary(env) {
  const row = await env.DB.prepare("SELECT body FROM summary WHERE name = ?").bind(SUMMARY_NAME).first();
  const body = row?.body ?? (await refreshSummary(env, true));
  return new Response(body, {
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=300" },
  });
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (request.method === "GET" && pathname === "/") {
      return new Response("Evo 匿名统计的接收地址（只收 Evo 用户同意后发送的数据）。说明见 https://evotracker.cn/privacy.html\n",
                          { headers: { "Content-Type": "text/plain; charset=utf-8" } });
    }
    if (request.method === "GET" && pathname === "/v1/summary") {
      try {
        return await summary(env);
      } catch (e) {
        return reply({ ok: false, error: "server error" }, 500);
      }
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

  // 定时任务（wrangler.toml 的 crons，每小时一次）：重算全体玩家的汇总
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(refreshSummary(env, false));
  },
};
