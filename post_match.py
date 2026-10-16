"""一局结束后，把总结页和经验值页的数据归到同一局里。

以开局画面为界：两次开局画面之间出现的总结页和经验值页，算同一局。
- 总结页存进数据库后，用核对通过的经验值页补上 RP、段位；名次缺的话也补上
- 只有经验值页、没有总结页的局（第 2 名、冠军），在下一局开局或停止运行时单独记一局（不标待确认，备注里说明缺什么）
- 经验值页和总结页对不上（名次不同、计分击杀比总结页多、时间相差太久），说明不是同一局
  （比如开局画面没认出来）：单独记一局并标记待确认
"""

import storage

RP_SOURCE_XP = "经验值页"
XP_ONLY_NOTE = "没有总结页（第 2 名、冠军时没有这一页）：伤害、生存时间缺失；击杀/助攻是计入排位点数的次数，可能比实际少"
MAX_GAP_SECONDS = 600  # 同一局的总结页和经验值页，时间不会相差这么久


def xp_score(reading, problems):
    """经验值页读数的好坏：核对通过的最好；否则读到的项越多越好。"""
    filled = sum(reading[k] is not None for k in ("rp_delta", "placement", "kills", "assists"))
    return (not problems, filled)


def xp_conflicts(reading, xp_time, summary, summary_time):
    """经验值页和总结页对不上的地方。空列表 = 对得上，是同一局。"""
    out = []
    if reading["placement"] is not None and summary.get("placement") is not None \
            and reading["placement"] != summary["placement"]:
        out.append(f"名次 {reading['placement']} ≠ 总结页的 {summary['placement']}")
    kda = summary.get("kda")
    if kda:
        for i, key, label in ((0, "kills", "击杀"), (1, "assists", "助攻")):
            if reading[key] is not None and reading[key] > kda[i]:
                out.append(f"计分{label} {reading[key]} 多于总结页的 {kda[i]}")
    gap = abs((xp_time - summary_time).total_seconds())
    if gap > MAX_GAP_SECONDS:
        out.append(f"时间相差 {gap / 60:.0f} 分钟")
    return out


def rank_text(reading):
    if reading["rank_tier"] is None:
        return "-"
    return f"{reading['rank_tier']} {reading['rank_division']} {reading['rank_progress']}"


class PostMatch:
    def __init__(self, conn, log=print):
        self.conn, self.log = conn, log
        self.inserted = 0  # 由这里新记下的局数（只有经验值页的局）
        self.reset()

    def reset(self):
        self.match_id = None  # 这一局总结页存进数据库后的编号
        self.summary = None  # 这一局总结页的读数和时间
        self.xp = None  # 这一局读得最好的一次经验值页：(读数, 问题, 截图路径, 时间)
        self.xp_done = False  # 核对通过的经验值页已经用过了（补进这一局，或单独记成一局）

    def on_summary_saved(self, match_id, values, when):
        """总结页存进数据库之后调用。"""
        if self.match_id is not None:  # 没看到开局画面，又来了一局新的总结页：上一局先收尾
            self.finish()
        self.match_id, self.summary = match_id, (values, when)
        self._use_xp()

    def on_xp(self, reading, problems, path, when):
        """每次离开经验值页、识别了最后一帧之后调用。"""
        if self.xp_done:
            return
        if self.xp is None or xp_score(reading, problems) > xp_score(*self.xp[:2]):
            self.xp = (reading, problems, path, when)
        if self.xp[1]:  # 到目前为止还没有一次读全
            self.log(f"[{when:%H:%M:%S}] 经验值页没读全（{problems[0]}）。下次等数字停下再离开")
        self._use_xp()

    def finish(self):
        """这一局彻底结束（下一局开局，或停止运行）。只有经验值页的局在这里单独记一局。"""
        if self.match_id is None and self.xp is not None and not self.xp_done:
            reading, problems, path, when = self.xp
            self._insert_xp_only(reading, not problems, problems, path, when)
        self.reset()

    def _use_xp(self):
        if self.xp_done or self.xp is None or self.xp[1] or self.match_id is None:
            return  # 还没有核对通过的经验值页，或者还没有总结页
        reading, _, path, when = self.xp
        conflicts = xp_conflicts(reading, when, *self.summary)
        if conflicts:
            note = "经验值页和这段时间的总结页对不上（" + "；".join(conflicts) + "），可能是另一局，请核对"
            self._insert_xp_only(reading, True, [note], path, when)
        else:
            self._apply(reading)
        self.xp_done = True

    def _apply(self, reading):
        """把核对通过的经验值页补进这一局。"""
        row = storage.get_match(self.conn, self.match_id)
        fields = {
            "rp_delta": reading["rp_delta"],
            "rp_source": RP_SOURCE_XP,
            "rank_tier": reading["rank_tier"],
            "rank_division": reading["rank_division"],
            "rank_progress": reading["rank_progress"],
        }
        if row["placement"] is None and reading["placement"] is not None:
            fields["placement"] = reading["placement"]
            rest = [p for p in (row["review_note"] or "").split("；") if p and not p.startswith("名次")]
            fields["review_note"] = "；".join(rest) or None
            fields["needs_review"] = int(bool(rest))
        storage.update_match(self.conn, self.match_id, fields)
        self.log(f"           经验值页：RP {reading['rp_delta']:+d}，第 {reading['placement']} 名，"
                 f"段位 {rank_text(reading)} → 已补进编号 {self.match_id}")

    def _insert_xp_only(self, reading, verified, notes, path, when):
        """单独记一局（这一局没有总结页）。verified = 经验值页读数核对通过（段位才可信）。"""
        rp = reading["rp_delta"]
        match = {
            "ended_at": f"{when:%Y-%m-%d %H:%M:%S}",
            "placement": reading["placement"],
            "kills": reading["kills"],
            "assists": reading["assists"],
            "rp_delta": rp,
            "rp_source": RP_SOURCE_XP if rp is not None else None,
            "needs_review": int(bool(notes)),
            "review_note": "；".join(notes + [XP_ONLY_NOTE]),
            "evidence_path": str(path),
        }
        if verified:
            match.update({k: reading[k] for k in ("rank_tier", "rank_division", "rank_progress")})
        match_id = storage.insert_match(self.conn, match)
        self.inserted += 1
        rp_text = "-" if rp is None else f"{rp:+d}"
        self.log(f"[{when:%H:%M:%S}] 记下一局（编号 {match_id}，只有经验值页）：第 {reading['placement'] or '-'} 名，"
                 f"RP {rp_text}，段位 {rank_text(reading) if verified else '-'}")
        for n in notes:
            self.log(f"           ⚠ {n}")
