# --- ファイル: web_runner_mcp_client_GUI.py (ステップ5実装・完全版) ---

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
    QComboBox, QPushButton, QPlainTextEdit, QLabel, QDialog, QMessageBox,
    QCheckBox, QSpinBox, QLineEdit,
    QSizePolicy
)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QUrl, QObject
from PySide6.QtGui import QClipboard
# QWebEngineView と QWebChannel は GeneratorDialog でのみ使用
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

# --- コア機能 ---
try:
    from web_runner_mcp_client_core import execute_web_runner_via_mcp
except ImportError:
    print("Error: web_runner_mcp_client_core.py not found or cannot be imported.")
    print("Please ensure web_runner_mcp_client_core.py is in the same directory or in PYTHONPATH.")
    sys.exit(1)

# --- 設定・ユーティリティ ---
try:
    import config
    import utils
    DEFAULT_OUTPUT_FILE = Path(config.MCP_CLIENT_OUTPUT_FILE)
except ImportError:
    print("Warning: config.py or utils.py not found. Using default output filename './output/web_runner_mcp.txt'")
    DEFAULT_OUTPUT_FILE = Path("./output/web_runner_mcp.txt")
    utils = None # utilsがなければファイル書き込み機能の一部が制限される

# --- Playwrightランチャー (HTML取得用) ---
try:
    import playwright_launcher
except ImportError:
    logging.critical("playwright_launcher.pyが見つかりません。HTML取得機能は利用できません。")
    playwright_launcher = None # なければHTML取得はできない

# --- HTMLプロセッサー ---
try:
    from html_processor import cleanup_html, DO_CLEANUP
    from bs4 import BeautifulSoup # HTMLパースに必要
except ImportError:
    logging.error("html_processor.py または beautifulsoup4 が見つかりません。HTMLクリーンアップは無効になります。")
    def cleanup_html(html_content: str) -> str: return html_content # ダミー関数
    DO_CLEANUP = False

# --- テンプレートプロセッサー ---
try:
    from template_processor import fill_template
except ImportError:
    logging.error("template_processor.py が見つかりません。テンプレート機能は利用できません。")
    # ダミー関数を定義しておく (ImportError回避のため)
    def fill_template(template_path: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        raise ImportError("template_processor.fill_template is not available.")

# --- 定数 ---
JSON_FOLDER = Path("./json") # テンプレートJSONフォルダ
GENERATOR_HTML = Path("./json_generator.html") # 従来版ジェネレータ

# --- McpWorker クラス ---
class McpWorker(QThread):
    """MCP通信をバックグラウンドで実行するワーカースレッド"""
    result_ready = Signal(str)      # 成功時にJSON文字列を発行
    error_occurred = Signal(object) # 失敗時にエラー情報(dict or str)を発行
    status_update = Signal(str)     # 実行中のステータス更新用

    def __init__(self, json_input: Dict[str, Any], headless: bool, slow_mo: int):
        super().__init__()
        self.json_input = json_input
        self.headless = headless
        self.slow_mo = slow_mo
        self._is_running = True # スレッドが動作中かを示すフラグ

    def run(self):
        """スレッドのメイン処理"""
        print("DEBUG: McpWorker.run started")
        self.status_update.emit("MCPタスク実行中...")
        success = False
        result_or_error: Union[str, Dict[str, Any]] = {"error": "Worker execution failed unexpectedly."}
        try:
            # anyioが必要 (なければImportError)
            import anyio
            # web_runner_mcp_client_core の関数を呼び出す
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
                    # 失敗時の辞書型エラー
                    self.error_occurred.emit(result_or_error)
                elif not success and isinstance(result_or_error, str):
                    # 失敗時の文字列エラー (コア関数からの直接エラーなど)
                     self.error_occurred.emit({"error": "Received string error from core", "raw_details": result_or_error})
                else:
                     # 予期しない形式
                     self.error_occurred.emit({"error": "Unexpected result format from core function", "result": str(result_or_error)})

        except ImportError as e:
             # anyio がない場合など
             err_msg = f"MCPワーカー実行エラー: 必要なライブラリ '{e.name}' がありません。\npip install {e.name} でインストールしてください。"
             print(f"ERROR in McpWorker.run: {err_msg}")
             if self._is_running: self.error_occurred.emit({"error": "Missing Dependency", "details": err_msg})
        except Exception as e:
            # その他の予期せぬ実行時エラー
            err_msg = f"MCPワーカー実行エラー: {type(e).__name__}: {e}\n{traceback.format_exc()}"
            print(f"ERROR in McpWorker.run: {err_msg}")
            if self._is_running: self.error_occurred.emit({"error": "Exception in McpWorker", "details": err_msg})
        finally:
            self._is_running = False # 処理が完了または中断したらフラグを下げる
            print("DEBUG: McpWorker.run finished")

    def stop_worker(self):
        """ワーカーの停止を試みる（シグナル発行抑制のため）"""
        print("DEBUG: Requesting McpWorker to stop (flag set).")
        self._is_running = False

# --- GeneratorDialog クラス ---
class GeneratorDialog(QDialog):
    """従来のJSONジェネレーターを表示するダイアログ"""
    json_generated = Signal(str)

    class Bridge(QObject):
        """JavaScript と通信するためのブリッジオブジェクト"""
        receiveJsonSignal = Signal(str)
        @Slot(str)
        def receiveJsonFromHtml(self, jsonString):
            """JavaScriptからJSON文字列を受け取るスロット"""
            self.receiveJsonSignal.emit(jsonString)

    def __init__(self, html_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("JSON Generator (従来)")
        self.setGeometry(200, 200, 900, 700)
        layout = QVBoxLayout(self)
        self.webview = QWebEngineView() # Webコンテンツ表示用ウィジェット
        layout.addWidget(self.webview)

        # WebChannel (PythonとJSの通信) の設定
        self.bridge = self.Bridge(self)
        self.channel = QWebChannel(self.webview.page())
        self.webview.page().setWebChannel(self.channel)
        # JavaScript側から 'pyBridge' という名前でアクセスできるように登録
        self.channel.registerObject("pyBridge", self.bridge)

        if html_path.exists():
            file_url = QUrl.fromLocalFile(str(html_path.resolve()))
            # HTML読み込み完了後に実行するJavaScript
            script = """
                 <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
                 <script>
                     document.addEventListener('DOMContentLoaded', function() {
                         if (typeof QWebChannel === 'undefined') { console.error('qwebchannel.js did not load'); return; }
                         new QWebChannel(qt.webChannelTransport, function(channel) {
                             window.pyBridge = channel.objects.pyBridge; // PythonオブジェクトをJSに公開
                             console.log('Python Bridge (pyBridge) initialized.');
                             // 元の generateJsonData 関数を保持
                             const originalGenerateJsonData = window.generateJsonData;
                             // generateJsonData 関数をオーバーライドしてPythonに通知する機能を追加
                             window.generateJsonData = function() {
                                 originalGenerateJsonData(); // 元のJSON生成処理を実行
                                 // 少し待ってからJSONテキストを取得してPythonに送信
                                 setTimeout(() => {
                                     const jsonElement = document.getElementById('generated-json');
                                     const jsonString = jsonElement ? jsonElement.textContent : null;
                                     // 有効なJSON文字列か簡易チェック
                                     if (jsonString && !jsonString.startsWith('JSON') && !jsonString.startsWith('入力エラー')) {
                                         if (window.pyBridge && window.pyBridge.receiveJsonFromHtml) {
                                             // Python側の receiveJsonFromHtml を呼び出す
                                             window.pyBridge.receiveJsonFromHtml(jsonString);
                                         } else { console.error('Python bridge or method not available.'); }
                                     } else { console.log('No valid JSON to send to Python.'); }
                                 }, 100); // 100ms待機 (DOM更新を待つため)
                             };
                         });
                     });
                 </script>
             """
            # Webページの読み込み完了シグナルに接続し、スクリプトを実行
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
        self.accept() # ダイアログをOK状態で閉じる

    def closeEvent(self, event):
        """ダイアログが閉じられるときの処理"""
        page = self.webview.page()
        if page:
            # WebChannelの登録解除など、リソースをクリーンアップ
            if hasattr(self, 'channel') and self.channel:
                 self.channel.deregisterObject("pyBridge")
            self.webview.setPage(None) # ページをクリアしてメモリ解放を助ける
        super().closeEvent(event)

# --- MainWindow クラス ---
class MainWindow(QMainWindow):
    """メインウィンドウクラス"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web-Runner MCP Client (Template & Params)")
        self.setGeometry(100, 100, 850, 650) # ウィンドウサイズ

        self.mcp_worker: Optional[McpWorker] = None # MCP実行用ワーカー
        self.generator_dialog: Optional[GeneratorDialog] = None # 従来版ジェネレータ用ダイアログ
        self.current_placeholders: List[str] = [] # 選択中テンプレートのプレースホルダーリスト

        # --- UI要素のセットアップ ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget) # 全体の垂直レイアウト

        # 1段目: テンプレート選択、更新、従来ジェネレータ、実行ボタン
        top_layout = QHBoxLayout()
        self.json_selector = QComboBox()
        self.refresh_button = QPushButton("🔄 更新")
        self.generator_button = QPushButton("JSONジェネレーター(従来)")
        self.run_button = QPushButton("実行 ▶")
        self.run_button.setStyleSheet("background-color: #28a745; color: white;") # 緑色のボタン
        top_layout.addWidget(QLabel("テンプレートJSON:"))
        top_layout.addWidget(self.json_selector, 1) # 横に伸びるように
        top_layout.addWidget(self.refresh_button)
        top_layout.addWidget(self.generator_button)
        top_layout.addWidget(self.run_button)
        main_layout.addLayout(top_layout)

        # 2段目: URLメモ表示ラベル
        self.url_memo_label = QLabel("")
        self.url_memo_label.setStyleSheet("font-style: italic; color: grey;") # 見た目調整
        self.url_memo_label.setWordWrap(True) # 折り返し有効
        main_layout.addWidget(self.url_memo_label)

        # 3段目: パラメータ入力エリア (動的に中身が変わる)
        self.params_widget = QWidget()
        self.params_layout = QVBoxLayout(self.params_widget)
        self.params_layout.setContentsMargins(0, 5, 0, 5) # 上下マージン
        main_layout.addWidget(self.params_widget)

        # 4段目: 実行オプション (ヘッドレス、SlowMo、コピーボタン)
        options_layout = QHBoxLayout()
        self.headless_checkbox = QCheckBox("ヘッドレス実行")
        self.headless_checkbox.setChecked(False)
        self.headless_checkbox.setToolTip("ブラウザ画面を表示せずに実行します。")
        options_layout.addWidget(self.headless_checkbox)

        options_layout.addWidget(QLabel("SlowMo (ms):"))
        self.slowmo_spinbox = QSpinBox()
        self.slowmo_spinbox.setRange(0, 30000) # 0から30秒
        self.slowmo_spinbox.setValue(0)
        self.slowmo_spinbox.setSingleStep(100) # 100ms単位で変更
        self.slowmo_spinbox.setToolTip("各操作間の遅延時間(ミリ秒)。")
        options_layout.addWidget(self.slowmo_spinbox)

        copy_button_text = f"コピー ({os.path.join('output', DEFAULT_OUTPUT_FILE.name)})"
        self.copy_button = QPushButton(copy_button_text)
        self.copy_button.setToolTip(f"結果ファイル ({DEFAULT_OUTPUT_FILE}) の内容をコピーします。")
        options_layout.addWidget(self.copy_button)

        options_layout.addStretch() # 右側にスペースを空ける
        main_layout.addLayout(options_layout)

        # 5段目: 結果表示エリア
        self.result_display = QPlainTextEdit()
        self.result_display.setReadOnly(True)
        self.result_display.setPlaceholderText("テンプレートを選択し、必要ならパラメータを入力して「実行」を押してください。")
        main_layout.addWidget(self.result_display, 1) # 縦方向にスペースを占めるように

        # 6段目: ステータス表示ラベル
        self.status_label = QLabel("アイドル")
        main_layout.addWidget(self.status_label)

        # --- シグナル/スロット接続 ---
        self.refresh_button.clicked.connect(self.populate_json_files)
        self.generator_button.clicked.connect(self.open_generator)
        self.run_button.clicked.connect(self.run_mcp_from_template) # 実行ボタンのハンドラ
        self.copy_button.clicked.connect(self.copy_output_to_clipboard)
        self.json_selector.currentIndexChanged.connect(self.on_template_selected) # ドロップダウン変更時のハンドラ

        # --- 初期化処理 ---
        JSON_FOLDER.mkdir(exist_ok=True) # jsonフォルダがなければ作成
        DEFAULT_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True) # outputフォルダがなければ作成
        self.populate_json_files() # ドロップダウン初期化

    def populate_json_files(self):
        """jsonフォルダ内のJSONファイルを読み込みドロップダウンに設定する"""
        current_selection = self.json_selector.currentText() # 更新前の選択状態を保持
        self.json_selector.blockSignals(True) # 更新中のシグナル発生を抑制
        self.json_selector.clear() # ドロップダウンの内容をクリア
        try:
            json_files = sorted([f.name for f in JSON_FOLDER.glob("*.json") if f.is_file()])
            if json_files:
                self.json_selector.addItems(json_files) # ファイル名リストを設定
                # 前回の選択を復元（ファイルが残っていれば）
                index = self.json_selector.findText(current_selection)
                if index != -1:
                    self.json_selector.setCurrentIndex(index)
                elif self.json_selector.count() > 0:
                    self.json_selector.setCurrentIndex(0) # なければ先頭を選択

                self.status_label.setText(f"{len(json_files)}個のテンプレートを検出")
                self.run_button.setEnabled(True) # 実行ボタン有効化
            else:
                # JSONファイルが見つからない場合
                self.json_selector.addItem("テンプレートJSONが見つかりません")
                self.status_label.setText(f"'{JSON_FOLDER}'フォルダにJSONファイルがありません")
                self.run_button.setEnabled(False) # 実行ボタン無効化
                self._clear_parameter_inputs() # パラメータ欄クリア
                self.url_memo_label.setText("") # URLメモクリア
                self.current_placeholders = [] # プレースホルダーリストクリア
        except Exception as e:
            self.show_error_message(f"テンプレートJSONフォルダの読み込みエラー:\n{e}")
            self.run_button.setEnabled(False)
        finally:
            self.json_selector.blockSignals(False) # シグナル抑制を解除
            # 選択状態が変わった可能性があるので、パラメータ表示更新処理を強制的に呼ぶ
            self.on_template_selected()

    @Slot()
    def on_template_selected(self):
        """ドロップダウンでテンプレートが選択/変更されたときに呼ばれる"""
        selected_file = self.json_selector.currentText()
        self._clear_parameter_inputs() # まず既存のパラメータ入力欄を消す
        self.current_placeholders = [] # 保持しているプレースホルダー名もリセット
        self.url_memo_label.setText("") # URLメモもリセット

        if not selected_file or selected_file == "テンプレートJSONが見つかりません":
            return # 有効なファイルが選択されていなければ何もしない

        json_path = JSON_FOLDER / selected_file
        if not json_path.exists():
            # 基本的には populate_json_files で除外されるはず
            self.status_label.setText("エラー: 選択されたファイルが見つかりません (再読込してください)")
            return

        try:
            # 選択されたJSONファイルの内容を読み込む
            with open(json_path, 'r', encoding='utf-8') as f:
                file_content = f.read()

            # JSONをパースして URLメモ (target_url_memo) を取得・表示
            try:
                json_data = json.loads(file_content)
                url_memo = json_data.get("target_url_memo")
                target_url = json_data.get("target_url", "URL不明")
                self.url_memo_label.setText(f"メモ: {url_memo}" if url_memo else f"URL: {target_url}")
            except json.JSONDecodeError:
                # JSONとして不正な場合は警告表示
                self.url_memo_label.setText("警告: テンプレートJSONの形式が不正です")

            # 正規表現でファイル内容から {{プレースホルダー名}} を全て見つける
            placeholders = sorted(list(set(re.findall(r"\{\{([^}]+)\}\}", file_content))))
            self.current_placeholders = placeholders # 見つかった名前をクラス変数に保持

            if not placeholders:
                self.params_widget.setVisible(False) # プレースホルダーがなければ入力欄エリア自体を隠す
                return # パラメータがないのでここで終了

            # 見つかったプレースホルダーごとにラベルと入力欄を動的に生成
            for placeholder_name in placeholders:
                param_layout = QHBoxLayout() # ラベルと入力欄を横に並べるレイアウト
                label = QLabel(f"{placeholder_name}:") # ラベル作成
                label.setMinimumWidth(150) # ラベルの最小幅を設定して整列しやすくする
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter) # 右寄せ・垂直中央揃え

                line_edit = QLineEdit() # テキスト入力欄作成
                # 後で入力値を取得するためにオブジェクト名を設定
                line_edit.setObjectName(f"param_input_{placeholder_name}")
                # 初期値としてプレースホルダー自身を表示（ユーザーへのヒント）
                #initial_value = f"{{{{{placeholder_name}}}}}"
                initial_value = "" # 空文字列を初期値にする
                #line_edit.setPlaceholderText(...)
                line_edit.setText(initial_value)
                line_edit.setToolTip(f"'{placeholder_name}' に対応する値を入力してください")

                param_layout.addWidget(label)
                param_layout.addWidget(line_edit, 1) # 入力欄が横方向に伸びるように
                self.params_layout.addLayout(param_layout) # パラメータエリアのレイアウトに追加

            self.params_widget.setVisible(True) # パラメータ入力欄エリアを表示
            self.status_label.setText(f"テンプレート '{selected_file}' 読み込み完了 ({len(placeholders)}個のパラメータ検出)")

        except FileNotFoundError:
             # 通常は起こらないはずだが念のため
             self.status_label.setText(f"エラー: ファイルが見つかりません: {json_path}")
             self._clear_parameter_inputs()
        except Exception as e:
            # その他のエラー（読み取り権限など）
            self.show_error_message(f"テンプレートファイル '{selected_file}' の処理中にエラー:\n{e}")
            self.status_label.setText("エラー: テンプレート処理失敗")
            self._clear_parameter_inputs()

    def _clear_parameter_inputs(self):
        """パラメータ入力欄ウィジェットを全て削除するヘルパー関数"""
        # params_layout に含まれるアイテム（レイアウトやウィジェット）を逆順に削除していく
        # 正順で削除するとインデックスがずれるため
        while self.params_layout.count() > 0:
            item = self.params_layout.takeAt(0) # 先頭からアイテムを取得してレイアウトから削除
            if item is None: continue

            widget = item.widget()
            layout = item.layout()

            if widget:
                widget.deleteLater() # ウィジェットを削除スケジュールに入れる
            elif layout:
                # レイアウト内のウィジェットも再帰的に削除
                while layout.count() > 0:
                    child_item = layout.takeAt(0)
                    if child_item and child_item.widget():
                        child_item.widget().deleteLater()
                layout.deleteLater() # レイアウト自体も削除スケジュールに入れる
        # エリア自体を非表示にする
        self.params_widget.setVisible(False)

    @Slot()
    def run_mcp_from_template(self):
        """テンプレートとGUIで入力されたパラメータを使ってMCP実行を開始する"""
        if self.mcp_worker and self.mcp_worker.isRunning():
            self.show_error_message("タスク実行中です。完了までお待ちください。"); return

        selected_file = self.json_selector.currentText()
        if not selected_file or selected_file == "テンプレートJSONが見つかりません":
            self.show_error_message("実行するテンプレートJSONを選択してください。"); return

        json_path = JSON_FOLDER / selected_file
        if not json_path.exists():
             self.show_error_message(f"エラー: 選択されたファイル '{selected_file}' が見つかりません。\n「更新」ボタンを押してリストを再読み込みしてください。")
             return

        # --- GUIからパラメータを取得 ---
        parameters: Dict[str, Any] = {}
        if self.current_placeholders: # プレースホルダーがある場合のみ
            all_params_filled = True
            for placeholder_name in self.current_placeholders:
                # objectName を使って対応する QLineEdit を見つける
                line_edit: Optional[QLineEdit] = self.params_widget.findChild(QLineEdit, f"param_input_{placeholder_name}")
                if line_edit:
                    value_str = line_edit.text().strip()
                    # 簡単なバリデーション: 空でないか、初期値のままではないか
                    if not value_str or value_str == f"{{{{{placeholder_name}}}}}":
                        all_params_filled = False
                        line_edit.setStyleSheet("border: 1px solid red;") # 未入力/初期値のままなら赤枠表示
                    else:
                        line_edit.setStyleSheet("") # 入力されていれば通常表示に戻す
                        parameters[placeholder_name] = value_str # 取得した値を辞書に格納
                else:
                    # 通常は起こらないはず
                    print(f"エラー: GUI要素が見つかりません (param_input_{placeholder_name})")
                    all_params_filled = False # 予期せぬエラー

            if not all_params_filled:
                self.show_error_message("赤枠で示されたパラメータを全て入力してください。"); return
        # else: プレースホルダーがなければパラメータ辞書は空のまま

        # --- テンプレート処理とMCP実行 ---
        try:
            # template_processor.fill_template を呼び出して実行用JSONを生成
            # fill_template が import できていない場合はここで ImportError が発生する
            filled_json_data = fill_template(str(json_path), parameters)
            print("DEBUG: Generated JSON for execution:", json.dumps(filled_json_data, indent=2, ensure_ascii=False)) # デバッグ出力

            # --- MCPワーカーの起動 ---
            self.status_label.setText(f"テンプレート '{selected_file}' で実行開始...")
            self.set_buttons_enabled(False) # 実行中はボタン類を無効化
            self.result_display.clear() # 前回の結果をクリア
            # 実行中の情報を表示
            params_display = json.dumps(parameters, indent=2, ensure_ascii=False) if parameters else "(パラメータなし)"
            self.result_display.setPlaceholderText(f"実行中...\nTemplate: {selected_file}\nParameters:\n{params_display}")

            # 実行オプションを取得
            headless_mode = self.headless_checkbox.isChecked()
            slow_mo_value = self.slowmo_spinbox.value()

            # McpWorker スレッドを開始
            self.mcp_worker = McpWorker(filled_json_data, headless_mode, slow_mo_value)
            # シグナルとスロットを接続
            self.mcp_worker.result_ready.connect(self.display_result)
            self.mcp_worker.error_occurred.connect(self.display_error)
            self.mcp_worker.status_update.connect(self.update_status)
            self.mcp_worker.finished.connect(self.task_finished) # 完了時の処理
            self.mcp_worker.start() # スレッド開始

        except ImportError:
             # fill_template が import できなかった場合
             self.show_error_message("実行エラー: パラメータ処理モジュール (template_processor.py) が見つかりません。")
             self.status_label.setText("エラー: モジュール不足"); self.set_buttons_enabled(True)
        except FileNotFoundError:
             # fill_template内でファイルが見つからなかった場合
             self.show_error_message(f"実行エラー: テンプレートファイルが見つかりません。\nPath: {json_path}")
             self.status_label.setText("エラー: ファイルなし"); self.set_buttons_enabled(True)
        except json.JSONDecodeError as e:
             # fill_template内でJSONパースに失敗した場合
             self.show_error_message(f"実行エラー: テンプレートJSONの形式が不正か、パラメータ置換に失敗しました。\nファイルを確認してください。\nError: {e}")
             self.status_label.setText("エラー: JSONパース失敗"); self.set_buttons_enabled(True)
        except Exception as e:
            # その他の予期せぬエラー
            self.show_error_message(f"パラメータ置換または実行準備中に予期せぬエラー:\n{e}")
            self.status_label.setText("エラー: 準備失敗"); self.set_buttons_enabled(True)
            traceback.print_exc() # 詳細をコンソールに出力

    @Slot(str)
    def display_result(self, result_json_string: str):
        """成功結果を整形して表示し、ファイルにも書き込む"""
        self.result_display.setPlainText("--- Web Runner Execution Result ---\n\nOverall Status: Success\n\n")
        try:
            # 結果文字列をJSONオブジェクト(リスト)にパース
            result_data_list = json.loads(result_json_string)
            # 見やすいように整形して表示
            formatted_json = json.dumps(result_data_list, indent=2, ensure_ascii=False)
            self.result_display.appendPlainText(formatted_json)
            self.status_label.setText("実行成功")

            # utils があればファイルにも書き込む
            if utils and isinstance(result_data_list, list):
                try:
                    # utils.write_results_to_file を使用
                    utils.write_results_to_file(result_data_list, str(DEFAULT_OUTPUT_FILE))
                    print(f"Result also written to {DEFAULT_OUTPUT_FILE}")
                except Exception as write_e:
                     # ファイル書き込みエラーはログに記録
                     logging.error(f"結果のファイル書き込みエラー: {write_e}", exc_info=True)
        except (json.JSONDecodeError, TypeError) as e:
             # JSONパース失敗などのエラー
             self.result_display.appendPlainText(f"\n--- Error Parsing Server Response ---\n{result_json_string}")
             self.status_label.setText("警告: サーバー応答の形式が不正です")
             logging.error(f"結果JSONのパース/処理エラー: {e}", exc_info=True)

    @Slot(object)
    def display_error(self, error_info: Union[str, Dict[str, Any]]):
        """エラー結果を整形して表示し、ファイルにも書き込む"""
        self.result_display.setPlainText("--- Web Runner Execution Result (Error) ---\n\n")
        error_message = str(error_info) # デフォルトはそのまま文字列化
        if isinstance(error_info, dict):
            try:
                # 辞書の場合はJSON形式で見やすくする
                error_message = json.dumps(error_info, indent=2, ensure_ascii=False)
            except Exception:
                # JSON変換に失敗した場合はそのまま文字列化 (既に上で代入済み)
                pass
        # elif isinstance(error_info, str): # 文字列の場合はそのまま error_message を使う

        self.result_display.appendPlainText(f"エラーが発生しました:\n\n{error_message}")
        self.status_label.setText("エラー発生")
        # エラー時もファイルに書き込み試行
        try:
            with open(DEFAULT_OUTPUT_FILE, 'w', encoding='utf-8') as f:
                 f.write(f"--- Execution Failed ---\n{error_message}")
            print(f"Error details written to {DEFAULT_OUTPUT_FILE}")
        except Exception as write_e:
            print(f"Error writing error details to file: {write_e}")
            logging.error(f"エラー詳細のファイル書き込みエラー: {write_e}", exc_info=True)

    @Slot(str)
    def update_status(self, status: str):
        """ステータスラベルのテキストを更新する"""
        self.status_label.setText(status)

    @Slot()
    def task_finished(self):
        """MCPワーカー（または他の非同期タスク）完了時のUI状態更新"""
        print("DEBUG: task_finished slot called.")
        self.set_buttons_enabled(True) # ボタン類を再度有効化
        # エラーで終了した場合を除き、ステータスを「アイドル」に戻す
        if not self.status_label.text().startswith("エラー"):
            self.status_label.setText("アイドル")
        self.mcp_worker = None # 完了したらワーカーへの参照をクリア

    def set_buttons_enabled(self, enabled: bool):
        """主要な操作ボタンとオプションの有効/無効をまとめて切り替える"""
        self.run_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.generator_button.setEnabled(enabled)
        self.headless_checkbox.setEnabled(enabled)
        self.slowmo_spinbox.setEnabled(enabled)
        self.copy_button.setEnabled(enabled)
        self.json_selector.setEnabled(enabled) # ドロップダウンも制御

    def open_generator(self):
        """従来版JSONジェネレーターダイアログを開く"""
        if not GENERATOR_HTML.exists():
             self.show_error_message(f"エラー: ジェネレーターHTMLが見つかりません。\nPath: {GENERATOR_HTML.resolve()}"); return
        # ダイアログが未作成か非表示の場合のみ新規作成して表示
        if self.generator_dialog is None or not self.generator_dialog.isVisible():
            self.generator_dialog = GeneratorDialog(GENERATOR_HTML, self)
            self.generator_dialog.json_generated.connect(self.paste_generated_json) # シグナル接続
            self.generator_dialog.show()
        else:
            # 既に表示されている場合は手前に持ってくる
            self.generator_dialog.raise_()
            self.generator_dialog.activateWindow()

    @Slot(str)
    def paste_generated_json(self, json_string: str):
        """従来版ジェネレーターから受け取ったJSONを結果表示エリアに表示する"""
        self.result_display.setPlaceholderText("JSONジェネレーターからJSONが入力されました。")
        self.result_display.setPlainText(json_string)
        try:
            # JSONとしてパースできるか軽くチェック
            json.loads(json_string)
            self.status_label.setText("ジェネレーターからJSONを取得しました")
        except json.JSONDecodeError:
            self.show_error_message("ジェネレーターから受け取った内容がJSON形式ではありません。")
            self.status_label.setText("警告: 無効なJSON形式")

    @Slot()
    def copy_output_to_clipboard(self):
        """結果ファイルの内容をクリップボードにコピーする"""
        output_filepath = DEFAULT_OUTPUT_FILE
        try:
            # outputフォルダがなければ作成
            output_filepath.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"Warning: Could not create output directory {output_filepath.parent}: {e}")

        if not output_filepath.exists():
            self.show_error_message(f"エラー: 結果ファイルが見つかりません。\n先に処理を実行してください。\nPath: {output_filepath}")
            self.status_label.setText("コピー失敗: ファイルなし")
            return

        try:
            # ファイルを読み込む
            with open(output_filepath, 'r', encoding='utf-8') as f:
                file_content = f.read()

            # クリップボードを取得してテキストを設定
            clipboard = QApplication.instance().clipboard()
            if clipboard:
                clipboard.setText(file_content)
                self.status_label.setText(f"'{output_filepath.name}' の内容をコピーしました。")
                # (オプション) 成功メッセージを表示する場合
                # QMessageBox.information(self, "コピー完了", f"'{output_filepath.name}' の内容をクリップボードにコピーしました。")
            else:
                self.show_error_message("エラー: クリップボードにアクセスできません。")
                self.status_label.setText("コピー失敗: クリップボードエラー")

        except IOError as e:
            self.show_error_message(f"エラー: 結果ファイルの読み込みに失敗しました。\nError: {e}")
            self.status_label.setText("コピー失敗: 読み込みエラー")
        except Exception as e:
            self.show_error_message(f"予期せぬコピーエラーが発生しました。\nError: {e}")
            self.status_label.setText("コピー失敗: 不明なエラー")
            logging.error("クリップボードへのコピー中にエラー", exc_info=True)

    def show_error_message(self, message: str):
        """エラーメッセージダイアログを表示する"""
        QMessageBox.critical(self, "エラー", message)

    def closeEvent(self, event):
        """ウィンドウが閉じられるときの処理"""
        print("Close event triggered.")
        # 実行中のワーカーがあれば停止を試みる
        worker = getattr(self, 'mcp_worker', None)
        if worker and worker.isRunning():
            print("Stopping MCP worker...")
            worker.stop_worker() # 停止フラグを立てる
            if not worker.wait(1000): # 1秒待機して終了しなければ警告
                 print("Warning: Worker thread did not stop gracefully.")
        # ジェネレーターダイアログが開いていれば閉じる
        if self.generator_dialog and self.generator_dialog.isVisible():
            self.generator_dialog.close()
        print("Exiting client application.")
        event.accept() # ウィンドウを閉じる

# --- アプリケーション実行 ---
if __name__ == "__main__":
    # --- ロギング設定 ---
    log_file = "gui_client.log"
    log_dir = Path("./logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / log_file
    # ファイルハンドラ (INFOレベル以上をファイルに記録)
    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter('%(asctime)s - [%(levelname)s] [%(name)s:%(lineno)d] %(message)s')
    file_handler.setFormatter(file_formatter)
    # コンソールハンドラ (INFOレベル以上をコンソールに表示)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s - [%(levelname)s] %(name)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    # ルートロガー設定 (既存ハンドラを削除してから追加)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]: root_logger.removeHandler(handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.INFO) # ここで全体の閾値を設定

    # --- 必須ライブラリチェック ---
    try:
        import PySide6
        import anyio
        # オプションだがテンプレート機能に必要なライブラリ
        # import template_processor # ここでチェックするとエラーで落ちる可能性があるため起動後に行う
    except ImportError as e:
        # GUI表示前にエラーを出力
        print(f"エラー: 必須ライブラリ '{e.name}' が見つかりません。")
        print(f"実行に必要なライブラリをインストールしてください: pip install PySide6 anyio")
        # GUIライブラリがない場合はGUI表示できないので終了
        if e.name == 'PySide6':
             msg = QMessageBox()
             msg.setIcon(QMessageBox.Critical)
             msg.setText(f"エラー: GUIライブラリ 'PySide6' が見つかりません。\n\npip install PySide6\n\nでインストールしてください。")
             msg.setWindowTitle("起動エラー")
             msg.exec() # ここで終了
        sys.exit(1)

    # --- アプリケーション起動 ---
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())