"""文字识别（OCR）的公用部分：创建识别引擎、读配置和截图、裁出小块、读一块里的文字。

总结页（summary_page.py）、经验值页（xp_page.py）、开局画面（match_start.py）的识别都用这里。
只裁出配置文件里指定的小块去识别，不识别整张图，所以更快、更省内存。
全程不联网：直接指定安装包里自带的模型文件，RapidOCR 的下载代码不会被执行。
"""

import gc
import time
from pathlib import Path

import cv2
import numpy as np
import rapidocr
from rapidocr import RapidOCR

import game_lang
from layout import cut

MODEL_DIR = Path(rapidocr.__file__).parent / "models"

# 小块只有一两百像素宽：长边超过 960 才缩小，否则保持原样
# （默认规则是"短边小于 736 就放大到 736"，会把小块放大十几倍，又慢又占内存）
SMALL_CROP_PARAMS = {
    "Det.limit_type": "max",
    "Det.limit_side_len": 960,
    # 小块里没有字时，RapidOCR 会打印一条警告；实战采集时每秒都会刷屏，所以只显示错误
    "Global.log_level": "error",
}


def create_engine(extra_params=None):
    """创建 OCR 引擎。extra_params 可以追加或覆盖设置，例如识别小块时用 SMALL_CROP_PARAMS。"""
    params = {
        # 直接指定自带模型，不走"找不到就下载"的逻辑
        "Det.model_path": str(MODEL_DIR / "PP-OCRv6_det_small.onnx"),
        "Rec.model_path": str(MODEL_DIR / "PP-OCRv6_rec_small.onnx"),
        "Cls.model_path": str(MODEL_DIR / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
        # 游戏里的字不会倒过来，关掉方向判断，省时间和内存
        "Global.use_cls": False,
        "Global.log_level": "warning",
        # 只用 1 个 CPU 线程，尽量不和游戏抢资源
        "EngineConfig.onnxruntime.intra_op_num_threads": 1,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    }
    params.update(extra_params or {})
    return RapidOCR(params=params)


class LazyEngine:
    """用到时才创建识别引擎，闲置一段时间后释放（release_if_idle），把模型占的内存还给系统。
    打游戏的大部分时间用不到识别（screens.py 的外观检查就排除了），不必一直占着约 40 MB。
    用法和 create_engine() 返回的引擎一样：engine(图片)。
    clock：取当前时间的函数（秒），测试时可以换成假时钟。"""

    def __init__(self, params=None, idle_seconds=120, clock=time.monotonic, log=None):
        self.params, self.idle_seconds, self.clock = params, idle_seconds, clock
        self.log = log or (lambda message: None)
        self._engine, self._last_used = None, 0.0
        self.loads = self.calls = 0  # 加载过几次模型、识别过几次（统计用）
        self.busy = 0.0  # 识别一共用了几秒（真实时间，统计用：笔记本上可能比台式机慢 10 倍以上）

    def __call__(self, img, **kwargs):
        if self._engine is None:
            start = time.perf_counter()
            self._engine = create_engine(self.params)
            self.loads += 1
            self.log(f"加载识别模型（用时 {(time.perf_counter() - start) * 1000:.0f} 毫秒）")
        self.calls += 1
        self._last_used = self.clock()
        start = time.perf_counter()
        try:
            return self._engine(img, **kwargs)
        finally:
            self.busy += time.perf_counter() - start

    def release_if_idle(self):
        """闲置超过 idle_seconds 秒就释放模型。"""
        if self._engine is not None and self.clock() - self._last_used > self.idle_seconds:
            self._engine = None
            gc.collect()
            self.log(f"识别模型闲置了 {self.idle_seconds} 秒，已释放")


def load_config(name, lang=None):
    """读 config/ 里的一个识别规则文件，如 load_config("summary.json")。
    lang：游戏语言（如 "en"），叠上这种语言和简体不一样的地方（见 game_lang.py）；None 是简体。"""
    return game_lang.load(name, lang)


def load_image(path):
    """读图片，返回 BGR 格式。用 imdecode 而不是 imread，这样路径里有中文也能读。"""
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"读不了这张图片: {path}")
    return img


def crop(img, roi, cfg, anchor=None):
    """按基准分辨率下的坐标裁出一块并放大。截图分辨率、宽高比不同时怎么换算坐标见 layout.py；
    anchor：这块界面贴哪条边（没给就用 cfg 里的 "anchor"）。"""
    base, upscale = cfg["base_resolution"], cfg["upscale"]
    piece = cut(img, roi, base, cfg.get("anchor") if anchor is None else anchor)
    _, _, rw, rh = roi
    # 统一放大到"基准尺寸 × upscale"，不同分辨率的截图得到同样大小的小块
    piece = cv2.resize(piece, (rw * upscale, rh * upscale), interpolation=cv2.INTER_CUBIC)
    if cfg.get("grayscale"):
        # 转灰度：实验发现彩色（橙色）数字容易把斜杠读成 1，灰度下读得更准
        piece = cv2.cvtColor(cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return piece


def read_lines(engine, img, roi, cfg, anchor=None):
    """识别截图里的一小块，返回读到的每一行 [(文字, 置信度), ...]；没读到返回空列表。"""
    result = engine(crop(img, roi, cfg, anchor))
    return list(zip(result.txts or (), result.scores or ()))
