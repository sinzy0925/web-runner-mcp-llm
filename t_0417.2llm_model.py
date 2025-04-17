import google.generativeai as genai
import os
from dotenv import load_dotenv

# .env読み込みとAPIキー設定
load_dotenv()
api_key = os.getenv('API_KEY')

if not api_key:
    print("エラー: API_KEY が環境変数に見つかりません。")
    exit()

try:
    genai.configure(api_key=api_key) # Transport指定は必須ではない
except Exception as e:
    print(f"Geminiの設定中にエラー: {e}")
    exit()

print("利用可能なモデル:")
print("-" * 20)

try:
    # list_models() を使って利用可能なモデルを取得
    # generateContent メソッドをサポートするモデルに絞り込むことが多い
    models = genai.list_models()

    # generateContent をサポートするモデルをフィルタリングして表示
    found_generative_models = False
    for m in models:
        # モデルが 'generateContent' メソッドをサポートしているかチェック
        if 'generateContent' in m.supported_generation_methods:
            print(f"  モデル名 (model_name): {m.name}")
            print(f"    表示名: {m.display_name}")
            print(f"    説明: {m.description[:100]}...") # 長い場合は省略
            print(f"    入力トークン制限: {m.input_token_limit}")
            print(f"    出力トークン制限: {m.output_token_limit}")
            print("-" * 10)
            found_generative_models = True

    if not found_generative_models:
        print("generateContentをサポートするモデルが見つかりませんでした。")
        print("全モデルを表示します:")
        for m in models:
             print(f"  モデル名: {m.name}")
             print(f"    サポートメソッド: {m.supported_generation_methods}")
             print("-" * 10)


except Exception as e:
    print(f"モデルリストの取得中にエラーが発生しました: {e}")