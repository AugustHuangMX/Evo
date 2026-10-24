-- Evo 匿名统计的数据库（Cloudflare D1，名字 evo-stats）。worker.js 往里写，tools/stats_report.py 读。
-- 只有用户同意后发来的数据；没有 IP 地址、游戏名、ID。id 是 Evo 里随机生成的匿名编号。
-- 建表：wrangler d1 execute evo-stats --remote --file schema.sql（已经有的表不动）。
-- 给已有的表加列不能写在这里（CREATE TABLE IF NOT EXISTS 不会加列）：另写一个文件，只运行一次（如 add_mate_stats.sql）

-- 日活：每个编号每天一行（那天 Evo 开着、联网发过一次）
CREATE TABLE IF NOT EXISTS pings (
  id TEXT NOT NULL,
  day TEXT NOT NULL,  -- 用户电脑上的日期
  version TEXT,  -- Evo 版本号
  screen TEXT,  -- 屏幕分辨率，如 2560x1440
  game_res TEXT,  -- Apex 视频设置里的分辨率
  game_mode TEXT,  -- fullscreen / borderless / windowed
  aspect TEXT,  -- Evo 设置页选的游戏画面比例：auto / 16:9 / 16:10 / 4:3
  received_at TEXT NOT NULL,  -- 服务器收到的时间（UTC）
  PRIMARY KEY (id, day)
);

-- 每局一行。k 是 Evo 算出来的对局编号（同一局再发就覆盖）；没读出来的项为空
CREATE TABLE IF NOT EXISTS matches (
  id TEXT NOT NULL,
  k TEXT NOT NULL,
  day TEXT,  -- 这一局结束的日期（用户电脑上的）
  version TEXT,  -- 记这一局的 Evo 版本号
  mode TEXT,  -- ranked / pubs / other
  map TEXT,
  legend TEXT,  -- 自己的传奇（中文名）
  mate1 TEXT,  -- 两名队友的传奇
  mate2 TEXT,
  placement INTEGER,
  kills INTEGER,
  assists INTEGER,
  knockdowns INTEGER,
  damage INTEGER,
  rp_delta INTEGER,
  tier TEXT,  -- 段位，只有大段，如 钻石
  rp_source TEXT,  -- RP 从哪个画面来：经验值页 / 对局中 / 大厅校准
  review INTEGER,  -- 1 = 这一局在 Evo 里标了待确认
  edited TEXT,  -- 用户在战绩页改过哪几项（列名，逗号分隔）
  received_at TEXT NOT NULL,
  -- 两名队友这一局的数据（总结页上读的；0.3.8 之后的 Evo 才发，2026-10-24 加的列，已有的数据库见 add_mate_stats.sql）
  mate1_kills INTEGER,
  mate1_assists INTEGER,
  mate1_knockdowns INTEGER,
  mate1_damage INTEGER,
  mate2_kills INTEGER,
  mate2_assists INTEGER,
  mate2_knockdowns INTEGER,
  mate2_damage INTEGER,
  PRIMARY KEY (id, k)
);

CREATE INDEX IF NOT EXISTS pings_day ON pings (day);
CREATE INDEX IF NOT EXISTS matches_day ON matches (day);
CREATE INDEX IF NOT EXISTS matches_received ON matches (received_at);  -- 汇总前看有没有新数据（只读一行）

-- 全体玩家的汇总（Evo 数据页"全体玩家"下载的就是 body；worker.js 每小时算一次，有新数据或换了一天才重算）
CREATE TABLE IF NOT EXISTS summary (
  name TEXT PRIMARY KEY,  -- 汇总的版本（v1）；格式改了换个名字，老版本的 Evo 照样读老的
  body TEXT NOT NULL,  -- JSON，格式见 worker.js 的 makeSummary
  made_at TEXT NOT NULL,  -- 算的时间（UTC）
  data_at TEXT  -- 算的时候最新一局的收到时间：没变（也没换天）就不重算
);
