"""Mirror酱（国内渠道，2026-11-04 用户和 Mirror酱 谈好、定的做法）。

Mirror酱 是一个付费的下载加速服务（国内很多二次元游戏工具都用它分发）。发 GitHub Release 时，GitHub Actions
（.github/workflows/mirrorchyan_release.yml，Mirror酱 提的 PR #4）把 Evo-<版本>-win64.zip 和更新日志传给它，
它自己做出和前一个版本相比的增量包。玩家要在 Mirror酱 买 CDK 才能从它那里下载；不带 CDK 只能查版本。
接入不收费，Mirror酱 按玩家的订阅给开发者分成。文档：https://github.com/MirrorChyan/docs

Evo 里用到它的地方：
1. 检查更新（update_check.check）：所有开着自动检查的人每次检查都问一下 latest()，不带 CDK（用户决定）。
   latest.json 读不到时（比如国内连不上 Cloudflare）用这里查到的版本号提醒更新
2. 国内渠道的一键更新（updater.py）：玩家在设置页选了国内渠道、填了 CDK，点"立即更新"时带上 CDK 再问一次，
   拿到带时效的下载地址、SHA-256、大小和包的种类（incremental 增量包 / full 完整包）。只走玩家选的渠道，不自动换
3. 设置页保存 CDK 时核对一次（check_cdk），显示有效到哪天、或者为什么不能用

请求：GET https://mirrorchyan.com/api/resources/Evo/latest?current_version=v<现在的版本>&os=windows&arch=amd64
      &user_agent=evo_desktop（&cdk=<CDK>，只有国内渠道下载、核对 CDK 时才带）
回答：{"code": 0, "msg": "...", "data": {"version_name": "v0.5.4", "release_note": "...",
      "url", "sha256", "filesize", "update_type", "cdk_expired_time"（后面这几项只有带了有效 CDK、有新版本时才有）}}
      出错时 code 不是 0（HTTP 也可能不是 200），见 ERRORS。

CDK 是玩家花钱买的：只存在本机的设置里（settings.json），永远不上传给 Mirror酱 以外的地方、不写进日志；
请求的完整地址（里面有 CDK）和带时效的下载地址也不写进日志、不放进出错信息（用户决定）。

设置（app_settings）：
- update_channel："global" 国际渠道（默认，dl.evotracker.cn）/ "cn" 国内渠道（Mirror酱，要 CDK）
- mirrorchyan_cdk：玩家填的 CDK
- mirrorchyan_cdk_check：上次核对 CDK 的结果 {"ok", "expires" 或 "error", "checked_at"}（设置页显示）
- mirrorchyan_notice_off：设置页点"国内渠道"时弹出的说明（用户写的原文：要花钱、开发者有分成、优先推荐国际渠道），
  点过"不再提示"就不再弹

测试时可以用环境变量 EVO_MIRRORCHYAN_URL 换成本机的测试地址。
用法：python code/mirrorchyan.py   → 不带 CDK 问一次并打印结果
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import app_settings
from storage import APP_VERSION

API = os.environ.get("EVO_MIRRORCHYAN_URL", "https://mirrorchyan.com/api/resources/Evo/latest")
# 用 EVO_MIRRORCHYAN_URL 换成本机的测试地址时，也接受从同一台主机（http 也行）下载，方便不联网测试
TEST_HOST = urllib.parse.urlparse(API).hostname if os.environ.get("EVO_MIRRORCHYAN_URL") else None
PARAMS = {"os": "windows", "arch": "amd64", "user_agent": "evo_desktop"}  # user_agent：Mirror酱 统计"从哪个客户端用的 CDK"
# 设置页"了解 Mirror酱"：Mirror酱 上 Evo 的页面（source 让 Mirror酱 统计玩家从哪里点进去付费的）
PAGE = "https://mirrorchyan.com/zh/projects?rid=Evo&source=evo_settings"
CHANNEL_KEY, CDK_KEY, CHECK_KEY = "update_channel", "mirrorchyan_cdk", "mirrorchyan_cdk_check"
NOTICE_KEY = "mirrorchyan_notice_off"  # 设置页点"国内渠道"时弹出的说明，点过"不再提示"就是 True
TIMEOUT = 15  # 秒，和检查 latest.json 一样
MAX_BYTES = 256 * 1024  # 回答里有更新日志，一般几 KB
MAX_PACKAGE = 1024 ** 3  # 和 update_check 一样：安装包约 110 MB，超过 1 GB 一定是写错了
MAX_CDK = 128
# Mirror酱 的错误代码（https://github.com/MirrorChyan/docs/blob/main/ErrorCode.md）→ 给玩家看的原因
ERRORS = {
    7001: "Mirror酱 CDK 已过期，续费或换一个 CDK 后再试",
    7002: "Mirror酱 CDK 不对，请检查后重新填写",
    7003: "这个 Mirror酱 CDK 今天的下载次数用完了，明天再试",
    7004: "这个 Mirror酱 CDK 不是给 Evo 用的，请换一个",
    7005: "这个 Mirror酱 CDK 已被封禁，请联系 Mirror酱 售后（QQ 群 995458883）",
    8001: "Mirror酱 上暂时没有 Evo 的这个版本，过一会儿再试",
}
CDK_ERRORS = {7001, 7002, 7003, 7004, 7005}
NOTE_GROUPS = ("新增", "改进", "修复")  # 更新日志（GitHub Release 的说明，publish.github_notes）里的分组


class MirrorError(Exception):
    """问 Mirror酱 没问到。str(e) 给玩家看（不含 CDK 和地址）；code 是 Mirror酱 的错误代码（连不上时为 None）。"""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def channel():
    """玩家选的更新渠道："cn" 国内渠道（Mirror酱）/ "global" 国际渠道（默认）。"""
    return "cn" if app_settings.get(CHANNEL_KEY) == "cn" else "global"


def cdk():
    """玩家填的 CDK；没填返回 None。"""
    value = app_settings.get(CDK_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else None


def clean_cdk(text):
    """设置页填的 CDK：去掉前后空格；不像 CDK（太长、有空白或控制字符）返回 None。"""
    text = str(text or "").strip()
    return text if 0 < len(text) <= MAX_CDK and re.fullmatch(r"[\x21-\x7e]+", text) else None


def version_tuple(text):
    """"v0.5.4" 或 "0.5.4" → (0, 5, 4)；格式不对返回 None。"""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def notes(text):
    """更新日志（Markdown）→ 设置页显示的几行：只取"新增""改进""修复"下面的每一条，写成"新增：……"，最多 10 条。"""
    out, group = [], None
    for line in str(text or "").splitlines():
        line = line.strip()
        if line.startswith("---"):
            break
        if line.startswith("#"):
            name = line.lstrip("#").strip()
            group = name if name in NOTE_GROUPS else None
        elif group and line.startswith(("- ", "* ")):
            out.append(f"{group}：{line[2:].strip()}"[:200])
    return out[:10]


def latest(key=None, current=APP_VERSION, timeout=TIMEOUT):
    """问一次 Mirror酱。key：玩家的 CDK（不带就只查版本）。返回
    {"version": "0.5.4", "notes": [...], "download": {"url", "sha256", "size", "update_type"} 或 None,
     "cdk_expires": "2026-12-04" 或 None}。出错抛出 MirrorError。"""
    params = {"current_version": f"v{current}" if current else "", **PARAMS}
    if key:
        params["cdk"] = key
    request = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": f"Evo/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = _json(response.read(MAX_BYTES))
    except urllib.error.HTTPError as e:  # 出错时 Mirror酱 也回 JSON（带错误代码），先看它
        data = _json(_read(e))
        if not isinstance(data, dict) or not isinstance(data.get("code"), int) or data["code"] == 0:
            raise MirrorError(f"Mirror酱 暂时用不了（HTTP {e.code}），请稍后再试") from None
    except OSError:
        raise MirrorError("连不上 Mirror酱（网络不通，或者服务器暂时不可用）") from None
    if not isinstance(data, dict) or not isinstance(data.get("code"), int):
        raise MirrorError("Mirror酱 返回的内容格式不对，请稍后再试")
    if data["code"] != 0:
        code = data["code"]
        raise MirrorError(ERRORS.get(code, f"Mirror酱 暂时用不了（代码 {code}），请稍后再试"), code)
    info = data.get("data") if isinstance(data.get("data"), dict) else {}
    version = version_tuple(info.get("version_name"))
    if version is None:
        raise MirrorError("Mirror酱 返回的版本信息格式不对，请稍后再试")
    return {"version": ".".join(map(str, version)), "notes": notes(info.get("release_note")),
            "download": _download(info), "cdk_expires": _date(info.get("cdk_expired_time"))}


def _read(e):
    try:
        return e.read(MAX_BYTES)
    except OSError:
        return b""


def _json(body):
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, AttributeError):
        return None


def _download(info):
    """回答里的下载信息：地址是 https、SHA-256 和大小格式对，才算有；不对返回 None。"""
    url, digest, size = info.get("url"), info.get("sha256"), info.get("filesize")
    try:
        parsed = urllib.parse.urlparse(str(url))
    except ValueError:
        return None
    test = TEST_HOST is not None and parsed.scheme in ("http", "https") and parsed.hostname == TEST_HOST
    if not (parsed.scheme == "https" or test) or not parsed.hostname or parsed.username or parsed.password:
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        return None
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_PACKAGE:
        return None
    kind = "incremental" if info.get("update_type") == "incremental" else "full"
    return {"url": str(url), "sha256": digest.lower(), "size": size, "update_type": kind}


def _date(stamp):
    """CDK 到期时间（秒）→ "2026-12-04"；没有或不对返回 None。"""
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or stamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def check_cdk(key, timeout=TIMEOUT):
    """设置页保存 CDK 时核对一次，结果记进设置并返回：{"ok": True, "expires", "checked_at"} 或
    {"ok": False, "error", "cdk": 是不是 CDK 本身的问题, "checked_at"}。"""
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        answer = latest(key, timeout=timeout)
        result = {"ok": True, "expires": answer["cdk_expires"], "checked_at": checked_at}
    except MirrorError as e:
        result = {"ok": False, "error": str(e), "cdk": e.code in CDK_ERRORS, "checked_at": checked_at}
    app_settings.set_value(CHECK_KEY, result)
    return result


if __name__ == "__main__":
    print(f"现在的版本：{APP_VERSION}；不带 CDK 问 Mirror酱")
    try:
        r = latest()
        print(f"Mirror酱 上的最新版本：{r['version']}")
        for note in r["notes"]:
            print(f"  · {note}")
    except MirrorError as e:
        print(f"没问到：{e}")
