"""运行全部测试：python tests/run_all.py（约 5 分钟）

什么时候跑：改了识别（layout.py、game_aspect.py、game_lang.py、ocr.py、screens.py、summary_page.py、teammates.py、xp_page.py、match_start.py、eliminated.py、legends.py、player_name.py、hud.py、lobby.py）、记录程序（live_collect.py、in_match.py、post_match.py、calibration.py、screenshots.py）、数据库（storage.py）、段位（ranks.py）、面板（panel_app.py、传奇头像 tools/legend_icons.py）、检查和一键更新（update_check.py、updater.py）、窗口和 WebView2 检查（app_window.py、webview2.py）、数据文件夹（paths.py、data_move.py）、开机自启（autostart.py）、提示音（sounds.py、tools/make_sounds.py）、匿名统计（telemetry.py）
或 config/ 里的识别规则之后；新赛季游戏更新以后。全部 ✅ 才说明没改坏。

测试用的截图不提交到仓库（太大，而且有其他玩家的名字和 ID），只在开发者电脑上项目文件夹的 captures/ 里；
找不到截图的测试会显示"跳过"。测试文件只 import 项目代码，项目代码不 import 测试，所以测试不会被打进安装包。
"""

import time

import fake_game  # noqa: F401  要在项目代码之前导入（设置临时数据文件夹）
import test_accounts
import test_autostart
import test_aspect
import test_cloud_sync
import test_data_move
import test_eliminated
import test_follow
import test_english
import test_game_lang
import test_in_match
import test_legend_icons
import test_legends
import test_light
import test_player_name
import test_ranks
import test_replay
import test_screenshots
import test_share
import test_sounds
import test_source_run
import test_stats_page
import test_summary_page
import test_teammates
import test_telemetry
import test_updater
import test_webview2

TESTS = {"总结页停留": test_summary_page, "轻量（按需加载模型）": test_light, "整晚重放": test_replay,
         "传奇识别": test_legends, "玩家名（区分账号）": test_player_name, "多账号（对照表和弹窗）": test_accounts,
         "队友（读卡片、队友表、战绩页）": test_teammates, "截图留不留": test_screenshots, "段位": test_ranks,
         "源码运行（战绩记进安装版）": test_source_run, "淘汰画面（名次入库、和总结页/经验值页核对）": test_eliminated,
         "0.3.0 新流程（对局中、冠军、大厅校准、各来源优先级）": test_in_match,
         "一键更新（下载、核对、安装、恢复、面板）": test_updater,
         "WebView2 检查（没有时问要不要装、不装用浏览器、不用 IE 内核）": test_webview2,
         "游戏画面比例（16:10、4:3：换算、自动检测、设置页、整局重放）": test_aspect,
         "游戏语言（规则按语言分开、认出游戏语言、设置页选游戏语言、匹配局不补段位）": test_game_lang,
         "英文游戏画面（排位、匹配两局整段重放）": test_english,
         "数据文件夹（放在哪里、搬家、指路文件、老玩家自动搬）": test_data_move,
         "传奇头像（图标、许可文件、战绩页和总览页）": test_legend_icons,
         "开机自动启动（启动项、任务管理器里关掉、换了位置、被拦住、面板）": test_autostart,
         "提示音（声音文件、开关、什么时候响、记录程序、设置页）": test_sounds,
         "分享战绩（三种卡片、不写队友名字、对话框、保存图片）": test_share,
         "数据页（常一起玩的队友、传奇表现）": test_stats_page,
         "总览页\"关注我\"（B 站、小黑盒）": test_follow,
         "匿名统计（同意了才发、只发说好的几项、日活、重发改过的局、连不上、面板）": test_telemetry,
         "云同步（二维码、绑定、上传、重传、删除、连不上、手机上解绑、设置页）": test_cloud_sync}


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
