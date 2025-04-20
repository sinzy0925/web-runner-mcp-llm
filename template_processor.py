import json
import re
from typing import Dict, Any

def fill_template(template_path: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """テンプレートJSONを読み込み、プレースホルダーをパラメータで置換する"""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            template_str = f.read()

        filled_str = template_str
        # {{キー}} の形式で置換
        for key, value in parameters.items():
            placeholder = f"{{{{{key}}}}}" # {{キー}}
            # JSON文字列として埋め込むための処理
            if isinstance(value, str):
                # 文字列はJSONエスケープしてダブルクォートを除去
                value_str = json.dumps(value)[1:-1]
            else:
                # それ以外はそのままJSON文字列化
                value_str = json.dumps(value)
            # 正規表現で安全に置換（placeholderが複数あっても対応）
            filled_str = re.sub(re.escape(placeholder), lambda _: value_str, filled_str)

        # 文字列から辞書に戻す
        filled_json = json.loads(filled_str)
        print(f"[template_processor] Filled JSON: {filled_json}") # デバッグ用
        return filled_json
    except FileNotFoundError:
        print(f"[template_processor] エラー: テンプレートファイルが見つかりません: {template_path}")
        raise
    except json.JSONDecodeError as e:
        print(f"[template_processor] エラー: JSONの解析に失敗しました (置換後): {e}")
        print(f"Processed string was: {filled_str}") # 問題の文字列を出力
        raise
    except Exception as e:
        print(f"[template_processor] エラー: テンプレート処理中に予期せぬエラー: {e}")
        raise

# --- 単体テスト用 ---
if __name__ == '__main__':
    test_template = './json/pmda_template.json' # ステップ1で作成したファイル
    test_params = {'薬剤名': 'ロキソニンS'}
    try:
        result = fill_template(test_template, test_params)
        print("\n--- fill_template test result ---")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print("---------------------------------")
    except Exception as e:
        print(f"\n--- fill_template test failed ---")
        print(e)
        print("---------------------------------")