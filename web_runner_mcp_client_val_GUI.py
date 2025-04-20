# --- ファイル: web_runner_mcp_client_GUI.py (ステップ4実装) ---

import sys
import os
import json
import asyncio
import platform
import traceback
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Union
import time
import re # <<< 正規表現のためにインポート
import logging

# --- GUIライブラリ ---
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QComboBox, QPushButton, QPlainTextEdit, QLabel, QDialog, QMessageBox,
    QCheckBox, QSpinBox, QLineEdit, # <<< QLineEdit をインポート
    QSizePolicy # <<< サイズポリシーのためにインポート
)
from PySide6.QtCore import (
    Qt, QThread, Signal, Slot, QUrl, QObject
)
from PySide6.QtGui import QClipboard
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

# --- (他のインポートは変更なし) ---
try:
    from web_runner_mcp_client_core import execute_web_runner_via_mcp
except ImportError:
    print("Error: web_runner_mcp_client_core.py not found or cannot be imported.")
    print("Please ensure web_runner_mcp_client_core.py is in the same directory.")
    sys.exit(1)
try:
    import config
    import utils
    DEFAULT_OUTPUT_FILE = Path(config.MCP_CLIENT_OUTPUT_FILE)
except ImportError:
    print("Warning: config.py or utils.py not found. Using default output filename './output/web_runner_mcp.txt'")
    DEFAULT_OUTPUT_FILE = Path("./output/web_runner_mcp.txt")
    utils = None
# --- LLM JSON生成関数 (ダミーでもOK) ---
try:
    from generate_action_json_from_llm import get_element_info_list_with_fallback
except ImportError:
    logging.error("LLM生成関数が見つかりません。")
    async def get_element_info_list_with_fallback(*args, **kwargs): return None, None, None
# --- Playwright処理 ---
try: import playwright_launcher
except ImportError: logging.critical("playwright_launcherが見つかりません。"); sys.exit(1)
# --- HTML Processor (ダミーでもOK) ---
try:
    from html_processor import cleanup_html, DO_CLEANUP; from bs4 import BeautifulSoup
except ImportError: logging.error("html_processorが見つかりません。"); 

def cleanup_html(html): 
    return html; 
    DO_CLEANUP = False

# --- 定数 ---
JSON_FOLDER = Path("./json")
GENERATOR_HTML = Path("./json_generator.html")
GENERATED_JSON_DIR = Path("./generated_json_from_gui") # LLM版GUI用

# --- (AsyncTaskWorker, McpWorker, GeneratorDialog クラスは変更なし) ---
# --- McpWorker クラス (変更なし) ---
class McpWorker(QThread):
    result_ready = Signal(str)
    error_occurred = Signal(object)
    status_update = Signal(str)
    def __init__(self, json_input: Dict[str, Any], headless: bool, slow_mo: int):
        super().__init__(); self.json_input = json_input; self.headless = headless; self.slow_mo = slow_mo; self._is_running = True
    def run(self):
        print("DEBUG: McpWorker.run started"); self.status_update.emit("MCPタスク実行中...")
        success = False; result_or_error: Union[str, Dict[str, Any]] = {"error": "Worker execution failed unexpectedly."}
        try:
            import anyio
            success, result_or_error = anyio.run(execute_web_runner_via_mcp, self.json_input, self.headless, self.slow_mo)
            print(f"DEBUG: execute_web_runner_via_mcp finished. Success: {success}")
            if self._is_running:
                if success and isinstance(result_or_error, str): self.result_ready.emit(result_or_error)
                elif not success and isinstance(result_or_error, dict): self.error_occurred.emit(result_or_error)
                elif not success and isinstance(result_or_error, str): self.error_occurred.emit({"error": "Received string error", "raw_details": result_or_error})
                else: self.error_occurred.emit({"error": "Unexpected result format", "result": str(result_or_error)})
        except ImportError: err_msg = "MCPエラー: 'anyio'が必要です。pip install anyio"; print(err_msg); self.error_occurred.emit({"error": "Missing Dependency", "details": err_msg})
        except Exception as e: err_msg = f"MCPワーカー実行エラー: {type(e).__name__}: {e}\n{traceback.format_exc()}"; print(f"ERROR: {err_msg}"); self.error_occurred.emit({"error": "Exception in McpWorker", "details": err_msg})
        finally: self._is_running = False; print("DEBUG: McpWorker.run finished")
    def stop_worker(self): print("DEBUG: Requesting McpWorker stop."); self._is_running = False

# --- GeneratorDialog クラス (変更なし) ---
class GeneratorDialog(QDialog):
    json_generated = Signal(str); 
    class Bridge(QObject): 
        receiveJsonSignal = Signal(str); 
        
        @Slot(str) 
        def receiveJsonFromHtml(self, jsonString): 
            self.receiveJsonSignal.emit(jsonString)
    
    def __init__(self, html_path: Path, parent=None):
        super().__init__(parent); self.setWindowTitle("JSON Generator (従来)"); self.setGeometry(200, 200, 900, 700); layout = QVBoxLayout(self); self.webview = QWebEngineView(); layout.addWidget(self.webview)
        self.bridge = self.Bridge(self); self.channel = QWebChannel(self.webview.page()); self.webview.page().setWebChannel(self.channel); self.channel.registerObject("pyBridge", self.bridge)
        if html_path.exists():
            file_url = QUrl.fromLocalFile(str(html_path.resolve())); script = """<script src="qrc:///qtwebchannel/qwebchannel.js"></script><script>document.addEventListener('DOMContentLoaded', function() { if (typeof QWebChannel === 'undefined') { console.error('qwebchannel.js did not load'); return; } new QWebChannel(qt.webChannelTransport, function(channel) { window.pyBridge = channel.objects.pyBridge; console.log('Python Bridge initialized.'); const originalGenerateJsonData = window.generateJsonData; window.generateJsonData = function() { originalGenerateJsonData(); setTimeout(() => { const jsonElement = document.getElementById('generated-json'); const jsonString = jsonElement ? jsonElement.textContent : null; if (jsonString && !jsonString.startsWith('JSON') && !jsonString.startsWith('入力エラー')) { if (window.pyBridge && window.pyBridge.receiveJsonFromHtml) { window.pyBridge.receiveJsonFromHtml(jsonString); } else { console.error('Python bridge unavailable.'); } } else { console.log('No valid JSON.'); } }, 100); }; }); });</script>"""
            self.webview.page().loadFinished.connect(lambda ok: self.webview.page().runJavaScript(script) if ok else None); self.webview.setUrl(file_url)
        else: error_label = QLabel(f"Error: HTML not found\n{html_path.resolve()}"); error_label.setAlignment(Qt.AlignCenter); layout.addWidget(error_label)
        self.bridge.receiveJsonSignal.connect(self.on_json_received_from_html)
    @Slot(str) 
    def on_json_received_from_html(self, json_string): 
        self.json_generated.emit(json_string); 
        self.accept()
    def closeEvent(self, event): 
        page = self.webview.page(); 
        self.channel.deregisterObject("pyBridge") if page and hasattr(self, 'channel') else None; self.webview.setPage(None); super().closeEvent(event)

# --- MainWindow クラス ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web-Runner MCP Client (Template & Params)") # タイトル変更
        self.setGeometry(100, 100, 850, 650)

        self.mcp_worker: Optional[McpWorker] = None
        self.generator_dialog: Optional[GeneratorDialog] = None
        # --- ▼▼▼ ステップ4: プレースホルダー名を保持するリスト ▼▼▼ ---
        self.current_placeholders: List[str] = []
        # --- ▲▲▲ ステップ4 ▲▲▲ ---

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- 上部のファイル選択・実行ボタンなど ---
        top_layout = QHBoxLayout()
        self.json_selector = QComboBox() # テンプレート選択用
        self.refresh_button = QPushButton("🔄 更新")
        self.generator_button = QPushButton("JSONジェネレーター(従来)") # 従来版ジェネレータボタン
        self.run_button = QPushButton("実行 ▶") # テンプレート実行ボタン
        top_layout.addWidget(QLabel("テンプレートJSON:")) # ラベル変更
        top_layout.addWidget(self.json_selector, 1)
        top_layout.addWidget(self.refresh_button)
        top_layout.addWidget(self.generator_button) # 従来ボタンを追加
        top_layout.addWidget(self.run_button)
        main_layout.addLayout(top_layout)

        # --- ▼▼▼ ステップ4: target_url_memo表示用ラベル ▼▼▼ ---
        self.url_memo_label = QLabel("") # 最初は空
        self.url_memo_label.setStyleSheet("font-style: italic; color: grey;")
        self.url_memo_label.setWordWrap(True) # 長いメモは折り返す
        main_layout.addWidget(self.url_memo_label)
        # --- ▲▲▲ ステップ4 ▲▲▲ ---

        # --- ▼▼▼ ステップ4: パラメータ入力欄用ウィジェットとレイアウト ▼▼▼ ---
        self.params_widget = QWidget() # パラメータ入力欄をまとめるウィジェット
        self.params_layout = QVBoxLayout(self.params_widget) # 垂直レイアウト
        self.params_layout.setContentsMargins(0, 5, 0, 5) # 上下のマージン調整
        main_layout.addWidget(self.params_widget)
        # --- ▲▲▲ ステップ4 ▲▲▲ ---

        # --- オプション設定のレイアウト ---
        options_layout = QHBoxLayout()
        self.headless_checkbox = QCheckBox("ヘッドレスモードで実行")
        self.headless_checkbox.setChecked(False)
        self.headless_checkbox.setToolTip("チェックするとブラウザ画面を表示せずにバックグラウンドで実行します。")
        options_layout.addWidget(self.headless_checkbox)

        options_layout.addWidget(QLabel("SlowMo (ms):"))
        self.slowmo_spinbox = QSpinBox()
        self.slowmo_spinbox.setRange(0, 30000); self.slowmo_spinbox.setValue(0); self.slowmo_spinbox.setSingleStep(100)
        self.slowmo_spinbox.setToolTip("各Playwright操作間の遅延時間(ミリ秒)。デバッグ時に便利です。")
        options_layout.addWidget(self.slowmo_spinbox)

        copy_button_text = f"コピー ({os.path.join('output', DEFAULT_OUTPUT_FILE.name)})"
        self.copy_button = QPushButton(copy_button_text)
        self.copy_button.setToolTip(f"結果ファイル ({DEFAULT_OUTPUT_FILE}) の内容をクリップボードにコピーします。")
        options_layout.addWidget(self.copy_button)

        options_layout.addStretch()
        main_layout.addLayout(options_layout)

        # --- 結果表示エリア ---
        self.result_display = QPlainTextEdit()
        self.result_display.setReadOnly(True)
        self.result_display.setPlaceholderText("テンプレートを選択し、パラメータを入力して実行してください。")
        main_layout.addWidget(self.result_display, 1)

        # --- ステータスラベル ---
        self.status_label = QLabel("アイドル")
        main_layout.addWidget(self.status_label)

        # --- シグナルとスロットの接続 ---
        self.refresh_button.clicked.connect(self.populate_json_files)
        self.generator_button.clicked.connect(self.open_generator) # 従来ジェネレータ起動
        self.run_button.clicked.connect(self.run_mcp_from_template) # ★実行ボタンの接続先変更★
        self.copy_button.clicked.connect(self.copy_output_to_clipboard)
        # --- ▼▼▼ ステップ4: ファイル選択変更時の処理を接続 ▼▼▼ ---
        self.json_selector.currentIndexChanged.connect(self.on_template_selected)
        # --- ▲▲▲ ステップ4 ▲▲▲ ---

        # --- 初期設定 ---
        JSON_FOLDER.mkdir(exist_ok=True)
        DEFAULT_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.populate_json_files() # 初回読み込み
        self.run_button.setStyleSheet("background-color: #28a745; color: white;")

    def populate_json_files(self):
        """jsonフォルダのJSONファイルを読み込みドロップダウンに設定"""
        self.json_selector.clear()
        try:
            json_files = sorted([f.name for f in JSON_FOLDER.glob("*.json") if f.is_file()])
            if json_files:
                self.json_selector.addItems(json_files)
                self.status_label.setText(f"{len(json_files)}個のJSONファイルを検出")
                self.run_button.setEnabled(True)
                # --- ▼▼▼ ステップ4: 初回選択時の処理も呼び出す ▼▼▼ ---
                # currentIndexChanged シグナルが発生するので、接続されていれば on_template_selected が呼ばれるはず
                # 念のため初回も呼ぶ場合:
                # self.on_template_selected()
                # --- ▲▲▲ ステップ4 ▲▲▲ ---
            else:
                self.json_selector.addItem("JSONファイルが見つかりません")
                self.status_label.setText(f"'{JSON_FOLDER}'にJSONファイルがありません")
                self.run_button.setEnabled(False)
                # --- ▼▼▼ ステップ4: ファイルがない場合はパラメータ欄もクリア ▼▼▼ ---
                self._clear_parameter_inputs()
                self.url_memo_label.setText("")
                self.current_placeholders = []
                # --- ▲▲▲ ステップ4 ▲▲▲ ---
        except Exception as e:
            self.show_error_message(f"JSONファイルの読み込みエラー: {e}")
            self.run_button.setEnabled(False)

    # --- ▼▼▼ ステップ4: 新しいメソッド: テンプレート選択時の処理 ▼▼▼ ---
    @Slot()
    def on_template_selected(self):
        """ドロップダウンでテンプレートが選択されたときに呼ばれる"""
        selected_file = self.json_selector.currentText()
        self._clear_parameter_inputs() # 既存のパラメータ入力欄をクリア
        self.current_placeholders = [] # 保持しているプレースホルダー名もクリア
        self.url_memo_label.setText("") # URLメモもクリア

        if not selected_file or selected_file == "JSONファイルが見つかりません":
            return

        json_path = JSON_FOLDER / selected_file
        if not json_path.exists():
            # populate_json_filesでリフレッシュされるはずだが念のため
            self.status_label.setText("エラー: 選択されたファイルが見つかりません")
            return

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
                # JSONとしてパースして target_url_memo を取得
                try:
                    json_data = json.loads(file_content)
                    url_memo = json_data.get("target_url_memo")
                    if url_memo:
                        self.url_memo_label.setText(f"メモ: {url_memo}")
                    else:
                        # target_url を表示する代替案
                        target_url_display = json_data.get("target_url", "URL不明")
                        self.url_memo_label.setText(f"URL: {target_url_display}")

                except json.JSONDecodeError:
                    self.url_memo_label.setText("警告: JSONパース不可 (メモ取得できず)")


            # 正規表現で {{プレースホルダー名}} を全て見つける
            # {{ と }} の間の、} 以外の文字にマッチ
            placeholders = sorted(list(set(re.findall(r"\{\{([^}]+)\}\}", file_content))))
            self.current_placeholders = placeholders # 見つかったプレースホルダー名を保持

            if not placeholders:
                self.params_widget.setVisible(False) # プレースホルダーがなければ非表示
                return

            # パラメータ入力欄を動的に生成
            for placeholder_name in placeholders:
                param_layout = QHBoxLayout()
                label = QLabel(f"{placeholder_name}:")
                # ラベルの幅を一定にする (見た目の調整)
                label.setMinimumWidth(150)
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

                line_edit = QLineEdit()
                line_edit.setObjectName(f"param_input_{placeholder_name}") # オブジェクト名設定
                # --- ▼▼▼ 初期値設定を試みる (オプション) ▼▼▼ ---
                # テンプレート内のプレースホルダーを含む value を見つけて初期値とする (簡易版)
                #match = re.search(rf'"value":\s*"(.*{{\{{{placeholder_name}\}}}.*)"', file_content)
                #if match:
                #    # 簡単のため、プレースホルダーそのものを初期値とする
                #    line_edit.setText(f"{{{{{placeholder_name}}}}}")
                #    line_edit.setToolTip(f"'{placeholder_name}' の値を入力してください")
                # --- ▲▲▲ ---
                initial_value = f"{{{{{placeholder_name}}}}}" # 例: "{{薬剤名}}"
                line_edit.setText(initial_value)
                line_edit.setToolTip(f"'{placeholder_name}' の値を入力してください")

                param_layout.addWidget(label)
                param_layout.addWidget(line_edit, 1) # 入力欄が伸びるように
                self.params_layout.addLayout(param_layout)

            self.params_widget.setVisible(True) # パラメータ欄を表示
            self.status_label.setText(f"テンプレート '{selected_file}' 読み込み完了 ({len(placeholders)}個のパラメータ)")

        except FileNotFoundError:
             self.status_label.setText(f"エラー: ファイルが見つかりません: {json_path}")
             self._clear_parameter_inputs()
        except Exception as e:
            self.show_error_message(f"テンプレートファイル '{selected_file}' の処理エラー: {e}")
            self.status_label.setText("エラー: テンプレート処理失敗")
            self._clear_parameter_inputs()

    def _clear_parameter_inputs(self):
        """パラメータ入力欄をクリアするヘルパー関数"""
        # self.params_layout 内のすべてのレイアウトとウィジェットを削除
        while self.params_layout.count() > 0:
            item = self.params_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                # ネストされたレイアウト内のウィジェットもクリアする必要がある
                while item.layout().count() > 0:
                    child_item = item.layout().takeAt(0)
                    if child_item.widget():
                        child_item.widget().deleteLater()
                item.layout().deleteLater() # レイアウト自体も削除
        self.params_widget.setVisible(False) # クリアしたら非表示に

    # --- ▲▲▲ ステップ4 ▲▲▲ ---

    # --- ▼▼▼ ステップ5で実装するメソッドのプレースホルダー ▼▼▼ ---
    @Slot()
    def run_mcp_from_template(self):
        """テンプレートと入力されたパラメータを使ってMCP実行を開始する"""
        if self.mcp_worker and self.mcp_worker.isRunning():
            self.show_error_message("現在、別のタスクを実行中です。")
            return

        selected_file = self.json_selector.currentText()
        if not selected_file or selected_file == "JSONファイルが見つかりません" or not self.current_placeholders:
            # プレースホルダーがないテンプレートを実行する場合もここに入る可能性がある
            # 今はプレースホルダーがない場合の実行は未対応とする
             if not self.current_placeholders and selected_file != "JSONファイルが見つかりません":
                  # プレースホルダーがない場合は、パラメータ入力なしで実行するか、
                  # 別の実行ボタンを用意する必要がある。一旦エラーとする。
                  self.show_error_message(f"選択されたテンプレート '{selected_file}' にはパラメータがありません。\n（現時点ではパラメータ付きテンプレートのみ実行可能です）")
                  return
             else:
                  self.show_error_message("実行するテンプレートJSONを選択してください。")
                  return

        json_path = JSON_FOLDER / selected_file
        if not json_path.exists():
             self.show_error_message(f"エラー: 選択されたファイル '{selected_file}' が見つかりません。")
             self.populate_json_files(); return # リスト更新

        # --- ここからステップ5で実装するパラメータ取得とJSON生成ロジック ---
        parameters: Dict[str, Any] = {}
        all_params_filled = True
        for placeholder_name in self.current_placeholders:
            line_edit: Optional[QLineEdit] = self.params_widget.findChild(QLineEdit, f"param_input_{placeholder_name}")
            if line_edit:
                value_str = line_edit.text().strip()
                if not value_str or value_str == f"{{{{{placeholder_name}}}}}": # 未入力または初期値のままはエラー扱い（必要に応じて変更）
                    all_params_filled = False
                    line_edit.setStyleSheet("border: 1px solid red;") # 未入力箇所を強調
                else:
                    line_edit.setStyleSheet("") # エラー表示解除
                    # ここで型変換が必要な場合があるが、一旦文字列として扱う
                    parameters[placeholder_name] = value_str
            else:
                # オブジェクトが見つからないのは想定外
                print(f"エラー: パラメータ入力欄が見つかりません: {placeholder_name}")
                all_params_filled = False

        if not all_params_filled:
            self.show_error_message("全てのパラメータを入力してください。")
            return

        try:
            # template_processor をインポートして使用
            from template_processor import fill_template
            filled_json_data = fill_template(str(json_path), parameters)
            print("DEBUG: Generated JSON for execution:", json.dumps(filled_json_data, indent=2, ensure_ascii=False)) # デバッグ用

            # --- MCPワーカーの実行 (既存のrun_mcpとほぼ同じ) ---
            self.status_label.setText(f"テンプレート '{selected_file}' (パラメータ指定) で実行開始...")
            self.set_buttons_enabled(False) # ボタン無効化
            self.result_display.clear()
            self.result_display.setPlaceholderText(f"実行中: {selected_file} with params:\n{json.dumps(parameters, indent=2, ensure_ascii=False)}")

            headless_mode = self.headless_checkbox.isChecked()
            slow_mo_value = self.slowmo_spinbox.value()

            self.mcp_worker = McpWorker(filled_json_data, headless_mode, slow_mo_value)
            self.mcp_worker.result_ready.connect(self.display_result)
            self.mcp_worker.error_occurred.connect(self.display_error)
            self.mcp_worker.status_update.connect(self.update_status)
            self.mcp_worker.finished.connect(self.task_finished)
            self.mcp_worker.start()

        except ImportError:
             self.show_error_message("エラー: template_processor.py のインポートに失敗しました。")
             self.status_label.setText("エラー: モジュール不足")
        except FileNotFoundError:
             self.show_error_message(f"エラー: テンプレートファイルが見つかりません: {json_path}")
             self.status_label.setText("エラー: ファイルなし")
        except Exception as e:
            self.show_error_message(f"パラメータ置換または実行準備中にエラー: {e}")
            self.status_label.setText("エラー: 準備失敗")
            traceback.print_exc() # 詳細をコンソールに

        # --- ここまでステップ5で実装するロジック ---

    # --- run_mcp (従来用、LLM連携用 - 今回は呼ばれない) ---
    def run_mcp(self, json_data: Optional[Dict[str, Any]] = None):
        # このメソッドはテンプレート実行ボタンからは呼ばれなくなった
        # 従来ジェネレータからの実行や、将来LLM連携する場合に残しておく
        print("DEBUG: run_mcp called (likely from generator or future LLM)")
        # ... (run_mcpの元の実装 ... パラメータ置換なしで直接実行する) ...
        # ... (今回は実装省略) ...
        self.show_error_message("直接JSON実行は現在無効です。テンプレートを選択して実行してください。")
        pass

    # --- display_result, display_error, update_status, task_finished (変更なし) ---
    @Slot(str)
    def display_result(self, result_json_string: str):
        """成功結果表示・保存"""
        self.result_display.setPlainText("--- Web Runner Execution Result ---\n\nOverall Status: Success\n\n")
        try:
            result_data_list = json.loads(result_json_string)
            formatted = json.dumps(result_data_list, indent=2, ensure_ascii=False)
            self.result_display.appendPlainText(formatted)
            self.status_label.setText("実行成功")
            if utils and isinstance(result_data_list, list):
                try: utils.write_results_to_file(result_data_list, str(DEFAULT_OUTPUT_FILE))
                except Exception as e: logging.error(f"結果書込エラー: {e}", exc_info=True)
        except (json.JSONDecodeError, TypeError) as e:
             self.result_display.appendPlainText(f"\n--- Error Parsing Server Response ---\n{result_json_string}")
             self.status_label.setText("警告: 不正な応答")
             logging.error(f"結果JSONパース/処理エラー: {e}", exc_info=True)

    @Slot(object)
    def display_error(self, error_info: Union[str, Dict[str, Any]]):
        """エラー結果表示・保存"""
        self.result_display.setPlainText("--- Web Runner Execution Result (Error) ---\n\n")
        error_message = str(error_info)
        if isinstance(error_info, dict):
            try: error_message = json.dumps(error_info, indent=2, ensure_ascii=False)
            except Exception: pass
        self.result_display.appendPlainText(f"エラーが発生しました:\n\n{error_message}")
        self.status_label.setText("エラー発生")
        try:
            with open(DEFAULT_OUTPUT_FILE, 'w', encoding='utf-8') as f: f.write(f"--- Execution Failed ---\n{error_message}")
            print(f"Error details written to {DEFAULT_OUTPUT_FILE}")
        except Exception as write_e: print(f"Error writing error details to file: {write_e}"); logging.error(f"Error writing error details: {write_e}", exc_info=True)

    @Slot(str)
    def update_status(self, status: str): self.status_label.setText(status)

    @Slot()
    def task_finished(self):
        """タスク完了時のUI状態更新"""
        print("DEBUG: task_finished slot called.")
        self.set_buttons_enabled(True) # ボタン有効化
        if not self.status_label.text().startswith("エラー"): self.status_label.setText("アイドル")
        self.mcp_worker = None # ワーカー参照クリア

    def set_buttons_enabled(self, enabled: bool):
        """主要な操作ボタンの有効/無効を切り替える"""
        self.run_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.generator_button.setEnabled(enabled)
        self.headless_checkbox.setEnabled(enabled)
        self.slowmo_spinbox.setEnabled(enabled)
        self.copy_button.setEnabled(enabled)
        self.json_selector.setEnabled(enabled) # ドロップダウンも制御

    # --- open_generator, paste_generated_json, copy_output_to_clipboard, show_error_message, closeEvent (変更なし) ---
    def open_generator(self):
         if not GENERATOR_HTML.exists(): self.show_error_message(f"エラー: {GENERATOR_HTML.resolve()} なし"); return
         if self.generator_dialog is None or not self.generator_dialog.isVisible():
             self.generator_dialog = GeneratorDialog(GENERATOR_HTML, self)
             self.generator_dialog.json_generated.connect(self.paste_generated_json); self.generator_dialog.show()
         else: self.generator_dialog.raise_(); self.generator_dialog.activateWindow()
    @Slot(str)
    def paste_generated_json(self, json_string: str):
        self.result_display.setPlaceholderText("ジェネレーターからJSON入力"); self.result_display.setPlainText(json_string)
        try: json.loads(json_string); self.status_label.setText("ジェネレーターからJSON取得")
        except json.JSONDecodeError: self.show_error_message("無効なJSON"); self.status_label.setText("警告: 無効なJSON")
    @Slot()
    def copy_output_to_clipboard(self):
        output_filepath = DEFAULT_OUTPUT_FILE; output_filepath.parent.mkdir(parents=True, exist_ok=True)
        if not output_filepath.exists(): self.show_error_message(f"ファイルなし\n{output_filepath}"); return
        try:
            with open(output_filepath, 'r', encoding='utf-8') as f: content = f.read()
            clipboard = QApplication.instance().clipboard()
            if clipboard: clipboard.setText(content); self.status_label.setText(f"'{output_filepath.name}' をコピー")
            else: self.show_error_message("クリップボードアクセス不可")
        except Exception as e: self.show_error_message(f"コピーエラー\n{e}"); logging.error("コピーエラー", exc_info=True)
    def show_error_message(self, message: str): QMessageBox.critical(self, "エラー", message)
    def closeEvent(self, event):
        print("Close event triggered.")
        worker = getattr(self, 'mcp_worker', None) # McpWorker をチェック
        if worker and worker.isRunning(): print("Stopping MCP worker..."); worker.stop_worker(); worker.wait(1000)
        if self.generator_dialog and self.generator_dialog.isVisible(): self.generator_dialog.close()
        print("Exiting client application."); event.accept()


# --- アプリケーション実行 (変更なし) ---
if __name__ == "__main__":
    log_file = "gui_client.log"; log_dir = Path("./logs"); log_dir.mkdir(exist_ok=True); log_path = log_dir / log_file
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] %(name)s: %(message)s', handlers=[logging.FileHandler(log_path, encoding='utf-8')])
    console_handler = logging.StreamHandler(sys.stdout); console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] %(name)s: %(message)s'); console_handler.setFormatter(formatter)
    logging.getLogger().addHandler(console_handler)
    try: import PySide6; import anyio
    except ImportError as e: print(f"エラー: 必要ライブラリ不足: {e.name}\npip install PySide6 anyio"); sys.exit(1)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())