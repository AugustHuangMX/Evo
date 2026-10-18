"""运行全部测试：python tests/run_all.py（约 1 分钟）

什么时候跑：改了识别（ocr.py、screens.py、summary_page.py、teammates.py、xp_page.py、match_start.py、eliminated.py、legends.py、player_name.py、hud.py、lobby.py）、记录程序（live_collect.py、in_match.py、post_match.py、calibration.py、screenshots.py）、数据库（storage.py）、段位（ranks.py）、面板（panel_app.py）
或 config/ 里的识别规则之后；新赛季游戏更新以后。全部 ✅ 才说明没改坏。

测试用的截图不提交到仓库（太大，而且有其他玩家的名字和 ID），只在开发者电脑上项目文件夹的 captures/ 里；
找不到截图的测试会显示"跳过"。测试文件只 import 项目代码，项目代码不 import 测试，所以测试不会被打进安装包。
"""

import time

import fake_game  # noqa: F401  要在项目代码之前导入（设置临时数据文件夹）
import test_accounts
import test_eliminated
import test_in_match
import test_legends
import test_light
import test_player_name
import test_ranks
import test_replay
import test_screenshots
import test_source_run
import test_summary_page
import test_teammates

TESTS = {"总结页停留": test_summary_page, "轻量（按需加载模型）": test_light, "整晚重放": test_replay,
         "传奇识别": test_legends, "玩家名（区分账号）": test_player_name, "多账号（对照表和弹窗）": test_accounts,
         "队友（读卡片、队友表、战绩页）": test_teammates, "截图留不留": test_screenshots, "段位": test_ranks,
         "源码运行（战绩记进安装版）": test_source_run, "淘汰画面（名次入库、和总结页/经验值页核对）": test_eliminated,
         "0.3.0 新流程（对局中、冠军、大厅校准、各来源优先级）": test_in_match}


def main():
    start = time.perf_counter()
    results = {}
    for name, test in TESTS.items():
        results[name] = test.main()
        print()
    print(f"===== 结果（用时 {time.perf_counter() - start:.0f} 秒）=====")
    for name, ok in results.items():
        print(f"  {'⏭ 跳过' if ok is None else ('✅ 通过' if ok else '❌ 没通过')}  {name}")
    return all(ok is not False for ok in results.values())


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
