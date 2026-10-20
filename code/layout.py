"""游戏界面在不同宽高比下摆在哪：把配置里的坐标（1920×1080 基准）换算成截图上的像素。所有裁小块的地方都经过这里。

2026-10-20 用 16:10 实测（DX12 全屏、启动项 -w 2560 -h 1600，见 docs/识别经验.md）：
- 比 16:9 窄的画面（16:10、4:3），游戏按宽度缩放界面：界面和 16:9 时一样宽，上下多出一截
- 多出来的高度怎么分，每块界面不一样，写在 config 里每个区域旁边的 "anchor"（不写就用整个文件的 "anchor"）：
  "top" 贴上边、"center" 居中、"bottom" 贴下边，或者 0–1 之间的数（多出来的高度里，这块界面上面空出几成）
- 16:9 时没有多出来的高度，anchor 不起作用，结果和以前完全一样。比 16:9 更宽的（如 21:9）还没校准过，照旧横竖各自拉伸
- 游戏的宽高比不一定等于截图的宽高比：DX12 全屏用拉伸分辨率时，游戏按 2560×1600 摆界面，再压扁成显示器的 2560×1440。
  所以分两步：先按游戏的宽高比摆好，再横竖各自拉伸到截图大小。游戏的宽高比用 set_game_aspect() 设（没设 = 和截图一样）
"""

ANCHORS = {"top": 0.0, "center": 0.5, "bottom": 1.0}
SAME_ASPECT = 0.01  # 宽高比相差不到这么多，算同一种（1366×768、1360×768 都算 16:9）

_game_aspect = None  # 游戏的宽高比（宽 / 高）；None = 和截图一样


def set_game_aspect(aspect):
    """设置游戏的宽高比（如 16 / 10）；None = 和截图一样。"""
    global _game_aspect
    _game_aspect = aspect


def game_aspect():
    return _game_aspect


def anchor_value(anchor):
    """"top" / "center" / "bottom" / 0–1 的数 → 0–1 的数；没写（None）当作贴上边。"""
    if anchor is None:
        return 0.0
    return ANCHORS[anchor] if isinstance(anchor, str) else float(anchor)


class Mapping:
    """一张截图上，一块界面（同一个 anchor）的换算：基准坐标 → 截图像素。
    shape：截图的 (高, 宽, ...)；base：基准分辨率 [宽, 高]；aspect：游戏的宽高比（None = 用 set_game_aspect 设的）。"""

    def __init__(self, shape, base, anchor=None, aspect=None):
        h, w = shape[:2]
        aspect = aspect or _game_aspect or w / h
        bw, bh = base
        self.dx = self.dy = 0.0
        canvas_w, canvas_h = bw, bh  # 游戏的画面有多大（基准像素）
        if aspect < bw / bh - SAME_ASPECT:  # 比 16:9 窄：按宽缩放，上下多出来
            canvas_h = bw / aspect
            self.dy = anchor_value(anchor) * (canvas_h - bh)
        self.sx, self.sy = w / canvas_w, h / canvas_h  # 基准像素 → 截图像素（横竖各自，压扁过的截图两个不一样）

    def box(self, box):
        """基准坐标 [x, y, 宽, 高] → 截图上的像素 (左, 上, 右, 下)。"""
        x, y, w, h = box
        return (round((x + self.dx) * self.sx), round((y + self.dy) * self.sy),
                round((x + self.dx + w) * self.sx), round((y + self.dy + h) * self.sy))


def cut(img, box, base, anchor=None, aspect=None):
    """按基准坐标从截图上裁一块（原分辨率，不缩放）。"""
    x0, y0, x1, y1 = Mapping(img.shape, base, anchor, aspect).box(box)
    return img[y0:y1, x0:x1]
