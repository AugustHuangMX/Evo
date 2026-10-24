"""打包诊断工具：python tools/build_diag.py  →  dist/Evo诊断工具.exe（单个 exe，发给记不下战绩的玩家；见 code/evo_diag.py）

1. 没有程序图标就先生成（和 Evo 同一个，见 build.py）
2. 用 PyInstaller 按 tools/diag.spec 打包（单个 exe，不带识别引擎和 OpenCV）
3. 自检：真的运行打包好的 exe 截几秒（把 explorer.exe 当作游戏），压缩包里要有截图和诊断信息
4. 打印大小和 SHA-256（发群文件、官网时核对用）

和 Evo 的安装包（build.py）互不影响，可以单独打包、单独发。
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 项目文件夹（本文件在 tools/ 里）
SPEC = ROOT / "tools" / "diag.spec"
EXE = ROOT / "dist" / "Evo诊断工具.exe"
ICON = ROOT / "build" / "icon" / "evo.ico"


def self_check():
    """真的运行打包好的 exe 截几秒，返回 (通过没有, 说明)。"""
    with tempfile.TemporaryDirectory(prefix="evo-diag-check-") as tmp:
        result = subprocess.run([str(EXE), "--minutes", "0.12", "--game", "explorer.exe", "--out", tmp, "--no-wait"],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=180)
        zips = list(Path(tmp).glob("*.zip"))
        if result.returncode != 0 or len(zips) != 1:
            return False, f"退出码 {result.returncode}，压缩包 {len(zips)} 个\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}"
        with zipfile.ZipFile(zips[0]) as zf:
            names = zf.namelist()
            info = json.loads(zf.read("info.json"))
        frames = sum(n.startswith("frames/") for n in names)
        ok = frames > 0 and info["dxcam"]["截屏对象"] == "建好了" and isinstance(info["显示器"], list)
        return ok, f"截图 {frames} 张，截屏对象：{info['dxcam']['截屏对象']}，显示器 {len(info['显示器'])} 个"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if not ICON.exists():
        sys.path.insert(0, str(ROOT / "tools"))
        import build

        build.make_icon()
    subprocess.run([sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--distpath", str(ROOT / "dist"),
                    "--workpath", str(ROOT / "build" / "diag")], check=True)
    ok, text = self_check()
    print(f"\n自检{'通过' if ok else '没通过'}：{text}")
    size = EXE.stat().st_size / 2 ** 20
    digest = hashlib.sha256(EXE.read_bytes()).hexdigest()
    print(f"打包好了：{EXE}（{size:.0f} MB）\nSHA-256：{digest}")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
