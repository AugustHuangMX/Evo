"""一局结束后，把各个来源的数据归到同一局里，按优先级定下每一项（规则见 docs/对局全流程图.md"数据来源和优先级"）。

来源（readings 表的 source）：总结页、经验值页、淘汰画面、冠军横幅、对局中（右上角最后一次读数，见 in_match.py）、
大厅校准（见 calibration.py）。每个来源读到的数都记进 readings 表（每局 × 每项 × 每个来源一行），不只记最后采用的那个；
每一项最后用哪个来源的数，由 settle() 统一定：

| 项 | 来源（从高到低） |
|---|---|
| 名次 | 总结页、经验值页、冠军横幅、淘汰画面互相核对，对不上标待确认；都没有时用对局中的剩余小队数 |
| 击杀、助攻 | 总结页 → 对局中 → 经验值页（计分次数，可能偏少） |
| 伤害 | 总结页 → 对局中 |
| 老板分（参与分） | 对局中 |
| RP | 经验值页 → 对局中（只限自己最后被淘汰；先成盒的、拿冠军的不用） → 大厅校准 |

对局中的读数比总结页少是正常的（最后一秒的击杀、伤害没截到）；比总结页多，说明有一边读错了，标待确认。
总结页没读全（停留太短）时，缺的项能用别的来源补上，就不标待确认（2026-10-18 用户决定：玩家很快跳过时，
对局中的数据要可靠到不需要玩家确认）。

怎么归到同一局：
- 以开局画面为界：两次开局画面之间的淘汰画面、总结页、经验值页、对局中的数据算同一局。回到大厅也算这一局结束
- 只有经验值页、没有总结页的局（第 2 名有时没有总结页；或者很快跳过了），在这一局结束时单独记一局
- 在大厅里重新打开上一局的经验值页（0.4.1 起）：这一轮还没有别的数据、读到的每一项都和上一次用过的经验值页一样，
  就是上一局的，不再记（2026-10-27 玩家反馈：每打开一次就多记一局）。重启 Evo 以后不记得上一次的，管不到
- 总结页、经验值页都没有，但有对局中的数据（结算画面一出来就跳过、提前回大厅）：也单独记一局。没看到开局画面、
  也没看到结算画面（比如靶场）的对局中数据不记
- 只看到淘汰画面的局：单独记一局，只有名次（加上模式、地图、传奇）
- 一局只认第一次读到的淘汰画面名次。淘汰画面、冠军横幅、对局中的数据都在总结页、经验值页之前出现：
  它们出现 SAME_MATCH_SECONDS 秒以后又来了，说明是新的一局（没认出开局画面），上一局先收尾。
  对局中的数据看的是开始的时间：读数要等回到大厅、下一局开局时才交来（先读结算画面，见 in_match.py），总结页存下以后才到也照样补进这一局
- 经验值页和总结页对不上（名次不同、计分击杀比总结页多、时间相差太久），说明不是同一局：单独记一局并标记待确认
- 开局画面读到的模式和地图、对局中认出的传奇，属于接下来这一局：存这一局时一起写进去。
  开局画面没读出模式、但有 RP 的局，记为排位（只有排位赛才有排位点数）
- 队友：名字和传奇以对局中左下角为准，总结页（ID 和这一局的数据）按名字对上（_merge_mates）
- 玩家名（区分账号用，见 player_name.py）：整次记录通用，不随每局清掉（换账号要重启游戏）。
  存一局时还不知道名字的，等认出名字后补上
- 一局彻底结束（finish）时调用 on_round_end：记录程序在这里删掉不需要的截图（见 screenshots.py）
"""

import ranks
import storage
from paths import data_relative

SOURCE_SUMMARY, SOURCE_XP, SOURCE_ELIM = "总结页", "经验值页", "淘汰画面"  # 读数来源（readings 表的 source）
SOURCE_CHAMPION, SOURCE_HUD, SOURCE_LOBBY = "冠军横幅", "对局中", "大厅校准"
PLACEMENT_SOURCES = (SOURCE_SUMMARY, SOURCE_XP, SOURCE_CHAMPION, SOURCE_ELIM)  # 互相核对；对不上时先用排在前面的
XP_FIELDS = ("rp_delta", "placement", "kills", "assists", "rank_tier", "rank_division", "rank_progress")
HUD_FIELDS = ("kills", "assists", "participation", "damage")  # 对局中右上角读到就记（成盒以后读不到，用成盒前的）

# 备注里的"说明"：只是说明这一局缺什么，不算要确认的问题。旧版本写的说明也认（面板靠它判断"这局没进总结页"）
XP_ONLY_NOTE = "没有总结页：伤害、生存时间缺失；击杀/助攻是计入排位点数的次数，可能比实际少"
OLD_XP_ONLY_NOTE = "没有总结页（第 2 名、冠军时没有这一页）：伤害、生存时间缺失；击杀/助攻是计入排位点数的次数，可能比实际少"
HUD_ONLY_NOTE = "没有总结页：击杀、助攻、伤害来自对局中右上角；击倒、生存时间、急救、重生缺失"
ELIM_ONLY_NOTE = "只看到淘汰画面（没进总结页和经验值页）：只有名次，击杀、伤害、RP、段位缺失"
LEFT_EARLY_NOTE = "提前回了大厅（没看到结算画面）：名次、RP 等下次回大厅时校准"
REQUEUE_NOTE = "成盒后直接进了下一局（队长重新匹配），没看到结算画面"
NO_SUMMARY_NOTES = (XP_ONLY_NOTE, OLD_XP_ONLY_NOTE, HUD_ONLY_NOTE, ELIM_ONLY_NOTE)
INFO_NOTES = NO_SUMMARY_NOTES + (LEFT_EARLY_NOTE, REQUEUE_NOTE)
COUNTED_KILLS_NOTES = (XP_ONLY_NOTE, OLD_XP_ONLY_NOTE)  # 击杀是"计分次数"的局（总览页的场均击杀不算它们）
NO_END_NOTE = "没看到结算画面（可能队长直接重新匹配、掉线、游戏崩溃或中途停止记录），请核对"

MAX_GAP_SECONDS = 600  # 同一局的总结页和经验值页，时间不会相差这么久
SAME_MATCH_SECONDS = 120  # 总结页、经验值页之后这么久之内看到的淘汰画面等算同一局（新的一局从排队到被淘汰，至少要这么久）
MODE_LABELS = {"ranked": "排位", "pubs": "匹配", "other": "其他"}
MATE_SIMILAR = 0.6  # 对局中和总结页的队友名字这么像就算同一个人（名字里有游戏显示不了的字，两边可能读得不一样）


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
    return f"{ranks.rank_name(reading['rank_tier'], reading['rank_division'])} {reading['rank_progress']}"


def signed(n):
    return "-" if n is None else f"{n:+d}"


# ---------- 每一项最后用哪个来源（settle） ----------

def _split_notes(text):
    """把备注拆成 (问题列表, 说明列表)。说明本身含"；"，先整条拿出来再拆。"""
    text = text or ""
    info = [n for n in INFO_NOTES if n in text]
    for n in info:
        text = text.replace(n, "")
    return [p for p in text.split("；") if p], info


def _first(got, order):
    """按 order 的顺序，第一个读到的来源和值：(来源, 值)；都没有返回 (None, None)。"""
    for source in order:
        if got.get(source) is not None:
            return source, got[source]
    return None, None


def settle(conn, match_id, log=print):
    """按这一局各来源的读数（readings 表）定下名次、击杀、助攻、伤害、老板分、RP，并重写备注、待确认。
    备注里其他来源写的问题（经验值页明细对不上、大厅校准对不上……）保留。"""
    got, sources = {}, set()  # 项 → {来源: 值}；这一局有哪些来源（没读到的也算，比如总结页上名次没读到）
    for r in storage.readings_of(conn, match_id):
        sources.add(r["source"])
        if r["value"] is not None:
            got.setdefault(r["field"], {})[r["source"]] = r["value"]
    row = storage.get_match(conn, match_id)
    problems, old_info = _split_notes(row["review_note"])
    fields, notes, filled = {}, [], []

    # 名次：几个来源互相核对
    placement = got.get("placement", {})
    order = [s for s in PLACEMENT_SOURCES if s in placement]
    if order:
        value = placement[order[0]]
        fields["placement"] = value
        problems = [p for p in problems if not p.startswith("名次")]
        if len({placement[s] for s in order}) > 1:
            notes.append("名次对不上：" + "、".join(f"{s}第 {placement[s]}" for s in order) + "，请核对")
            log(f"           ⚠ {notes[-1]}（编号 {match_id}，先记第 {value} 名）")
        elif row["placement"] is None:
            log(f"           名次没读到，用{order[0]}的第 {value} 名补上（编号 {match_id}）")
        elif len(order) > 1:
            log(f"           名次核对：{'、'.join(order)}都是第 {value}（编号 {match_id}）")
    elif placement.get(SOURCE_HUD) is not None:  # 只有对局中的剩余小队数：只用来补，不参与核对
        fields["placement"] = placement[SOURCE_HUD]
        problems = [p for p in problems if not p.startswith("名次")]
        if row["placement"] is None:
            filled.append(f"名次 {placement[SOURCE_HUD]}（对局中的剩余小队数）")

    # 击杀、助攻、伤害：总结页为准，对局中补空、核对
    labels = {"kills": "击杀", "assists": "助攻", "damage": "伤害"}
    counted = False  # 击杀来自经验值页（计分次数）
    for field, label in labels.items():
        order = (SOURCE_SUMMARY, SOURCE_HUD, SOURCE_XP) if field != "damage" else (SOURCE_SUMMARY, SOURCE_HUD)
        source, value = _first(got.get(field, {}), order)
        if source is None:
            continue
        fields[field] = value
        hud = got.get(field, {}).get(SOURCE_HUD)
        if source == SOURCE_SUMMARY and hud is not None and hud > value:
            notes.append(f"对局中读到的{label} {hud} 多于总结页的 {value}，请核对")
        if source != SOURCE_SUMMARY and row[field] is None:
            filled.append(f"{label} {value}（{source}）")
        counted |= field == "kills" and source == SOURCE_XP
    if fields.get("kills") is not None and fields.get("assists") is not None:
        problems = [p for p in problems if not p.startswith("击杀/助攻/击倒")]
    if fields.get("damage") is not None:
        problems = [p for p in problems if not p.startswith("伤害")]

    # 老板分：只有对局中有
    if got.get("participation", {}).get(SOURCE_HUD) is not None:
        fields["participation"] = got["participation"][SOURCE_HUD]

    # RP：经验值页 → 对局中（只在自己最后被淘汰时记）→ 大厅校准。拿冠军的局不用对局中的：
    # 冠军的名次分是比赛结束后才加的（2026-10-18 实战：右上角最后 +210，经验值页 +263）
    champion = got.get("placement", {}).get(SOURCE_CHAMPION) == 1
    order = (SOURCE_XP, SOURCE_LOBBY) if champion else (SOURCE_XP, SOURCE_HUD, SOURCE_LOBBY)
    source, value = _first(got.get("rp_delta", {}), order)
    if source is not None:
        fields["rp_delta"], fields["rp_source"] = value, source
        hud = got["rp_delta"].get(SOURCE_HUD)
        if source == SOURCE_XP and hud is not None and hud != value:
            log(f"           RP 核对：经验值页 {value:+d}，对局中最后读到 {hud:+d}（编号 {match_id}；记经验值页的）")
        elif source == SOURCE_XP and hud is not None:
            log(f"           RP 核对：经验值页、对局中都是 {value:+d}（编号 {match_id}）")
        if source != SOURCE_XP:
            problems = [p for p in problems if not p.startswith("RP：")]
            if row["rp_delta"] is None:
                filled.append(f"RP {value:+d}（{source}）")
        if row["mode"] is None:
            fields["mode"] = "ranked"  # 有排位点数，说明是排位赛

    # 总结页停留太短：缺的项都补上了，就不用确认
    if all(fields.get(k, row[k]) is not None for k in ("placement", "kills", "assists", "damage")):
        problems = [p for p in problems if not p.startswith(("总结页停留太短", "在总结页等了"))]
    problems = [p for p in problems if not p.startswith("对局中读到的") and p != NO_END_NOTE]

    # 说明：这一局缺什么（0.3.0 以前记的局没有各来源的读数，原来的说明不动）
    info = [] if sources - {SOURCE_LOBBY} else old_info
    if sources - {SOURCE_LOBBY} and SOURCE_SUMMARY not in sources:
        if counted:
            info.append(XP_ONLY_NOTE)
        elif any(SOURCE_HUD in got.get(k, {}) for k in HUD_FIELDS):
            info.append(HUD_ONLY_NOTE)
        elif SOURCE_XP not in sources:
            info.append(ELIM_ONLY_NOTE)
    end = got.get("end_screen", {}).get(SOURCE_HUD)
    if end == "lobby":
        info.append(LEFT_EARLY_NOTE)
    elif end == "requeue":
        info.append(REQUEUE_NOTE)
    elif end in ("stop", "next"):  # 还活着就没了下文：掉线、崩溃、停止记录
        notes.append(NO_END_NOTE)

    all_notes = notes + problems
    fields["review_note"] = "；".join(all_notes + info) or None
    fields["needs_review"] = int(bool(all_notes))
    storage.update_match(conn, match_id, fields)
    if filled:
        log(f"           用别的来源补上（编号 {match_id}）：{'、'.join(filled)}")
    for note in notes:
        if note.startswith("对局中读到的"):
            log(f"           ⚠ {note}（编号 {match_id}）")


class PostMatch:
    def __init__(self, conn, log=print, name_slip=0.8, on_round_end=None):
        self.conn, self.log, self.on_round_end = conn, log, on_round_end
        self.inserted = 0  # 由这里新记下的局数（没有总结页的局）
        self.info = {}  # 开局画面读到的这一局的模式、地图，对局中认出的传奇
        self.unknown_maps = set()  # 开局画面上读到、但不在已知地图名单里的文字
        self.player_name = None  # 这次记录认出的玩家名
        self.name_slip = name_slip  # 和之前的名字这么像，就当作识别误差（见 config/player_name.json）
        self.unnamed = []  # 这次记录里存下时还不知道玩家名的局，认出名字后补上
        self.last_xp = None  # 上一次用过的经验值页读数（补进了一局或单独记了一局），不随每局清掉
        self.reset()

    def reset(self):
        self.match_id = None  # 这一局总结页存进数据库后的编号
        self.summary = None  # 这一局总结页的读数和时间
        self.xp = None  # 这一局读得最好的一次经验值页：(读数, 问题, 截图路径, 时间)
        self.xp_done = False  # 核对通过的经验值页已经用过了（补进这一局，或单独记成一局）
        self.elim = None  # 这一局淘汰画面读到的名次：(名次, 置信度, 时间)
        self.champion = None  # 认出"捍卫者"（冠军）的时间
        self.hud = None  # 这一局对局中最后的读数（见 in_match.py 的 InMatchWatcher._hand_over）
        self.hud_end = None  # 对局中看到这一局结束的时间（读数要等回到大厅、下一局开局时才交来）

    def info_complete(self):
        return "mode" in self.info and "map" in self.info

    def _pages_time(self):
        """这一局总结页、经验值页最晚出现的时间；都还没有为 None。"""
        pages = [t for t in (self.summary and self.summary[1], self.xp and self.xp[3]) if t]
        return max(pages) if pages else None

    def _late(self, when):
        """when 比这一局的总结页、经验值页晚了 SAME_MATCH_SECONDS 秒以上：是新的一局（没认出开局画面）。"""
        pages = self._pages_time()
        return pages is not None and (when - pages).total_seconds() > SAME_MATCH_SECONDS

    # ---------- 各个画面交来的数据 ----------

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

    def on_eliminated(self, placement, score, when):
        """淘汰画面读到名次之后调用（见 eliminated.py）。一局只认第一次读到的。"""
        if self.elim is not None and placement == self.elim[0]:
            return  # 同一个画面又出现了一次
        if self._late(when):
            self.finish()  # 新的一局（没认出开局画面）：上一局先收尾
        elif self.elim is not None:
            self.log(f"[{when:%H:%M:%S}] 这一局又看到\"排名第 {placement}\"（之前是第 {self.elim[0]}），只认第一次")
            return
        self.elim = (placement, score, when)
        if self.match_id is not None:  # 总结页已经存下了（淘汰画面这次比它晚）
            self._save_elim(self.match_id)
            self._settle(self.match_id)

    def on_champion(self, when, where):
        """认出"捍卫者"（冠军）：拿冠军那一刻的横幅，或者总结页顶部的标题。名次就是第 1。"""
        if self.champion is None and self._late(when) and where != SOURCE_SUMMARY:
            self.finish()
        if self.champion is None:
            self.log(f"[{when:%H:%M:%S}] {where}：捍卫者（冠军），记第 1 名")
        self.champion = self.champion or when
        if self.match_id is not None:
            self._save_champion(self.match_id)
            self._settle(self.match_id)

    def on_hud_end(self, when):
        """对局中看到这一局结束了（结算画面）。对局中的读数等回到大厅、下一局开局时才交来（先读结算画面，见 in_match.py）。"""
        self.hud_end = when

    def on_hud_final(self, hud):
        """对局中最后的读数（一局结束以后，回到大厅、下一局开局、停止记录时，见 in_match.InMatchWatcher._hand_over）：
        started、ended（时间）、end（怎么结束的：eliminated / champion / summary / xp / lobby / next / stop）、
        saw_start（看到了这一局的开局画面）、own_dead（结束时自己已经成盒）、values（右上角各项，没有的为 None）、
        mates（[{slot, name, legend}]）、weapons（每一段拿在手上的武器 [(编号, 开始秒, 结束秒)]，见 weapons.WeaponTimer）。"""
        if self._late(hud["started"]):
            self.finish()  # 这些数据比上一局的结算画面还晚：是新的一局
        self.hud = hud
        if self.match_id is not None:
            if hud.get("legend") and storage.get_match(self.conn, self.match_id)["legend"] is None:
                storage.update_match(self.conn, self.match_id, {"legend": hud["legend"]})
            self._save_hud(self.match_id)
            self._merge_mates(self.match_id)
            self._settle(self.match_id)

    def on_summary_saved(self, match_id, values, when, player_name=None):
        """总结页存进数据库之后调用（总结页的读数、队友已经记进去了）。player_name：总结页中间卡片上读到的名字（没读到为 None）。"""
        if self.match_id is not None:  # 没看到开局画面，又来了一局新的总结页：上一局先收尾
            self.finish()
        self.match_id, self.summary = match_id, (values, when)
        if player_name is not None:
            self.on_player_name(player_name, when, "总结页")
        fields = dict(self.info)
        if self._legend():
            fields["legend"] = self._legend()
        if self.player_name is not None:
            fields["player_name"] = self.player_name
        if fields:
            storage.update_match(self.conn, match_id, fields)
        self._wait_for_name(match_id)
        if self.elim is not None:
            self._save_elim(match_id)
        if self.champion is not None:
            self._save_champion(match_id)
        if self.hud is not None:
            self._save_hud(match_id)
            self._merge_mates(match_id)
        self._settle(match_id)
        self._use_xp()

    def on_xp(self, reading, problems, path, when):
        """每次离开经验值页、识别了最后一帧之后调用。"""
        if self.xp_done:
            return
        if self._reopened_xp(reading):
            self.log(f"[{when:%H:%M:%S}] 回到了上一局的经验值页（在大厅里打开的），不重复记")
            return
        if self.xp is None or xp_score(reading, problems) > xp_score(*self.xp[:2]):
            self.xp = (reading, problems, path, when)
        if self.xp[1]:  # 到目前为止还没有一次读全
            self.log(f"[{when:%H:%M:%S}] 经验值页没读全（{problems[0]}）。下次等数字停下再离开")
        self._use_xp()

    def finish(self):
        """这一局彻底结束（下一局开局、回到大厅，或停止运行）。没有总结页的局在这里单独记一局。
        开局画面会反复进出：只有这一局真的有数据时，才清掉开局画面读到的模式、地图。"""
        had_match = any(x is not None for x in (self.match_id, self.xp, self.elim, self.hud, self.champion))
        if self.match_id is None:
            if self.xp is not None and not self.xp_done:
                reading, problems, path, when = self.xp
                self._insert_xp_only(reading, not problems, problems, path, when, with_others=True)
            elif self._hud_counts():
                self._insert_hud_only()
            elif self.elim is not None or self.champion is not None:
                self._insert_elim_only()
        if had_match:
            if "map" not in self.info and self.unknown_maps:
                self.log(f"           开局画面没认出地图（读到：{'、'.join(sorted(self.unknown_maps))}）。"
                         "如果是新地图，可以加进 config/match_start.json")
            self.info, self.unknown_maps = {}, set()
        self.reset()
        if self.on_round_end is not None:
            self.on_round_end()

    # ---------- 内部 ----------

    def _settle(self, match_id):
        settle(self.conn, match_id, self.log)

    def _hud_counts(self):
        """对局中的数据能不能单独记一局：看到了开局画面，或者看到了结算画面（靶场里的不算）。"""
        h = self.hud
        return h is not None and (h["saw_start"] or h["end"] in ("eliminated", "champion", "summary", "xp", "squad_out")) \
            and (any(h["values"].get(k) is not None for k in HUD_FIELDS) or h["mates"])

    def _reopened_xp(self, reading):
        """是不是在大厅里重新打开了上一局的经验值页：这一轮还没有别的数据（真正只有经验值页的局前面有对局中的数据，
        读数还没交来时至少看到了结束），读到的项（至少一项）都和上一次用过的一样。"""
        if self.last_xp is None or any(x is not None for x in (self.match_id, self.hud, self.hud_end, self.elim,
                                                                self.champion)):
            return False
        read = {k: reading[k] for k in XP_FIELDS if reading[k] is not None}
        return bool(read) and all(self.last_xp[k] == v for k, v in read.items())

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
            self._insert_xp_only(reading, True, [note], path, when, with_others=False)  # 其他来源属于总结页那一局
        else:
            self._apply(reading)
        self.xp_done = True

    def _apply(self, reading):
        """把核对通过的经验值页补进这一局：段位、段内进度；RP、名次交给 settle 按优先级定。"""
        storage.update_match(self.conn, self.match_id, {
            "rank_tier": reading["rank_tier"],
            "rank_division": reading["rank_division"],
            "rank_progress": reading["rank_progress"],
        })
        self.log(f"           经验值页：RP {signed(reading['rp_delta'])}，第 {reading['placement']} 名，"
                 f"段位 {rank_text(reading)} → 已补进编号 {self.match_id}")
        self._save_xp(self.match_id)
        self._settle(self.match_id)

    def _save_xp(self, match_id):
        reading, _, _, when = self.xp
        self.last_xp = reading
        storage.save_readings(self.conn, match_id, SOURCE_XP, {k: reading[k] for k in XP_FIELDS},
                              f"{when:%Y-%m-%d %H:%M:%S}")

    def _save_elim(self, match_id):
        placement, score, when = self.elim
        storage.save_readings(self.conn, match_id, SOURCE_ELIM, {"placement": placement},
                              f"{when:%Y-%m-%d %H:%M:%S}", {"placement": score})

    def _save_champion(self, match_id):
        storage.save_readings(self.conn, match_id, SOURCE_CHAMPION, {"placement": 1},
                              f"{self.champion:%Y-%m-%d %H:%M:%S}")

    def _save_hud(self, match_id):
        """对局中最后的读数记进 readings 表，拿过的武器记进 weapon_holds 表。自己先成盒的局（队友后被淘汰），RP 和剩余小队数不是最终的：
        RP 另记成 rp_at_death（只留着以后核对，不采用），剩余小队数不记。end_screen：这一局怎么结束的（settle 写说明用）。"""
        h = self.hud
        values = {k: h["values"].get(k) for k in HUD_FIELDS}
        if h["own_dead"]:
            values["rp_at_death"] = h["values"].get("rp_delta")
        elif h["end"] == "champion":  # 冠军的名次分比赛结束后才加：右上角最后的 RP 不是最终的，只留着核对
            values["rp_before_win"] = h["values"].get("rp_delta")
            values["placement"] = 1
        else:
            values["rp_delta"] = h["values"].get("rp_delta")
            values["placement"] = 1 if h["end"] == "champion" else h["values"].get("squads_left")
        values["end_screen"] = h["end"]
        storage.save_readings(self.conn, match_id, SOURCE_HUD, values, f"{h['ended']:%Y-%m-%d %H:%M:%S}")
        if h.get("weapons"):
            storage.save_weapon_holds(self.conn, match_id, h["weapons"])  # 数据页"最常用武器"

    def _merge_mates(self, match_id):
        """对局中认出的队友（名字、传奇）和总结页上的队友（名字、ID、数据）按名字对上。
        名字以对局中的为准（2026-10-18 用户决定），总结页的补空；对不上的只在日志里记一笔。没有总结页的局，记成 hud1、hud2。"""
        hud_mates = [m for m in (self.hud["mates"] if self.hud else []) if m["name"] or m["legend"]]
        if not hud_mates:
            return
        rows = storage.teammates_of(self.conn, match_id)
        if not rows:
            storage.save_teammates(self.conn, match_id, [{**m, "slot": m["slot"]} for m in hud_mates])
            return
        free = list(range(len(hud_mates)))
        pairs = []
        for row in rows:
            scores = [(storage.name_similarity(hud_mates[i]["name"], row["name"]), i) for i in free
                      if hud_mates[i]["name"] and row["name"]]
            best = max(scores, default=None)
            if best and best[0] >= MATE_SIMILAR:
                pairs.append((row, hud_mates[best[1]]))
                free.remove(best[1])
        left = [row for row in rows if row not in [p[0] for p in pairs]]
        if len(left) == 1 and len(free) == 1 and not (left[0]["name"] and hud_mates[free[0]]["name"]):
            pairs.append((left[0], hud_mates[free[0]]))  # 只剩一个对一个，而且有一边名字没读到：就是同一个人
            free = []
        for row, mate in pairs:
            fields = {"legend": mate["legend"]}
            if mate["name"]:
                if row["name"] and storage.name_key(mate["name"]) != storage.name_key(row["name"]):
                    self.log(f"           队友名字：对局中读到 {mate['name']}，总结页读到 {row['name']}，记对局中的")
                fields["name"] = mate["name"]
            storage.update_teammate(self.conn, match_id, row["slot"], {k: v for k, v in fields.items() if v})
        for i in free:
            self.log(f"           对局中的队友 {hud_mates[i]['name'] or '（名字没读到）'}"
                     f"（{hud_mates[i]['legend'] or '传奇认不出'}）在总结页上没对上，不记")

    def _base_fields(self, when):
        return {
            "ended_at": f"{when:%Y-%m-%d %H:%M:%S}",
            "mode": self.info.get("mode"),
            "map": self.info.get("map"),
            "legend": self._legend(),
            "player_name": self.player_name,
        }

    def _legend(self):
        """这一局自己的传奇：对局中认出的；对局中的数据交上来时一起带着，免得下一局开局时被清掉
        （2026-10-18 实战：成盒后直接进下一局的那局，传奇认出来了却没记上）。"""
        return self.info.get("legend") or (self.hud or {}).get("legend")

    def _save_others(self, match_id):
        """没有总结页的局：把淘汰画面、冠军横幅、对局中的读数和队友都记进这一局。"""
        if self.elim is not None:
            self._save_elim(match_id)
        if self.champion is not None:
            self._save_champion(match_id)
        if self.hud is not None:
            self._save_hud(match_id)
            self._merge_mates(match_id)

    def _insert_elim_only(self):
        """单独记一局：只看到了淘汰画面（或冠军横幅），没进总结页和经验值页，也没有对局中的数据。"""
        when = self.elim[2] if self.elim else self.champion
        match_id = storage.insert_match(self.conn, self._base_fields(when))
        self._save_others(match_id)
        self._settle(match_id)
        self._wait_for_name(match_id)
        self.inserted += 1
        row = storage.get_match(self.conn, match_id)
        self.log(f"[{when:%H:%M:%S}] 记下一局（编号 {match_id}，只看到{'淘汰画面' if self.elim else '冠军横幅'}）："
                 f"第 {row['placement']} 名")

    def _insert_hud_only(self):
        """单独记一局：没有总结页和经验值页，数据来自对局中右上角（加上淘汰画面、冠军横幅）。"""
        h = self.hud
        match_id = storage.insert_match(self.conn, self._base_fields(h["ended"]))
        self._save_others(match_id)
        self._settle(match_id)
        self._wait_for_name(match_id)
        self.inserted += 1
        row = storage.get_match(self.conn, match_id)
        self.log(f"[{h['ended']:%H:%M:%S}] 记下一局（编号 {match_id}，没有总结页和经验值页，用对局中的数据）："
                 f"第 {row['placement'] or '-'} 名，击杀/助攻 {row['kills']}/{row['assists']}，老板分 {row['participation']}，"
                 f"伤害 {row['damage']}，RP {signed(row['rp_delta'])}")
        if row["needs_review"]:
            self.log(f"           ⚠ {row['review_note']}")

    def _insert_xp_only(self, reading, verified, notes, path, when, with_others):
        """单独记一局（这一局没有总结页）。verified = 经验值页读数核对通过（段位才可信）；
        with_others = 这一局淘汰画面、对局中的读数也算这一局的（经验值页和总结页对不上、另记一局时不算）。"""
        match = {**self._base_fields(when), "review_note": "；".join(notes) or None,
                 "needs_review": int(bool(notes)), "evidence_path": data_relative(path)}
        if verified:
            match.update({k: reading[k] for k in ("rank_tier", "rank_division", "rank_progress")})
        match_id = storage.insert_match(self.conn, match)
        self.last_xp = reading
        storage.save_readings(self.conn, match_id, SOURCE_XP, {k: reading[k] for k in XP_FIELDS},
                              f"{when:%Y-%m-%d %H:%M:%S}")
        if with_others:
            self._save_others(match_id)
        self._settle(match_id)
        self._wait_for_name(match_id)
        self.inserted += 1
        row = storage.get_match(self.conn, match_id)
        self.log(f"[{when:%H:%M:%S}] 记下一局（编号 {match_id}，只有经验值页）：第 {row['placement'] or '-'} 名，"
                 f"RP {signed(row['rp_delta'])}，段位 {rank_text(reading) if verified else '-'}")
        for n in notes:
            self.log(f"           ⚠ {n}")
