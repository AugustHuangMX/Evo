"""把打包好的 Evo 安装到固定位置，并在桌面和开始菜单建快捷方式。先运行 python build.py，再运行：

    python install.py "E:\\Evo Tracker" --data "E:\\Evo Tracker Data"

- 把 dist/Evo 整个复制到安装文件夹。以前装过的话先删掉旧的程序文件——只删里面有 Evo.exe 的文件夹，免得误删别的东西
- --data：数据文件夹（数据库、截图、日志）。写进安装文件夹里的 data_location.txt，Evo.exe 启动时据此找数据；
  不写就用 %LOCALAPPDATA%\\Evo。安装只动程序文件，不碰数据文件夹
- 在桌面和开始菜单建"Evo"快捷方式
- 在项目里记下数据文件夹（installed_data_location.txt，不提交）：用源码运行 evo_app.py 时，战绩也记进这个数据文件夹（见 source_run.py）
- Evo 正在运行时不安装（文件被占用，也免得装到一半）
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from game_detect import process_names
from source_run import POINTER_FILE

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "Evo"


def make_shortcut(link, target, folder):
    """用 Windows 自带的 WScript.Shell 建快捷方式（.lnk）。"""
    ps = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}'); "
          f"$s.TargetPath = '{target}'; $s.WorkingDirectory = '{folder}'; $s.IconLocation = '{target},0'; "
          "$s.Description = 'Evo：本地战绩记录 for Apex Legends'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def special_folder(name):
    """桌面（Desktop）、开始菜单程序（Programs）这些文件夹的实际位置（桌面可能被 OneDrive 挪过）。"""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{name}')"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description="安装打包好的 Evo")
    parser.add_argument("target", type=Path, help="安装到哪个文件夹，如 E:\\Evo Tracker")
    parser.add_argument("--data", type=Path, help="数据文件夹，如 E:\\Evo Tracker Data（不写就用 %%LOCALAPPDATA%%\\Evo）")
    args = parser.parse_args()
    target = args.target.resolve()
    data = args.data.resolve() if args.data else None

    if not (DIST / "Evo.exe").is_file():
        sys.exit("还没有打包：先运行 python build.py")
    if target == ROOT or ROOT in target.parents:
        sys.exit(f"不能装在项目文件夹 {ROOT} 里面")
    if data is not None and (data == target or target in data.parents or data in target.parents):
        sys.exit("数据文件夹不能和安装文件夹是同一个，也不能互相包含（重新安装会删掉安装文件夹）")
    if target.exists() and any(target.iterdir()) and not (target / "Evo.exe").is_file():
        sys.exit(f"{target} 里已经有别的东西（而且不是 Evo），不安装，免得误删")
    if "evo.exe" in process_names():
        sys.exit("Evo 正在运行：请先右键托盘图标 → 退出，再安装")

    if target.exists():
        print(f"删除旧版本：{target}")
        shutil.rmtree(target)
    print(f"复制 {DIST} → {target}")
    shutil.copytree(DIST, target)
    if data is not None:
        (target / "data_location.txt").write_text(f"{data}\n", encoding="utf-8")
        data.mkdir(parents=True, exist_ok=True)
        print(f"数据文件夹：{data}（写进了 data_location.txt）")

    data_dir = data or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Evo"  # 和 paths.py 里打包版的规则一样
    POINTER_FILE.write_text(f"{data_dir}\n", encoding="utf-8")
    print(f"用源码运行 evo_app.py 时，战绩也记进 {data_dir}（写进了项目里的 {POINTER_FILE.name}）")

    exe = target / "Evo.exe"
    for folder in ("Desktop", "Programs"):
        link = special_folder(folder) / "Evo.lnk"
        make_shortcut(link, exe, target)
        print(f"快捷方式：{link}")
    print(f"\n安装完成：{exe}")


if __name__ == "__main__":
    main()
