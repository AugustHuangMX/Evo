"""Local analysis data. Missing OCR values stay null; no network or database writes."""

from datetime import date
import math

BASE_METRICS = {
    "damage": ("伤害", 0), "kills": ("击杀", 1), "assists": ("助攻", 1),
    "knockdowns": ("击倒", 1), "placement": ("名次", 1),
    "survival_time": ("生存时间（秒）", 0), "rp_delta": ("RP", 1),
    "revives": ("救起队友", 1), "respawns": ("重生队友", 1),
}
METRICS = {**BASE_METRICS,
    "ka": ("K+A", 1), "squad_kills": ("小队击杀", 1),
    "damage_per_min": ("每存活分钟伤害", 1), "ka_per10": ("每存活10分钟 K+A", 1),
    "knocks_per10": ("每存活10分钟击倒", 1),
    "kill_participation": ("击杀参与率（%）", 1), "damage_share": ("小队伤害占比（%）", 1),
    "kill_share": ("小队击杀占比（%）", 1),
}
MODES = {"all": "全部模式", "ranked": "排位", "pubs": "匹配及其他", "unknown": "未识别模式"}


def select(rows, args):
    """Filter before taking the latest 30 matches, consistently across every local card."""
    options = {key: sorted({r[key] for r in rows if r[key]}) for key in ("legend", "map")}
    chosen = {"mode": args.get("mode") if args.get("mode") in MODES else "all"}
    errors = []
    for key in options:
        value = args.get(key, "")
        chosen[key] = value if value in options[key] or value == "__unknown__" else ""
    for key in ("since", "until"):
        value = args.get(key, "")
        try:
            chosen[key] = date.fromisoformat(value).isoformat() if value else ""
        except ValueError:
            chosen[key] = ""
            errors.append("日期格式无效，请使用年-月-日。")
    if chosen["since"] and chosen["until"] and chosen["since"] > chosen["until"]:
        errors.append("开始日期不能晚于结束日期。")

    def include(r):
        mode = chosen["mode"]
        if mode == "ranked" and r["mode"] != "ranked":
            return False
        if mode == "pubs" and r["mode"] == "ranked":
            return False
        if mode == "unknown" and r["mode"]:
            return False
        for key in options:
            value = chosen[key]
            if value and ((value == "__unknown__" and r[key]) or (value != "__unknown__" and r[key] != value)):
                return False
        day = (r["ended_at"] or "")[:10]
        if chosen["since"] or chosen["until"]:
            try:
                date.fromisoformat(day)
            except ValueError:
                return False
            if chosen["since"] and day < chosen["since"]:
                return False
            if chosen["until"] and day > chosen["until"]:
                return False
        return True

    filtered = [] if errors else [r for r in rows if include(r)]
    return filtered, chosen, options, list(dict.fromkeys(errors))


def payload(rows, counted_kills_notes, pages=None, teammates=()):
    """Only fields needed by the charts; never include names, screenshots or review notes."""
    data = []
    by_match = {}
    for mate in teammates:
        by_match.setdefault(mate["match_id"], []).append(dict(mate))

    def number(value, signed=False):
        return value if isinstance(value, (int, float)) and math.isfinite(value) and (signed or value >= 0) else None

    for r in rows:
        item = {key: number(r[key], key == "rp_delta") for key in BASE_METRICS}
        if item["placement"] is not None and not 1 <= item["placement"] <= 20:
            item["placement"] = None
        if any(note in (r["review_note"] or "") for note in counted_kills_notes):
            item["kills"] = None
            item["assists"] = None  # XP-page assists can also be scoring counts, not actual assists.
        # A stray value on a non-ranked match must not become ranked performance.
        if r["mode"] != "ranked":
            item["rp_delta"] = None
        k, a, damage, seconds = (item[key] for key in ("kills", "assists", "damage", "survival_time"))
        item["ka"] = k + a if k is not None and a is not None else None
        for key, field, scale in (("damage_per_min", "damage", 60), ("ka_per10", "ka", 600), ("knocks_per10", "knockdowns", 600)):
            item[key] = item[field] / seconds * scale if seconds is not None and seconds > 0 and item[field] is not None else None
        # Only the two summary slots are a complete trio; HUD duplicates are not additional players.
        pair = [m for m in by_match.get(r["match_id"], ()) if m.get("slot") in ("left", "right")]
        complete = len(pair) == 2 and {m["slot"] for m in pair} == {"left", "right"}
        if complete:
            ids = [m.get("player_id") for m in pair]
            if all(ids):
                complete = ids[0] != ids[1]
            elif pair[0].get("name") and pair[0]["name"] == pair[1].get("name"):
                complete = False
        squad = number(r["squad_kills"])
        item["squad_source"] = "记录的小队击杀" if squad is not None else None
        if squad is None and complete and k is not None and all(number(m.get("kills")) is not None for m in pair):
            squad = k + sum(m["kills"] for m in pair)
            item["squad_source"] = "本人及两名队友击杀合计"
        item["squad_kills"] = squad
        item["kill_participation"] = item["ka"] / squad * 100 if squad and item["ka"] is not None and item["ka"] <= squad else None
        item["kill_share"] = k / squad * 100 if squad and k is not None and k <= squad else None
        item["squad_damage"] = damage + sum(m["damage"] for m in pair) if complete and damage is not None and all(number(m.get("damage")) is not None for m in pair) else None
        item["damage_share"] = damage / item["squad_damage"] * 100 if item["squad_damage"] else None
        item["rank_tier"] = (r["rank_tier"] or "未识别段位") if r["mode"] == "ranked" else "非排位 / 未识别模式"
        item.update(id=r["match_id"], page=(pages or {}).get(r["match_id"], 1), when=r["ended_at"] or "", legend=r["legend"] or "未识别传奇",
                    map=r["map"] or "未识别地图", mode={"ranked": "排位", "pubs": "匹配"}.get(r["mode"], "其他 / 未识别"),
                    review=bool(r["needs_review"]))
        data.append(item)
    return {"rows": data, "metrics": {key: {"label": label, "digits": digits} for key, (label, digits) in METRICS.items()}}
