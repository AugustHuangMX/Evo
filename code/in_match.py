"""对局中（开局画面之后、结算画面之前）：认自己和队友，记下自己的状态，连续保存右上角，一局结束时交给 PostMatch。

规则见 docs/对局全流程图.md 第 4–6 步。每张截图调用一次 update(画面, 截图, 时间)：
- 左下角认得出自己的传奇 = 这一张是自己的界面（观战时认不出，见 hud.py）。只在这种截图上：
  - 认自己的传奇：同一个传奇认出 2 次算数（之前在靶场里认出的不算，开局画面会清掉）
  - 认两名队友的传奇（同样 2 次算数，一局只认一次）；名字那一条裁下来先留着
  - 还不知道自己的玩家名时，把头像旁边的名字裁下来先留着（最多 max_hud_reads 块）
  - 右上角那一块存在内存里：内容变了才存，只留最近的 keep 块（一局结束时每块要读约 0.2 秒）
  - 自己那一行变红 = 倒地；看到"观战"标签 = 成盒；成盒后又看到自己的界面 = 被复活
  - 没倒地时看右下角手上那把武器（weapons.py：图标变了才和参照图比），记下每一段拿在手上的时间（2026-10-24）
- 交火时一律不用识别引擎（原则 3）。一局结束时（淘汰画面、冠军横幅、总结页、经验值页、返回大厅按钮、回到大厅、下一局开局、停止记录）
  才读：右上角从最新的一块往回读，每一项取最后一次读到的值；击杀、助攻、老板分、伤害从没显示过就是 0
  （前提是这几块确实是右上角：剩余小队数或 RP 至少读到过一次，否则全部当作没读到）
- 对局中截图加快到每 match_interval 秒一张（config/hud.json，2026-10-18 用户决定先用 0.3 秒测 CPU）：
  从开局画面或看到自己的界面起，到结算画面或回到大厅为止（active）
"""

from collections import deque

from hud import TOP_FIELDS, TOP_LABELS
from weapons import WeaponDetector, WeaponTimer

END_SCREENS = ("eliminated", "champion", "summary", "xp", "squad_out")  # 这一局打完了（squad_out：右下角"返回大厅"）
ZERO_IF_HIDDEN = ("kills", "assists", "participation", "damage")  # 右上角没显示 = 0
STATE_ALIVE, STATE_KNOCKED, STATE_DEAD = "存活", "倒地", "成盒"
SAME_MATCH_SECONDS = 180  # 看到自己的界面以后这么久之内又出现开局画面（横幅闪回来），还是同一局
AFTER_END_SECONDS = 60  # 结算以后这么久之内又看到自己的界面（拿冠军后还能跑几秒），不算新的一局


class InMatchWatcher:
    VOTES = 2  # 同一个传奇认出这么多次才算数

    def __init__(self, hud, post, log, name_cfg, save=None, keep_all=lambda: False, weapons=None):
        """save(截图, 文件名)：存一张截图（记录程序的截图文件夹）；keep_all()：现在是不是"保留全部截图"。
        保留全部截图时（用源码运行时永远是），一局结束时存下最后一张自己的界面（hud_时间_last.png），用来校准对局中的区域。
        weapons：武器识别（weapons.WeaponDetector，不给就新建一个：读 config/weapons.json 和参照图）。"""
        self.hud, self.post, self.log = hud, post, log
        self.weapons = WeaponTimer(weapons or WeaponDetector())
        self.save, self.keep_all = save, keep_all
        self.last_frame = None  # 保留全部截图时：最近一张右上角有变化的自己的界面（整张）
        self.name_place = name_cfg["places"]["hud"]  # 头像旁边的名字（roi、anchor）
        self.max_names = name_cfg["max_hud_reads"]
        top = hud.cfg["top_right"]
        self.keep = top["keep"]
        self.checks = 0  # 看了几次左下角（统计用）
        self.name_reads = 0  # 一局结束时读了几次自己的名字（统计用）
        self.top_reads = 0  # 一局结束时读了几块右上角（统计用）
        self.active = False  # 正在对局中：截图加快
        self._reset(saw_start=False)

    def _reset(self, saw_start):
        self.saw_start = saw_start  # 这一局看到了开局画面（靶场没有开局画面）
        self.started = None  # 这一局第一次看到自己界面的时间
        self.state = None  # 存活 / 倒地 / 成盒
        self.dead_at = None  # 成盒的时间（被复活后清掉）
        self.spectated = {}  # 成盒以后观战的人用的传奇（编号 → 次数）：是队友 = 队友还活着；不是 = 自己是最后一个
        self.votes, self.legend = {}, None
        self.mate_votes = [{}, {}]
        self.mates = [None, None]  # 两名队友的传奇（编号）
        self.mate_names = [[], []]  # 两名队友的名字那一条（最多 3 块）
        self.own_names = []  # 自己的名字那一条
        self.tops = deque(maxlen=self.keep)  # 右上角
        self.weapons.reset()  # 每一段拿在手上的武器
        self.last_frame = None
        self.done = False  # 这一局已经交给 PostMatch 了
        self.done_at = None

    @property
    def pending(self):
        return self.started is not None and not self.done

    def update(self, screen, frame, now):
        if screen == "match_start":
            # 新的一局：上一局已经交完；或者手上的数据不是这一局的（靶场里的、自己已经成盒、看到自己的界面已经很久）。
            # 刚看到自己的界面、横幅又闪回来（同一局的跳伞阶段）不算
            if self.done or (self.pending and (not self.saw_start or self.dead_at is not None
                                                or (now - self.started).total_seconds() > SAME_MATCH_SECONDS)):
                if self.pending:
                    self._finish(now, "next")
                self._reset(saw_start=True)
                self.post.on_legend(None, now)
            self.saw_start = self.active = True
            return
        if screen in END_SCREENS or screen == "lobby":
            if self.pending:
                self._finish(now, screen)
            self.active = False
            return
        if screen is not None:
            return
        self.checks += 1
        own = self.hud.own_legend(frame)
        if own is None:
            if self.pending and self.hud.spectating(frame):
                if self.dead_at is None:
                    self.dead_at = now
                    self._set_state(STATE_DEAD, now)
                    if self.save is not None and self.keep_all():  # 成盒那一刻：查"排名第 X"有没有认出来用
                        self.save(frame, f"hud_{now:%H%M%S}_dead.png")
                watched = self.hud.spectated_legend(frame)
                if watched is not None:
                    self.spectated[watched] = self.spectated.get(watched, 0) + 1
            return
        if self.done:  # 结算之后又看到了自己的界面：过了一会儿才算新的一局（没认出开局画面）
            if (now - self.done_at).total_seconds() <= AFTER_END_SECONDS:
                return
            self._reset(saw_start=False)
            self.post.on_legend(None, now)
        if self.started is None:
            self.started = now
            self.log(f"[{now:%H:%M:%S}] 对局中：看到自己的界面，开始记录右上角")
        self.active = True
        if self.dead_at is not None:
            self.dead_at, self.spectated = None, {}
            self.log(f"[{now:%H:%M:%S}] 对局中：被复活了，继续记录")
        self._set_state(STATE_KNOCKED if self.hud.knocked(frame) else STATE_ALIVE, now)
        if self.state == STATE_ALIVE:  # 倒地时拿不了武器
            self.weapons.update(frame, now)
        self._vote_own(own, now)
        self._vote_mates(frame)
        if self.post.player_name is None and len(self.own_names) < self.max_names:
            self.own_names.append(self.hud.own_name_snip(frame, self.name_place["roi"], self.name_place.get("anchor")))
        snip = self.hud.top_snip(frame)
        if self.hud.changed(snip, self.tops[-1] if self.tops else None):
            self.tops.append(snip)
            if self.save is not None and self.keep_all():
                self.last_frame = frame.copy()

    def stop(self, now):
        """停止记录时：还有没交的数据就交上去（PostMatch 会标"没看到结算画面"）。"""
        if self.pending:
            self._finish(now, "stop")
        self.active = False

    def _set_state(self, state, now):
        if state != self.state:
            if self.state is not None or state != STATE_ALIVE:
                self.log(f"[{now:%H:%M:%S}] 对局中：{self.state or '开始'} → {state}")
            self.state = state

    def _vote_own(self, key, now):
        if self.legend is not None:
            return
        self.votes[key] = self.votes.get(key, 0) + 1
        if self.votes[key] >= self.VOTES:
            self.legend = key
            self.post.on_legend(self.hud.legends.name(key), now)

    def _vote_mates(self, frame):
        if all(m is not None for m in self.mates) and all(len(n) >= 3 for n in self.mate_names):
            return
        for slot, key in enumerate(self.hud.mate_legends(frame)):
            if key is None:
                continue
            if len(self.mate_names[slot]) < 3:
                self.mate_names[slot].append(self.hud.mate_name_snip(frame, slot))
            if self.mates[slot] is None:
                votes = self.mate_votes[slot]
                votes[key] = votes.get(key, 0) + 1
                if votes[key] >= self.VOTES:
                    self.mates[slot] = key

    def _read_top(self):
        """从最新的一块往回读，每一项取最后一次读到的值。"""
        got, shown = {}, set()
        for snip in reversed(self.tops):
            values, _, seen = self.hud.read_top(snip)
            self.top_reads += 1
            shown |= seen
            for name in TOP_FIELDS:
                if name not in got and values[name] is not None:
                    got[name] = values[name]
            if len(got) == len(TOP_FIELDS):
                break
        if "squads_left" not in got and "rp_delta" not in got:
            return {name: None for name in TOP_FIELDS}  # 这几块不像右上角（分辨率、界面不对？），不当作 0
        # 没显示过的才算 0；显示着但没读出来的留空（宁可空着，也不写错的 0）
        return {name: got.get(name, 0 if name in ZERO_IF_HIDDEN and name not in shown else None) for name in TOP_FIELDS}

    def _was_last(self):
        """自己成盒以后，观战的都不是队友（认出了被观战的人，队友的传奇也认出来了）：自己是小队最后一个被淘汰的。
        队友还活着时，游戏默认观战队友；全队被淘汰后观战的是对手。认不出来时当作不是（保守：名次、RP 不用右上角的）。"""
        mates = {k for votes in self.mate_votes for k in votes}  # 队友位置上认出过的传奇（认出一次也算，宁可当作队友）
        return bool(self.spectated) and bool(mates) and not (set(self.spectated) & mates)

    def _read_mates(self):
        out = []
        left = self.hud.cfg["teammates"]["name_left"]
        for slot in range(2):
            name = next((n for n in (self.hud.read_name(s, left) for s in self.mate_names[slot]) if n), None)
            key = self.mates[slot]
            if name or key:
                out.append({"slot": f"hud{slot + 1}", "name": name, "legend": self.hud.legends.name(key) if key else None})
        return out

    def _finish(self, now, end):
        """一局结束：读存下来的右上角、名字，交给 PostMatch。"""
        self.done, self.done_at, self.active = True, now, False
        if not self.saw_start and end not in END_SCREENS:
            self.log(f"[{now:%H:%M:%S}] 对局中的数据不记：没看到开局画面，也没看到结算画面（可能是靶场）")
            return
        if self.post.player_name is None:
            for snip in self.own_names:
                self.name_reads += 1
                name = self.hud.read_name(snip)
                if name:
                    self.post.on_player_name(name, now, "对局中")
                    break
        values = self._read_top()
        mates = self._read_mates()
        own_dead = self.dead_at is not None
        last_one = own_dead and self._was_last()
        if own_dead and end == "next":
            end = "requeue"  # 成盒以后直接进了下一局（队长重新匹配）：这一局已经打完了，不是掉线
        top = "，".join(f"{TOP_LABELS[k]} {'-' if values[k] is None else values[k]}" for k in TOP_FIELDS)
        mate_text = "、".join(f"{m['name'] or '名字没读到'}（{m['legend'] or '传奇认不出'}）" for m in mates) or "没认出"
        weapons = self.weapons.result()
        self.log(f"[{now:%H:%M:%S}] 对局结束（{_END_TEXT.get(end, end)}）：右上角最后读到 {top}；"
                 f"自己最后是{self.state or '-'}；队友 {mate_text}；武器 {self._weapon_text(weapons)}")
        if last_one:
            watched = "、".join(self.hud.legends.name(k) for k in self.spectated)
            self.log(f"           成盒以后观战的是 {watched}，不是队友：自己是小队最后一个被淘汰的，右上角最后的名次、RP 就是最终的")
        self.post.on_hud_final({
            "started": self.started, "ended": now, "end": end, "saw_start": self.saw_start,
            "own_dead": own_dead and not last_one, "values": values, "mates": mates,
            "legend": self.hud.legends.name(self.legend) if self.legend else None,
            "weapons": weapons,
        })
        if self.last_frame is not None:
            name = f"hud_{now:%H%M%S}_last.png"
            self.save(self.last_frame, name)
            self.log(f"           保留全部截图：存下最后一张自己的界面（{name}），用来核对右上角、队友的区域")
        self.tops.clear()  # 读完就放掉，省内存
        self.own_names, self.mate_names, self.last_frame = [], [[], []], None


    def _weapon_text(self, holds):
        """日志里的武器：每把拿了多久（拿得最久的在前），如"R-301 3:12、辅助手枪 0:45"。"""
        total = {}
        for key, start, end in holds:
            total[key] = total.get(key, 0) + end - start
        if not total:
            return "没认出"
        return "、".join(f"{self.weapons.detector.name(k)} {int(s // 60)}:{int(s % 60):02d}"
                        for k, s in sorted(total.items(), key=lambda kv: -kv[1]))


_END_TEXT = {"eliminated": "淘汰画面", "champion": "冠军横幅", "summary": "总结页", "xp": "经验值页",
             "lobby": "回到大厅", "next": "下一局开局", "requeue": "成盒后直接进了下一局", "stop": "停止记录",
             "squad_out": "返回大厅按钮：小队已淘汰"}
