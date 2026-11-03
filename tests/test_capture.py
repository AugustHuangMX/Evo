"""截屏（capture.py）：dxcam 建不起来时怎么办（2026-11-03，几位玩家的 Evo"一打开 Apex 就识别不到了"）。

- 报 DXGI_ERROR_UNSUPPORTED（双显卡的笔记本上 Evo 跑在独显上）：把 Evo.exe 设成「节能」（别的图形设置照旧），
  记录程序抛出 NeedRestart，托盘程序马上新开一个（只马上重来一次）；已经是「节能」了还不行，改用 mss
- 别的原因、用源码运行、图形设置写不进去：直接改用 mss
- mss 截到的和 dxcam 一样（主显示器、BGR、尺寸），没有 OpenCV 时（测试工具）结果也一样
- 记录程序：先建截屏对象，要重来时不留空的截图文件夹；用 mss 时照常记录，日志里写着截法
- 测试工具：一样换 mss，诊断信息里写着截法和图形设置
图形设置全部写在测试用的注册表位置（HKCU\\Software\\Evo-test-gpu），跑完删掉，不碰真正的图形设置。

运行：python tests/test_capture.py
"""

import queue
import sys
import threading
import winreg
from pathlib import Path
from unittest import mock

import fake_game
from fake_game import BLACK, ROOT, report  # 要在项目代码之前导入（设置临时数据文件夹）

import dxcam  # noqa: E402
import numpy as np  # noqa: E402
from comtypes import COMError  # noqa: E402

import capture  # noqa: E402
import evo_app  # noqa: E402
import live_collect  # noqa: E402
import tester_capture  # noqa: E402
import tester_info  # noqa: E402
from paths import CAPTURES_DIR  # noqa: E402

TEST_KEY = r"Software\Evo-test-gpu"
capture.GPU_PREFS_KEY = TEST_KEY
EXE = ROOT / "_gpu_test" / "Evo.exe"  # 不用真的存在
# 两位玩家日志里一模一样的错
UNSUPPORTED = COMError(-2005270524, "指定的设备接口或功能级别在此系统上不受支持。", (None, None, None, 0, None))


def remove_test_key():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_KEY)
    except FileNotFoundError:
        pass


def set_value(text):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, TEST_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, str(EXE), 0, winreg.REG_SZ, text)


def read_value():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
        return winreg.QueryValueEx(key, str(EXE))[0]


def open_with(error, restart=True, exe=EXE):
    """dxcam 建截屏对象时报 error（None = 不报错），返回 (截屏对象, 说明, 日志, 抛出的 NeedRestart 或 None)。"""
    logs = []
    fake = object()
    create = mock.Mock(side_effect=error) if error is not None else mock.Mock(return_value=fake)
    with mock.patch.object(dxcam, "create", create):
        try:
            camera, notes = capture.open_camera(log=logs.append, restart=restart, exe=exe)
        except capture.NeedRestart as e:
            return None, None, logs, e
    if camera is fake:
        camera = "dxcam"
    return camera, notes, logs, None


def rules_checks():
    out = {}
    out["错误码：玩家日志里的 -2005270524 就是 DXGI_ERROR_UNSUPPORTED"] = (
        capture.hresult(UNSUPPORTED) == capture.DXGI_ERROR_UNSUPPORTED)
    out["错误码：别的错误不算"] = capture.hresult(RuntimeError("坏了")) is None and capture.hresult(ValueError()) is None
    cases = {"": "GpuPreference=1;", "GpuPreference=2;": "GpuPreference=1;",
             "AppStatus=4096;": "AppStatus=4096;GpuPreference=1;",
             "GpuPreference=2;SwapEffectUpgradeEnable=1;": "GpuPreference=1;SwapEffectUpgradeEnable=1;"}
    got = {old: capture.with_power_saving(old) for old in cases}
    out[f"改成「节能」：只改 GpuPreference，别的设置照旧（{got}）"] = got == cases
    out["用源码运行：不改图形设置（python.exe 不归 Evo 管）"] = capture.program_exe() is None
    with mock.patch.object(sys, "frozen", True, create=True):
        out["打包版：改的是 Evo.exe 自己"] = capture.program_exe() == Path(sys.executable)

    remove_test_key()
    out["图形设置：没设过时读出 None"] = capture.gpu_preference(EXE) is None
    set_value("AppStatus=4096;")
    capture.set_power_saving(EXE)
    out[f"图形设置：写进注册表，别的设置还在（{read_value()}）"] = (
        capture.gpu_preference(EXE) == "1" and read_value() == "AppStatus=4096;GpuPreference=1;")
    remove_test_key()
    return out


def open_checks():
    out = {}
    camera, notes, logs, _ = open_with(None)
    out["dxcam 建得起来：照旧用 dxcam，不碰图形设置"] = (
        camera == "dxcam" and notes == {"截法": "dxcam", "dxcam": None, "图形设置": None}
        and capture.gpu_preference(EXE) is None and not logs)

    remove_test_key()
    set_value("GpuPreference=2;")  # 玩家在别处设成了高性能
    _, _, logs, restart = open_with(UNSUPPORTED)
    out["DXGI_ERROR_UNSUPPORTED：把 Evo.exe 设成「节能」，要新开记录程序（NeedRestart）"] = (
        restart is not None and capture.gpu_preference(EXE) == "1")
    out[f"日志里写明改了什么、原来是什么（{logs}）"] = (
        len(logs) == 1 and "设成「节能」" in logs[0] and "原来是高性能" in logs[0] and "显示卡" in logs[0])

    camera, notes, logs, restart = open_with(UNSUPPORTED)
    out["已经是「节能」还是这个错：改用 mss，不再要求重来"] = (
        restart is None and isinstance(camera, capture.MssCamera) and notes["截法"] == "mss"
        and "已经是「节能」" in notes["图形设置"] and "-2005270524" in notes["dxcam"])
    out[f"改用 mss 时日志里写着原因（{logs}）"] = len(logs) == 1 and "改用 mss" in logs[0] and "已经是「节能」" in logs[0]
    camera.release()

    remove_test_key()
    camera, notes, logs, restart = open_with(UNSUPPORTED, restart=False)
    out[f"测试工具（restart=False）：设成「节能」、这次先用 mss（{notes['图形设置']}）"] = (
        restart is None and isinstance(camera, capture.MssCamera) and capture.gpu_preference(EXE) == "1"
        and "下次打开生效" in notes["图形设置"])
    camera.release()

    remove_test_key()
    camera, notes, logs, restart = open_with(RuntimeError("测试：dxcam 坏了"))
    out["别的原因建不起来：直接改用 mss，不碰图形设置"] = (
        isinstance(camera, capture.MssCamera) and notes["图形设置"] is None and capture.gpu_preference(EXE) is None
        and "测试：dxcam 坏了" in notes["dxcam"])
    camera.release()

    camera, notes, logs, restart = open_with(UNSUPPORTED, exe=None)
    out["用源码运行时报这个错：不改图形设置，直接改用 mss"] = (
        restart is None and isinstance(camera, capture.MssCamera) and notes["图形设置"] == "用源码运行，不改图形设置")
    camera.release()

    with mock.patch.object(capture, "set_power_saving", side_effect=PermissionError("被拦住了")):
        camera, notes, logs, restart = open_with(UNSUPPORTED)
    out[f"图形设置写不进去（比如被安全软件拦住）：改用 mss（{notes['图形设置']}）"] = (
        restart is None and isinstance(camera, capture.MssCamera) and "写不进去" in notes["图形设置"])
    camera.release()
    with mock.patch.object(capture, "set_power_saving"):  # 写了，读回来还是没设过
        camera, notes, logs, restart = open_with(UNSUPPORTED)
    out["写了没生效：改用 mss，不要求重来（免得来回重启）"] = (
        restart is None and isinstance(camera, capture.MssCamera) and "没生效" in notes["图形设置"])
    camera.release()
    remove_test_key()
    return out


def mss_checks():
    """真的截：mss 和 dxcam 截的是同一个主显示器、同样的格式。"""
    out = {}
    real = dxcam.create(output_color="BGR", processor_backend="numpy")
    frame = None
    for _ in range(20):  # dxcam 刚建好时可能还没有画面
        frame = real.grab()
        if frame is not None:
            break
    real.release()
    cam = capture.MssCamera()
    shot = cam.grab()
    out[f"mss 截主显示器，尺寸和 dxcam 一样（{cam.width}x{cam.height}，dxcam {None if frame is None else frame.shape}）"] = (
        frame is not None and shot.shape == frame.shape and (cam.width, cam.height) == (frame.shape[1], frame.shape[0]))
    # 屏幕一直在变（两张不是同一时刻截的），不比内容；颜色顺序拿同一张截图和 mss 自己给的 RGB 比
    raw = cam._sct.grab(cam._mon)
    rgb = np.frombuffer(raw.rgb, np.uint8).reshape(raw.height, raw.width, 3)
    with mock.patch.object(cam._sct, "grab", lambda mon: raw):
        with_cv2 = cam.grab()
        cam._cv2 = None  # 测试工具不带 OpenCV
        with_numpy = cam.grab()
    out["mss 截到的和 dxcam 一样是 BGR（同一张截图，和 mss 给的 RGB 反过来一模一样）"] = (
        with_cv2.dtype == np.uint8 and with_cv2.flags["C_CONTIGUOUS"] and (with_cv2 == rgb[:, :, ::-1]).all())
    out["没有 OpenCV 时（测试工具）转出来的一模一样"] = with_numpy.shape == with_cv2.shape and (with_numpy == with_cv2).all()
    cam.release()
    return out


def recorder_checks(tmp):
    out = {}
    live = CAPTURES_DIR / "live"
    before = set(live.iterdir()) if live.exists() else set()
    logs = []
    with mock.patch.object(dxcam, "create", side_effect=UNSUPPORTED), mock.patch.object(capture, "program_exe", lambda: EXE):
        try:
            live_collect.collect(db_path=tmp / "restart.db", stop=threading.Event(), log=logs.append)
            restarted = False
        except capture.NeedRestart:
            restarted = True
    after = set(live.iterdir()) if live.exists() else set()
    out["记录程序：设成「节能」后要求新开一个，不留空的截图文件夹（以前每 60 秒多一个）"] = (
        restarted and after == before and capture.gpu_preference(EXE) == "1")

    q = queue.Queue()
    with mock.patch.object(live_collect, "collect", side_effect=capture.NeedRestart("截屏：图形设置要新开的记录程序才生效")):
        evo_app.collect_process(threading.Event(), q)
    out["记录子进程把\"要重来\"告诉托盘程序（不当作出错）"] = q.get_nowait()[0] == "restart"

    class Gone:  # 已经退出的记录子进程
        def is_alive(self):
            return False

    tray_logs = []
    rec = evo_app.Recorder(log=tray_logs.append)
    q.put(("restart", "截屏：图形设置要新开的记录程序才生效"))
    rec._read(Gone(), q, {"saved": 0})
    out[f"托盘程序：马上重新开始记录，不等 60 秒（{tray_logs}）"] = (
        rec._last_error == 0.0 and rec._wake.is_set() and "马上重新开始记录" in tray_logs[-1])
    q.put(("restart", "截屏：图形设置要新开的记录程序才生效"))
    rec._read(Gone(), q, {"saved": 0})
    out["再要重来：按出错算，等 60 秒（免得来回重启）"] = rec._last_error > 0 and "秒后重试" in tray_logs[-1]

    # 已经是「节能」了还不行：记录程序改用 mss，照常记录（假摄像头代替 mss，假时钟）
    stop = threading.Event()
    camera = fake_game.FakeCamera(list(range(5)), lambda i: BLACK, stop)
    logs = []
    with mock.patch.object(dxcam, "create", side_effect=UNSUPPORTED), mock.patch.object(capture, "program_exe", lambda: EXE), \
            mock.patch.object(capture, "MssCamera", lambda: camera), \
            mock.patch.object(live_collect, "time", fake_game.FakeClock(camera)):
        live_collect.collect(db_path=tmp / "mss.db", stop=stop, log=logs.append)
    text = "\n".join(logs)
    out["记录程序：已经是「节能」还是建不起来，改用 mss 照常截屏、记录"] = (
        camera.i == 5 and "改用 mss" in text and "（截屏：mss）" in text and "本次记录：截屏 5 次" in text)
    remove_test_key()
    return out


def tester_checks():
    out = {}
    logs = []
    with mock.patch.object(dxcam, "create", side_effect=UNSUPPORTED), mock.patch.object(capture, "program_exe", lambda: EXE):
        dxcam_mod, camera, notes = tester_capture.open_camera(logs.append)
    info = tester_info.collect_info(dxcam_mod, camera, notes, [])
    text = tester_info.render_info(info)
    out["测试工具：dxcam 建不起来时一样换 mss，把自己设成「节能」（下次打开生效）"] = (
        isinstance(camera, capture.MssCamera) and notes["截法"] == "mss" and capture.gpu_preference(EXE) == "1")
    out["诊断信息里写着 dxcam 的错、这次的截法、图形设置、全部显卡"] = (
        "-2005270524" in info["dxcam"]["截屏对象"] and "这次的截法：mss" in text and "图形设置：刚把 Evo.exe 设成「节能」" in text
        and "全部显卡（包括没接显示器的）：" in text and f"{camera.width}×{camera.height}" in text)
    camera.release()
    with mock.patch.object(capture, "MssCamera", side_effect=OSError("测试：mss 也坏了")), \
            mock.patch.object(dxcam, "create", side_effect=RuntimeError("测试：dxcam 坏了")):
        _, camera, notes = tester_capture.open_camera(logs.append)
    out["连 mss 也建不起来：测试工具照样能跑（只看进程、存缩略图）"] = camera is None and "mss 也坏了" in notes["错误"]
    remove_test_key()
    return out


def main():
    print("截屏（dxcam 建不起来时：双显卡的笔记本设成节能、改用 mss；记录程序、托盘程序、测试工具）")
    tmp = Path(fake_game._TEMP) / "capture"
    tmp.mkdir(exist_ok=True)
    remove_test_key()
    try:
        checks = {**rules_checks(), **open_checks(), **mss_checks(), **recorder_checks(tmp), **tester_checks()}
    finally:
        remove_test_key()
    return report(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
