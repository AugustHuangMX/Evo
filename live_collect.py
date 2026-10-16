"""实战采集：打游戏时在后台运行，自动识别每一局的总结页并存进数据库。

每秒截一次屏（只截屏，不碰游戏），只检查几个小区域里的关键字：
- 总结页（中间卡片上有"击杀/助攻/击倒"）：改成每 0.3 秒检查一次。名次、击杀/助攻/击倒、伤害都读到了，
  并且连续两次相同（名次会晚 1–2 秒滑进来，数字也可能有动画），就保存截图、把这一局写进数据库。
  如果还没读全就离开了总结页，或者等了 10 秒还没读全，就用读得最全的一次存下，标记"待确认"。
  每局只存一次，在总结页和经验值页之间来回切换也不会重复存
- 经验值页：离开时识别最后一帧（数字有动画，最后一帧才是最终结果），补上这一局的 RP、段位，名次缺的话也补上。
  没有总结页的局（第 2 名、冠军）只靠经验值页单独记一局。怎么归到同一局见 post_match.py
- 开局画面：读本局的模式和地图（见 match_start.py），两项都读到就不再读
- 经验值页、开局画面、大厅（见 config/screens.json）：进入时存一张截图，离开前最后一帧再存一张。
  开局画面用来分隔每一局；以后用这些截图做模式、地图的识别
- 其他画面：什么都不做（大部分时间都是这样）

数据存在数据文件夹（见 paths.py；用源码运行时就是项目文件夹）的 data/evo.db（用 python storage.py 查看），
截图存在 captures/live/<开始时间>/。
测试阶段截图都保留，用来做测试集；正式版只保留"待确认"的截图（原则 2）。

运行：python live_collect.py
停止：在终端按 Ctrl+C，或到达 --minutes 设定的时长后自动停止
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import cv2
import dxcam

import storage
from apex_capture_test import set_low_priority
from ocr_probe import create_engine, memory_mb
from match_start import MATCH_START_CONFIG_PATH, read_match_start
from paths import CAPTURES_DIR, CONFIG_DIR
from post_match import PostMatch
from summary_demo import CONFIG_PATH, KEY_FIELDS, SMALL_CROP_PARAMS, crop, read_summary, show, summary_to_match
from xp_page import XP_CONFIG_PATH, read_xp

SCREENS_PATH = CONFIG_DIR / "screens.json"
LATE_FIELDS = ("placement", "squad_kills")  # 总结页上晚 1–2 秒才滑进来的项


def is_summary(engine, frame, cfg):
    """当前画面是不是总结页：识别一个小区域，看关键字在不在。"""
    spec = cfg["detect"]
    result = engine(crop(frame, spec["roi"], cfg))
    text = "".join(result.txts or ())
    return all(k in text for k in spec["keywords"])


def which_screen(engine, frame, screens):
    """当前画面是不是 config/screens.json 里的某个画面，是的话返回它的名字（如 "xp"），都不是返回 None。"""
    for name, spec in screens["screens"].items():
        result = engine(crop(frame, spec["roi"], {**screens, **spec}))
        text = "".join(result.txts or ())
        if all(k in text for k in spec["keywords"]):
            return name
    return None


def same_match(a, b, min_fields=4):
    """两次读数是不是同一局：两边都读到的项全部相同（至少要有 min_fields 项可比）。
    用于在总结页和经验值页之间来回切换时，避免同一局被存好几次。"""
    if a is None or b is None:
        return False
    both = [k for k in a if a[k] is not None and b.get(k) is not None]
    return len(both) >= min_fields and all(a[k] == b[k] for k in both)


def save_png(path, frame):
    cv2.imencode(".png", frame)[1].tofile(path)


def main():
    parser = argparse.ArgumentParser(description="打游戏时自动识别每局的总结页并存进数据库")
    parser.add_argument("--interval", type=float, default=1, help="每隔几秒检查一次（默认 1）")
    parser.add_argument("--fast", type=float, default=0.3, help="在总结页、数字还没存下时，每隔几秒检查一次（默认 0.3）")
    parser.add_argument("--summary-timeout", type=float, default=10,
                        help="在总结页最多等几秒让数字读全，超时就先存下并标记待确认（默认 10）")
    parser.add_argument("--minutes", type=float, default=240, help="最多运行几分钟（默认 240）")
    parser.add_argument("--db", type=Path, default=storage.DB_PATH, help="数据库文件（默认 data/evo.db）")
    args = parser.parse_args()

    set_low_priority()
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    screens = json.loads(SCREENS_PATH.read_text(encoding="utf-8"))
    xp_cfg = json.loads(XP_CONFIG_PATH.read_text(encoding="utf-8"))
    start_cfg = json.loads(MATCH_START_CONFIG_PATH.read_text(encoding="utf-8"))
    run_dir = CAPTURES_DIR / "live" / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    conn = storage.connect(args.db)
    post = PostMatch(conn)

    camera = dxcam.create(output_color="BGR", processor_backend="numpy")
    engine = create_engine(SMALL_CROP_PARAMS)
    print(f"屏幕: {camera.width}x{camera.height}")
    print(f"数据库: {args.db.resolve()}")
    print(f"截图保存到: {run_dir.resolve()}")
    print("开始运行，到总结页时会自动保存。按 Ctrl+C 停止。\n")

    saved = 0
    visit = None  # 这一次停在总结页的情况：开始时间、上一次的读数、读得最全的一次
    done_this_page = False  # 这一次总结页已经存过了
    last_saved = None  # 最近存进数据库的那一局的读数（切到别的页再切回来时，用来认出是同一局）
    on_screen = None  # 现在停在哪个只存截图的画面（经验值页、开局画面、大厅）
    screen_last = None  # 这个画面最近的一帧和时间，离开时存下来
    screen_frames = 0  # 在这个画面已经看到了几帧
    screen_enter_path = None  # 进入这个画面时存的截图
    deadline = time.monotonic() + args.minutes * 60

    def record(results, problems, frame, when, note=None):
        """把一次总结页的读数存进数据库。如果和上一局是同一局（来回切页），就不重复存。"""
        nonlocal saved, last_saved
        values = {name: r["value"] for name, r in results.items()}
        if same_match(values, last_saved):
            print(f"[{when:%H:%M:%S}] 回到了同一局的总结页，不重复保存")
            return
        if note:
            problems = problems + [note]
        image_path = run_dir / f"{saved + 1:02d}_{when:%H%M%S}.png"
        save_png(image_path, frame)
        match = summary_to_match(results, problems, f"{when:%Y-%m-%d %H:%M:%S}", image_path)
        match_id = storage.insert_match(conn, match)
        saved += 1
        last_saved = values
        print(f"[{when:%H:%M:%S}] 第 {saved} 局（编号 {match_id}）：第 {show(values['placement'])} 名，"
              f"击杀/助攻/击倒 {show(values['kda'])}，伤害 {show(values['damage'])}")
        for p in problems:
            print(f"           ⚠ {p}")
        post.on_summary_saved(match_id, values, when)

    def record_best(note):
        """数字没等到读全：用这次停留里读得最全的一次存下（会标记待确认）。
        如果只是名次还没滑进来、其他数字已经连续两次一致，就只标"名次没读到"（经验值页可以补上），不加 note。"""
        _, results, problems, frame, when = visit["best"]
        record(results, problems, frame, when, None if visit["core_stable"] else note)

    def leave_screen():
        """离开某个画面：存下最后一帧（只看到一帧时，进入时已经存过了）；如果是经验值页，识别最后一帧。"""
        if on_screen is None:
            return
        last_frame, last_time = screen_last
        path = screen_enter_path
        if screen_frames > 1:
            path = run_dir / f"{on_screen}_{last_time:%H%M%S}_leave.png"
            save_png(path, last_frame)
            print(f"[{last_time:%H:%M:%S}] 离开{screens['screens'][on_screen]['label']}，已存最后一帧")
        if on_screen == "xp":
            reading, problems = read_xp(engine, last_frame, xp_cfg)
            post.on_xp(reading, problems, path, last_time)

    try:
        while time.monotonic() < deadline:
            tick = time.monotonic()
            on_summary = False
            try:
                frame = camera.grab(new_frame_only=False)
            except Exception as e:  # 切换分辨率、切出游戏等情况，跳过这一次
                print(f"[{datetime.now():%H:%M:%S}] 截屏出错，跳过：{e}")
                frame = None

            if frame is not None:
                now = datetime.now()
                on_summary = is_summary(engine, frame, cfg)

                # ---- 总结页：识别并存进数据库 ----
                if not on_summary:
                    if visit is not None and not done_this_page:
                        record_best("总结页停留太短，名次等数据可能没读全，请核对")
                    visit, done_this_page = None, False
                elif not done_this_page:
                    if visit is None:
                        visit = {"start": time.monotonic(), "last": None, "best": None, "core_stable": False}
                    results, problems = read_summary(engine, frame, cfg)
                    values = {name: r["value"] for name, r in results.items()}
                    filled = sum(v is not None for v in values.values())
                    if visit["best"] is None or filled >= visit["best"][0]:
                        visit["best"] = (filled, results, problems, frame.copy(), now)
                    complete = all(values[k] is not None for k in KEY_FIELDS)
                    # 除名次、小队总击杀以外的数字都读到了，且和上一次相同
                    core = {k: v for k, v in values.items() if k not in LATE_FIELDS}
                    if visit["last"] is not None and all(v is not None for v in core.values()) \
                            and core == {k: v for k, v in visit["last"].items() if k not in LATE_FIELDS}:
                        visit["core_stable"] = True

                    if complete and values == visit["last"]:  # 关键项都读到了，且连续两次相同
                        record(results, problems, frame, now)
                        done_this_page = True
                    elif time.monotonic() - visit["start"] > args.summary_timeout:
                        record_best(f"在总结页等了 {args.summary_timeout:g} 秒，数字仍没读全或不稳定，请核对")
                        done_this_page = True
                    visit["last"] = values

                # ---- 经验值页、开局画面、大厅：存截图；离开经验值页时识别；开局画面表示上一局彻底结束 ----
                screen = None if on_summary else which_screen(engine, frame, screens)
                if screen != on_screen:
                    leave_screen()
                    if screen == "match_start":
                        post.finish()
                    if screen is not None:
                        screen_enter_path = run_dir / f"{screen}_{now:%H%M%S}_enter.png"
                        save_png(screen_enter_path, frame)
                        print(f"[{now:%H:%M:%S}] 进入{screens['screens'][screen]['label']}，已存截图")
                    on_screen, screen_frames = screen, 0
                if screen is not None:
                    screen_last = (frame.copy(), now)
                    screen_frames += 1
                if screen == "match_start" and not post.info_complete():  # 读本局的模式和地图，读到就不再读
                    post.on_match_start(read_match_start(engine, frame, start_cfg), now)

            wait = args.fast if (on_summary and not done_this_page) else args.interval
            time.sleep(max(0, wait - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        print("\n已手动停止。")
    finally:
        if visit is not None and not done_this_page:  # 停止时正停在还没存下的总结页
            record_best("总结页停留太短，名次等数据可能没读全，请核对")
        leave_screen()
        post.finish()
        camera.release()
        conn.close()

    mem_now, mem_peak = memory_mb()
    print(f"\n本次共保存 {saved + post.inserted} 局。内存: 现在 {mem_now:.0f} MB，峰值 {mem_peak:.0f} MB")
    print("查看数据库里的记录: python storage.py")


if __name__ == "__main__":
    main()
