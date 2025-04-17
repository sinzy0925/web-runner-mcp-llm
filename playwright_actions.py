# --- ファイル: playwright_actions.py (最終修正版・完全コード) ---
import asyncio
import logging
import os
import time
import pprint
import traceback
import re
from urllib.parse import urljoin, urlparse
from typing import List, Tuple, Optional, Union, Dict, Any, Set

from playwright.async_api import (
    Page,
    Frame,
    Locator,
    FrameLocator,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
    APIRequestContext,
    expect # オプション: 入力値検証に使用する場合
)

import config
import utils
from playwright_finders import find_element_dynamically, find_all_elements_dynamically
from playwright_helper_funcs import get_page_inner_text

logger = logging.getLogger(__name__)

# --- メールアドレス抽出用ヘルパー関数 ---
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
async def _extract_emails_from_page_async(context: BrowserContext, url: str, timeout: int) -> List[str]:
    """指定されたURLからメールアドレスを抽出するヘルパー"""
    page = None
    emails_found: Set[str] = set()
    start_time = time.monotonic()
    page_access_timeout = max(int(timeout * 0.8), 15000)
    logger.info(f"URLからメール抽出開始: {url} (タイムアウト: {page_access_timeout}ms)")
    try:
        page = await context.new_page()
        nav_timeout = max(int(page_access_timeout * 0.9), 10000)
        await page.goto(url, wait_until="load", timeout=nav_timeout)
        remaining_time_for_text = page_access_timeout - (time.monotonic() - start_time) * 1000
        if remaining_time_for_text <= 1000: raise PlaywrightTimeoutError("Not enough time for text extraction")
        text_timeout = int(remaining_time_for_text)
        page_text = await page.locator(':root').inner_text(timeout=text_timeout)
        if page_text:
            found_in_text = EMAIL_REGEX.findall(page_text)
            if found_in_text: emails_found.update(found_in_text)
        mailto_links = await page.locator("a[href^='mailto:']").all()
        if mailto_links:
            for link in mailto_links:
                try:
                    href = await link.get_attribute('href', timeout=500)
                    if href and href.startswith('mailto:'):
                        email_part = href[len('mailto:'):].split('?')[0]
                        if email_part and EMAIL_REGEX.match(email_part):
                            emails_found.add(email_part)
                except Exception: pass
        elapsed = (time.monotonic() - start_time) * 1000
        logger.info(f"メール抽出完了 ({url}). ユニーク候補: {len(emails_found)} ({elapsed:.0f}ms)")
        # --- ★ メール保存処理 (重要) ★ ---
        if emails_found:
            unique_emails_list = list(emails_found)
            try:
                output_dir = Path("output")
                output_dir.mkdir(exist_ok=True)
                mail_file_path = output_dir / "server_mails.txt"
                with open(mail_file_path, "a", encoding="utf-8") as f:
                    for email in unique_emails_list:
                        f.write(email + '\n')
                logger.info(f"Appended {len(unique_emails_list)} unique emails to {mail_file_path}")
            except Exception as mfwe:
                logger.error(f"Mail file write error: {mfwe}")
        # --- ★ メール保存処理ここまで ★ ---
        return list(emails_found) #抽出したメアドリストは返す
    except Exception as e:
        logger.warning(f"メール抽出エラー ({url}): {type(e).__name__} - {e}", exc_info=False)
        logger.debug(f"メール抽出エラー ({url}) Traceback:", exc_info=True)
        return []
    finally:
        if page and not page.is_closed():
            try: await page.close()
            except Exception: pass


# --- Locator試行ヘルパー関数 ---
async def try_locators_sequentially(
    target_scope: Union[Page, FrameLocator],
    hints: List[Dict[str, Any]],
    action_name: str,
    overall_timeout: int
) -> Tuple[Optional[Locator], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """ヒントリストに基づいてLocatorを順番に試し、最初に見つかった有効なLocatorを返す。"""
    start_time = time.monotonic()
    successful_locator: Optional[Locator] = None
    successful_hint: Optional[Dict[str, Any]] = None
    attempt_logs: List[Dict[str, Any]] = []
    hint_count = len(hints) if hints else 1
    base_validation_timeout = max(500, min(2000, overall_timeout // max(1, hint_count)))
    logger.info(f"[{action_name}] Trying {len(hints)} locator hints (overall timeout: {overall_timeout}ms, base validation timeout: {base_validation_timeout}ms)...")
    hints.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get("confidence", "low"), 2))

    for i, hint in enumerate(hints):
        elapsed_ms = (time.monotonic() - start_time) * 1000
        if elapsed_ms >= overall_timeout:
            logger.warning(f"[{action_name}] Overall timeout ({overall_timeout}ms) reached during hint processing.")
            break
        remaining_ms = overall_timeout - elapsed_ms
        current_validation_timeout = min(base_validation_timeout, max(100, int(remaining_ms * 0.8)))

        hint_type = hint.get("type")
        hint_value = hint.get("value")
        hint_name = hint.get("name")
        hint_level = hint.get("level")
        hint_common_selector = hint.get("common_selector")
        hint_index = hint.get("index")
        hint_confidence = hint.get("confidence", "low")

        locator_description = f"Hint {i+1}/{len(hints)}: type={hint_type}, confidence={hint_confidence}"
        value_str = str(hint_value)
        value_log = f"{value_str[:50]}{'...' if len(value_str) > 50 else ''}"
        logging.info(f"  Trying: {locator_description} (value: {value_log})")

        attempt_log = {"hint": hint, "status": "Skipped", "reason": "No locator generated"}
        locator_to_try: Optional[Locator] = None

        try:
            options = {}
            if hint_type == "role_and_text" and isinstance(hint_value, str):
                 role = hint_value
                 if isinstance(hint_name, str):
                     if hint_name.startswith('/') and hint_name.endswith('/') and len(hint_name) > 2:
                          try:
                              pattern_part = hint_name[1:-1]; flags = 0
                              if pattern_part.endswith('i'): pattern_part = pattern_part[:-1]; flags = re.IGNORECASE
                              options["name"] = re.compile(pattern_part, flags)
                              logging.debug(f"    Using get_by_role with regex name: {options['name']}")
                          except re.error as re_err:
                              logging.warning(f"    Invalid regex pattern: {hint_name} - {re_err}. Treating as literal.")
                              options["name"] = hint_name
                     else: options["name"] = hint_name; logging.debug(f"    Using get_by_role with exact name: {options['name']}")
                 if role == "heading" and isinstance(hint_level, int): options["level"] = hint_level
                 if isinstance(role, str) and role: locator_to_try = target_scope.get_by_role(role, **options)
                 else: logging.warning(f"    Invalid role value: {role}"); attempt_log["reason"] = f"Invalid role value: {role}"
            elif hint_type == "test_id" and isinstance(hint_value, str): locator_to_try = target_scope.get_by_test_id(hint_value)
            elif hint_type == "text_exact" and isinstance(hint_value, str): locator_to_try = target_scope.get_by_text(hint_value, exact=True)
            elif hint_type == "placeholder" and isinstance(hint_value, str): locator_to_try = target_scope.get_by_placeholder(hint_value)
            elif hint_type == "aria_label" and isinstance(hint_value, str): locator_to_try = target_scope.get_by_label(hint_value, exact=True)
            elif hint_type == "css_selector_candidate" and isinstance(hint_value, str): locator_to_try = target_scope.locator(hint_value)
            elif hint_type == "nth_child" and isinstance(hint_common_selector, str) and isinstance(hint_index, int) and hint_index >= 0:
                locator_to_try = target_scope.locator(hint_common_selector).nth(hint_index)
            else: attempt_log["reason"] = f"Invalid or unknown hint type/data: {hint}"; logging.warning(f"    -> {attempt_log['reason']}")

            if locator_to_try:
                attempt_log["reason"] = "Checking validity..."
                try: attempt_log["final_selector_attempted"] = repr(locator_to_try)
                except Exception: attempt_log["final_selector_attempted"] = "repr() failed for locator"

                count = await locator_to_try.count()
                attempt_log["count"] = count; logging.debug(f"    -> Count: {count}")
                if count == 1:
                    is_visible = False
                    try:
                        await locator_to_try.wait_for(state='visible', timeout=current_validation_timeout)
                        is_visible = True; logging.debug(f"    -> Visible (within {current_validation_timeout}ms).")
                    except PlaywrightTimeoutError: logging.debug(f"    -> Not visible within {current_validation_timeout}ms timeout.")
                    except Exception as vis_err: logging.warning(f"    -> Visibility check error: {vis_err}")
                    attempt_log["is_visible"] = is_visible
                    if is_visible:
                        successful_locator = locator_to_try; successful_hint = hint
                        attempt_log["status"] = "Success"; attempt_log["reason"] = "Found unique and visible element."
                        logging.info(f"    -> Success! Found element.")
                        attempt_logs.append(attempt_log); break
                    else: attempt_log["status"] = "Fail"; attempt_log["reason"] = "Element found but not visible."; logging.info(f"    -> Element found but not visible.")
                elif count > 1: attempt_log["status"] = "Fail"; attempt_log["reason"] = f"Multiple elements found ({count})."; logging.info(f"    -> Multiple elements found ({count}).")
                else: attempt_log["status"] = "Fail"; attempt_log["reason"] = "Element not found."; logging.info(f"    -> Element not found.")
            else: attempt_log["status"] = "Fail"
        except PlaywrightError as e: attempt_log["status"] = "Error"; attempt_log["reason"] = f"PlaywrightError: {e}"; logging.warning(f"    -> PlaywrightError: {e}")
        except Exception as e: attempt_log["status"] = "Error"; attempt_log["reason"] = f"Unexpected Error: {e}"; logging.warning(f"    -> Unexpected error: {e}"); logging.debug(traceback.format_exc())
        attempt_logs.append(attempt_log)

    if successful_locator: logger.info(f"[{action_name}] Located element using hint: {successful_hint}")
    else: logger.warning(f"[{action_name}] Failed to locate element using any of the provided hints.")
    return successful_locator, successful_hint, attempt_logs


# --- execute_actions_async 本体 (input修正、エラーチェック修正、フォールバック実装含む) ---
async def execute_actions_async(
    initial_page: Page,
    actions: List[Dict[str, Any]],
    api_request_context: APIRequestContext,
    default_timeout: int
) -> Tuple[bool, List[Dict[str, Any]]]:
    results: List[Dict[str, Any]] = []
    current_target: Union[Page, FrameLocator] = initial_page
    root_page: Page = initial_page
    current_context: BrowserContext = root_page.context
    iframe_stack: List[Union[Page, FrameLocator]] = []

    for i, step_data in enumerate(actions):
        step_num = i + 1
        action = step_data.get("action", "").lower()
        selector = step_data.get("selector")
        target_hints = step_data.get("target_hints")
        iframe_selector_input = step_data.get("iframe_selector")
        value = step_data.get("value")
        attribute_name = step_data.get("attribute_name")
        option_type = step_data.get("option_type")
        option_value = step_data.get("option_value")
        action_wait_time = step_data.get("wait_time_ms", default_timeout)

        logger.info(f"--- ステップ {step_num}/{len(actions)}: Action='{action}' ---")
        step_info = {
            "selector": selector, "target_hints_count": len(target_hints) if isinstance(target_hints, list) else None,
            "iframe(指定)": iframe_selector_input, "value": value, "attribute_name": attribute_name,
            "option_type": option_type, "option_value": option_value,
        }
        step_info_str = ", ".join([f"{k}='{str(v)[:50]}...'" if isinstance(v, str) and len(v) > 50 else f"{k}='{v}'"
                                   for k, v in step_info.items() if v is not None])
        logger.info(f"詳細: {step_info_str} (timeout: {action_wait_time}ms)")
        if isinstance(target_hints, list): logger.debug(f"Target Hints:\n{pprint.pformat(target_hints)}")

        step_result_base = {"step": step_num, "action": action}
        if step_data.get("memo"): step_result_base["memo"] = step_data["memo"]

        try:
            if root_page.is_closed(): raise PlaywrightError(f"Root page closed.")
            current_base_url = root_page.url

            # --- Iframe/Parent Frame 切替 ---
            if action == "switch_to_iframe":
                if not iframe_selector_input: raise ValueError("iframe_selector required.")
                logger.info(f"Switching to iframe: {iframe_selector_input}")
                target_frame_locator = current_target.frame_locator(iframe_selector_input)
                try: await target_frame_locator.locator(':root').wait_for(state='attached', timeout=action_wait_time)
                except PlaywrightTimeoutError as e: raise PlaywrightTimeoutError(f"Iframe '{iframe_selector_input}' not found: {e}")
                if id(current_target) not in [id(s) for s in iframe_stack]: iframe_stack.append(current_target)
                current_target = target_frame_locator; logger.info(f"Switched to FrameLocator.")
                results.append({**step_result_base, "status": "success", "selector": iframe_selector_input}); continue
            elif action == "switch_to_parent_frame":
                status="warning"; message=None
                if not iframe_stack: logger.warning("Already at top-level."); message="Already at top-level..."
                else: current_target = iframe_stack.pop(); logger.info(f"Switched to parent: {type(current_target).__name__}"); status="success"
                if status == "warning" and isinstance(current_target, FrameLocator): current_target = root_page
                results.append({**step_result_base, "status": status, "message": message}); continue

            # --- ページ全体操作 ---
            if action in ["wait_page_load", "sleep", "scroll_page_to_bottom"]:
                 if action == "wait_page_load":
                     await root_page.wait_for_load_state("load", timeout=action_wait_time); logger.info("Page load complete.")
                 elif action == "sleep":
                     try: seconds = float(value); assert seconds >= 0
                     except: raise ValueError("Invalid sleep value.")
                     await asyncio.sleep(seconds); logger.info(f"Slept for {seconds}s.")
                     results.append({**step_result_base, "status": "success", "duration_sec": seconds}); continue
                 elif action == "scroll_page_to_bottom":
                     await root_page.evaluate('window.scrollTo(0, document.body.scrollHeight)'); await asyncio.sleep(0.5); logger.info("Scrolled to bottom.")
                 results.append({**step_result_base, "status": "success"}); continue

            # --- 要素操作準備と要素特定ロジック ---
            target_element: Optional[Locator] = None
            found_elements_list: List[Tuple[Locator, Union[Page, FrameLocator]]] = []
            found_scope: Optional[Union[Page, FrameLocator]] = None
            successful_hint_info: Optional[Dict[str, Any]] = None
            locator_attempt_logs: List[Dict[str, Any]] = []

            single_element_actions = ["click", "input", "hover", "get_inner_text", "get_text_content", "get_inner_html", "get_attribute", "wait_visible", "select_option", "scroll_to_element"]
            multiple_elements_actions = ["get_all_attributes", "get_all_text_contents"]
            is_screenshot_action = action == "screenshot"
            needs_single_element = action in single_element_actions or (is_screenshot_action and (target_hints or selector))
            needs_multiple_elements = action in multiple_elements_actions

            # --- 要素特定実行 ---
            if needs_single_element:
                element_located_by = None

                # 1. target_hints を試す
                if target_hints and isinstance(target_hints, list) and target_hints:
                    logger.info(f"Locating element based on target_hints...")
                    target_element, successful_hint_info, locator_attempt_logs = await try_locators_sequentially(
                        current_target, target_hints, action, action_wait_time
                    )
                    step_result_base["locator_attempts"] = locator_attempt_logs
                    if target_element:
                        logger.info(f"Element located using target_hints: {successful_hint_info}")
                        step_result_base["locator_hint_used"] = successful_hint_info
                        found_scope = current_target
                        element_located_by = 'hints'
                    else:
                        logger.warning("Failed to locate element using target_hints.")

                # 2. target_hints がない、または失敗した場合に selector でフォールバック
                if not target_element and selector:
                    fallback_reason = "target_hints not provided" if not target_hints else "target_hints failed"
                    logger.warning(f"{fallback_reason}, falling back to dynamic search using selector: '{selector}'")
                    required_state = 'visible' if action in ['click', 'hover', 'input', 'wait_visible', 'select_option', 'scroll_to_element', 'screenshot'] else 'attached'
                    target_element, found_scope = await find_element_dynamically(
                        current_target, selector, max_depth=config.DYNAMIC_SEARCH_MAX_DEPTH, timeout=action_wait_time, target_state=required_state
                    )
                    if target_element and found_scope:
                        logger.info(f"Element located using fallback selector '{selector}'.")
                        step_result_base["selector_used_as_fallback"] = selector
                        element_located_by = 'fallback_selector'
                    else:
                        logger.warning(f"Failed to locate element using fallback selector '{selector}' as well.")

                # 3. target_hints も selector もなく、要素が必要な場合のエラーチェック
                if not target_hints and not selector:
                     raise ValueError(f"Action '{action}' requires 'target_hints' or 'selector'.")

                # --- 要素特定最終チェック ---
                if not target_element:
                    error_msg = f"Failed to locate required single element for action '{action}'."
                    tried_hints = step_result_base.get("locator_attempts") is not None
                    tried_fallback = step_result_base.get("selector_used_as_fallback") is not None
                    original_selector = step_data.get("selector")
                    if tried_hints and tried_fallback: error_msg += f" Tried target_hints and fallback selector '{step_result_base['selector_used_as_fallback']}'."
                    elif tried_hints and not original_selector: error_msg += " Tried target_hints, but no fallback selector was provided."
                    elif tried_hints: error_msg += f" Tried target_hints and fallback selector '{original_selector}'."
                    elif original_selector: error_msg += f" Tried selector '{original_selector}'."
                    raise PlaywrightError(error_msg) # エラータイプを PlaywrightError に

            elif needs_multiple_elements:
                if not selector: raise ValueError(f"Action '{action}' requires a 'selector'.")
                logger.info(f"Locating multiple elements using selector '{selector}'...")
                found_elements_list = await find_all_elements_dynamically(
                    current_target, selector, max_depth=config.DYNAMIC_SEARCH_MAX_DEPTH, timeout=action_wait_time
                )
                if not found_elements_list: logger.warning(f"Element(s) for '{selector}' not found.")
                step_result_base["selector"] = selector

            # --- スコープ更新処理 ---
            if needs_single_element and found_scope:
                if id(found_scope) != id(current_target):
                    logger.info(f"Scope updated to: {type(found_scope).__name__}")
                    if id(current_target) not in [id(s) for s in iframe_stack]: iframe_stack.append(current_target)
                    current_target = found_scope
                logger.info(f"Final operating scope: {type(current_target).__name__}")

            # --- 各アクション実行 ---
            action_result_details = {} # 結果詳細用辞書
            # locator情報が結果に含まれるように調整
            if step_result_base.get("locator_hint_used"): action_result_details["locator_hint_used"] = step_result_base["locator_hint_used"]
            elif step_result_base.get("selector_used_as_fallback"): action_result_details["selector"] = step_result_base["selector_used_as_fallback"]; action_result_details["locator_method"] = "fallback_selector"
            elif step_result_base.get("selector"): action_result_details["selector"] = step_result_base["selector"]; action_result_details["locator_method"] = "selector"

            if action == "click":
                if not target_element: raise PlaywrightError("Click action failed: Element could not be located.")
                logger.info("Clicking element...")
                context_for_click = root_page.context; new_page: Optional[Page] = None
                try:
                    async with context_for_click.expect_page(timeout=config.NEW_PAGE_EVENT_TIMEOUT) as new_page_info:
                        await target_element.click(timeout=action_wait_time)
                    new_page = await new_page_info.value; new_page_url = new_page.url; logger.info(f"New page opened: {new_page_url}")
                    try: await new_page.wait_for_load_state("load", timeout=action_wait_time)
                    except PlaywrightTimeoutError: logger.warning(f"New page load timeout.")
                    root_page = new_page; current_target = new_page; current_context = new_page.context; iframe_stack.clear()
                    action_result_details.update({"new_page_opened": True, "new_page_url": new_page_url})
                except PlaywrightTimeoutError:
                    logger.info(f"No new page opened within {config.NEW_PAGE_EVENT_TIMEOUT}ms timeout.")
                    action_result_details["new_page_opened"] = False
                except Exception as click_err: logger.error(f"Click error: {click_err}", exc_info=True); raise click_err
                results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "input":
                 if not target_element: raise PlaywrightError("Input action failed: Element could not be located.")
                 if value is None: raise ValueError("Input requires 'value'.")
                 input_value_str = str(value)
                 logger.info(f"要素に '{input_value_str[:50]}{'...' if len(input_value_str) > 50 else ''}' を入力します...")
                 try:
                     await target_element.wait_for(state='visible', timeout=max(1000, action_wait_time // 3))
                     #await target_element.wait_for(state='enabled', timeout=max(1000, action_wait_time // 3)) # Incorrect state
                     logger.debug("Clicking element to ensure focus before input.")
                     await target_element.click(timeout=max(1000, action_wait_time // 3))
                     await target_element.fill(input_value_str, timeout=action_wait_time)
                     await asyncio.sleep(0.1) # Short wait
                     logger.info("入力が成功しました。")
                     action_result_details["value"] = value
                     results.append({**step_result_base, "status": "success", **action_result_details})
                 except PlaywrightTimeoutError as e: logger.error(f"Input action timed out: {e}"); raise PlaywrightTimeoutError(f"Timeout during input: {e}") from e
                 except Exception as e: logger.error(f"Error during input action: {e}", exc_info=True); raise PlaywrightError(f"Failed input: {e}") from e

            elif action == "hover":
                 if not target_element: raise PlaywrightError("Hover action failed: Element could not be located.")
                 await target_element.hover(timeout=action_wait_time); logger.info("Hover success.")
                 results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "get_inner_text":
                 if not target_element: raise PlaywrightError("Get innerText failed: Element could not be located.")
                 text = await target_element.inner_text(timeout=action_wait_time); text = text.strip() if text else ""
                 logger.info(f"Got innerText: '{text[:100]}...'"); action_result_details["text"] = text
                 results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "get_text_content":
                 if not target_element: raise PlaywrightError("Get textContent failed: Element could not be located.")
                 text = await target_element.text_content(timeout=action_wait_time); text = text.strip() if text else ""
                 logger.info(f"Got textContent: '{text[:100]}...'"); action_result_details["text"] = text
                 results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "get_inner_html":
                 if not target_element: raise PlaywrightError("Get innerHTML failed: Element could not be located.")
                 html_content = await target_element.inner_html(timeout=action_wait_time)
                 logger.info(f"Got innerHTML: {html_content[:500]}..."); action_result_details["html"] = html_content
                 results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "get_attribute":
                if not target_element: raise PlaywrightError("Get attribute failed: Element could not be located.")
                if not attribute_name: raise ValueError("Get attribute requires 'attribute_name'.")
                attr_value = await target_element.get_attribute(attribute_name, timeout=action_wait_time)
                pdf_text_content = None; processed_value = attr_value
                if attribute_name.lower() == 'href' and attr_value is not None:
                     try:
                         absolute_url = urljoin(current_base_url, attr_value); processed_value = absolute_url
                         if absolute_url.lower().endswith('.pdf'):
                             pdf_bytes = await utils.download_pdf_async(api_request_context, absolute_url)
                             if pdf_bytes: pdf_text_content = await asyncio.to_thread(utils.extract_text_from_pdf_sync, pdf_bytes)
                             else: pdf_text_content = "Error: PDF download failed."
                     except Exception as url_e: logger.error(f"URL/PDF error: {url_e}"); pdf_text_content = f"Error: {url_e}"
                logger.info(f"Got attribute '{attribute_name}': '{processed_value}'")
                action_result_details.update({"attribute": attribute_name, "value": processed_value})
                if pdf_text_content is not None: action_result_details["pdf_text"] = pdf_text_content
                results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "get_all_attributes":
                if not selector: raise ValueError("get_all_attributes requires 'selector'.")
                if not attribute_name: raise ValueError("get_all_attributes requires 'attribute_name'.")
                if not found_elements_list: logger.warning(f"No elements for '{selector}'."); action_result_details["results_count"] = 0
                else:
                    num_found = len(found_elements_list); logger.info(f"Getting '{attribute_name}' from {num_found} elements...")
                    url_list, pdf_list, content_list, mail_list, generic_list = [],[],[],[],[]
                    if attribute_name.lower() in ['href', 'pdf', 'content', 'mail']:
                        CONCURRENT_LIMIT=5; semaphore=asyncio.Semaphore(CONCURRENT_LIMIT)
                        async def process_single_element_for_href_related(locator: Locator, index: int, base_url: str, attr_mode: str, sem: asyncio.Semaphore) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[List[str]]]:
                            original_href: Optional[str] = None; absolute_url: Optional[str] = None
                            pdf_text: Optional[str] = None; scraped_text: Optional[str] = None
                            emails_from_page: Optional[List[str]] = None
                            async with sem:
                                try:
                                    href_timeout = max(500, action_wait_time // num_found if num_found > 5 else action_wait_time // 3)
                                    original_href = await locator.get_attribute("href", timeout=href_timeout)
                                    if original_href is None: return None, None, None, None
                                    try: absolute_url = urljoin(base_url, original_href); parsed_url = urlparse(absolute_url)
                                    except Exception: return f"Error converting URL: {original_href}", None, None, None
                                    if parsed_url.scheme not in ['http', 'https']: return absolute_url, None, None, None # Return URL only if not HTTP/S

                                    if attr_mode == 'pdf' and absolute_url.lower().endswith('.pdf'):
                                         pdf_bytes = await utils.download_pdf_async(api_request_context, absolute_url)
                                         if pdf_bytes: pdf_text = await asyncio.to_thread(utils.extract_text_from_pdf_sync, pdf_bytes)
                                         else: pdf_text = "Error: PDF download failed."
                                    elif attr_mode == 'content' and not absolute_url.lower().endswith('.pdf'):
                                         success, content_or_error = await get_page_inner_text(current_context, absolute_url, action_wait_time); scraped_text = content_or_error
                                    elif attr_mode == 'mail': emails_from_page = await _extract_emails_from_page_async(current_context, absolute_url, action_wait_time)
                                    return absolute_url, pdf_text, scraped_text, emails_from_page
                                except Exception as e: return f"Error: {type(e).__name__}", None, None, None
                        tasks = [process_single_element_for_href_related(loc, idx, current_base_url, attribute_name.lower(), semaphore) for idx, (loc, _) in enumerate(found_elements_list)]
                        results_tuples = await asyncio.gather(*tasks)
                        flat_mails=[]; processed_domains=set()
                        for url, pdf, content, mails in results_tuples:
                            url_list.append(url)
                            if attribute_name.lower() == 'pdf': pdf_list.append(pdf)
                            if attribute_name.lower() == 'content': content_list.append(content)
                            if attribute_name.lower() == 'mail' and mails: flat_mails.extend(mails)
                        if attribute_name.lower() == 'mail':
                             unique_emails_this_step = []
                             for email in flat_mails:
                                 if isinstance(email, str) and '@' in email:
                                     try: domain = email.split('@',1)[1].lower();
                                     except IndexError: continue
                                     if domain and domain not in processed_domains: unique_emails_this_step.append(email); processed_domains.add(domain)
                             mail_list = unique_emails_this_step # Use the unique list for results
                             # --- ★ メール保存処理を _extract_emails_from_page_async 内に移動 ★ ---
                        action_result_details["results_count"] = len(url_list)
                        if attribute_name.lower() in ['href', 'pdf', 'content', 'mail']: action_result_details["url_list"] = url_list
                        if attribute_name.lower() == 'pdf': action_result_details["pdf_texts"] = pdf_list
                        if attribute_name.lower() == 'content': action_result_details["scraped_texts"] = content_list
                        if attribute_name.lower() == 'mail': action_result_details["extracted_emails"] = mail_list # 結果にはドメインユニークリストを記録
                    else: # 通常属性
                        async def get_single_attr(locator: Locator, attr_name: str, index: int, timeout_ms: int) -> Optional[str]:
                            try: return await locator.get_attribute(attr_name, timeout=timeout_ms)
                            except PlaywrightTimeoutError: logger.warning(f"Timeout getting attr '{attr_name}' for element {index}"); return f"Error: Timeout"
                            except Exception as e: logger.warning(f"Error getting attr '{attr_name}' for element {index}: {type(e).__name__}"); return f"Error: {type(e).__name__}"
                        timeout = max(500, action_wait_time // num_found if num_found > 5 else action_wait_time // 3)
                        tasks = [get_single_attr(loc, attribute_name, idx, timeout) for idx, (loc, _) in enumerate(found_elements_list)]
                        generic_list = await asyncio.gather(*tasks)
                        action_result_details.update({"attribute": attribute_name, "attribute_list": generic_list, "results_count": len(generic_list)})
                    action_result_details["attribute"] = attribute_name
                results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "get_all_text_contents":
                if not selector: raise ValueError("get_all_text_contents requires 'selector'.")
                text_list: List[Optional[str]] = []
                if not found_elements_list: logger.warning(f"No elements for '{selector}'."); action_result_details["results_count"] = 0
                else:
                    num_found = len(found_elements_list); logger.info(f"Getting textContent from {num_found} elements...")
                    async def get_single_text(locator: Locator, index: int, timeout_ms: int) -> Optional[str]:
                        try: text = await locator.text_content(timeout=timeout_ms); return text.strip() if text else ""
                        except PlaywrightTimeoutError: logger.warning(f"Timeout getting text for element {index}"); return "Error: Timeout"
                        except Exception as e: logger.warning(f"Error getting text for element {index}: {type(e).__name__}"); return f"Error: {type(e).__name__}"
                    timeout = max(500, action_wait_time // num_found if num_found > 5 else action_wait_time // 3)
                    tasks = [get_single_text(loc, idx, timeout) for idx, (loc, _) in enumerate(found_elements_list)]
                    text_list = await asyncio.gather(*tasks)
                    action_result_details["results_count"] = len(text_list)
                action_result_details["text_list"] = text_list
                results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "wait_visible":
                if not target_element: raise PlaywrightError("Wait visible failed: Element could not be located.")
                logger.info("Element visibility confirmed (likely during location).")
                results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "select_option":
                  if not target_element: raise PlaywrightError("Select option failed: Element could not be located.")
                  if option_type not in ['value', 'index', 'label'] or option_value is None: raise ValueError("Invalid option_type/value.")
                  logger.info(f"Selecting option (Type: {option_type}, Value: '{option_value}')...")
                  if option_type == 'value': await target_element.select_option(value=str(option_value), timeout=action_wait_time)
                  elif option_type == 'index': await target_element.select_option(index=int(option_value), timeout=action_wait_time)
                  elif option_type == 'label': await target_element.select_option(label=str(option_value), timeout=action_wait_time)
                  logger.info("Select option success.")
                  action_result_details.update({"option_type": option_type, "option_value": option_value})
                  results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "scroll_to_element":
                   if not target_element: raise PlaywrightError("Scroll action failed: Element could not be located.")
                   await target_element.scroll_into_view_if_needed(timeout=action_wait_time); await asyncio.sleep(0.3); logger.info("Scroll success.")
                   results.append({**step_result_base, "status": "success", **action_result_details})

            elif action == "screenshot":
                  filename_base = str(value).strip() if value else f"screenshot_step{step_num}"
                  filename = f"{filename_base}.png" if not filename_base.lower().endswith(('.png', '.jpg', '.jpeg')) else filename_base
                  screenshot_path = os.path.join(config.DEFAULT_SCREENSHOT_DIR, filename); os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)
                  logger.info(f"Saving screenshot to '{screenshot_path}'...")
                  if target_element: await target_element.screenshot(path=screenshot_path, timeout=action_wait_time); logger.info("Element screenshot saved.")
                  else: await root_page.screenshot(path=screenshot_path, full_page=True, timeout=action_wait_time*2); logger.info("Page screenshot saved.")
                  action_result_details["filename"] = screenshot_path
                  results.append({**step_result_base, "status": "success", **action_result_details})

            else: # 未知のアクション
                known_actions = single_element_actions + multiple_elements_actions + \
                                ["switch_to_iframe", "switch_to_parent_frame", "wait_page_load",
                                 "sleep", "scroll_page_to_bottom", "screenshot"]
                if action not in known_actions:
                     logger.warning(f"Undefined action '{action}'. Skipping.")
                     results.append({**step_result_base, "status": "skipped", "message": f"Undefined action: {action}"})
                     continue # 次のステップへ


        # --- ステップごとのエラーハンドリング ---
        except (PlaywrightTimeoutError, PlaywrightError, ValueError, IndexError, Exception) as e:
            error_message = f"ステップ {step_num} ({action}) エラー: {type(e).__name__} - {e}"
            logger.error(error_message, exc_info=True)
            error_screenshot_path = None
            if root_page and not root_page.is_closed():
                 timestamp = time.strftime("%Y%m%d_%H%M%S")
                 error_ss_path = os.path.join(config.DEFAULT_SCREENSHOT_DIR, f"error_step{step_num}_{timestamp}.png")
                 try:
                     os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)
                     await root_page.screenshot(path=error_ss_path, full_page=True, timeout=10000)
                     error_screenshot_path = error_ss_path; logger.info(f"Error screenshot saved: {error_ss_path}")
                 except Exception as ss_e: logger.error(f"Failed to save error screenshot: {ss_e}")

            error_details = {
                **step_result_base,
                "status": "error",
                "selector": selector, "target_hints": target_hints, "locator_attempts": locator_attempt_logs,
                "message": str(e), "full_error": error_message, "traceback": traceback.format_exc(),
            }
            if error_screenshot_path: error_details["error_screenshot"] = error_screenshot_path
            results.append(error_details)
            return False, results # 処理中断

    # --- 全ステップ完了 ---
    logger.info("All steps processed.")
    return True, results
# --- ▲▲▲ execute_actions_async 本体 (ここまで) ▲▲▲ ---