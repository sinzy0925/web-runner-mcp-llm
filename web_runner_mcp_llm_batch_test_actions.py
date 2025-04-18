# --- ファイル: batch_test_runner.py (asyncio.to_thread 修正・完全版) ---

import asyncio
import json
import logging
import time
import traceback
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
import os
import re
import shutil

# --- 必要なモジュールをインポート ---
# LLM関連
try:
    from generate_action_json_from_llm import get_element_info_list_with_fallback
except ImportError:
    logging.error("generate_action_json_from_llm.py またはLLM関数が見つかりません。")
    async def get_element_info_list_with_fallback(*args, **kwargs):
        logging.warning("ダミーのLLM関数を使用します。")
        return None, None, None

# HTML処理関連
try:
    from html_processor import cleanup_html, DO_CLEANUP
except ImportError:
    logging.error("html_processor.py が見つかりません。HTMLクリーンアップはスキップされます。")
    def cleanup_html(html_content: str) -> str:
        logging.warning("ダミーのHTMLクリーンアップ関数を使用します。")
        return html_content
    DO_CLEANUP = False

# Playwright関連
try:
    import playwright_launcher
    import config
    import utils
except ImportError as e:
    logging.critical(f"必須モジュール(playwright_launcher, config, utils)が見つかりません: {e.name}")
    print(f"Error: Missing required module - {e.name}")
    exit()

# MCPクライアント関連
try:
    from web_runner_mcp_client_core import execute_web_runner_via_mcp
except ImportError:
    logging.critical("web_runner_mcp_client_core.py が見つかりません。")
    exit()

# --- 設定 ---
SCREENSHOT_BASE_DIR = Path(config.DEFAULT_SCREENSHOT_DIR)
INPUT_JSON_FILE     = Path("./web_runner_mcp_llm_batch_test_actions.json") # ★入力ファイル名を修正★
OUTPUT_SUMMARY_FILE = Path("./web_runner_mcp_llm_batch_output.txt")
OUTPUT_JSON_DIR     = Path("./output_generated_json_for_batch")
OUTPUT_DETAILS_DIR  = Path("./output_result")

# --- ログ・ファイル削除 ---
def delete_directory_contents(directory_path):
  """指定されたディレクトリ内のファイルやサブディレクトリを削除する（ディレクトリ自体は残す）"""
  path = Path(directory_path)
  if not path.is_dir():
      print(f"ディレクトリ '{path}' が存在しないか、ディレクトリではありません。")
      return
  try:
    for item in path.iterdir():
      try:
        if item.is_file() or item.is_link(): item.unlink()
        elif item.is_dir(): shutil.rmtree(item)
      except Exception as e: print(f"  削除失敗: {item}. Error: {e}")
    print(f"ディレクトリ '{path}' 内クリア完了。")
  except Exception as e: print(f"ディレクトリクリアエラー ({path}): {e}")

print("--- 出力ディレクトリのクリア ---")
delete_directory_contents(OUTPUT_JSON_DIR)
delete_directory_contents(OUTPUT_DETAILS_DIR)
if OUTPUT_SUMMARY_FILE.exists():
    try: OUTPUT_SUMMARY_FILE.unlink(); print(f"既存サマリファイル削除: {OUTPUT_SUMMARY_FILE}")
    except Exception as e: print(f"サマリファイル削除失敗: {e}")
print("-----------------------------")


# --- ロギング設定 ---
log_file = "batch_runner.log"; log_dir = Path("./logs"); log_dir.mkdir(exist_ok=True); log_path = log_dir / log_file
log_format = '%(asctime)s - [%(levelname)s] [%(name)s:%(lineno)d] %(message)s'
file_handler = logging.FileHandler(log_path, encoding='utf-8'); file_handler.setFormatter(logging.Formatter(log_format))
stream_handler = logging.StreamHandler(); stream_handler.setFormatter(logging.Formatter(log_format))
root_logger = logging.getLogger();
for handler in root_logger.handlers[:]: root_logger.removeHandler(handler)
root_logger.addHandler(file_handler); root_logger.addHandler(stream_handler); root_logger.setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# --- HTML取得関数 ---
async def get_actual_html_for_batch(url: str) -> Optional[str]:
    """PlaywrightでHTMLを取得し、クリーンアップする (バッチ用)"""
    logger.info(f"HTML取得開始: {url}")
    html_content = None
    try:
        get_html_action = [{"action": "get_inner_html", "selector": "html", "memo": "Get full HTML for batch"}]
        default_timeout = getattr(config, 'DEFAULT_ACTION_TIMEOUT', 10000)
        fetch_timeout = max(60000, default_timeout * 3)

        success, results = await playwright_launcher.run_playwright_automation_async(
            target_url=url, actions=get_html_action, headless_mode=False,
            slow_motion=0, default_timeout=fetch_timeout
        )
        if success and results and isinstance(results[0].get("html"), str):
            html_content = results[0]["html"]
            logger.info(f"HTML取得成功 (URL: {url}, Length: {len(html_content)})")
            if DO_CLEANUP:
                cleaned_html = await asyncio.to_thread(cleanup_html, html_content)
                logger.info(f"クリーンアップ後のHTMLサイズ: {len(cleaned_html)}")
                return cleaned_html
            else: logger.info("HTMLクリーンアップ skip"); return html_content
        else: logger.error(f"HTML取得失敗 (URL: {url}). Result: {results}"); return None
    except Exception as e: logger.error(f"HTML取得中エラー (URL: {url}): {e}", exc_info=True); return None

# --- 結果出力関数 (サマリファイル用) ---
def append_summary_result(case_name: str, result_line: str):
    """整形済みの結果行をサマリ結果ファイルに追記する"""
    try:
        with open(OUTPUT_SUMMARY_FILE, "a", encoding="utf-8") as f: f.write(result_line + "\n")
        logger.info(f"サマリ結果追記: {OUTPUT_SUMMARY_FILE.name} <- {case_name}")
    except Exception as e: logger.error(f"サマリ結果ファイル書込エラー ({case_name}): {e}")

# --- 詳細結果ファイル保存関数 ---
def save_detailed_result(filename_prefix: str, success: bool, data: Union[str, Dict], output_dir: Path):
    """ケースごとの詳細結果やエラー情報をファイルに保存する"""
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_details.json" if success else "_error.json"
    safe_prefix = re.sub(r'[\\/*?:"<>|]', "", filename_prefix)
    filename = output_dir / f"{safe_prefix}{suffix}"
    try:
        content_to_write = ""
        if isinstance(data, str):
            if success:
                try: content_to_write = json.dumps(json.loads(data), indent=2, ensure_ascii=False)
                except json.JSONDecodeError: content_to_write = data; filename = filename.with_suffix(".txt"); logger.warning(f"JSONパース失敗 ({filename_prefix})")
            else: content_to_write = f"MCP Error (String):\n\n{data}"; filename = filename.with_suffix(".txt")
        elif isinstance(data, dict):
            try: content_to_write = json.dumps(data, indent=2, ensure_ascii=False)
            except Exception as e: content_to_write = str(data); filename = filename.with_suffix(".txt"); logger.warning(f"Dict to JSON failed: {e}")
        else: content_to_write = f"Unexpected Data:\n\n{str(data)}"; filename = filename.with_suffix(".txt")
        with open(filename, "w", encoding="utf-8") as f: f.write(content_to_write)
        logger.info(f"詳細結果/エラー情報を保存: {filename.name}")
    except Exception as e: logger.error(f"詳細結果ファイル保存エラー ({filename}): {e}")

# --- メインのバッチ処理関数 (asyncio.to_thread 修正版) ---
async def run_batch_tests():
    """入力JSONに基づき、一連のテストケース（複数ステップ含む）を実行する"""
    logger.info(f"--- バッチテスト開始 (入力: {INPUT_JSON_FILE}) ---")
    OUTPUT_JSON_DIR.mkdir(parents=True, exist_ok=True); SCREENSHOT_BASE_DIR.mkdir(parents=True, exist_ok=True); OUTPUT_DETAILS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 入力JSON読み込み
    try:
        with open(INPUT_JSON_FILE, "r", encoding="utf-8") as f: test_cases: List[Dict[str, Any]] = json.load(f)
        logger.info(f"{len(test_cases)} 件のテストケースを読み込みました。")
    except Exception as e: logger.critical(f"入力ファイルエラー: {e}", exc_info=True); return

    # 2. 各テストケースを処理
    for i, case in enumerate(test_cases):
        case_name = case.get("case_name", f"UnnamedCase_{i+1}")
        start_url = case.get("start_url")
        steps = case.get("steps")
        logger.info("\n" + "=" * 40); logger.info(f"[{i+1}/{len(test_cases)}] 実行中: {case_name}")
        logger.info(f"  開始URL: {start_url}")

        if not start_url or not steps or not isinstance(steps, list):
            logger.error("テストケース形式不正。スキップ。")
            append_summary_result(case_name, f"テスト結果: {case_name},結果:ERROR - テストケース形式不正,N/A")
            continue

        current_url: Optional[str] = start_url
        current_html: Optional[str] = None
        previous_action_type: Optional[str] = None
        case_success = True
        last_step_result_summary = "ケース未実行"
        last_screenshot_filename = None

        for j, step in enumerate(steps):
            step_num = j + 1
            instruction = step.get("instruction")
            logger.info(f"--- ステップ {step_num}/{len(steps)}: {case_name} ---")
            logger.info(f"  指示: {instruction}")

            if not instruction: logger.error("ステップ指示なし。スキップ。"); last_step_result_summary = f"ERROR - ステップ{step_num} 指示なし"; case_success = False; break
            if not current_url: logger.error("URL不明。続行不可。"); last_step_result_summary = f"ERROR - ステップ{step_num} URL不明"; case_success = False; break

            step_mcp_success = False
            step_mcp_result_or_error : Union[str, Dict] = f"ステップ{step_num}未実行"
            step_screenshot_filename = None

            try:
                # 2a. HTML取得判断と実行
                needs_html_refetch = (step_num == 1) or (previous_action_type in ["click", "submit", "select_option"])
                if needs_html_refetch:
                    logger.info(f"ステップ{step_num}: HTML再取得 (URL: {current_url})")
                    current_html = await get_actual_html_for_batch(current_url)
                    if not current_html: raise RuntimeError(f"ステップ{step_num}: HTML取得失敗")
                else:
                    logger.info(f"ステップ{step_num}: 前のHTML再利用")
                    if not current_html: raise RuntimeError(f"ステップ{step_num}: 再利用HTMLなし")

                # 2b. LLMでJSON生成
                logger.info(f"ステップ{step_num}: LLMでアクションJSON生成中...")
                # ★★★ asyncio.to_thread を使う ★★★
                target_hints, fallback_selector, action_details = await asyncio.to_thread(
                    get_element_info_list_with_fallback, current_html, instruction
                )
                # ★★★ ここまで修正 ★★★

                if not action_details or action_details.get("action_type") == "unknown": raise ValueError(f"ステップ{step_num}: LLMアクションタイプ特定失敗")
                action_obj: Dict[str, Any] = { "memo": instruction, "action": action_details.get("action_type"), "selector": fallback_selector, "target_hints": target_hints if target_hints is not None else [], }
                for key in ["value", "attribute_name", "option_type", "option_value"]:
                    if action_details.get(key) is not None: action_obj[key] = action_details[key]
                action_json = { "target_url": current_url, "actions": [action_obj] }
                try:
                    json_filename = f"{case_name}_step{step_num}_generated.json"; output_path = OUTPUT_JSON_DIR / json_filename
                    with open(output_path, "w", encoding="utf-8") as f: json.dump(action_json, f, indent=2, ensure_ascii=False)
                    logger.info(f"生成JSON保存: {output_path.name}")
                except Exception as e_save: logger.error(f"生成JSON保存エラー: {e_save}")

                # 2c. Web-Runner実行
                logger.info(f"ステップ{step_num}: Web-Runnerを実行 (アクション: {action_obj['action']})...")
                step_screenshot_filename_base = f"{case_name}_step{step_num}_screenshot"; safe_ss_base = re.sub(r'[\\/*?:"<>|]', "_", step_screenshot_filename_base)
                screenshot_action = {"action": "screenshot", "memo": f"ステップ{step_num}実行後スクショ", "value": safe_ss_base}
                sleep_action = {"action": "sleep", "memo": "スクショ後待機", "value": 2}
                actions_to_run = action_json["actions"] + [screenshot_action, sleep_action]
                json_to_run = {**action_json, "actions": actions_to_run, "target_url": current_url}
                step_mcp_success, step_mcp_result_or_error = await execute_web_runner_via_mcp(json_to_run, headless=False, slow_mo=0)

                # 2d. ステップ結果解析と状態更新
                previous_action_type = action_obj['action']
                if step_mcp_success and isinstance(step_mcp_result_or_error, str):
                    try:
                        step_results_list = json.loads(step_mcp_result_or_error)
                        if step_results_list and len(step_results_list) >= 1:
                            action_result = step_results_list[0]
                            step_status = action_result.get("status")
                            if step_status == "success":
                                logger.info(f"ステップ {step_num} 成功.")
                                last_step_result_summary = f"ステップ{step_num}({action_obj['action']}): 成功"
                                # URL更新ロジック (暫定)
                                if action_obj['action'] == "click" and action_result.get("new_page_opened") and action_result.get("new_page_url"):
                                    current_url = action_result.get("new_page_url"); logger.info(f"新ページ遷移: {current_url}"); current_html = None
                                elif action_obj['action'] == "click": logger.warning("クリック後のURL変化は検出されませんでした。")
                                # スクショファイル名取得
                                screenshot_step_index = -1
                                for idx, step_res in enumerate(step_results_list):
                                    if step_res.get("action") == "screenshot": screenshot_step_index = idx; break
                                if screenshot_step_index != -1 and step_results_list[screenshot_step_index].get("status") == "success":
                                    ss_path = step_results_list[screenshot_step_index].get("filename")
                                    if ss_path: step_screenshot_filename = Path(ss_path).name; logger.info(f"スクショファイル名: {step_screenshot_filename}")
                                    else: logger.warning("スクショ成功だがfilename無し")
                                elif screenshot_step_index != -1: logger.warning(f"スクショ失敗: {step_results_list[screenshot_step_index].get('message')}")
                            else: logger.error(f"ステップ {step_num} アクション失敗: {action_result.get('message')}"); last_step_result_summary = f"ERROR - ステップ{step_num}({action_obj['action']}) 失敗: {str(action_result.get('message'))[:50]}"; case_success = False; break
                        else: raise ValueError("結果リスト空 or 無効")
                    except json.JSONDecodeError: logger.error(f"結果JSONパース失敗 ({case_name} Step {step_num})"); raise ValueError("結果JSONパース失敗")
                    except Exception as e_parse: logger.error(f"結果解析エラー ({case_name} Step {step_num})", exc_info=True); raise ValueError(f"結果解析エラー: {e_parse}")
                elif not step_mcp_success: raise RuntimeError(f"MCP実行失敗: {step_mcp_result_or_error}")
                else: raise TypeError("不明なMCP応答")

            except (ValueError, RuntimeError, TypeError, Exception) as step_e:
                 logger.error(f"ステップ {step_num} 処理中にエラー: {step_e}", exc_info=True)
                 last_step_result_summary = f"ERROR - ステップ{step_num} 内部エラー: {str(step_e)[:50]}"
                 case_success = False
                 save_detailed_result(f"{case_name}_step{step_num}", False, {"error": str(step_e), "traceback": traceback.format_exc()}, OUTPUT_DETAILS_DIR)
                 break

            # 各ステップの詳細結果保存
            save_detailed_result(f"{case_name}_step{step_num}", step_mcp_success, step_mcp_result_or_error, OUTPUT_DETAILS_DIR)
            last_screenshot_filename = step_screenshot_filename

            await asyncio.sleep(0.5) # ステップ間に待機

        # --- ケース終了後のサマリ出力 ---
        final_summary_line = ""
        if case_success: final_summary_line = f"テスト結果: {case_name},結果:成功 ({last_step_result_summary}),{last_screenshot_filename if last_screenshot_filename else 'N/A'}"
        else: final_summary_line = f"テスト結果: {case_name},結果:{last_step_result_summary},{last_screenshot_filename if last_screenshot_filename else 'N/A'}"
        append_summary_result(case_name, final_summary_line)

        await asyncio.sleep(1) # ケース間に待機

    logger.info(f"--- バッチテスト完了 ---")


# --- 実行ブロック ---
if __name__ == "__main__":
    try: import anyio; import playwright; import google.generativeai; import bs4
    except ImportError as e: logger.critical(f"必須ライブラリ '{e.name}' 不足"); exit()
    try: anyio.run(run_batch_tests)
    except Exception as e: logger.critical(f"バッチ処理開始失敗: {e}", exc_info=True)