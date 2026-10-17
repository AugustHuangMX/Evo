"""一局结束后，把总结页和经验值页的数据归到同一局里。

以开局画面为界：两次开局画面之间出现的总结页和经验值页，算同一局。
- 总结页存进数据库后，用核对通过的经验值页补上 RP、段位；名次缺的话也补上
- 只有经验值页、没有总结页的局（第 2 名、冠军），在下一局开局或停止运行时单独记一局（不标待确认，备注里说明缺什么）
- 经验值页和总结页对不上（名次不同、计分击杀比总结页多、时间相差太久），说明不是同一局
  （比如开局画面没认出来）：单独记一局并标记待确认
- 开局画面读到的模式和地图，属于接下来这一局：存总结页或单独记一局时一起写进去。
  开局画面没读出模式、但经验值页有 RP 的局，记为排位（只有排位赛的经验值页才有排位点数）
- 对局中认出的传奇（见 live_collect.LegendWatcher）也一样，存这一局时一起写进去（存中文名，面板直接显示）
- 玩家名（区分账号用，见 player_name.py）：整次记录通用，不随每局清掉（换账号要重启游戏）。
  存一局时还不知道名字的（比如第一局就没有总结页），等认出名字后补上
- 一局彻底结束（finish）时调用 on_round_end：记录程序在这里删掉不需要的截图（见 screenshots.py）。
  这时这一局要不要确认已经定了（经验值页补上名次后会取消待确认）
"""

import storage
from paths import data_relative

RP_SOURCE_XP = "经验值页"
XP_ONLY_NOTE = "没有总结页（第 2 名、冠军时没有这一页）：伤害、生存时间缺失；击杀/助攻是计入排位点数的次数，可能比实际少"
MAX_GAP_SECONDS = 600  # 同一局的总结页和经验值页，时间不会相差这么久
MODE_LABELS = {"ranked": "排位", "pubs": "匹配", "other": "其他"}


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
    def __init__(self, conn, log=print, name_slip=0.8, on_round_end=None):
        self.conn, self.log, self.on_round_end = conn, log, on_round_end
        self.inserted = 0  # 由这里新记下的局数（只有经验值页的局）
        self.info = {}  # 开局画面读到的这一局的模式、地图，对局中认出的传奇
        self.unknown_maps = set()  # 开局画面上读到、但不在已知地图名单里的文字
        self.player_name = None  # 这次记录认出的玩家名
        self.name_slip = name_slip  # 和之前的名字这么像，就当作识别误差（见 config/player_name.json）
        self.unnamed = []  # 这次记录里存下时还不知道玩家名的局，认出名字后补上
        self.reset()

    def reset(self):
        self.match_id = None  # 这一局总结页存进数据库后的编号
        self.summary = None  # 这一局总结页的读数和时间
        self.xp = None  # 这一局读得最好的一次经验值页：(读数, 问题, 截图路径, 时间)
        self.xp_done = False  # 核对通过的经验值页已经用过了（补进这一局，或单独记成一局）

    def info_complete(self):
        return "mode" in self.info and "map" in self.info

    def on_match_start(self, reading, when):
        """开局画面上读了一帧之后调用（reading 来自 match_start.read_match_start）。读到的项记下来，留给这一局用。"""
        new = {k: reading[k] for k in ("mode", "map") if reading[k] is not None and k not in self.info}
        if reading["map"] is None and len(reading["raw_map"]) >= 2:
            self.unknown_maps.add(reading["raw_map"])
        if new:
            self.info.update(new)
            text = "、".join(MODE_LABELS.get(v, v) if k == "mode" else v for k, v in new.items())
            self.log(f"[{when:%H:%M:%S}] 开局画面：读到 {text}")

    def on_legend(self, legend, when):
        """对局中认出了自己用的传奇（中文名）之后调用，留给这一局用。
        legend 为 None：新的一局开始了，之前认出的不算数（比如之前在靶场里认出的）。"""
        if legend is None:
            self.info.pop("legend", None)
            return
        self.info["legend"] = legend
        self.log(f"[{when:%H:%M:%S}] 对局中：认出传奇 {legend}")

    def on_player_name(self, name, when, where):
        """读到自己的玩家名之后调用（where：在哪读到的，写日志用）。
        - 第一次读到：记下，并补上这次记录里之前存下、还不知道名字的局
        - 和之前的一样（不管空格）：不用管
        - 和之前的很像：当作识别误差，仍用之前的名字
        - 完全不一样：换了账号（用终端运行时，一次记录可能跨过重启游戏），之后的局记在新名字下"""
        old = self.player_name
        if old is not None and storage.name_key(name) == storage.name_key(old):
            return
        if old is not None and storage.name_similarity(name, old) >= self.name_slip:
            self.log(f"[{when:%H:%M:%S}] {where}读到玩家名 {name}，和 {old} 很像，当作识别误差，仍记在 {old} 下")
            return
        self.player_name = name
        if old is None:
            self.log(f"[{when:%H:%M:%S}] {where}：认出玩家名 {name}")
        else:
            self.log(f"[{when:%H:%M:%S}] {where}：玩家名从 {old} 变成 {name}（换了账号？），之后的局记在 {name} 下")
        if self.unnamed:
            for match_id in self.unnamed:
                storage.update_match(self.conn, match_id, {"player_name": name})
            self.log(f"           之前存下的 {len(self.unnamed)} 局（编号 {'、'.join(map(str, self.unnamed))}）补上玩家名")
            self.unnamed = []

    def on_summary_saved(self, match_id, values, when, player_name=None):
        """总结页存进数据库之后调用。player_name：总结页中间卡片上读到的名字（没读到为 None）。"""
        if self.match_id is not None:  # 没看到开局画面，又来了一局新的总结页：上一局先收尾
            self.finish()
        self.match_id, self.summary = match_id, (values, when)
        if player_name is not None:
            self.on_player_name(player_name, when, "总结页")
        fields = dict(self.info)
        if self.player_name is not None:
            fields["player_name"] = self.player_name
        if fields:
            storage.update_match(self.conn, match_id, fields)
        self._wait_for_name(match_id)
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
        """这一局彻底结束（下一局开局，或停止运行）。只有经验值页的局在这里单独记一局。
        开局画面会反复进出：只有这一局真的有数据（总结页或经验值页）时，才清掉开局画面读到的模式、地图。"""
        had_match = self.match_id is not None or self.xp is not None
        if self.match_id is None and self.xp is not None and not self.xp_done:
            reading, problems, path, when = self.xp
            self._insert_xp_only(reading, not problems, problems, path, when)
        if had_match:
            if "map" not in self.info and self.unknown_maps:
                self.log(f"           开局画面没认出地图（读到：{'、'.join(sorted(self.unknown_maps))}）。"
                         "如果是新地图，可以加进 config/match_start.json")
            self.info, self.unknown_maps = {}, set()
        self.reset()
        if self.on_round_end is not None:
            self.on_round_end()

    def _wait_for_name(self, match_id):
        """刚存下一局：还不知道玩家名的话，记下编号，认出名字后补上。"""
        if self.player_name is None:
            self.unnamed.append(match_id)

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
        if row["mode"] is None:
            fields["mode"] = "ranked"  # 经验值页有排位点数，说明是排位赛
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
            "evidence_path": data_relative(path),
            "mode": self.info.get("mode") or ("ranked" if rp is not None else None),
            "map": self.info.get("map"),
            "legend": self.info.get("legend"),
            "player_name": self.player_name,
        }
        if verified:
            match.update({k: reading[k] for k in ("rank_tier", "rank_division", "rank_progress")})
        match_id = storage.insert_match(self.conn, match)
        self._wait_for_name(match_id)
        self.inserted += 1
        rp_text = "-" if rp is None else f"{rp:+d}"
        self.log(f"[{when:%H:%M:%S}] 记下一局（编号 {match_id}，只有经验值页）：第 {reading['placement'] or '-'} 名，"
                 f"RP {rp_text}，段位 {rank_text(reading) if verified else '-'}")
        for n in notes:
            self.log(f"           ⚠ {n}")
