"""生成代码地图 docs/代码地图.md：每个文件一行，写它是干什么的，按分工分组。找某个功能的代码在哪时先看那里。

python tools/code_map.py           重新生成（加、删、改名文件，或改了文件开头说明的第一句以后）
python tools/code_map.py --check   只检查地图是不是最新的、每个文件都分了组（tests/test_code_map.py 用）

每个文件的那一行取自它自己开头的说明，地图里不另写一份（免得两边对不上）：
- .py：开头的三引号说明；.spec、.toml：开头的 # 注释；.js：// 注释；.sql：-- 注释
- config/ 里的 .json："_说明"
- panel/templates/ 里的 .html、.svg：第一个 {# … #} 注释
只取第一句（到第一个句号），去掉写着日期的括号（"（2026-10-28 用户决定）"这种，地图里用不上），太长的截短。
说明写得不好，就改文件开头的说明，再运行一次。

怎么分组写在下面的 SECTIONS 里。code/ 和 tools/ 的新文件要自己加进去（没加的检查不通过，地图里列在"还没分组"）；
tests/、config/、panel/templates/ 按文件夹自动列出。只列 git 记着的文件和还没提交的新文件
（.gitignore 里的不列：小程序的文件、截图、数据）。
"""

import ast
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP_FILE = ROOT / "docs" / "代码地图.md"
MAX_LEN = 90  # 每行说明最多多少个字，再长就截短

# (二级标题, 三级标题或 None, 条目)。条目是一个列表，里面每一项是：
# - "code/x.py" 或通配 "tests/test_*.py"：每个文件一行，说明取自文件开头
# - ("文件夹/", "说明") 或 ("通配", "说明")：这些文件合成一行，说明写在这里（图片、素材、答案这类没有开头说明的）
# 条目也可以是一段文字（不列文件）
SECTIONS = [
    ("模型", "记录的主循环、截屏、画面比例和语言", [
        "code/live_collect.py", "code/game_detect.py", "code/capture.py", "code/layout.py",
        "code/game_aspect.py", "code/game_lang.py"]),
    ("模型", "认出画面，读结算画面和大厅", [
        "code/screens.py", "code/summary_page.py", "code/teammates.py", "code/xp_page.py",
        "code/eliminated.py", "code/match_start.py", "code/lobby.py", "code/player_name.py"]),
    ("模型", "对局中", ["code/in_match.py", "code/hud.py", "code/legends.py", "code/weapons.py"]),
    ("模型", "合并、校准、段位、赛季、留截图", [
        "code/post_match.py", "code/calibration.py", "code/ranks.py", "code/seasons.py", "code/screenshots.py"]),
    ("模型", "游戏里的数据页（录入历史赛季数据）", [
        "code/season_stats.py", "code/season_rules.py", "code/season_import.py"]),
    ("模型", "规则（config/）", [
        "config/*.json", "config/lang/*.json",
        ("config/templates/", "参照图（外观检查用）：按画面放；legends/ 传奇头像、weapons/ 武器、hud/ 对局中，"
                              "en/、zh-TW/ 是英文、繁体游戏和简体不一样的那些")]),
    ("引擎", None, ["code/ocr.py"]),
    ("页面", "程序", [
        "code/panel_app.py", "code/panel_seasons.py", "code/app_window.py", "code/radar.py",
        "code/global_stats.py", "code/map_rotation.py"]),
    ("页面", "模板（panel/templates/）", ["panel/templates/*.html", "panel/templates/share/*.svg"]),
    ("页面", "图片、字体", [
        ("panel/static/", "样式表 style.css、字体，图标（legends/ 传奇头像由 tools/legend_icons.py 生成、ranks/ 段位、"
                          "brands/ 关注我、partner/ 派圈助手、support/ 收款码）"),
        ("panel/prototype/", "最早的界面设计稿（只作参考，程序不用）")]),
    ("底座", None, [
        "code/evo_app.py", "code/paths.py", "code/source_run.py", "code/storage.py", "code/app_settings.py",
        "code/data_move.py", "code/autostart.py", "code/process_info.py", "code/icons.py", "code/sounds.py",
        "code/webview2.py", "code/update_check.py", "code/update_patch.py", "code/updater.py", "code/mirrorchyan.py",
        "code/telemetry.py", "code/notice_check.py",
        ("server/", "匿名统计的服务器（Cloudflare Worker + D1）：worker.js 收数据、给汇总，schema.sql 建表，"
                    "wrangler.toml 部署配置")]),
    ("测试工具（给玩家的 Evo测试工具.exe）", None, [
        "code/evo_tester.py", "code/tester_window.py", "code/tester_capture.py", "code/tester_info.py",
        "code/tester_projects.py", "code/tester_answers.py"]),
    # 只在本机、不进 git 的文件（.gitignore 里）扫不到，固定写一段
    ("小程序", None, "只在本机、不进 git（见 docs/小程序.md）：`miniprogram/`，和 Evo 里绑定、上传战绩的 "
                     "`code/cloud_sync.py`、`code/panel_cloud.py`、`code/qr.py`、`panel/templates/_cloud.html`、"
                     "`tests/test_cloud_sync.py`、`tools/cloud_ping.py`"),
    ("开发用的脚本（tools/）", "打包、安装、发布", [
        "tools/build.py", "tools/evo.spec", "tools/install.py", "tools/release.py", "tools/publish.py",
        "tools/cos.py", "tools/notice.py", "tools/build_tester.py", "tools/tester.spec", "tools/code_path.py"]),
    ("开发用的脚本（tools/）", "做素材", [
        "tools/make_icon.py", "tools/make_sounds.py", "tools/legend_icons.py", "tools/weapon_templates.py",
        "tools/feature_cards.py", "tools/window_shots.py"]),
    ("开发用的脚本（tools/）", "查问题、看数据", [
        "tools/ocr_probe.py", "tools/frame_capture.py", "tools/legend_capture.py", "tools/log_speed.py",
        "tools/diag_report.py", "tools/stats_report.py", "tools/demo_data.py", "tools/demo_seasons.py",
        "tools/code_map.py"]),
    ("测试（tests/）", None, [
        "tests/run_all.py", "tests/fake_game.py", "tests/test_*.py",
        ("tests/*.csv", "正确答案（整晚重放、数据页、武器）；队友名字的答案只在本机")]),
    ("其他", None, [
        ("assets/", "素材原图：icon/ 图标、legends/ 传奇头像（各自的许可在 LICENSE.txt）、ranks/ 段位、sounds/ 提示音"),
        ("release/", "发版用：更新内容.txt、使用说明.txt（随安装包）、做图流程.md + 宣传图.json（新功能配图）"),
        ("docs/", "说明文档（什么时候读哪一份见 CLAUDE.md），images/ 是 README 的截图"),
        ("claude/", "进度和待办：progress.md、todo.md、progress_archive.md"),
        ("README.md", "GitHub 上的介绍"), ("PRIVACY.md", "隐私说明"), ("LICENSE", "AGPL-3.0 许可证"),
        ("CLAUDE.md", "项目说明（给 Claude 的）"), (".gitignore", "不进 git 的文件（每一项为什么不进写在旁边）")]),
]

HEADER = """# 代码地图

每个文件是干什么的，一行一个，按分工分组（模型、引擎、页面、底座……见 CLAUDE.md"用词和分工"）。找某个功能的代码在哪时先看这里。

这个文件是 `python tools/code_map.py` 生成的，**不要手改**：每行取自那个文件开头的说明（第一句），说明写得不好就改文件开头。
加、删、改名文件，或者改了文件开头说明的第一句以后，重新运行一次；`python tests/run_all.py` 会检查它是不是最新的。
"""


def git_files():
    """git 记着的文件 + 还没提交的新文件（不含 .gitignore 里的），斜杠分隔的相对路径。"""
    out = subprocess.run(["git", "-c", "core.quotePath=false", "ls-files", "-z", "--cached", "--others",
                          "--exclude-standard"], cwd=ROOT, capture_output=True, check=True).stdout
    return sorted({p for p in out.decode("utf-8").split("\0") if p and (ROOT / p).is_file()})


def matches(pattern, path):
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return fnmatch.fnmatchcase(path, pattern) and path.count("/") == pattern.count("/")


def raw_description(path):
    """文件开头的说明（整段），没有返回 None。"""
    text = (ROOT / path).read_text(encoding="utf-8-sig")
    suffix = Path(path).suffix
    if suffix == ".py":
        try:
            return ast.get_docstring(ast.parse(text))
        except SyntaxError:
            return None
    if suffix == ".json":
        try:
            return json.loads(text).get("_说明")
        except (ValueError, AttributeError):
            return None
    if suffix in (".html", ".svg"):
        found = re.search(r"\{#-?(.*?)-?#\}", text, re.S)
        return found.group(1) if found else None
    mark = {".spec": "#", ".toml": "#", ".js": "//", ".sql": "--"}.get(suffix)
    if mark:
        lines = []
        for line in text.splitlines():
            if not line.startswith(mark):
                break
            lines.append(line[len(mark):].strip())
        return "\n".join(lines) or None
    return None


def drop_dated(text):
    """去掉写着日期的括号："（2026-10-28 用户决定）""(0.3.7 起，2026-10-22 …)"，括号套括号也行。"""
    pairs = {"（": "）", "(": ")"}
    out, i = [], 0
    while i < len(text):
        if text[i] in pairs:
            depth, j = 0, i
            while j < len(text):
                if text[j] in pairs:
                    depth += 1
                elif text[j] in pairs.values():
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            inside = text[i:j + 1]
            if depth == 0 and re.search(r"20\d\d-\d\d-\d\d", inside):
                i = j + 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def first_sentence(text):
    paragraph = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    joined = ""
    for line in paragraph.splitlines():
        line = line.strip()
        if joined and re.match(r"[A-Za-z0-9]", line) and re.search(r"[A-Za-z0-9,.;:)]$", joined):
            joined += " "
        joined += line
    sentence = drop_dated(joined).split("。")[0]
    sentence = re.sub(r"\s{2,}", " ", sentence).strip(" ，、：；")
    if len(sentence) > MAX_LEN:
        cut = max(sentence.rfind(c, 0, MAX_LEN) for c in "，、；：")
        sentence = sentence[:cut if cut > MAX_LEN // 2 else MAX_LEN] + "……"
    return sentence


def link(path):
    """从 docs/ 链到这个文件（VS Code 里点得开）：文件只显示文件名，文件夹显示整个路径，通配写成代码。"""
    if any(c in path for c in "*?"):
        return f"`{path}`"
    target = "../" + path
    if " " in target:
        target = f"<{target}>"
    return f"[{path if path.endswith('/') else path.split('/')[-1]}]({target})"


def section_lines(items, files, covered, problems):
    """一组条目 → 地图里的行；用到的文件记进 covered，有问题记进 problems。"""
    lines = []
    for item in items:
        if isinstance(item, tuple):  # 几个文件合成一行，说明写在 SECTIONS 里
            pattern, text = item
            hit = [f for f in files if matches(pattern, f)]
            if not hit:
                problems.append(f"地图里写了 {pattern}，但没有这样的文件（改 tools/code_map.py 的 SECTIONS）")
            covered.update(hit)
            lines.append(f"- {link(pattern)}：{text}")
            continue
        hit = [f for f in files if matches(item, f) and f not in covered]
        if not hit and not any(c in item for c in "*?"):
            problems.append(f"地图里写了 {item}，但没有这个文件（删了或改名了：改 tools/code_map.py 的 SECTIONS）")
        for f in hit:
            covered.add(f)
            raw = raw_description(f)
            sentence = first_sentence(raw) if raw else ""
            if not sentence:
                problems.append(f"{f} 开头没有说明（写一段：这个文件是干什么的）")
            lines.append(f"- {link(f)}：{sentence or '（没有说明）'}")
    return lines


def build():
    """返回 (地图的文字, 问题列表)。问题：没分组的文件、写了但已经没有的文件、没有开头说明的文件。"""
    files = git_files()
    covered, problems, blocks = set(), [], [HEADER.rstrip("\n")]
    last_h2 = None
    for h2, h3, items in SECTIONS:
        if h2 != last_h2:
            blocks.append(f"## {h2}")
            last_h2 = h2
        if h3:
            blocks.append(f"### {h3}")
        if isinstance(items, str):  # 一段固定的文字
            blocks.append(items)
        else:
            blocks.append("\n".join(section_lines(items, files, covered, problems)))
    rest = [f for f in files if f not in covered]
    if rest:
        blocks += ["## 还没分组", "\n".join(f"- {link(f)}" for f in rest)]
        problems += [f"{f} 还没分组（加到 tools/code_map.py 的 SECTIONS 里）" for f in rest]
    return "\n\n".join(blocks) + "\n", problems


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    check = "--check" in sys.argv[1:]
    try:
        text, problems = build()
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"读不到 git 的文件列表：{e}")
        return 2
    for p in problems:
        print(f"  {p}")
    if check:
        current = MAP_FILE.read_text(encoding="utf-8") if MAP_FILE.exists() else ""
        if current != text:
            print("  docs/代码地图.md 不是最新的：运行 python tools/code_map.py")
            return 1
        return 1 if problems else 0
    MAP_FILE.write_text(text, encoding="utf-8", newline="\n")
    print(f"写好了 {MAP_FILE.relative_to(ROOT)}（{text.count(chr(10) + '- ')} 行）")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
