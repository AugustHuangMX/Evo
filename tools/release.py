"""做发布用的压缩包：python tools/release.py  →  dist/Evo-<版本号>-win64.zip 和同名的 .sha256 文件

按顺序做下面几件事，任何一步出问题就停下：
1. 检查代码都已提交：压缩包要对应一个确定的提交，没提交的改动不会被悄悄打进去
2. 跑全部测试（tests/run_all.py）
3. 打包（build.py）
4. 用打包好的 Evo.exe 自检一张总结页截图，确认识别模型、参照图都打进去了（截图只在开发者电脑上，没有就跳过）
5. 压缩：最外层是一个 Evo 文件夹，附上《使用说明（先看这个）.txt》（原文在 release/使用说明.txt）
6. 算 SHA-256：写在官网上，下载的人可以核对文件有没有被改过
7. 做补丁包（增量更新，2026-10-24 用户决定，以后每次发布都这样；格式见 code/update_patch.py）：给 dist 里最近
   PATCH_FROM 个旧版本（不早于 DELTA_SINCE：更早的 Evo 不会用补丁包）各做一个，只放变了的文件
8. 写 dist/latest.json：Evo 检查新版本时读的就是它（update_check.py）。更新内容取自 release/更新内容.txt；
   还写上压缩包和补丁包的地址、SHA-256 和大小，Evo 里的一键更新（updater.py）按它下载、核对；
   以及国内备用线路（腾讯云 COS 上的同一个文件，update_check.COS_BASE）

版本号取自 storage.py 的 APP_VERSION。压缩包里没有 data_location.txt，所以别人的数据会放在他们自己电脑上程序旁边的
Evo Tracker Data（规则见 paths.py）。
只是想试一下流程、还有没提交的改动时：python tools/release.py --allow-dirty（文件名会带上 -dirty，不要发布这种；也不写 latest.json）

做好以后用 python tools/publish.py 上传和核对（压缩包、官网、最后 latest.json，见 publish.py 开头）。
它按下面的顺序做——上传到 R2（dl.evotracker.cn）和腾讯云 COS：先传压缩包和补丁包，确认能下载、SHA-256 对得上，
最后再传 latest.json——latest.json 一换，所有开着自动检查的 Evo 就会提醒更新，所以压缩包必须先到位。
latest.json 只让 CDN 缓存 5 分钟（max-age=300），换了以后很快就能被查到
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
import update_check
import update_patch
from storage import APP_VERSION

ROOT = Path(__file__).resolve().parent.parent  # 项目文件夹（本文件在 tools/ 里）
DIST = ROOT / "dist"
APP = DIST / "Evo"
README = ROOT / "release" / "使用说明.txt"
README_NAME = "使用说明（先看这个）.txt"
NOTES = ROOT / "release" / "更新内容.txt"
DOWNLOAD_PAGE = "https://evotracker.cn/#download"
DOWNLOAD_BASE = "https://dl.evotracker.cn"  # 压缩包传到 R2 后的地址（publish.py 也是这个）
DELTA_SINCE = "0.4.0"  # 从这个版本起 Evo 会用补丁包，更早的版本不做补丁包（它们只会下完整的）
PATCH_FROM = 3  # 给最近几个旧版本做补丁包（更早的下完整的安装包）


def mirror_urls(name):
    """国内备用线路：腾讯云 COS 上的同一个文件（2026-10-24 用户决定）。还没设存储桶时没有。"""
    return [f"{update_check.COS_BASE}/{name}"] if update_check.COS_BASE else []


CHECK_IMAGE = ROOT / "captures" / "live" / "20261016_211452" / "05_222749.png"
CHECK_EXPECT = "总结页 第 8 名，击杀/助攻/击倒 3/0/3，伤害 688"  # 这张截图的正确结果


class Stop(Exception):
    """某一步出了问题，停下来不往下做。"""


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout.strip()


def step(n, text):
    print(f"\n[{n}/8] {text}", flush=True)


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


def version_tuple(text):
    return tuple(int(x) for x in text.split("."))


def old_packages():
    """dist 里要做补丁包的旧版本：比现在的旧、不早于 DELTA_SINCE 的最近 PATCH_FROM 个。返回 [(版本号, 压缩包)]。
    压缩包要和打包时记下的 SHA-256（.sha256 文件）一样：被改过就不用它（那个版本的人照样能下完整的安装包）。"""
    found = []
    for path in DIST.glob("Evo-*-win64.zip"):
        m = re.fullmatch(r"Evo-(\d+\.\d+\.\d+)-win64\.zip", path.name)
        if not m or not version_tuple(DELTA_SINCE) <= version_tuple(m.group(1)) < version_tuple(APP_VERSION):
            continue
        recorded = DIST / f"{path.name}.sha256"
        if not recorded.is_file() or recorded.read_text(encoding="utf-8").split()[0] != sha256(path):
            print(f"    ⚠ 跳过 {path.name}：和打包时记下的 SHA-256 不一样（或者没记），{m.group(1)} 的人会下完整的安装包")
            continue
        found.append((m.group(1), path))
    return sorted(found, key=lambda item: version_tuple(item[0]))[-PATCH_FROM:]


def make_patches(target):
    """给最近几个旧版本各做一个补丁包。返回 {旧版本号: (补丁包文件名, SHA-256, 大小)}。"""
    patches = {}
    for version, path in old_packages():
        name = update_patch.patch_name(APP_VERSION, version)
        took, total = update_patch.make(path, target, DIST / name, version, APP_VERSION)
        size = (DIST / name).stat().st_size
        patches[version] = (name, sha256(DIST / name), size)
        print(f"    {name}：{total} 个文件里变了 {took} 个，{size / 2**20:.1f} MB")
    if not patches:
        print(f"    没有要做补丁包的旧版本（dist 里没有 {DELTA_SINCE} 到 {APP_VERSION} 之间的压缩包）：大家都下完整的安装包")
    return patches


def write_latest(today, name, digest, size, patches):
    """写 dist/latest.json（格式见 update_check.py）。更新内容：release/更新内容.txt 里每行一条，# 开头的行不算。
    download 是一键更新用的（updater.py）：Evo 从这个地址下载压缩包，核对 SHA-256 和大小；太慢或连不上时改用 mirrors；
    patches 是补丁包（从这些版本升上来只下载变了的文件）。"""
    notes = [line.strip() for line in NOTES.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.strip().startswith("#")] if NOTES.is_file() else []
    if len(notes) > 10 or any(len(n) > 200 for n in notes):
        raise Stop(f"{NOTES.name} 最多 10 条、每条不超过 200 字（Evo 只显示这么多）")
    latest = {"version": APP_VERSION, "date": today, "page": DOWNLOAD_PAGE, "notes": notes,
              "download": {"url": f"{DOWNLOAD_BASE}/{name}", "sha256": digest, "size": size,
                           "mirrors": mirror_urls(name),
                           "patches": {version: {"url": f"{DOWNLOAD_BASE}/{patch}", "sha256": patch_digest,
                                                 "size": patch_size, "mirrors": mirror_urls(patch)}
                                       for version, (patch, patch_digest, patch_size) in patches.items()}}}
    target = DIST / "latest.json"
    target.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"    {target}：版本 {APP_VERSION}，{len(notes)} 条更新内容")
    for note in notes:
        print(f"      · {note}")


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
        subprocess.run([sys.executable, "tools/build.py"], cwd=ROOT, check=True)
        if (APP / "data_location.txt").exists():
            raise Stop("dist/Evo 里有 data_location.txt，别人的数据会被指到你电脑上的文件夹，不能发布")

        step(4, "用打包好的 Evo.exe 自检")
        self_check()

        step(5, "压缩")
        name = f"Evo-{APP_VERSION}{'-dirty' if dirty else ''}-win64.zip"
        target = DIST / name
        today = f"{date.today():%Y-%m-%d}"
        readme = README.read_text(encoding="utf-8").format(version=APP_VERSION, date=today, commit=commit)
        count = make_zip(target, readme)
        size = target.stat().st_size
        print(f"    {target}（{count} 个文件，{size / 2**20:.0f} MB）")

        step(6, "算 SHA-256")
        digest = sha256(target)
        (DIST / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
        print(f"    {digest}")

        step(7, "做补丁包（一键更新只下载变了的文件）")
        if dirty:
            print("    ⏭ 跳过：有没提交的改动，这次的压缩包不能发布")
        else:
            patches = make_patches(target)

        step(8, "写版本号文件 latest.json")
        if dirty:
            print("    ⏭ 跳过：有没提交的改动，这次的压缩包不能发布")
        else:
            write_latest(today, name, digest, size, patches)
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
    else:
        print("\n上传：python tools/publish.py（先传压缩包和官网、逐项核对；最后 python tools/publish.py --live 传 latest.json）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
