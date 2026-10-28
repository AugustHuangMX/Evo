"""腾讯云对象存储 COS（国内线路的存储桶）：发布时上传文件。publish.py 用，只在开发电脑上用，不进 Evo。

不用腾讯云的 SDK（要多装好几个包）：只是上传几个文件，用 Python 自带的库按 COS 的"请求签名"规则发请求就行。
大文件分块上传（每块 2 MB，同时传 8 块，每块出错重试几次），每块都带 Content-MD5，COS 收到的和本机的不一样会拒收。
每个文件都记下 SHA-256（x-cos-meta-sha256），publish.py 用它判断线上是不是已经是这个文件。

密钥：只能往这个存储桶上传的子账号的 SecretId / SecretKey，放在 %USERPROFILE%\\.evo\\cos.json
（不在项目文件夹里、不进 git、不进 Evo）：
    {"secret_id": "AKID……", "secret_key": "……"}
存储桶的地址是 update_check.COS_BASE（Evo 也用它）。

试一下密钥能不能用：python tools/cos.py 某个文件   → 传到存储桶的 _test/ 下面，再删掉
"""

import base64
import hashlib
import hmac
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
import update_check

KEY_FILE = Path.home() / ".evo" / "cos.json"
# 从瑞士传到上海：一个连接只有约 40 KB/s，同时 8 个合计约 200 KB/s（16 个也只到约 240 KB/s）。所以大文件分成小块、
# 同时传几块；块小一点，断了只重传这一块。112 MB 的安装包约 10 分钟（2026-10-25 实测）
PART = 2 * 2**20  # 分块上传每块多大
WORKERS = 8  # 同时传几块
SIMPLE_MAX = 4 * 2**20  # 比这小的一次传完
TRIES = 5  # 每个请求最多试几次（从瑞士传到上海，偶尔会断）
TIMEOUT = 300  # 秒：一块（2 MB）要在这么久里传完；Python 对一次发送的整段数据算总时间


class CosError(Exception):
    """上传没成功，str(e) 是原因。"""


def _keys():
    try:
        data = json.loads(KEY_FILE.read_text(encoding="utf-8"))
        secret_id, secret_key = str(data["secret_id"]).strip(), str(data["secret_key"]).strip()
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise CosError(f'读不到 COS 的密钥：要在 {KEY_FILE} 里写上 {{"secret_id": "……", "secret_key": "……"}}'
                       "（只能往这个存储桶上传的子账号的密钥）") from e
    if "粘贴" in secret_id + secret_key or not secret_id or not secret_key:
        raise CosError(f"{KEY_FILE} 里的密钥还没填（还是占位的文字）")
    return secret_id, secret_key


def _host():
    if not update_check.COS_BASE:
        raise CosError("还没有设 COS 存储桶：先在 code/update_check.py 里写上 COS_BASE")
    return urllib.parse.urlparse(update_check.COS_BASE).hostname


def _quote(text):
    return urllib.parse.quote(str(text), safe="-_.~")


def _pairs(items):
    """签名用的"键列表"和"键=值"：键转小写，都按 URL 编码，按键排序。"""
    pairs = sorted((_quote(k).lower(), _quote(v)) for k, v in items.items())
    return ";".join(k for k, _ in pairs), "&".join(f"{k}={v}" for k, v in pairs)


def sign(method, path, params, headers, secret_id, secret_key, now=None, valid=3600):
    """COS 的请求签名（Authorization 头）。见腾讯云文档"请求签名"。"""
    start = int(now if now is not None else time.time()) - 60  # 提前一分钟，免得本机的钟慢了
    key_time = f"{start};{start + valid}"
    sign_key = hmac.new(secret_key.encode(), key_time.encode(), hashlib.sha1).hexdigest()
    param_list, http_params = _pairs(params)
    header_list, http_headers = _pairs(headers)
    http_string = f"{method.lower()}\n{path}\n{http_params}\n{http_headers}\n"
    to_sign = f"sha1\n{key_time}\n{hashlib.sha1(http_string.encode()).hexdigest()}\n"
    signature = hmac.new(sign_key.encode(), to_sign.encode(), hashlib.sha1).hexdigest()
    return (f"q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}&q-key-time={key_time}"
            f"&q-header-list={header_list}&q-url-param-list={param_list}&q-signature={signature}")


def _request(method, name, params=None, headers=None, body=b""):
    """发一个签了名的请求，出错（网络断了、服务器 5xx）重试几次。返回 (状态码, 回应头, 内容)。4xx 抛出 CosError。"""
    secret_id, secret_key = _keys()
    host, params = _host(), params or {}
    headers = {"host": host, "content-length": str(len(body)), **{k.lower(): v for k, v in (headers or {}).items()}}
    if body:
        headers["content-md5"] = base64.b64encode(hashlib.md5(body).digest()).decode()
    query = "&".join(f"{_quote(k)}={_quote(v)}" if v != "" else _quote(k) for k, v in params.items())
    url = f"https://{host}/{_quote(name).replace('%2F', '/')}" + (f"?{query}" if query else "")
    last = None
    for attempt in range(TRIES):
        if attempt:
            time.sleep(2 * attempt)
        signed = {**headers, "authorization": sign(method, "/" + name, params, headers, secret_id, secret_key)}
        request = urllib.request.Request(url, data=body if method in ("PUT", "POST") else None, method=method,
                                         headers={k.title(): v for k, v in signed.items()})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code < 500:
                raise CosError(f"COS 拒绝了 {method} {name}（HTTP {e.code}）：{detail}") from e
            last = f"HTTP {e.code}：{detail}"
        except OSError as e:
            last = str(e)
    raise CosError(f"{method} {name} 试了 {TRIES} 次都没成功（{last}）")


def _xml_text(body, tag):
    for el in ET.fromstring(body).iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            return el.text
    return None


def upload(path, name, content_type, digest, cache_control=None, log=print):
    """把本机的 path 传到存储桶里的 name，记下 SHA-256（digest）。"""
    path = Path(path)
    size = path.stat().st_size
    meta = {"content-type": content_type, "x-cos-meta-sha256": digest}
    if cache_control:
        meta["cache-control"] = cache_control
    if size <= SIMPLE_MAX:
        _request("PUT", name, headers=meta, body=path.read_bytes())
        return
    _, _, body = _request("POST", name, params={"uploads": ""}, headers=meta)
    upload_id = _xml_text(body, "UploadId")
    if not upload_id:
        raise CosError(f"COS 没给分块上传的编号：{body[:200]!r}")
    starts = list(range(0, size, PART))
    done, lock, began = [0], threading.Lock(), time.monotonic()

    def put_part(number):
        with open(path, "rb") as f:
            f.seek(starts[number - 1])
            body = f.read(PART)
        _, headers, _ = _request("PUT", name, params={"partNumber": str(number), "uploadId": upload_id}, body=body)
        with lock:
            done[0] += len(body)
            speed = done[0] / max(time.monotonic() - began, 0.001)
            log(f"    COS：{done[0] / 2**20:.0f} / {size / 2**20:.0f} MB（{speed / 1024:.0f} KB/s）")
        return number, headers.get("ETag", "")

    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            parts = sorted(pool.map(put_part, range(1, len(starts) + 1)))
        xml = "<CompleteMultipartUpload>" + "".join(
            f"<Part><PartNumber>{n}</PartNumber><ETag>{etag}</ETag></Part>" for n, etag in parts) + "</CompleteMultipartUpload>"
        _, _, body = _request("POST", name, params={"uploadId": upload_id}, headers={"content-type": "application/xml"},
                              body=xml.encode())
        if b"CompleteMultipartUploadResult" not in body:
            raise CosError(f"COS 没有把分块拼起来：{body[:200]!r}")
    except BaseException:
        try:
            _request("DELETE", name, params={"uploadId": upload_id})  # 放弃这次分块上传，免得占地方
        except CosError:
            pass
        raise


def delete(name):
    _request("DELETE", name)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    path = Path(sys.argv[1])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    name = f"_test/{path.name}"
    try:
        upload(path, name, "application/octet-stream", digest)
        url = f"{update_check.COS_BASE}/{name}"
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            ok = hashlib.sha256(r.read()).hexdigest() == digest and r.headers.get("x-cos-meta-sha256") == digest
        print(f"{'✅' if ok else '❌'} 传上去了，从 {url} 下载回来{'一样' if ok else '不一样'}")
        delete(name)
        print("已删掉测试文件")
    except (CosError, OSError) as e:
        print(f"❌ {e}")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
