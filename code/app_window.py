"""Evo 的软件窗口：用 Windows 自带的 WebView2（Edge 用的同一个组件）显示面板，深色自定义标题栏。

托盘程序在单独的进程里运行它（见 evo_app.py 的 PanelServer）：关掉窗口，这个进程就退出，内存全部还给系统。
窗口去掉了 Windows 自带的标题栏和边框（frameless），所以：
- 标题栏是面板网页顶部的一条（panel/templates/base.html，只在 Evo 窗口里显示）
- 在标题栏按下鼠标时，告诉 Windows"用户按住了标题栏"，拖动交给 Windows 处理，手感和普通窗口一样；
  四边和四角的细条同理，交给 Windows 改大小
- 最小化 / 最大化 / 关闭是网页里的按钮，通过 window.pywebview.api 调用下面的 WindowApi

单独试：python code/app_window.py http://127.0.0.1:5000/   （先另外启动面板：python code/panel_app.py --no-browser）
"""

import ctypes
import sys
import threading
from ctypes import wintypes

import webview

import storage
from paths import DATA_DIR

USER_AGENT = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              f"EvoApp/{storage.APP_VERSION}")  # 面板网页看到 EvoApp，就显示自定义标题栏
BACKGROUND = "#0D0F13"  # 网页加载出来之前的底色（和黑夜主题一致，避免白屏闪一下）
WM_NCLBUTTONDOWN = 0x00A1
HIT = {"caption": 2, "left": 10, "right": 11, "top": 12, "topleft": 13, "topright": 14,
       "bottom": 15, "bottomleft": 16, "bottomright": 17}

_user32 = ctypes.WinDLL("user32")
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = ctypes.c_ssize_t
_dwmapi = ctypes.WinDLL("dwmapi")


class WindowApi:
    """网页里的标题栏按钮、拖动、改大小，通过 window.pywebview.api.xxx() 调用这里。
    （下划线开头的属性和方法不会暴露给网页。）"""

    def __init__(self):
        self._window = None

    def _form(self):
        return self._window.native  # WinForms 的窗口对象

    def _on_ui(self, func):
        """在窗口自己的线程里执行（WinForms 要求窗口操作都在这个线程里做）。"""
        from System import Action

        self._form().Invoke(Action(func))

    def _maximized(self):
        from System.Windows.Forms import FormWindowState

        return self._form().WindowState == FormWindowState.Maximized

    def _nc_button_down(self, where):
        """告诉 Windows：用户在标题栏 / 某条边上按下了鼠标。接下来的拖动由 Windows 接管。"""
        form = self._form()

        def go():
            _user32.ReleaseCapture()
            _user32.SendMessageW(form.Handle.ToInt64(), WM_NCLBUTTONDOWN, HIT[where], 0)

        self._on_ui(go)

    def begin_drag(self):
        if not self._maximized():  # 最大化时不能拖
            self._nc_button_down("caption")

    def begin_resize(self, edge):
        if edge in HIT and edge != "caption" and not self._maximized():
            self._nc_button_down(edge)

    def toggle_maximize(self):
        def go():
            from System.Drawing import Rectangle
            from System.Windows.Forms import FormWindowState, Screen

            form = self._form()
            if form.WindowState == FormWindowState.Maximized:
                form.WindowState = FormWindowState.Normal
            else:
                # 没有边框的窗口最大化时会盖住任务栏：限定在屏幕的工作区里（坐标相对于这块屏幕）
                screen = Screen.FromHandle(form.Handle)
                area, bounds = screen.WorkingArea, screen.Bounds
                form.MaximizedBounds = Rectangle(area.X - bounds.X, area.Y - bounds.Y, area.Width, area.Height)
                form.WindowState = FormWindowState.Maximized

        self._on_ui(go)
        return self._maximized()

    def is_maximized(self):
        return self._maximized()

    def minimize(self):
        self._window.minimize()

    def close(self):
        self._window.destroy()

    def allow_foreground(self):
        """点"安装并重新启动"时调用（base.html）：允许接下来新打开的 Evo 把窗口放到最前、拿到焦点。
        Windows 只让刚被点过的程序这样做，新 Evo 是后台的安装程序打开的，没有这个许可窗口会被别的程序挡住。"""
        _user32.AllowSetForegroundWindow(-1)  # ASFW_ANY：到用户下一次按键、点鼠标为止

    def _bring_to_front(self):
        def go():
            form = self._form()
            from System.Windows.Forms import FormWindowState

            if form.WindowState == FormWindowState.Minimized:
                form.WindowState = FormWindowState.Normal
            form.Show()
            form.TopMost = True  # 先置顶再取消，才能从别的程序后面跳到最前
            form.TopMost = False
            form.Activate()

        self._on_ui(go)


def _style_window(window):
    """Windows 11：窗口四角改成圆角（没有边框的窗口默认是直角）。旧版 Windows 不支持就算了。"""
    hwnd = wintypes.HWND(window.native.Handle.ToInt64())
    round_corners = ctypes.c_int(2)  # DWMWCP_ROUND
    _dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(round_corners), ctypes.sizeof(round_corners))


def _watch_focus(api, focus):
    """托盘程序再点"打开面板"时会设置 focus：把已经打开的窗口调到最前面。"""
    while True:
        focus.wait()
        focus.clear()
        try:
            api._bring_to_front()
        except Exception:  # noqa: BLE001 窗口正在关闭等情况，忽略
            return


def run(url, focus=None):
    """打开 Evo 窗口，直到窗口关闭才返回。focus：托盘程序用来"把窗口调到最前"的信号（multiprocessing.Event）。"""
    # 再确认一次 pywebview 用的是 WebView2：它找不到 WebView2 时会悄悄改用 IE 内核，面板会乱成一堆文字（见 webview2.py）。
    # 不是的话报错，托盘程序改用浏览器打开
    from webview.platforms import winforms

    if winforms.renderer != "edgechromium":
        raise RuntimeError(f"没有可用的 WebView2，pywebview 要改用 {winforms.renderer}")
    webview.settings["ALLOW_DOWNLOADS"] = True  # 导出 CSV
    api = WindowApi()
    window = webview.create_window(
        "Evo", url, js_api=api, width=1280, height=860, min_size=(960, 640),
        frameless=True, easy_drag=False, background_color=BACKGROUND,
    )
    api._window = window

    def shown():
        _style_window(window)
        # 打开时调到最前：一键更新后新版本自己打开，不调的话窗口会被刚才在用的程序挡住，像是没打开
        api._bring_to_front()

    window.events.shown += shown
    if focus is not None:
        threading.Thread(target=_watch_focus, args=(api, focus), name="focus", daemon=True).start()
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(DATA_DIR / "webview"),
                  user_agent=USER_AGENT)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000/")
