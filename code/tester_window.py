"""测试工具的窗口（tkinter，Python 自带；2026-11-02 用户定的：小窗口，不用命令行）。

一步一步走（窗口上面写着第几步）：档案 → 选项目 → 录制 → 收尾 → 完成。
- 档案：第一次打开时填（昵称、游戏语言、启动项），存在 exe 旁边，以后直接到"选项目"（可以点"改"）
- 选项目：config/tester.json 里的几项，选一项看它的步骤；"打一局"要 Evo 开着，没看到就提醒（照样能开始）
- 录制：截屏在另一个线程里跑（evo_tester.Session），窗口每 0.2 秒刷新一次：录了多久、游戏开没开、存了多少；
  提醒（"截图够了，可以结束了"，黄色框）；这一项的步骤；"靶场：武器"多一份武器名单可以打勾（只是给测试员自己记，不认图）；工具的提示（最近几行）
- 收尾："打一局"一局一局确认 Evo 记下的战绩（对 / 不对，不对就改成正确的），再问有没有漏记的局、大厅的段位和分数对不对，
  可以勾"全部带上"（开发者要样本时）；"赛季数据页"看 Evo 读到了几项、核对了没有。备注（可以不写）→ 打包（也在另一个
  线程里，窗口不卡；确认对了的局的截图打包前删掉，见 tester_answers.py）
- 完成：压缩包在哪、多大，"打开文件夹""再测一项"
关窗口时还在录、还没打包，先问一句，不悄悄丢掉。只用本窗口的按钮，不注册全局键盘钩子（原则 1）。

样子和 Evo 的窗口一样（颜色取自 panel/static/style.css 的深色）；数字用 Windows 自带的 Bahnschrift（tkinter 用不了
Evo 面板的 Chakra Petch 网页字体），中文用微软雅黑。
"""

import ctypes
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from tester_answers import FIELDS, rank_text

# 颜色：同 panel/static/style.css 的深色
BG, CARD, INSET = "#0D0F13", "#151820", "#0D0F13"
TEXT, MUTED, BORDER = "#ECEEF2", "#9AA3B2", "#262C38"
ACCENT, ACCENT_TEXT, GOOD = "#FF5A5A", "#2A0A0A", "#7CE8B8"
WARN_TEXT, WARN_BG, WARN_BORDER = "#F4D68A", "#1E1A10", "#5A4A1E"
NAV_ON = "#1B1F29"

STEPS = ["档案", "选项目", "录制", "收尾", "完成"]
REFRESH_MS = 200
LOG_LINES = 200  # 提示区最多留几行


def _font_family(root, wanted, fallback):
    return wanted if wanted in tkfont.families(root) else fallback


def make_dpi_aware():
    """认得清屏幕缩放（不然 150% 缩放时整个窗口是糊的）。dxcam 之后也会设一次，设过了就不管。"""
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        try:
            ctypes.WinDLL("user32").SetProcessDPIAware()
        except (OSError, AttributeError):
            pass


def dark_title_bar(root):
    """Windows 10 / 11 的深色标题栏（和窗口一样深）；系统不支持就算了。"""
    try:
        root.update_idletasks()
        hwnd = ctypes.WinDLL("user32").GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        ctypes.WinDLL("dwmapi").DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except (OSError, AttributeError):
        pass


def open_folder(path):
    """打开文件夹并选中这个文件（explorer 要这种引号写法）。"""
    subprocess.Popen(f'explorer /select,"{path}"')


class TesterApp:
    """整个窗口。projects：tester_projects.load_projects()；profile：测试员档案（{} = 还没填）；
    new_session(项目, 档案, 写提示)：建一个 evo_tester.Session；evo_running()：Evo 开着没有；
    open_folder(压缩包)：打开文件夹选中它（测试时换掉）。"""

    def __init__(self, root, projects, profile, languages, weapons, save_profile, new_session, evo_running,
                 version, icon=None, open_folder=open_folder):
        self.root, self.projects, self.profile = root, projects, dict(profile)
        self.languages, self.weapons, self.save_profile = languages, weapons, save_profile
        self.new_session, self.evo_running, self.version = new_session, evo_running, version
        self.open_folder = open_folder
        self.queue = queue.Queue()
        self.session = self.stop = None
        self.state = "idle"  # idle / starting / recording / stopping / finishing / done
        self.project = None
        self.started_at = None
        self.zip_path = None

        root.title(f"Evo 测试工具 {version}")
        root.configure(bg=BG)
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)  # 屏幕缩放（150% = 1.5）：窗口大小跟着放大
        height = min(self.px(720), root.winfo_screenheight() - self.px(90))  # 1366×768 的笔记本上也放得下
        root.minsize(self.px(700), min(self.px(520), height))
        root.geometry(f"{self.px(780)}x{height}")
        if icon is not None:
            try:
                root.iconphoto(True, tk.PhotoImage(file=str(icon)))
            except tk.TclError:
                pass
        dark_title_bar(root)
        ui = _font_family(root, "Microsoft YaHei UI", "Microsoft YaHei")
        num = _font_family(root, "Bahnschrift", "Segoe UI")
        self.f_body = (ui, 10)
        self.f_small = (ui, 9)
        self.f_bold = (ui, 10, "bold")
        self.f_title = (ui, 14, "bold")
        self.f_num = (num, 20)
        self.f_brand = (num, 13, "bold")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        style = ttk.Style(root)
        style.theme_use("clam")  # Windows 自带的滚动条不能改颜色；clam 的可以
        style.configure("Evo.Vertical.TScrollbar", background=CARD, troughcolor=BG, bordercolor=BG,
                        arrowcolor=MUTED, lightcolor=CARD, darkcolor=CARD, gripcount=0)
        style.map("Evo.Vertical.TScrollbar", background=[("active", NAV_ON)])

        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=24, pady=(16, 0))
        tk.Label(top, text="EVO", font=self.f_brand, fg=ACCENT, bg=BG).pack(side="left")
        tk.Label(top, text=f"  测试工具 {version}", font=self.f_body, fg=MUTED, bg=BG).pack(side="left")
        self.step_bar = tk.Frame(top, bg=BG)
        self.step_bar.pack(side="right")
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(12, 0))
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=24, pady=16)

        if self.profile.get("nickname") and self.profile.get("game_language"):
            self.show_projects()
        else:
            self.show_profile()
        root.after(REFRESH_MS, self.tick)

    # ---------- 小零件 ----------

    def px(self, n):
        return round(n * self.scale)

    @staticmethod
    def autowrap(label, margin=4):
        """文字按标签实际的宽度换行（窗口拉宽、卡片变宽时跟着变）。空格换成不断行的空格：tkinter 有空格时只在空格处
        断行，"看 Evo 记得对不对"会在 Evo 前后断开、右边空一大块；中文本来就可以在任何字之间断。"""
        label.config(text=label.cget("text").replace(" ", "\u00a0"))
        label.bind("<Configure>", lambda e: label.config(wraplength=max(50, e.width - margin)), add="+")
        return label

    def clear(self, step):
        for child in self.body.winfo_children():
            child.destroy()
        for child in self.step_bar.winfo_children():
            child.destroy()
        for i, name in enumerate(STEPS):
            on = i == step
            tk.Label(self.step_bar, text=f"{i + 1} {name}", font=self.f_bold if on else self.f_small,
                     fg=TEXT if on else MUTED, bg=NAV_ON if on else BG, padx=8, pady=3).pack(side="left", padx=1)
        tk.Label(self.step_bar, text=f"  第 {step + 1} 步，共 {len(STEPS)} 步", font=self.f_small, fg=MUTED,
                 bg=BG).pack(side="left")

    def title(self, parent, text, sub=None):
        tk.Label(parent, text=text, font=self.f_title, fg=TEXT, bg=BG, anchor="w").pack(fill="x")
        if sub:
            self.autowrap(tk.Label(parent, text=sub, font=self.f_body, fg=MUTED, bg=BG, anchor="w",
                                   justify="left")).pack(fill="x", pady=(2, 0))

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, font=self.f_bold if primary else self.f_body,
                         fg=ACCENT_TEXT if primary else TEXT, bg=ACCENT if primary else CARD,
                         activeforeground=ACCENT_TEXT if primary else TEXT,
                         activebackground="#FF7A7A" if primary else NAV_ON, relief="flat", bd=0,
                         padx=18, pady=7, cursor="hand2", highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=TEXT, disabledforeground=MUTED)

    def scroll_area(self, parent):
        """能上下滚动的一块（放得下时不出滚动条），返回里面放东西的框。窗口矮时"结束"等按钮照样看得见。"""
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="both", expand=True, pady=(10, 0))
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview, style="Evo.Vertical.TScrollbar")
        inner = tk.Frame(canvas, bg=BG)
        item = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)

        def fit(_event=None):
            canvas.configure(scrollregion=(0, 0, inner.winfo_reqwidth(), inner.winfo_reqheight()))
            if inner.winfo_reqheight() > canvas.winfo_height() > 1:
                bar.pack(side="right", fill="y")
            else:
                bar.pack_forget()
                canvas.yview_moveto(0)

        def wheel(event):
            if bar.winfo_ismapped():
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        inner.bind("<Configure>", fit)
        # 等空闲时再看要不要滚动条：在这里直接放上 / 拿掉滚动条，里面那层不会跟着变窄（2026-11-02 试出来的）
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(item, width=e.width), canvas.after_idle(fit)))
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def card(self, parent, **pack):
        frame = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER, padx=14, pady=10)
        frame.pack(fill="x", **pack)
        return frame

    def label(self, parent, text, muted=False, bold=False, bg=CARD):
        widget = tk.Label(parent, text=text, font=self.f_bold if bold else (self.f_small if muted else self.f_body),
                          fg=MUTED if muted else TEXT, bg=bg, anchor="w", justify="left")
        widget.pack(fill="x")
        self.autowrap(widget)
        return widget

    def warn(self, parent, text, **pack):
        box = tk.Frame(parent, bg=WARN_BG, highlightthickness=1, highlightbackground=WARN_BORDER, padx=12, pady=8)
        box.pack(fill="x", **pack)
        self.autowrap(tk.Label(box, text=text, font=self.f_body, fg=WARN_TEXT, bg=WARN_BG, anchor="w",
                               justify="left")).pack(fill="x")
        return box

    def entry(self, parent, value=""):
        var = tk.StringVar(value=value)
        tk.Entry(parent, textvariable=var, font=self.f_body, fg=TEXT, bg=INSET, insertbackground=TEXT, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT).pack(fill="x", ipady=5,
                                                                                          pady=(4, 0))
        return var

    def steps_card(self, parent, project, **pack):
        box = self.card(parent, **pack)
        self.label(box, "怎么做", bold=True)
        for i, step in enumerate(project["steps"], 1):
            self.label(box, f"{i}. {step}").pack_configure(pady=(4, 0))
        return box

    # ---------- 1 档案 ----------

    def show_profile(self):
        self.clear(0)
        self.title(self.body, "先填一下你的情况", "只问一次，存在这个程序旁边。会跟着压缩包一起发给开发者，方便知道是谁、在什么设置下测的。")
        box = self.card(self.body, pady=(14, 0))
        self.label(box, "昵称（群里的名字）", bold=True)
        nickname = self.entry(box, self.profile.get("nickname", ""))
        self.label(box, "游戏语言", bold=True).pack_configure(pady=(14, 0))
        lang = tk.StringVar(value=self.profile.get("game_language", ""))
        row = tk.Frame(box, bg=CARD)
        row.pack(fill="x", pady=(4, 0))
        for name in self.languages:
            tk.Radiobutton(row, text=name, value=name, variable=lang, font=self.f_body, fg=TEXT, bg=CARD,
                           selectcolor=INSET, tristatevalue="-",  # 还没选时不画成半选 activebackground=CARD, activeforeground=TEXT,
                           highlightthickness=0).pack(side="left", padx=(0, 16))
        self.label(box, "启动项（可以不填）", bold=True).pack_configure(pady=(14, 0))
        self.label(box, "Steam 或 EA App 里给 Apex 填的启动选项，比如 -w 2560 -h 1600 +cl_is_softened_locale 1", muted=True)
        launch = self.entry(box, self.profile.get("launch_options", ""))
        error = tk.Label(self.body, text="", font=self.f_body, fg=ACCENT, bg=BG, anchor="w")
        error.pack(fill="x", pady=(8, 0))

        def done():
            if not nickname.get().strip():
                error.config(text="请填一下昵称")
                return
            if not lang.get():
                error.config(text="请选一下游戏语言")
                return
            self.profile = {"nickname": nickname.get().strip(), "game_language": lang.get(),
                            "launch_options": launch.get().strip()}
            self.save_profile(self.profile)
            self.show_projects()

        bar = tk.Frame(self.body, bg=BG)
        bar.pack(fill="x", side="bottom")
        self.button(bar, "下一步", done, primary=True).pack(side="right")

    # ---------- 2 选项目 ----------

    def show_projects(self):
        self.clear(1)
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="这次测什么？", font=self.f_title, fg=TEXT, bg=BG).pack(side="left")
        who = f"{self.profile.get('nickname')} · {self.profile.get('game_language')}"
        tk.Button(head, text="改", command=self.show_profile, font=self.f_small, fg=MUTED, bg=BG, relief="flat", bd=0,
                  activebackground=BG, activeforeground=TEXT, cursor="hand2").pack(side="right")
        tk.Label(head, text=who, font=self.f_small, fg=MUTED, bg=BG).pack(side="right", padx=(0, 4))

        choice = tk.StringVar(value=self.project["key"] if self.project else next(iter(self.projects)))
        cards = {}
        grid = tk.Frame(self.body, bg=BG)
        grid.pack(fill="x", pady=(12, 0))
        bar = tk.Frame(self.body, bg=BG)  # 先放最下面的：窗口再矮，"开始"也看得见
        bar.pack(fill="x", side="bottom", pady=(12, 0))
        detail = self.scroll_area(self.body)

        def pick(*_):
            for key, frame in cards.items():
                frame.config(highlightbackground=ACCENT if key == choice.get() else BORDER)
            for child in detail.winfo_children():
                child.destroy()
            project = self.projects[choice.get()]
            if project.get("evo") == "required" and not self.evo_running():
                self.warn(detail, "没看到 Evo 在运行。这一项打完要对 Evo 记下的战绩，请先打开 Evo。"
                                  "不开也能录，但截图会全部带上，压缩包很大", pady=(0, 10))
            self.steps_card(detail, project)

        for i, (key, project) in enumerate(self.projects.items()):
            frame = tk.Frame(grid, bg=CARD, highlightthickness=2, highlightbackground=BORDER, padx=12, pady=10,
                             cursor="hand2")
            frame.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0 if i % 2 == 0 else 6, 0 if i % 2 else 6),
                       pady=6)
            tk.Radiobutton(frame, text=project["name"], value=key, variable=choice, command=pick, font=self.f_bold,
                           fg=TEXT, bg=CARD, selectcolor=INSET, activebackground=CARD, activeforeground=TEXT,
                           highlightthickness=0, anchor="w").pack(fill="x")
            summary = tk.Label(frame, text=project["summary"], font=self.f_small, fg=MUTED, bg=CARD, anchor="w",
                               justify="left")
            self.autowrap(summary)
            summary.pack(fill="x", padx=(22, 0))
            for widget in (frame, summary):
                widget.bind("<Button-1>", lambda _e, k=key: (choice.set(k), pick()))
            cards[key] = frame
        grid.columnconfigure(0, weight=1, uniform="c")
        grid.columnconfigure(1, weight=1, uniform="c")
        pick()
        self.button(bar, "开始", lambda: self.start(self.projects[choice.get()]), primary=True).pack(side="right")

    # ---------- 3 录制 ----------

    def start(self, project):
        self.project = project
        self.stop = threading.Event()
        self.session = self.new_session(project, self.profile, lambda text: self.queue.put(("log", text)),
                                        lambda text: self.queue.put(("notice", text)))
        self.state = "starting"
        self.started_at = time.monotonic()
        self.show_recording()
        threading.Thread(target=self._record, daemon=True).start()

    def _record(self):
        try:
            self.session.start()
            self.queue.put(("started", None))
            self.queue.put(("ended", self.session.run(self.stop)))
        except Exception as e:  # 截屏对象、诊断信息出了意外：告诉测试员，截到的照样能打包
            self.queue.put(("log", f"⚠ 出错了：{type(e).__name__}: {e}"))
            self.queue.put(("ended", f"出错了：{e}"))

    def show_recording(self):
        self.clear(2)
        project = self.project
        head = self.card(self.body)
        row = tk.Frame(head, bg=CARD)
        row.pack(fill="x")
        self.v_state = tk.Label(row, text="正在准备……", font=self.f_bold, fg=TEXT, bg=CARD, anchor="w")
        self.v_state.pack(side="left")
        self.v_time = tk.Label(row, text="0:00", font=self.f_num, fg=TEXT, bg=CARD)
        self.v_time.pack(side="right")
        self.v_count = tk.Label(head, text="", font=self.f_body, fg=MUTED, bg=CARD, anchor="w")
        self.v_count.pack(fill="x")
        self.notice = tk.Frame(self.body, bg=BG)  # 提醒（时间、大小快到了等）
        self.notice.pack(fill="x")

        bar = tk.Frame(self.body, bg=BG)  # 先放最下面的：窗口再矮，"结束"也看得见
        bar.pack(fill="x", side="bottom", pady=(12, 0))
        self.b_end = self.button(bar, "结束", self.end, primary=True)
        self.b_end.pack(side="right")
        tk.Label(bar, text="打完回到这里点“结束”。关掉游戏也会自动结束。", font=self.f_small, fg=MUTED,
                 bg=BG).pack(side="left")
        self.v_log = tk.Text(self.body, height=4, font=self.f_small, fg=MUTED, bg=INSET, relief="flat",
                             highlightthickness=1, highlightbackground=BORDER, wrap="word", padx=8, pady=6)
        self.v_log.pack(fill="x", side="bottom")
        self.v_log.config(state="disabled")
        tk.Label(self.body, text="提示", font=self.f_small, fg=MUTED, bg=BG, anchor="w").pack(fill="x", side="bottom",
                                                                                         pady=(10, 2))

        middle = self.scroll_area(self.body)
        self.steps_card(middle, project)
        if project.get("checklist") == "weapons":
            box = self.card(middle, pady=(10, 0))
            self.label(box, "武器名单（拿过一把就勾上，只是给你自己记的）", bold=True)
            names = tk.Frame(box, bg=CARD)
            names.pack(fill="x", pady=(4, 0))
            english = self.profile.get("game_language") == "English"
            for i, (_key, zh, en) in enumerate(self.weapons):
                tk.Checkbutton(names, text=en if english else zh, font=self.f_small, fg=TEXT, bg=CARD,
                               selectcolor=INSET, activebackground=CARD, activeforeground=TEXT, highlightthickness=0,
                               anchor="w", pady=0).grid(row=i // 4, column=i % 4, sticky="w", padx=(0, 10))
            for c in range(4):
                names.columnconfigure(c, weight=1, uniform="w")

    def end(self):
        if self.state in ("starting", "recording"):
            self.stop.set()
            self.state = "stopping"
            self.b_end.config(text="正在停下……", state="disabled")

    def _append_log(self, text):
        if not hasattr(self, "v_log") or not self.v_log.winfo_exists():
            return
        self.v_log.config(state="normal")
        self.v_log.insert("end", text.strip() + "\n")
        lines = int(self.v_log.index("end-1c").split(".")[0])
        if lines > LOG_LINES:
            self.v_log.delete("1.0", f"{lines - LOG_LINES}.0")
        self.v_log.see("end")
        self.v_log.config(state="disabled")

    def _show_notice(self, text):
        """录的时候的提醒：黄色框放在状态下面（最多留 3 条），提示区也写一行。"""
        self._append_log(f"提醒：{text}")
        if not hasattr(self, "notice") or not self.notice.winfo_exists():
            return
        boxes = self.notice.winfo_children()
        if len(boxes) >= 3:
            boxes[0].destroy()
        self.warn(self.notice, f"{time.strftime('%H:%M')}  {text}", pady=(10, 0))

    def _refresh_recording(self):
        rec = self.session.recorder if self.session else None
        if not hasattr(self, "v_time") or not self.v_time.winfo_exists():
            return
        seconds = int(time.monotonic() - self.started_at)
        self.v_time.config(text=f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600
                           else f"{seconds // 60}:{seconds % 60:02d}")
        if rec is None:
            return
        if self.state == "stopping":
            self.v_state.config(text="正在停下……", fg=MUTED)
        elif rec.game_on:
            self.v_state.config(text="● 正在录（游戏开着）", fg=GOOD)
        else:
            self.v_state.config(text="等游戏打开（打开 Apex 后自动开始录）", fg=WARN_TEXT)
        parts = [f"存了 {rec.saved} 张", f"{rec.mb:.0f} MB"]
        if hasattr(rec, "pages"):
            parts.insert(0, f"存好了 {rec.pages} 页")
        if hasattr(rec, "done"):
            parts.insert(0, f"存好了 {len(rec.done)} 把")
        self.v_count.config(text="  ·  ".join(parts))

    # ---------- 4 收尾、5 完成 ----------

    def show_finish(self, reason):
        self.clear(3)
        self.state = "finishing"
        rec = self.session.recorder
        if rec is None or self.session.info is None:  # 还没开始截屏就出错了：没有东西可打包
            self.state = "done"
            self.title(self.body, "没录成", f"{reason}。请把这个文件夹里的 tool_log.txt 发给开发者：\n{self.session.folder}")
            bar = tk.Frame(self.body, bg=BG)
            bar.pack(fill="x", side="bottom")
            self.button(bar, "关闭", self.root.destroy).pack(side="right")
            self.button(bar, "返回", self.show_projects).pack(side="right", padx=(0, 8))
            return
        self.title(self.body, "录好了", f"结束的原因：{reason}。存了 {rec.saved} 张，{rec.mb:.0f} MB。")
        bar = tk.Frame(self.body, bg=BG)  # 先放最下面的："打包"一直看得见
        bar.pack(fill="x", side="bottom", pady=(12, 0))
        status = tk.Label(bar, text="", font=self.f_body, fg=MUTED, bg=BG)
        status.pack(side="left")
        area = self.scroll_area(self.body)
        kind = self.project.get("answers")
        collect = lambda: None  # noqa: E731  对答案的项目才有要交给打包的答案
        if kind == "matches":
            collect = self.match_answers(area)
        elif kind == "season_stats":
            self.season_answers(area)
        box = self.card(area, pady=(10, 0))
        self.label(box, "备注（可以不写）", bold=True)
        self.label(box, "比如哪里不对、大概几点几分，开发者看截图时好找", muted=True)
        note = tk.Text(box, height=4, font=self.f_body, fg=TEXT, bg=INSET, insertbackground=TEXT, relief="flat",
                       highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, wrap="word",
                       padx=8, pady=6)
        note.pack(fill="x", pady=(4, 0))

        def pack_up():
            button.config(text="正在打包……", state="disabled")
            status.config(text="截图多时要一会儿，别关窗口")
            text, answers = note.get("1.0", "end").strip(), collect()
            threading.Thread(target=lambda: self.queue.put(("packed", self.session.finish(text, answers))),
                             daemon=True).start()

        button = self.button(bar, "打包", pack_up, primary=True)
        button.pack(side="right")

    def choice_row(self, parent, options, variable, bg=CARD):
        """一排单选（还没选时都不选中）。"""
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=(4, 0))
        for text, value in options:
            tk.Radiobutton(row, text=text, value=value, variable=variable, font=self.f_body, fg=TEXT, bg=bg,
                           selectcolor=INSET, tristatevalue="-", activebackground=bg, activeforeground=TEXT,
                           highlightthickness=0).pack(side="left", padx=(0, 18))
        return row

    def match_answers(self, area):
        """打一局：一局一局确认 Evo 记下的战绩，再问漏记、大厅。返回 collect()：交给 Session.finish 的答案。"""
        record = self.session.evo_record()
        matches = record["局"]
        if record["数据库"] is None:
            self.warn(area, "没找到 Evo 的战绩（这台电脑上没装 Evo，或者没打开过）。截图会全部带上")
        elif not matches:
            self.warn(area, "这段时间 Evo 一局都没记下，截图会全部带上。请在下面的备注里写一下打了几局、大概几点、第几名")
        else:
            self.label(area, f"这段时间 Evo 记下了 {len(matches)} 局。请一局一局看看对不对，不对就改成正确的。"
                             "确认对了的局不带截图，压缩包会小很多；没选的局截图照样带上", bg=BG)
        judged = {}
        for i, match in enumerate(matches, 1):
            box = self.card(area, pady=(10, 0))
            facts = [f"第 {i} 局", f"{match['last_read']:%H:%M} 读完"]
            facts += [str(match[k]) for k in ("mode", "map", "legend") if match.get(k)]
            self.label(box, " · ".join(facts), bold=True)
            if match.get("needs_review"):
                self.label(box, f"Evo 标了待确认：{match.get('review_note') or '读得不确定'}", muted=True)
            grid = tk.Frame(box, bg=CARD)
            grid.pack(fill="x", pady=(6, 0))
            values = {}
            verdict = tk.StringVar(value="")
            for col, (key, name) in enumerate(FIELDS):
                tk.Label(grid, text=name, font=self.f_small, fg=MUTED, bg=CARD, anchor="w").grid(
                    row=0, column=col, sticky="w", padx=(0, 10))
                var = tk.StringVar(value="" if match[key] is None else str(match[key]))
                tk.Entry(grid, textvariable=var, width=7, font=self.f_body, fg=TEXT, bg=INSET, insertbackground=TEXT,
                         relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT).grid(
                    row=1, column=col, sticky="we", padx=(0, 10), ipady=3)
                var.trace_add("write", lambda *_a, v=verdict: v.set("不对"))  # 改了数字就是"不对"
                values[key] = var
            self.choice_row(box, [("对", "对"), ("不对（在上面改成正确的）", "不对")], verdict)
            judged[match["match_id"]] = (match, values, verdict)

        missed = tk.StringVar(value="")
        lobby_verdict, lobby_fix = tk.StringVar(value=""), tk.StringVar(value="")
        keep_all = tk.BooleanVar(value=False)
        box = self.card(area, pady=(10, 0))
        self.label(box, "这段时间还有 Evo 没记下的局吗？", bold=True)
        self.choice_row(box, [("没有", "没有"), ("有（在备注里写一下大概几点、第几名）", "有")], missed)
        lobby = record["大厅"][-1] if record["大厅"] else None
        if lobby is not None:
            self.label(box, f"大厅现在显示的段位和分数是「{rank_text(lobby)}」吗？", bold=True).pack_configure(pady=(12, 0))
            row = self.choice_row(box, [("对", "对"), ("不对，是", "不对")], lobby_verdict)
            tk.Entry(row, textvariable=lobby_fix, width=16, font=self.f_body, fg=TEXT, bg=INSET, insertbackground=TEXT,
                     relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT).pack(
                side="left", ipady=3)
            lobby_fix.trace_add("write", lambda *_a: lobby_verdict.set("不对"))
        tk.Checkbutton(box, text="这次的截图全部带上（开发者要样本时勾）", variable=keep_all, font=self.f_body, fg=TEXT,
                       bg=CARD, selectcolor=INSET, activebackground=CARD, activeforeground=TEXT, highlightthickness=0,
                       anchor="w").pack(fill="x", pady=(12, 0))

        def collect():
            out = {"局": {}, "漏记": missed.get() or None, "大厅": None, "全部带上": keep_all.get()}
            for match_id, (match, values, verdict) in judged.items():
                fixed = {}
                if verdict.get() == "不对":
                    for key, var in values.items():
                        text = var.get().strip()
                        if text != ("" if match[key] is None else str(match[key])):
                            fixed[key] = int(text) if text.lstrip("+-").isdigit() else text
                out["局"][match_id] = {"判断": verdict.get() or None, "正确的": fixed}
            if lobby is not None:
                out["大厅"] = {"Evo 读的": rank_text(lobby), "判断": lobby_verdict.get() or None,
                             "正确的": lobby_fix.get().strip()}
            return out

        return collect

    def season_answers(self, area):
        """赛季数据页：Evo 读到了几项、核对保存了几项（没核对的提醒先去 Evo 的"赛季"页核对）。"""
        frame = tk.Frame(area, bg=BG)
        frame.pack(fill="x")

        def fill():
            for child in frame.winfo_children():
                child.destroy()
            record = self.session.evo_record()
            rows = record["赛季"]
            if record["数据库"] is None:
                self.warn(frame, "没找到 Evo 的数据（这台电脑上没装 Evo，或者没打开过）。截图照样带上")
                return
            if not rows:
                self.warn(frame, "这段时间 Evo 一页数据页都没读到（可能没点“开始录入”，或者还认不出这种画面）。截图照样带上")
                return
            pending = sum(1 for r in rows if r.get("status") == "pending")
            box = self.card(frame)
            self.label(box, f"Evo 读到了 {len(rows)} 项：核对保存了 {len(rows) - pending} 项，还有 {pending} 项没核对",
                       bold=True)
            if pending:
                self.label(box, "最好先到 Evo 的“赛季”页核对、保存（改成游戏里显示的数），再点“重新看一下”。不核对也能打包",
                           muted=True)
                self.button(box, "重新看一下", fill).pack(anchor="w", pady=(8, 0))

        fill()

    def show_done(self, zip_path):
        self.clear(4)
        self.state = "done"
        self.zip_path = zip_path
        if zip_path is None:
            self.title(self.body, "打包没成功", f"截图都在这个文件夹里，可以自己压缩后发给开发者：\n{self.session.folder}")
        else:
            size = zip_path.stat().st_size / 2 ** 20
            self.title(self.body, "好了，谢谢！", "把这个压缩包发给 Evo 开发者就行（QQ 群或者网盘都可以）。")
            box = self.card(self.body, pady=(14, 0))
            self.label(box, zip_path.name, bold=True)
            self.label(box, f"{size:.0f} MB · 在 {zip_path.parent}", muted=True)
        bar = tk.Frame(self.body, bg=BG)
        bar.pack(fill="x", side="bottom")
        self.button(bar, "关闭", self.root.destroy).pack(side="right")
        self.button(bar, "再测一项", self.show_projects).pack(side="right", padx=(0, 8))
        if zip_path is not None:
            self.button(bar, "打开文件夹", lambda: self.open_folder(zip_path), primary=True).pack(side="left")
            self.open_folder(zip_path)

    # ---------- 刷新、关窗口 ----------

    def tick(self):
        while True:
            try:
                kind, value = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append_log(value)
            elif kind == "notice":
                self._show_notice(value)
            elif kind == "started" and self.state == "starting":
                self.state = "recording"
            elif kind == "ended":
                self.show_finish(value)
            elif kind == "packed":
                self.show_done(value)
        if self.state in ("starting", "recording", "stopping"):
            self._refresh_recording()
        self.root.after(REFRESH_MS, self.tick)

    def on_close(self):
        if self.state in ("starting", "recording"):
            if messagebox.askyesno("还在录", "还在录。现在结束，去打包吗？\n（选“否”接着录）", parent=self.root):
                self.end()
            return
        if self.state in ("stopping", "finishing"):
            folder = self.session.folder if self.session else ""
            if not messagebox.askyesno("还没打包", f"还没打包。现在关掉的话，截图留在这个文件夹里：\n{folder}\n\n确定关掉吗？",
                                       parent=self.root):
                return
            if self.stop is not None:
                self.stop.set()
        self.root.destroy()


def run(projects, profile, languages, weapons, save_profile, new_session, evo_running, version, icon=None):
    """打开窗口，关掉才返回。"""
    make_dpi_aware()
    root = tk.Tk()
    TesterApp(root, projects, profile, languages, weapons, save_profile, new_session, evo_running, version, icon)
    root.mainloop()


def icon_path():
    """窗口左上角的图标：Evo 的图标（打包时放进 exe，见 tools/tester.spec）。"""
    base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    path = base / "assets" / "icon" / "evo.png"
    return path if path.is_file() else None
