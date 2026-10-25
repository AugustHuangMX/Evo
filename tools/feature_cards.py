"""新版本宣传图（开发工具）：给新功能做 1920×1080 的配图（QQ 群、官网更新日志用）。风格固定，规则见 release/做图流程.md。

做法：
1. 在临时文件夹里生成演示战绩（demo_data.py 的数字 + 编出来的队友），打开面板，不碰正式数据
2. Edge 无界面模式截 Evo 窗口（1280×800，放大 4 倍截，放大框里也清楚），再量出要放大的元素在哪里
3. 按 release/宣传图.json 合成：左上角版本标签和标题，下面是窗口截图（红框标出要看的地方、四周压暗），
   旁边是放大框，虚线连起来

用法：python tools/feature_cards.py           输出到 release/<版本号>截图/（同名的图会被替换）
     python tools/feature_cards.py --keep    留下临时文件夹（原图、合成用的网页），排查时用
"""

import argparse
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PIL import Image

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）

ROOT = code_path.ROOT
CARDS_FILE = ROOT / "release" / "宣传图.json"
FONTS = (ROOT / "panel" / "static" / "fonts").as_uri()
ICON = (ROOT / "assets" / "icon" / "evo.png").as_uri()
EDGE = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 "
      "Edg/130.0 EvoApp/")  # 带 EvoApp/：面板画出 Evo 窗口自己的标题栏（见 panel_app.py 的 in_app）

VIEW_W, VIEW_H = 1280, 800  # Evo 窗口（CSS 像素）
SHOT_SCALE = 4  # 原图放大几倍截；放大框最多放大 2.5 倍 × 输出 1.5 倍 = 3.75，不超过它就不会糊
W, H, OUT_SCALE = 1280, 720, 1.5  # 画布（CSS 像素），输出 1920×1080
MARGIN = 48  # 画布左右留边
WIN_TOP, WIN_BOX = 176, 480  # 窗口截图放在标题下面这一块（纵向居中）
WIN_SCALE = 0.6  # 窗口截图默认缩小到 0.6（宣传图.json 里的 window 可以改）
MAX_ZOOM, MAX_CW, MAX_CH = 2.5, 600, 300  # 放大框：最多放大 2.5 倍，最宽 600、最高 300
TAG_W = 84  # 放大框右边的小标签（"自己""队友"）占的宽度

# 演示数据：自己用的传奇多换几个，头像才显得多；队友名字是编的（不能用真实玩家的名字）
MY_LEGENDS = ["恶灵", "恶灵", "寻血猎犬", "动力小子", "罗芭", "地平线", "希尔", "艾许", "万蒂奇"]
MATE_LEGENDS = ["直布罗陀", "命脉", "探路者", "班加罗尔", "幻象", "瓦尔基里", "纽卡斯尔", "卡特莉丝", "密客", "沃特森"]
MY_NAME = "EvoPlayer"  # 自己的游戏名（编的；分享卡片右上角会显示）
# 队友（编的）：每局从这三个人里挑两个；名次好的局多半有夜航灯塔，数据页"常一起玩的队友"里才看得出谁是上分好搭子
MATE_POOL = [("夜航灯塔", "1082736451"), ("SilverFox_77", "2093847561"), ("咕咕鸽", "3048571926")]
MATE_WEIGHTS = {"good": [6, 2, 1], "bad": [1, 3, 3]}  # 名次 1–5 的局 / 其余的局，挑到这三个人的权重
WIN_RATE = 0.11  # 随机出来的名次几乎没有第 1 名，"冠军"一列会全是 0：大约每 9 局改一局成冠军（RP 按演示数据的算法重算）
# 武器（0.4.0 起记录）：每局拿过 3–7 段，按权重挑（编号见 config/weapons.json）
WEAPONS = [("r301", 10), ("r99", 7), ("wingman", 6), ("eva8", 5), ("flatline", 4), ("nemesis", 3), ("car", 3),
           ("peacekeeper", 2), ("longbow", 1)]
STATS_URL = "http://127.0.0.1:9"  # 全体玩家：开了匿名统计才能看；地址指到本机一个没人听的端口，保证不联网
VIDEOCONFIG = ('"VideoConfig"\n{\n\t"setting.defaultres"\t\t"2560"\n\t"setting.defaultresheight"\t\t"1600"\n'
               '\t"setting.fullscreen"\t\t"1"\n}\n')  # 假的 Apex 视频设置：全屏 2560×1600，自动检测结果是 16:10
SHOWN_DATA_DIR = r"E:\Evo Tracker Data"  # 设置页上显示的数据文件夹（免得截到临时文件夹和 Windows 用户名）
MOVE_CHECK = {"chosen": r"D:\Games", "target": r"D:\Games\Evo Tracker Data", "problem": None, "existing": None,
              "size_mb": 86, "free_gb": 412}  # 页面地址带 _shot=move 时："更改数据位置"展开、显示"能搬"的检查结果

VERSION = None  # 图上写的版本号（main 里定：宣传图.json 的 version，没写就用程序的版本号）
LABEL = "新功能"  # 左上角小标签版本号后面的字（宣传图.json 的 label；0.4.0 起写成空的，只有版本号：新功能和特色功能放在一起介绍）
MEASURE = {"groups": None}  # 量元素位置时，面板在网页里加一段脚本（见 serve_panel）

MEASURE_JS = """
(function () {
  var groups = %s;
  function run() {
    var out = groups.map(function (g) {
      var r = [];
      g.forEach(function (sel) {
        document.querySelectorAll(sel).forEach(function (el) {
          var b = el.getBoundingClientRect(); r.push([b.left, b.top, b.width, b.height]);
        });
      });
      return r;
    });
    var pre = document.createElement("pre"); pre.id = "evo-boxes"; pre.hidden = true;
    pre.textContent = JSON.stringify({view: [innerWidth, innerHeight], boxes: out}); document.body.appendChild(pre);
  }
  window.addEventListener("load", function () { document.fonts.ready.then(function () { setTimeout(run, 300); }); });
})();
"""


def find_edge():
    for path in EDGE:
        if path.is_file():
            return path
    sys.exit("找不到 Edge（msedge.exe）：截图要用它")


def edge(tmp, args, size=(VIEW_W, VIEW_H), scale=SHOT_SCALE, ua=True):
    """Edge 无界面模式跑一次（深色主题、不显示滚动条），返回它的输出。"""
    cmd = [str(find_edge()), "--headless=new", f"--user-data-dir={tmp / 'edge'}", "--force-dark-mode",
           "--hide-scrollbars", f"--window-size={size[0]},{size[1]}", f"--force-device-scale-factor={scale}",
           "--virtual-time-budget=3000", *([f"--user-agent={UA}{VERSION}"] if ua else []), *args]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120).stdout


def make_demo_data():
    """演示战绩写进临时数据文件夹的 evo.db（叫 evo.db，页面顶部才不显示"演示数据"）；加上队友，去掉"待确认"。"""
    import demo_data
    import storage

    demo_data.LEGENDS = MY_LEGENDS
    demo_data.DEMO_DB = storage.DB_PATH
    original, win_rng = demo_data.fake_match, random.Random(3)

    def fake_match(rng, ended_at):  # 偶尔改成冠军，RP 按演示数据同样的算法重算（段位在后面累加，前后对得上）
        m = original(rng, ended_at)
        if win_rng.random() < WIN_RATE:
            m["placement"] = 1
            if m["rp_delta"] is not None:
                m["rp_delta"] = demo_data.PLACEMENT_RP[1] + min(m["kills"] + m["assists"] // 2, 6) * 14 - demo_data.ENTRY_COST
        return m

    demo_data.fake_match = fake_match
    demo_data.main()
    demo_data.fake_match = original
    rng = random.Random(7)
    with sqlite3.connect(storage.DB_PATH) as conn:
        for match_id, placement in conn.execute("SELECT match_id, placement FROM matches").fetchall():
            weights = MATE_WEIGHTS["good" if placement and placement <= 5 else "bad"]
            first = rng.choices(range(3), weights)[0]
            second = rng.choices([i for i in range(3) if i != first], [w for i, w in enumerate(weights) if i != first])[0]
            mates = []
            for slot, (name, pid), legend in zip(("left", "right"), (MATE_POOL[first], MATE_POOL[second]), rng.sample(MATE_LEGENDS, 2)):
                k = rng.randint(0, 6)
                mates.append({"slot": slot, "name": name, "player_id": pid, "legend": legend, "kills": k,
                              "assists": rng.randint(0, 5), "knockdowns": k + rng.randint(0, 2),
                              "damage": 150 + k * 240 + rng.randint(0, 300), "survival_time": rng.randint(600, 1400),
                              "revives": rng.randint(0, 2), "respawns": rng.choice([0, 0, 1])})
            storage.save_teammates(conn, match_id, mates)
            t, holds = 0, []
            for _ in range(rng.randint(3, 7)):
                d = rng.randint(40, 260)
                holds.append((rng.choices([w for w, _ in WEAPONS], [k for _, k in WEAPONS])[0], t, t + d))
                t += d + rng.randint(0, 20)
            storage.save_weapon_holds(conn, match_id, holds)
        conn.execute("UPDATE matches SET player_name = ?", (MY_NAME,))
        conn.execute("UPDATE matches SET needs_review = 0, review_note = NULL")  # 宣传图里不要"待确认"提示条
        latest = conn.execute("SELECT MAX(match_id) FROM matches").fetchone()[0]
    write_everyone(storage.DB_PATH.parent)
    return storage.DB_PATH, latest


def write_everyone(folder):
    """全体玩家的汇总（编的数字，格式和统计服务器一样，见 code/global_stats.py），存成刚下载过的样子，页面不会去下载。"""
    from datetime import datetime, timedelta

    rng = random.Random(11)
    names = list(dict.fromkeys(["恶灵", "罗芭", "命脉", "探路者", "万蒂奇", "地平线", "寻血猎犬", "动力小子", "直布罗陀",
                                "班加罗尔", "希尔", "艾许"]))
    total = 41730 * 3

    def legends(scale):
        out, n = [], 7600 * scale
        for i, name in enumerate(names):
            out.append({"name": name, "n": int(n), "pick": round(n / (total * scale), 4), "win": round(0.05 + rng.random() * 0.04, 4),
                        "top5": round(0.26 + rng.random() * 0.1, 4), "place": round(8.4 + i * 0.18 + rng.random() * 0.5, 2),
                        "kills": round(1.3 + rng.random() * 0.7, 2), "damage": int(620 + rng.random() * 260)})
            n *= 0.82
        return out

    combos = [{"legends": c, "n": n, "rate": round(n / 41730, 4), "place": p} for c, n, p in [
        (["命脉", "恶灵", "罗芭"], 1834, 8.6), (["万蒂奇", "探路者", "罗芭"], 1502, 8.9), (["导线管", "探路者", "班加罗尔"], 1217, 9.1),
        (["希尔", "恶灵", "直布罗陀"], 1033, 9.3), (["命脉", "万蒂奇", "地平线"], 921, 9.4)]]
    people = {"place": [6.4, 7.6, 8.8, 10.2, 12.6], "win": [0.0, 0.03, 0.06, 0.1, 0.16],
              "kills": [1.4, 2.2, 2.9, 3.5, 4.4], "damage": [560, 760, 960, 1180, 1420]}

    def part(players, matches, scale=1.0):
        return {"players": players, "matches": matches, "legends": legends(scale), "combos": combos, "people": people}

    now = datetime.now()
    summary = {"v": 1, "made_at": (now - timedelta(minutes=53)).strftime("%Y-%m-%dT%H:%M:00Z"),
               "recent_from": (now - timedelta(days=7)).strftime("%Y-%m-%d"),
               "slices": {"all.ranked.all": part(1286, 41730), "7d.ranked.all": part(512, 9120, 0.22),
                          "all.ranked.low": part(610, 18800, 0.45), "all.ranked.diamond": part(402, 14300, 0.34),
                          "all.ranked.master": part(96, 3100, 0.08), "all.pubs.all": part(733, 12900, 0.31)},
               "tiers": {"all": {"players": 1286, "counts": {"大师": 61, "钻石": 402, "白金": 455, "黄金": 280, "白银": 88}},
                         "7d": {"players": 512, "counts": {"大师": 30, "钻石": 190, "白金": 180, "黄金": 112}}}}
    (folder / "global_stats.json").write_text(json.dumps({"fetched_at": now.strftime("%Y-%m-%d %H:%M:%S"), "summary": summary},
                                                         ensure_ascii=False), encoding="utf-8")


def serve_panel(db):
    """在本进程里开面板（随机端口），返回 (地址, 服务)。几处改动只在这里生效，不改面板代码。"""
    from flask import request
    from werkzeug.serving import make_server

    import panel_app

    original_move_view, original_render = panel_app.move_view, panel_app.render_template

    def move_view():
        view = original_move_view()
        view["blocked"] = None
        if request.args.get("_shot") == "move":
            view["check"] = MOVE_CHECK
        return view

    def render_template(name, **kw):
        if name == "settings.html":
            kw.update(data_dir=SHOWN_DATA_DIR, captures_dir=SHOWN_DATA_DIR + r"\captures", resolution="2560 × 1440")
            # 开机自启：用源码运行时开关是灰的，而且会读到这台电脑真实的启动项。演示成"已开启"，不看真实的
            kw["autostart"] = {**kw["autostart"], "on": True, "blocked": None, "failed": None,
                               "off_in_windows": False, "other": None}
        return original_render(name, **kw)

    @panel_app.app.after_request
    def inject(resp):
        if resp.mimetype == "text/html":
            # 跳到 #某个标题 时，别让标题藏在自绘标题栏下面（Evo 窗口里滚动的是 body）
            extra = "<style>body{scroll-padding-top:36px}</style>"
            html = resp.get_data(as_text=True).replace("</head>", extra + "</head>", 1)
            # 关掉动画：截到的、量到的都是播完的样子（走势图从左往右展开，没播完就截会丢曲线）
            html = html.replace("</head>", "<style>*,*::before,*::after{animation:none!important;transition:none!important}</style></head>", 1)
            if request.args.get("_shot") in ("share", "share2"):  # 打开分享对话框（要在下面量位置的脚本之前，量的时候对话框已经打开）
                html = html.replace("</body>", "<script>document.querySelector('[data-share-open]').click()</script></body>", 1)
            if request.args.get("_shot") == "share2":  # 分享对话框切到第二种卡片（总览页是"近 30 局概况"）
                html = html.replace("</body>", "<script>(function(){const r=document.querySelectorAll('input[name=share-kind]');"
                                    "r[1].checked=true;r[1].dispatchEvent(new Event('change'));})()</script></body>", 1)
            if request.args.get("_shot") == "lang":  # 游戏语言的下拉框展开成列表（截图打不开下拉框），选中 English
                html = html.replace("</body>", "<script>(function(){const s=document.getElementById('language-select');"
                                    "s.size=s.options.length;const en=[...s.options].find(o=>/English/.test(o.text));"
                                    "if(en)s.value=en.value;})()</script></body>", 1)
            if MEASURE["groups"] is not None:
                html = html.replace("</body>", f"<script>{MEASURE_JS % json.dumps(MEASURE['groups'])}</script></body>", 1)
            resp.set_data(html)
        return resp

    panel_app.move_view = move_view
    panel_app.render_template = render_template
    panel_app.app.config["DB"] = db
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # 不打印每个请求
    server = make_server("127.0.0.1", 0, panel_app.app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}", server


def as_list(v):
    return [v] if isinstance(v, str) else list(v)


def measure(tmp, url, groups):
    """量出每组选择器匹配到的元素在窗口里的位置：[[(x, y, 宽, 高), ...], ...]（CSS 像素）。
    Edge 读网页内容（--dump-dom）时页面比窗口小一圈（扣掉了窗口边框，截图时不扣），位置会和截图对不上：
    先按截图的大小量一次，页面不是 1280×800 就把窗口放大差的那么多再量，直到和截图一样大。"""
    size = (VIEW_W, VIEW_H)
    MEASURE["groups"] = groups
    try:
        for _ in range(2):
            dom = edge(tmp, ["--dump-dom", url], size=size, scale=1)
            found = re.search(r'<pre id="evo-boxes"[^>]*>([^<]*)</pre>', dom)
            if not found:
                sys.exit(f"没量出元素位置：{url}")
            result = json.loads(found.group(1))
            view_w, view_h = result["view"]
            if (view_w, view_h) == (VIEW_W, VIEW_H):
                return result["boxes"]
            size = (size[0] + VIEW_W - view_w, size[1] + VIEW_H - view_h)
    finally:
        MEASURE["groups"] = None
    sys.exit(f"量位置时页面是 {view_w}×{view_h}，和截图的 {VIEW_W}×{VIEW_H} 不一样：{url}")


def union(boxes, pad):
    """把几个元素合成一块，四周留 pad（一个数，或 [左右, 上下]），不超出窗口。"""
    px, py = (pad, pad) if isinstance(pad, (int, float)) else pad
    x0 = max(0, min(b[0] for b in boxes) - px)
    y0 = max(0, min(b[1] for b in boxes) - py)
    x1 = min(VIEW_W, max(b[0] + b[2] for b in boxes) + px)
    y1 = min(VIEW_H, max(b[1] + b[3] for b in boxes) + py)
    return x0, y0, x1 - x0, y1 - y0


CSS = """
@font-face { font-family: "Chakra Petch"; font-weight: 600; src: url("%(fonts)s/ChakraPetch-600.woff2") format("woff2"); }
@font-face { font-family: "Chakra Petch"; font-weight: 700; src: url("%(fonts)s/ChakraPetch-700.woff2") format("woff2"); }
:root { --accent: #FF5A5A; --text: #ECEEF2; --muted: #9AA3B2; --bg: #0A0C10; }
html, body { margin: 0; }
body { width: %(w)dpx; height: %(h)dpx; overflow: hidden; position: relative; color: var(--text);
  font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
  background: radial-gradient(900px 520px at 92%% -10%%, rgb(255 90 90 / 0.13), transparent 60%%),
              radial-gradient(700px 500px at -10%% 110%%, rgb(76 154 255 / 0.08), transparent 60%%), var(--bg); }
.head { position: absolute; left: 56px; top: 46px; }
.pill { display: inline-flex; align-items: center; gap: 8px; height: 28px; padding: 0 12px 0 8px; border-radius: 999px;
  background: rgb(255 90 90 / 0.12); border: 1px solid rgb(255 90 90 / 0.35); color: #FFB3B3; font-size: 13px; font-weight: 600; }
.pill img { width: 18px; height: 18px; }
.num { font-family: "Chakra Petch", "Microsoft YaHei UI", sans-serif; }
.pill .num { letter-spacing: 0.5px; }
h1 { margin: 14px 0 0; font-size: 42px; line-height: 1.15; font-weight: 700; letter-spacing: 1px; }
.win { position: absolute; border-radius: 10px; overflow: hidden; background-size: 100%% 100%%;
  box-shadow: 0 0 0 1px rgb(255 255 255 / 0.09), 0 24px 70px rgb(0 0 0 / 0.55); }
.hl { position: absolute; border-radius: 6px; outline: 2px solid var(--accent); box-shadow: 0 0 0 2000px rgb(4 5 8 / 0.5); }
svg.links { position: absolute; inset: 0; width: 100%%; height: 100%%; }
.co { position: absolute; border-radius: 10px; background-repeat: no-repeat;
  box-shadow: 0 0 0 2px var(--accent), 0 0 0 7px rgb(255 90 90 / 0.14), 0 28px 80px rgb(0 0 0 / 0.7); }
.cap { position: absolute; transform: translateY(-100%%); margin-top: -12px; font-size: 14px; font-weight: 600; color: #FFB3B3;
  background: #1A1114; border: 1px solid rgb(255 90 90 / 0.4); border-radius: 6px; padding: 3px 10px; white-space: nowrap; }
.tag { position: absolute; transform: translateY(-50%%); display: flex; align-items: center; gap: 8px; font-size: 16px; font-weight: 600; }
.tag::before { content: ""; width: 22px; height: 2px; background: var(--accent); }
.tag span { background: var(--accent); color: #2A0A0A; border-radius: 4px; padding: 2px 9px; }
.ratios { position: absolute; right: 56px; top: 58px; display: flex; align-items: flex-end; gap: 18px; }
.ratio { display: flex; flex-direction: column; align-items: center; gap: 8px; font-family: "Chakra Petch", sans-serif;
  font-weight: 600; font-size: 15px; color: var(--muted); }
.ratio i { display: block; width: 92px; border: 2px solid #3A4150; border-radius: 4px; background: rgb(255 255 255 / 0.02); }
.ratio.new { color: var(--text); }
.ratio.new i { border-color: var(--accent); background: rgb(255 90 90 / 0.08); }
"""

# 放大框和红框之间的两条虚线（加一层很淡的红色）：放大框在右边时连右上→左上、右下→左下，在下面时连左下→左上、右下→右上
LINKS_JS = """
const hl = document.querySelector('.hl').getBoundingClientRect();
const co = document.querySelector('.co').getBoundingClientRect();
const P = (r, c) => [c.includes('r') ? r.right : r.left, c.includes('b') ? r.bottom : r.top];
const pairs = co.left >= hl.right - 4 ? [['tr', 'tl'], ['br', 'bl']] : [['bl', 'tl'], ['br', 'tr']];
const [a1, b1] = [P(hl, pairs[0][0]), P(co, pairs[0][1])], [a2, b2] = [P(hl, pairs[1][0]), P(co, pairs[1][1])];
document.querySelector('svg.links').innerHTML = `<polygon points="${a1} ${b1} ${b2} ${a2}" fill="rgb(255 90 90 / 0.07)"/>` +
  [[a1, b1], [a2, b2]].map(([a, b]) => `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" ` +
  `stroke="rgb(255 90 90 / 0.65)" stroke-width="1.5" stroke-dasharray="4 4"/>`).join('');
"""


def card_html(card, shot, region, tag_boxes):
    """一张宣传图的网页。排版规则：窗口截图在左下；放大框放得下就在窗口右边，放不下就靠画布右边、压住窗口一点；
    放大框和红框纵向对齐，不超出画布。"""
    rx, ry, rw, rh = region
    ws = card.get("window", WIN_SCALE)
    wx, wy = MARGIN, WIN_TOP + (WIN_BOX - VIEW_H * ws) / 2
    tag_w = TAG_W if tag_boxes else 0
    z = card.get("zoom") or min(MAX_ZOOM, MAX_CW / rw, MAX_CH / rh)
    cw, ch = rw * z, rh * z
    win_right = wx + VIEW_W * ws
    cx = win_right + 24 if win_right + 24 + cw + tag_w <= W - MARGIN else W - MARGIN - tag_w - cw
    cy = min(max(wy + (ry + rh / 2) * ws - ch / 2, WIN_TOP), H - 40 - ch)
    img = shot.as_uri()
    title = re.sub(r"\d[\d:.]*", lambda m: f'<span class="num">{m.group(0)}</span>', card["title"])
    parts = [
        f'<div class="head"><div class="pill"><img src="{ICON}" alt=""><span class="num">EVO {VERSION}</span>{LABEL}</div>'
        f'<h1>{title}</h1></div>',
        f'<div class="win" style="left:{wx}px;top:{wy}px;width:{VIEW_W * ws}px;height:{VIEW_H * ws}px;'
        f'background-image:url(\'{img}\')"><div class="hl" style="left:{rx * ws}px;top:{ry * ws}px;'
        f'width:{rw * ws}px;height:{rh * ws}px"></div></div>',
        '<svg class="links"></svg>',
        f'<div class="co" style="left:{cx}px;top:{cy}px;width:{cw}px;height:{ch}px;background-image:url(\'{img}\');'
        f'background-size:{VIEW_W * z}px {VIEW_H * z}px;background-position:{-rx * z}px {-ry * z}px"></div>',
        f'<div class="cap" style="left:{cx}px;top:{cy}px">{card["caption"]}</div>',
    ]
    for text, (_, by, _, bh) in tag_boxes:
        parts.append(f'<div class="tag" style="left:{cx + cw + 6}px;top:{cy + (by + bh / 2 - ry) * z}px"><span>{text}</span></div>')
    if card.get("aspect_boxes"):
        parts.append('<div class="ratios">' + "".join(
            f'<div class="ratio{" new" if new else ""}"><i style="height:{92 / ratio_of(label):.1f}px"></i>{label}</div>'
            for label, new in card["aspect_boxes"]) + "</div>")
    if card.get("note"):
        parts.append(f'<div class="note">{card["note"]}</div>')
    css = CSS % {"fonts": FONTS, "w": W, "h": H} + NOTE_CSS
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{card["title"]}</title>'
            f'<style>{css}</style></head><body>{"".join(parts)}<script>{LINKS_JS}</script></body></html>')


FULL_TOP, FULL_BOTTOM = 150, 700  # "只放截图"：窗口截图放在标题下面这一块，横向居中
NOTE_CSS = ".note { position: absolute; left: 0; right: 0; bottom: 22px; text-align: center; font-size: 13px; color: var(--muted); }"


def page_html(card, parts):
    title = re.sub(r"\d[\d:.]*", lambda m: f'<span class="num">{m.group(0)}</span>', card["title"])
    head = (f'<div class="head"><div class="pill"><img src="{ICON}" alt=""><span class="num">EVO {VERSION}</span>{LABEL}</div>'
            f'<h1>{title}</h1></div>')
    if card.get("note"):
        parts.append(f'<div class="note">{card["note"]}</div>')
    css = CSS % {"fonts": FONTS, "w": W, "h": H} + NOTE_CSS + GALLERY_CSS
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{card["title"]}</title>'
            f'<style>{css}</style></head><body>{head}{"".join(parts)}</body></html>')


def full_html(card, shot, tag_boxes):
    """只放截图（不放大）：整个窗口放大到标题下面这一块，横向居中；tags 放在窗口右边、对准那一行。"""
    bottom = FULL_BOTTOM - (34 if card.get("note") else 0)  # 有底部小字时窗口收一点，小字在窗口下面
    s = min((W - 2 * MARGIN - (TAG_W if tag_boxes else 0)) / VIEW_W, (bottom - FULL_TOP) / VIEW_H)
    ww, wh = VIEW_W * s, VIEW_H * s
    wx, wy = (W - ww) / 2, FULL_TOP + (bottom - FULL_TOP - wh) / 2
    parts = [f'<div class="win" style="left:{wx}px;top:{wy}px;width:{ww}px;height:{wh}px;background-image:url(\'{shot.as_uri()}\')"></div>']
    for text, (_, by, _, bh) in tag_boxes:
        parts.append(f'<div class="tag" style="left:{wx + ww + 6}px;top:{wy + (by + bh / 2) * s}px"><span>{text}</span></div>')
    return page_html(card, parts)


GALLERY_CSS = """
.gal { position: absolute; border-radius: 10px; background-size: 100% 100%;
  box-shadow: 0 0 0 1px rgb(255 255 255 / 0.09), 0 24px 70px rgb(0 0 0 / 0.55); }
.gal-label { position: absolute; transform: translateX(-50%); font-size: 15px; font-weight: 600; color: #FFB3B3;
  background: #1A1114; border: 1px solid rgb(255 90 90 / 0.4); border-radius: 6px; padding: 3px 12px; white-space: nowrap; }
"""


def gallery_html(card, crops):
    """几张图并排（比如几种分享卡片）：一样高，整排横向居中，每张下面写名字。crops：[(图片路径, 宽, 高, 名字)]"""
    gap, label_h = 28, 40
    total_ratio = sum(w / h for _, w, h, _ in crops)
    h = min(380, (W - 2 * MARGIN - gap * (len(crops) - 1)) / total_ratio)
    x = (W - (h * total_ratio + gap * (len(crops) - 1))) / 2
    y = FULL_TOP + (FULL_BOTTOM - FULL_TOP - h - label_h) / 2
    parts = []
    for path, w0, h0, label in crops:
        w = h * w0 / h0
        parts.append(f'<div class="gal" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;background-image:url(\'{path.as_uri()}\')"></div>')
        parts.append(f'<div class="gal-label" style="left:{x + w / 2}px;top:{y + h + 14}px">{label}</div>')
        x += w + gap
    return page_html(card, parts)


def ratio_of(label):
    a, b = label.split(":")
    return float(a) / float(b)


def main():
    global VERSION, LABEL
    parser = argparse.ArgumentParser(description="做新版本的宣传图（release/宣传图.json → release/<版本号>截图/）")
    parser.add_argument("--keep", action="store_true", help="留下临时文件夹（原图、合成用的网页）")
    args = parser.parse_args()

    spec = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="evo-cards-"))
    data = tmp / "data"
    data.mkdir()
    os.environ["EVO_DATA_DIR"] = str(data)  # 在 import 程序代码之前设好：演示数据、设置都在临时文件夹里
    os.environ["EVO_VIDEOCONFIG"] = str(tmp / "videoconfig.txt")
    (tmp / "videoconfig.txt").write_text(VIDEOCONFIG, encoding="utf-8")
    os.environ["EVO_STATS_URL"] = STATS_URL
    (data / "settings.json").write_text('{"update_auto": true, "telemetry_consent": true, "autostart_asked": true}',
                                         encoding="utf-8")  # 免得截到顶部的提问（自动检查、匿名统计、开机自启）；开了匿名统计，全体玩家那一页才能看

    import storage
    VERSION = spec.get("version") or storage.APP_VERSION
    LABEL = spec.get("label", LABEL)
    db, latest = make_demo_data()
    base, server = serve_panel(db)
    out_dir = ROOT / "release" / f"{VERSION}截图"
    out_dir.mkdir(exist_ok=True)
    try:
        for i, card in enumerate(spec["cards"], 1):
            png = out_dir / f'{i:02d}-{card["file"]}.png'
            if card.get("gallery"):  # 几张图并排：每张从各自的页面里裁出来（窗口放大 4 倍截，裁出来也清楚）
                crops = []
                for k, item in enumerate(card["gallery"]):
                    url = base + item["page"].replace("{latest}", str(latest))
                    raw = tmp / f"{i:02d}-{k}-raw.png"
                    edge(tmp, [f"--screenshot={raw}", url])
                    found = measure(tmp, url, [as_list(item["on"])])[0]
                    if not found:
                        sys.exit(f"第 {i} 张：页面上找不到 {item['on']}（{url}）")
                    bx, by, bw, bh = found[0]
                    crop = tmp / f"{i:02d}-{k}.png"
                    Image.open(raw).crop(tuple(round(v * SHOT_SCALE) for v in (bx, by, bx + bw, by + bh))).save(crop)
                    crops.append((crop, bw, bh, item["label"]))
                page = tmp / f"{i:02d}-card.html"
                page.write_text(gallery_html(card, crops), encoding="utf-8")
                edge(tmp, [f"--screenshot={png}", page.as_uri()], size=(W, H), scale=OUT_SCALE, ua=False)
                print(f"{png.relative_to(ROOT)}  （{len(crops)} 张并排）")
                continue
            url = base + card["page"].replace("{latest}", str(latest))
            shot = tmp / f"{i:02d}-shot.png"
            edge(tmp, [f"--screenshot={shot}", url])
            if card.get("layout") == "full":  # 只放截图，不放大
                tags = card.get("tags", [])
                boxes = measure(tmp, url, [as_list(t["on"]) for t in tags]) if tags else []
                tag_boxes = [(t["text"], b) for t, found in zip(tags, boxes) for b in found]
                page = tmp / f"{i:02d}-card.html"
                page.write_text(full_html(card, shot, tag_boxes), encoding="utf-8")
                edge(tmp, [f"--screenshot={png}", page.as_uri()], size=(W, H), scale=OUT_SCALE, ua=False)
                print(f"{png.relative_to(ROOT)}  （只放截图）")
                continue
            tags = card.get("tags", [])
            boxes = measure(tmp, url, [as_list(card["zoom_on"])] + [as_list(t["on"]) for t in tags])
            for sel, found in zip([card["zoom_on"]] + [t["on"] for t in tags], boxes):
                if not found:
                    sys.exit(f"第 {i} 张：页面上找不到 {sel}（{url}）")
            region = union(boxes[0], card.get("pad", [12, 10]))
            tag_boxes = [(t["text"], b) for t, found in zip(tags, boxes[1:]) for b in found]
            page = tmp / f"{i:02d}-card.html"
            page.write_text(card_html(card, shot, region, tag_boxes), encoding="utf-8")
            edge(tmp, [f"--screenshot={png}", page.as_uri()], size=(W, H), scale=OUT_SCALE, ua=False)
            print(f"{png.relative_to(ROOT)}  （放大区域 {', '.join(f'{v:.0f}' for v in region)}）")
    finally:
        server.shutdown()
        if args.keep:
            print(f"临时文件夹：{tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
