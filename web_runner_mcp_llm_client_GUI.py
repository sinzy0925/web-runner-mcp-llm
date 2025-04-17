# --- ファイル: web_runner_mcp_llm_client_GUI.py (完全コード・HTML取得以外実装) ---

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
    from generate_action_json_from_llm import get_element_info_list_with_fallback
except ImportError:
    logging.error("LLM生成関数が見つかりません。")
    async def get_element_info_list_with_fallback(*args, **kwargs):
        logging.warning("ダミーのLLM関数が使用されています。")
        return None, None

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

# --- 実際のHTML取得関数 (実装が必要) ---
async def get_actual_html(url: str) -> Optional[str]:
    """PlaywrightでHTMLを取得し、html_processorでクリーンアップする"""
    logging.info(f"実際にPlaywrightでHTMLを取得開始: {url}")
    try:
        get_html_action = [{"action": "get_inner_html", "selector": "html", "memo": "Get full HTML"}]
        html_timeout = 30000
        effective_timeout = config.DEFAULT_ACTION_TIMEOUT if hasattr(config, 'DEFAULT_ACTION_TIMEOUT') else 10000
        fetch_timeout = max(html_timeout, effective_timeout * 2)

        # === ▼▼▼ ここで Playwright を呼び出す ▼▼▼ ===
        # 例:
        success, results = await playwright_launcher.run_playwright_automation_async(
            target_url=url, actions=get_html_action, headless_mode=True,
            slow_motion=0, default_timeout=fetch_timeout
        )
        # =======================================

        if success and results and isinstance(results[0].get("html"), str):
            html_content = results[0]["html"]
            logging.info(f"HTML取得成功 (URL: {url}, Length: {len(html_content)})")
            if DO_CLEANUP:
                # ★★★ クリーンアップは同期関数なので await asyncio.to_thread を使うのがより安全 ★★★
                # cleaned_html = cleanup_html(html_content)
                cleaned_html = await asyncio.to_thread(cleanup_html, html_content)
                logging.info(f"クリーンアップ後のHTMLサイズ: {len(cleaned_html)}")
                return cleaned_html
            else:
                logging.info("HTMLクリーンアップはスキップされました。")
                return html_content
        else:
            logging.error(f"HTML取得失敗 (URL: {url}). Result: {results}")
            return None
    except Exception as e:
        logging.error(f"HTML取得中に予期せぬエラー (URL: {url}): {e}", exc_info=True)
        return None


# --- McpWorker クラス ---
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
            import anyio
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
            err_msg = "MCPワーカー実行エラー: 'anyio' ライブラリが必要です。\npip install anyio でインストールしてください。"
            print(f"ERROR in McpWorker.run: {err_msg}")
            if self._is_running: self.error_occurred.emit({"error": "Missing Dependency", "details": err_msg})
        except Exception as e:
            err_msg = f"MCPワーカー実行エラー: {type(e).__name__}: {e}\n{traceback.format_exc()}"
            print(f"ERROR in McpWorker.run: {err_msg}")
            if self._is_running: self.error_occurred.emit({"error": "Exception in McpWorker", "details": err_msg})
        finally:
            self._is_running = False
            print("DEBUG: McpWorker.run finished")

    def stop_worker(self):
        """ワーカーの停止を試みる（シグナル発行抑制のため）"""
        print("DEBUG: Requesting McpWorker to stop (flag set).")
        self._is_running = False

# --- GeneratorDialog クラス ---
class GeneratorDialog(QDialog):
    """従来のJSONジェネレーターを表示するダイアログ"""
    json_generated = Signal(str) # 生成されたJSON文字列を通知

    class Bridge(QObject): # JavaScript と通信するためのブリッジオブジェクト
        receiveJsonSignal = Signal(str)
        @Slot(str)
        def receiveJsonFromHtml(self, jsonString):
            self.receiveJsonSignal.emit(jsonString)

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
            # qwebchannel.jsをロードし、Python側ブリッジと接続
            # 元のgenerateJsonData関数をラップし、生成後にPythonにJSONを送信
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

        # --- ★ ワーカー参照を初期化 ★ ---
        self.async_task_worker: Optional[AsyncTaskWorker] = None
        self.generator_dialog: Optional[GeneratorDialog] = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- UI要素の配置 ---
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("ターゲットURL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("例: https://www.google.co.jp/")
        url_layout.addWidget(self.url_input, 1)
        main_layout.addLayout(url_layout)

        instruction_layout = QHBoxLayout()
        instruction_layout.addWidget(QLabel("自然言語指示:"))
        self.instruction_input = QLineEdit()
        self.instruction_input.setPlaceholderText("例: 検索ボックスに「Playwright」と入力して (入力値は「」で囲む)")
        instruction_layout.addWidget(self.instruction_input, 1)
        main_layout.addLayout(instruction_layout)

        action_layout = QHBoxLayout()
        self.generate_run_button = QPushButton("生成して実行 ▶")
        self.generate_run_button.setStyleSheet("background-color: #007bff; color: white;")
        self.generator_button = QPushButton("JSONジェネレーター(従来)")
        action_layout.addWidget(self.generator_button)
        action_layout.addStretch()
        action_layout.addWidget(self.generate_run_button)
        main_layout.addLayout(action_layout)

        options_layout = QHBoxLayout()
        self.headless_checkbox = QCheckBox("ヘッドレスモードで実行")
        self.headless_checkbox.setChecked(False)
        self.headless_checkbox.setToolTip("バックグラウンドで実行します。")
        options_layout.addWidget(self.headless_checkbox)
        options_layout.addWidget(QLabel("SlowMo (ms):"))
        self.slowmo_spinbox = QSpinBox()
        self.slowmo_spinbox.setRange(0, 30000); self.slowmo_spinbox.setValue(0); self.slowmo_spinbox.setSingleStep(100)
        self.slowmo_spinbox.setToolTip("操作間の遅延(ミリ秒)。")
        options_layout.addWidget(self.slowmo_spinbox)
        copy_button_text = f"コピー ({os.path.join('output', DEFAULT_OUTPUT_FILE.name)})"
        self.copy_button = QPushButton(copy_button_text)
        self.copy_button.setToolTip(f"結果ファイル ({DEFAULT_OUTPUT_FILE}) の内容をコピー。")
        options_layout.addWidget(self.copy_button)
        options_layout.addStretch()
        main_layout.addLayout(options_layout)

        self.result_display = QPlainTextEdit()
        self.result_display.setReadOnly(True)
        self.result_display.setPlaceholderText("ここに実行結果が表示されます...")
        main_layout.addWidget(self.result_display, 1)

        self.status_label = QLabel("アイドル")
        main_layout.addWidget(self.status_label)

        # --- シグナルとスロットの接続 ---
        self.generator_button.clicked.connect(self.open_generator)
        self.generate_run_button.clicked.connect(self.on_generate_run_clicked)
        self.copy_button.clicked.connect(self.copy_output_to_clipboard)

        # --- 初期設定 ---
        GENERATED_JSON_DIR.mkdir(exist_ok=True)
        DEFAULT_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    def open_generator(self):
         """従来のJSONジェネレーターを開く"""
         if not GENERATOR_HTML.exists():
             self.show_error_message(f"エラー: {GENERATOR_HTML.resolve()} が見つかりません。")
             return
         # ダイアログが既に開いているかチェック
         if self.generator_dialog is None or not self.generator_dialog.isVisible():
             self.generator_dialog = GeneratorDialog(GENERATOR_HTML, self)
             self.generator_dialog.json_generated.connect(self.paste_generated_json)
             self.generator_dialog.show()
         else:
             # 既に開いている場合は最前面に表示
             self.generator_dialog.raise_()
             self.generator_dialog.activateWindow()

    @Slot(str)
    def paste_generated_json(self, json_string: str):
        """従来のJSONジェネレーターからJSONを受け取って表示"""
        self.result_display.setPlaceholderText("JSONジェネレーターからJSONが入力されました。\n内容を確認してください。")
        try:
             # JSONとして妥当かパースしてみる
             parsed_json = json.loads(json_string)
             # 整形して表示
             formatted_json = json.dumps(parsed_json, indent=2, ensure_ascii=False)
             self.result_display.setPlainText(formatted_json)
             self.status_label.setText("JSONジェネレーターからJSONを取得しました")
        except json.JSONDecodeError:
              self.show_error_message("ジェネレーターから無効なJSONデータを受け取りました。")
              self.result_display.setPlainText(json_string) # 生の文字列を表示

    @Slot()
    def on_generate_run_clicked(self):
        """「生成して実行」ボタンがクリックされたときの処理"""
        if hasattr(self, 'async_task_worker') and self.async_task_worker and self.async_task_worker.isRunning():
            self.show_error_message("現在、別のタスクを実行中です。")
            return

        target_url = self.url_input.text().strip()
        instruction = self.instruction_input.text().strip()
        if not target_url or not instruction:
            self.show_error_message("ターゲットURLと指示を入力してください。")
            return

        self.set_buttons_enabled(False)
        self.result_display.clear()
        self.result_display.setPlaceholderText(f"URL: {target_url}\n指示: {instruction}\n処理を開始します...")
        self.status_label.setText("処理開始...")
        QApplication.processEvents()

        # --- AsyncTaskWorker を使って非同期処理を開始 ---
        self.async_task_worker = AsyncTaskWorker(target_url, instruction,
                                                  self.headless_checkbox.isChecked(),
                                                  self.slowmo_spinbox.value())
        self.async_task_worker.json_generated.connect(self.handle_json_generated)
        self.async_task_worker.mcp_result.connect(self.handle_mcp_result)
        self.async_task_worker.task_error.connect(self.handle_async_task_error)
        self.async_task_worker.finished.connect(self.handle_async_task_completion)
        self.async_task_worker.status_update.connect(self.update_status)
        self.async_task_worker.start()

    @Slot(str, str)
    def handle_json_generated(self, json_string: str, filepath: str):
        """生成されたJSONをファイルに保存し、UIに表示する"""
        try:
            self.status_label.setText(f"JSON生成完了: {Path(filepath).name}")
            self.result_display.appendPlainText("--- Generated JSON ---\n")
            self.result_display.appendPlainText(json_string)
            self.result_display.appendPlainText(f"\n(Saved to: {filepath})\n")
            self.result_display.appendPlainText("\n--- Running Web Runner ---")
        except Exception as e:
            logging.error(f"Error handling generated JSON: {e}")
            self.result_display.appendPlainText(f"\n--- Error displaying generated JSON: {e} ---")

    @Slot(bool, object)
    def handle_mcp_result(self, success: bool, result_or_error: Union[str, Dict]):
        """MCP実行結果を処理する"""
        if success and isinstance(result_or_error, str):
            self.display_result(result_or_error)
        elif not success:
            self.display_error(result_or_error)
        else: # 予期しない形式
             self.display_error({"error": "Unexpected MCP result format", "data": str(result_or_error)})

    @Slot(str)
    def handle_async_task_error(self, error_message: str):
        """非同期タスク全体のエラーを処理する"""
        print(f"ERROR in AsyncTaskWorker: {error_message}") # コンソールにも出力
        # 詳細なエラーを結果表示エリアにも出す
        self.result_display.appendPlainText(f"\n--- Task Error ---\n{error_message}")
        # ユーザー向けにはシンプルなメッセージ
        self.show_error_message(f"処理中にエラーが発生しました。\n詳細は結果エリアを確認してください。")
        self.status_label.setText("エラー")

    def handle_async_task_completion(self):
        """非同期タスク完了後の共通処理"""
        print(f"DEBUG: Handling async task completion.")
        if not self.status_label.text().startswith("エラー"):
            self.status_label.setText("アイドル")
        self.set_buttons_enabled(True)
        # self.async_task_worker を None にするのは慎重に。参照が消える。
        # 必要ならワーカーの isRunning() を確認するなど。
        # 今回は完了時にボタンを有効化するだけにする。

    @Slot(str)
    def display_result(self, result_json_string: str):
        """成功結果を整形して表示し、ファイルに書き込む"""
        self.result_display.appendPlainText("\n--- Execution Result (Success) ---")
        try:
            result_data_list = json.loads(result_json_string)
            formatted_result = json.dumps(result_data_list, indent=2, ensure_ascii=False)
            self.result_display.appendPlainText(formatted_result)
            self.status_label.setText("実行成功")
            # ファイル書き込み (utilsが利用可能なら)
            if utils and isinstance(result_data_list, list):
                try:
                    # write_results_to_file はリスト形式の結果を期待する
                    utils.write_results_to_file(result_data_list, str(DEFAULT_OUTPUT_FILE))
                    print(f"Result also written to {DEFAULT_OUTPUT_FILE}")
                except Exception as write_e:
                    print(f"Error writing results to file: {write_e}")
                    logging.error(f"Error writing results to {DEFAULT_OUTPUT_FILE}: {write_e}", exc_info=True)
            elif not utils:
                 logging.warning("utils module not found, cannot write results to file.")
        except (json.JSONDecodeError, TypeError) as e:
             error_msg = f"サーバーからの応答の処理中にエラー ({type(e).__name__}):\n{result_json_string}"
             self.result_display.appendPlainText(f"\n--- Result Processing Error ---\n{error_msg}")
             self.status_label.setText("警告: 不正な応答")
             logging.error(error_msg)

    @Slot(object)
    def display_error(self, error_info: Union[str, Dict[str, Any]]):
        """エラー結果を整形して表示し、ファイルに書き込む"""
        self.result_display.appendPlainText("\n--- Execution Result (Error) ---")
        error_message = "不明なエラー"
        if isinstance(error_info, dict):
            try: error_message = json.dumps(error_info, indent=2, ensure_ascii=False)
            except Exception: error_message = str(error_info)
        elif isinstance(error_info, str): error_message = error_info
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
        """ステータスラベルを更新する"""
        self.status_label.setText(status)

    def set_buttons_enabled(self, enabled: bool):
        """ボタン類の有効/無効をまとめて設定"""
        self.generate_run_button.setEnabled(enabled)
        self.generator_button.setEnabled(enabled)
        self.headless_checkbox.setEnabled(enabled)
        self.slowmo_spinbox.setEnabled(enabled)
        self.copy_button.setEnabled(enabled)

    def show_error_message(self, message: str):
        """エラーメッセージボックスを表示する"""
        QMessageBox.critical(self, "エラー", message)

    @Slot()
    def copy_output_to_clipboard(self):
        """結果ファイルの内容をクリップボードにコピーする"""
        output_filepath = DEFAULT_OUTPUT_FILE
        try:
            output_filepath.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"Warning: Could not create output dir {output_filepath.parent}: {e}")
        if not output_filepath.exists():
            self.show_error_message(f"エラー: 結果ファイルが見つかりません。\n{output_filepath}")
            self.status_label.setText("コピー失敗: ファイルなし")
            return
        try:
            with open(output_filepath, 'r', encoding='utf-8') as f:
                file_content = f.read()
            clipboard = QApplication.instance().clipboard()
            if clipboard is None:
                 self.show_error_message("エラー: クリップボードにアクセスできません。")
                 self.status_label.setText("コピー失敗: クリップボードエラー")
                 return
            clipboard.setText(file_content)
            self.status_label.setText(f"'{output_filepath.name}' の内容をコピーしました。")
        except IOError as e:
            self.show_error_message(f"エラー: 結果ファイルの読み込みに失敗しました。\n{e}")
            self.status_label.setText("コピー失敗: 読み込みエラー")
        except Exception as e:
            self.show_error_message(f"コピー中に予期せぬエラーが発生しました。\n{e}")
            self.status_label.setText("コピー失敗: 不明なエラー")
            traceback.print_exc()

    def closeEvent(self, event):
        """ウィンドウを閉じる際の処理"""
        print("Close event triggered.")
        # 実行中のワーカースレッドがあれば停止を試みる
        worker_to_stop = None
        if hasattr(self, 'async_task_worker') and self.async_task_worker and self.async_task_worker.isRunning():
             worker_to_stop = self.async_task_worker
             print("Stopping AsyncTaskWorker...")
             # AsyncTaskWorkerに明示的な停止メソッドがあれば呼び出す
             # worker_to_stop.stop() # 仮
        # McpWorkerも使わなくなったが、念のため残しておく
        elif self.mcp_worker and self.mcp_worker.isRunning():
            worker_to_stop = self.mcp_worker
            print("Stopping McpWorker...")
            self.mcp_worker.stop_worker()

        if worker_to_stop:
             if not worker_to_stop.wait(2000): # 2秒待機
                  print(f"Warning: Worker thread ({type(worker_to_stop).__name__}) did not stop gracefully.")

        if self.generator_dialog and self.generator_dialog.isVisible():
            self.generator_dialog.close()
        print("Exiting client application.")
        event.accept()


# --- 非同期処理実行用ワーカースレッド ---
class AsyncTaskWorker(QThread):
    """HTML取得、LLM呼び出し、MCP実行を非同期で行うワーカースレッド"""
    json_generated = Signal(str, str) # 生成されたJSON文字列とファイルパス
    mcp_result = Signal(bool, object) # MCP実行結果 (success, result_or_error)
    task_error = Signal(str)          # タスク全体のエラーメッセージ
    status_update = Signal(str)       # ステータス更新用

    def __init__(self, target_url: str, instruction: str, headless: bool, slow_mo: int):
        super().__init__()
        self.target_url = target_url
        self.instruction = instruction
        self.headless = headless
        self.slow_mo = slow_mo

    def run(self):
        """ワーカースレッドのエントリーポイント"""
        try:
            import anyio
            # anyio.run を使って非同期関数を実行
            anyio.run(self._run_async_tasks)
        except ImportError:
            logging.error("anyioライブラリが見つかりません。pip install anyio")
            self.task_error.emit("エラー: 'anyio' ライブラリが必要です。pip install anyio")
        except Exception as e:
            error_msg = f"非同期タスク実行エラー: {type(e).__name__}: {e}\n{traceback.format_exc()}"
            logging.error(error_msg)
            self.task_error.emit(error_msg)

    async def _run_async_tasks(self):
        """実際の非同期処理"""
        final_json_data = None
        try:
            # 1. HTML取得
            self.status_update.emit("HTML取得中...")
            # ★★★ 実際のHTML取得関数を呼び出す ★★★
            html_content = await get_actual_html(self.target_url)
            if not html_content:
                 raise Exception(f"URLからのHTML取得に失敗しました: {self.target_url}")

            # 2. LLM呼び出し (ブロッキングするので別スレッド推奨)
            self.status_update.emit("LLMでJSON生成中...")
            # asyncio.to_thread を使う (Python 3.9+)
            try:
                 target_hints, fallback_selector = await asyncio.to_thread(
                     get_element_info_list_with_fallback, html_content, self.instruction
                 )
            except RuntimeError as loop_err:
                 # ネストされたイベントループエラーのハンドリング（anyio使用時は通常不要だが念のため）
                 logging.error(f"LLM呼び出しの非同期実行エラー: {loop_err}")
                 # 代替案: スレッドプールを使うなど
                 raise Exception("LLM呼び出しのスレッド実行に失敗しました。") from loop_err

            # 3. JSON組み立て
            self.status_update.emit("アクションJSON組み立て中...")
            action_type = "click" # デフォルト
            if "入力" in self.instruction or "いれる" in self.instruction: action_type = "input"
            elif "取得" in self.instruction or "テキスト" in self.instruction: action_type = "get_text_content"
            elif "属性" in self.instruction: action_type = "get_attribute"
            # 必要に応じて他のアクションタイプ判定を追加

            action_value = None
            attribute_name = None
            if action_type == "input":
                match = re.search(r"「(.*?)」|『(.*?)』|'(.*?)'|\"(.*?)\"", self.instruction)
                if match: action_value = next(g for g in match.groups() if g is not None)
                else: raise ValueError("入力アクションの入力値が見つかりませんでした (「」等で囲んでください)。")
            elif action_type == "get_attribute":
                 match = re.search(r"の(.*)属性", self.instruction)
                 if match: attribute_name = match.group(1).strip()
                 else: raise ValueError("属性取得アクションの属性名が見つかりません (「～のXXX属性」形式)。")

            action_obj: Dict[str, Any] = {
                "memo": self.instruction, "action": action_type,
                "selector": fallback_selector,
                "target_hints": target_hints if target_hints is not None else []
            }
            if action_value is not None: action_obj["value"] = action_value
            if attribute_name is not None: action_obj["attribute_name"] = attribute_name

            final_json_data = { "target_url": self.target_url, "actions": [action_obj] }

            # 4. JSONファイル出力
            self.status_update.emit("JSONファイル保存中...")
            json_string_for_signal = ""
            try:
                GENERATED_JSON_DIR.mkdir(parents=True, exist_ok=True)
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                # ファイル名にもう少し情報を加える
                sanitized_instruction = re.sub(r'\W+', '_', self.instruction)[:30]
                json_filename = f"generated_{action_type}_{sanitized_instruction}_{timestamp}.json"
                output_path = GENERATED_JSON_DIR / json_filename
                json_string_for_signal = json.dumps(final_json_data, indent=2, ensure_ascii=False)
                with open(output_path, "w", encoding="utf-8") as f: f.write(json_string_for_signal)
                # ファイルパスもシグナルで渡す
                self.json_generated.emit(json_string_for_signal, str(output_path.resolve()))
            except Exception as e:
                logging.error(f"生成JSONの保存エラー: {e}", exc_info=True)
                self.status_update.emit("JSON保存エラー") # エラーが出ても処理は続行を試みる

            # 5. Web-Runner実行 (MCP経由)
            if final_json_data:
                 self.status_update.emit("Web-Runner実行中 (MCP経由)...")
                 # execute_web_runner_via_mcp は非同期関数なので await する
                 success, result_or_error = await execute_web_runner_via_mcp(
                     final_json_data, self.headless, self.slow_mo
                 )
                 # 結果をメインスレッドに通知
                 self.mcp_result.emit(success, result_or_error)
            else:
                 # final_json_data が None の場合 (通常は発生しないはず)
                 raise Exception("アクションJSONの組み立てに失敗したため、実行できませんでした。")

        except Exception as e:
            # _run_async_tasks 内で発生したエラーをメインスレッドに通知
            error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            self.task_error.emit(error_msg)

# --- アプリケーション実行 ---
if __name__ == "__main__":
    # ロギング設定
    log_file = "gui_client.log"; log_dir = Path("./logs"); log_dir.mkdir(exist_ok=True); log_path = log_dir / log_file
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] %(name)s: %(message)s', handlers=[logging.FileHandler(log_path, encoding='utf-8')])
    # コンソールにもINFO以上を出力
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] %(name)s: %(message)s')
    console_handler.setFormatter(formatter)
    logging.getLogger().addHandler(console_handler)

    # ライブラリ不足チェック（起動前に最低限）
    try:
        import PySide6
        import google.generativeai
        import playwright
        import bs4
        import anyio
    except ImportError as e:
         print(f"エラー: 必要なライブラリが見つかりません: {e.name}")
         print("pip install PySide6 google-generativeai playwright beautifulsoup4 anyio lxml")
         sys.exit(1)

    # Playwright ブラウザチェック (初回起動時に必要)
    if not os.path.exists(os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright")):
         print("Playwrightのブラウザがインストールされていないようです。")
         print("ターミナルで playwright install を実行してください。")
         # ここでインストールを試みることも可能だが、ユーザーに任せる方が安全
         # import subprocess
         # try:
         #     subprocess.check_call([sys.executable, "-m", "playwright", "install"])
         # except Exception as install_err:
         #     print(f"Playwrightのインストールに失敗しました: {install_err}")
         #     sys.exit(1)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())