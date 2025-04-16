# --- ファイル: web_runner_mcp_client_GUI.py (コピーボタン追加版) ---

import sys
import os
import json
import asyncio
import platform
import traceback
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Union

# --- GUIライブラリ ---
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QComboBox, QPushButton, QPlainTextEdit, QLabel, QDialog, QMessageBox,
    QCheckBox, QSpinBox
)
from PySide6.QtCore import (
    Qt, QThread, Signal, Slot, QUrl, QObject
)
# --- ▼▼▼ QClipboard をインポート ▼▼▼ ---
from PySide6.QtGui import QClipboard
# --- ▲▲▲ QClipboard をインポート ▲▲▲ ---
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

# --- 実績のあるコア関数をインポート ---
try:
    from web_runner_mcp_client_core import execute_web_runner_via_mcp
except ImportError:
    print("Error: web_runner_mcp_client_core.py not found or cannot be imported.")
    print("Please ensure web_runner_mcp_client_core.py is in the same directory.")
    sys.exit(1)

# --- utils, config のインポート ---
try:
    import config
    import utils
    # --- ▼▼▼ configからファイルパスを取得 ▼▼▼ ---
    DEFAULT_OUTPUT_FILE = Path(config.MCP_CLIENT_OUTPUT_FILE)
    # --- ▲▲▲ configからファイルパスを取得 ▲▲▲ ---
except ImportError:
    print("Warning: config.py or utils.py not found. Using default output filename './output/web_runner_mcp.txt'")
    DEFAULT_OUTPUT_FILE = Path("./output/web_runner_mcp.txt")
    utils = None

# --- 定数 ---
JSON_FOLDER = Path("./json")
GENERATOR_HTML = Path("./json_generator.html")

# --- MCP通信を行うワーカースレッド (変更なし) ---
class McpWorker(QThread):
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

            if success and isinstance(result_or_error, str):
                self.result_ready.emit(result_or_error)
            elif not success and isinstance(result_or_error, dict):
                self.error_occurred.emit(result_or_error)
            elif not success and isinstance(result_or_error, str):
                 self.error_occurred.emit({"error": "Received string error", "raw_details": result_or_error})
            else:
                 self.error_occurred.emit({"error": "Unexpected result format from core function", "result": str(result_or_error)})
        except Exception as e:
            err_msg = f"MCPワーカー実行エラー: {type(e).__name__}: {e}\n{traceback.format_exc()}"
            print(f"ERROR in McpWorker.run: {err_msg}")
            self.error_occurred.emit({"error": "Exception in McpWorker", "details": err_msg})
        finally:
            self._is_running = False
            print("DEBUG: McpWorker.run finished")

    def stop_worker(self):
        print("DEBUG: Requesting McpWorker to stop (flag set).")
        self._is_running = False


# --- GeneratorDialog クラス (変更なし) ---
class GeneratorDialog(QDialog):
    json_generated = Signal(str)
    class Bridge(QObject):
        receiveJsonSignal = Signal(str)
        @Slot(str)
        def receiveJsonFromHtml(self, jsonString):
            self.receiveJsonSignal.emit(jsonString)
    def __init__(self, html_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("JSON Generator")
        self.setGeometry(200, 200, 900, 700)
        layout = QVBoxLayout(self)
        self.webview = QWebEngineView()
        layout.addWidget(self.webview)
        self.bridge = self.Bridge(self)
        self.channel = QWebChannel(self.webview.page())
        self.webview.page().setWebChannel(self.channel)
        self.channel.registerObject("pyBridge", self.bridge)
        if html_path.exists():
            file_url = QUrl.fromLocalFile(str(html_path.resolve()))
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
                                 originalGenerateJsonData();
                                 setTimeout(() => {
                                     const jsonElement = document.getElementById('generated-json');
                                     const jsonString = jsonElement ? jsonElement.textContent : null;
                                     if (jsonString && !jsonString.startsWith('JSON') && !jsonString.startsWith('入力エラー')) {
                                         if (window.pyBridge && window.pyBridge.receiveJsonFromHtml) {
                                             window.pyBridge.receiveJsonFromHtml(jsonString);
                                         } else { console.error('Python bridge not available.'); }
                                     } else { console.log('No valid JSON to send.'); }
                                 }, 100);
                             };
                         });
                     });
                 </script>
             """
            self.webview.page().loadFinished.connect(lambda ok: self.webview.page().runJavaScript(script) if ok else None)
            self.webview.setUrl(file_url)
        else:
            error_label = QLabel(f"Error: HTML file not found at\n{html_path}")
            layout.addWidget(error_label)
        self.bridge.receiveJsonSignal.connect(self.on_json_received_from_html)

    @Slot(str)
    def on_json_received_from_html(self, json_string):
        self.json_generated.emit(json_string)
        self.accept()

    def closeEvent(self, event):
        page = self.webview.page()
        if page:
            if hasattr(self, 'channel') and self.channel:
                 self.channel.deregisterObject("pyBridge")
            self.webview.setPage(None)
        super().closeEvent(event)


# --- メインウィンドウ ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web-Runner MCP Client (Core Utilized)")
        # ウィンドウサイズを少し広げる場合
        self.setGeometry(100, 100, 850, 650)

        self.mcp_worker: Optional[McpWorker] = None
        self.generator_dialog: Optional[GeneratorDialog] = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- 上部のファイル選択・実行ボタンなど ---
        top_layout = QHBoxLayout()
        self.json_selector = QComboBox()
        self.refresh_button = QPushButton("🔄 更新")
        self.generator_button = QPushButton("JSONジェネレーター")
        self.run_button = QPushButton("実行 ▶")
        top_layout.addWidget(QLabel("実行するJSON:"))
        top_layout.addWidget(self.json_selector, 1)
        top_layout.addWidget(self.refresh_button)
        top_layout.addWidget(self.generator_button)
        top_layout.addWidget(self.run_button)
        main_layout.addLayout(top_layout)

        # --- オプション設定のレイアウト ---
        options_layout = QHBoxLayout()
        self.headless_checkbox = QCheckBox("ヘッドレスモードで実行")
        self.headless_checkbox.setChecked(False)
        self.headless_checkbox.setToolTip("チェックするとブラウザ画面を表示せずにバックグラウンドで実行します。")
        options_layout.addWidget(self.headless_checkbox)

        options_layout.addWidget(QLabel("SlowMo (ms):"))
        self.slowmo_spinbox = QSpinBox()
        self.slowmo_spinbox.setRange(0, 30000)
        self.slowmo_spinbox.setValue(0)
        self.slowmo_spinbox.setSingleStep(100)
        self.slowmo_spinbox.setToolTip("各Playwright操作間の遅延時間(ミリ秒)。デバッグ時に便利です。")
        options_layout.addWidget(self.slowmo_spinbox)

        # --- ▼▼▼ コピーボタン追加 ▼▼▼ ---
        # ファイル名をボタンテキストに表示 (パスが長い場合は調整が必要かも)
        copy_button_text = f"コピー ({os.path.join('output', DEFAULT_OUTPUT_FILE.name)})" # 例: output/ファイル名
        self.copy_button = QPushButton(copy_button_text)
        self.copy_button.setToolTip(f"結果ファイル ({DEFAULT_OUTPUT_FILE}) の内容をクリップボードにコピーします。")
        options_layout.addWidget(self.copy_button)
        # --- ▲▲▲ コピーボタン追加 ▲▲▲ ---

        options_layout.addStretch() # ボタンの後にスペーサーを追加
        main_layout.addLayout(options_layout)

        # --- 結果表示エリア ---
        self.result_display = QPlainTextEdit()
        self.result_display.setReadOnly(True)
        self.result_display.setPlaceholderText("ここに実行結果が表示されます...")
        main_layout.addWidget(self.result_display, 1) # 縦方向に伸縮

        # --- ステータスラベル ---
        self.status_label = QLabel("アイドル")
        main_layout.addWidget(self.status_label)

        # --- シグナルとスロットの接続 ---
        self.refresh_button.clicked.connect(self.populate_json_files)
        self.generator_button.clicked.connect(self.open_generator)
        self.run_button.clicked.connect(self.run_mcp)
        # --- ▼▼▼ コピーボタンのシグナル接続 ▼▼▼ ---
        self.copy_button.clicked.connect(self.copy_output_to_clipboard)
        # --- ▲▲▲ コピーボタンのシグナル接続 ▲▲▲ ---

        # --- 初期設定 ---
        JSON_FOLDER.mkdir(exist_ok=True)
        # output ディレクトリもなければ作成
        DEFAULT_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.populate_json_files()
        self.run_button.setStyleSheet("background-color: #28a745; color: white;")

    def populate_json_files(self):
        # ... (変更なし) ...
        self.json_selector.clear()
        try:
            json_files = sorted([f.name for f in JSON_FOLDER.glob("*.json") if f.is_file()])
            if json_files:
                self.json_selector.addItems(json_files)
                self.status_label.setText(f"{len(json_files)}個のJSONファイルを検出")
                self.run_button.setEnabled(True)
            else:
                self.json_selector.addItem("JSONファイルが見つかりません")
                self.status_label.setText(f"'{JSON_FOLDER}'フォルダにJSONファイルがありません")
                self.run_button.setEnabled(False)
        except Exception as e:
            self.show_error_message(f"JSONファイルの読み込みエラー: {e}")
            self.run_button.setEnabled(False)

    def open_generator(self):
        # ... (変更なし) ...
         if not GENERATOR_HTML.exists():
              self.show_error_message(f"エラー: {GENERATOR_HTML} が見つかりません。")
              return
         if self.generator_dialog is None or not self.generator_dialog.isVisible():
             self.generator_dialog = GeneratorDialog(GENERATOR_HTML, self)
             self.generator_dialog.json_generated.connect(self.paste_generated_json)
             self.generator_dialog.show()
         else:
             self.generator_dialog.raise_()
             self.generator_dialog.activateWindow()

    @Slot(str)
    def paste_generated_json(self, json_string):
        # ... (変更なし) ...
        self.result_display.setPlaceholderText("JSONジェネレーターからJSONが入力されました。\n内容を確認し、必要であればファイルに保存して選択、または直接実行してください。")
        try:
             parsed_json = json.loads(json_string)
             formatted_json = json.dumps(parsed_json, indent=2, ensure_ascii=False)
             self.result_display.setPlainText(formatted_json)
             self.status_label.setText("JSONジェネレーターからJSONを取得しました")
        except json.JSONDecodeError:
              self.show_error_message("ジェネレーターから無効なJSONを受け取りました。")
              self.result_display.setPlainText(json_string)

    def run_mcp(self, json_data: Optional[Dict[str, Any]] = None):
        # ... (変更なし) ...
        if self.mcp_worker and self.mcp_worker.isRunning():
            self.show_error_message("現在、別のタスクを実行中です。")
            return

        input_source = ""
        selected_json_input = None
        if json_data:
            selected_json_input = json_data
            input_source = "ジェネレーターからのJSON"
            self.result_display.clear()
            self.result_display.setPlaceholderText("ジェネレーターからのJSONで実行中...")
        else:
            selected_file = self.json_selector.currentText()
            if not selected_file or selected_file == "JSONファイルが見つかりません":
                self.show_error_message("実行するJSONファイルを選択してください。")
                return
            json_path = JSON_FOLDER / selected_file
            if not json_path.exists():
                 self.show_error_message(f"エラー: 選択されたファイル '{selected_file}' が見つかりません。")
                 self.populate_json_files()
                 return
            input_source = f"ファイル '{selected_file}'"
            self.result_display.clear()
            self.result_display.setPlaceholderText(f"'{selected_file}' を実行中...")
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    selected_json_input = json.load(f)
            except Exception as e:
                self.show_error_message(f"JSONファイルの読み込み/パースエラー ({selected_file}): {e}")
                self.status_label.setText("エラー")
                return

        self.status_label.setText(f"{input_source} で実行開始...")
        self.run_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.generator_button.setEnabled(False)
        self.headless_checkbox.setEnabled(False)
        self.slowmo_spinbox.setEnabled(False)
        self.copy_button.setEnabled(False) # コピーボタンも実行中は無効化

        headless_mode = self.headless_checkbox.isChecked()
        slow_mo_value = self.slowmo_spinbox.value()

        self.mcp_worker = McpWorker(selected_json_input, headless_mode, slow_mo_value)
        self.mcp_worker.result_ready.connect(self.display_result)
        self.mcp_worker.error_occurred.connect(self.display_error)
        self.mcp_worker.status_update.connect(self.update_status)
        self.mcp_worker.finished.connect(self.task_finished)
        self.mcp_worker.start()

    # --- ▼▼▼ コピー処理用スロット関数 ▼▼▼ ---
    @Slot()
    def copy_output_to_clipboard(self):
        """結果ファイルの内容をクリップボードにコピーする。"""
        output_filepath = DEFAULT_OUTPUT_FILE
        # output ディレクトリが存在しない場合は作成を試みる
        try:
            output_filepath.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"Warning: Could not create output directory {output_filepath.parent}: {e}")

        if not output_filepath.exists():
            self.show_error_message(f"エラー: 結果ファイルが見つかりません。\nパス: {output_filepath}")
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
            self.status_label.setText(f"'{output_filepath.name}' の内容をクリップボードにコピーしました。")
            # 必要であれば情報メッセージを表示
            # QMessageBox.information(self, "コピー完了", f"'{output_filepath.name}' の内容をクリップボードにコピーしました。")

        except IOError as e:
            self.show_error_message(f"エラー: 結果ファイルの読み込みに失敗しました。\nエラー: {e}")
            self.status_label.setText("コピー失敗: 読み込みエラー")
        except Exception as e:
            self.show_error_message(f"予期せぬエラーが発生しました。\nエラー: {e}")
            self.status_label.setText("コピー失敗: 不明なエラー")
            traceback.print_exc() # デバッグ用にトレースバックを出力
    # --- ▲▲▲ コピー処理用スロット関数 ▲▲▲ ---


    @Slot(str)
    def display_result(self, result_json_string: str):
        # ... (変更なし) ...
        display_text = ""
        result_data_list_for_file = None
        try:
             result_data_list = json.loads(result_json_string)
             if not isinstance(result_data_list, list):
                 raise TypeError("Result data is not a list.")
             result_data_list_for_file = result_data_list

             display_text += "--- Web Runner Execution Result ---\n\n"
             display_text += f"Overall Status: Success\n\n"

             for i, step_result in enumerate(result_data_list):
                step_num = step_result.get('step', i + 1)
                action_type = step_result.get('action', 'Unknown')
                status = step_result.get('status', 'Unknown')

                display_text += f"--- Step {step_num}: {action_type} ({status}) ---\n"

                if status == "success":
                    details_to_write = {k: v for k, v in step_result.items() if k not in ['step', 'status', 'action']}
                    if 'selector' in details_to_write:
                        display_text += f"Selector: {details_to_write.pop('selector')}\n"
                    if action_type == 'get_all_attributes':
                        attr_name = details_to_write.pop('attribute', 'N/A')
                        if 'url_list' in details_to_write: # 'url_list' キーをチェック
                            url_list = details_to_write.pop('url_list', []) # 'url_list' を取得
                            if url_list:
                                display_text += "Result (URL List):\n" + '\n'.join(f"- {str(item)}" for item in url_list if item is not None) + "\n"
                        elif 'attribute_list' in details_to_write:
                             attr_list = details_to_write.pop('attribute_list', [])
                             if attr_list:
                                 display_text += f"Result (Attribute List for '{attr_name}'):\n" + '\n'.join(f"- {str(item)}" for item in attr_list if item is not None) + "\n"
                        if 'pdf_texts' in details_to_write:
                             pdf_texts = details_to_write.pop('pdf_texts', [])
                             valid_pdf_texts = [t for t in pdf_texts if t and isinstance(t, str)]
                             if valid_pdf_texts:
                                 display_text += "Extracted PDF Texts:\n" + '\n\n--- Next PDF Text ---\n\n'.join(valid_pdf_texts) + '\n'
                        if 'scraped_texts' in details_to_write:
                             scraped_texts = details_to_write.pop('scraped_texts', [])
                             if scraped_texts:
                                 display_text += "Scraped Page Texts:\n"
                                 display_text += '\n\n--- Next Page Text ---\n\n'.join(str(t) if t is not None else '(No text or Error)' for t in scraped_texts) + '\n'
                    elif action_type == 'get_all_text_contents':
                        text_list_result = details_to_write.pop('text_list', [])
                        if isinstance(text_list_result, list):
                            valid_texts = [str(text).strip() for text in text_list_result if text is not None and str(text).strip()]
                            if valid_texts:
                                display_text += "Result Text List:\n" + '\n'.join(f"- {text}" for text in valid_texts) + "\n"
                    elif action_type in ['get_text_content', 'get_inner_text'] and 'text' in details_to_write:
                        display_text += f"Result Text:\n{details_to_write.pop('text', '')}\n"
                    elif action_type == 'get_inner_html' and 'html' in details_to_write:
                        display_text += f"Result HTML:\n{details_to_write.pop('html', '')}\n"
                    elif action_type == 'get_attribute':
                        attr_name = details_to_write.pop('attribute', ''); attr_value = details_to_write.pop('value', None)
                        display_text += f"Result Attribute ('{attr_name}'): {attr_value}\n"
                        if 'pdf_text' in details_to_write:
                            display_text += f"Extracted PDF Text:\n{details_to_write.pop('pdf_text', '')}\n"
                    if details_to_write:
                        display_text += "Other Details:\n"
                        for key, val in details_to_write.items():
                            display_text += f"  {key}: {val}\n"
                elif status == "error":
                    if step_result.get('selector'): display_text += f"Selector: {step_result.get('selector')}\n"
                    display_text += f"Message: {step_result.get('message')}\n"
                    if step_result.get('full_error'): display_text += f"Details: {step_result.get('full_error')}\n"
                    if step_result.get('error_screenshot'): display_text += f"Screenshot: {step_result.get('error_screenshot')}\n"
                else:
                    display_text += f"Message: {step_result.get('message', 'No details')}\n"
                display_text += "\n"

             self.result_display.setPlainText(display_text)
             self.status_label.setText("実行成功")

             if utils and result_data_list_for_file:
                 try:
                     utils.write_results_to_file(result_data_list_for_file, str(DEFAULT_OUTPUT_FILE))
                     print(f"Result also written to {DEFAULT_OUTPUT_FILE}")
                 except Exception as write_e:
                      print(f"Error writing results to file: {write_e}")

        except (json.JSONDecodeError, TypeError) as e:
             error_msg = f"サーバーからの応答の処理中にエラー ({type(e).__name__}):\n{result_json_string}"
             self.result_display.setPlainText(error_msg)
             self.status_label.setText("警告: 不正な応答")
             print(error_msg)

    @Slot(object)
    def display_error(self, error_info: Union[str, Dict[str, Any]]):
        # ... (変更なし) ...
        error_message = "不明なエラー"
        if isinstance(error_info, dict):
            try: error_message = json.dumps(error_info, indent=2, ensure_ascii=False)
            except Exception: error_message = str(error_info)
        elif isinstance(error_info, str):
            error_message = error_info
        self.result_display.setPlainText(f"エラーが発生しました:\n\n{error_message}")
        self.status_label.setText("エラー発生")
        # エラー時にもファイルに書き込み試行
        try:
            with open(DEFAULT_OUTPUT_FILE, 'w', encoding='utf-8') as f:
                 f.write(f"--- Execution Failed ---\n{error_message}")
            print(f"Error details written to {DEFAULT_OUTPUT_FILE}")
        except Exception as write_e:
            print(f"Error writing error details to file: {write_e}")
        # エラーメッセージボックス表示は削除（冗長なため）
        # self.show_error_message(error_message)


    @Slot(str)
    def update_status(self, status: str):
        # ... (変更なし) ...
        self.status_label.setText(status)

    @Slot()
    def task_finished(self):
        # ... (変更なし) ...
        print("DEBUG: task_finished slot called.")
        self.run_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.generator_button.setEnabled(True)
        self.headless_checkbox.setEnabled(True)
        self.slowmo_spinbox.setEnabled(True)
        self.copy_button.setEnabled(True) # コピーボタンも再度有効化
        if not self.status_label.text().startswith("エラー"):
            self.status_label.setText("アイドル")
        self.mcp_worker = None

    def show_error_message(self, message: str):
        # ... (変更なし) ...
        QMessageBox.critical(self, "エラー", message)

    def closeEvent(self, event):
        # ... (変更なし) ...
        print("Close event triggered.")
        if self.mcp_worker and self.mcp_worker.isRunning():
            print("Stopping MCP worker thread...")
            self.mcp_worker.stop_worker()
            if not self.mcp_worker.wait(3000):
                 print("Warning: Worker thread did not stop gracefully.")
        if self.generator_dialog and self.generator_dialog.isVisible():
            self.generator_dialog.close()
        print("Exiting client application.")
        event.accept()

# --- アプリケーション実行 ---
if __name__ == "__main__":
    # WindowsでのAnyIOポリシー設定 (必要に応じて)
    # if platform.system() == "Windows":
    #     asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())