"""代码地图（docs/代码地图.md，python tools/code_map.py 生成）：是不是最新的、每个文件都分了组、都有开头说明。不用截图。

加、删、改名文件，或者改了文件开头说明的第一句以后没重新生成，这里就不通过：运行 python tools/code_map.py。
"""

import subprocess
import sys

from fake_game import ROOT, report


def main():
    print("代码地图：docs/代码地图.md 是最新的")
    try:
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "code_map.py"), "--check"], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  ⏭ 跳过：运行不了 tools/code_map.py（{e}）")
        return None
    if result.returncode == 2:  # 没有 git（比如从 GitHub 下载的压缩包）
        print(f"  ⏭ 跳过：{result.stdout.strip()}")
        return None
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr[-2000:])
    return report({"地图是最新的，每个文件都分了组、都有开头说明（不是的话运行 python tools/code_map.py）":
                   result.returncode == 0})


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
