"""测试员 11-04 用测试工具截的第 31 赛季画面：16:9（两位，2560×1440）和 4:3（一位，游戏 1920×1440 压到 1920×1080，手柄）。

截图从 bugs_test/ 里的诊断包解出来，放在 captures/testers/20261104_16x9_a、_16x9_b、_4x3（不在仓库里：有别的玩家的名字；
找不到就跳过）。frames 里的是缩到 1920 宽的 JPG，名字带 full_ 的是原尺寸（每分钟一张，更接近 Evo 实时读的原图）。
- 新地图"电力区域"（E-District）：开局横幅认得出
- 4:3 大厅的段内分数：原尺寸截图上"436/750 RP"会读成"436/750.RP""6817750 RP"（斜杠读错），照样读得出
- 对局中队友的名字：第 31 赛季起是逐字出来的动画，认出传奇 2 秒以后才裁；和总结页一个是另一个的开头时算同一个人、
  记长的（诊断包里没有动画中途的截图，把名字后半截涂掉来模拟）
- 自己的名字：总结页卡片上被游戏截短（"…dkaldad"），大厅读到完整的以后改记完整的

运行：python tests/test_testers_1104.py
"""

import glob
from datetime import datetime, timedelta

import numpy as np

from fake_game import SHOTS, load, report  # 要在项目代码之前导入

import layout
import ranks
from hud import Hud
from in_match import MATE_NAME_DELAY, InMatchWatcher
from layout import Mapping
from lobby import LobbyReader, split_progress
from match_start import read_match_start
from ocr import SMALL_CROP_PARAMS, create_engine, load_config
from post_match import PostMatch
import storage
from storage import truncated_of

FOLDERS = {name: SHOTS / "testers" / f"20261104_{name}" for name in ("16x9_a", "16x9_b", "4x3")}
# 4:3 大厅（白银 II）：原尺寸截图上斜杠读错的两种，和读得好好的一张
LOBBY_4X3 = {"full_224540": 436, "full_231152": 681, "full_231253": 681, "0985": 436}


def shot(folder, prefix):
    return load(glob.glob(str(FOLDERS[folder] / f"{prefix}*.jpg"))[0])


def text_checks():
    """不用截图：段内分数那一行斜杠读错时怎么拆（lobby.split_progress）。"""
    return {
        "白银一段 750 分、青铜 IV 500 分（ranks.division_size）":
            (ranks.division_size("白银", "II"), ranks.division_size("青铜", "IV")) == (750, 500),
        "'436/750.RP'（多读出一个点）→ 436": split_progress(["436/750.RP"], 750, [314]) == 436,
        "'6817750RP'（斜杠读成 7）→ 681，和右边'69 RP'对得上": split_progress(["6817750RP"], 750, [69]) == 681,
        "'436750RP'（斜杠没读出来）没法核对时不猜（436 还是 43）": split_progress(["436750RP"], 750) is None,
        "'436750RP' 有右边的'314 RP'核对 → 436": split_progress(["436750RP"], 750, [314]) == 436,
        "右边那行'69RP'本身不当作段内分数": split_progress(["69RP"], 750, [69]) is None,
        # 名字是编的（别的玩家的名字不进仓库）
        "名字被截短：'Abcd'是'Abcdefg'的开头 → 同一个人，记长的": truncated_of("Abcd", "Abcdefg") == "Abcdefg",
        "反过来、中文、不管空格也一样": (truncated_of("测试名字", "测试"), truncated_of("Ab Cd", "AbCdEf")) == ("测试名字", "AbCdEf"),
        "一样的名字、只剩 1 个字、不是开头的：都不算截短":
            (truncated_of("名字A", "名字A"), truncated_of("A", "Abc"), truncated_of("Abcx", "Abcdefg")) == (None, None, None),
    }


def merge_checks():
    """不用截图：对局中和总结页的队友名字，一边被截短了也对得上，记长的那个（名字是编的）。"""
    log = []
    p = PostMatch(storage.connect(":memory:"), log=log.append)
    match_id = storage.insert_match(p.conn, {"placement": 5, "ended_at": "2026-11-04 16:38:39"})
    storage.save_teammates(p.conn, match_id, [{"slot": "left", "name": "Abcdefg"}, {"slot": "right", "name": "测试名字"}])
    p.hud = {"mates": [{"slot": "hud1", "name": "测试名字很长", "legend": "亡灵"},  # 总结页上被游戏截短了
                       {"slot": "hud2", "name": "Ab", "legend": "万蒂奇"}]}  # 对局中赶上了动画
    p._merge_mates(match_id)
    got = [(r["slot"], r["name"], r["legend"]) for r in storage.teammates_of(p.conn, match_id)]
    return {f"对局中'Ab'、总结页'Abcdefg'（以前像不到 0.6，对不上、传奇丢了）；总结页被截短的'测试名字'：都对上，记长的（{got}）":
            got == [("left", "Abcdefg", "万蒂奇"), ("right", "测试名字很长", "亡灵")]}


def own_name_checks():
    """不用截图：自己的名字在总结页卡片上被游戏截短（名字是编的）。"""
    when = datetime(2026, 11, 4, 22, 33, 47)
    p = PostMatch(storage.connect(":memory:"), log=lambda line: None)
    p.on_player_name("Abcdef[ghijkl", when, "总结页")  # 截短的先读到
    first = storage.insert_match(p.conn, {"placement": 15, "ended_at": "2026-11-04 22:33:47", "player_name": p.player_name})
    p._wait_for_name(first)
    p.on_player_name("Abcdef[ghijklm]", when, "大厅")  # 后来大厅读到完整的
    renamed = storage.get_match(p.conn, first)["player_name"]
    p.on_player_name("Abcdef[ghijkl", when, "总结页")  # 再读到截短的
    after = p.player_name
    q = PostMatch(storage.connect(":memory:"), log=lambda line: None)
    q.on_player_name("Bob", when, "大厅")
    q.on_player_name("Bobby", when, "大厅")
    return {
        f"自己的名字：先读到截短的、大厅读到完整的 → 改记完整的，这次记录里已经存下的局也改过来（{renamed}）":
            renamed == "Abcdef[ghijklm]",
        f"之后再读到截短的：仍记完整的（{after}）": after == "Abcdef[ghijklm]",
        f"太短的不算截短（'Bob' 和 'Bobby' 可能真是两个账号，照旧当作换了账号：{q.player_name}）": q.player_name == "Bobby",
    }


def reveal(hud, frame, keep):
    """模拟第 31 赛季队友名字逐字出来的动画：两名队友名字条从左数 keep 个基准像素以后涂成名字条右端的底色。"""
    out, mates = frame.copy(), hud.cfg["teammates"]
    mapping = Mapping(out.shape, hud.base, mates.get("anchor"))
    for slot in mates["slots"]:
        x, y, w, h = slot["name"]
        x0, y0, x1, y1 = mapping.box([x + keep, y, w - keep, h])
        bx0, _, bx1, _ = mapping.box([x + w - 40, y, 30, h])
        out[y0:y1, x0:x1] = np.median(out[y0:y1, bx0:bx1].reshape(-1, 3), axis=0)
    return out


def mate_name_checks(engine):
    """16:9 开局：队友名字刚出现的 2 秒里（每 0.3 秒一张）只露出前半截，之后才完整。"""
    hud = Hud(engine)
    left = hud.cfg["teammates"]["name_left"]
    full = shot("16x9_a", "0158")  # 名字完整的一张
    want = [hud.read_name(hud.mate_name_snip(full, s), left) for s in range(2)]
    half = reveal(hud, full, 50)  # 只露出前三四个字（Evo 当时的日志就是这样）
    early = [hud.read_name(hud.mate_name_snip(half, s), left) for s in range(2)]
    watcher = InMatchWatcher(hud, None, lambda *_: None, load_config("player_name.json"))
    t0 = datetime(2026, 11, 4, 16, 24, 43)
    seen = []
    for k in range(10):  # 0–2.7 秒：前 2.1 秒只露出前半截，2.1 秒那张露出一大半，之后完整
        frame = half if k < 7 else reveal(hud, full, 80) if k == 7 else full
        watcher._vote_mates(frame, t0 + timedelta(seconds=0.3 * k))
        seen.append(sum(len(n) for n in watcher.mate_names))
    got = [m["name"] for m in watcher._read_mates()]
    return {
        f"模拟动画中途：只读到名字的开头（读到 {[len(n or '') for n in early]} 个字，完整的 {[len(n) for n in want]} 个）":
            all(e and w and truncated_of(e, w) == w for e, w in zip(early, want)),
        f"认出队友 {MATE_NAME_DELAY} 秒以内不裁名字（每张以后留下的块数 {seen}）": seen[:7] == [0] * 7 and seen[-1] == 6,
        "留下的 3 块有一块赶上了动画，取读到最多的：两名队友都是完整的名字": got == want,
    }


def lobby_checks(engine):
    layout.set_game_aspect(4 / 3)
    reader = LobbyReader(engine)
    got = {}
    for prefix in LOBBY_4X3:
        r = reader.read(shot("4x3", prefix))
        got[prefix] = (r["tier"], r["division"], r["progress"])
    layout.set_game_aspect(None)
    want = {p: ("白银", "II", v) for p, v in LOBBY_4X3.items()}
    return {f"4:3 大厅：白银 II，原尺寸上斜杠读错的也读得出分数（{got}）": got == want}


def map_checks(engine):
    layout.set_game_aspect(4 / 3)
    start = read_match_start(engine, shot("4x3", "0016"), load_config("match_start.json"))
    layout.set_game_aspect(None)
    return {f"4:3 开局横幅：排位赛、电力区域（{start['mode']}、{start['map']}）":
            (start["mode"], start["map"]) == ("ranked", "电力区域")}


def main():
    print("测试员 11-04 的截图（第 31 赛季，16:9 和 4:3）")
    checks = text_checks()
    checks.update(merge_checks())
    checks.update(own_name_checks())
    missing = [str(f) for f in FOLDERS.values() if not f.is_dir()]
    if missing:
        print(f"  ⏭ 跳过截图部分：找不到 {'、'.join(missing)}（截图不在仓库里）")
        return report(checks)
    engine = create_engine(SMALL_CROP_PARAMS)
    checks.update(map_checks(engine))
    checks.update(lobby_checks(engine))
    checks.update(mate_name_checks(engine))
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
