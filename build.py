"""打包成 Windows 程序：python build.py  →  dist/Evo/Evo.exe

1. 生成程序图标 build/icon/evo.ico（用 assets/icon/ 里的图标，裁掉四周透明空白；没有就用面板左上角的盾牌）
2. 用 PyInstaller 按 evo.spec 打包（文件夹模式：一个很小的 Evo.exe + _internal 文件夹）
3. 放入许可证：Evo 自己的 MIT 许可证；pystray 的 LGPL 许可证（以及它引用的 GPL 全文）
4. 打印打包结果的大小

需要先安装 PyInstaller（只在开发时用，不会装进程序里）：python -m pip install pyinstaller
"""

import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "build" / "icon" / "evo.ico"
DIST = ROOT / "dist" / "Evo"
ICON_SIZES = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]

LICENSE_NOTE = """Evo 自己的代码使用 MIT 许可证，见上一层的 LICENSE.txt。

Evo 使用了以下第三方库，它们各自的许可证随库一起放在 _internal 文件夹里（*.dist-info）：

- pystray（托盘图标）：GNU LGPL v3，许可证见本文件夹的 pystray-COPYING.LGPL 和 pystray-COPYING。
  按 LGPL 的要求，pystray 以 Python 源文件的形式放在 _internal/pystray 文件夹里，
  你可以把它换成自己修改过的版本；pystray 的源代码：https://github.com/moses-palmer/pystray
- 其他主要依赖：RapidOCR（Apache 2.0）、ONNX Runtime（MIT）、OpenCV（Apache 2.0）、NumPy（BSD）、
  Flask（BSD）、Pillow（MIT-CMU）、dxcam（MIT）、PP-OCR 模型（Apache 2.0）
- 面板字体 Chakra Petch：SIL Open Font License 1.1，见 _internal/panel/static/fonts/OFL.txt
"""


def make_icon():
    from icons import base_icon  # 和托盘图标同一个来源：assets/icon/ 里的图标（裁掉四周空白），没有就用盾牌

    img, source = base_icon(256)
    print(f"图标：{source}")
    ICON.parent.mkdir(parents=True, exist_ok=True)
    img.save(ICON, sizes=ICON_SIZES)


def add_licenses():
    shutil.copy2(ROOT / "LICENSE", DIST / "LICENSE.txt")
    folder = DIST / "licenses"
    folder.mkdir(exist_ok=True)
    for f in importlib.metadata.distribution("pystray").files:
        if f.name.upper().startswith("COPYING"):
            shutil.copy2(f.locate(), folder / f"pystray-{f.name}")
    (folder / "README.txt").write_text(LICENSE_NOTE, encoding="utf-8")


def main():
    make_icon()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "evo.spec"], cwd=ROOT, check=True)
    add_licenses()
    files = [p for p in DIST.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    exe = DIST / "Evo.exe"
    print(f"\n打包完成：{exe}（启动器 {exe.stat().st_size / 1024 ** 2:.1f} MB）")
    print(f"整个文件夹 {len(files)} 个文件，{size / 1024 ** 2:.0f} MB")


if __name__ == "__main__":
    main()
