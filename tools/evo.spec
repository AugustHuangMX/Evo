# PyInstaller 打包配置。一般不直接用它，运行 python tools/build.py 即可（会先生成图标，打包后再放入许可证）。
# 打包方式：文件夹模式（onedir）——一个很小的 Evo.exe 启动器 + _internal 文件夹（依赖、配置、网页、识别模型）。
import os

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

ROOT = os.path.dirname(SPECPATH)  # 项目文件夹（本文件在 tools/ 里）
CODE = os.path.join(ROOT, "code")  # 程序代码
# 微信小程序（绑定、上传战绩：cloud_sync.py、panel_cloud.py、qr.py、_cloud.html）：这些文件只在开发者电脑上，不上传 GitHub
# （2026-10-24 用户决定）。小程序上线以前不打进安装包（0.4.0 起），Evo 找不到它们就不显示那一栏、不同步；上线时改成 True
WITH_CLOUD = False
CLOUD_MODULES = ["cloud_sync", "panel_cloud", "qr"]

datas = [
    (os.path.join(ROOT, "config"), "config"),                    # 识别规则
    (os.path.join(ROOT, "panel", "templates"), "panel/templates"),  # 面板网页
    (os.path.join(ROOT, "panel", "static"), "panel/static"),        # 面板样式、字体
    (os.path.join(ROOT, "assets"), "assets"),                    # 图标
    # 微软的 WebView2 安装程序（build.py 先下载、核对签名；电脑上没有 WebView2 时用，见 webview2.py）
    (os.path.join(ROOT, "build", "webview2", "MicrosoftEdgeWebview2Setup.exe"), "webview2"),
]
datas += collect_data_files("rapidocr")      # 识别模型（.onnx）和识别引擎的配置（.yaml）
datas += copy_metadata("pystray")            # pystray 的许可证信息（LGPL 要求）

a = Analysis(
    [os.path.join(CODE, "evo_app.py")],
    pathex=[CODE],
    datas=datas,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", *([] if WITH_CLOUD else CLOUD_MODULES)],  # 用不到的大模块
    # pystray 是 LGPL：以 .py 源文件形式放在 _internal/pystray 里（不压进程序内部），别人可以换成自己改过的版本
    module_collection_mode={"pystray": "py"},
)
# OpenCV 自带的视频读写组件（ffmpeg，29 MB）：Evo 只读写图片，用不到（2026-10-17 实测读图、存 PNG、缩放、比对都不会加载它）
a.binaries = [b for b in a.binaries if "opencv_videoio_ffmpeg" not in b[0]]
if not WITH_CLOUD:
    a.datas = [d for d in a.datas if os.path.basename(d[0]) != "_cloud.html"]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Evo",
    console=False,  # 不显示黑色终端窗口；运行记录写在数据文件夹的 logs\evo.log（数据文件夹在哪见 paths.py）
    icon=os.path.join(ROOT, "build", "icon", "evo.ico"),
    upx=False,  # 不压缩：压缩过的 exe 更容易被杀毒软件误报
)
coll = COLLECT(exe, a.binaries, a.datas, name="Evo", upx=False)
