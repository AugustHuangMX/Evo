"""比较几份 Evo 日志（evo.log）里识别有多快（开发工具；2026-10-27 查笔记本玩家"提示音很慢"时写的）。

用法：python tools/log_speed.py 日志1 [日志2 ...]
例如：python tools/log_speed.py "E:\\Evo Tracker Data\\logs\\evo.log" bugs_test\\0010\\evo_logs\\evo.log

日志每一行的开头是写日志的时间，方括号里是那一帧截图的时间，两者之差就是读那一帧用了多久（精确到秒）。每份日志打印：
- 读对局中存下的右上角用了几秒（以前一局结束时就读，日志写"对局结束（…）"；2026-10-27 起回到大厅、下一局开局时才读，
  写"对局中的数据（… 结束，…）"）
- 总结页从读到的那一帧到存下用了几秒；存了几次，其中几次没读完就离开了（"总结页停留太短"）
- 存下以后到响"总结页读完了"（读队友、名字）用了几秒
- 响了"对局结束"的局里，后来响了"总结页读完了"的有几局，中间隔了几秒
- 对局中记录程序的 CPU；"本次记录"里识别平均每次几秒（2026-10-27 起才有）
"""

import re
import statistics
import sys
from datetime import datetime

LINE = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) (?:\[(\d\d:\d\d:\d\d)\] )?(.*)$")


def spread(values, unit="秒"):
    if not values:
        return "-"
    return f"中位 {statistics.median(values):g} {unit}（{min(values):g}–{max(values):g}），{len(values)} 次"


def analyse(path):
    top, save, after, gap, cpu, per_call = [], [], [], [], [], []
    saves = short = ends = 0
    last_save = end_at = None  # 最近存下的总结页 (帧时间, 写日志时间)；这一局"对局结束"响的时刻
    for raw in open(path, encoding="utf-8", errors="replace"):
        m = LINE.match(raw.rstrip("\n").lstrip("\ufeff"))
        if not m:
            continue
        day, logged, frame, text = m.groups()
        at = datetime.strptime(f"{day} {logged}", "%Y-%m-%d %H:%M:%S")
        shot = datetime.strptime(f"{day} {frame}", "%Y-%m-%d %H:%M:%S") if frame else None
        late = (at - shot).total_seconds() % 86400 if shot else None  # 跨午夜也对
        if shot and text.startswith(("对局结束（", "对局中的数据（")):
            top.append(late)
        elif shot and re.match(r"第 \d+ 局（编号", text):
            saves += 1
            save.append(late)
            last_save = (frame, at)
        elif "总结页停留太短" in text:
            short += 1
        elif text.startswith("提示音：对局结束"):
            ends += 1
            end_at = at
        elif text.startswith("提示音：总结页读完了"):
            if last_save and last_save[0] == frame:
                after.append((at - last_save[1]).total_seconds())
            if end_at:
                gap.append((at - end_at).total_seconds())
                end_at = None
        elif "进入开局画面" in text or "进入大厅" in text:
            end_at = None
        elif text.startswith("占用") and "对局中" in text:
            cpu.append(int(re.search(r"CPU 平均 (\d+)%", text)[1]))
        elif text.startswith("本次记录") and (found := re.search(r"平均每次 ([\d.]+) 秒", text)):
            per_call.append(float(found[1]))
    print(f"== {path}")
    print(f"  读对局中存下的右上角：{spread(top)}")
    print(f"  总结页（从读到的那一帧到存下）：{spread(save)}；存了 {saves} 次，其中没读完就离开 {short} 次")
    print(f"  存下以后到响'总结页读完了'（读队友、名字）：{spread(after)}")
    print(f"  响了'对局结束' {ends} 局，后来响了'总结页读完了' {len(gap)} 局，中间隔了：{spread(gap)}")
    print(f"  对局中 CPU：{spread(cpu, '% 个核')}")
    print(f"  识别平均每次：{spread(per_call)}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print(__doc__)
        return
    for path in sys.argv[1:]:
        analyse(path)


if __name__ == "__main__":
    main()
