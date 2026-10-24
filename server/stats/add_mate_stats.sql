-- 2026-10-24：已有的数据库加上队友的数据、全体玩家的汇总（新建的数据库直接用 schema.sql，不用运行这个）。
-- 只运行一次，而且要在部署新的 worker.js 之前（新的 worker.js 写这几列）：
--   wrangler d1 execute evo-stats --remote --file add_mate_stats.sql
--   wrangler d1 execute evo-stats --remote --file schema.sql   （加汇总表和索引；已有的表不动）
-- 再运行一次会报 duplicate column name，数据不受影响
ALTER TABLE matches ADD COLUMN mate1_kills INTEGER;
ALTER TABLE matches ADD COLUMN mate1_assists INTEGER;
ALTER TABLE matches ADD COLUMN mate1_knockdowns INTEGER;
ALTER TABLE matches ADD COLUMN mate1_damage INTEGER;
ALTER TABLE matches ADD COLUMN mate2_kills INTEGER;
ALTER TABLE matches ADD COLUMN mate2_assists INTEGER;
ALTER TABLE matches ADD COLUMN mate2_knockdowns INTEGER;
ALTER TABLE matches ADD COLUMN mate2_damage INTEGER;
