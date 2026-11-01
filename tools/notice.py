"""发开发者公告：Evo 窗口顶部显示，重要的再用托盘通知提醒一次（格式和 Evo 怎么读见 code/notice_check.py）。

    python tools/notice.py "公告正文"                          发一条普通公告（只在 Evo 窗口里显示）
    python tools/notice.py "公告正文" --important              重要公告：也用托盘通知提醒一次（Apex 开着时等关了游戏再弹）
        可以加：--link https://evotracker.cn/……（只能是自己的网站）、--until 2026-11-05（过了这天不显示）、
                --versions "<=0.5.0"（只给这些版本看，逗号隔开的都要满足，如 ">=0.5.1,<0.6.0"）
    python tools/notice.py --clear                              撤下（传一个没有公告的文件）
    python tools/notice.py --show                               看线上现在是什么

一次只发一条：新的换掉旧的。原文先在聊天里给用户确认；加 --yes 不再问"确认上传吗"。
传到 R2（https://dl.evotracker.cn/notice.json）和 COS（国内线路）各一份，都下载回来核对。COS 传不上或读不到时
（比如 2026-10-31 起存储桶被封）照样发：Evo 读不到 COS 上的会改读 dl.evotracker.cn 的。
Evo 开着"自动检查新版本"时每 30 分钟读一次，打开窗口时也会读，所以发出去以后最慢约半小时大家都能看到；撤下也一样。
只有带公告功能的 Evo 能看到（0.5.0 及以前没有）。
"""

import argparse
import json
import sys
import urllib.error
from datetime import datetime

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
import cos
import notice_check
import update_check
from publish import BUCKET, COS, DL, cos_bytes, remote_bytes, wrangler
from release import COS_PAUSED, DIST, Stop, sha256

NAME = "notice.json"
LOCAL = DIST / NAME
R2_URL = f"{DL}/{NAME}"
CONTENT_TYPE = "application/json; charset=utf-8"


def build(args):
    """要发的文件内容（--clear 时没有公告）。格式不对（notice_check 会去掉或改掉的）就停下。"""
    if args.clear:
        return {"notices": []}
    notice = {"id": datetime.now().strftime("%Y%m%d-%H%M"), "level": "important" if args.important else "info",
              "text": args.text}
    for key in ("link", "until", "versions"):
        if getattr(args, key) is not None:
            notice[key] = getattr(args, key)
    checked = notice_check.clean(notice)
    if checked is None:
        raise Stop("公告的格式不对：--until 要写成 2026-11-05，--versions 要写成 <=0.5.0 这样，正文不能是空的")
    if args.link and checked["link"] is None:
        raise Stop(f"链接只能是自己网站上的 https 地址（{'、'.join(sorted(update_check.TRUSTED_HOSTS))}）")
    if checked["text"] != args.text:
        raise Stop(f"正文超过 {notice_check.TEXT_MAX} 个字，或者有换行、连着的空格（Evo 会改成一行）：先改短、改成一行")
    return {"notices": [notice]}


def describe(data):
    notices = notice_check.clean_list(data.get("notices")) if isinstance(data, dict) else []
    if not notices:
        return "（没有公告）"
    return "\n".join(f"  [{'重要' if n['level'] == 'important' else '普通'}] {n['id']}：{n['text']}"
                     + (f"\n    链接 {n['link']}" if n["link"] else "") + (f"\n    到 {n['until']}" if n["until"] else "")
                     + (f"\n    只给 {n['versions']}" if n["versions"] else "") for n in notices)


def online():
    """线上（R2）现在的公告；没有这个文件返回 None。"""
    data = remote_bytes(R2_URL)
    return json.loads(data.decode("utf-8")) if data is not None else None


def upload(local):
    print(f"\n上传到 R2：{R2_URL}")
    wrangler("r2", "object", "put", f"{BUCKET}/{NAME}", "--file", str(LOCAL), "--remote",
             "--cache-control", "no-cache", "--content-type", CONTENT_TYPE)
    if remote_bytes(R2_URL) != local:
        raise Stop(f"{R2_URL} 和本机的 {LOCAL} 不一样")
    print("    ✅ 下载回来核对过，一样")
    if COS_PAUSED:  # 2026-11-01 用户决定：COS 被封，找到替代方案之前都不传（恢复时把当时线上的公告补传上去）
        print("COS 停用中（release.COS_PAUSED），不传 COS：Evo 读不到 COS 上的会改读 dl.evotracker.cn 的")
        return
    print(f"上传到 COS：{COS}/{NAME}")
    try:
        cos.upload(LOCAL, NAME, CONTENT_TYPE, sha256(LOCAL), cache_control="no-cache")
        same = cos_bytes(f"{COS}/{NAME}") == local
    except (cos.CosError, urllib.error.URLError, OSError) as e:
        print(f"    ⚠ COS 没传上或读不到（{e}）：Evo 会改读 dl.evotracker.cn 的，照样能看到")
        return
    print("    ✅ 下载回来核对过，一样" if same else "    ⚠ COS 上的和本机的不一样：Evo 读 COS 时会读到旧的，查一下")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="发开发者公告（说明见 notice.py 开头）")
    parser.add_argument("text", nargs="?", help="公告正文（一行，最多 200 个字）")
    parser.add_argument("--important", action="store_true", help="重要公告：也用托盘通知提醒一次")
    parser.add_argument("--link", help="详情链接（只能是自己网站上的 https 地址）")
    parser.add_argument("--until", help="过了这天就不显示，如 2026-11-05")
    parser.add_argument("--versions", help='只给这些版本看，如 "<=0.5.0"')
    parser.add_argument("--clear", action="store_true", help="撤下公告")
    parser.add_argument("--show", action="store_true", help="看线上现在是什么")
    parser.add_argument("--yes", action="store_true", help="不再问确认")
    args = parser.parse_args()
    try:
        if args.show:
            data = online()
            print(f"{R2_URL}：" + ("没有这个文件" if data is None else f"\n{describe(data)}"))
            return 0
        if bool(args.text) == args.clear:
            parser.error("写公告正文，或者用 --clear 撤下（两个只能选一个）")
        data = build(args)
        current = online()
        print("线上现在：\n" + ("  没有这个文件" if current is None else describe(current)))
        print("要换成：\n" + describe(data))
        if not args.yes and input("\n确认上传吗？输入 y 上传：").strip().lower() != "y":
            print("没有上传")
            return 1
        LOCAL.parent.mkdir(parents=True, exist_ok=True)
        local = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        LOCAL.write_bytes(local)
        upload(local)
        print("\n发好了：开着\"自动检查新版本\"的 Evo 最慢约半小时内看到" + ("（已撤下）" if args.clear else ""))
        return 0
    except Stop as e:
        print(f"\n❌ {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
