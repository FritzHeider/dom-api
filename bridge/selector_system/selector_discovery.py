"""
Self-healing selector system for ChatGPT DOM interaction.
Maintains a cache of verified selectors with confidence tracking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page

from bridge.logging_setup import get_logger

logger = get_logger("selector_discovery")


@dataclass
class SelectorCandidate:
    """Represents a verified selector with metadata."""

    selector: str
    selector_type: str  # "css", "xpath", "aria", "text"
    confidence: float
    last_verified: datetime
    failure_count: int = 0

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "selector": self.selector,
            "selector_type": self.selector_type,
            "confidence": self.confidence,
            "last_verified": self.last_verified.isoformat(),
            "failure_count": self.failure_count,
        }

    @staticmethod
    def from_dict(data: dict) -> SelectorCandidate:
        """Reconstruct from dict."""
        return SelectorCandidate(
            selector=data["selector"],
            selector_type=data["selector_type"],
            confidence=data["confidence"],
            last_verified=datetime.fromisoformat(data["last_verified"]),
            failure_count=data.get("failure_count", 0),
        )


class SelectorStore:
    """In-memory selector cache with optional JSON persistence."""

    def __init__(self, cache_path: Optional[Path] = None):
        self.cache_path = cache_path or Path("./selector_cache.json")
        self._store: dict[str, SelectorCandidate] = {}
        self.load_from_disk()

    def load_from_disk(self) -> None:
        """Load cached selectors from JSON file if it exists."""
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r") as f:
                    data = json.load(f)
                    for key, candidate_dict in data.items():
                        self._store[key] = SelectorCandidate.from_dict(candidate_dict)
                logger.info("selector_cache_loaded", path=str(self.cache_path), count=len(self._store))
            except Exception as e:
                logger.warning("selector_cache_load_failed", error=str(e))

    def save_to_disk(self) -> None:
        """Persist cache to JSON file."""
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "w") as f:
                data = {key: candidate.to_dict() for key, candidate in self._store.items()}
                json.dump(data, f, indent=2)
            logger.debug("selector_cache_saved", path=str(self.cache_path))
        except Exception as e:
            logger.error("selector_cache_save_failed", error=str(e))

    def save(self, key: str, candidate: SelectorCandidate) -> None:
        """Store a verified selector candidate."""
        self._store[key] = candidate
        self.save_to_disk()
        logger.debug("selector_saved", key=key, selector=candidate.selector, confidence=candidate.confidence)

    def load(self, key: str) -> Optional[SelectorCandidate]:
        """Retrieve a cached selector."""
        return self._store.get(key)

    def mark_failed(self, key: str) -> None:
        """Increment failure count for a selector."""
        candidate = self._store.get(key)
        if candidate:
            candidate.failure_count += 1
            candidate.last_verified = datetime.utcnow()
            self.save_to_disk()
            logger.debug("selector_marked_failed", key=key, failure_count=candidate.failure_count)

    def get_best(self, key: str) -> Optional[SelectorCandidate]:
        """Get the best candidate for a key (highest confidence, lowest failures)."""
        candidate = self._store.get(key)
        if candidate and candidate.failure_count > 5:
            logger.warning("selector_too_many_failures", key=key, failures=candidate.failure_count)
            return None
        return candidate

    def clear(self) -> None:
        """Clear all cached selectors."""
        self._store.clear()
        self.save_to_disk()

    def all_keys(self) -> list[str]:
        """Return all cached selector keys."""
        return list(self._store.keys())


class SelectorDiscovery:
    """Discover and verify ChatGPT UI selectors."""

    def __init__(self, page: Page, store: Optional[SelectorStore] = None):
        self.page = page
        self.store = store or SelectorStore()
        self.logger = get_logger("selector_discovery")

    async def _verify_selector(self, selector: str, selector_type: str = "css") -> bool:
        """Verify a selector exists and is visible on the page."""
        try:
            if selector_type == "css":
                element_count = await self.page.locator(selector).count()
                return element_count > 0
            elif selector_type == "xpath":
                element_count = await self.page.locator(f"xpath={selector}").count()
                return element_count > 0
            else:
                return False
        except Exception as e:
            self.logger.debug("selector_verification_failed", selector=selector, error=str(e))
            return False

    async def _find_first_working(
        self, key: str, candidates: list[tuple[str, str]]
    ) -> Optional[SelectorCandidate]:
        """Try candidates in order, return first that works."""
        # Check cached selector first
        cached = self.store.get_best(key)
        if cached and await self._verify_selector(cached.selector, cached.selector_type):
            self.logger.debug("selector_cache_hit", key=key, selector=cached.selector)
            return cached

        # Try each candidate
        for selector, selector_type in candidates:
            if await self._verify_selector(selector, selector_type):
                candidate = SelectorCandidate(
                    selector=selector,
                    selector_type=selector_type,
                    confidence=0.9,
                    last_verified=datetime.utcnow(),
                    failure_count=0,
                )
                self.store.save(key, candidate)
                self.logger.info("selector_discovered", key=key, selector=selector)
                return candidate

        self.logger.warning("selector_discovery_failed", key=key, candidates_tried=len(candidates))
        return None

    async def find_chat_input(self) -> Optional[SelectorCandidate]:
        """Discover the chat input textarea selector."""
        candidates = [
            ("#prompt-textarea", "css"),
            ("textarea[data-id='root']", "css"),
            ("div[contenteditable='true']", "css"),
            ("textarea[placeholder*='Message']", "css"),
            ("textarea[placeholder*='message']", "css"),
            ("[data-testid='text-input']", "css"),
            ("form textarea", "css"),
            ("main textarea", "css"),
        ]
        return await self._find_first_working("chat_input", candidates)

    async def find_submit_button(self) -> Optional[SelectorCandidate]:
        """Discover the submit/send button selector."""
        # The send button only appears after text is typed — type a char first.
        typed = False
        try:
            input_el = self.page.locator("#prompt-textarea").first
            if await input_el.count() > 0:
                await input_el.click()
                await self.page.keyboard.type("a")
                typed = True
        except Exception:
            pass

        candidates = [
            ("button[data-testid='send-button']", "css"),
            ("button[aria-label='Send prompt']", "css"),
            ("button[aria-label='Send message']", "css"),
            ("button[aria-label*='Send']", "css"),
            ("form button[type='submit']", "css"),
            ("button svg[data-icon='paper-plane']", "css"),
        ]
        result = await self._find_first_working("submit_button", candidates)

        # Clear the temporary character
        if typed:
            try:
                await self.page.keyboard.press("Control+a")
                await self.page.keyboard.press("Backspace")
            except Exception:
                pass

        # For the paper-plane case, we need the parent button
        if result and "paper-plane" in result.selector:
            result.selector = f"{result.selector}/.."
            self.store.save("submit_button", result)

        return result

    async def find_response_container(self) -> Optional[SelectorCandidate]:
        """Discover the assistant response container selector."""
        candidates = [
            ("[data-message-author-role='assistant']", "css"),
            (".agent-turn", "css"),
            ("[data-testid*='conversation-turn']:last-of-type", "css"),
            (".markdown.prose", "css"),
            ("article[data-message-role='assistant']", "css"),
        ]
        result = await self._find_first_working("response_container", candidates)

        # This selector only exists after a response is received.
        # Store the best-known candidate so it's ready for first use.
        if result is None:
            fallback = SelectorCandidate(
                selector="[data-message-author-role='assistant']",
                selector_type="css",
                confidence=0.7,
                last_verified=datetime.utcnow(),
                failure_count=0,
            )
            self.store.save("response_container", fallback)
            self.logger.info("selector_stored_as_fallback", key="response_container")
            result = fallback

        return result

    async def find_stop_button(self) -> Optional[SelectorCandidate]:
        """Discover the stop generation button selector."""
        candidates = [
            ("[data-testid='stop-button']", "css"),
            ("button[aria-label='Stop streaming']", "css"),
            ("button[aria-label='Stop generating']", "css"),
            ("button[aria-label*='Stop']", "css"),
        ]
        result = await self._find_first_working("stop_button", candidates)

        # Stop button only exists during active generation.
        # Store the best-known candidate so it's ready for first use.
        if result is None:
            fallback = SelectorCandidate(
                selector="[data-testid='stop-button']",
                selector_type="css",
                confidence=0.7,
                last_verified=datetime.utcnow(),
                failure_count=0,
            )
            self.store.save("stop_button", fallback)
            self.logger.info("selector_stored_as_fallback", key="stop_button")
            result = fallback

        return result

    async def is_generating(self) -> bool:
        """Check if the model is currently generating a response."""
        # Look for stop button presence
        stop_button = await self.find_stop_button()
        if stop_button and await self._verify_selector(stop_button.selector, stop_button.selector_type):
            self.logger.debug("generating_detected_via_stop_button")
            return True

        # Check for streaming indicator classes
        streaming_indicators = [
            ".result-streaming",
            "[data-streaming='true']",
            ".streaming",
        ]
        for indicator in streaming_indicators:
            if await self._verify_selector(indicator, "css"):
                self.logger.debug("generating_detected_via_streaming_indicator", indicator=indicator)
                return True

        return False

    async def rediscover_all(self) -> dict[str, Optional[SelectorCandidate]]:
        """Re-run all discovery methods and update store."""
        self.logger.info("rediscovery_started")

        results = {
            "chat_input": await self.find_chat_input(),
            "submit_button": await self.find_submit_button(),
            "response_container": await self.find_response_container(),
            "stop_button": await self.find_stop_button(),
        }

        successful = sum(1 for v in results.values() if v is not None)
        self.logger.info("rediscovery_completed", successful=successful, total=len(results))

        return results
