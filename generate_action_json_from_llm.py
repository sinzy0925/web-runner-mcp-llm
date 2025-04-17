import os
import google.generativeai as genai
import pathlib
from dotenv import load_dotenv
import logging
import traceback
import time
import json
from typing import List, Dict, Any, Optional # Optional を追加
import re # ファイル名サニタイズ用
from urllib.parse import urlparse # ファイル名サニタイズ用

# --- 初期設定 ---
load_dotenv()
api_key = os.getenv('API_KEY')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

if not api_key:
    logging.error("API_KEYが環境変数に見つかりません。.envファイルを確認してください。")
    exit()

try:
    genai.configure(api_key=api_key, transport='rest')
except Exception as e:
    logging.error(f"Geminiの設定中にエラーが発生しました: {e}")
    exit()

# --- Geminiモデル設定 ---
MODEL_NAME = "gemini-2.0-flash" # または "gemini-1.5-pro-latest" など
try:
    model = genai.GenerativeModel(model_name=MODEL_NAME)
    logging.info(f"Geminiモデル '{MODEL_NAME}' を初期化しました。")
except Exception as e:
    logging.warning(f"モデル '{MODEL_NAME}' の初期化に失敗: {e}. Flashにフォールバックします。")
    try:
        # MODEL_NAME = "gemini-1.5-flash-latest" # 正しいFlashモデル名に
        model = genai.GenerativeModel(model_name=MODEL_NAME)
        logging.info(f"Geminiモデル '{MODEL_NAME}' (フォールバック) を初期化しました。")
    except Exception as e2:
        logging.error(f"フォールバックモデル '{MODEL_NAME}' の初期化にも失敗しました: {e2}")
        exit()


# --- ディレクトリ設定 ---
HTML_INPUT_DIR = pathlib.Path("./real_html_outputs")
ACTION_JSON_OUTPUT_DIR = pathlib.Path("./json_generated_by_llm")

# --- ファイル名生成関数 ---
def sanitize_filename(url: str) -> str:
    """URLから安全なファイル名を生成する"""
    try:
        parsed = urlparse(url)
        # パスとクエリを結合し、不要文字を置換、長さを制限
        path_query = f"{parsed.path.strip('/')}_{parsed.query}".replace('/', '_').replace('&', '_').replace('=', '-')
        filename = f"{parsed.netloc}_{path_query}"
        filename = re.sub(r'[^a-zA-Z0-9_-]', '_', filename).strip('_')
        # Windowsの最大パス長を考慮し、ファイル名を短めに制限 (例: 150文字)
        max_len = 150
        if len(filename) > max_len:
            filename = filename[:max_len]
        return f"{filename}.html" if filename else "default_page.html"
    except Exception as e:
        logging.warning(f"Error sanitizing URL '{url}': {e}")
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
            {"memo": "最初の検索結果リンクをクリック", "instruction": "最初の検索結果のタイトルへのリンク", "action_type": "click", "depends_on_previous_html": f"{HTML_INPUT_DIR}/{sanitize_filename('https://www.google.com/search?q=Playwrightによる自動テスト')}", "wait_time_ms": 10000} # 2ページ目のHTMLがないため、1ページ目のHTMLを使う想定（課題あり）
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

# --- LLM呼び出し関数 (プロンプトと応答処理を修正) ---
def get_element_info_list_with_fallback(
    html_content: str, instruction: str, max_retries=3, retry_delay=5
) -> tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """
    要素特定情報の候補リストと、可能であればフォールバック用セレクターを生成。
    戻り値: (ヒントリスト or None, フォールバックセレクター or None)
    """
    logging.info("-" * 30)
    logging.info(f"指示に基づいて要素情報リストとフォールバックセレクタを生成中: '{instruction}'")

    # ▼▼▼ プロンプト修正: fallback_selector を要求 ▼▼▼
    prompt = f"""
あなたはHTMLコンテンツとユーザーの指示を理解し、Web自動操作ツールPlaywrightで要素を特定するための最適な情報を抽出するエキスパートです。
以下のHTMLコンテンツとユーザーの指示を読み、指示された要素を特定するために**考えられる複数の方法**とその**有効性の確信度（high, medium, low）**をJSON形式でリストアップしてください。
さらに、**もし可能であれば、最も信頼性が高く簡潔なCSSセレクターを1つ `fallback_selector` として追加**してください。

# HTMLコンテンツ
```html
{html_content}


ユーザーの指示
「{instruction}」

あなたのタスク

ユーザーの指示に合致する要素を特定するために、以下の情報をPlaywrightのLocator APIが利用しやすい形、かつ変更に強い（頑健な）方法を優先して抽出し、それぞれの確信度と共にJSONオブジェクトの**配列（リスト）**として出力してください。リストは確信度の高い順に並べてください。
【重要】指示に「N番目」「最初の」「最後の」といった順序に関する言葉が含まれている場合:

まず、その要素が含まれるリスト全体の共通セレクター (common_selector) を特定。

次に、指示された順序 (index) を設定。

type: "nth_child" を候補に含める。

抽出/推測する情報 (各オブジェクト内):

type: 特定方法の種類 ("nth_child", "test_id", "role_and_text", "aria_label", "text_exact", "placeholder", "css_selector_candidate")

value: 特定に必要な値。type="nth_child"の場合は通常null。

name: type="role_and_text" の場合、テキストまたはaria-label。

level: type="role_and_text", role="heading" の場合、見出しレベル。

common_selector: type="nth_child" の場合に、要素群に共通するCSSセレクター。

index: type="nth_child" の場合に、0始まりのインデックス。

confidence: この特定方法が有効である確信度 ("high", "medium", "low")。

加えて、以下のキーを持つオブジェクトもJSONに含めてください:

fallback_selector: (Optional[str]) 最も信頼できる単一のCSSセレクター。複雑すぎず、IDや安定した属性を優先。特定が難しい場合は null。

出力JSONフォーマット (オブジェクトの配列 + fallback_selector):
{{
"hints": [
{{ "type": "...", "value": ..., "confidence": "high", ... }},
{{ "type": "...", "value": ..., "confidence": "medium", ... }}
],
"fallback_selector": "最も信頼できるCSSセレクター (文字列) or null"
}}
該当する要素が見つからない場合は、"hints": [] と "fallback_selector": null を持つオブジェクトを返してください。
重要: 出力は上記のJSONオブジェクト形式のみとし、他の説明やマークダウンは含めないでください。
出力JSON:"""
# ▲▲▲ プロンプト修正 ▲▲▲

    generated_hints = None
    fallback_selector = None
    for attempt in range(max_retries):
        try:
            time.sleep(10) # APIレート制限考慮
            logging.info(f"Gemini APIへのリクエスト送信 (試行 {attempt + 1}/{max_retries})...")
            start_time = time.monotonic()
            safety_settings = [ {"category": c, "threshold": "BLOCK_NONE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]]

            # --- JSONモード対応 (オプション、モデルによっては有効) ---
            response = None
            try:
                # JSONモードを試す (対応モデルの場合)
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json"
                )
                response = model.generate_content(
                    prompt,
                    generation_config=generation_config,
                    safety_settings=safety_settings
                )
                logging.info("Gemini APIをJSONモードで呼び出しました。")
            except Exception as json_mode_err:
                logging.warning(f"JSONモードでのAPI呼び出しに失敗: {json_mode_err}. 通常モードで再試行します。")
                # 通常モードで呼び出し
                response = model.generate_content(prompt, safety_settings=safety_settings)
                logging.info("Gemini APIを通常モードで呼び出しました。")
            # --- JSONモード対応ここまで ---

            end_time = time.monotonic()
            logging.info(f"Gemini API応答受信。処理時間: {end_time - start_time:.2f} 秒")

            if response and response.candidates and response.text:
                raw_response_text = response.text.strip()
                logging.debug(f"生の応答テキスト:\n{raw_response_text}")
                json_text = raw_response_text
                # マークダウンコードブロック除去
                if json_text.startswith("```json") and json_text.endswith("```"):
                    json_text = json_text[len("```json"): -len("```")].strip()
                elif json_text.startswith("```") and json_text.endswith("```"):
                    json_text = json_text[len("```"): -len("```")].strip()
                try:
                    parsed_json = json.loads(json_text)
                    # ▼▼▼ 応答形式チェックとデータ抽出 ▼▼▼
                    if isinstance(parsed_json, dict) and "hints" in parsed_json and isinstance(parsed_json["hints"], list):
                        generated_hints = parsed_json["hints"]
                        fallback_selector = parsed_json.get("fallback_selector") # Optional
                        if not isinstance(fallback_selector, str) and fallback_selector is not None:
                            logging.warning(f"fallback_selectorが文字列でないため無視します: {fallback_selector}")
                            fallback_selector = None
                        logging.info("JSONオブジェクト形式の情報を正常に抽出・解析しました。")
                        break # 成功したのでループを抜ける
                    else:
                        logging.warning(f"Geminiが期待した形式でない応答を返しました (dict、hintsキー、hintsがlist): {type(parsed_json)}")
                    # ▲▲▲ 応答形式チェックとデータ抽出 ▲▲▲
                except json.JSONDecodeError as e:
                    logging.warning(f"応答JSONの解析失敗: {e}\nText: {json_text}")
            else:
                logging.warning("API応答が空か候補なし。")
                # 応答の詳細を確認 (デバッグ用)
                if response:
                    logging.debug(f"Full response details: {response}")
                    # ブロックされた場合の理由などを確認
                    # if response.prompt_feedback: logging.warning(f"Prompt Feedback: {response.prompt_feedback}")

        except Exception as e:
            logging.error(f"API呼び出しエラー (試行 {attempt + 1}): {e}")
            logging.debug(traceback.format_exc()) # トレースバックも記録
            if attempt == max_retries - 1:
                logging.error("最大リトライ回数に達しました。")
                return None, None # エラー時は両方 None を返す

        if attempt < max_retries - 1:
            logging.info(f"{retry_delay}秒待機してリトライします...")
            time.sleep(retry_delay)

    # --- 結果のログ出力と返却 ---
    if generated_hints is not None:
        logging.info(f"抽出情報リスト (候補数: {len(generated_hints)}): {json.dumps(generated_hints, ensure_ascii=False, indent=2)}")
    else:
        logging.warning("要素特定ヒントリストは生成されませんでした。")

    if fallback_selector:
        logging.info(f"フォールバックセレクター: {fallback_selector}")
    else:
        logging.info("フォールバックセレクターは生成されませんでした。")

    return generated_hints, fallback_selector

def generate_action_json_files():
    #"""テストシナリオに基づいてアクションJSONファイルを生成する"""
    logging.info(">>> アクションJSONファイル生成を開始します <<<")
    ACTION_JSON_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    total_scenarios = len(TEST_SCENARIOS); generated_files = 0
    html_cache = {} # HTMLコンテンツのキャッシュ用

    for i, scenario in enumerate(TEST_SCENARIOS):
        scenario_name = scenario["scenario_name"]
        target_url = scenario["target_url"]
        output_filename = scenario["output_json_filename"]
        steps = scenario["steps"]
        output_path = ACTION_JSON_OUTPUT_DIR / output_filename

        logging.info("\n" + "=" * 50)
        logging.info(f"シナリオ {i+1}/{total_scenarios}: {scenario_name}")

        actions_for_json = []
        current_html_file_path = None # 現在のステップで使用するHTMLファイルパス

        for j, step in enumerate(steps):
            memo = step["memo"]
            instruction = step["instruction"]
            action_type = step.get("action_type", "click") # デフォルトはclick
            action_value = step.get("action_value")
            attribute_name = step.get("attribute_name")
            depends_on_html = step.get("depends_on_previous_html") # 前のステップの結果HTMLを使うか
            wait_time = step.get("wait_time_ms")

            logging.info(f"-- ステップ {j+1}: {memo} ({action_type}) --")

            # --- 使用するHTMLファイルを決定 ---
            if depends_on_html:
                # 指定されたパスを使用
                current_html_file_path = pathlib.Path(depends_on_html)
            elif j == 0:
                # 最初のステップは target_url からファイル名を生成
                try:
                    current_html_file_path = HTML_INPUT_DIR / sanitize_filename(target_url)
                except Exception as e_fn:
                    logging.error(f"初期HTMLファイル名の特定中にエラー: {e_fn}")
                    continue # このステップはスキップ
            # depends_on_html も j==0 でもない場合、前のステップと同じHTMLを使い続ける
            # (current_html_file_path は前のループの値が保持されている)

            if not current_html_file_path or not current_html_file_path.exists():
                logging.error(f"   エラー: HTMLファイルが見つかりません: {current_html_file_path}")
                continue # このステップはスキップ

            # --- HTMLコンテンツを読み込み (キャッシュ利用) ---
            html_content = None
            html_path_str = str(current_html_file_path.resolve()) # 絶対パスでキャッシュキー
            if html_path_str in html_cache:
                html_content = html_cache[html_path_str]
                logging.info(f"   キャッシュからHTML読み込み ({current_html_file_path.name})")
            else:
                try:
                    with open(current_html_file_path, 'r', encoding='utf-8') as f:
                        html_content = f.read()
                    html_cache[html_path_str] = html_content # キャッシュに保存
                    logging.info(f"   HTMLファイル読み込み ({len(html_content)}文字) - {current_html_file_path.name}")
                except Exception as e:
                    logging.error(f"   HTMLファイル '{current_html_file_path.name}' の読み込みに失敗しました: {e}")
                    continue # このステップはスキップ

            if not html_content: # 念のためチェック
                logging.error("HTMLコンテンツが空です。スキップします。")
                continue

            # --- LLMを呼び出してヒントリストとフォールバックセレクタを取得 ---
            target_hints, fallback_selector_str = get_element_info_list_with_fallback(html_content, instruction)

            # --- アクションオブジェクトを作成 ---
            action_obj: Dict[str, Any] = {
                "memo": memo,
                "action": action_type,
                # ★★★ selector キーを追加し、fallback_selector_str を設定 ★★★
                "selector": fallback_selector_str, # LLMが返さなければ None になる
                "target_hints": target_hints if target_hints is not None else [] # ヒントがなければ空リスト
            }
            # 警告ログ
            if target_hints is None:
                logging.warning("   警告: target_hints がLLMから取得できませんでした。")
            if fallback_selector_str is None:
                logging.warning("   警告: fallback_selector がLLMから取得できませんでした。")

            # アクションタイプに応じた他のキーを追加
            if action_type == "input" and action_value is not None:
                action_obj["value"] = action_value
            if action_type == "get_attribute" and attribute_name is not None:
                action_obj["attribute_name"] = attribute_name
            # select_option 用のキーも必要ならここに追加
            # if action_type == "select_option": ...
            if wait_time is not None:
                action_obj["wait_time_ms"] = wait_time

            actions_for_json.append(action_obj)
            logging.info(f"   アクション追加 (ヒント数: {len(action_obj['target_hints'])}, フォールバックセレクタ: {'有り' if action_obj['selector'] else '無し'})")

        # --- シナリオのJSONファイルを出力 ---
        final_json_data = {
            "target_url": target_url,
            "actions": actions_for_json
        }
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True) # 出力ディレクトリ確認
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(final_json_data, f, indent=2, ensure_ascii=False)
            logging.info(f"アクションJSON保存: {output_path}")
            generated_files += 1
        except Exception as e:
            logging.error(f"JSONファイル '{output_path}' の保存中にエラー: {e}")

    logging.info("\n" + "=" * 50)
    logging.info(f">>> 全シナリオ処理完了 ({generated_files}/{total_scenarios} ファイル生成) <<<")
    logging.info(f"生成ファイルは '{ACTION_JSON_OUTPUT_DIR.resolve()}'")
    logging.info("=" * 50)


if __name__ == "__main__":
    try:
        generate_action_json_files()
    except Exception as e:
        logging.error(f"スクリプト全体の実行中に予期せぬエラーが発生しました: {e}")
        traceback.print_exc()

