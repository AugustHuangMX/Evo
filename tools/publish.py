"""发布新版本。先改 storage.py 的 APP_VERSION 和 release/更新内容.txt 并提交，然后：

    python tools/publish.py --dry-run   试一遍：准备压缩包、预览官网要改的地方和更新日志的原文，不上传任何东西
    python tools/publish.py             第一段：确认更新日志的原文后，上传压缩包和官网，逐项核对
    python tools/publish.py --live      第二段：上传 latest.json，开着"自动检查新版本"的 Evo 开始提醒更新

为什么分两段：latest.json 一换，所有开着自动检查的 Evo 都会提醒更新，收不回来。
所以先把压缩包、官网都传好、核对过，人也看过了，再做最后一步。

更新日志（2026-10-19 用户要求）：官网更新日志页要加的这一版，原文必须先给开发者看、点头以后才能发布。
--dry-run 会显示原文和核对码（按原文算的）；在终端里运行第一段会直接问你，否则要加 --changelog-ok 核对码。
确认以后更新内容又改过的话，核对码就对不上，要重新确认。

第一段：
  1. 准备压缩包：dist 里已经有当前提交打的压缩包就直接用，否则运行 release.py
     （检查提交、测试、打包、自检、压缩、算 SHA-256、做补丁包、写 latest.json）；
     然后显示更新日志页要加的这一版（原文），确认过才往下做，没确认就什么都不上传
  2. 上传压缩包和补丁包到 R2 存储桶（dl.evotracker.cn）；线上已经是同一个文件就跳过
  3. 从 dl.evotracker.cn 下载回来，核对 SHA-256
  4. 上传压缩包和补丁包到腾讯云 COS（国内备用线路，2026-10-24 用户决定；官网的"国内备用下载"和一键更新都用它）；
     线上已经是同一个文件就跳过
  5. 从 COS 下载回来，核对 SHA-256
  6. 改官网：site/index.html 的下载链接、国内备用下载链接、版本号、大小、SHA-256；
     site/changelog.html 最上面加这一版（内容来自 latest.json，也就是 release/更新内容.txt），"最新"标签挪过来
  7. 上传官网（Cloudflare Pages）；线上已经是新版本就跳过
  8. 打开 evotracker.cn，确认下载链接和 SHA-256 已经是新的，更新日志页有这一版
第二段（--live）：
  9. 再核对一次线上的压缩包、补丁包（R2 比 MD5，COS 比记下的 SHA-256，不用重新下载）和官网
  10. 上传 latest.json（R2 和 COS 各一份；Evo 先读 R2 的，读不到再读 COS 的）
  11. 下载回来，确认和本机的一样

上传 R2 用 Wrangler（Cloudflare 的命令行工具，只装在开发电脑上，第一次用要 wrangler login），不用打开 Cloudflare 网站；
上传 COS 用 tools/cos.py（密钥在 %USERPROFILE%\\.evo\\cos.json，见 cos.py 开头）。
任何一步出问题就停下；改好后重新运行，已经做完的步骤会自动跳过。已经发布的版本不能再覆盖，要发就先改版本号。
"""

import argparse
import hashlib
import html as htmllib
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
import cos
import release
import update_check
from release import DIST, README_NAME, Stop, git, sha256
from storage import APP_VERSION

ROOT = Path(__file__).resolve().parent.parent  # 项目文件夹（本文件在 tools/ 里）
INDEX = ROOT / "site" / "index.html"
CHANGELOG = ROOT / "site" / "changelog.html"
BUCKET = "evotracker"         # R2 存储桶，公开地址是 dl.evotracker.cn
PAGES_PROJECT = "evotracker"  # Cloudflare Pages 项目，网址是 evotracker.cn
DL = "https://dl.evotracker.cn"
SITE_URL = "https://evotracker.cn/"
CHANGELOG_URL = f"{SITE_URL}changelog"
# 更新日志页按每条开头的"新增：""改进：""修复："分组（按这个顺序），别的开头都归到"其他"；后面是网页上小标签的样式
NOTE_GROUPS = (("新增", "chg-new"), ("改进", "chg-better"), ("修复", "chg-fix"), ("其他", ""))
ZIP_NAME = f"Evo-{APP_VERSION}-win64.zip"
ZIP = DIST / ZIP_NAME
ZIP_URL = f"{DL}/{ZIP_NAME}"
LATEST = DIST / "latest.json"
LATEST_URL = f"{DL}/latest.json"
COS = update_check.COS_BASE  # 国内备用线路：腾讯云 COS 的存储桶（还没设时是 None，不能发布）
MIRROR_URL = f"{COS}/{ZIP_NAME}"
# 官网下载区"下载慢？尝试备用下载方式"展开后的链接：<a … data-mirror href="COS 上的压缩包">，发版时换成新版本的
MIRROR_LINK = re.compile(r'(<a\s[^>]*\bdata-mirror\b[^>]*\bhref=")([^"]*)(")')


def step(n, text):
    print(f"\n[{n}/11] {text}", flush=True)


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


def online_version():
    data = remote_bytes(LATEST_URL)
    return json.loads(data.decode("utf-8")).get("version") if data else None


def site_is_new(digest):
    """线上官网的下载链接、国内备用下载链接和 SHA-256 是不是已经是这个压缩包的，更新日志页有没有这一版。"""
    html, changelog = remote_bytes(SITE_URL), remote_bytes(CHANGELOG_URL)
    html = html.decode("utf-8") if html is not None else ""
    return (ZIP_URL in html and MIRROR_URL in html and digest in html
            and changelog is not None and f'id="v{APP_VERSION}"' in changelog.decode("utf-8"))


def url_sha256(url, fresh=True):
    """下载一个文件、算 SHA-256。fresh：地址后面加时间参数绕过 CDN 缓存（R2 要；COS 前面没有 CDN，不加）。"""
    request = urllib.request.Request(f"{url}?t={int(time.time())}" if fresh else url,
                                     headers={"User-Agent": f"Evo-publish/{APP_VERSION}", "Cache-Control": "no-cache"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=300) as r:
        for block in iter(lambda: r.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------- 腾讯云 COS（国内备用线路） ----------

def cos_head(url):
    """COS 上有这个文件就返回 (大小, 上传时记下的 SHA-256)，没有返回 None。"""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": f"Evo-publish/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            return int(r.headers.get("Content-Length", -1)), r.headers.get("x-cos-meta-sha256", "")
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):  # 没有这个文件（COS 对没有的文件可能回 403）
            return None
        raise


def cos_bytes(url):
    """下载 COS 上的一个小文件；不存在返回 None。"""
    request = urllib.request.Request(url, headers={"User-Agent": f"Evo-publish/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None
        raise


# ---------- 第一段 ----------

def zip_commit(path):
    """压缩包是哪次提交打的：读使用说明第一行的"代码 xxx"（release.py 写的）；有没提交的改动时返回 None。"""
    with zipfile.ZipFile(path) as z:
        first = z.read(f"Evo/{README_NAME}").decode("utf-8-sig").splitlines()[0]
    m = re.search(r"代码 ([0-9a-f]+)(\+改动)?", first)
    return m.group(1) if m and not m.group(2) else None


def packages(latest):
    """要传到 R2 和 COS 的文件：[(文件名, 本机的文件, SHA-256)]。压缩包在最前面，补丁包来自 latest.json。"""
    out = [(ZIP_NAME, ZIP, latest["download"]["sha256"])]
    for patch in latest["download"].get("patches", {}).values():
        name = patch["url"].rsplit("/", 1)[-1]
        out.append((name, DIST / name, patch["sha256"]))
    return out


def ready(head):
    """dist 里的压缩包、SHA-256、补丁包、latest.json 都是当前提交、当前版本的吗？"""
    if not (ZIP.is_file() and LATEST.is_file() and (DIST / f"{ZIP_NAME}.sha256").is_file()):
        return False
    commit = zip_commit(ZIP)
    same_commit = commit is not None and (head.startswith(commit) or commit.startswith(head))
    latest = json.loads(LATEST.read_text(encoding="utf-8"))
    if not (same_commit and latest.get("version") == APP_VERSION
            and latest.get("download", {}).get("mirrors") == release.mirror_urls(ZIP_NAME)):
        return False
    return all(path.is_file() and sha256(path) == digest for _, path, digest in packages(latest)[1:])


def prepare():
    """确保 dist 里有当前提交打的压缩包和补丁包；没有就运行 release.py。返回 (SHA-256, 大小, latest.json 的内容)。"""
    if not COS:
        raise Stop("还没有设国内备用线路的 COS 存储桶（code/update_check.py 的 COS_BASE）：设好、提交以后再发布")
    release.check_clean(False)
    head = git("rev-parse", "--short", "HEAD")
    if ready(head):
        print(f"    dist 里已经有提交 {head} 打的 {ZIP_NAME}，不用重新打包")
    else:
        print("    运行 release.py（检查提交、测试、打包、自检、压缩、做补丁包，要几分钟）", flush=True)
        if subprocess.run([sys.executable, "tools/release.py"], cwd=ROOT).returncode != 0:
            raise Stop("release.py 没有做完（原因见上面）")
        if not ready(head):
            raise Stop("release.py 做完了，但压缩包、补丁包或 latest.json 和当前提交、版本号对不上")
    digest = sha256(ZIP)
    if (DIST / f"{ZIP_NAME}.sha256").read_text(encoding="utf-8").split()[0] != digest:
        raise Stop(f"{ZIP_NAME} 和打包时记下的 SHA-256 不一样，可能被改过：删掉它重新运行")
    return digest, ZIP.stat().st_size, json.loads(LATEST.read_text(encoding="utf-8"))


def upload_r2(files, dry_run):
    for name, path, _ in files:
        info = remote_info(f"{DL}/{name}")
        if info == (path.stat().st_size, md5(path)):
            print(f"    {name}：线上已经是这个文件（大小和 MD5 都一样），跳过")
            continue
        if info:
            print(f"    {name}：线上有同名的文件但内容不一样，会被覆盖（这个版本还没正式发布，可以覆盖）")
        if dry_run:
            print(f"    （试运行）会上传 {path} → R2 {BUCKET}/{name}")
            continue
        wrangler("r2", "object", "put", f"{BUCKET}/{name}", "--file", str(path), "--remote",
                 "--content-type", "application/zip")


def upload_cos(files, dry_run):
    for name, path, digest in files:
        if cos_head(f"{COS}/{name}") == (path.stat().st_size, digest):
            print(f"    {name}：COS 上已经是这个文件（大小和 SHA-256 都一样），跳过")
            continue
        if dry_run:
            print(f"    （试运行）会上传 {path} → COS {name}")
            continue
        print(f"    上传 {name}（{path.stat().st_size / 2**20:.1f} MB）→ COS …", flush=True)
        cos.upload(path, name, "application/zip", digest)


def verify(files, base, fresh):
    """从线上下载回来核对 SHA-256。"""
    for name, path, digest in files:
        url = f"{base}/{name}"
        print(f"    下载 {url}（{path.stat().st_size / 2**20:.1f} MB）…", flush=True)
        got = url_sha256(url, fresh)
        if got != digest:
            raise Stop(f"线上 {url} 的 SHA-256 是 {got}，和本机的不一样")
        print("    ✅ 和本机的一样")


def update_site(digest, size, dry_run):
    """改 site/index.html 里的下载链接、国内备用下载链接、版本号、大小、SHA-256。返回有没有改动。"""
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

    mirrors = MIRROR_LINK.findall(html)  # "下载慢？尝试备用下载方式"展开后的国内备用下载（COS）
    if len(mirrors) != 1:
        raise Stop(f"site/index.html 里的国内备用下载链接（带 data-mirror 的 <a>）应该有 1 处，找到 {len(mirrors)} 处：网页的写法可能变了，请手动改")
    changes.append(("国内备用下载链接", mirrors[0][1], MIRROR_URL, 1))

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


# ---------- 更新日志页（site/changelog.html） ----------

def changelog_groups(notes):
    """更新内容按开头的"新增：""改进：""修复：""其他："分组、去掉开头（别的开头原样归到"其他"）。
    返回 [(组名, 样式, [条目…])…]，空的组不要。0.4.0 时"其他："没去掉，页面上成了"其他"下面一条"其他：……"。"""
    groups = {name: [] for name, _ in NOTE_GROUPS}
    for note in notes:
        name, sep, rest = note.partition("：")
        if sep and name in groups and rest.strip():
            groups[name].append(rest.strip())
        else:
            groups["其他"].append(note)
    return [(name, css, groups[name]) for name, css in NOTE_GROUPS if groups[name]]


def changelog_entry(latest):
    """这一版在更新日志页上的样子。返回 (给人看的原文, 网页里的 HTML, 核对码)。核对码按原文算，原文一变就不一样。"""
    version, day = latest["version"], date.fromisoformat(latest["date"])
    day_text = f"{day.year} 年 {day.month} 月 {day.day} 日"
    groups = changelog_groups(latest["notes"])
    if not groups:
        raise Stop("latest.json 里没有更新内容（来自 release/更新内容.txt），更新日志页没法写这一版")
    text = [f"{version} · {day_text} · 最新 · 预览版"]
    html = [
        f'      <li class="release release-latest" id="v{version}">',
        '        <div class="release-side">',
        f'          <h2 class="release-ver">{version}</h2>',
        f'          <p class="release-date"><time datetime="{day:%Y-%m-%d}">{day_text}</time></p>',
        '          <p class="release-tags"><span class="tag tag-latest">最新</span><span class="tag">预览版</span></p>',
        '        </div>',
        '        <div class="release-body">',
    ]
    for name, css, items in groups:
        text.append(name)
        text += [f"  · {item}" for item in items]
        html.append(f'          <h3 class="chg-title"><span class="{f"chg {css}" if css else "chg"}">{name}</span></h3>')
        html.append('          <ul class="chg-list">')
        html += [f"            <li>{htmllib.escape(item, quote=False)}</li>" for item in items]
        html.append('          </ul>')
    html += ['        </div>', '      </li>', '']
    text = "\n".join(text)
    return text, "\n".join(html), hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]


def ask_yes(prompt):
    """在终端里问一句，输入 y 才算同意。没有终端可问（比如被别的程序调用）时返回 None。
    Windows 上标准输入接到 NUL 时 isatty() 也是 True，所以还要接住读不到输入的 EOFError。"""
    if not sys.stdin.isatty():
        return None
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return None


def confirm_changelog(code, given):
    """更新日志的原文要先给开发者看、点头以后才能发布。原文已经打在上面了。"""
    if given == code:
        print(f"    ✅ 已确认（核对码 {code}）")
        return
    if given:
        raise Stop(f"这段原文的核对码是 {code}，不是 {given}：更新内容在确认以后改过，请把上面的原文重新确认一遍")
    answer = ask_yes("    原文没问题吗？输入 y 后回车：")
    if answer is None:
        raise Stop(f"要先确认上面的原文：在终端里运行（会问你），或者确认过以后加 --changelog-ok {code}")
    if not answer:
        raise Stop("没有确认更新日志的原文，什么都没上传")


def update_changelog(entry, dry_run):
    """在 site/changelog.html 最上面加这一版，"最新"标签和蓝色圆点从上一版挪过来。返回有没有改动。"""
    html = CHANGELOG.read_bytes().decode("utf-8")  # 按原样读写，不改换行符
    nl = "\r\n" if "\r\n" in html else "\n"
    entry = entry.replace("\n", nl)
    if f'id="v{APP_VERSION}"' in html:
        if entry in html:
            print("    更新日志页已经有这一版，不用改")
            return False
        raise Stop(f"site/changelog.html 里已经有 {APP_VERSION}，但和这次的更新内容不一样：请手动核对")
    head = f'<ol class="releases">{nl}'
    top = re.search(r'<li class="release release-latest" id="v(\d+\.\d+\.\d+)">', html)
    marks = (head, ' release-latest"', '<span class="tag tag-latest">最新</span>')
    if not top or any(html.count(m) != 1 for m in marks):
        raise Stop("site/changelog.html 里找不到时间线的开头，或者\"最新\"的不止一版：网页的写法可能变了，请手动改")
    if version_tuple(top.group(1)) >= version_tuple(APP_VERSION):
        raise Stop(f"更新日志页最上面是 {top.group(1)}，不比 {APP_VERSION} 旧")
    new = html.replace(' release-latest"', '"').replace('<span class="tag tag-latest">最新</span>', "")
    new = new.replace(head, head + nl + entry)
    print(f"    更新日志页：最上面加 {APP_VERSION}，\"最新\"从 {top.group(1)} 挪过来")
    if dry_run:
        print("    （试运行）不写文件")
    else:
        CHANGELOG.write_bytes(new.encode("utf-8"))
    return True


def publish(dry_run, changelog_ok):
    step(1, "准备压缩包和补丁包")
    digest, size, latest = prepare()
    files = packages(latest)
    print(f"    {ZIP_NAME}，{size / 2**20:.0f} MB，SHA-256 {digest}")
    for name, path, _ in files[1:]:
        print(f"    补丁包 {name}，{path.stat().st_size / 2**20:.1f} MB")
    online = online_version()
    print(f"    线上的 latest.json 现在是 {online or '（没有）'}")
    if online and version_tuple(online) >= version_tuple(APP_VERSION):
        raise Stop(f"线上已经发布了 {online}，不比 {APP_VERSION} 旧：已经发布的版本不能再覆盖，要发新版本先改 storage.py 的 APP_VERSION")
    text, entry, code = changelog_entry(latest)
    print("\n    官网更新日志页最上面会加这一版（原文）：")
    for line in text.splitlines():
        print(f"      {line}")
    print(f"    核对码：{code}")
    if dry_run:
        print(f"    （试运行）正式发布前要确认这段原文；确认过以后：python tools/publish.py --changelog-ok {code}")
    else:
        confirm_changelog(code, changelog_ok)

    step(2, "上传压缩包和补丁包到 R2（dl.evotracker.cn）")
    upload_r2(files, dry_run)

    step(3, "从 dl.evotracker.cn 下载回来核对 SHA-256")
    if dry_run:
        print("    （试运行）跳过")
    else:
        verify(files, DL, fresh=True)

    step(4, "上传压缩包和补丁包到腾讯云 COS（国内备用线路）")
    upload_cos(files, dry_run)

    step(5, "从 COS 下载回来核对 SHA-256")
    if dry_run:
        print("    （试运行）跳过")
    else:
        verify(files, COS, fresh=False)

    step(6, "改官网 site/index.html 和更新日志页 site/changelog.html")
    update_site(digest, size, dry_run)
    update_changelog(entry, dry_run)

    step(7, "上传官网（Cloudflare Pages）")
    if site_is_new(digest):
        print("    线上的官网已经是新版本，跳过")
    elif dry_run:
        print(f"    （试运行）会运行 wrangler pages deploy site --project-name {PAGES_PROJECT} --branch main")
    else:
        wrangler("pages", "deploy", "site", "--project-name", PAGES_PROJECT, "--branch", "main", "--commit-dirty=true")

    step(8, "确认官网已经是新版本")
    if dry_run:
        print("    （试运行）跳过")
    else:
        for _ in range(12):  # 刚传上去可能要几秒才生效，最多等 1 分钟
            if site_is_new(digest):
                break
            time.sleep(5)
        else:
            raise Stop(f"{SITE_URL} 上还不是新的下载链接和 SHA-256，或者 {CHANGELOG_URL} 还没有这一版，过一会儿重新运行 python tools/publish.py 看看")
        print(f"    ✅ {SITE_URL} 的下载链接和国内备用下载链接是 {ZIP_NAME}，SHA-256 也对；{CHANGELOG_URL} 有 {APP_VERSION}")

    if dry_run:
        print("\n试运行完成，没有上传任何东西。正式上传：python tools/publish.py")
    else:
        print(f"\n第一段完成：{APP_VERSION} 的压缩包、补丁包和官网都已经上线，但还没有任何 Evo 会提醒更新。")
        print("自己从官网下载试一下；确认没问题后，最后一步：python tools/publish.py --live")


# ---------- 第二段 ----------

def go_live(yes):
    step(9, "核对线上的压缩包、补丁包和官网")
    if not (ZIP.is_file() and LATEST.is_file()):
        raise Stop(f"dist 里没有 {ZIP_NAME} 或 latest.json：先运行 python tools/publish.py")
    latest = json.loads(LATEST.read_text(encoding="utf-8"))
    if latest.get("version") != APP_VERSION:
        raise Stop(f"dist/latest.json 写的是 {latest.get('version')}，不是 {APP_VERSION}：先运行 python tools/publish.py")
    if not COS or latest.get("download", {}).get("mirrors") != release.mirror_urls(ZIP_NAME):
        raise Stop("dist/latest.json 里的国内备用线路和 update_check.COS_BASE 不一样：删掉 dist 里的压缩包，重新运行 python tools/publish.py")
    for name, path, digest in packages(latest):
        if not path.is_file() or remote_info(f"{DL}/{name}") != (path.stat().st_size, md5(path)):
            raise Stop(f"R2 上的 {name} 和本机的不一样（或者还没传）：先运行 python tools/publish.py")
        if cos_head(f"{COS}/{name}") != (path.stat().st_size, digest):
            raise Stop(f"COS 上的 {name} 和本机的不一样（或者还没传）：先运行 python tools/publish.py")
    if not site_is_new(sha256(ZIP)):
        raise Stop("官网还不是这个版本：先运行 python tools/publish.py")
    print("    ✅ R2 和 COS 上的压缩包、补丁包，还有官网，都是这个版本")
    local = LATEST.read_bytes()
    r2_done, cos_done = remote_bytes(LATEST_URL) == local, cos_bytes(f"{COS}/latest.json") == local
    if r2_done and cos_done:
        print(f"\n线上的 latest.json（R2 和 COS）已经是 {APP_VERSION}，不用再传")
        return
    online = online_version()
    if online and version_tuple(online) > version_tuple(APP_VERSION):
        raise Stop(f"线上已经是更新的 {online}，不能换回 {APP_VERSION}")

    print(f"\n    要发布：{online or '（没有）'} → {APP_VERSION}（{latest['date']}），开着自动检查的 Evo 会显示这些更新内容：")
    for note in latest["notes"]:
        print(f"      · {note}")
    if not yes:
        answer = ask_yes("    确认发布吗？输入 y 后回车：")
        if answer is None:
            raise Stop("需要确认：在终端里运行（会问你），或者确认过以后加 --yes")
        if not answer:
            print("    没有发布")
            return

    step(10, "上传 latest.json（R2，再 COS）")
    if r2_done:
        print("    R2 上的已经是这个，跳过")
    else:
        wrangler("r2", "object", "put", f"{BUCKET}/latest.json", "--file", str(LATEST), "--remote",
                 "--cache-control", "max-age=300", "--content-type", "application/json; charset=utf-8")
    if cos_done:
        print("    COS 上的已经是这个，跳过")
    else:
        try:
            cos.upload(LATEST, "latest.json", "application/json; charset=utf-8", sha256(LATEST), cache_control="no-cache")
        except cos.CosError as e:
            raise Stop(f"R2 上的 latest.json 已经换成 {APP_VERSION}（Evo 会提醒更新），COS 上的没传上（{e}）："
                       "重新运行 python tools/publish.py --live --yes 补传") from e

    step(11, "确认线上的 latest.json 和本机的一样")
    if remote_bytes(LATEST_URL) != local:
        raise Stop(f"{LATEST_URL} 和本机的 dist/latest.json 不一样")
    if cos_bytes(f"{COS}/latest.json") != local:
        raise Stop(f"{COS}/latest.json 和本机的 dist/latest.json 不一样")
    print("    ✅ R2 和 COS 上的都一样")
    print(f"\n{APP_VERSION} 已发布。CDN 缓存最多 5 分钟，之后所有 Evo 检查更新都会看到新版本。")
    print("别忘了：在 claude/progress.md 的\"版本\"里记一笔，提交，git push origin main")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="发布新版本：上传压缩包、官网和 latest.json（说明见 publish.py 开头）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="试一遍：准备压缩包、预览官网要改的地方，不上传")
    mode.add_argument("--live", action="store_true", help="最后一步：上传 latest.json，Evo 开始提醒更新")
    parser.add_argument("--yes", action="store_true", help="和 --live 一起用：已经确认过，不再问")
    parser.add_argument("--changelog-ok", metavar="核对码", help="第一段用：更新日志的原文已经确认过（核对码见 --dry-run 的输出）")
    args = parser.parse_args()
    try:
        if args.live:
            go_live(args.yes)
        else:
            publish(args.dry_run, args.changelog_ok)
    except Stop as e:
        print(f"\n❌ 停下了：{e}")
        return 1
    except subprocess.CalledProcessError as e:
        print(f"\n❌ 停下了：{' '.join(map(str, e.cmd))} 出错（返回码 {e.returncode}）")
        return 1
    except cos.CosError as e:
        print(f"\n❌ 停下了：上传到 COS 没成功（{e}）")
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(f"\n❌ 停下了：网络或文件出错（{e}）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
