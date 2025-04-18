# --- ファイル: generate_action_json_from_llm.py (プロンプト修正・ファイル名変更対応版) ---

import os
import google.generativeai as genai
import pathlib
from dotenv import load_dotenv
import logging
import traceback
import time
import json
from typing import List, Dict, Any, Optional, Tuple
import re # ファイル名サニタイズ用
from urllib.parse import urlparse # ファイル名サニタイズ用

# --- 初期設定 ---
load_dotenv()
api_key = os.getenv('API_KEY')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__) # このファイル用のロガー

if not api_key:
    logger.error("API_KEYが環境変数に見つかりません。.envファイルを確認してください。")
    exit()

try:
    genai.configure(api_key=api_key, transport='rest')
except Exception as e:
    logger.error(f"Geminiの設定中にエラーが発生しました: {e}")
    exit()

# --- Geminiモデル設定 ---
MODEL_NAME = "gemini-2.0-flash" # または "gemini-1.5-pro-latest" など
try:
    model = genai.GenerativeModel(model_name=MODEL_NAME)
    logger.info(f"Geminiモデル '{MODEL_NAME}' を初期化しました。")
except Exception as e:
    logger.warning(f"モデル '{MODEL_NAME}' の初期化に失敗: {e}. Flashにフォールバックします。")
    try:
        # MODEL_NAME = "gemini-1.5-flash-latest" # 正しいFlashモデル名に
        model = genai.GenerativeModel(model_name=MODEL_NAME) # Flashモデル名を確認
        logger.info(f"Geminiモデル '{MODEL_NAME}' (フォールバック) を初期化しました。")
    except Exception as e2:
        logger.error(f"フォールバックモデル '{MODEL_NAME}' の初期化にも失敗しました: {e2}")
        exit()


# --- ディレクトリ設定 ---
HTML_INPUT_DIR = pathlib.Path("./real_html_outputs")
ACTION_JSON_OUTPUT_DIR = pathlib.Path("./json_generated_by_llm")

# --- ファイル名生成関数 ---
def sanitize_filename(url: str) -> str:
    """URLから安全なファイル名を生成する"""
    try:
        parsed = urlparse(url)
        path_query = f"{parsed.path.strip('/')}_{parsed.query}".replace('/', '_').replace('&', '_').replace('=', '-')
        filename = f"{parsed.netloc}_{path_query}"
        filename = re.sub(r'[^a-zA-Z0-9_-]', '_', filename).strip('_')
        max_len = 150
        if len(filename) > max_len: filename = filename[:max_len]
        return f"{filename}.html" if filename else "default_page.html"
    except Exception as e:
        logger.warning(f"URLからのファイル名生成エラー '{url}': {e}")
        return "default_page.html"

# --- テストシナリオ定義 (変更なし) ---
TEST_SCENARIOS: List[Dict[str, Any]] = [
    {
        "scenario_name": "Google_Search_Click_First_And_Next",
        "target_url": "https://www.google.co.jp/",
        "output_json_filename": "google_search_click_first_and_next.json",
        "steps": [
            {"memo": "検索ボックスに入力", "instruction": "検索キーワードを入力するテキストエリア", "action_type": "input", "action_value": "Playwrightによる自動テスト"},
            {"memo": "Google検索ボタンをクリック", "instruction": "「Google 検索」ボタン", "action_type": "click", "wait_time_ms": 7000},
            {"memo": "「次へ」リンクをクリック", "instruction": "「次へ」のページネーションリンク", "action_type": "click", "depends_on_previous_html": f"{HTML_INPUT_DIR}/{sanitize_filename('https://www.google.com/search?q=Playwrightによる自動テスト')}", "wait_time_ms": 7000},
            {"memo": "最初の検索結果リンクをクリック", "instruction": "最初の検索結果のタイトルへのリンク", "action_type": "click", "depends_on_previous_html": f"{HTML_INPUT_DIR}/{sanitize_filename('https://www.google.com/search?q=Playwrightによる自動テスト')}", "wait_time_ms": 10000}
        ]
    },
    {
        "scenario_name": "ChatGPT_Lab_Get_Info",
        "target_url": "https://chatgpt-lab.com/n/nc1a8b4bb4911",
        "output_json_filename": "chatgpt_lab_get_info.json",
        "steps": [
            {"memo": "記事タイトルを取得 (textContent)", "instruction": "記事の見出し (タイトル)", "action_type": "get_text_content"},
            {"memo": "記事本文の最初の段落を取得 (innerText)", "instruction": "記事本文の最初の段落", "action_type": "get_inner_text"},
            {"memo": "記事画像のsrc属性を取得", "instruction": "記事の中にある最初の画像", "action_type": "get_attribute", "attribute_name": "src"},
            {"memo": "共有ボタンのツールチップを取得 (カスタム属性)", "instruction": "共有ボタン", "action_type": "get_attribute", "attribute_name": "data-tooltip"}
        ]
    },
]

# --- LLM呼び出し関数 (プロンプト修正版) ---
def get_element_info_list_with_fallback(
    html_content: str, instruction: str, max_retries=3, retry_delay=5
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str], Optional[Dict[str, Any]]]:
    """
    要素特定情報の候補リスト、フォールバック用セレクター、アクション詳細を生成。
    戻り値: (ヒントリスト or None, フォールバックセレクター or None, アクション詳細dict or None)
    """
    logger.info("-" * 30)
    logger.info(f"指示に基づいて要素情報リスト、フォールバックセレクタ、アクション詳細を生成中: '{instruction}'")

    # --- ▼▼▼ プロンプト修正 ▼▼▼ ---
    prompt = f"""
あなたはHTMLコンテンツとユーザーの指示を理解し、Web自動操作ツールPlaywrightで要素を特定し、適切なアクションを判断するエキスパートです。
以下のHTMLコンテンツとユーザーの指示を読み、指示された要素を特定するために**考えられる複数の方法**とその**有効性の確信度**、**推奨されるアクション**、そして**フォールバック用のCSSセレクター**をJSON形式で出力してください。

# HTMLコンテンツ
```html
{html_content}


ユーザーの指示
「{instruction}」

あなたのタスク

要素特定ヒント (hints): 指示された要素を特定するために、以下の情報をPlaywrightのLocator APIが利用しやすい形で、変更に強い方法を優先して抽出し、確信度と共にJSONオブジェクトの**配列（リスト）**として出力 (確信度順)。

type: ("nth_child", "test_id", "role_and_text", "aria_label", "text_exact", "placeholder", "css_selector_candidate")

value: 特定に必要な値。type="nth_child"の場合は通常null。

name: type="role_and_text" の場合、テキスト/aria-label。

level: type="role_and_text", role="heading" の場合、見出しレベル。

common_selector: type="nth_child" の場合、共通CSSセレクター。

index: type="nth_child" の場合、0始まりのインデックス。

confidence: ("high", "medium", "low")。
重要: この hints 配列内のオブジェクトには、絶対に fallback_selector キーを含めないでください。

フォールバックセレクター (fallback_selector): もし特定可能であれば、最も信頼でき、構造変化に強く、簡潔な単一のCSSセレクター (文字列) を指定してください。IDや意味のある属性（role, name, data-testidなど）を優先してください。特定が難しい、あるいは不要と判断した場合は null を指定してください。

アクション詳細 (action_details): ユーザー指示から実行すべきアクションを推測し、以下のキーを持つオブジェクトで出力。

action_type: 推奨されるアクション名 (文字列, 例: "input", "click", "get_text_content", "get_attribute", "select_option", "hover", "scroll_to_element" など)。不明な場合は "unknown"。

value: (Optional[str|float|int]) action_type="input" の場合に入力する値、action_type="sleep" の場合に待機秒数。 指示から入力値（例：「〇〇」）を正確に抽出すること。

attribute_name: (Optional[str]) action_type="get_attribute" の場合に取得する属性名。指示から属性名（例：「href」）を正確に抽出すること。

option_type: (Optional[str]) action_type="select_option" の場合の選択タイプ ('value', 'index', 'label')。

option_value: (Optional[str|int]) action_type="select_option" の場合の選択値。

出力JSONフォーマット (この形式のJSONオブジェクトのみを出力すること):
{{
"hints": [
{{ "type": "...", "value": ..., "confidence": "high", ... }},
{{ "type": "...", "value": ..., "confidence": "medium", ... }}
],
"fallback_selector": "CSSセレクター (文字列) or null",
"action_details": {{
"action_type": "推奨アクション名",
"value": "入力値など or null",
"attribute_name": "属性名 or null",
"option_type": "選択タイプ or null",
"option_value": "選択値 or null"
}}
}}
要素が見つからない/アクションが不明な場合は、"hints": [], "fallback_selector": null, "action_details": {{"action_type": "unknown"}} のように返してください。
他の説明やマークダウンは含めないでください。
出力JSON:"""
# --- ▲▲▲ プロンプト修正 ▲▲▲ ---

    generated_hints = None
    fallback_selector = None
    action_details = None
    for attempt in range(max_retries):
        try:
            time.sleep(10) # APIレート制限考慮
            logger.info(f"Gemini APIへのリクエスト送信 (試行 {attempt + 1}/{max_retries})...")
            start_time = time.monotonic()
            safety_settings = [ {"category": c, "threshold": "BLOCK_NONE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]]
            response = None
            try:
                generation_config=genai.types.GenerationConfig(response_mime_type="application/json")
                response = model.generate_content(prompt, generation_config=generation_config, safety_settings=safety_settings)
                logger.info("Gemini APIをJSONモードで呼び出しました。")
            except Exception as json_mode_err:
                logger.warning(f"JSONモードAPI呼び出し失敗: {json_mode_err}. 通常モード試行...")
                response = model.generate_content(prompt, safety_settings=safety_settings)
                logger.info("Gemini APIを通常モードで呼び出しました。")

            end_time = time.monotonic()
            logger.info(f"Gemini API応答受信。処理時間: {end_time - start_time:.2f} 秒")

            if response and response.candidates and response.text:
                raw_response_text = response.text.strip()
                logger.debug(f"生の応答テキスト:\n{raw_response_text}")
                json_text = raw_response_text
                if json_text.startswith("```json") and json_text.endswith("```"): json_text = json_text[len("```json"): -len("```")].strip()
                elif json_text.startswith("```") and json_text.endswith("```"): json_text = json_text[len("```"): -len("```")].strip()
                try:
                    parsed_json = json.loads(json_text)
                    if (isinstance(parsed_json, dict) and
                            "hints" in parsed_json and isinstance(parsed_json["hints"], list) and
                            "action_details" in parsed_json and isinstance(parsed_json["action_details"], dict) and
                            "action_type" in parsed_json["action_details"]):
                        generated_hints = parsed_json["hints"]
                        fallback_selector = parsed_json.get("fallback_selector")
                        action_details = parsed_json["action_details"]
                        if not isinstance(fallback_selector, str) and fallback_selector is not None: fallback_selector = None
                        logger.info("JSONオブジェクト形式の情報を正常に抽出・解析しました。")
                        break
                    else: logger.warning(f"Geminiが期待した形式でない応答: {type(parsed_json)}")
                except json.JSONDecodeError as e: logger.warning(f"応答JSONの解析失敗: {e}\nText: {json_text}")
            else:
                logger.warning("API応答が空か候補なし。")
                if response: logger.debug(f"詳細応答: {response}")

        except Exception as e:
            logger.error(f"API呼び出しエラー (試行 {attempt + 1}): {e}")
            logger.debug(traceback.format_exc())
            if attempt == max_retries - 1: logger.error("最大リトライ回数。"); return None, None, None

        if attempt < max_retries - 1: logger.info(f"{retry_delay}秒待機してリトライします..."); time.sleep(retry_delay)

    if generated_hints is not None: logger.info(f"抽出情報リスト (候補数: {len(generated_hints)}): {json.dumps(generated_hints, ensure_ascii=False, indent=2)}")
    else: logger.warning("要素特定ヒントリストは生成されませんでした。")
    if fallback_selector: logger.info(f"フォールバックセレクター: {fallback_selector}")
    else: logger.info("フォールバックセレクターは生成されませんでした。")
    if action_details: logger.info(f"アクション詳細: {json.dumps(action_details, ensure_ascii=False)}")
    else: logger.warning("アクション詳細が生成されませんでした。")

    return generated_hints, fallback_selector, action_details

def generate_action_json_files():
    #"""テストシナリオに基づいてアクションJSONファイルを生成する"""
    logger.info(">>> アクションJSONファイル生成を開始します <<<")
    ACTION_JSON_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    total_scenarios = len(TEST_SCENARIOS); generated_files = 0
    html_cache = {}

    for i, scenario in enumerate(TEST_SCENARIOS):
        scenario_name = scenario["scenario_name"]
        target_url = scenario["target_url"]
        output_filename = scenario["output_json_filename"]
        steps = scenario["steps"]
        output_path = ACTION_JSON_OUTPUT_DIR / output_filename

        logger.info("\n" + "=" * 50)
        logger.info(f"シナリオ {i+1}/{total_scenarios}: {scenario_name}")

        actions_for_json = []
        current_html_file_path = None

        for j, step in enumerate(steps):
            memo = step["memo"]; instruction = step["instruction"]
            depends_on_html = step.get("depends_on_previous_html"); wait_time = step.get("wait_time_ms")

            logging.info(f"-- ステップ {j+1}: {memo} (指示: '{instruction}') --")

            # HTML決定と読み込み
            if depends_on_html: current_html_file_path = pathlib.Path(depends_on_html)
            elif j == 0:
                try: current_html_file_path = HTML_INPUT_DIR / sanitize_filename(target_url)
                except Exception as e_fn: logging.error(f"初期HTMLファイル名特定エラー: {e_fn}"); continue
            if not current_html_file_path or not current_html_file_path.exists(): logging.error(f"   エラー: HTMLファイルが見つかりません: {current_html_file_path}"); continue
            html_content = None; html_path_str = str(current_html_file_path.resolve())
            if html_path_str in html_cache: html_content = html_cache[html_path_str]; logging.info(f"   キャッシュからHTML読み込み ({current_html_file_path.name})")
            else:
                try:
                    with open(current_html_file_path, 'r', encoding='utf-8') as f: html_content = f.read()
                    html_cache[html_path_str] = html_content
                    logging.info(f"   HTMLファイル読み込み ({len(html_content)}文字) - {current_html_file_path.name}")
                except Exception as e: logging.error(f"   HTML読込失敗: {e}"); continue
            if not html_content: logging.error("HTMLコンテンツが空です。"); continue

            # LLM呼び出し
            target_hints, fallback_selector_str, action_details_dict = get_element_info_list_with_fallback(html_content, instruction)

            # アクションオブジェクト作成
            action_obj: Dict[str, Any] = { "memo": memo }
            if action_details_dict and action_details_dict.get("action_type") != "unknown":
                action_obj["action"] = action_details_dict.get("action_type")
                for key in ["value", "attribute_name", "option_type", "option_value"]:
                    if action_details_dict.get(key) is not None: action_obj[key] = action_details_dict[key]
            else:
                logger.warning("LLMが有効なアクションタイプを特定できませんでした。'click' を使用します。")
                action_obj["action"] = "click" # デフォルトアクション

            action_obj["selector"] = fallback_selector_str
            action_obj["target_hints"] = target_hints if target_hints is not None else []
            if wait_time is not None: action_obj["wait_time_ms"] = wait_time

            actions_for_json.append(action_obj)
            logging.info(f"   アクション追加 (タイプ: {action_obj['action']}, ヒント数: {len(action_obj['target_hints'])}, フォールバック: {'有り' if action_obj['selector'] else '無し'})")

        # JSON出力
        final_json_data = { "target_url": target_url, "actions": actions_for_json }
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f: json.dump(final_json_data, f, indent=2, ensure_ascii=False)
            logging.info(f"アクションJSON保存: {output_path}"); generated_files += 1
        except Exception as e: logging.error(f"JSON保存エラー: {e}")

    logging.info("\n" + "=" * 50)
    logging.info(f">>> 全シナリオ処理完了 ({generated_files}/{total_scenarios} ファイル生成) <<<")
    logging.info(f"生成ファイルは '{ACTION_JSON_OUTPUT_DIR.resolve()}'")
    logging.info("=" * 50)

if __name__ == "__main__":
    try:
        generate_action_json_files()
    except Exception as e:
        logger.error(f"スクリプト実行エラー: {e}"); traceback.print_exc()

