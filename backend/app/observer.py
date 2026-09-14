"""Playwright observer attached to a worker-open Chrome via CDP.

The observer is a read-only, consent-only automation:

- reads the current page state (URL, title, visible text) and classifies it with
  login_policy.classify_page;
- advances *only* ordinary consent interstitials (I agree / Continue pages on
  accounts.* / consent.*) by clicking the match;
- reports CAPTCHA / MFA / security / unknown pages by calling
  observe_session_page, which pauses the session for a human;

It never fills or submits credentials, recovery codes, or 2FA secrets, never
clears/deletes cookies, and never manipulates fingerprint/stealth signals.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .login_policy import PageAction, PageObservation, classify_page

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page, sync_playwright

logger = logging.getLogger(__name__)

_SAFE_CONSENT_LABELS = ("i agree", "accept all", "continue", "next", "got it", "ok")


class BrowserObserver:
    def __init__(self, session_id: str, profile_id: str, cdp_port: int, observe_session_page=object, interval: float = 1.0) -> None:
        self.session_id = session_id
        self.profile_id = profile_id
        self.cdp_port = cdp_port
        self._observe_session_page = observe_session_page
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_observation: tuple[str, str, str, str] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"browser-observer-{self.session_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragmatic fallback: no observer, session is human hand-off
            logger.warning("playwright not installed; session %s is human hand-off", self.session_id)
            return
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.cdp_port}")
                try:
                    while not self._stop.is_set():
                        self._tick(browser)
                        self._stop.wait(self._interval)
                finally:
                    browser.close()
        except Exception:  # noqa: BLE001 - observer must never kill the worker
            logger.exception("browser observer ended for session %s", self.session_id)

    def _tick(self, browser: "Browser") -> None:
        for page in browser.contexts[0].pages if browser.contexts else []:
            url = page.url or ""
            title = page.title() or ""
            text = _visible_text(page)
            signal, action = classify_page(PageObservation(url=url, title=title, text=text))
            observation_key = (url, title, signal.value, action.value)
            if self._observe_session_page is not object and observation_key != self._last_observation:
                try:
                    self._observe_session_page(self.session_id, url, title, text)
                    self._last_observation = observation_key
                except Exception:  # noqa: BLE001
                    self._stop.set()
                    return
            if action == PageAction.WAIT_FOR_HUMAN or signal.value == "UNKNOWN":
                logger.info("session %s paused for human on %s", self.session_id, signal.value)
                self._stop.set()
                return
            if _is_safe_interstitial(url, signal.value) and _can_click_consent(page):
                _click_consent(page)


def _visible_text(page: "Page") -> str:
    try:
        return (page.locator("body").inner_text(timeout=1000)[:4000] if page.locator("body").count() else "")
    except Exception:  # noqa: BLE001
        return ""


def _is_safe_interstitial(url: str, signal: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return signal == "ORDINARY_INTERSTITIAL" and host in {"accounts.google.com", "consent.google.com"}


def _can_click_consent(page: "Page") -> bool:
    try:
        if page.locator("input[type=password], input[type=email]").count():
            return False
        text = _visible_text(page)
        return any(word in text.lower() for word in ("terms", "privacy", "consent", "welcome", "continue"))
    except Exception:  # noqa: BLE001
        return False


def _click_consent(page: "Page") -> None:
    try:
        for button in page.locator("button, [role=button], a").all():
            label = (button.inner_text() or "").strip().lower()
            if label in _SAFE_CONSENT_LABELS or any(word in label for word in ("i agree", "accept all")):
                button.click(timeout=1500)
                logger.info("advanced consent on %s", page.url)
                return
    except Exception:  # noqa: BLE001
        return
