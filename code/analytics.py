"""Local analysis data. Missing OCR values stay null; no network or database writes."""

from datetime import date

METRICS = {
    "damage": ("伤害", 0), "kills": ("击杀", 1), "assists": ("助攻", 1),
    "knockdowns": ("击倒", 1), "placement": ("名次", 1),
    "survival_time": ("生存时间（秒）", 0), "rp_delta": ("RP", 1),
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


def payload(rows, counted_kills_notes, pages=None):
    """Only fields needed by the charts; never include names, screenshots or review notes."""
    data = []
    for r in rows:
        item = {key: r[key] for key in METRICS}
        if any(note in (r["review_note"] or "") for note in counted_kills_notes):
            item["kills"] = None
        # A stray value on a non-ranked match must not become ranked performance.
        if r["mode"] != "ranked":
            item["rp_delta"] = None
        item.update(id=r["match_id"], page=(pages or {}).get(r["match_id"], 1), when=r["ended_at"] or "", legend=r["legend"] or "未识别传奇",
                    map=r["map"] or "未识别地图", mode={"ranked": "排位", "pubs": "匹配"}.get(r["mode"], "其他 / 未识别"),
                    review=bool(r["needs_review"]))
        data.append(item)
    return {"rows": data, "metrics": {key: {"label": label, "digits": digits} for key, (label, digits) in METRICS.items()}}
