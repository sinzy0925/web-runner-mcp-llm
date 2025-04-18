# --- ファイル: batch_test_runner.py (完全コード - 2023/04/18 時点最新) ---

import asyncio
import json
import logging
import time
import traceback
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
import os
import re
import shutil # ファイル削除用

# --- 必要なモジュールをインポート ---
# LLM関連
try:
    from generate_action_json_from_llm import get_element_info_list_with_fallback
except ImportError:
    logging.error("generate_action_json_from_llm.py または LLM関数が見つかりません。")
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
    logging.critical(f"Playwright関連モジュール(playwright_launcher, config, utils)が見つかりません: {e.name}")
    print(f"Error: Missing Playwright related module - {e.name}")
    exit()

# MCPクライアント関連
try:
    from web_runner_mcp_client_core import execute_web_runner_via_mcp
except ImportError:
    logging.critical("web_runner_mcp_client_core.py が見つかりません。")
    exit()

# --- 設定 ---
SCREENSHOT_BASE_DIR = Path(config.DEFAULT_SCREENSHOT_DIR)
INPUT_JSON_FILE     = Path("./web_runner_mcp_llm_batch_test_cases.json") # 入力ファイル名
OUTPUT_SUMMARY_FILE = Path("./web_runner_mcp_llm_batch_output.txt") # サマリファイル名
OUTPUT_JSON_DIR     = Path("./output_generated_json_for_batch") # 生成JSON保存先
OUTPUT_DETAILS_DIR  = Path("./output_result") # 詳細結果保存先

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
        if item.is_file() or item.is_link():
          item.unlink()
          print(f"  削除(ファイル): {item}")
        elif item.is_dir():
          shutil.rmtree(item)
          print(f"  削除(ディレクトリ): {item}")
      except Exception as e:
        print(f"  削除失敗: {item}. エラー: {e}")
    print(f"ディレクトリ '{path}' 内をクリアしました。")
  except Exception as e:
    print(f"ディレクトリ内容のクリア中に予期しないエラーが発生しました ({path}): {e}")

# バッチ実行前に出力先をクリア
print("--- 出力ディレクトリのクリア ---")
delete_directory_contents(OUTPUT_JSON_DIR)
delete_directory_contents(OUTPUT_DETAILS_DIR)
# delete_directory_contents(SCREENSHOT_BASE_DIR) # スクショは通常クリアしない
# delete_directory_contents(Path("./logs")) # ログも通常クリアしない


# --- ロギング設定 ---
log_file = "batch_runner.log"
log_dir = Path("./logs")
log_dir.mkdir(exist_ok=True)
log_path = log_dir / log_file
log_format = '%(asctime)s - [%(levelname)s] [%(name)s:%(lineno)d] %(message)s'
# ファイルハンドラとストリームハンドラを設定
file_handler = logging.FileHandler(log_path, encoding='utf-8')
file_handler.setFormatter(logging.Formatter(log_format))
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter(log_format))
# ルートロガーにハンドラを追加（既存があればクリアする）
root_logger = logging.getLogger()
# 既存のハンドラをクリア (二重ログ防止)
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
root_logger.addHandler(file_handler)
root_logger.addHandler(stream_handler)
root_logger.setLevel(logging.INFO) # 全体のログレベルを設定
logger = logging.getLogger(__name__) # このファイル用のロガーを取得

# --- HTML取得関数 ---
async def get_actual_html_for_batch(url: str) -> Optional[str]:
    """PlaywrightでHTMLを取得し、クリーンアップする (バッチ用)"""
    logger.info(f"HTML取得開始: {url}")
    html_content = None # 初期化
    try:
        get_html_action = [{"action": "get_inner_html", "selector": "html", "memo": "Get full HTML for batch"}]
        # タイムアウト設定: config.pyの値があればそれを使い、なければデフォルトを設定
        default_timeout = getattr(config, 'DEFAULT_ACTION_TIMEOUT', 10000)
        fetch_timeout = max(60000, default_timeout * 3) # 最低60秒、またはデフォルトの3倍

        success, results = await playwright_launcher.run_playwright_automation_async(
            target_url=url,
            actions=get_html_action,
            headless_mode=True, # ★ヘッドレスOFFで実行★
            slow_motion=0,
            default_timeout=fetch_timeout
        )
        if success and results and isinstance(results[0].get("html"), str):
            html_content = results[0]["html"]
            logger.info(f"HTML取得成功 (URL: {url}, Length: {len(html_content)})")
            if DO_CLEANUP:
                # クリーンアップはCPU負荷がかかる可能性があるので別スレッドで実行
                cleaned_html = await asyncio.to_thread(cleanup_html, html_content)
                logger.info(f"クリーンアップ後のHTMLサイズ: {len(cleaned_html)}")
                return cleaned_html
            else:
                logger.info("HTMLクリーンアップはスキップされました。")
                return html_content
        else:
            logger.error(f"HTML取得失敗 (URL: {url}). Result: {results}")
            return None # 失敗時は None を返す
    except Exception as e:
        logger.error(f"HTML取得中に予期せぬエラー (URL: {url}): {e}", exc_info=True)
        return None # エラー時も None を返す

# --- 結果出力関数 (サマリファイル用) ---
def append_summary_result(case_name: str, result_line: str):
    """整形済みの結果行をサマリ結果ファイルに追記する"""
    try:
        with open(OUTPUT_SUMMARY_FILE, "a", encoding="utf-8") as f:
            f.write(result_line + "\n")
        logger.info(f"サマリ結果追記: {OUTPUT_SUMMARY_FILE.name} <- {case_name}")
    except Exception as e:
        logger.error(f"サマリ結果ファイルへの書き込みエラー ({case_name}): {e}")

# --- 詳細結果ファイル保存関数 ---
def save_detailed_result(case_name: str, success: bool, data: Union[str, Dict], output_dir: Path):
    """ケースごとの詳細結果やエラー情報をファイルに保存する"""
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_details.json" if success else "_error.json"
    safe_case_name = re.sub(r'[\\/*?:"<>|]', "", case_name) # ファイル名禁則文字を除去
    filename = output_dir / f"{safe_case_name}{suffix}"
    try:
        content_to_write = ""
        if isinstance(data, str):
            if success: # 成功時のJSON文字列
                try:
                    parsed_json = json.loads(data)
                    content_to_write = json.dumps(parsed_json, indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    logger.warning(f"結果は成功扱いでしたがJSONパース失敗 ({case_name})。テキスト保存。")
                    content_to_write = data
                    filename = filename.with_suffix(".txt") # 拡張子変更
            else: # 失敗時の文字列エラー
                 content_to_write = f"MCP Execution Failed (String Error):\n\n{data}"
                 filename = filename.with_suffix(".txt")
        elif isinstance(data, dict): # 失敗時の辞書エラー
            try:
                content_to_write = json.dumps(data, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"エラー辞書のJSON変換失敗 ({case_name}): {e}。文字列保存。")
                content_to_write = str(data)
                filename = filename.with_suffix(".txt")
        else: # その他の予期しない形式
            content_to_write = f"Unexpected Data Format Received:\n\n{str(data)}"
            filename = filename.with_suffix(".txt")

        with open(filename, "w", encoding="utf-8") as f:
            f.write(content_to_write)
        logger.info(f"詳細結果/エラー情報を保存: {filename.name}")
    except Exception as e:
        logger.error(f"詳細結果/エラー情報のファイル保存エラー ({filename}): {e}")

# --- メインのバッチ処理関数 ---
async def run_batch_tests():
    """入力JSONに基づき、一連のテストケースを実行する"""
    logger.info(f"--- バッチテスト開始 (入力: {INPUT_JSON_FILE}) ---")
    # 出力ディレクトリ作成（存在してもエラーにならない）
    OUTPUT_JSON_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_BASE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DETAILS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 入力JSON読み込み
    try:
        with open(INPUT_JSON_FILE, "r", encoding="utf-8") as f:
            test_cases: List[Dict[str, Any]] = json.load(f)
        logger.info(f"{len(test_cases)} 件のテストケースを読み込みました。")
    except FileNotFoundError:
        logger.critical(f"エラー: 入力ファイルが見つかりません: {INPUT_JSON_FILE}")
        return
    except json.JSONDecodeError as e:
        logger.critical(f"エラー: 入力JSONの形式が正しくありません: {e}")
        return
    except Exception as e:
        logger.critical(f"エラー: 入力ファイルの読み込み中にエラー: {e}", exc_info=True)
        return

    # 2. 各テストケースを処理
    for i, case in enumerate(test_cases):
        case_name = case.get("case_name", f"UnnamedCase_{i+1}")
        instruction = case.get("instruction")
        url = case.get("url")
        logger.info("\n" + "=" * 40)
        logger.info(f"[{i+1}/{len(test_cases)}] 実行中: {case_name}")
        logger.info(f"  URL: {url}")
        logger.info(f"  指示: {instruction}")

        # 各ケースの変数を初期化
        html_content: Optional[str] = None
        action_json: Optional[Dict[str, Any]] = None
        mcp_success: bool = False
        mcp_result_or_error: Union[str, Dict] = "処理未実行" # 初期状態
        screenshot_filename: Optional[str] = None
        final_summary_result_str: str = "処理未実行" # 初期状態

        try:
            # 2a. HTML取得
            if not url: raise ValueError("テストケースにURLが指定されていません。")
            html_content = await get_actual_html_for_batch(url)
            if not html_content: raise RuntimeError("HTMLの取得に失敗しました。")

            # 2b. LLMでアクションJSONを生成
            if not instruction: raise ValueError("テストケースに指示が指定されていません。")
            logger.info("LLMでアクションJSONを生成中...")
            # LLM呼び出しはブロッキングする可能性があるので別スレッドで実行
            target_hints, fallback_selector, action_details = await asyncio.to_thread(
                get_element_info_list_with_fallback, html_content, instruction
            )

            # LLMからのアクションタイプを検証
            if not action_details or action_details.get("action_type") == "unknown":
                raise ValueError("LLMがアクションタイプを特定できませんでした。指示を見直してください。")

            # アクションJSONオブジェクトを組み立て
            action_obj: Dict[str, Any] = {
                "memo": instruction,
                "action": action_details.get("action_type"),
                "selector": fallback_selector, # フォールバックセレクター
                "target_hints": target_hints if target_hints is not None else [], # LLM生成ヒント
            }
            # アクション詳細の他のキーを追加 (Noneでない場合のみ)
            for key in ["value", "attribute_name", "option_type", "option_value"]:
                if action_details.get(key) is not None:
                    action_obj[key] = action_details[key]

            # 完全なアクションJSONデータを作成 (単一アクション)
            action_json = { "target_url": url, "actions": [action_obj] }

            # 生成されたJSONをファイルに保存
            try:
                json_filename = f"{case_name}_generated.json"
                output_path = OUTPUT_JSON_DIR / json_filename
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(action_json, f, indent=2, ensure_ascii=False)
                logger.info(f"生成されたJSONを保存: {output_path}")
            except Exception as e_save:
                # 保存エラーは警告に留め、処理は続行
                logger.error(f"生成JSONの保存中にエラーが発生しました: {e_save}")

            # 2c. Web-Runner実行 (+ スクリーンショットと待機のアクションを追加)
            logger.info("Web-Runnerを実行します (MCP経由)...")
            screenshot_filename_base = f"{case_name}_screenshot"
            # ★ ファイル名に使えない文字を除去して value に設定 ★
            safe_ss_base = re.sub(r'[\\/*?:"<>|]', "_", screenshot_filename_base)
            screenshot_action = {"action": "screenshot", "memo": "アクション実行後のスクリーンショット", "value": safe_ss_base}
            sleep_action = {"action": "sleep", "memo": "スクリーンショット後の待機", "value": 2} # 2秒待機

            # 元のアクションリストにスクリーンショットとスリープを追加
            actions_to_run = action_json["actions"] + [screenshot_action, sleep_action]
            json_to_run = {**action_json, "actions": actions_to_run}

            # MCP経由で実行
            mcp_success, mcp_result_or_error = await execute_web_runner_via_mcp(
                json_to_run,
                headless=False, # ★ヘッドレスOFFで実行★
                slow_mo=0       # SlowMoなし
            )

            # 2d. 結果の解析 (サマリ用)
            if mcp_success and isinstance(mcp_result_or_error, str):
                try:
                    results_list = json.loads(mcp_result_or_error)
                    # 結果リストが存在し、空でないことを確認
                    if results_list and len(results_list) > 0:
                        first_action_result = results_list[0] # 主なアクションの結果
                        status = first_action_result.get("status")
                        if status == "success":
                            action_type = first_action_result.get("action")
                            # 件数情報を取得 (リストが存在しない場合やNoneの場合は0件とする)
                            url_count = len(first_action_result.get("url_list", [])) if isinstance(first_action_result.get("url_list"), list) else 0
                            text_count = len(first_action_result.get("scraped_texts", [])) if isinstance(first_action_result.get("scraped_texts"), list) else 0
                            pdf_count = len(first_action_result.get("pdf_texts", [])) if isinstance(first_action_result.get("pdf_texts"), list) else 0
                            attr_count = len(first_action_result.get("attribute_list", [])) if isinstance(first_action_result.get("attribute_list"), list) else 0
                            all_text_count = len(first_action_result.get("text_list", [])) if isinstance(first_action_result.get("text_list"), list) else 0
                            email_count = len(first_action_result.get("extracted_emails", [])) if isinstance(first_action_result.get("extracted_emails"), list) else 0
                            total_items = first_action_result.get('results_count', 0) # 全体の件数

                            # サマリ文字列を生成
                            summary_parts = []
                            if total_items > 0: summary_parts.append(f"{total_items}件") # 全体件数があれば表示
                            if url_count > 0: summary_parts.append(f"url:{url_count}件")
                            if text_count > 0: summary_parts.append(f"content:{text_count}件")
                            if pdf_count > 0: summary_parts.append(f"pdf:{pdf_count}件")
                            if email_count > 0: summary_parts.append(f"mail:{email_count}件")
                            if attr_count > 0: summary_parts.append(f"attr:{attr_count}件")
                            if all_text_count > 0: summary_parts.append(f"textList:{all_text_count}件")

                            # アクションタイプに応じたサマリ結果
                            if action_type in ["get_text_content", "get_inner_text"]:
                                final_summary_result_str = first_action_result.get("text", "N/A")[:50] # 取得テキストの一部
                            elif action_type == "get_attribute":
                                final_summary_result_str = str(first_action_result.get("value", "N/A"))[:50] # 取得属性値の一部
                            elif action_type.startswith("get_all_"):
                                final_summary_result_str = ",".join(summary_parts) if summary_parts else "0件取得" # 件数情報
                            else: # click, input など
                                final_summary_result_str = "アクション成功"

                            # --- 2e. 詳細結果ファイル保存 ---
                            save_detailed_result(case_name, mcp_success, mcp_result_or_error, OUTPUT_DETAILS_DIR)

                            # --- 2f. サマリ結果ファイルに追記 ---
                            summary_line = f"テスト結果: {case_name},結果:{final_summary_result_str},{screenshot_filename if screenshot_filename else 'N/A'}"
                            append_summary_result(case_name, summary_line)


                            # スクリーンショットファイル名の取得を試みる
                            if len(results_list) > 1 and results_list[1].get("action") == "screenshot" and results_list[1].get("status") == "success":
                                ss_path = results_list[1].get("filename")
                                if ss_path:
                                    # ファイル名だけを抽出
                                    try: screenshot_filename = Path(ss_path).name
                                    except Exception: screenshot_filename = ss_path # パス解析失敗時はそのまま
                        else: # status が "success" でない場合 (アクション失敗)
                             final_summary_result_str = f"ERROR - アクション失敗: {str(first_action_result.get('message', '詳細不明'))[:100]}"
                    else: # 結果リストが空の場合
                        final_summary_result_str = "ERROR - 結果リストが空または無効"
                except json.JSONDecodeError:
                    final_summary_result_str = "ERROR - 結果JSONパース失敗"
                    logger.warning(f"結果JSONパース失敗 ({case_name}): {mcp_result_or_error[:500]}...") # パース失敗した文字列もログに
                except Exception as e_parse:
                    final_summary_result_str = f"ERROR - 結果解析中に予期せぬエラー: {e_parse}"
                    logger.error(f"結果解析エラー ({case_name}): {e_parse}", exc_info=True)
            elif not mcp_success: # MCP実行自体が失敗した場合
                 error_detail = str(mcp_result_or_error)[:100] # エラーメッセージを省略
                 if isinstance(mcp_result_or_error, dict):
                     error_detail = str(mcp_result_or_error.get("error", str(mcp_result_or_error)))[:100]
                 final_summary_result_str = f"ERROR - MCP実行失敗: {error_detail}"
            else: # 予期しないMCP応答形式
                final_summary_result_str = "ERROR - 不明なMCP応答形式"



        # --- エラーハンドリング ---
        except ValueError as e: # 設定関連のエラー
             logger.error(f"設定エラー ({case_name}): {e}")
             final_summary_result_str = f"ERROR - 設定不備: {e}"
        except RuntimeError as e: # HTML取得失敗など実行中のエラー
             logger.error(f"実行時エラー ({case_name}): {e}")
             final_summary_result_str = f"ERROR - 実行時: {e}"
        except Exception as e: # その他の予期せぬエラー
             logger.error(f"予期せぬエラーが発生しました ({case_name}): {e}", exc_info=True)
             final_summary_result_str = f"ERROR - 予期せぬエラー: {e}"


        # --- ケース間に少し待機 ---
        await asyncio.sleep(1)

    logger.info(f"--- バッチテスト完了 ---")

# --- 実行ブロック ---
if __name__ == "__main__":
    # ライブラリ存在チェック
    try:
        import anyio
        import playwright
        import google.generativeai
        import bs4
        # import lxml # lxmlはオプションなのでチェックしない
    except ImportError as e:
        logger.critical(f"エラー: 必須ライブラリ '{e.name}' が見つかりません。")
        print(f"エラー: 必須ライブラリ '{e.name}' が見つかりません。\npip install anyio playwright google-generativeai beautifulsoup4")
        exit()

    # anyio.run() を使ってメインの非同期関数を実行
    try:
        # Windowsでイベントループポリシーが必要な場合 (通常は不要)
        # if platform.system() == "Windows":
        #      asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        anyio.run(run_batch_tests)
    except Exception as e:
        logger.critical(f"バッチ処理の開始または実行中に致命的なエラーが発生しました: {e}", exc_info=True)