# --- ファイル: html_processor.py ---
"""
HTMLコンテンツのクリーンアップ処理を行うユーティリティ関数を提供します。
"""
import logging
import traceback
import re
import time
from typing import List, Set
from bs4 import BeautifulSoup, Comment # BeautifulSoupが必要

logger = logging.getLogger(__name__)

# --- HTMLクリーンアップ設定 ---
DO_CLEANUP = True # ★ クリーンアップを実行するかどうかのフラグ
TAGS_TO_REMOVE: List[str] = ['script', 'style', 'link', 'meta', 'header', 'footer', 'nav', 'aside', 'iframe', 'noscript', 'svg']
ATTRIBUTES_TO_REMOVE: List[str] = [
    'jsaction', 'jscontroller', 'jsname', 'jsmodel', 'jsshadow', 'jsslot', 'data-ved', 'data-jsarwt',
    'data-usm', 'data-lm', 'ping', 'nonce', 'crossorigin', 'integrity', 'referrerpolicy', 'decoding',
    'loading', 'aria-hidden', 'style', # styleはTAGS_KEEP_STYLE_ATTR以外で削除
    r'^on.*$' # onから始まるイベントハンドラ属性 (正規表現)
]
SECTIONS_TO_REMOVE_SELECTORS: List[str] = [
    # 一般的な不要セクション
    'header', 'footer', 'nav', 'aside', '.sidebar', '#sidebar', '.advertisement', '[role="banner"]',
    '[role="contentinfo"]', '[role="navigation"]', '[role="complementary"]',
    # Google特有の可能性のある不要セクション
    '#top_nav', '.fbar', 'g-scrolling-carousel',
    # Note (chatgpt-lab) 特有の可能性のある不要セクション
    '.o-navbar', '.o-noteFooter', '.o-noteFollowingNotes', '.o-noteCreator',
    '.o-noteBottomActions'
    # 必要に応じて他のサイト特有のセレクターを追加
]
TAGS_KEEP_STYLE_ATTR: Set[str] = {'a', 'button', 'input', 'textarea', 'select', 'option'}
REMOVE_COMMENTS = True
PRETTIFY_HTML = True # 整形するかどうか

# --- HTMLクリーンアップ関数 ---
def cleanup_html(html_content: str) -> str:
    """
    与えられたHTML文字列から不要なタグ、属性、コメントを削除し、整形します。
    """
    if not html_content:
        return ""
    logging.info("HTMLクリーンアップ処理開始...")
    start_time = time.time()
    try:
        # lxmlがなければhtml.parserを使う
        parser = 'lxml'
        try:
            import lxml
        except ImportError:
            parser = 'html.parser'
            logging.debug("lxmlが見つからないため、html.parserを使用します。")

        soup = BeautifulSoup(html_content, parser)

        # 1. コメント削除
        removed_comments_count = 0
        if REMOVE_COMMENTS:
            comments = soup.find_all(string=lambda text: isinstance(text, Comment))
            for comment in comments:
                comment.extract()
            removed_comments_count = len(comments)
            if removed_comments_count > 0: logging.info(f"  - {removed_comments_count} 個のコメントを削除しました。")

        # 2. セクション削除
        removed_section_count = 0
        for selector in SECTIONS_TO_REMOVE_SELECTORS:
            try:
                sections = soup.select(selector)
                count = len(sections)
                if count > 0:
                    for section in sections:
                        section.decompose()
                    logging.debug(f"  - セレクター '{selector}' に一致するセクション {count} 個を削除しました。")
                    removed_section_count += count
            except Exception as e_sel:
                logging.warning(f"  - セクション削除セレクター '{selector}' の処理中にエラー: {e_sel}")
        if removed_section_count > 0: logging.info(f"  - 合計 {removed_section_count} 個のセクションを削除しました。")

        # 3. タグ削除
        removed_tags_count = 0
        for tag_name in TAGS_TO_REMOVE:
            try:
                tags = soup.find_all(tag_name)
                count = len(tags)
                if count > 0:
                    for tag in tags:
                        tag.decompose()
                    logging.debug(f"  - タグ '{tag_name}' {count} 個を削除しました。")
                    removed_tags_count += count
            except Exception as e_tag:
                 logging.warning(f"  - タグ '{tag_name}' の削除中にエラー: {e_tag}")
        if removed_tags_count > 0: logging.info(f"  - 合計 {removed_tags_count} 個の指定タグを削除しました。")

        # 4. 属性削除
        removed_attrs_count = 0
        attr_patterns = []
        literal_attrs_to_remove = set()
        for item in ATTRIBUTES_TO_REMOVE:
            if isinstance(item, str) and any(c in item for c in '^$*+?.'):
                try: attr_patterns.append(re.compile(item))
                except re.error: logging.warning(f"  - 無効な属性削除正規表現をスキップ: {item}")
            elif isinstance(item, str): literal_attrs_to_remove.add(item)

        for tag in soup.find_all(True): # 全てのタグを対象
            if not hasattr(tag, 'attrs'): continue # 属性がないタグはスキップ
            attrs_to_delete = []
            for attr_name in list(tag.attrs.keys()): # イテレーション中に変更するためリスト化
                # style属性を保持するタグかチェック
                if attr_name == 'style' and tag.name in TAGS_KEEP_STYLE_ATTR:
                    continue # style属性を保持する場合は削除しない
                # 削除対象の属性名リストに含まれるかチェック
                if attr_name in literal_attrs_to_remove:
                    attrs_to_delete.append(attr_name)
                    continue
                # 削除対象の正規表現パターンにマッチするかチェック
                for pattern in attr_patterns:
                    if pattern.match(attr_name):
                        attrs_to_delete.append(attr_name)
                        break # マッチしたら次の属性へ
            # 削除対象の属性を実際に削除
            if attrs_to_delete:
                 for attr_name in attrs_to_delete:
                     del tag[attr_name]
                     removed_attrs_count += 1
                     logging.debug(f"    - 属性 '{attr_name}' をタグ <{tag.name}> から削除しました。")

        if removed_attrs_count > 0: logging.info(f"  - 合計 {removed_attrs_count} 個の不要な属性を削除しました。")

        # 5. 整形して返す
        cleaned_html = soup.prettify() if PRETTIFY_HTML else str(soup)
        end_time = time.time()
        logging.info(f"HTMLクリーンアップ完了 (処理時間: {end_time - start_time:.2f}秒).")
        return cleaned_html

    except ImportError:
        logging.error("HTMLクリーンアップに必要なライブラリ (BeautifulSoup4, lxml or html.parser) がインストールされていません。")
        return html_content # エラー時は元のHTMLを返す
    except Exception as e:
        logging.error(f"HTMLクリーンアップ中に予期せぬエラーが発生しました: {e}")
        logging.debug(traceback.format_exc())
        return html_content # エラー時は元のHTMLを返す

# --- 単体テスト用 ---
if __name__ == '__main__':
    import time
    logging.basicConfig(level=logging.DEBUG) # DEBUGレベルでテスト
    test_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Test Page</title>
        <script>console.log('hello');</script>
        <style>.red {color: red;}</style>
        <link rel="stylesheet" href="style.css">
    </head>
    <body>
        <!-- This is a comment -->
        <header role="banner"><h1>Header</h1></header>
        <nav><a href="#">Nav</a></nav>
        <main>
            <p style="font-weight: bold;" onclick="alert('hi')">This is <b>bold</b> paragraph.</p>
            <div class="advertisement" data-ved="123">Ad here</div>
            <button jsaction="click:handleClick">Click Me</button>
            <a href="/link" style="color:blue;">A link with style</a>
        </main>
        <aside class="sidebar">Sidebar</aside>
        <footer>Footer</footer>
        <script src="analytics.js"></script>
    </body>
    </html>
    """
    print("--- 元のHTML ---")
    print(test_html)
    print("\n--- クリーンアップ後のHTML ---")
    cleaned = cleanup_html(test_html)
    print(cleaned)