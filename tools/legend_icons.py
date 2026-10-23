"""整理传奇头像图标：assets/legends/ 里的原图（Apex Legends Wiki 上的 SVG）→ panel/static/legends/<编号>.svg，
面板上显示在传奇名字前面（颜色跟随文字，见 style.css 的 .legend-icon）。

做三件事：
1. 按 config/legends.json 里的英文名对上传奇编号：文件名去掉 _Icon、_Mobile，下划线当空格（Mad_Maggie_Icon.svg → mad_maggie）
2. 去掉绘图软件（Inkscape）留下的编辑信息
3. 量出图形实际画到哪里（用 Edge 无界面模式），四周的空白裁成一样多，每个头像上下一样高

许可：每个原图的许可不一样（不适用 AGPL-3.0），写在 assets/legends/LICENSE.txt，这里原样复制过去、随安装包一起发。
不在 LICENSE.txt 里的原图不生成：先去 wiki 的文件页上查清许可、写进去。

新传奇上线：把 wiki 上的图标放进 assets/legends/，在 LICENSE.txt 里加一行，再运行 python tools/legend_icons.py
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import code_path  # noqa: F401  先把 code/ 加进搜索路径，下面才能 import 程序代码（见 code_path.py）
from paths import ASSETS_DIR, CONFIG_DIR, PANEL_DIR

SRC_DIR = ASSETS_DIR / "legends"
OUT_DIR = PANEL_DIR / "static" / "legends"
LICENSE = "LICENSE.txt"
PAD = 0.03  # 四周各留边长的 3%（个别图形有描边，量出来的范围不含描边）

SVG = "http://www.w3.org/2000/svg"
EDITOR_NS = {  # 绘图软件的编辑信息，显示用不到
    "http://www.inkscape.org/namespaces/inkscape",
    "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    "http://purl.org/dc/elements/1.1/",
    "http://creativecommons.org/ns#",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
}
ROOT_DROP = ("width", "height", "version", "{http://www.w3.org/XML/1998/namespace}space")  # 大小由面板的样式定
EDGE = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]

ET.register_namespace("", SVG)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


def namespace(name):
    return name[1:].split("}")[0] if name.startswith("{") else ""


def legend_key(filename, by_en):
    """原图文件名 → 传奇编号（config/legends.json 的键）；对不上返回 None。"""
    en = re.sub(r"(_Mobile)?_Icon$", "", Path(filename).stem).replace("_", " ").lower()
    return by_en.get(en)


def clean(path):
    """读一个原图，去掉编辑信息和没人引用的 id，返回根元素。"""
    text = path.read_text(encoding="utf-8")
    referenced = {a or b for a, b in re.findall(r'url\(#([^)]+)\)|href="#([^"]+)"', text)}
    root = ET.fromstring(text)

    def strip(el):
        for child in list(el):
            if namespace(child.tag) in EDITOR_NS or child.tag == f"{{{SVG}}}metadata":
                el.remove(child)
            else:
                strip(child)
        for key in list(el.attrib):
            if namespace(key) in EDITOR_NS or (key == "id" and el.attrib[key] not in referenced):
                del el.attrib[key]
        if "style" in el.attrib:  # 样式里 Inkscape 自己的写法（如 -inkscape-stroke），浏览器不认
            style = ";".join(p for p in el.attrib["style"].split(";") if p.strip() and not p.strip().startswith("-inkscape-"))
            if style:
                el.set("style", style)
            else:
                del el.attrib["style"]
        if el.text and not el.text.strip():
            el.text = None
        el.tail = None

    strip(root)
    for child in list(root):
        if child.tag == f"{{{SVG}}}defs" and len(child) == 0:
            root.remove(child)
    for key in ROOT_DROP:
        root.attrib.pop(key, None)
    return root


def find_edge():
    for path in EDGE:
        if path.is_file():
            return path
    sys.exit("找不到 Edge（msedge.exe）：要用它量图形的范围")


def measure(roots):
    """用 Edge 无界面模式打开一个放着所有图标的网页，量出每个图标实际画到的范围 {编号: (x, y, 宽, 高)}，单位和 viewBox 一样。"""
    blocks = "".join(f'<div data-key="{key}">{ET.tostring(root, encoding="unicode")}</div>' for key, root in roots.items())
    script = ("var r=[];document.querySelectorAll('div[data-key]').forEach(function(d){"
              "var b=d.querySelector('svg').getBBox();r.push(d.dataset.key+'='+[b.x,b.y,b.width,b.height].join(','))});"
              "document.getElementById('out').textContent=r.join(';');")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "measure.html"
        page.write_text(f'<!doctype html><meta charset="utf-8"><body>{blocks}<pre id="out"></pre><script>{script}</script>',
                        encoding="utf-8")
        result = subprocess.run([str(find_edge()), "--headless", "--disable-gpu", f"--user-data-dir={Path(tmp) / 'edge'}",
                                 "--dump-dom", page.as_uri()], capture_output=True, text=True, encoding="utf-8", timeout=60)
    found = re.search(r'<pre id="out">([^<]*)</pre>', result.stdout)
    if not found or not found.group(1):
        sys.exit("Edge 没有量出图标的范围")
    boxes = {}
    for item in found.group(1).split(";"):
        key, numbers = item.split("=")
        boxes[key] = tuple(float(v) for v in numbers.split(","))
    return boxes


def number(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")


def square_view_box(box):
    """以图形为中心的正方形，四周各留 PAD。"""
    x, y, w, h = box
    side = max(w, h) * (1 + 2 * PAD)
    return " ".join(number(v) for v in (x + w / 2 - side / 2, y + h / 2 - side / 2, side, side))


def main():
    legends = json.loads((CONFIG_DIR / "legends.json").read_text(encoding="utf-8"))["legends"]
    by_en = {info["en"].lower(): key for key, info in legends.items()}
    license_text = (SRC_DIR / LICENSE).read_text(encoding="utf-8")

    roots = {}
    for path in sorted(SRC_DIR.glob("*.svg")):
        key = legend_key(path.name, by_en)
        if key is None:
            print(f"跳过 {path.name}：config/legends.json 里没有这个英文名的传奇")
        elif path.name not in license_text:
            print(f"跳过 {path.name}：{LICENSE} 里还没写它的许可")
        else:
            roots[key] = clean(path)

    boxes = measure(roots)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.svg"):  # 删掉旧的，免得留下已经不用的图标
        old.unlink()
    total = 0
    for key, root in roots.items():
        root.set("viewBox", square_view_box(boxes[key]))
        out = OUT_DIR / f"{key}.svg"
        out.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
        total += out.stat().st_size
    shutil.copyfile(SRC_DIR / LICENSE, OUT_DIR / LICENSE)

    print(f"生成 {len(roots)} 个图标到 {OUT_DIR}（共 {total / 1024:.0f} KB），许可文件已复制")
    missing = [info["zh"] for key, info in legends.items() if key not in roots]
    if missing:
        print("这些传奇还没有图标（面板上只显示名字）：" + "、".join(missing))


if __name__ == "__main__":
    main()
