import json
import re
# --- ▼▼▼ typing と yaml をインポート ▼▼▼ ---
from typing import Dict, Any, Optional, Tuple
try:
    import yaml # PyYAML ライブラリ
except ImportError:
    print("エラー: PyYAML ライブラリが見つかりません。")
    print("YAMLファイルを扱うには 'pip install PyYAML' を実行してください。")
    # YAML機能なしで続行するか、ここで終了するか選択
    # exit(1) # 終了する場合
    yaml = None # ダミーとしてNoneを設定
# --- ▲▲▲ ---

# --- fill_template 関数 (既存・変更なし) ---
def fill_template(template_path: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """テンプレートJSONを読み込み、プレースホルダーをパラメータで置換する"""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            template_str = f.read()
        filled_str = template_str
        for key, value in parameters.items():
            placeholder = f"{{{{{key}}}}}"
            if isinstance(value, str): value_str = json.dumps(value)[1:-1]
            else: value_str = json.dumps(value)
            filled_str = re.sub(re.escape(placeholder), lambda _: value_str, filled_str)
        filled_json = json.loads(filled_str)
        print(f"[template_processor] Filled JSON generated successfully.") # ログ変更
        # print(f"[template_processor] Filled JSON: {filled_json}") # 詳細すぎるのでコメントアウト
        return filled_json
    except FileNotFoundError: print(f"[template_processor] エラー: テンプレートファイルが見つかりません: {template_path}"); raise
    except json.JSONDecodeError as e: print(f"[template_processor] エラー: JSON解析失敗(置換後): {e}\nString: {filled_str}"); raise
    except Exception as e: print(f"[template_processor] エラー: テンプレート処理中: {e}"); raise

# --- ▼▼▼ YAML読み込み関数を追加 ▼▼▼ ---
def load_config_from_yaml(filepath: str) -> Optional[Dict[str, Any]]:
    """YAML設定ファイルを読み込み、設定辞書を返す"""
    if yaml is None:
        print("[template_processor] エラー: PyYAMLがインポートされていないため、YAMLファイルを読み込めません。")
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f) # 安全な読み込み関数を使用

        if not isinstance(config_data, dict):
            print(f"[template_processor] エラー: YAMLファイルの内容が辞書形式ではありません: {filepath}")
            return None

        # --- (オプション) 実行設定の型変換とデフォルト値設定 ---
        # headless: 文字列 "true" (大文字小文字問わず) の場合に True、それ以外は False (デフォルト: True)
        config_data['headless'] = str(config_data.get('headless', 'true')).lower() == 'true'

        # slow_mo: 数値に変換。変換できなければ0 (デフォルト: 0)
        try:
            config_data['slow_mo'] = int(config_data.get('slow_mo', 0))
        except (ValueError, TypeError):
            print(f"[template_processor] 警告: YAML内の 'slow_mo' の値が無効です。0を使用します。")
            config_data['slow_mo'] = 0

        # default_timeout_ms: 数値に変換。なければ None のまま
        timeout_val = config_data.get('default_timeout_ms')
        if timeout_val is not None:
            try:
                config_data['default_timeout_ms'] = int(timeout_val)
            except (ValueError, TypeError):
                print(f"[template_processor] 警告: YAML内の 'default_timeout_ms' の値が無効です。無視します。")
                config_data['default_timeout_ms'] = None # 無効な場合はNoneに戻す

        print(f"[template_processor] YAML設定読み込み完了: {filepath}")
        return config_data

    except FileNotFoundError:
        print(f"[template_processor] エラー: YAML設定ファイルが見つかりません: {filepath}")
        return None
    except yaml.YAMLError as e:
        print(f"[template_processor] エラー: YAMLファイルのパースに失敗しました: {filepath}\n{e}")
        return None
    except Exception as e:
        print(f"[template_processor] エラー: YAML設定ファイルの読み込み中に予期せぬエラー: {filepath}\n{e}")
        return None

# --- ▼▼▼ 設定分割関数を追加 ▼▼▼ ---
def separate_config(config_data: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any], Dict[str, Any]]:
    """
    YAMLから読み込んだ設定辞書を、テンプレートパス、テンプレート用パラメータ、
    Web-Runner実行設定の3つに分割する。
    """
    if not isinstance(config_data, dict):
        return None, {}, {} # 不正な入力

    template_path = config_data.get("target_template")
    if template_path and not isinstance(template_path, str):
        print("[template_processor] 警告: 'target_template' の値が文字列ではありません。Noneとして扱います。")
        template_path = None

    # Web-Runner実行設定用のキーを定義
    run_setting_keys = {"headless", "slow_mo", "default_timeout_ms"}

    run_settings: Dict[str, Any] = {}
    template_params: Dict[str, Any] = {}

    for key, value in config_data.items():
        if key == "target_template":
            continue # テンプレートパスは除外
        elif key in run_setting_keys:
            run_settings[key] = value # 実行設定として格納
        else:
            template_params[key] = value # それ以外はテンプレート用パラメータ

    print(f"[template_processor] Config separated: template='{template_path}', params={list(template_params.keys())}, run_settings={list(run_settings.keys())}")
    return template_path, template_params, run_settings

# --- 単体テスト用 ---
if __name__ == '__main__':
    # --- fill_template のテスト (既存) ---
    print("\n--- Testing fill_template ---")
    test_template_json = './json/pmda_template.json'
    test_params_json = {'薬剤名': 'ロキソニン'}
    try:
        result_json = fill_template(test_template_json, test_params_json)
        print("fill_template Result:")
        print(json.dumps(result_json, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"fill_template Test Failed: {e}")
    print("-" * 20)

    # --- load_config_from_yaml と separate_config のテスト (新規追加) ---
    print("\n--- Testing YAML loading and separation ---")
    # テスト用のYAMLファイルを作成 (なければ)
    test_yaml_content = """
target_template: "./json/pmda_template.json"
薬剤名: "バファリン"
headless: false
slow_mo: "300" # 文字列でもOKのはず
# default_timeout_ms: 15000 # オプション
another_param: 123 # 数値パラメータの例
"""
    test_yaml_path = './temp_test_config.yaml'
    try:
        with open(test_yaml_path, 'w', encoding='utf-8') as f:
            f.write(test_yaml_content)
        print(f"Created temporary test YAML: {test_yaml_path}")

        # YAML読み込みテスト
        loaded_config = load_config_from_yaml(test_yaml_path)
        if loaded_config:
            print("\nload_config_from_yaml Result:")
            print(json.dumps(loaded_config, indent=2, ensure_ascii=False))

            # 設定分割テスト
            t_path, t_params, r_settings = separate_config(loaded_config)
            print("\nseparate_config Result:")
            print(f"  Template Path: {t_path}")
            print(f"  Template Params: {json.dumps(t_params, indent=2, ensure_ascii=False)}")
            print(f"  Run Settings: {r_settings}")

            # さらに fill_template も試す
            if t_path and t_params:
                print("\nTesting fill_template with separated params:")
                try:
                    final_json = fill_template(t_path, t_params)
                    print("Final Filled JSON:")
                    print(json.dumps(final_json, indent=2, ensure_ascii=False))
                except Exception as e_fill:
                    print(f"Fill template failed after separation: {e_fill}")

        else:
            print("\nYAML loading failed.")

    except Exception as e_yaml_test:
        print(f"\nYAML Test Error: {e_yaml_test}")
    finally:
        # テスト用ファイルを削除
        if os.path.exists(test_yaml_path):
            try:
                os.remove(test_yaml_path)
                print(f"\nRemoved temporary test YAML: {test_yaml_path}")
            except Exception as e_remove:
                print(f"\nFailed to remove temporary test YAML: {e_remove}")
    print("-" * 20)