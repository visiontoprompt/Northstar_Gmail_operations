"""Playwright observer attached to a worker-open Chrome via CDP.

The observer is a consent-only automation that may drive the sign-in form:

- reads the current page state (URL, title, visible text) and classifies it with
  login_policy.classify_page;
- advances *only* ordinary consent interstitials (I agree / Continue pages on
  accounts.* / consent.*) by clicking the match;
- when BROWSER_AUTO_LOGIN is enabled and the account has a stored encrypted
  secret, types the identifier and password into Google's own sign-in form
  (accounts.google.com / consent.google.com only) and submits each step;
- reports CAPTCHA / MFA / security / unknown pages by calling
  observe_session_page, which pauses the session for a human;
- keeps watching after a page pauses, so the mailbox is detected as soon as the
  human clears a challenge and Gmail loads.

It never solves CAPTCHAs, never submits recovery or 2FA codes, only types into
Google's own login host, never touches already-filled fields, and attempts each
page once so a rejected password cannot be retried in a loop. Every fill and
submit is recorded as an audit event. Fingerprint/stealth signals are never
manipulated and cookies are never cleared.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .login_policy import PageAction, PageObservation, classify_page

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page

logger = logging.getLogger(__name__)

_SAFE_CONSENT_LABELS = ("i agree", "accept all", "continue", "next", "got it", "ok")
_SUBMIT_LABELS = ("sign in", "log in", "continue", "next")
_LOGIN_HOSTS = {"accounts.google.com", "consent.google.com"}
_BLANK_URL_PREFIXES = ("about:", "chrome:", "devtools:")


class BrowserObserver:
    def __init__(
        self,
        session_id: str,
        profile_id: str,
        cdp_port: int,
        observe_session_page=object,
        resolve_credentials=None,
        report_event=None,
        interval: float = 1.0,
        settle_seconds: float = 5.0,
        connect_timeout: float = 12.0,
    ) -> None:
        self.session_id = session_id
        self.profile_id = profile_id
        self.cdp_port = cdp_port
        self._observe_session_page = observe_session_page
        self._resolve_credentials = resolve_credentials
        self._report_event = report_event
        self._interval = interval
        self._settle_seconds = settle_seconds
        self._connect_timeout = connect_timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_observation: tuple[str, str, str, str] | None = None
        self._unknown_since: float | None = None
        self._credentials: tuple[str, str] | None = None
        self._credentials_tried = False
        self._auto_attempted_urls: set[str] = set()

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

    def _emit(self, event: str, detail: str = "", result: str = "success") -> None:
        if self._report_event is None:
            return
        try:
            self._report_event(self.session_id, event, detail, result)
        except Exception:  # noqa: BLE001 - event reporting must never kill the observer
            logger.exception("observer event report failed for session %s", self.session_id)

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragmatic fallback: no observer, session is human hand-off
            logger.warning("playwright not installed; session %s is human hand-off", self.session_id)
            return
        with sync_playwright() as pw:
            browser = _connect_with_retry(pw, self.cdp_port, self.session_id, self._connect_timeout, self._stop)
            if browser is None:
                return
            logger.info("AUTOMATION ACTIVE: observer attached to session %s on CDP port %s", self.session_id, self.cdp_port)
            try:
                while not self._stop.is_set():
                    try:
                        self._tick(browser)
                    except Exception:  # noqa: BLE001 - a bad page must not kill the observer
                        logger.exception("observer tick failed for session %s", self.session_id)
                    self._stop.wait(self._interval)
            finally:
                # This Browser is attached to a Chrome process owned by
                # runtime.py. Calling browser.close() over CDP closes the
                # remote Chrome and breaks the user's dedicated session.
                # Exiting sync_playwright below disconnects the observer;
                # runtime.close_local_browser remains the only owner that
                # may terminate the Chrome process.
                logger.debug("observer disconnecting from session %s", self.session_id)

    def _tick(self, browser: "Browser") -> None:
        page = _active_page(browser)
        if page is None:
            return
        url = page.url or ""
        title = page.title() or ""
        text = _visible_text(page)
        signal, action = classify_page(PageObservation(url=url, title=title, text=text))

        if signal.value == "UNKNOWN":
            if not _page_has_settled(self, url, text):
                # The page engine is often still loading when the observer first
                # attaches. Judge it only after the settle window so opening the
                # browser never fails the session during a cold start.
                return
        else:
            self._unknown_since = None

        observation_key = (url, title, signal.value, action.value)
        if self._observe_session_page is not object and observation_key != self._last_observation:
            try:
                self._observe_session_page(self.session_id, url, title, text)
                self._last_observation = observation_key
                if signal.value == "LOGIN_FORM":
                    logger.info("session %s: login form detected on %s; awaiting manual sign-in in the opened window", self.session_id, url)
                elif signal.value == "UNKNOWN":
                    logger.warning("session %s: page stayed unknown on %s; paused for a human", self.session_id, url)
            except Exception:  # noqa: BLE001
                self._stop.set()
                return

        if signal.value == "LOGIN_FORM" and self._resolve_credentials is not None and _is_login_host(url) and url not in self._auto_attempted_urls:
            _fill_sign_in_form(page, self, url)

        if _is_safe_interstitial(url, signal.value) and _can_click_consent(page):
            _click_consent(page)


def _connect_with_retry(pw, cdp_port: int, session_id: str, timeout: float, stop: threading.Event):
    """Chrome may not have finished opening its debugging endpoint when the
    observer thread starts; keep retrying for the attach window."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline and not stop.is_set():
        try:
            logger.info("observer connecting to CDP on port %s for session %s", cdp_port, session_id)
            return pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.5)
    logger.warning("observer could not attach to session %s within %.0fs: %s", session_id, timeout, last_error)
    return None


def _active_page(browser: "Browser") -> "Page | None":
    for context in browser.contexts:
        for page in context.pages:
            url = page.url or ""
            if url and not url.startswith(_BLANK_URL_PREFIXES):
                return page
    for context in browser.contexts:
        if context.pages:
            return context.pages[0]
    return None


def _page_has_settled(observer: "BrowserObserver", url: str, text: str) -> bool:
    now = time.monotonic()
    if observer._unknown_since is None:
        observer._unknown_since = now
    if not url or url.startswith(_BLANK_URL_PREFIXES) or not text:
        # Engine is still mid-load; give it more time before judging.
        return False
    settled = now - observer._unknown_since >= observer._settle_seconds
    if not settled:
        logger.debug("session %s: page unresolved on %s; inside settle window", observer.session_id, url)
    return settled


def _resolve_credentials(observer: "BrowserObserver") -> tuple[str, str] | None:
    if not observer._credentials_tried:
        observer._credentials_tried = True
        if observer._resolve_credentials is not None:
            try:
                observer._credentials = observer._resolve_credentials(observer.session_id)
            except Exception:  # noqa: BLE001
                logger.exception("credential resolution failed for session %s", observer.session_id)
    return observer._credentials


def _fill_sign_in_form(page: "Page", observer: "BrowserObserver", url: str) -> None:
    """Enter the stored identifier/password into Google's own sign-in form and
    press the primary submit button. Attempting a page once prevents a rejected
    password from being retyped in a loop; fields that are already filled (for
    example after Google pre-fills on retry) are never overwritten."""
    credentials = _resolve_credentials(observer)
    if credentials is None:
        observer._auto_attempted_urls.add(url)
        observer._emit("skipped", "no stored credentials for account")
        return
    identifier, password = credentials
    try:
        password_field = page.locator("input[type=password], input[name=Passwd], #password")
        identifier_field = page.locator("input[type=email], input[name=identifier], input[name=Email], input[id=identifierId], input[name=identifier]")
        if password_field.count() and password_field.first.input_value(timeout=1000):
            observer._auto_attempted_urls.add(url)
            return
        if identifier_field.count() and identifier_field.first.input_value(timeout=1000):
            observer._auto_attempted_urls.add(url)
            return
        if password_field.count():
            password_field.first.fill(password, timeout=1500)
            observer._auto_attempted_urls.add(url)
            observer._emit("password_filled", url)
            _click_submit(page, url)
            return
        if identifier_field.count():
            identifier_field.first.fill(identifier, timeout=1500)
            observer._auto_attempted_urls.add(url)
            observer._emit("identifier_filled", url)
            _click_submit(page, url)
            return
    except Exception:  # noqa: BLE001
        observer._auto_attempted_urls.add(url)
        observer._emit("failed", f"interaction failed on {url}", result="failure")


def _click_submit(page: "Page", url: str) -> None:
    try:
        for button in page.locator("button, [role=button]").all():
            label = (button.inner_text() or "").strip().lower()
            if label in _SUBMIT_LABELS or any(word in label for word in _SUBMIT_LABELS):
                button.click(timeout=1500)
                logger.info("advanced sign-in step on %s", url)
                return
    except Exception:  # noqa: BLE001
        return


def _is_login_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host in _LOGIN_HOSTS


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