"""大厅校准：用前后两次大厅读到的总分，核对中间各局的 RP（2026-10-18 用户决定：总分以大厅为准，是最高优先级）。

前后两次大厅读数之间的排位局，RP 加起来应该等于两次总分之差（总分 = 段位换算成的连续分数，见 ranks.score）：
- 对得上：没问题
- 只缺一局的 RP（比如没进经验值页、成盒后提前回了大厅）：用差值补上，来源记"大厅校准"
- 对不上，或者缺两局以上：这几局标"待确认"，备注写出差多少；总分仍以大厅为准
- 跨了大段（如钻石 → 大师、钻石 → 白金）：升大段可能有奖励分、降大段有保护，前后之差 ≠ 各局 RP 之和，只写日志不标
- 两次之间没有记下任何排位局但总分变了（Evo 没开的时候打过）：只写日志
- 大厅之前最近的那一局没有段位（没进经验值页）：它结束后的段位就是这次大厅读到的，顺便补上（匹配等不算排位的局不补）
只看同一个玩家名的局（多账号），还不知道玩家名的局也算进去。
"""

import ranks
import storage
from post_match import SOURCE_LOBBY, settle

RP_RANGE = (-200, 600)  # 补出来的 RP 不在这个范围里，说明中间还有别的变化（掉线惩罚、赛季重置……），不补


def _same_player(row_name, name):
    return name is None or row_name is None or storage.name_key(row_name) == storage.name_key(name)


def calibrate(conn, reading, log=print):
    """reading：刚记下的大厅读数（read_at、player_name、rank_tier、rank_division、rank_progress、score）。"""
    name = reading["player_name"]
    previous = [r for r in storage.lobby_readings(conn) if r["read_at"] < reading["read_at"]
                and _same_player(r["player_name"], name)]
    games = [m for m in conn.execute("SELECT * FROM matches WHERE ended_at <= ? ORDER BY ended_at, match_id",
                                     (reading["read_at"],)) if _same_player(m["player_name"], name)]
    _fill_last_rank(conn, reading, games, previous, log)
    if not previous:
        log(f"           大厅校准：这是第一次读到大厅的段位（总分 {reading['score']}），以后从这里开始对账")
        return
    prev = previous[-1]
    between = [m for m in games if m["ended_at"] > prev["read_at"]]
    ranked = [m for m in between if m["mode"] == "ranked" or m["rp_delta"] is not None]
    diff = reading["score"] - prev["score"]
    known = sum(m["rp_delta"] for m in ranked if m["rp_delta"] is not None)
    missing = [m for m in ranked if m["rp_delta"] is None]
    ids = "、".join(str(m["match_id"]) for m in ranked)
    head = f"           大厅校准：上次大厅总分 {prev['score']}，这次 {reading['score']}（{diff:+d}）"
    if not ranked:
        if diff:
            log(f"{head}，但中间没有记下排位局（可能 Evo 没开的时候打过），不核对")
        return
    if prev["rank_tier"] != reading["rank_tier"]:
        log(f"{head}，中间 {len(ranked)} 局（编号 {ids}）RP 加起来 {known:+d}；跨了大段（{prev['rank_tier']} → "
            f"{reading['rank_tier']}，可能有升段奖励或降段保护），不核对")
        return
    if not missing:
        if known == diff:
            log(f"{head}，中间 {len(ranked)} 局（编号 {ids}）RP 加起来正好 {known:+d}，对上了")
        else:
            _flag(conn, ranked, f"大厅校准：前后两次大厅总分相差 {diff:+d}，中间 {len(ranked)} 局（编号 {ids}）"
                                f"RP 加起来 {known:+d}，对不上，请核对", log)
        return
    rest = diff - known
    if len(missing) == 1 and RP_RANGE[0] <= rest <= RP_RANGE[1]:
        m = missing[0]
        storage.save_readings(conn, m["match_id"], SOURCE_LOBBY, {"rp_delta": rest}, reading["read_at"])
        settle(conn, m["match_id"], log)
        log(f"{head}，编号 {m['match_id']} 没有 RP，用差值补上 {rest:+d}")
        return
    _flag(conn, missing, f"大厅校准：前后两次大厅总分相差 {diff:+d}，中间有 {len(missing)} 局没读到 RP，"
                         f"这几局合计 {rest:+d}，请核对", log)


def _flag(conn, matches, note, log):
    for m in matches:
        old = m["review_note"] or ""
        if note in old:
            continue
        storage.update_match(conn, m["match_id"], {"needs_review": 1, "review_note": f"{note}；{old}" if old else note})
    log(f"           ⚠ {note}（标待确认：编号 {'、'.join(str(m['match_id']) for m in matches)}）")


def _fill_last_rank(conn, reading, games, previous, log):
    """大厅之前最近的那一局没有段位：它结束后的段位就是这次读到的（中间没有别的局）。"""
    if not games:
        return
    last = games[-1]
    if last["rank_tier"] is not None or last["mode"] in ("pubs", "other"):
        return  # 已经有段位；或者是匹配等不算排位的局：段位不会变，不补
    if previous and previous[-1]["read_at"] >= last["ended_at"]:
        return  # 这一局之后已经回过大厅了，那次才是它结束后的段位
    storage.update_match(conn, last["match_id"], {"rank_tier": reading["rank_tier"],
                                                  "rank_division": reading["rank_division"],
                                                  "rank_progress": reading["rank_progress"]})
    log(f"           编号 {last['match_id']} 没有段位：补上这次大厅读到的 "
        f"{ranks.rank_name(reading['rank_tier'], reading['rank_division'])} {reading['rank_progress']}")
