"""总览页的地图轮换（2026-10-28 用户决定）：匹配、排位、轮换模式现在是哪张图、还剩多久换、下一张是什么。

- 数据：统计服务器 GET {telemetry.STATS_URL}/v1/maps（server/stats/worker.js 的 maps）。服务器用用户自己申请的
  Apex Legends Status API Key 去取，存到最早换图为止；Evo 里不放 Key。回答几百字节，请求里不带任何编号和战绩
- 先问再联网（2026-10-28 用户决定）：隐私说明承诺过新的联网功能先问、默认不开，代码签名（SignPath）也要求
  不经用户要求不联网。所以还没问过时，总览页的地图卡片里只问一句"显示各模式现在的地图？"，点了"显示"才下载；
  点"不用了"就不再显示。设置页"常规"里随时能开关（settings.json 的 show_maps：没问过是 None）
- 只有面板进程下载（Evo 窗口开着、打开总览页时），托盘和记录程序不下载。存在数据文件夹的 map_rotation.json：
  现在这张到点了就当作下一张在打；有一个模式的"现在这张"到点了就重新下载（页面上的倒计时到点时也会叫一次）
- 时间：服务器回答里带着它现在的时间，下载时和电脑的时间比，记下差多少，电脑时钟不准时倒计时也对
- 英文名 → 简体：config/rotation.json，查不到的显示英文（不编译名）

测试时把 telemetry.STATS_URL 换成本机的测试地址（或者设环境变量 EVO_STATS_URL）。
"""

import gzip
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import app_settings
import storage
import telemetry
from paths import CONFIG_DIR, DB_PATH

CACHE_PATH = DB_PATH.parent / "map_rotation.json"  # {"fetched_at", "offset": 服务器时间 − 电脑时间（秒）, "modes"}
SETTING_KEY = "show_maps"  # True 显示（联网）/ False 不显示 / 没有 = 还没问过（不联网）
TIMEOUT = 15  # 秒：和匿名统计一样，国内有的网络到 Cloudflare 绕得远
MAX_SIZE = 64 * 1024
FORMAT = 1  # 服务器回答的格式版本
RULES = json.loads((CONFIG_DIR / "rotation.json").read_text(encoding="utf-8"))
_MAPS = {k.lower(): v for k, v in RULES["maps"].items()}
_EVENTS = {k.lower(): v for k, v in RULES["events"].items()}
_lock = threading.Lock()  # 页面打开时的自动下载和倒计时到点时的下载可能同时来，一次只下载一个


def enabled():
    """总览页显示不显示地图轮换（显示才联网）。还没问过算不显示。"""
    return app_settings.get(SETTING_KEY) is True


def card():
    """总览页的地图卡片要什么：点过"不用了"、在设置页关了是 None（没有卡片）；还没问过是 {"ask": True}
    （卡片上问一次，不联网）；开着是 view()。"""
    choice = app_settings.get(SETTING_KEY)
    if choice is None:
        return {"ask": True}
    return view(cached()) if choice is True else None


def zh_map(name):
    """英文地图名 → 简体；查不到返回原来的英文。"""
    return _MAPS.get(name.lower()) or _MAPS.get(name.lower().removeprefix("the ")) or name


def zh_event(name):
    """轮换模式的玩法（如 "TDM"）→ 简体；查不到返回原来的英文。"""
    return _EVENTS.get(name.lower(), name) if name else None


# ---------- 下载和存放 ----------

def _slot(o):
    """一场：{map, event, end}（end 是服务器时间，秒）；不像样返回 None。"""
    if not isinstance(o, dict) or not isinstance(o.get("map"), str) or not o["map"].strip():
        return None
    end = o.get("end")
    if not isinstance(end, (int, float)) or isinstance(end, bool) or end <= 0:
        return None
    event = o.get("event")
    return {"map": o["map"].strip(), "event": event.strip() if isinstance(event, str) and event.strip() else None,
            "end": int(end)}


def _modes(raw):
    """服务器回答里的 modes → {模式: {"current", "next"}}，只留认识、像样的；一个都没有返回 None。"""
    if not isinstance(raw, dict):
        return None
    out = {}
    for mode in RULES["modes"]:
        part = raw.get(mode["key"])
        current = _slot(part.get("current")) if isinstance(part, dict) else None
        if current:
            out[mode["key"]] = {"current": current, "next": _slot(part.get("next"))}
    return out or None


def cached():
    """存着的轮换 {"fetched_at", "offset", "modes"}；没有、格式不对返回 None。"""
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("offset"), (int, float)):
        return None
    modes = _modes(data.get("modes"))
    return {**data, "modes": modes} if modes else None


def fetch(now=None):
    """下载一次轮换，存进数据文件夹。关着时不联网。返回 {"ok", "error"}。now：电脑现在的时间（秒，测试用）。"""
    if not enabled():
        return {"ok": False, "error": "没有打开"}
    with _lock:
        request = urllib.request.Request(f"{telemetry.STATS_URL}/v1/maps", headers={
            "User-Agent": f"Evo/{storage.APP_VERSION}", "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read(MAX_SIZE + 1)
                gzipped = response.headers.get("Content-Encoding") == "gzip"
            if len(raw) > MAX_SIZE:
                raise ValueError("太大")
            answer = json.loads((gzip.decompress(raw) if gzipped else raw).decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"服务器暂时取不到（{e.code}）"}
        except (ValueError, EOFError, gzip.BadGzipFile):
            return {"ok": False, "error": "服务器的回答格式不对"}
        except OSError:
            return {"ok": False, "error": "连不上服务器"}
        modes = _modes(answer.get("modes")) if isinstance(answer, dict) and answer.get("v") == FORMAT else None
        server_now = answer.get("now") if isinstance(answer, dict) else None
        if not modes or not isinstance(server_now, (int, float)):
            return {"ok": False, "error": "服务器的回答格式不对"}
        local_now = time.time() if now is None else now
        data = {"fetched_at": datetime.fromtimestamp(local_now).strftime("%Y-%m-%d %H:%M:%S"),
                "offset": round(server_now - local_now), "modes": modes}
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = CACHE_PATH.with_name(CACHE_PATH.name + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, CACHE_PATH)
        return {"ok": True, "error": None}


# ---------- 页面上显示的 ----------

def left_text(seconds):
    """还剩多久：如 "13:05:42"、"08:31"（不到 1 小时不写小时）。"""
    s = max(0, int(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _title(slot):
    """一场写成什么：大逃杀写地图；轮换模式写玩法（地图另写一行）。"""
    return zh_event(slot["event"]) if slot["event"] else zh_map(slot["map"])


def view(cache=None, now=None):
    """总览页的地图卡片：{"modes": [每个模式一格], "fetch": 要不要马上下载}。
    每格：key、name（匹配 / 排位 / 轮换模式）、title（地图，轮换模式是玩法）、sub（轮换模式的地图）、
    end（换图的时间，电脑时间的秒，页面上的倒计时用）、left（还剩多久）、next（下一张 / 下一场，不知道是 None）、
    next_end（下一张结束的时间）。还没下载过、这个模式没有数据、两张都过了点：title 是 None。"""
    now = time.time() if now is None else now
    server_now = now + (cache["offset"] if cache else 0)
    slots = []
    for mode in RULES["modes"]:
        part = (cache or {}).get("modes", {}).get(mode["key"]) if cache else None
        current, nxt = (part["current"], part["next"]) if part else (None, None)
        if current and current["end"] <= server_now:  # 到点了：当作下一张在打（再下一张不知道）
            current, nxt = (nxt, None) if nxt and nxt["end"] > server_now else (None, None)
        slot = {"key": mode["key"], "name": mode["name"], "title": None, "sub": None, "end": None, "left": None,
                "next": None, "next_end": None}
        if current:
            offset = cache["offset"]
            slot.update(title=_title(current), sub=zh_map(current["map"]) if current["event"] else None,
                        end=current["end"] - offset, left=left_text(current["end"] - server_now))
            if nxt:
                slot.update(next=_title(nxt) + (f" · {zh_map(nxt['map'])}" if nxt["event"] else ""),
                            next_end=nxt["end"] - offset)
        slots.append(slot)
    ended = cache is None or any(p["current"]["end"] <= server_now for p in cache["modes"].values())
    return {"modes": slots, "fetch": ended}
