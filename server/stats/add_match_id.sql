-- 已有 D1 只运行一次：先备份数据库，在部署新版 worker.js 之前运行本迁移。
-- wrangler d1 export evo-stats --remote --output before-match-id.sql
-- wrangler d1 execute evo-stats --remote --file add_match_id.sql
-- 新建数据库直接用 schema.sql，不再运行本文件。重复运行 ALTER 会报 duplicate column name，不会删数据。
ALTER TABLE matches ADD COLUMN match_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS matches_global_id ON matches (match_id) WHERE match_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS match_keys (
  id TEXT NOT NULL,
  k TEXT NOT NULL,
  match_id TEXT NOT NULL,
  PRIMARY KEY (id, k)
);
-- 升级后重新计算汇总。旧匿名键无法反推身份，历史重复需相应设备补报 match_id 后才能归并。
DELETE FROM summary;
