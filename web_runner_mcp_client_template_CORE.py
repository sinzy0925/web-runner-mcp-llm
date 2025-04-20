# --- ファイル: web_runner_mcp_client_core.py (YAML設定ファイル対応版) ---
import asyncio
import sys, os
import json
from pathlib import Path
import anyio
import platform
import traceback
from typing import Optional, Dict, Any, Tuple, Union, List
import argparse
import io

# MCP クライアントライブラリ
from mcp import ClientSession, StdioServerParameters, types as mcp_types
from mcp.client.stdio import stdio_client

# --- 設定・ユーティリティ・テンプレート処理 ---
try:
    import config
    import utils
    DEFAULT_OUTPUT_FILE = Path(config.MCP_CLIENT_OUTPUT_FILE)
except ImportError:
    print("Warning: config.py or utils.py not found. Using default output filename './output/web_runner_mcp.txt'")
    DEFAULT_OUTPUT_FILE = Path("./output/web_runner_mcp.txt")
    utils = None

try:
    # YAML読み込みとテンプレート処理関数をインポート
    from template_processor import load_config_from_yaml, separate_config, fill_template
except ImportError:
    print("Error: template_processor.py not found. YAML config and template processing is unavailable.")
    # 実行時エラーを防ぐためにダミー関数を定義 (あるいはここで終了させる)
    def load_config_from_yaml(filepath: str): raise ImportError("template_processor not found")
    def separate_config(config_data): raise ImportError("template_processor not found")
    def fill_template(template_path, params): raise ImportError("template_processor not found")
    # sys.exit(1) # 起動時に終了させる場合はこちら

SERVER_SCRIPT = Path("./web_runner_mcp_server.py")

# --- execute_web_runner_via_mcp 関数 (変更なし) ---
# この関数は実行用JSONデータと実行設定を直接受け取るので変更不要
async def execute_web_runner_via_mcp(
    input_json_data: Dict[str, Any],
    headless: bool = True, # デフォルトをTrueに変更 (YAMLで指定されることが多くなるため)
    slow_mo: int = 0
) -> Tuple[bool, Union[str, Dict[str, Any]]]:
    """指定されたJSONデータをWeb-Runner MCPサーバーに送信し、実行結果を取得する。"""
    print("--- Executing Web Runner via MCP ---")
    print(f"Headless: {headless}, SlowMo: {slow_mo}") # 入力データは大きいので省略
    # print(f"Input data: {json.dumps(input_json_data, indent=2, ensure_ascii=False)}") # デバッグ時

    if not SERVER_SCRIPT.exists():
        return False, {"error": f"Server script not found: {SERVER_SCRIPT}"}

    # input_args を input_json_data から構築
    tool_arguments = {
        "input_args": {
            "target_url": input_json_data.get("target_url"),
            "actions": input_json_data.get("actions", []),
            # ★★★ headless, slow_mo は引数から受け取る ★★★
            "headless": headless,
            "slow_mo": slow_mo,
            # ★★★ default_timeout_ms も input_json_data から取得(オプション) ★★★
            "default_timeout_ms": input_json_data.get("default_timeout_ms")
        }
    }
    if not tool_arguments["input_args"]["target_url"] or not tool_arguments["input_args"]["actions"]:
         return False, {"error": "'target_url' or 'actions' missing in input_json_data."}

    # --- (サーバーパラメータ設定とMCP通信部分は変更なし) ---
    server_env = os.environ.copy(); server_env["PYTHONIOENCODING"] = "utf-8"
    server_params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER_SCRIPT), "--transport", "stdio"], env=server_env
    )
    session: Optional[ClientSession] = None
    try:
        print("Connecting to server via stdio_client...")
        async with stdio_client(server_params) as streams:
            print("DEBUG: stdio_client context entered.")
            read_stream, write_stream = streams
            print("DEBUG: Got streams.")
            async with ClientSession(read_stream, write_stream) as session:
                print("DEBUG: ClientSession context entered.")
                await session.initialize()
                print("DEBUG: Initialization complete.")
                print("Calling 'execute_web_runner' tool...")
                tool_result: mcp_types.CallToolResult = await session.call_tool(name="execute_web_runner", arguments=tool_arguments)
                print("DEBUG: Tool call finished.")
                if tool_result.isError:
                    print("--- Tool Execution Error ---")
                    error_content = tool_result.content[0].text if tool_result.content and isinstance(tool_result.content[0], mcp_types.TextContent) else "Unknown error"
                    print(f"Received error details:\n{error_content}")
                    try: error_data = json.loads(error_content); return False, {"error": "MCP tool failed", "details": error_data}
                    except json.JSONDecodeError: return False, {"error": "MCP tool failed", "raw_details": error_content}
                else:
                    print("--- Tool Execution Success ---")
                    if tool_result.content and isinstance(tool_result.content[0], mcp_types.TextContent): return True, tool_result.content[0].text
                    else: return False, {"error": "No content received"}
    except Exception as e:
        print(f"--- MCP Communication Exception ---"); error_msg = f"{type(e).__name__}: {e}"; print(error_msg); print("--- Traceback ---"); traceback.print_exc()
        return False, {"error": f"MCP communication error: {error_msg}"}
    finally: print("--- Web Runner via MCP Finished ---")


# --- main 関数 (YAMLファイル対応に修正) ---
async def main():
    """YAML設定ファイルを読み込んでWeb-Runnerを実行し、結果をファイルに出力"""
    parser = argparse.ArgumentParser(description="Run Web-Runner MCP task from YAML config file.")
    # --- ▼▼▼ 引数を --config に変更 ▼▼▼ ---
    parser.add_argument(
        "--config",
        type=Path,
        required=True, # 必須引数に変更
        help="Path to the YAML configuration file."
    )
    # --- ▲▲▲ ---
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Path to the output file (default: {DEFAULT_OUTPUT_FILE})."
    )
    args = parser.parse_args()
    config_file_path: Path = args.config
    output_file_path: Path = args.output

    print(f"Loading configuration from YAML: {config_file_path}")
    if not config_file_path.exists():
        print(f"Error: Config file not found at {config_file_path}")
        return

    # --- YAML設定の読み込みと処理 ---
    try:
        config_data = load_config_from_yaml(config_file_path)
        if not config_data:
            raise ValueError("Failed to load or parse YAML config file.")

        template_path_str, template_params, run_settings = separate_config(config_data)

        if not template_path_str:
            raise ValueError("'target_template' not found in YAML config file.")
        template_path = Path(template_path_str)
        if not template_path.exists():
             raise FileNotFoundError(f"Template JSON file specified in YAML not found: {template_path}")

        print(f"Using template: {template_path}")
        print(f"Template parameters: {template_params}")
        print(f"Run settings: {run_settings}")

        # テンプレートにパラメータを埋め込んで実行用JSONを生成
        filled_json_data = fill_template(str(template_path), template_params)
        if not filled_json_data:
            raise ValueError("Failed to fill template with parameters.")

        # default_timeout_ms も run_settings から取得して filled_json_data に追加 (オプション)
        if run_settings.get("default_timeout_ms") is not None:
             filled_json_data["default_timeout_ms"] = run_settings["default_timeout_ms"]

        # --- MCP経由で実行 ---
        # run_settings から値を取得 (getの第二引数でデフォルト値を設定)
        headless_param = run_settings.get("headless", True) # デフォルトTrue
        slow_mo_param = run_settings.get("slow_mo", 0)     # デフォルト0

        success, result_or_error = await execute_web_runner_via_mcp(
            filled_json_data,
            headless=headless_param,
            slow_mo=slow_mo_param
        )

        # --- 結果のファイル書き込み (変更なし) ---
        print(f"\nWriting result to: {output_file_path}")
        output_file_path.parent.mkdir(parents=True, exist_ok=True) # 出力ディレクトリ作成

        if success and isinstance(result_or_error, str):
            try:
                result_data_list = json.loads(result_or_error)
                if isinstance(result_data_list, list) and utils:
                     # 最終結果に設定情報も追記する (オプション)
                     summary_data = {"config_file": str(config_file_path), "run_settings": run_settings}
                     utils.write_results_to_file(result_data_list, str(output_file_path), final_summary_data=summary_data)
                elif not utils:
                     print("Warning: utils module not found, cannot use write_results_to_file.")
                     with open(output_file_path, 'w', encoding='utf-8') as f: f.write(result_or_error)
                else:
                     print("Error: Result JSON from server is not a list. Writing raw data.")
                     with open(output_file_path, 'w', encoding='utf-8') as f: f.write(result_or_error)
            except json.JSONDecodeError:
                 print(f"Error: Received non-JSON success result. Writing raw data.");
                 with open(output_file_path, 'w', encoding='utf-8') as f: f.write(result_or_error)
            except Exception as write_e: print(f"Error writing result file: {write_e}"); traceback.print_exc()
        else: # success is False
            print("\n--- Final Result (Error) ---")
            formatted_error = json.dumps(result_or_error, indent=2, ensure_ascii=False) if isinstance(result_or_error, dict) else str(result_or_error)
            print(formatted_error)
            with open(output_file_path, 'w', encoding='utf-8') as f: f.write(f"--- Execution Failed ---\n{formatted_error}")

    except (ImportError, FileNotFoundError, ValueError) as e:
        # 設定ファイル関連のエラー
        print(f"Error during configuration processing: {e}")
    except Exception as e:
        # その他の予期せぬエラー
        print(f"An unexpected error occurred in main: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    # --- (Windows向け設定やanyio実行部分は変更なし) ---
    if platform.system() == "Windows":
        # asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy()) # 通常不要
        pass
    try:
        anyio.run(main)
    except Exception as e:
        print(f"Error running anyio task: {e}")
        traceback.print_exc()