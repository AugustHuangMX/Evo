# 诊断工具（code/evo_diag.py）的 PyInstaller 打包配置。一般不直接用它，运行 python tools/build_diag.py 即可。
# 打包方式：单个 exe（onefile，2026-10-24 用户决定：玩家下载就能双击），命令行窗口（console）。和 Evo 的安装包无关。
import os

from PyInstaller.utils.hooks import copy_metadata

ROOT = os.path.dirname(SPECPATH)  # 项目文件夹（本文件在 tools/ 里）
CODE = os.path.join(ROOT, "code")  # 程序代码
ICON = os.path.join(ROOT, "build", "icon", "evo.ico")  # 和 Evo 同一个图标（build_diag.py 先生成）

datas = []
for package in ("dxcam", "mss", "pillow", "numpy", "comtypes"):
    datas += copy_metadata(package)  # 各个库的许可证信息

a = Analysis(
    [os.path.join(CODE, "evo_diag.py")],
    pathex=[CODE],
    datas=datas,
    # dxcam 的 numpy 截屏组件是动态加载的，PyInstaller 自己找不到；有了它就不用 OpenCV
    hiddenimports=["dxcam.processor._numpy_kernels"],
    # 用不到的大模块。paths、app_settings：诊断工具绝不能加载（paths 一 import 就会在程序旁边建数据文件夹，见 evo_diag.py）
    excludes=["cv2", "rapidocr", "onnxruntime", "flask", "webview", "pystray", "tkinter", "matplotlib", "IPython",
              "pytest", "paths", "app_settings"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Evo诊断工具",
    console=True,  # 命令行窗口：写着怎么用、截了多少，按回车结束
    icon=ICON if os.path.isfile(ICON) else None,
    upx=False,  # 不压缩：压缩过的 exe 更容易被杀毒软件误报
)
