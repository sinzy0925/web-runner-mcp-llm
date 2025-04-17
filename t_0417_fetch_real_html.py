# fetch_real_html.py (SyntaxError修正 + 整形改善版)
from playwright_stealth import stealth_async
import asyncio
import os
import logging
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
from bs4 import BeautifulSoup, Comment, NavigableString
import traceback
import re
from urllib.parse import urlparse

# --- 設定 ---
TARGET_URLS = [
    "https://www.google.co.jp/",
    "https://www.google.com/search?q=Playwrightによる自動テスト",
    "https://chatgpt-lab.com/n/nc1a8b4bb4911"
]
OUTPUT_DIR = Path("./real_html_outputs")
DO_CLEANUP = True

# --- Playwright設定 ---
VIEWPORT_WIDTH = 1920; VIEWPORT_HEIGHT = 1080
PAGE_LOAD_TIMEOUT = 30000; WAIT_AFTER_LOAD = 3000
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

# --- HTMLクリーンアップ設定 ---
TAGS_TO_REMOVE = ['script', 'style', 'link', 'meta', 'header', 'footer', 'nav', 'aside', 'iframe', 'noscript', 'svg']
ATTRIBUTES_TO_REMOVE = [
    'jsaction', 'jscontroller', 'jsname', 'jsmodel', 'jsshadow', 'jsslot', 'data-ved', 'data-jsarwt',
    'data-usm', 'data-lm', 'ping', 'nonce', 'crossorigin', 'integrity', 'referrerpolicy', 'decoding',
    'loading', 'aria-hidden', 'style', r'^on.*$'
]
SECTIONS_TO_REMOVE_SELECTORS = [
    'header', 'footer', 'nav', 'aside', '.sidebar', '#sidebar', '.advertisement', '[role="banner"]',
    '[role="contentinfo"]', '[role="navigation"]', '[role="complementary"]', '#top_nav', '.fbar',
    'g-scrolling-carousel', '.o-navbar', '.o-noteFooter', '.o-noteFollowingNotes', '.o-noteCreator',
    '.o-noteBottomActions'
]
TAGS_KEEP_STYLE_ATTR = ['a', 'button', 'input', 'textarea', 'select', 'option']
REMOVE_COMMENTS = True
PRETTIFY_HTML = True

# --- ロギング ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- ファイル名生成 ---
def sanitize_filename(url: str) -> str:
    try: parsed = urlparse(url); path_part = parsed.path.strip('/').replace('/', '_')[:50] if parsed.path else ""; query_part = parsed.query.replace('&', '_').replace('=', '-')[:50] if parsed.query else ""; filename = f"{parsed.netloc}_{path_part}_{query_part}"; filename = re.sub(r'[^a-zA-Z0-9_-]', '_', filename).strip('_'); filename = filename[:150]; return f"{filename}.html" if filename else "default_page.html"
    except Exception: logging.warning(f"URLからのファイル名生成失敗: {url}", exc_info=True); return "default_page.html"

# --- HTMLクリーンアップ ---
def cleanup_html(html_content: str) -> str:
    logging.info("HTMLクリーンアップ処理開始...")
    try:
        soup = BeautifulSoup(html_content, 'lxml')
        # 1. コメント削除
        if REMOVE_COMMENTS:
            count = 0;
            for comment in soup.find_all(string=lambda text: isinstance(text, Comment)): comment.extract(); count += 1
            if count > 0: logging.info(f"{count} 個コメント削除.")
        # 2. セクション削除
        removed_section_count = 0
        for selector in SECTIONS_TO_REMOVE_SELECTORS:
            try:
                sections = soup.select(selector); count = len(sections)
                if count > 0:
                    for section in sections: section.decompose()
                    logging.debug(f"セレクター '{selector}' セクション {count} 個削除.")
                    removed_section_count += count
            except Exception as e_sel: logging.warning(f"セクション削除セレクター '{selector}' エラー: {e_sel}")
        if removed_section_count > 0: logging.info(f"合計 {removed_section_count} 個セクション削除.")
        # 3. タグ削除
        removed_tags_count = 0
        for tag_name in TAGS_TO_REMOVE:
            tags = soup.find_all(tag_name); count = len(tags)
            if count > 0:
                for tag in tags: tag.decompose()
                logging.debug(f"タグ '{tag_name}' {count} 個削除.")
                removed_tags_count += count
        if removed_tags_count > 0: logging.info(f"合計 {removed_tags_count} 個タグ削除.")
        # 4. 属性削除
        removed_attrs_count = 0; patterns = []; literal_attrs = set()
        for item in ATTRIBUTES_TO_REMOVE:
            if isinstance(item, str) and any(c in item for c in '^$*+?.'):
                try: patterns.append(re.compile(item))
                except re.error: logging.warning(f"無効正規表現スキップ: {item}")
            elif isinstance(item, str): literal_attrs.add(item)
        for tag in soup.find_all(True):
            attrs_to_delete = [];
            if not hasattr(tag, 'attrs'): continue
            for attr_name in list(tag.attrs.keys()):
                should_remove = False
                if attr_name == 'style' and tag.name in TAGS_KEEP_STYLE_ATTR: continue
                if attr_name in literal_attrs: should_remove = True
                else:
                    for pattern in patterns:
                        if pattern.match(attr_name): should_remove = True; break
                if should_remove: attrs_to_delete.append(attr_name)
            if attrs_to_delete:
                 for attr_name in attrs_to_delete: del tag[attr_name]; removed_attrs_count += 1
        if removed_attrs_count > 0: logging.info(f"合計 {removed_attrs_count} 個不要属性削除.")
        # 5. 整形
        cleaned_html = soup.prettify() if PRETTIFY_HTML else str(soup)
        logging.info("HTMLクリーンアップ完了.")
        return cleaned_html
    except ImportError: logging.error("BeautifulSoup4/lxml未インストール"); return html_content
    except Exception as e: logging.error(f"HTMLクリーンアップエラー: {e}"); logging.debug(traceback.format_exc()); return html_content

# --- HTML取得・保存 ---
async def fetch_and_save_html(url: str, output_dir: Path):
    logging.info("-" * 30); logging.info(f"処理開始: {url}")
    playwright = browser = None; html_content = None
    output_filename = sanitize_filename(url); output_path = output_dir / output_filename
    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': VIEWPORT_WIDTH, 'height': VIEWPORT_HEIGHT}, user_agent=USER_AGENT, locale='ja-JP')
        await stealth_async(context)
        page = await context.new_page()
        logging.info(f"ナビゲーション中...")
        await page.goto(url, wait_until="load", timeout=PAGE_LOAD_TIMEOUT)
        logging.info(f"ページ読込完了。追加待機 {WAIT_AFTER_LOAD}ms...")
        await asyncio.sleep(WAIT_AFTER_LOAD / 1000)
        html_content = await page.content(); logging.info(f"取得HTMLサイズ (クリーンアップ前): {len(html_content)} bytes")
        if DO_CLEANUP and html_content: html_to_save = cleanup_html(html_content)
        elif html_content:
             html_to_save = html_content
             if PRETTIFY_HTML:
                 # === ▼▼▼ 整形処理修正 ▼▼▼ ===
                 try:
                     soup = BeautifulSoup(html_to_save, 'lxml')
                     html_to_save = soup.prettify()
                     logging.debug("HTMLをlxmlで整形しました。")
                 except Exception as e_lxml:
                     logging.warning(f"lxmlでのHTML整形失敗: {e_lxml}。html.parser試行...")
                     try:
                         soup = BeautifulSoup(html_to_save, 'html.parser')
                         html_to_save = soup.prettify()
                         logging.debug("HTMLをhtml.parserで整形しました。")
                     except Exception as e_parser:
                         logging.warning(f"html.parserでのHTML整形も失敗: {e_parser}")
                 # === ▲▲▲ 整形処理修正 ▲▲▲ ===
        else: html_to_save = None
        if html_to_save:
            output_dir.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f: f.write(html_to_save)
            logging.info(f"HTML保存: '{output_path.resolve()}' ({len(html_to_save)} bytes)")
        else: logging.warning("保存するHTMLコンテンツなし。")
    except Exception as e: logging.error(f"エラー ({url}): {e}"); logging.debug(traceback.format_exc())
    finally:
        if browser: await browser.close()
        if playwright: await playwright.stop()
        logging.info(f"処理完了: {url}")

# --- main関数 ---
async def main():
    logging.info(f"HTML取得開始 (保存先: {OUTPUT_DIR})")
    logging.info(f"HTMLクリーンアップ: {'有効' if DO_CLEANUP else '無効'}")
    for url in TARGET_URLS: await fetch_and_save_html(url, OUTPUT_DIR)
    logging.info("全URL処理完了。")

if __name__ == "__main__":
    asyncio.run(main())