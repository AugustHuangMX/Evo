"""做发布用的压缩包：python release.py  →  dist/Evo-<版本号>-win64.zip 和同名的 .sha256 文件

按顺序做下面几件事，任何一步出问题就停下：
1. 检查代码都已提交：压缩包要对应一个确定的提交，没提交的改动不会被悄悄打进去
2. 跑全部测试（tests/run_all.py）
3. 打包（build.py）
4. 用打包好的 Evo.exe 自检一张总结页截图，确认识别模型、参照图都打进去了（截图只在开发者电脑上，没有就跳过）
5. 压缩：最外层是一个 Evo 文件夹，附上《使用说明（先看这个）.txt》（原文在 release/使用说明.txt）
6. 算 SHA-256：写在官网上，下载的人可以核对文件有没有被改过

版本号取自 storage.py 的 APP_VERSION。压缩包里没有 data_location.txt，所以别人的数据会放在他们自己的 %LOCALAPPDATA%\\Evo。
只是想试一下流程、还有没提交的改动时：python release.py --allow-dirty（文件名会带上 -dirty，不要发布这种）
"""

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

from storage import APP_VERSION

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
APP = DIST / "Evo"
README = ROOT / "release" / "使用说明.txt"
README_NAME = "使用说明（先看这个）.txt"
CHECK_IMAGE = ROOT / "captures" / "live" / "20261016_211452" / "05_222749.png"
CHECK_EXPECT = "总结页 第 8 名，击杀/助攻/击倒 3/0/3，伤害 688"  # 这张截图的正确结果


class Stop(Exception):
    """某一步出了问题，停下来不往下做。"""


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout.strip()


def step(n, text):
    print(f"\n[{n}/6] {text}", flush=True)


def check_clean(allow_dirty):
    changes = git("status", "--porcelain")
    if not changes:
        print("    代码都已提交")
        return False
    if not allow_dirty:
        raise Stop("还有没提交的改动（下面这些），先提交再做压缩包；只是试流程就加 --allow-dirty：\n" + changes)
    print("    ⚠ 有没提交的改动，这次的压缩包只能自己试，不要发布")
    return True


def run_tests():
    result = subprocess.run([sys.executable, "tests/run_all.py"], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    output = result.stdout + result.stderr
    if result.returncode != 0:
        print(output)
        raise Stop("测试没有全部通过")
    print(output[output.find("===== 结果"):].rstrip())


def self_check():
    """用打包好的 Evo.exe 识别一张总结页截图。数据和日志写到临时文件夹，不碰任何真实数据。"""
    if not CHECK_IMAGE.is_file():
        print(f"    ⏭ 跳过：找不到截图 {CHECK_IMAGE}")
        return
    with tempfile.TemporaryDirectory(prefix="evo-release-") as tmp:
        subprocess.run([str(APP / "Evo.exe"), "--check-image", str(CHECK_IMAGE)], cwd=APP, timeout=180,
                       env={**os.environ, "EVO_DATA_DIR": tmp}, check=True)
        log = (Path(tmp) / "logs" / "evo.log").read_text(encoding="utf-8")
    if CHECK_EXPECT not in log:
        print(log)
        raise Stop("打包好的 Evo.exe 自检结果不对")
    print(f"    ✅ {CHECK_EXPECT}")


def make_zip(target, readme_text):
    files = sorted(p for p in APP.rglob("*") if p.is_file())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in files:
            z.write(path, Path("Evo") / path.relative_to(APP))
        # 开头带 BOM、Windows 换行：用记事本打开一定不乱码
        z.writestr(f"Evo/{README_NAME}", ("﻿" + readme_text.replace("\r\n", "\n").replace("\n", "\r\n")).encode("utf-8"))
    return len(files) + 1


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="做发布用的压缩包（打包、自检、压缩、算 SHA-256）")
    parser.add_argument("--allow-dirty", action="store_true", help="有没提交的改动也继续（只用来试流程，不要发布）")
    args = parser.parse_args()

    try:
        step(1, "检查代码是否都已提交")
        dirty = check_clean(args.allow_dirty)
        commit = git("rev-parse", "--short", "HEAD") + ("+改动" if dirty else "")

        step(2, "跑全部测试")
        run_tests()

        step(3, "打包（build.py）")
        subprocess.run([sys.executable, "build.py"], cwd=ROOT, check=True)
        if (APP / "data_location.txt").exists():
            raise Stop("dist/Evo 里有 data_location.txt，别人的数据会被指到你电脑上的文件夹，不能发布")

        step(4, "用打包好的 Evo.exe 自检")
        self_check()

        step(5, "压缩")
        name = f"Evo-{APP_VERSION}{'-dirty' if dirty else ''}-win64.zip"
        target = DIST / name
        readme = README.read_text(encoding="utf-8").format(version=APP_VERSION, date=f"{date.today():%Y-%m-%d}",
                                                           commit=commit)
        count = make_zip(target, readme)
        size = target.stat().st_size
        print(f"    {target}（{count} 个文件，{size / 2**20:.0f} MB）")

        step(6, "算 SHA-256")
        digest = sha256(target)
        (DIST / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
        print(f"    {digest}")
    except Stop as e:
        print(f"\n❌ 停下了：{e}")
        return 1
    except subprocess.CalledProcessError as e:
        print(f"\n❌ 停下了：{' '.join(map(str, e.cmd))} 出错（返回码 {e.returncode}）")
        return 1

    print(f"\n完成：{name}，{size / 2**20:.0f} MB，版本 {APP_VERSION}，代码 {commit}")
    print(f"SHA-256：{digest}")
    if dirty:
        print("⚠ 这次有没提交的改动，只能自己试，不要发布")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
