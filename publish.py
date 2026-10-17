"""发布新版本。先改 storage.py 的 APP_VERSION 和 release/更新内容.txt 并提交，然后：

    python publish.py --dry-run   试一遍：准备压缩包、预览官网要改的地方，不上传任何东西
    python publish.py             第一段：上传压缩包和官网，逐项核对
    python publish.py --live      第二段：上传 latest.json，开着"自动检查新版本"的 Evo 开始提醒更新

为什么分两段：latest.json 一换，所有开着自动检查的 Evo 都会提醒更新，收不回来。
所以先把压缩包、官网都传好、核对过，人也看过了，再做最后一步。

第一段：
  1. 准备压缩包：dist 里已经有当前提交打的压缩包就直接用，否则运行 release.py
     （检查提交、测试、打包、自检、压缩、算 SHA-256、写 latest.json）
  2. 上传压缩包到 R2 存储桶（dl.evotracker.cn）；线上已经是同一个文件就跳过
  3. 从 dl.evotracker.cn 下载回来，核对 SHA-256
  4. 改官网 site/index.html：下载链接、版本号、大小、SHA-256
  5. 上传官网（Cloudflare Pages）；线上已经是新版本就跳过
  6. 打开 evotracker.cn，确认下载链接和 SHA-256 已经是新的
第二段（--live）：
  7. 再核对一次线上的压缩包（比 MD5，不用重新下载）和官网
  8. 上传 latest.json（CDN 只缓存 5 分钟）
  9. 下载回来，确认和本机的一样

上传用 Wrangler（Cloudflare 的命令行工具，只装在开发电脑上，第一次用要 wrangler login），不用打开 Cloudflare 网站。
任何一步出问题就停下；改好后重新运行，已经做完的步骤会自动跳过。已经发布的版本不能再覆盖，要发就先改版本号。
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import release
from release import DIST, README_NAME, Stop, git, sha256
from storage import APP_VERSION

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "site" / "index.html"
BUCKET = "evotracker"         # R2 存储桶，公开地址是 dl.evotracker.cn
PAGES_PROJECT = "evotracker"  # Cloudflare Pages 项目，网址是 evotracker.cn
DL = "https://dl.evotracker.cn"
SITE_URL = "https://evotracker.cn/"
ZIP_NAME = f"Evo-{APP_VERSION}-win64.zip"
ZIP = DIST / ZIP_NAME
ZIP_URL = f"{DL}/{ZIP_NAME}"
LATEST = DIST / "latest.json"
LATEST_URL = f"{DL}/latest.json"


def step(n, text):
    print(f"\n[{n}/9] {text}", flush=True)


def version_tuple(text):
    """"0.10.2" → (0, 10, 2)，用来比较新旧。"""
    return tuple(int(x) for x in str(text).split("."))


def md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def wrangler(*args):
    exe = shutil.which("wrangler")
    if not exe:
        raise Stop("找不到 wrangler：先装 Node.js 和 Wrangler（npm install -g wrangler），再运行 wrangler login")
    subprocess.run([exe, *args], cwd=ROOT, check=True)


# ---------- 读线上的文件（地址后面加时间参数，绕过 CDN 缓存，读到的一定是刚传上去的） ----------

def open_url(url, method="GET"):
    request = urllib.request.Request(f"{url}?t={int(time.time())}", method=method,
                                     headers={"User-Agent": f"Evo-publish/{APP_VERSION}", "Cache-Control": "no-cache"})
    return urllib.request.urlopen(request, timeout=60)


def remote_bytes(url):
    """下载一个小文件；不存在返回 None。"""
    try:
        with open_url(url) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def remote_info(url):
    """线上有这个文件就返回 (大小, MD5)，没有返回 None。R2 的 ETag 就是文件的 MD5（一次传完的文件）。"""
    try:
        with open_url(url, "HEAD") as r:
            return int(r.headers.get("Content-Length", -1)), r.headers.get("ETag", "").strip('"')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def remote_sha256(url):
    digest = hashlib.sha256()
    with open_url(url) as r:
        for block in iter(lambda: r.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def online_version():
    data = remote_bytes(LATEST_URL)
    return json.loads(data.decode("utf-8")).get("version") if data else None


def site_is_new(digest):
    """线上官网的下载链接和 SHA-256 是不是已经是这个压缩包的。"""
    html = remote_bytes(SITE_URL)
    return html is not None and ZIP_URL in html.decode("utf-8") and digest in html.decode("utf-8")


# ---------- 第一段 ----------

def zip_commit(path):
    """压缩包是哪次提交打的：读使用说明第一行的"代码 xxx"（release.py 写的）；有没提交的改动时返回 None。"""
    with zipfile.ZipFile(path) as z:
        first = z.read(f"Evo/{README_NAME}").decode("utf-8-sig").splitlines()[0]
    m = re.search(r"代码 ([0-9a-f]+)(\+改动)?", first)
    return m.group(1) if m and not m.group(2) else None


def ready(head):
    """dist 里的压缩包、SHA-256、latest.json 都是当前提交、当前版本的吗？"""
    if not (ZIP.is_file() and LATEST.is_file() and (DIST / f"{ZIP_NAME}.sha256").is_file()):
        return False
    commit = zip_commit(ZIP)
    same_commit = commit is not None and (head.startswith(commit) or commit.startswith(head))
    return same_commit and json.loads(LATEST.read_text(encoding="utf-8")).get("version") == APP_VERSION


def prepare():
    """确保 dist 里有当前提交打的压缩包；没有就运行 release.py。返回 (SHA-256, 大小)。"""
    release.check_clean(False)
    head = git("rev-parse", "--short", "HEAD")
    if ready(head):
        print(f"    dist 里已经有提交 {head} 打的 {ZIP_NAME}，不用重新打包")
    else:
        print("    运行 release.py（检查提交、测试、打包、自检、压缩，要几分钟）", flush=True)
        if subprocess.run([sys.executable, "release.py"], cwd=ROOT).returncode != 0:
            raise Stop("release.py 没有做完（原因见上面）")
        if not ready(head):
            raise Stop("release.py 做完了，但压缩包或 latest.json 和当前提交、版本号对不上")
    digest = sha256(ZIP)
    if (DIST / f"{ZIP_NAME}.sha256").read_text(encoding="utf-8").split()[0] != digest:
        raise Stop(f"{ZIP_NAME} 和打包时记下的 SHA-256 不一样，可能被改过：删掉它重新运行")
    return digest, ZIP.stat().st_size


def upload_zip(size, dry_run):
    info = remote_info(ZIP_URL)
    if info == (size, md5(ZIP)):
        print("    线上已经是这个文件（大小和 MD5 都一样），跳过")
        return
    if info:
        print("    线上有同名的文件但内容不一样，会被覆盖（这个版本还没正式发布，可以覆盖）")
    if dry_run:
        print(f"    （试运行）会上传 {ZIP} → R2 {BUCKET}/{ZIP_NAME}")
        return
    wrangler("r2", "object", "put", f"{BUCKET}/{ZIP_NAME}", "--file", str(ZIP), "--remote",
             "--content-type", "application/zip")


def update_site(digest, size, dry_run):
    """改 site/index.html 里的下载链接、版本号、大小、SHA-256。返回有没有改动。"""
    html = INDEX.read_bytes().decode("utf-8")  # 按原样读写，不改换行符
    old_version = re.search(r"dl\.evotracker\.cn/Evo-(\d+\.\d+\.\d+)-win64\.zip", html)
    old_digest = re.search(r"<code>([0-9a-f]{64})</code>", html)
    if not (old_version and old_digest):
        raise Stop("site/index.html 里找不到下载链接或 SHA-256，网页的写法可能变了，请手动改")
    old_version, old_digest = old_version.group(1), old_digest.group(1)
    sizes = re.findall(r"约 (\d+) MB", html)
    if len(sizes) != 2:
        raise Stop(f"site/index.html 里的\"约 … MB\"应该有 2 处，找到 {len(sizes)} 处：网页的写法可能变了，请手动改")
    changes = [  # (说明, 原文, 新文, 网页里应该有几处)
        ("下载链接", f"{DL}/Evo-{old_version}-win64.zip", ZIP_URL, 2),
        ("首屏版本号", f"<span>v{old_version} 预览版</span>", f"<span>v{APP_VERSION} 预览版</span>", 1),
        ("下载区标题", f"<h3>Evo {old_version} 预览版</h3>", f"<h3>Evo {APP_VERSION} 预览版</h3>", 1),
        ("SHA-256", f"<code>{old_digest}</code>", f"<code>{digest}</code>", 1),
    ] + [("大小", f"约 {s} MB", f"约 {round(size / 2**20)} MB", sizes.count(s)) for s in sorted(set(sizes))]

    new = html
    for label, old, replacement, expected in changes:
        found = new.count(old)
        if found != expected:
            raise Stop(f"site/index.html 里的{label}应该有 {expected} 处，找到 {found} 处：网页的写法可能变了，请手动改")
        if old != replacement:
            new = new.replace(old, replacement)
            print(f"    {label}（{found} 处）：{old} → {replacement}")
    if new == html:
        print("    已经是这个版本，不用改")
        return False
    if dry_run:
        print("    （试运行）不写文件")
    else:
        INDEX.write_bytes(new.encode("utf-8"))
    return True


def publish(dry_run):
    step(1, "准备压缩包")
    digest, size = prepare()
    print(f"    {ZIP_NAME}，{size / 2**20:.0f} MB，SHA-256 {digest}")
    online = online_version()
    print(f"    线上的 latest.json 现在是 {online or '（没有）'}")
    if online and version_tuple(online) >= version_tuple(APP_VERSION):
        raise Stop(f"线上已经发布了 {online}，不比 {APP_VERSION} 旧：已经发布的版本不能再覆盖，要发新版本先改 storage.py 的 APP_VERSION")

    step(2, "上传压缩包到 R2（dl.evotracker.cn）")
    upload_zip(size, dry_run)

    step(3, "下载回来核对 SHA-256")
    if dry_run:
        print("    （试运行）跳过")
    else:
        print(f"    下载 {ZIP_URL}（{size / 2**20:.0f} MB）…", flush=True)
        online_digest = remote_sha256(ZIP_URL)
        if online_digest != digest:
            raise Stop(f"线上文件的 SHA-256 是 {online_digest}，和本机的不一样")
        print("    ✅ 和本机的一样")

    step(4, "改官网 site/index.html")
    update_site(digest, size, dry_run)

    step(5, "上传官网（Cloudflare Pages）")
    if site_is_new(digest):
        print("    线上的官网已经是新版本，跳过")
    elif dry_run:
        print(f"    （试运行）会运行 wrangler pages deploy site --project-name {PAGES_PROJECT} --branch main")
    else:
        wrangler("pages", "deploy", "site", "--project-name", PAGES_PROJECT, "--branch", "main", "--commit-dirty=true")

    step(6, "确认官网已经是新版本")
    if dry_run:
        print("    （试运行）跳过")
    else:
        for _ in range(12):  # 刚传上去可能要几秒才生效，最多等 1 分钟
            if site_is_new(digest):
                break
            time.sleep(5)
        else:
            raise Stop(f"{SITE_URL} 上还不是新的下载链接和 SHA-256，过一会儿重新运行 python publish.py 看看")
        print(f"    ✅ {SITE_URL} 的下载链接是 {ZIP_NAME}，SHA-256 也对")

    if dry_run:
        print("\n试运行完成，没有上传任何东西。正式上传：python publish.py")
    else:
        print(f"\n第一段完成：{APP_VERSION} 的压缩包和官网都已经上线，但还没有任何 Evo 会提醒更新。")
        print("自己从官网下载试一下；确认没问题后，最后一步：python publish.py --live")


# ---------- 第二段 ----------

def go_live(yes):
    step(7, "核对线上的压缩包和官网")
    if not (ZIP.is_file() and LATEST.is_file()):
        raise Stop(f"dist 里没有 {ZIP_NAME} 或 latest.json：先运行 python publish.py")
    latest = json.loads(LATEST.read_text(encoding="utf-8"))
    if latest.get("version") != APP_VERSION:
        raise Stop(f"dist/latest.json 写的是 {latest.get('version')}，不是 {APP_VERSION}：先运行 python publish.py")
    if remote_info(ZIP_URL) != (ZIP.stat().st_size, md5(ZIP)):
        raise Stop(f"线上的 {ZIP_NAME} 和本机的不一样（或者还没传）：先运行 python publish.py")
    if not site_is_new(sha256(ZIP)):
        raise Stop("官网还不是这个版本：先运行 python publish.py")
    print("    ✅ 压缩包和官网都是这个版本")
    if remote_bytes(LATEST_URL) == LATEST.read_bytes():
        print(f"\n线上的 latest.json 已经是 {APP_VERSION}，不用再传")
        return
    online = online_version()
    if online and version_tuple(online) > version_tuple(APP_VERSION):
        raise Stop(f"线上已经是更新的 {online}，不能换回 {APP_VERSION}")

    print(f"\n    要发布：{online or '（没有）'} → {APP_VERSION}（{latest['date']}），开着自动检查的 Evo 会显示这些更新内容：")
    for note in latest["notes"]:
        print(f"      · {note}")
    if not yes:
        if not sys.stdin.isatty():
            raise Stop("需要确认：在终端里运行（会问你），或者确认过以后加 --yes")
        if input("    确认发布吗？输入 y 后回车：").strip().lower() not in ("y", "yes"):
            print("    没有发布")
            return

    step(8, "上传 latest.json")
    wrangler("r2", "object", "put", f"{BUCKET}/latest.json", "--file", str(LATEST), "--remote",
             "--cache-control", "max-age=300", "--content-type", "application/json; charset=utf-8")

    step(9, "确认线上的 latest.json 和本机的一样")
    if remote_bytes(LATEST_URL) != LATEST.read_bytes():
        raise Stop(f"{LATEST_URL} 和本机的 dist/latest.json 不一样")
    print("    ✅ 一样")
    print(f"\n{APP_VERSION} 已发布。CDN 缓存最多 5 分钟，之后所有 Evo 检查更新都会看到新版本。")
    print("别忘了：在 claude/progress.md 的\"版本\"里记一笔，提交，git push origin main")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="发布新版本：上传压缩包、官网和 latest.json（说明见 publish.py 开头）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="试一遍：准备压缩包、预览官网要改的地方，不上传")
    mode.add_argument("--live", action="store_true", help="最后一步：上传 latest.json，Evo 开始提醒更新")
    parser.add_argument("--yes", action="store_true", help="和 --live 一起用：已经确认过，不再问")
    args = parser.parse_args()
    try:
        if args.live:
            go_live(args.yes)
        else:
            publish(args.dry_run)
    except Stop as e:
        print(f"\n❌ 停下了：{e}")
        return 1
    except subprocess.CalledProcessError as e:
        print(f"\n❌ 停下了：{' '.join(map(str, e.cmd))} 出错（返回码 {e.returncode}）")
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(f"\n❌ 停下了：网络或文件出错（{e}）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
