# --- ファイル: web_runner_mcp_llm_client_GUI.py (LLMアクション推測対応版・完全コード) ---

import sys
import os
import json
import asyncio
import platform
import traceback
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Union
import time
import re
import logging

# --- GUIライブラリ ---
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QPlainTextEdit, QLabel, QDialog, QMessageBox,
    QCheckBox, QSpinBox, QLineEdit
)
from PySide6.QtCore import (
    Qt, QThread, Signal, Slot, QUrl, QObject
)
from PySide6.QtGui import QClipboard
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

# --- LLM JSON生成関数 ---
try:
    # ★★★ 修正されたLLM関数をインポート ★★★
    from generate_action_json_from_llm import get_element_info_list_with_fallback
except ImportError:
    logging.error("LLM生成関数が見つかりません。")
    async def get_element_info_list_with_fallback(*args, **kwargs):
        logging.warning("ダミーのLLM関数が使用されています。")
        # ★ ダミーでも戻り値のタプルの要素数を合わせる ★
        return None, None, None

# --- Playwright処理 ---
try:
    import playwright_launcher
    import config
except ImportError:
    logging.critical("playwright_launcher または config が見つかりません。")
    print("Error: playwright_launcher or config not found.")
    sys.exit(1)

# --- MCPクライアントコア ---
try:
    from web_runner_mcp_client_core import execute_web_runner_via_mcp
except ImportError:
    logging.error("web_runner_mcp_client_core.py が見つかりません。")
    sys.exit(1)

# --- utils ---
try:
    import utils
    DEFAULT_OUTPUT_FILE = Path(config.MCP_CLIENT_OUTPUT_FILE)
except ImportError:
    logging.warning("config.py or utils.py not found.")
    DEFAULT_OUTPUT_FILE = Path("./output/web_runner_mcp.txt")
    utils = None

# --- HTML Processor ---
try:
    from html_processor import cleanup_html, DO_CLEANUP
    from bs4 import BeautifulSoup
except ImportError:
    logging.error("html_processor.py または BeautifulSoup が見つかりません。HTMLクリーンアップは無効になります。")
    def cleanup_html(html_content: str) -> str:
        logging.warning("ダミーの cleanup_html 関数が使用されています。")
        return html_content
    DO_CLEANUP = False

# --- 定数 ---
GENERATOR_HTML = Path("./json_generator.html")
GENERATED_JSON_DIR = Path("./generated_json_from_gui")

# --- 実際のHTML取得関数 (変更なし、実装が必要) ---
async def get_actual_html(url: str) -> Optional[str]:
    """PlaywrightでHTMLを取得し、html_processorでクリーンアップする"""
    logging.info(f"実際にPlaywrightでHTMLを取得開始: {url}")
    try:
        get_html_action = [{"action": "get_inner_html", "selector": "html", "memo": "Get full HTML"}]
        html_timeout = 30000
        effective_timeout = config.DEFAULT_ACTION_TIMEOUT if hasattr(config, 'DEFAULT_ACTION_TIMEOUT') else 10000
        fetch_timeout = max(html_timeout, effective_timeout * 2)
        success, results = await playwright_launcher.run_playwright_automation_async(
            target_url=url, actions=get_html_action, headless_mode=True,
            slow_motion=0, default_timeout=fetch_timeout
        )
        if success and results and isinstance(results[0].get("html"), str):
            html_content = results[0]["html"]
            logging.info(f"HTML取得成功 (URL: {url}, Length: {len(html_content)})")
            if DO_CLEANUP:
                cleaned_html = await asyncio.to_thread(cleanup_html, html_content)
                logging.info(f"クリーンアップ後のHTMLサイズ: {len(cleaned_html)}")
                return cleaned_html
            else:
                logging.info("HTMLクリーンアップはスキップされました。")
                return html_content
        else: logging.error(f"HTML取得失敗 (URL: {url}). Result: {results}"); return None
    except Exception as e: logging.error(f"HTML取得中に予期せぬエラー (URL: {url}): {e}", exc_info=True); return None

# --- McpWorker クラス (変更なし) ---
# --- McpWorker クラス (SyntaxError修正版) ---
class McpWorker(QThread):
    """MCP通信をバックグラウンドで実行するワーカースレッド"""
    result_ready = Signal(str)
    error_occurred = Signal(object)
    status_update = Signal(str)

    def __init__(self, json_input: Dict[str, Any], headless: bool, slow_mo: int):
        super().__init__()
        self.json_input = json_input
        self.headless = headless
        self.slow_mo = slow_mo
        self._is_running = True

    def run(self):
        print("DEBUG: McpWorker.run started")
        self.status_update.emit("MCPタスク実行中...")
        success = False
        result_or_error: Union[str, Dict[str, Any]] = {"error": "Worker execution failed unexpectedly."}
        try:
            import anyio # anyioがインストールされている前提
            success, result_or_error = anyio.run(
                execute_web_runner_via_mcp,
                self.json_input,
                self.headless,
                self.slow_mo
            )
            print(f"DEBUG: execute_web_runner_via_mcp finished. Success: {success}")

            if self._is_running: # 停止要求がなければ結果を通知
                if success and isinstance(result_or_error, str):
                    self.result_ready.emit(result_or_error)
                elif not success and isinstance(result_or_error, dict):
                    self.error_occurred.emit(result_or_error)
                elif not success and isinstance(result_or_error, str):
                     self.error_occurred.emit({"error": "Received string error", "raw_details": result_or_error})
                else:
                     self.error_occurred.emit({"error": "Unexpected result format from core function", "result": str(result_or_error)})
        except ImportError:
             # --- ▼▼▼ 修正箇所 ▼▼▼ ---
             # anyio がない場合のエラー処理
             err_msg = "MCPワーカー実行エラー: 'anyio' ライブラリが必要です。\npip install anyio でインストールしてください。"
             print(f"ERROR in McpWorker.run: {err_msg}")
             if self._is_running: # 停止要求がなければシグナル発行
                 self.error_occurred.emit({"error": "Missing Dependency", "details": err_msg})
             # --- ▲▲▲ 修正箇所 ▲▲▲ ---
        except Exception as e:
            err_msg = f"MCPワーカー実行エラー: {type(e).__name__}: {e}\n{traceback.format_exc()}"
            print(f"ERROR in McpWorker.run: {err_msg}")
            if self._is_running: # 停止要求がなければシグナル発行
                self.error_occurred.emit({"error": "Exception in McpWorker", "details": err_msg})
        finally:
            self._is_running = False
            print("DEBUG: McpWorker.run finished")

    def stop_worker(self):
        """ワーカーの停止を試みる（シグナル発行抑制のため）"""
        print("DEBUG: Requesting McpWorker to stop (flag set).")
        self._is_running = False


# --- GeneratorDialog クラス (変更なし) ---
# --- GeneratorDialog クラス (SyntaxError修正版) ---
class GeneratorDialog(QDialog):
    """従来のJSONジェネレーターを表示するダイアログ"""
    json_generated = Signal(str) # 生成されたJSON文字列を通知

    class Bridge(QObject): # JavaScript と通信するためのブリッジオブジェクト
        receiveJsonSignal = Signal(str)
        # --- ▼▼▼ ここを修正 ▼▼▼ ---
        @Slot(str) # デコレータはメソッド定義の直前に記述
        def receiveJsonFromHtml(self, jsonString):
            self.receiveJsonSignal.emit(jsonString)
        # --- ▲▲▲ ここを修正 ▲▲▲ ---

    def __init__(self, html_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("JSON Generator (従来)")
        self.setGeometry(200, 200, 900, 700) # ウィンドウサイズ指定
        layout = QVBoxLayout(self)
        self.webview = QWebEngineView()
        layout.addWidget(self.webview)

        # WebChannelの設定
        self.bridge = self.Bridge(self)
        self.channel = QWebChannel(self.webview.page())
        self.webview.page().setWebChannel(self.channel)
        self.channel.registerObject("pyBridge", self.bridge) # JSから 'pyBridge' でアクセス可能に

        if html_path.exists():
            file_url = QUrl.fromLocalFile(str(html_path.resolve()))
            # HTML読み込み完了後に実行するJavaScript
            script = """
                 <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
                 <script>
                     document.addEventListener('DOMContentLoaded', function() {
                         if (typeof QWebChannel === 'undefined') { console.error('qwebchannel.js did not load'); return; }
                         new QWebChannel(qt.webChannelTransport, function(channel) {
                             window.pyBridge = channel.objects.pyBridge;
                             console.log('Python Bridge (pyBridge) initialized.');
                             const originalGenerateJsonData = window.generateJsonData;
                             window.generateJsonData = function() {
                                 originalGenerateJsonData(); // 元の関数を実行
                                 // 少し待ってからJSONを取得して送信
                                 setTimeout(() => {
                                     const jsonElement = document.getElementById('generated-json');
                                     const jsonString = jsonElement ? jsonElement.textContent : null;
                                     // 有効なJSON文字列か簡易チェック
                                     if (jsonString && !jsonString.startsWith('JSON') && !jsonString.startsWith('入力エラー')) {
                                         if (window.pyBridge && window.pyBridge.receiveJsonFromHtml) {
                                             window.pyBridge.receiveJsonFromHtml(jsonString);
                                         } else { console.error('Python bridge not available.'); }
                                     } else { console.log('No valid JSON to send.'); }
                                 }, 100); // 100ms待機
                             };
                         });
                     });
                 </script>
             """
            self.webview.page().loadFinished.connect(lambda ok: self.webview.page().runJavaScript(script) if ok else None)
            self.webview.setUrl(file_url) # HTMLファイルをロード
        else:
            # HTMLファイルが見つからない場合のエラー表示
            error_label = QLabel(f"Error: HTML file not found at\n{html_path.resolve()}")
            error_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(error_label)

        # ブリッジからのシグナルをダイアログのシグナルに接続
        self.bridge.receiveJsonSignal.connect(self.on_json_received_from_html)

    @Slot(str)
    def on_json_received_from_html(self, json_string):
        """JavaScriptからJSONを受け取ったときの処理"""
        self.json_generated.emit(json_string) # シグナルを発行
        self.accept() # ダイアログを閉じる

    def closeEvent(self, event):
        """ダイアログが閉じられるときの処理"""
        page = self.webview.page()
        if page:
            # WebChannelの登録解除など、クリーンアップ
            if hasattr(self, 'channel') and self.channel:
                 self.channel.deregisterObject("pyBridge")
            self.webview.setPage(None) # ページをクリア
        super().closeEvent(event)

# --- MainWindow クラス ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web-Runner MCP Client (LLM Interactive)")
        self.setGeometry(100, 100, 850, 700)
        self.async_task_worker: Optional[AsyncTaskWorker] = None
        self.generator_dialog: Optional[GeneratorDialog] = None
        central_widget = QWidget(); self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        # --- UI要素 (変更なし) ---
        url_layout = QHBoxLayout(); url_layout.addWidget(QLabel("ターゲットURL:"))
        self.url_input = QLineEdit(); self.url_input.setPlaceholderText("例: https://www.google.co.jp/")
        url_layout.addWidget(self.url_input, 1); main_layout.addLayout(url_layout)
        instruction_layout = QHBoxLayout(); instruction_layout.addWidget(QLabel("自然言語指示:"))
        self.instruction_input = QLineEdit(); self.instruction_input.setPlaceholderText("例: 検索ボックスに「Playwright」と入力して (入力値は「」で囲む)")
        instruction_layout.addWidget(self.instruction_input, 1); main_layout.addLayout(instruction_layout)
        action_layout = QHBoxLayout(); self.generate_run_button = QPushButton("生成して実行 ▶"); self.generate_run_button.setStyleSheet("background-color: #007bff; color: white;")
        self.generator_button = QPushButton("JSONジェネレーター(従来)"); action_layout.addWidget(self.generator_button); action_layout.addStretch(); action_layout.addWidget(self.generate_run_button)
        main_layout.addLayout(action_layout)
        options_layout = QHBoxLayout(); self.headless_checkbox = QCheckBox("ヘッドレス"); self.headless_checkbox.setChecked(False); options_layout.addWidget(self.headless_checkbox)
        options_layout.addWidget(QLabel("SlowMo:")); self.slowmo_spinbox = QSpinBox(); self.slowmo_spinbox.setRange(0, 30000); self.slowmo_spinbox.setValue(0); self.slowmo_spinbox.setSingleStep(100); options_layout.addWidget(self.slowmo_spinbox)
        copy_button_text = f"コピー ({os.path.join('output', DEFAULT_OUTPUT_FILE.name)})"; self.copy_button = QPushButton(copy_button_text); options_layout.addWidget(self.copy_button); options_layout.addStretch()
        main_layout.addLayout(options_layout)
        self.result_display = QPlainTextEdit(); self.result_display.setReadOnly(True); self.result_display.setPlaceholderText("実行結果...")
        main_layout.addWidget(self.result_display, 1)
        self.status_label = QLabel("アイドル"); main_layout.addWidget(self.status_label)
        # --- シグナル接続 ---
        self.generator_button.clicked.connect(self.open_generator)
        self.generate_run_button.clicked.connect(self.on_generate_run_clicked)
        self.copy_button.clicked.connect(self.copy_output_to_clipboard)
        # --- 初期設定 ---
        GENERATED_JSON_DIR.mkdir(exist_ok=True); DEFAULT_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    def open_generator(self):
         if not GENERATOR_HTML.exists(): self.show_error_message(f"エラー: {GENERATOR_HTML.resolve()} が見つかりません。"); return
         if self.generator_dialog is None or not self.generator_dialog.isVisible():
             self.generator_dialog = GeneratorDialog(GENERATOR_HTML, self)
             self.generator_dialog.json_generated.connect(self.paste_generated_json)
             self.generator_dialog.show()
         else: self.generator_dialog.raise_(); self.generator_dialog.activateWindow()

    @Slot(str)
    def paste_generated_json(self, json_string: str):
        self.result_display.setPlaceholderText("JSONジェネレーターからJSONが入力されました。\n内容を確認してください。")
        try:
             parsed_json = json.loads(json_string)
             formatted_json = json.dumps(parsed_json, indent=2, ensure_ascii=False)
             self.result_display.setPlainText(formatted_json)
             self.status_label.setText("JSONジェネレーターからJSONを取得しました")
        except json.JSONDecodeError: self.show_error_message("無効なJSON"); self.result_display.setPlainText(json_string)

    @Slot()
    def on_generate_run_clicked(self):
        """「生成して実行」ボタンハンドラ"""
        if hasattr(self, 'async_task_worker') and self.async_task_worker and self.async_task_worker.isRunning():
            self.show_error_message("タスク実行中です。"); return
        target_url = self.url_input.text().strip(); instruction = self.instruction_input.text().strip()
        if not target_url or not instruction: self.show_error_message("URLと指示を入力してください。"); return

        self.set_buttons_enabled(False); self.result_display.clear()
        self.result_display.setPlaceholderText(f"URL: {target_url}\n指示: {instruction}\n処理開始...")
        self.status_label.setText("処理開始..."); QApplication.processEvents()

        # AsyncTaskWorker で非同期処理を開始
        self.async_task_worker = AsyncTaskWorker(target_url, instruction,
                                                  self.headless_checkbox.isChecked(), self.slowmo_spinbox.value())
        self.async_task_worker.json_generated.connect(self.handle_json_generated)
        self.async_task_worker.mcp_result.connect(self.handle_mcp_result)
        self.async_task_worker.task_error.connect(self.handle_async_task_error)
        self.async_task_worker.finished.connect(self.handle_async_task_completion)
        self.async_task_worker.status_update.connect(self.update_status)
        self.async_task_worker.start()

    @Slot(str, str)
    def handle_json_generated(self, json_string: str, filepath: str):
        """生成JSONをUI表示・ログ"""
        try:
            self.status_label.setText(f"JSON生成完了: {Path(filepath).name}")
            self.result_display.appendPlainText("--- Generated JSON ---\n")
            self.result_display.appendPlainText(json_string)
            self.result_display.appendPlainText(f"\n(Saved to: {filepath})\n")
            self.result_display.appendPlainText("\n--- Running Web Runner ---")
        except Exception as e: logging.error(f"Error handling generated JSON: {e}")

    @Slot(bool, object)
    def handle_mcp_result(self, success: bool, result_or_error: Union[str, Dict]):
        """MCP結果処理"""
        if success and isinstance(result_or_error, str): self.display_result(result_or_error)
        elif not success: self.display_error(result_or_error)
        else: self.display_error({"error": "Unexpected MCP result format", "data": str(result_or_error)})

    @Slot(str)
    def handle_async_task_error(self, error_message: str):
        """非同期タスクエラー処理"""
        print(f"ERROR in AsyncTaskWorker: {error_message}")
        self.result_display.appendPlainText(f"\n--- Task Error ---\n{error_message}")
        self.show_error_message(f"処理中にエラーが発生しました:\n{error_message[:500]}...") # 長すぎるエラーは省略
        self.status_label.setText("エラー")

    def handle_async_task_completion(self):
        """非同期タスク完了処理"""
        print(f"DEBUG: Handling async task completion.")
        if not self.status_label.text().startswith("エラー"): self.status_label.setText("アイドル")
        self.set_buttons_enabled(True)
        self.async_task_worker = None # 完了したら参照をクリア

    @Slot(str)
    def display_result(self, result_json_string: str):
        """成功結果表示・保存"""
        self.result_display.appendPlainText("\n--- Execution Result (Success) ---")
        try:
            result_data_list = json.loads(result_json_string)
            formatted = json.dumps(result_data_list, indent=2, ensure_ascii=False)
            self.result_display.appendPlainText(formatted)
            self.status_label.setText("実行成功")
            if utils and isinstance(result_data_list, list):
                try: utils.write_results_to_file(result_data_list, str(DEFAULT_OUTPUT_FILE))
                except Exception as e: logging.error(f"結果書込エラー: {e}", exc_info=True)
        except Exception as e: logging.error(f"結果表示/処理エラー: {e}", exc_info=True); self.status_label.setText("警告: 結果処理エラー")

    @Slot(object)
    def display_error(self, error_info: Union[str, Dict[str, Any]]):
        """エラー結果を整形して表示し、ファイルに書き込む"""
        self.result_display.appendPlainText("\n--- Execution Result (Error) ---")
        error_message = str(error_info) # デフォルトはそのまま文字列化
        if isinstance(error_info, dict):
            # --- ▼▼▼ ここを修正 ▼▼▼ ---
            try:
                # 辞書の場合はJSON形式で見やすくする
                error_message = json.dumps(error_info, indent=2, ensure_ascii=False)
            except Exception:
                # JSON変換に失敗した場合はそのまま文字列化 (既に上で代入済み)
                pass
            # --- ▲▲▲ ここを修正 ▲▲▲ ---
        elif isinstance(error_info, str):
            # 文字列の場合はそのまま使う
            error_message = error_info

        self.result_display.appendPlainText(f"エラーが発生しました:\n\n{error_message}")
        self.status_label.setText("エラー発生")
        # エラー時もファイルに書き込み試行
        try:
            with open(DEFAULT_OUTPUT_FILE, 'w', encoding='utf-8') as f:
                 f.write(f"--- Execution Failed ---\n{error_message}")
            print(f"Error details written to {DEFAULT_OUTPUT_FILE}")
        except Exception as write_e:
            print(f"Error writing error details to file: {write_e}")
            logging.error(f"Error writing error details to {DEFAULT_OUTPUT_FILE}: {write_e}", exc_info=True)

    @Slot(str)
    def update_status(self, status: str): 
        self.status_label.setText(status)

    def set_buttons_enabled(self, enabled: bool):
        self.generate_run_button.setEnabled(enabled); self.generator_button.setEnabled(enabled)
        self.headless_checkbox.setEnabled(enabled); self.slowmo_spinbox.setEnabled(enabled); self.copy_button.setEnabled(enabled)

    def show_error_message(self, message: str): QMessageBox.critical(self, "エラー", message)

    @Slot()
    def copy_output_to_clipboard(self):
        output_filepath = DEFAULT_OUTPUT_FILE
        try: output_filepath.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e: print(f"Warning: Dir creation failed: {e}")
        if not output_filepath.exists(): self.show_error_message(f"ファイルなし\n{output_filepath}"); return
        try:
            with open(output_filepath, 'r', encoding='utf-8') as f: content = f.read()
            clipboard = QApplication.instance().clipboard()
            if clipboard: clipboard.setText(content); self.status_label.setText(f"'{output_filepath.name}' をコピー")
            else: self.show_error_message("クリップボードアクセス不可")
        except Exception as e: self.show_error_message(f"コピーエラー\n{e}"); logging.error("コピーエラー", exc_info=True)

    def closeEvent(self, event):
        print("Close event triggered.")
        worker = getattr(self, 'async_task_worker', None)
        if worker and worker.isRunning(): print("Stopping AsyncTaskWorker..."); worker.wait(1000)
        if self.generator_dialog and self.generator_dialog.isVisible(): self.generator_dialog.close()
        print("Exiting client application."); event.accept()

# --- 非同期処理実行用ワーカースレッド ---
class AsyncTaskWorker(QThread):
    json_generated = Signal(str, str)
    mcp_result = Signal(bool, object)
    task_error = Signal(str)
    status_update = Signal(str)

    def __init__(self, target_url: str, instruction: str, headless: bool, slow_mo: int):
        super().__init__()
        self.target_url = target_url; self.instruction = instruction; self.headless = headless; self.slow_mo = slow_mo

    def run(self):
        try: import anyio; anyio.run(self._run_async_tasks)
        except ImportError: self.task_error.emit("エラー: 'anyio' が必要です。pip install anyio")
        except Exception as e: self.task_error.emit(f"非同期タスク実行エラー: {e}\n{traceback.format_exc()}")

    async def _run_async_tasks(self):
        final_json_data = None
        try:
            self.status_update.emit("HTML取得中...")
            html_content = await get_actual_html(self.target_url) # ★★★ 実装されたHTML取得関数を呼ぶ ★★★
            if not html_content: raise Exception(f"URLからのHTML取得に失敗: {self.target_url}")

            self.status_update.emit("LLMでJSON生成中...")
            # ★★★ 修正されたLLM関数を呼ぶ ★★★
            target_hints, fallback_selector, action_details = await asyncio.to_thread(
                get_element_info_list_with_fallback, html_content, self.instruction
            )

            self.status_update.emit("アクションJSON組み立て中...")
            if not action_details or action_details.get("action_type") == "unknown":
                raise ValueError("LLMがアクションタイプを特定できませんでした。指示を具体的にしてください。")

            # --- ▼▼▼ LLMの結果からJSONを組み立てる ---
            action_obj: Dict[str, Any] = {
                "memo": self.instruction,
                "action": action_details.get("action_type"),
                "selector": fallback_selector,
                "target_hints": target_hints if target_hints is not None else [],
            }
            # アクション詳細のキーを動的に追加 (Noneでない場合のみ)
            for key in ["value", "attribute_name", "option_type", "option_value"]:
                if action_details.get(key) is not None:
                    action_obj[key] = action_details[key]
            # --- ▲▲▲ ---

            final_json_data = { "target_url": self.target_url, "actions": [action_obj] }

            self.status_update.emit("JSONファイル保存中...")
            json_string_for_signal = ""
            try:
                GENERATED_JSON_DIR.mkdir(parents=True, exist_ok=True)
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                sanitized_instruction = re.sub(r'\W+', '_', self.instruction)[:30]
                json_filename = f"generated_{action_obj['action']}_{sanitized_instruction}_{timestamp}.json"
                output_path = GENERATED_JSON_DIR / json_filename
                json_string_for_signal = json.dumps(final_json_data, indent=2, ensure_ascii=False)
                with open(output_path, "w", encoding="utf-8") as f: f.write(json_string_for_signal)
                self.json_generated.emit(json_string_for_signal, str(output_path.resolve()))
            except Exception as e: logging.error(f"生成JSON保存エラー: {e}", exc_info=True); self.status_update.emit("JSON保存エラー")

            if final_json_data:
                 self.status_update.emit("Web-Runner実行中 (MCP経由)...")
                 success, result_or_error = await execute_web_runner_via_mcp(
                     final_json_data, self.headless, self.slow_mo
                 )
                 self.mcp_result.emit(success, result_or_error)
            else: raise Exception("アクションJSON組み立て失敗")

        except Exception as e:
            self.task_error.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")

# --- アプリケーション実行 ---
if __name__ == "__main__":
    log_file = "gui_client.log"; log_dir = Path("./logs"); log_dir.mkdir(exist_ok=True); log_path = log_dir / log_file
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] %(name)s: %(message)s', handlers=[logging.FileHandler(log_path, encoding='utf-8')])
    console_handler = logging.StreamHandler(sys.stdout); console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] %(name)s: %(message)s'); console_handler.setFormatter(formatter)
    logging.getLogger().addHandler(console_handler)

    # ライブラリチェック
    try: import PySide6; import google.generativeai; import playwright; import bs4; import anyio
    except ImportError as e: print(f"エラー: 必要ライブラリ不足: {e.name}\npip install PySide6 google-generativeai playwright beautifulsoup4 anyio lxml"); sys.exit(1)

    # Playwright ブラウザチェックは削除（launcher側でハンドルされるはず）

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())