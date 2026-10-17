"""开发脚本（tools/ 里的）用：把程序代码所在的 code/ 加进 Python 找模块的路径，后面才能 import 程序代码。
用法：在 import 程序代码之前写 import code_path（类似 Stata 的 adopath + 文件夹）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 项目文件夹
CODE = ROOT / "code"

if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))
