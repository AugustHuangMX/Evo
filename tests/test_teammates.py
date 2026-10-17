"""队友：读总结页左右两张卡片（teammates.py）、队友表（storage.py）、战绩页展开两行（panel_app.py + matches.html）。

- 截图（找不到就跳过）：captures/live/ 里每一张总结页都读出两名队友，除了名字只有一个字的那位，名字、ID、各项数字全读到；
  三人击杀加起来等于小队总击杀（小队总击杀读到时）；同一个 ID 读出的名字都一样（不管空格）
  记录程序整条流程（存一局时读队友）在 test_replay.py 里逐项对答案
- 队友表（不用截图）：版本 3 的旧数据库升级后有队友表、原来的局不变；存、覆盖、删一局、清空都会带上队友
- 战绩页（不用截图，Flask 测试客户端）：点开一局只展开两名队友（名字、击杀/助攻/击倒、伤害、生存、急救、重生，悬停显示 ID），
  截图和修改表单要再点"详情与修改"；换一局时详情收起；待确认的局直接全部展开；
  只有经验值页的局、没有队友数据的局各有说明；名字没读到时写"名字没读到"

运行：python tests/test_teammates.py
"""

import re
import sqlite3
import tempfile
from pathlib import Path

from fake_game import SHOTS, report  # 要在项目代码之前导入（设置临时数据文件夹）

import panel_app
import storage
from post_match import XP_ONLY_NOTE

FOLDER = Path(tempfile.mkdtemp(prefix="evo-mates-", dir=storage.DB_PATH.parent.parent))  # 在测试的临时数据文件夹里


def screenshot_checks():
    """返回检查结果；没有截图返回 None。"""
    files = sorted(SHOTS.glob("live/*/[0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9].png"))
    if not files:
        return None
    from ocr import SMALL_CROP_PARAMS, create_engine, load_config, load_image  # 用到截图才加载识别引擎
    from summary_page import CONFIG_NAME, read_summary
    from teammates import read_teammates

    cfg, engine = load_config(CONFIG_NAME), create_engine(SMALL_CROP_PARAMS)
    two, complete, sums, sums_ok, names_by_id, one_letter = 0, 0, 0, 0, {}, 0
    for path in files:
        img = load_image(path)
        own, _ = read_summary(engine, img, cfg)
        mates = read_teammates(engine, img, cfg)
        two += len(mates) == 2
        for m in mates:
            numbers = all(m[k] is not None for k in storage.MATE_COLUMNS if k not in ("name", "player_id"))
            complete += numbers and m["player_id"] is not None and m["name"] is not None
            one_letter += numbers and m["player_id"] is not None and m["name"] is None
            if m["player_id"] and m["name"]:
                names_by_id.setdefault(m["player_id"], set()).add(storage.name_key(m["name"]))
        kills = [own["kda"]["value"][0] if own["kda"]["value"] else None] + [m["kills"] for m in mates]
        if own["squad_kills"]["value"] is not None and None not in kills:
            sums += 1
            sums_ok += sum(kills) == own["squad_kills"]["value"]
    people = 2 * len(files)
    return {
        f"{len(files)} 张总结页都读出两名队友（{two} 张）": two == len(files),
        f"{people} 人次里 {complete} 人次名字、ID、各项数字全读到；"
        f"其余 {one_letter} 人次是名字只有一个字（规则要求至少 2 个字，宁可留空）": complete + one_letter == people and one_letter <= 1,
        f"三人击杀加起来 = 小队总击杀（能核对的 {sums} 张里 {sums_ok} 张对得上）": sums >= 10 and sums_ok == sums,
        f"同一个 ID 读出的名字都一样（{len(names_by_id)} 个 ID）": all(len(v) == 1 for v in names_by_id.values()),
    }


def storage_checks():
    path = FOLDER / "v3.db"  # 版本 3 的数据库：有对照表，没有队友表
    path.unlink(missing_ok=True)
    old = sqlite3.connect(path)
    old.execute(f"CREATE TABLE matches ({', '.join(f'{k} {v}' for k, v in storage.COLUMNS.items())})")
    old.execute(storage.NAME_TABLE)
    old.execute("INSERT INTO matches (ended_at, kills, damage) VALUES ('2026-10-16 21:00:00', 3, 688)")
    old.execute("PRAGMA user_version = 3")
    old.commit()
    old.close()

    conn = storage.connect(path)
    upgraded = (conn.execute("PRAGMA user_version").fetchone()[0], tuple(conn.execute("SELECT kills, damage FROM matches").fetchone()))
    a = storage.insert_match(conn, {"ended_at": "2026-10-16 22:00:00"})
    b = storage.insert_match(conn, {"ended_at": "2026-10-16 23:00:00"})
    storage.save_teammates(conn, a, [{"slot": "right", "name": "B", "player_id": "016000000000000000002"},
                                     {"slot": "left", "name": "A", "kills": 2, "survival_time": 850}])
    saved = [(m["slot"], m["name"], m["player_id"], m["kills"], m["survival_time"]) for m in storage.teammates_of(conn, a)]
    storage.save_teammates(conn, a, [{"slot": "left", "name": "A2"}])
    overwritten = [(m["slot"], m["name"]) for m in storage.teammates_of(conn, a)]
    storage.save_teammates(conn, b, [{"slot": "left", "name": "C"}])
    storage.delete_match(conn, a)
    after_delete = (storage.teammates_of(conn, a), len(storage.teammates_of(conn, b)))
    storage.delete_all_matches(conn)
    left = conn.execute("SELECT COUNT(*) FROM teammates").fetchone()[0]
    conn.close()
    return {
        f"版本 3 的旧数据库升级到 {storage.SCHEMA_VERSION}，原来的局不变（{upgraded}）": upgraded == (storage.SCHEMA_VERSION, (3, 688)),
        "存两名队友，左边的在前，ID 开头的 0 留着": saved == [("left", "A", None, 2, 850), ("right", "B", "016000000000000000002", None, None)],
        "再存一次是覆盖，不会越存越多": overwritten == [("left", "A2")],
        "删掉一局，它的队友一起删，别的局不动": after_delete == ([], 1),
        "清空所有战绩，队友表也清空": left == 0,
    }


def panel_checks():
    path = FOLDER / "panel.db"
    path.unlink(missing_ok=True)
    conn = storage.connect(path)

    def add(**match):
        return storage.insert_match(conn, {"ended_at": "2026-10-17 12:00:00", "placement": 8, "kills": 3, **match})

    normal = add()
    storage.save_teammates(conn, normal, [
        {"slot": "left", "name": "Teammate A", "player_id": "016000000000000000001", "kills": 0, "assists": 3,
         "knockdowns": 1, "damage": 613, "survival_time": 562, "revives": 0, "respawns": 1},
        {"slot": "right", "name": None, "player_id": "016000000000000000002", "kills": 0, "assists": 2,
         "knockdowns": 0, "damage": 1548, "survival_time": None, "revives": 2, "respawns": 0}])
    review = add(needs_review=1, review_note="名次：没读到")
    storage.save_teammates(conn, review, [{"slot": "left", "name": "Mate"}])
    xp_only = add(review_note=XP_ONLY_NOTE)
    bare = add()
    panel_app.app.config.update(DB=path, TESTING=True)
    client, token = panel_app.app.test_client(), panel_app.app.config["TOKEN"]

    def get(url):
        return client.get(url).get_data(as_text=True)

    closed = get("/matches")
    opened = get(f"/matches?open={normal}")
    detail = get(f"/matches?open={normal}&detail=1")
    other_links = re.findall(r'href="([^"]*open=(\d+)[^"]*)"', detail)
    review_page = get(f"/matches?open={review}")
    xp_page = get(f"/matches?open={xp_only}")
    bare_page = get(f"/matches?open={bare}")
    client.post(f"/matches/{normal}/delete", data={"token": token, "next": "/matches"})
    conn2 = storage.connect(path)
    gone = storage.teammates_of(conn2, normal)
    conn2.close()
    conn.close()
    return {
        "没点开时不显示队友": 'class="mate-row' not in closed,
        "点开一局：两行队友，名字、击杀/助攻/击倒、伤害、生存、急救、重生都在": opened.count('class="mate-row') == 2
        and "Teammate A" in opened and "0 / 3 / 1" in opened and "1,548" in opened and "9:22" in opened
        and "急救 2 · 重生 0" in opened,
        "名字悬停显示 ID；名字没读到时写\"名字没读到\"，ID 照样有": 'title="ID 016000000000000000001"' in opened
        and "名字没读到" in opened and 'title="ID 016000000000000000002"' in opened,
        "点开时截图和修改表单先不出来，有\"详情与修改\"按钮": f'id="upd-{normal}"' not in opened and "详情与修改" in opened
        and f"open={normal}&amp;detail=1" in opened,
        "点\"详情与修改\"后：修改表单出来，按钮变成\"收起详情\"": f'id="upd-{normal}"' in detail and "收起详情" in detail,
        "详情打开时，点别的局（展开别的局）详情收起": bool(other_links)
        and all("detail=" not in href for href, mid in other_links if int(mid) != normal),
        "待确认的局点开就全部展开（队友 + 修改表单），没有\"详情与修改\"按钮": "Mate" in review_page
        and f'id="upd-{review}"' in review_page and "detail-toggle" not in review_page,
        "只有经验值页的局：写明没有总结页、读不到队友": "这局没有总结页" in xp_page,
        "没有队友数据的局：写\"没有队友的数据\"": "没有队友的数据" in bare_page,
        "删除一局：它的队友一起删掉": gone == [],
    }


def main():
    print("队友（总结页左右两张卡片）")
    ok = report(storage_checks())
    ok &= report(panel_checks())
    shots = screenshot_checks()
    if shots is None:
        print("  ⏭ 截图那几项跳过：找不到总结页截图（截图只在开发者电脑上，不在仓库里）")
    else:
        ok &= report(shots)
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
