"""
DOM fallback scraper using Playwright selectors.

Scrapes conversation state and messages directly from the DOM as a fallback
when network interception or React state readers are unavailable.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from bridge.browser_controller.session import BrowserSession
from bridge.logging_setup import get_logger


class DOMScraper:
    """Scrapes message and conversation data directly from the DOM."""

    def __init__(self, session: BrowserSession):
        """
        Initialize DOM scraper.

        Args:
            session: BrowserSession instance to scrape from
        """
        self.session = session
        self._logger = get_logger("ui_fallback")

    async def get_latest_assistant_message(self) -> Optional[str]:
        """
        Get the text content of the latest assistant message.

        Tries multiple selectors in priority order.

        Returns:
            Message text or None if not found
        """
        selectors = [
            "[data-message-author-role='assistant']:last-of-type .markdown",
            "[data-message-author-role='assistant']:last-of-type .prose",
            "[data-message-author-role='assistant']:last-of-type",
            "article[data-message-role='assistant']:last-of-type",
            "article[data-message-role='assistant']:last-of-type .prose",
            ".agent-turn:last-of-type .text-base",
            ".agent-turn:last-of-type",
            ".group:last-of-type .markdown",
            ".group:last-of-type .prose",
            ".group:last-of-type",
            "[role='article']:last-of-type",
        ]

        for selector in selectors:
            try:
                locator = self.session.page.locator(selector).last
                text = await locator.inner_text(timeout=2000)

                if text and text.strip():
                    self._logger.debug("latest_message_found", selector=selector, content_len=len(text))
                    return text.strip()

            except Exception as e:
                self._logger.debug("selector_failed", selector=selector, error=type(e).__name__)

        self._logger.warning("no_latest_message_found")
        return None

    async def get_all_messages(self) -> list[dict]:
        """
        Get all visible messages from the conversation.

        Extracts role and content for each message container.

        Returns:
            List of message dictionaries with keys: role, content
        """
        messages = []

        try:
            # Try to find all message containers with role attribute
            locator = self.session.page.locator("[data-message-author-role]")
            count = await locator.count()

            self._logger.debug("found_message_containers", count=count)

            for i in range(count):
                try:
                    element = locator.nth(i)

                    # Get role
                    role_attr = await element.get_attribute("data-message-author-role")
                    role = role_attr or "unknown"

                    # Get content - try multiple inner selectors
                    content_selectors = [
                        ".markdown",
                        ".prose",
                        "[class*='prose']",
                        "article",
                        None,  # Use element itself
                    ]

                    content_text = None
                    for content_sel in content_selectors:
                        try:
                            if content_sel:
                                content_element = element.locator(content_sel).first
                            else:
                                content_element = element

                            content_text = await content_element.inner_text(timeout=1000)
                            if content_text:
                                break
                        except Exception:
                            pass

                    if content_text:
                        messages.append({
                            "role": role,
                            "content": content_text.strip(),
                        })
                        self._logger.debug("message_extracted", role=role, content_len=len(content_text))

                except Exception as e:
                    self._logger.debug("message_extraction_error", index=i, error=type(e).__name__)

        except Exception as e:
            self._logger.warning("get_all_messages_failed", error=str(e))

        self._logger.info("all_messages_extracted", count=len(messages))
        return messages

    async def is_error_shown(self) -> Optional[str]:
        """
        Check if an error message is displayed.

        Looks for common error message selectors.

        Returns:
            Error message text or None if no error
        """
        selectors = [
            "[data-testid='error-message']",
            ".error-message",
            "[role='alert']",
            ".text-red-600",
            ".text-red-500",
            "[class*='error']",
        ]

        for selector in selectors:
            try:
                locator = self.session.page.locator(selector).first
                text = await locator.inner_text(timeout=1000)

                if text and text.strip():
                    self._logger.warning("error_message_found", selector=selector, message=text[:100])
                    return text.strip()

            except Exception:
                pass

        return None

    async def get_generated_images(self) -> list[str]:
        """
        Get URLs of images generated in the conversation.

        Looks for image elements in assistant messages.

        Returns:
            List of image URLs
        """
        urls = []

        try:
            # Find images in assistant messages
            selectors = [
                "[data-message-author-role='assistant'] img[src*='files.oaiusercontent']",
                "[data-message-author-role='assistant'] img",
                "article[data-message-role='assistant'] img",
                ".group:last-of-type img",
            ]

            for selector in selectors:
                try:
                    locator = self.session.page.locator(selector)
                    count = await locator.count()

                    for i in range(count):
                        try:
                            element = locator.nth(i)
                            src = await element.get_attribute("src")

                            if src:
                                urls.append(src)
                                self._logger.debug("image_url_found", src=src[:80])

                        except Exception:
                            pass

                except Exception:
                    pass

        except Exception as e:
            self._logger.warning("get_generated_images_failed", error=str(e))

        self._logger.info("images_found", count=len(urls))
        return urls

    async def get_download_links(self) -> list[dict]:
        """
        Get download links and file references from assistant messages.

        Looks for download buttons and file links.

        Returns:
            List of dictionaries with keys: url, filename, text
        """
        links = []

        try:
            selectors = [
                "a[download]",
                "a[href*='files.oaiusercontent']",
                "[data-message-author-role='assistant'] a[href]",
                "button[data-download]",
            ]

            for selector in selectors:
                try:
                    locator = self.session.page.locator(selector)
                    count = await locator.count()

                    for i in range(count):
                        try:
                            element = locator.nth(i)
                            href = await element.get_attribute("href")
                            download_attr = await element.get_attribute("download")
                            text = await element.inner_text(timeout=1000)

                            if href:
                                # Extract filename from href or download attribute
                                filename = download_attr or href.split("/")[-1]

                                links.append({
                                    "url": href,
                                    "filename": filename,
                                    "text": text.strip() if text else filename,
                                })
                                self._logger.debug("download_link_found", url=href[:80])

                        except Exception:
                            pass

                except Exception:
                    pass

        except Exception as e:
            self._logger.warning("get_download_links_failed", error=str(e))

        self._logger.info("download_links_found", count=len(links))
        return links

    async def wait_for_response(self, timeout_s: float = 120.0) -> Optional[str]:
        """
        Wait for a response to appear and finish generating.

        Uses DOM selectors to detect response containers and generation state.

        Args:
            timeout_s: Maximum seconds to wait

        Returns:
            Response text or None if timeout
        """
        self._logger.info("waiting_for_response", timeout_s=timeout_s)
        start_time = time.monotonic()

        try:
            # Wait for response container to appear
            await asyncio.wait_for(
                self.session.page.wait_for_selector(
                    "[data-message-author-role='assistant'], article[data-message-role='assistant']",
                    timeout=int(timeout_s * 1000),
                ),
                timeout=timeout_s,
            )
            self._logger.debug("response_container_found")
        except asyncio.TimeoutError:
            self._logger.warning("response_container_timeout")
            return None

        # Poll until generation completes
        while time.monotonic() - start_time < timeout_s:
            try:
                # Check for stop button (indicates still generating)
                try:
                    stop_btn = self.session.page.locator(
                        "[data-testid='stop-button'], "
                        "button[aria-label='Stop generating']"
                    )
                    is_visible = await stop_btn.first.is_visible(timeout=1000)
                    if is_visible:
                        self._logger.debug("still_generating")
                        await asyncio.sleep(0.5)
                        continue
                except Exception:
                    pass

                # Try to get latest message
                message = await self.get_latest_assistant_message()
                if message:
                    self._logger.info(
                        "response_received",
                        content_len=len(message),
                        elapsed_s=time.monotonic() - start_time,
                    )
                    return message

                await asyncio.sleep(0.5)

            except Exception as e:
                self._logger.debug("wait_loop_error", error=str(e))
                await asyncio.sleep(1)

        self._logger.warning("wait_for_response_timeout", timeout_s=timeout_s)
        return None

    async def take_screenshot(self, path: str) -> str:
        """
        Take a screenshot of the current page.

        Args:
            path: File path to save screenshot to

        Returns:
            Path to saved screenshot
        """
        try:
            await self.session.page.screenshot(path=path, full_page=True)
            self._logger.info("screenshot_taken", path=path)
            return path
        except Exception as e:
            self._logger.error("screenshot_failed", path=path, error=str(e))
            raise

    async def scroll_to_bottom(self) -> None:
        """Scroll conversation to the bottom."""
        try:
            await self.session.page.evaluate(
                "window.scrollTo(0, document.body.scrollHeight)"
            )
            self._logger.debug("scrolled_to_bottom")
        except Exception as e:
            self._logger.warning("scroll_to_bottom_failed", error=str(e))

    async def scroll_to_top(self) -> None:
        """Scroll conversation to the top."""
        try:
            await self.session.page.evaluate(
                "window.scrollTo(0, 0)"
            )
            self._logger.debug("scrolled_to_top")
        except Exception as e:
            self._logger.warning("scroll_to_top_failed", error=str(e))

    async def get_visible_text_preview(self, max_length: int = 500) -> str:
        """
        Get a preview of visible text on the page.

        Useful for debugging and monitoring.

        Args:
            max_length: Maximum length of preview text

        Returns:
            Visible text preview
        """
        try:
            text = await self.session.page.evaluate(
                "document.body.innerText"
            )

            if text:
                text = text.strip()[:max_length]
                return text

            return ""
        except Exception as e:
            self._logger.warning("get_visible_text_preview_failed", error=str(e))
            return ""

    async def count_messages(self) -> int:
        """
        Get the total count of visible messages.

        Returns:
            Number of messages
        """
        try:
            locator = self.session.page.locator("[data-message-author-role]")
            count = await locator.count()
            self._logger.debug("message_count", count=count)
            return count
        except Exception as e:
            self._logger.warning("count_messages_failed", error=str(e))
            return 0
