# --- ファイル: batch_runner_jsonl.py (結果ファイル名修正版) ---
import asyncio
import json
import sys
import os
import argparse
import traceback
from pathlib import Path
from typing import Dict, Any, Optional, Union, List
import logging # ロギングを追加
import re # 正規表現モジュールをインポート

# --- 必要なモジュールをインポート ---
try:
    from web_runner_mcp_client_core import execute_web_runner_via_mcp
except ImportError:
    print("Error: web_runner_mcp_client_core.py not found."); sys.exit(1)
try:
    # YAML用関数は不要になったので fill_template と separate_config のみ
    from template_processor import fill_template, separate_config
except ImportError:
    print("Error: template_processor.py not found."); sys.exit(1)
try:
    import config
    import utils
    DEFAULT_BATCH_OUTPUT_DIR = Path("./output_batch") # バッチ結果用のデフォルトディレクトリ
except ImportError:
    print("Warning: config or utils not found."); utils = None; DEFAULT_BATCH_OUTPUT_DIR = Path("./output_batch")

# --- ロギング設定 ---
log_file = "batch_runner_jsonl.log"
log_dir = Path("./logs"); log_dir.mkdir(exist_ok=True); log_path = log_dir / log_file
log_format = '%(asctime)s - [%(levelname)s] [%(name)s:%(lineno)d] %(message)s'
file_handler = logging.FileHandler(log_path, encoding='utf-8'); file_handler.setFormatter(logging.Formatter(log_format))
stream_handler = logging.StreamHandler(); stream_handler.setFormatter(logging.Formatter(log_format))
root_logger = logging.getLogger();
for handler in root_logger.handlers[:]: root_logger.removeHandler(handler)
root_logger.addHandler(file_handler); root_logger.addHandler(stream_handler); root_logger.setLevel(logging.INFO)
logger = logging.getLogger(__name__) # このファイル用

# --- 結果保存関数 (ファイル名生成ロジック修正版) ---
def save_batch_result(output_dir: Path, line_num: int, config_data: Optional[Dict], success: bool, result_or_error: Union[str, Dict]):
    """個別の実行結果をファイルに保存する"""
    output_dir.mkdir(parents=True, exist_ok=True)
    status_str = "success" if success else "error"

    # --- ファイル名生成ロジック ---
    template_name = "unknown_template"
    param_identifier = "noparam" # デフォルト

    if config_data and isinstance(config_data, dict): # config_dataがNoneや辞書でない場合を考慮
        try:
            # テンプレート名を取得 (パスからファイル名部分だけ)
            template_path_str = config_data.get("target_template", "")
            if template_path_str and isinstance(template_path_str, str):
                template_name = Path(template_path_str).stem # stem で拡張子なしのファイル名取得
            else:
                 logger.warning(f"Invalid 'target_template' in config data for line {line_num}. Using '{template_name}'.")


            # テンプレート用パラメータを取得 (実行設定などを除外)
            excluded_keys = {"target_template", "headless", "slow_mo", "default_timeout_ms"}
            template_params = {k: v for k, v in config_data.items() if k not in excluded_keys}

            # テンプレートパラメータがあれば、最初のキーと値を識別子に使う
            if template_params:
                # 辞書が空でないことを確認してからキーを取得
                first_param_key = next(iter(template_params.keys()))
                first_param_val = str(template_params[first_param_key])
                # ファイル名に使えない文字を除去・短縮
                safe_key = re.sub(r'[\\/*?:"<>|]', '_', first_param_key)
                safe_val = re.sub(r'[\\/*?:"<>|]', '_', first_param_val)[:20] # 20文字に制限
                param_identifier = f"{safe_key}_{safe_val}"
            elif "target_template" in config_data: # パラメータなくてもテンプレート名があればそれを使う
                 # テンプレート名自体も安全化
                 param_identifier = re.sub(r'[\\/*?:"<>|]', '_', template_name)
            # それ以外の場合は初期値 "noparam" が使われる

        except Exception as e:
            logger.warning(f"Error generating identifier for filename (line {line_num}): {e}")
            param_identifier = "generation_error" # エラー発生時の識別子
    elif config_data is None:
        logger.warning(f"Config data is None for line {line_num}. Using default filename parts.")
        template_name = "no_config"
        param_identifier = "no_config"
    else:
         logger.warning(f"Invalid config data type for line {line_num}: {type(config_data)}. Using default filename parts.")
         template_name = "invalid_config"
         param_identifier = "invalid_config"


    # 安全なファイル名を組み立て
    safe_template_name = re.sub(r'[\\/*?:"<>|]', '_', template_name)
    safe_identifier = re.sub(r'[\\/*?:"<>|]', '_', param_identifier) # 最終チェック
    base_filename = f"line{line_num}_{safe_template_name}_{safe_identifier}_{status_str}"
    # ファイル名が長すぎる場合も考慮 (Windowsのパス長制限など)
    max_len = 100 # ファイル名部分の最大長 (適宜調整)
    if len(base_filename) > max_len:
        base_filename = base_filename[:max_len] + "_truncated"

    filename = (output_dir / base_filename).with_suffix(".json") # まず.jsonで試す

    # --- ファイル内容書き込み ---
    content_to_write = ""
    # 出力するデータ構造を定義
    output_data = {
        "line_number": line_num,
        "input_config": config_data, # 入力として使われた設定
        "execution_status": status_str,
        "result_data": None, # 成功時の結果
        "error_details": None # 失敗時の詳細
    }

    if success and isinstance(result_or_error, str):
        try:
            # 成功結果をパースして格納
            output_data["result_data"] = json.loads(result_or_error)
        except json.JSONDecodeError:
            # パース失敗時は生データを格納し、拡張子を .txt に変更
            output_data["result_data"] = result_or_error
            filename = filename.with_suffix(".txt")
            logger.warning(f"Result for line {line_num} was successful but not valid JSON. Saved as .txt.")
    elif not success:
        # 失敗時のエラー情報を格納
        output_data["error_details"] = result_or_error
        # エラー情報が文字列でない場合も考慮
        if not isinstance(result_or_error, (str, dict, list, type(None))):
             output_data["error_details"] = str(result_or_error)


    try:
        # 最終的な出力データをJSON文字列に変換
        content_to_write = json.dumps(output_data, indent=2, ensure_ascii=False)
        # ファイルに書き込み
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content_to_write)
        logger.info(f"Saved result for line {line_num} to {filename.name}")
    except Exception as e:
        # ファイル保存中のエラー
        logger.error(f"Failed to save result file for line {line_num}: {filename} - {e}", exc_info=True)


# --- メインのバッチ処理関数 ---
async def run_batch(input_file: Path, output_dir: Path):
    """入力ファイルを1行ずつ読み込み、Web-Runnerタスクを実行する"""
    logger.info(f"--- Starting Batch Processing from: {input_file} ---")
    logger.info(f"Output directory: {output_dir}")
    line_count = 0
    success_count = 0
    error_count = 0

    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith('#'): # 空行やコメント行はスキップ
                    continue

                line_count += 1
                logger.info(f"\n--- Processing Line {line_num} ---")
                logger.debug(f"Raw line: {line}")

                # 各行の処理に必要な変数を初期化
                config_data: Optional[Dict[str, Any]] = None
                filled_json_data: Optional[Dict[str, Any]] = None
                run_settings: Dict[str, Any] = {}
                template_path: Optional[Path] = None
                current_success = False # この行の成功/失敗フラグ
                current_result_or_error: Union[str, Dict] = {"error": "Processing not started"} # 初期値

                try:
                    # 1. JSONパース: 各行が有効なJSONオブジェクトか確認
                    config_data = json.loads(line)
                    if not isinstance(config_data, dict):
                        raise ValueError("Line does not contain a valid JSON object.")
                    logger.info(f"Parsed config keys: {list(config_data.keys())}")

                    # 2. 設定分割: テンプレートパス、パラメータ、実行設定に分ける
                    # separate_configはYAML用の型変換を含むが、JSONパース後の型を使うので注意
                    template_path_str, template_params, _ = separate_config(config_data)
                    # 実行設定はconfig_dataから直接取得し、デフォルト値を設定
                    run_settings['headless'] = config_data.get('headless', True) # デフォルトTrue
                    run_settings['slow_mo'] = config_data.get('slow_mo', 0)       # デフォルト0
                    run_settings['default_timeout_ms'] = config_data.get('default_timeout_ms') # なければNone

                    if not template_path_str:
                        raise ValueError("'target_template' key is missing or invalid in the JSON object.")
                    template_path = Path(template_path_str)
                    if not template_path.exists():
                        raise FileNotFoundError(f"Template JSON file not found: {template_path}")
                    logger.info(f"Template: {template_path}, Params: {list(template_params.keys())}, Settings: {run_settings}")

                    # 3. テンプレート展開: パラメータを埋め込む
                    filled_json_data = fill_template(str(template_path), template_params)
                    if not filled_json_data:
                        raise ValueError("Failed to fill template (returned None or empty).")
                    # 実行設定 (default_timeout_ms) を最終JSONに追加 (あれば)
                    if run_settings['default_timeout_ms'] is not None:
                        filled_json_data['default_timeout_ms'] = run_settings['default_timeout_ms']

                    # 4. Web-Runner実行: MCP経由でコア関数を呼び出す
                    logger.info(f"Executing Web-Runner for line {line_num}...")
                    current_success, current_result_or_error = await execute_web_runner_via_mcp(
                        filled_json_data,
                        headless=run_settings.get('headless', True),
                        slow_mo=run_settings.get('slow_mo', 0)
                    )

                    # 5. 結果処理: 成功/失敗を記録
                    if current_success:
                        success_count += 1
                        logger.info(f"Line {line_num}: Execution successful.")
                    else:
                        error_count += 1
                        logger.error(f"Line {line_num}: Execution failed.")

                # --- 各行レベルのエラーハンドリング ---
                except json.JSONDecodeError as e:
                    logger.error(f"Line {line_num}: Invalid JSON format - {e}. Skipping.")
                    error_count += 1; current_success = False
                    current_result_or_error = {"error": "Invalid JSON format", "details": str(e), "raw_line": line}
                except FileNotFoundError as e:
                    logger.error(f"Line {line_num}: File not found error - {e}. Skipping.")
                    error_count += 1; current_success = False
                    current_result_or_error = {"error": "File not found", "details": str(e), "input_config": config_data}
                except ValueError as e:
                    logger.error(f"Line {line_num}: Configuration or template error - {e}. Skipping.")
                    error_count += 1; current_success = False
                    current_result_or_error = {"error": "Configuration/Template error", "details": str(e), "input_config": config_data}
                except Exception as e:
                    # MCP通信エラーなども含む予期せぬエラー
                    logger.error(f"Line {line_num}: Unexpected error during processing - {type(e).__name__}: {e}. Skipping.", exc_info=False) # トレースバックは別で記録
                    error_count += 1; current_success = False
                    current_result_or_error = {"error": f"Unexpected processing error: {type(e).__name__}", "details": str(e), "traceback": traceback.format_exc(), "input_config": config_data}
                finally:
                    # --- 6. 各行の結果をファイルに保存 ---
                    # config_data がパース前にエラーになった場合も考慮
                    save_batch_result(output_dir, line_num, config_data if 'config_data' in locals() else {"raw_line": line}, current_success, current_result_or_error)


                # 各行の処理間に少し待機（サーバー負荷軽減のため）
                await asyncio.sleep(0.5)

    except FileNotFoundError:
        logger.critical(f"Error: Input file not found: {input_file}")
    except Exception as e:
        # ファイル読み込み中のエラーなど
        logger.critical(f"An unexpected error occurred during batch processing initialization: {e}", exc_info=True)

    # --- バッチ処理全体のサマリ ---
    logger.info(f"--- Batch Processing Finished ---")
    logger.info(f"Total lines processed: {line_count}")
    logger.info(f"Successful tasks: {success_count}")
    logger.info(f"Failed tasks: {error_count}")

# --- コマンドライン引数処理と実行 ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Web-Runner MCP tasks in batch from a JSON Lines file.")
    parser.add_argument(
        "input_file",
        type=Path,
        help="Path to the input file (text file with one JSON object per line)."
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=DEFAULT_BATCH_OUTPUT_DIR,
        help=f"Directory to save individual results (default: {DEFAULT_BATCH_OUTPUT_DIR})."
    )
    args = parser.parse_args()

    # anyio を使って非同期関数を実行
    try:
        # 必要なライブラリが揃っているか再度確認 (anyioは必須)
        import anyio
        anyio.run(run_batch, args.input_file, args.output_dir)
    except ImportError:
        logger.critical("Error: 'anyio' library is required. Please install it: pip install anyio")
    except KeyboardInterrupt:
        logger.warning("Batch processing interrupted by user.")
    except Exception as e:
        logger.critical(f"Failed to start batch processing: {e}", exc_info=True)