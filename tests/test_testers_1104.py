"""测试员 11-04 用测试工具截的第 31 赛季画面：16:9（两位，2560×1440）和 4:3（一位，游戏 1920×1440 压到 1920×1080，手柄）。

截图从 bugs_test/ 里的诊断包解出来，放在 captures/testers/20261104_16x9_a、_16x9_b、_4x3（不在仓库里：有别的玩家的名字；
找不到就跳过）。frames 里的是缩到 1920 宽的 JPG，名字带 full_ 的是原尺寸（每分钟一张，更接近 Evo 实时读的原图）。
- 新地图"电力区域"（E-District）：开局横幅认得出
- 4:3 大厅的段内分数：原尺寸截图上"436/750 RP"会读成"436/750.RP""6817750 RP"（斜杠读错），照样读得出

运行：python tests/test_testers_1104.py
"""

import glob

from fake_game import SHOTS, load, report  # 要在项目代码之前导入

import layout
import ranks
from lobby import LobbyReader, split_progress
from match_start import read_match_start
from ocr import SMALL_CROP_PARAMS, create_engine, load_config

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
    missing = [str(f) for f in FOLDERS.values() if not f.is_dir()]
    if missing:
        print(f"  ⏭ 跳过截图部分：找不到 {'、'.join(missing)}（截图不在仓库里）")
        return report(checks)
    engine = create_engine(SMALL_CROP_PARAMS)
    checks.update(map_checks(engine))
    checks.update(lobby_checks(engine))
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
