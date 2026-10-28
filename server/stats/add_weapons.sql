-- 只运行一次（2026-10-28）：给已有的 matches 表加武器那一列（schema.sql 里的 CREATE TABLE IF NOT EXISTS 不会加列）。
-- 要在部署用到这一列的 worker.js 之前运行：wrangler d1 execute evo-stats --remote --file add_weapons.sql
-- 线上运行完就删掉这个文件（本机测试库也要运行一次：把 --remote 换成 --local）
ALTER TABLE matches ADD COLUMN weapons TEXT;
