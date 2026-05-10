"""
Tests for the selector discovery and self-healing system.
Tests DOM selector storage, discovery, persistence, and failure tracking.
"""

import pytest
import json
import tempfile
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from typing import Dict, List


class SelectorCandidate:
    """Represents a DOM selector candidate with confidence metric."""

    def __init__(self, selector: str, confidence: float = 0.8):
        self.selector = selector
        self.confidence = confidence
        self.failed_count = 0
        self.last_used = None

    def to_dict(self) -> dict:
        return {
            "selector": self.selector,
            "confidence": self.confidence,
            "failed_count": self.failed_count,
            "last_used": self.last_used,
        }

    @staticmethod
    def from_dict(data: dict) -> "SelectorCandidate":
        candidate = SelectorCandidate(data["selector"], data.get("confidence", 0.8))
        candidate.failed_count = data.get("failed_count", 0)
        candidate.last_used = data.get("last_used")
        return candidate


class SelectorStore:
    """Stores and manages DOM selectors with confidence tracking."""

    def __init__(self, storage_path: str | None = None):
        self.storage_path = storage_path
        self.selectors: Dict[str, List[SelectorCandidate]] = {}

    def add_selector(self, key: str, selector: str, confidence: float = 0.8) -> None:
        """Add a selector candidate for a given key."""
        candidate = SelectorCandidate(selector, confidence)
        if key not in self.selectors:
            self.selectors[key] = []
        self.selectors[key].append(candidate)

    def mark_failed(self, key: str, selector: str) -> None:
        """Mark a selector as failed."""
        if key not in self.selectors:
            return

        for candidate in self.selectors[key]:
            if candidate.selector == selector:
                candidate.failed_count += 1
                break

    def get_best(self, key: str) -> SelectorCandidate | None:
        """Get selector with highest confidence."""
        if key not in self.selectors or not self.selectors[key]:
            return None

        # Filter out failed selectors
        valid = [c for c in self.selectors[key] if c.failed_count < 3]
        if not valid:
            return None

        return max(valid, key=lambda c: c.confidence)

    def get_all_candidates(self, key: str) -> List[SelectorCandidate]:
        """Get all candidates for a key."""
        return self.selectors.get(key, [])

    def get_all_keys(self) -> List[str]:
        """Get all selector keys."""
        return list(self.selectors.keys())

    async def save(self) -> None:
        """Persist selectors to file."""
        if not self.storage_path:
            return

        data = {
            key: [c.to_dict() for c in candidates]
            for key, candidates in self.selectors.items()
        }

        with open(self.storage_path, "w") as f:
            json.dump(data, f, indent=2)

    async def load(self) -> None:
        """Load selectors from file."""
        if not self.storage_path or not Path(self.storage_path).exists():
            return

        with open(self.storage_path, "r") as f:
            data = json.load(f)

        self.selectors = {
            key: [SelectorCandidate.from_dict(c) for c in candidates]
            for key, candidates in data.items()
        }


class SelectorDiscovery:
    """Discovers DOM selectors using Playwright locators."""

    async def find_chat_input(self, page) -> SelectorCandidate | None:
        """Discover chat input field selector."""
        # Common ChatGPT selectors
        candidates = [
            ('textarea[placeholder*="message"]', 0.95),
            ('div[contenteditable="true"]', 0.85),
            ('input[type="text"][placeholder*="Say something"]', 0.9),
            ('textarea.rounded-lg', 0.8),
        ]

        for selector, confidence in candidates:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count > 0:
                    return SelectorCandidate(selector, confidence)
            except Exception:
                continue

        return None

    async def find_send_button(self, page) -> SelectorCandidate | None:
        """Discover send button selector."""
        candidates = [
            ('button:has-text("Send")', 0.9),
            ('button[title="Send"]', 0.85),
            ('button.bg-green-500', 0.75),
        ]

        for selector, confidence in candidates:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count > 0:
                    return SelectorCandidate(selector, confidence)
            except Exception:
                continue

        return None

    async def rediscover_all(self, page, store: SelectorStore) -> dict:
        """Rediscover all known selectors."""
        results = {}

        # Discover chat input
        chat_input = await self.find_chat_input(page)
        if chat_input:
            results["chat_input"] = chat_input
            store.add_selector("chat_input", chat_input.selector, chat_input.confidence)

        # Discover send button
        send_button = await self.find_send_button(page)
        if send_button:
            results["send_button"] = send_button
            store.add_selector("send_button", send_button.selector, send_button.confidence)

        return results


# Test Fixtures

@pytest.fixture
def temp_storage():
    """Create temporary file for storage."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def selector_store(temp_storage):
    """Create selector store with temporary storage."""
    return SelectorStore(storage_path=temp_storage)


@pytest.fixture
def discovery():
    """Create selector discovery instance."""
    return SelectorDiscovery()


@pytest.fixture
def mock_page():
    """Create mock Playwright page."""
    page = AsyncMock()
    page.locator = MagicMock()
    return page


# Test Cases

@pytest.mark.asyncio
async def test_selector_store_save_and_load(selector_store):
    """Test saving and loading selectors from file."""
    selector_store.add_selector("chat_input", "textarea.input", 0.95)
    selector_store.add_selector("send_button", "button.send", 0.9)

    await selector_store.save()

    # Create new store and load
    new_store = SelectorStore(storage_path=selector_store.storage_path)
    await new_store.load()

    assert "chat_input" in new_store.selectors
    assert "send_button" in new_store.selectors
    assert len(new_store.selectors["chat_input"]) == 1


@pytest.mark.asyncio
async def test_selector_store_mark_failed_increments_count(selector_store):
    """Test that marking selector as failed increments counter."""
    selector_store.add_selector("chat_input", "bad_selector", 0.5)

    assert selector_store.selectors["chat_input"][0].failed_count == 0

    selector_store.mark_failed("chat_input", "bad_selector")
    assert selector_store.selectors["chat_input"][0].failed_count == 1

    selector_store.mark_failed("chat_input", "bad_selector")
    assert selector_store.selectors["chat_input"][0].failed_count == 2


@pytest.mark.asyncio
async def test_selector_store_get_best_returns_highest_confidence(selector_store):
    """Test that get_best returns highest confidence selector."""
    selector_store.add_selector("chat_input", "selector_1", 0.7)
    selector_store.add_selector("chat_input", "selector_2", 0.95)
    selector_store.add_selector("chat_input", "selector_3", 0.8)

    best = selector_store.get_best("chat_input")
    assert best is not None
    assert best.selector == "selector_2"
    assert best.confidence == 0.95


@pytest.mark.asyncio
async def test_selector_store_get_best_excludes_failed(selector_store):
    """Test that get_best excludes failed selectors."""
    selector_store.add_selector("chat_input", "good_selector", 0.9)
    selector_store.add_selector("chat_input", "bad_selector", 0.95)

    # Mark bad_selector as failed 3+ times
    for _ in range(3):
        selector_store.mark_failed("chat_input", "bad_selector")

    best = selector_store.get_best("chat_input")
    assert best.selector == "good_selector"


@pytest.mark.asyncio
async def test_selector_store_persistence_roundtrip(selector_store):
    """Test complete save/load roundtrip maintains data."""
    selector_store.add_selector("chat_input", "textarea.chat", 0.95)
    selector_store.add_selector("chat_input", "div[contenteditable]", 0.85)
    selector_store.add_selector("send_button", "button.send", 0.9)

    # Mark one as failed
    selector_store.mark_failed("chat_input", "textarea.chat")

    await selector_store.save()

    # Load into new store
    new_store = SelectorStore(storage_path=selector_store.storage_path)
    await new_store.load()

    # Verify data integrity
    chat_candidates = new_store.get_all_candidates("chat_input")
    assert len(chat_candidates) == 2

    first_candidate = [c for c in chat_candidates if c.selector == "textarea.chat"][0]
    assert first_candidate.failed_count == 1
    assert first_candidate.confidence == 0.95


@pytest.mark.asyncio
async def test_selector_discovery_find_chat_input_returns_candidate(mock_page):
    """Test discovering chat input field."""
    # Setup mock locator
    mock_locator = AsyncMock()
    mock_locator.count = AsyncMock(return_value=1)

    # First selector matches
    def locator_side_effect(selector):
        if selector == 'textarea[placeholder*="message"]':
            return mock_locator
        else:
            raise Exception("Selector not found")

    mock_page.locator.side_effect = locator_side_effect

    discovery = SelectorDiscovery()
    result = await discovery.find_chat_input(mock_page)

    assert result is not None
    assert 'textarea[placeholder*="message"]' in result.selector
    assert result.confidence == 0.95


@pytest.mark.asyncio
async def test_selector_discovery_find_chat_input_returns_none_on_failure(mock_page):
    """Test that discovery returns None if no selector found."""
    # All locators fail
    mock_page.locator.side_effect = Exception("Not found")

    discovery = SelectorDiscovery()
    result = await discovery.find_chat_input(mock_page)

    assert result is None


@pytest.mark.asyncio
async def test_selector_discovery_rediscover_all_returns_all_keys(mock_page):
    """Test rediscovering all selectors."""
    mock_locator = AsyncMock()
    mock_locator.count = AsyncMock(return_value=1)

    # Return mock for specific selectors
    def locator_side_effect(selector):
        if "textarea" in selector or "contenteditable" in selector:
            return mock_locator
        elif "Send" in selector or "bg-green" in selector:
            return mock_locator
        raise Exception("Not found")

    mock_page.locator.side_effect = locator_side_effect

    discovery = SelectorDiscovery()
    store = SelectorStore()

    results = await discovery.rediscover_all(mock_page, store)

    assert "chat_input" in results
    assert "send_button" in results
    assert store.get_best("chat_input") is not None


@pytest.mark.asyncio
async def test_selector_candidate_failure_tracking(selector_store):
    """Test complete failure tracking workflow."""
    selector_store.add_selector("element", "selector_a", 0.9)
    selector_store.add_selector("element", "selector_b", 0.85)

    # Use selector_a
    best = selector_store.get_best("element")
    assert best.selector == "selector_a"

    # Mark as failed twice
    selector_store.mark_failed("element", "selector_a")
    selector_store.mark_failed("element", "selector_a")

    # Should still return it (failed_count < 3)
    best = selector_store.get_best("element")
    assert best.selector == "selector_a"

    # One more failure (total 3)
    selector_store.mark_failed("element", "selector_a")

    # Should now return selector_b
    best = selector_store.get_best("element")
    assert best.selector == "selector_b"


@pytest.mark.asyncio
async def test_selector_store_get_all_keys(selector_store):
    """Test retrieving all selector keys."""
    selector_store.add_selector("chat_input", "textarea", 0.9)
    selector_store.add_selector("send_button", "button", 0.85)
    selector_store.add_selector("sidebar", "nav", 0.8)

    keys = selector_store.get_all_keys()

    assert len(keys) == 3
    assert "chat_input" in keys
    assert "send_button" in keys
    assert "sidebar" in keys


@pytest.mark.asyncio
async def test_selector_store_get_all_candidates(selector_store):
    """Test retrieving all candidates for a key."""
    selector_store.add_selector("chat_input", "textarea", 0.9)
    selector_store.add_selector("chat_input", "div", 0.8)
    selector_store.add_selector("chat_input", "input", 0.7)

    candidates = selector_store.get_all_candidates("chat_input")

    assert len(candidates) == 3
    assert all(c.selector in ["textarea", "div", "input"] for c in candidates)
