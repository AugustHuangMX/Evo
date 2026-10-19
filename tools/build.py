"""打包成 Windows 程序：python tools/build.py  →  dist/Evo/Evo.exe

1. 生成程序图标 build/icon/evo.ico（用 assets/icon/ 里的图标，裁掉四周透明空白；没有就用面板左上角的盾牌）
2. 准备微软的 WebView2 安装程序（webview2.py 用它，电脑上没有 WebView2 时问用户要不要装）：
   build/webview2 里没有就从微软下载，每次都核对是微软签名的
3. 用 PyInstaller 按 evo.spec 打包（文件夹模式：一个很小的 Evo.exe + _internal 文件夹）
4. 放入许可证：Evo 自己的 MIT 许可证；pystray 的 LGPL 许可证（以及它引用的 GPL 全文）
5. 打印打包结果的大小

需要先安装 PyInstaller（只在开发时用，不会装进程序里）：python -m pip install pyinstaller
"""

import importlib.metadata
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
from webview2 import SETUP as WEBVIEW2_SETUP  # 用源码运行时在 build/webview2 里，evo.spec 从这里打包

ROOT = Path(__file__).resolve().parent.parent  # 项目文件夹（本文件在 tools/ 里）
ICON = ROOT / "build" / "icon" / "evo.ico"
DIST = ROOT / "dist" / "Evo"
ICON_SIZES = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
# 微软的 WebView2 常青版引导程序（Evergreen Bootstrapper），微软允许随软件分发
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

LICENSE_NOTE = """Evo 自己的代码使用 MIT 许可证，见上一层的 LICENSE.txt。

Evo 使用了以下第三方库，它们各自的许可证随库一起放在 _internal 文件夹里（*.dist-info）：

- pystray（托盘图标）：GNU LGPL v3，许可证见本文件夹的 pystray-COPYING.LGPL 和 pystray-COPYING。
  按 LGPL 的要求，pystray 以 Python 源文件的形式放在 _internal/pystray 文件夹里，
  你可以把它换成自己修改过的版本；pystray 的源代码：https://github.com/moses-palmer/pystray
- 其他主要依赖：RapidOCR（Apache 2.0）、ONNX Runtime（MIT）、OpenCV（Apache 2.0）、NumPy（BSD）、
  Flask（BSD）、Pillow（MIT-CMU）、dxcam（MIT）、PP-OCR 模型（Apache 2.0）
- 面板字体 Chakra Petch：SIL Open Font License 1.1，见 _internal/panel/static/fonts/OFL.txt
- _internal/webview2/MicrosoftEdgeWebview2Setup.exe：微软的 Microsoft Edge WebView2 安装程序，按微软的许可随 Evo 分发。
  只在电脑上没有 WebView2、你同意安装时才运行
"""


def make_icon():
    from icons import base_icon  # 和托盘图标同一个来源：assets/icon/ 里的图标（裁掉四周空白），没有就用盾牌

    img, source = base_icon(256)
    print(f"图标：{source}")
    ICON.parent.mkdir(parents=True, exist_ok=True)
    img.save(ICON, sizes=ICON_SIZES)


def get_webview2_setup():
    if not WEBVIEW2_SETUP.exists():
        print(f"下载 WebView2 安装程序：{WEBVIEW2_URL}")
        WEBVIEW2_SETUP.parent.mkdir(parents=True, exist_ok=True)
        part = WEBVIEW2_SETUP.with_suffix(".part")
        urllib.request.urlretrieve(WEBVIEW2_URL, part)
        part.replace(WEBVIEW2_SETUP)
    # 核对数字签名：必须有效、签名的是微软
    check = ("$s = Get-AuthenticodeSignature -LiteralPath $env:EVO_SIGNED_FILE; "
             "Write-Output $s.Status; Write-Output $s.SignerCertificate.Subject")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", check], capture_output=True, text=True,
                         env={**os.environ, "EVO_SIGNED_FILE": str(WEBVIEW2_SETUP)}, check=True).stdout.splitlines()
    status, subject = (out + ["", ""])[:2]
    if status.strip() != "Valid" or "O=Microsoft Corporation" not in subject:
        raise SystemExit(f"WebView2 安装程序的签名不对（{status.strip()}，{subject.strip()}），删掉 {WEBVIEW2_SETUP} 重新下载")
    print(f"WebView2 安装程序：{WEBVIEW2_SETUP.stat().st_size / 1024 ** 2:.1f} MB，微软签名有效")


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
    get_webview2_setup()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "tools/evo.spec"], cwd=ROOT, check=True)
    add_licenses()
    files = [p for p in DIST.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    exe = DIST / "Evo.exe"
    print(f"\n打包完成：{exe}（启动器 {exe.stat().st_size / 1024 ** 2:.1f} MB）")
    print(f"整个文件夹 {len(files)} 个文件，{size / 1024 ** 2:.0f} MB")


if __name__ == "__main__":
    main()
