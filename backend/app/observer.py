"""Playwright observer that launches and drives the account's own browser.

The observer launches the Chromium-family binary with a persistent user-data
dir (the saved account profile), navigates it to Google sign-in, and drives the
flow:

- reads the current page state (URL, title, visible text) and classifies it with
  login_policy.classify_page;
- advances *only* ordinary consent interstitials (I agree / Continue pages on
  accounts.* / consent.*) by clicking the match;
- when BROWSER_AUTO_LOGIN is enabled and the account has a stored encrypted
  secret, types the identifier and password into Google's own sign-in form
  (accounts.google.com / consent.google.com only) and submits each step;
- when a 2FA secret is stored, generates the TOTP and enters it;
- reports CAPTCHA / MFA / security / unknown pages by calling
  observe_session_page, which pauses the session for a human;
- keeps watching after a page pauses, so the mailbox is detected as soon as the
  human clears a challenge and Gmail loads.

Launching via Playwright's persistent context keeps Chrome's own command line
free of --remote-debugging-port / --enable-automation, which is what makes
Google answer "this browser or app may not be secure" instead of loading the
form. The saved profile (cookies/state) stays on disk in the profile dir and is
reused by later sessions, so an account that has signed in once can be opened
again without re-authentication.

It never solves CAPTCHAs, never submits recovery or 2FA codes, only types into
Google's own login host, never touches already-filled fields, and attempts each
page once so a rejected password cannot be retried in a loop. Every fill and
submit is recorded as an audit event. No fingerprint/stealth signal beyond
disabling Chrome's own automation infobar is manipulated and cookies are never
cleared.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .config import get_settings
from .login_policy import PageAction, PageObservation, classify_page
from .runtime import resolve_browser_executable
from .security import generate_totp

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

_SAFE_CONSENT_LABELS = ("i agree", "accept all", "continue", "continuer", "continuar", "next", "got it", "ok", "i understand", "understand")
_SUBMIT_LABELS = ("sign in", "log in", "continue", "next")
_LOGIN_HOSTS = {"accounts.google.com", "consent.google.com"}
_BLANK_URL_PREFIXES = ("about:", "chrome:", "devtools:")
_OPERATION_FAILURE_EVENTS = {
    "contacts": "contacts.failed",
    "labels_markers": "labels_markers.failed",
    "security": "security.failed",
}
_CREDENTIAL_FIELD_SELECTOR = "input[type=email], input[name=identifier], input[id=identifierId], input[type=password], input[name=Passwd], input#password"
_OTP_FIELD_SELECTOR = "input[inputmode=numeric]:visible, input#totpPin:visible, input[name=totpPin]:visible, input[autocomplete=one-time-code]:visible, input[type=tel][autocomplete=one-time-code]:visible"


class BrowserObserver:
    def __init__(
        self,
        session_id: str,
        profile_id: str,
        cdp_port: int,
        profile_dir: str | None = None,
        executable: str | None = None,
        headless: bool = False,
        start_url: str | None = None,
        observe_session_page=object,
        resolve_credentials=None,
        resolve_otp_secret=None,
        resolve_proxy=None,
        report_event=None,
        on_browser_closed=None,
        interval: float = 1.0,
        settle_seconds: float = 5.0,
        connect_timeout: float = 12.0,
        purpose: str = "login",
        contacts_csv_path: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.profile_id = profile_id
        self.cdp_port = cdp_port  # kept for backward compatibility; unused
        self.profile_dir = profile_dir
        self.executable = executable
        self.headless = headless
        self.start_url = start_url or "https://accounts.google.com/"
        self._observe_session_page = observe_session_page
        self._resolve_credentials = resolve_credentials
        self._resolve_otp_secret = resolve_otp_secret
        self._resolve_proxy = resolve_proxy
        self._report_event = report_event
        self._on_browser_closed = on_browser_closed
        self._interval = interval
        self._settle_seconds = settle_seconds
        self._connect_timeout = connect_timeout
        self.purpose = purpose
        self.contacts_csv_path = contacts_csv_path
        self._contacts_imported = False
        self._contacts_uploaded = False
        self._contacts_confirm_clicked = False
        self._contacts_post_confirm_ticks = 0
        self._contacts_confirm_retries = 0
        self._contacts_open_clicked = False
        self._contacts_tick_count = 0
        self._contacts_upload_tick = 0
        self._markers_verified = False
        self._labels_verified = False
        self._labels_markers_tick_count = 0
        self._labels_markers_completion_ticks = 0
        self._security_verified = False
        self._security_clicked = False
        self._security_tick_count = 0
        self._security_completion_ticks = 0
        self._start_navigation_attempts = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_observation: tuple[str, str, str, str] | None = None
        self._unknown_since: float | None = None
        self._credentials: tuple[str, str] | None = None
        self._credentials_tried = False
        self._otp_secret: str | None = None
        self._otp_secret_tried = False
        self._auto_attempted_urls: set[str] = set()
        self._chooser_clicked_urls: set[str] = set()
        self._continue_clicked_urls: set[str] = set()
        self._submitted_urls: set[str] = set()
        self._fill_failed_urls: dict[str, int] = defaultdict(int)
        self._otp_attempted_urls: set[str] = set()
        self._otp_failed_urls: dict[str, int] = defaultdict(int)
        self._reported_no_page = False
        self._logged_states: set[tuple[str, str]] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"browser-observer-{self.session_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        # Account-not-found cleanup is triggered from the observer callback
        # itself. Never join the current thread; setting the stop event lets
        # the loop leave and the persistent context close in _run's finally.
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)

    def _emit(self, event: str, detail: str = "", result: str = "success") -> None:
        if self._report_event is None:
            return
        try:
            self._report_event(self.session_id, event, detail, result)
        except Exception:  # noqa: BLE001 - event reporting must never kill the observer
            logger.exception("observer event report failed for session %s", self.session_id)

    def _notify_browser_closed(self) -> None:
        if self._on_browser_closed is None:
            return
        try:
            self._on_browser_closed(self.session_id)
        except Exception:  # noqa: BLE001 - cleanup reporting must not mask shutdown
            logger.exception("browser-close cleanup failed for session %s", self.session_id)

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragmatic fallback: no observer, session is human hand-off
            logger.warning("playwright not installed; session %s is human hand-off", self.session_id)
            return
        with sync_playwright() as pw:
            context = _launch_profile_context(pw, self)
            if context is None:
                self._emit("failed", "could not launch the isolated browser", result="failure")
                self._notify_browser_closed()
                return
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._start_navigation_attempts = 1
                if not _open_start_page(page, self.start_url):
                    failure_event = _OPERATION_FAILURE_EVENTS.get(self.purpose)
                    if failure_event:
                        self._emit(
                            failure_event,
                            "The isolated browser could not load the operation start page; verify the assigned proxy permits Google",
                            result="failure",
                        )
                    self._stop.set()
                    return
                page_count = len(context.pages)
                logger.info("AUTOMATION ACTIVE: observer launched the isolated browser for session %s (%d page target(s))", self.session_id, page_count)
                try:
                    for target in context.pages:
                        logger.info("session %s: page target %s", self.session_id, _open_page_url(target))
                except Exception:  # noqa: BLE001 - report-only
                    logger.warning("session %s: page targets unreadable", self.session_id)
                try:
                    while not self._stop.is_set():
                        try:
                            self._tick(context)
                        except Exception:  # noqa: BLE001 - a bad page must not kill the observer
                            logger.exception("observer tick failed for session %s", self.session_id)
                        self._stop.wait(self._interval)
                finally:
                    logger.info("observer closing the isolated browser for session %s", self.session_id)
            finally:
                try:
                    # Closing the persistent context terminates the browser but
                    # keeps the user-data dir on disk, so the account's saved
                    # cookies/profile persist for the next no-login session.
                    context.close()
                except Exception:  # noqa: BLE001
                    logger.debug("observer context close failed for session %s", self.session_id, exc_info=True)
                finally:
                    # Cleanup runs only after Chromium releases its files.
                    # Removing an unsaved profile from the page callback lets
                    # the closing browser recreate a partial directory.
                    self._notify_browser_closed()

    def _tick(self, browser: "Browser | BrowserContext") -> None:
        page = _active_page(browser)
        if page is None:
            if not self._reported_no_page:
                logger.warning("observer found no open page for session %s; pausing for the browser window", self.session_id)
                self._reported_no_page = True
            return
        url = page.url or ""
        if not url or url.startswith(_BLANK_URL_PREFIXES):
            if self._start_navigation_attempts < 4:
                self._start_navigation_attempts += 1
                logger.info("session %s: retrying initial navigation attempt %d to %s", self.session_id, self._start_navigation_attempts, self.start_url)
                _open_start_page(page, self.start_url)
                return
            if self.purpose == "security":
                self._emit("security.failed", "The isolated browser could not navigate to Google Account security", result="failure")
                self._stop.set()
            return
        title = page.title() or ""
        text = _visible_text(page)
        signal, action = classify_page(PageObservation(url=url, title=title, text=text))
        host = (urlparse(url).hostname or "").lower().rstrip(".")

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
            except Exception:  # noqa: BLE001
                self._stop.set()
                return

        # The "Choose an account" page (remembered profile) carries no input
        # fields, so classify_page reports it as ORDINARY_INTERSTITIAL (the copy
        # contains "continue"). Drive consent, the chooser, and auto-fill from
        # what the page actually has on a Google login host, never off-host.
        if host in _LOGIN_HOSTS:
            if _has_credential_input(page):
                if self._resolve_credentials is not None and url not in self._auto_attempted_urls:
                    _fill_sign_in_form(page, self, url)
            elif url not in self._chooser_clicked_urls and _handle_account_chooser(page, self, url):
                self._chooser_clicked_urls.add(url)
            elif url not in self._continue_clicked_urls and _handle_google_account_continue(page, self, url):
                self._continue_clicked_urls.add(url)
            if _has_otp_input(page) and self._resolve_otp_secret is not None and url not in self._otp_attempted_urls:
                _fill_otp_code(page, self, url)

        if _is_safe_interstitial(url, signal.value) and _can_click_consent(page):
            _click_consent(page)

        if self.purpose == "contacts" and not self._contacts_imported:
            _handle_contacts_tick(self, page, host, signal)
        if self.purpose == "labels_markers":
            _handle_labels_markers_tick(self, page, host)
        if self.purpose == "security":
            _handle_security_tick(self, page, host)

        state_key = (signal.value, url)
        if state_key in self._logged_states:
            return
        if signal.value == "CAPTCHA":
            logger.warning("session %s: anti-bot wall on %s; a human must pass it", self.session_id, url)
        elif signal.value == "MFA_OR_SECURITY_CHECK" and not (host in _LOGIN_HOSTS and _has_otp_input(page)):
            logger.info("session %s: second-factor prompt on %s awaiting manual confirmation", self.session_id, url)
        elif host in _LOGIN_HOSTS and _has_otp_input(page):
            logger.info("session %s: one-time code step on %s; auto sign-in is entering the TOTP", self.session_id, url)
        elif signal.value == "GMAIL_HOME":
            logger.info("session %s: mailbox detected on %s", self.session_id, url)
        elif host in _LOGIN_HOSTS and _has_credential_input(page):
            logger.info("session %s: login form detected on %s; auto sign-in is entering the saved credentials", self.session_id, url)
        elif signal.value == "UNKNOWN":
            logger.warning("session %s: page stayed unknown on %s; paused for a human", self.session_id, url)
        elif signal.value == "ORDINARY_INTERSTITIAL":
            logger.info("session %s: interstitial (consent/chooser) on %s", self.session_id, url)
        self._logged_states.add(state_key)


def _launch_profile_context(pw, observer: "BrowserObserver"):
    """Launch the account's own saved profile as a normal-looking Chrome window.

    Using Playwright's persistent context keeps the command line free of
    --remote-debugging-port and --enable-automation, which Chrome otherwise
    shows to Google and which make sign-in answer "this browser or app may not
    be secure". The profile dir is the same folder the app always used, so any
    cookies saved by an earlier sign-in survive and are reused later.
    """
    profile_dir = observer.profile_dir
    if not profile_dir:
        profile_dir = str((Path(get_settings().profile_root) / observer.profile_id).resolve())
    profile_path = Path(profile_dir)
    try:
        profile_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("observer could not create profile dir %s for session %s", profile_dir, observer.session_id)
        return None
    executable = observer.executable or resolve_browser_executable()
    if not executable:
        logger.warning("no browser executable found for session %s (set BROWSER_EXECUTABLE or install Chrome/Chromium)", observer.session_id)
        return None
    active_port_file = profile_path / "DevToolsActivePort"
    try:
        active_port_file.unlink(missing_ok=True)
    except OSError:
        pass
    logger.info("session %s: launching the saved profile with %s", observer.session_id, executable)
    launch_kwargs = {}
    if observer._resolve_proxy is not None:
        try:
            proxy = observer._resolve_proxy(observer.session_id)
        except Exception:  # noqa: BLE001 - a missing proxy must not block login
            logger.exception("session %s: could not resolve its proxy", observer.session_id)
            proxy = None
        if proxy:
            launch_kwargs["proxy"] = proxy
    if not observer.headless:
        launch_kwargs["no_viewport"] = True
    # Playwright owns the initial page and requires navigation through the Page
    # API. Keep the normal Chrome window here so the account session retains
    # its address bar and in-window Google navigation; the observer performs
    # the explicit target navigation immediately after the context is ready.
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ]
    try:
        return pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            executable_path=executable,
            headless=observer.headless,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
            **launch_kwargs,
        )
    except Exception:  # noqa: BLE001
        logger.exception("observer failed to launch the browser for session %s", observer.session_id)
        return None


def _open_start_page(page: "Page", start_url: str) -> bool:
    """Make the fresh window land on Google sign-in unless it already has a
    usable target (e.g. a profile that already progressed to the form)."""
    try:
        current = page.url or ""
    except Exception:  # noqa: BLE001
        current = ""
    try:
        if not current or current.startswith(_BLANK_URL_PREFIXES) or (urlparse(current).hostname or "") not in _LOGIN_HOSTS:
            # Google pages can keep network connections open through a proxy;
            # waiting for the document commit gives the observer a usable URL
            # without requiring every subresource to finish first.
            page.goto(start_url, wait_until="commit", timeout=15000)
            return True
        return True
    except Exception:  # noqa: BLE001 - a load failure must not kill the observer
        try:
            after_failure = page.url or ""
        except Exception:  # noqa: BLE001
            after_failure = ""
        logger.warning("observer could not open %s; current page=%s", start_url, after_failure or "about:blank", exc_info=True)
        return bool(after_failure and not after_failure.startswith(_BLANK_URL_PREFIXES))


def _open_page_url(page: "Page") -> str:
    try:
        return page.url or ""
    except Exception:  # noqa: BLE001 - a destroyed tab must not abort the observer
        return ""


def _page_rank(url: str) -> int:
    if not url:
        return 3
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host in _LOGIN_HOSTS:
        return 0
    if host == "mail.google.com" or url.startswith(_BLANK_URL_PREFIXES):
        return 2
    return 1


def _active_page(owner: "Browser | BrowserContext") -> "Page | None":
    if hasattr(owner, "contexts"):
        candidates = [page for context in owner.contexts for page in context.pages]
    else:
        candidates = list(getattr(owner, "pages", None) or [])
    if not candidates:
        return None
    # Prefer the sign-in tab: a remembered profile can leave a blank/first-run
    # window in another tab, and the automation must latch onto accounts.google.
    ranked = sorted(candidates, key=lambda page: _page_rank(_open_page_url(page)))
    for page in ranked:
        if _open_page_url(page):
            return page
    return ranked[0]


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
    submit each step. Only *visible* fields are touched (Google duplicates some
    inputs in the DOM). A transient failure mid-navigation is retried on the
    next tick instead of silently aborting the whole sign-in; a persistent one
    is reported and the URL is attempted once and then paused."""
    credentials = _resolve_credentials(observer)
    if credentials is None:
        observer._auto_attempted_urls.add(url)
        observer._emit("skipped", "no stored credentials for account")
        return
    identifier, password = credentials
    password_selector = "input[type=password]:visible, input[name=Passwd]:visible, input#password:visible"
    identifier_selector = "input[type=email]:visible, input[name=identifier]:visible, input[name=Email]:visible, input[id=identifierId]:visible"
    failures = observer._fill_failed_urls.get(url, 0)
    try:
        password_field = page.locator(password_selector)
        identifier_field = page.locator(identifier_selector)
        if password_field.count() and _input_value(password_field.first):
            if url not in observer._submitted_urls:
                _click_submit(page, url)
                observer._submitted_urls.add(url)
            return
        if identifier_field.count() and _input_value(identifier_field.first):
            if url not in observer._submitted_urls:
                _click_submit(page, url)
                observer._submitted_urls.add(url)
            return
        if password_field.count():
            password_field.first.fill(password, timeout=2000)
            observer._emit("password_filled", url)
            _click_submit(page, url)
            observer._submitted_urls.add(url)
            observer._auto_attempted_urls.add(url)
            return
        if identifier_field.count():
            identifier_field.first.fill(identifier, timeout=2000)
            observer._emit("identifier_filled", url)
            _click_submit(page, url)
            observer._submitted_urls.add(url)
            observer._auto_attempted_urls.add(url)
            return
    except Exception as exc:  # noqa: BLE001
        observer._fill_failed_urls[url] = failures + 1
        logger.exception("auto sign-in attempt failed for session %s on %s (will retry)", observer.session_id, url)
        if failures + 1 >= 3:
            observer._auto_attempted_urls.add(url)
            observer._emit("failed", f"persistent sign-in interaction failure on {url}: {exc}", result="failure")


def _has_credential_input(page: "Page") -> bool:
    """The identifier/password inputs are the one deterministic signal on a
    Google login page: placeholder text such as "Email or phone" is not exposed
    to innerText, so text heuristics alone can misclassify the page."""
    try:
        return page.locator(_CREDENTIAL_FIELD_SELECTOR).count() > 0
    except Exception:  # noqa: BLE001
        return False


def _has_otp_input(page: "Page") -> bool:
    try:
        return page.locator(_OTP_FIELD_SELECTOR).count() > 0
    except Exception:  # noqa: BLE001
        return False


def _resolve_otp_secret(observer: "BrowserObserver") -> str | None:
    if not observer._otp_secret_tried:
        observer._otp_secret_tried = True
        if observer._resolve_otp_secret is not None:
            try:
                observer._otp_secret = observer._resolve_otp_secret(observer.session_id)
            except Exception:  # noqa: BLE001
                logger.exception("OTP secret resolution failed for session %s", observer.session_id)
    return observer._otp_secret


def _fill_otp_code(page: "Page", observer: "BrowserObserver", url: str) -> None:
    """Generate a TOTP from the stored Authenticator secret and enter it into
    Google's code field. A transient failure is retried on the next tick; a
    persistent one is reported and the step is paused for the operator."""
    secret = _resolve_otp_secret(observer)
    if not secret:
        observer._otp_attempted_urls.add(url)
        observer._emit("skipped", "no stored 2FA secret for account")
        return
    failures = observer._otp_failed_urls.get(url, 0)
    try:
        code = generate_totp(secret)
    except Exception as exc:  # noqa: BLE001
        observer._otp_attempted_urls.add(url)
        observer._emit("failed", f"could not generate TOTP on {url}: {exc}", result="failure")
        return
    try:
        otp_field = page.locator(_OTP_FIELD_SELECTOR)
        if otp_field.count() and not _input_value(otp_field.first):
            otp_field.first.fill(code, timeout=2000)
            observer._emit("otp_filled", url)
            _click_submit(page, url)
            observer._otp_attempted_urls.add(url)
        elif otp_field.count():
            observer._otp_attempted_urls.add(url)
    except Exception as exc:  # noqa: BLE001
        observer._otp_failed_urls[url] = failures + 1
        logger.exception("OTP entry failed for session %s on %s (will retry)", observer.session_id, url)
        if failures + 1 >= 3:
            observer._otp_attempted_urls.add(url)
            observer._emit("failed", f"persistent OTP entry failure on {url}: {exc}", result="failure")


def _handle_account_chooser(page: "Page", observer: "BrowserObserver", url: str) -> bool:
    """The dedicated profile may already remember an account, so Google shows a
    "Choose an account" page without any input field. Expand it by clicking
    "Use another account"; the identifier form then appears on the same URL."""
    if _has_credential_input(page):
        return False
    clicked = _click_element_with_text(page, ("use another account",))
    if clicked:
        observer._emit("chooser_expanded", url)
    return clicked


_CONTINUE_HANDOFF_LABELS = ("continue", "continuer", "continuar")


def _handle_google_account_continue(page: "Page", observer: "BrowserObserver", url: str) -> bool:
    """Advance Google's post-login account hand-off page.

    Google may retain hidden sign-in inputs after authentication and show a
    visible button such as ``Continue`` / ``Continuer`` / ``Continuar`` before
    handing the profile to Gmail. This is restricted to Google's login hosts,
    requires no visible credential or OTP field, and only clicks an exact
    Continue-labelled control.
    """
    if _has_credential_input(page) or _has_otp_input(page):
        return False
    try:
        body_text = _visible_text(page).lower()
    except Exception:  # noqa: BLE001
        return False
    if not any(label in body_text for label in _CONTINUE_HANDOFF_LABELS):
        return False
    try:
        for candidate in page.locator("button, [role=button], a").all():
            try:
                label = (candidate.inner_text() or "").strip().lower()
            except Exception:  # noqa: BLE001
                continue
            if label in _CONTINUE_HANDOFF_LABELS:
                try:
                    candidate.click(timeout=1500)
                    observer._emit("account_continue_clicked", url)
                    logger.info("advanced Google account hand-off on %s", url)
                    return True
                except Exception:  # noqa: BLE001 - hidden/disabled; keep scanning
                    continue
    except Exception:  # noqa: BLE001
        return False
    return False


def _click_element_with_text(page: "Page", labels: tuple[str, ...]) -> bool:
    wanted = tuple(label.strip().lower() for label in labels)
    try:
        for candidate in page.locator("button, [role=button], a, li").all():
            try:
                label = (candidate.inner_text() or "").strip().lower()
            except Exception:  # noqa: BLE001
                continue
            if any(w in label for w in wanted):
                try:
                    candidate.click(timeout=1500)
                    return True
                except Exception:  # noqa: BLE001 - hidden/disabled; keep scanning
                    continue
    except Exception:  # noqa: BLE001
        return False
    return False


def _click_submit(page: "Page", url: str) -> None:
    try:
        for button in page.locator("button, [role=button], input[type=submit]").all():
            label = (button.inner_text() or "").strip().lower()
            if label in _SUBMIT_LABELS or any(word in label for word in _SUBMIT_LABELS):
                button.click(timeout=1500)
                logger.info("advanced sign-in step on %s", url)
                return
    except Exception:  # noqa: BLE001
        pass
    # Fallback used by the Google flow when the step has no labelled button:
    # the identifier/password field is focused after typing, so Enter submits.
    if not hasattr(page, "keyboard"):
        return
    try:
        page.keyboard.press("Enter")
        logger.info("signed in step on %s with keyboard Enter", url)
    except Exception:  # noqa: BLE001
        return


def _visible_text(page: "Page") -> str:
    try:
        return (page.locator("body").inner_text(timeout=1000)[:4000] if page.locator("body").count() else "")
    except Exception:  # noqa: BLE001
        return ""


def _input_value(field) -> str:
    """Read a field's current value without aborting the sign-in step.

    The selector may match a wrapper element carrying a credential-looking id
    (Google wraps the password field in a div#password), which is not an
    <input> and makes Locator.input_value raise. Such a node simply counts as
    empty so the flow falls through to the real input and fills it."""
    try:
        return field.input_value(timeout=1000)
    except Exception:  # noqa: BLE001
        return ""


def _is_safe_interstitial(url: str, signal: str) -> bool:
    if signal != "ORDINARY_INTERSTITIAL":
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"accounts.google.com", "consent.google.com"}:
        return True
    # Google Workspace terms-of-service / signin speedbump pages.
    if host == "workspace.google.com" and "termsofservice" in (parsed.path or "").lower():
        return True
    return False


def _can_click_consent(page: "Page") -> bool:
    try:
        if _has_credential_input(page) or _has_otp_input(page):
            return False
        text = _visible_text(page)
        return any(word in text.lower() for word in ("terms", "privacy", "consent", "welcome", "understand", "continue"))
    except Exception:  # noqa: BLE001
        return False


def _click_consent(page: "Page") -> None:
    try:
        for button in page.locator("button, [role=button], a").all():
            try:
                label = (button.inner_text() or "").strip().lower()
            except Exception:  # noqa: BLE001
                continue
            if label in _SAFE_CONSENT_LABELS or any(word in label for word in ("i agree", "accept all", "i understand", "understand", "continuer", "continuar")):
                try:
                    button.click(timeout=1500)
                    logger.info("advanced consent on %s", page.url)
                    return
                except Exception:  # noqa: BLE001 - hidden/disabled button; keep scanning
                    continue
    except Exception:  # noqa: BLE001
        return


_CONTACTS_IMPORT_LABELS = (
    "import contacts",
    "import",
    "importer les contacts",
    "importer des contacts",
    "importer",
    "importar contactos",
    "importar contatos",
    "kontakte importieren",
    "importieren",
    "импорт контактов",
    "импорт",
)
_CONTACTS_FILE_BUTTON_LABELS = (
    "select a file",
    "select file",
    "choose file",
    "choose a file",
    "browse",
    "sélectionnez un fichier",
    "choisir un fichier",
    "parcourir",
    "seleccionar un archivo",
    "seleccionar archivo",
    "elegir un archivo",
    "examinar",
)
_CONTACTS_CONFIRM_LABELS = (
    "import",
    "import now",
    "import contacts",
    "importar",
    "importar ahora",
    "importar contactos",
    "upload",
    "subir",
    "open",
    "abrir",
    "ok",
    "accept",
    "aceptar",
    "confirm",
    "confirmar",
    "importer",
    "ouvrir",
    "importer les contacts",
)
_CONTACTS_SETTINGS_LABELS = (
    "settings and help",
    "settings",
    "paramètres",
    "ajustes",
    "configuración",
    "einstellungen",
)
_CONTACTS_DONE_MARKERS = (
    "contacts imported",
    "contact imported",
    "imported successfully",
    "successfully imported",
    "import complete",
    "import terminée",
    "importation terminée",
    "importation réussie",
    "contacts importés",
    "contacts importés avec succès",
    "import terminé",
    "contactos importados",
    "contacto importado",
    "importado correctamente",
    "importados correctamente",
    "importación completa",
    "importación completada",
    "importación finalizada",
    "se importaron",
    "your import is complete",
    "the import is complete",
    "votre importation est terminée",
    "le processus est terminé",
    "processus terminé",
    "a été importé",
    "importé avec succès",
    "the process is complete",
    "the process has finished",
    "process completed",
    "el proceso ha terminado",
    "el proceso se completó",
)
_CONTACTS_MAX_TICKS = 90  # ~90 x 1.25s ≈ 2 minutes until the flow gives up
_CONTACTS_FINAL_IMPORT_SELECTOR = (
    "#yDmH0d > div.uW2Fw-Sx9Kwc.uW2Fw-Sx9Kwc-OWXEXe-n2to0e.uW2Fw-qON5Qe-FoKg4d-Sx9Kwc-fZiSAe.yaahMe.GaqAuc."
    "uW2Fw-Sx9Kwc-OWXEXe-FNFY6c > div.uW2Fw-wzTsW > div > div.uW2Fw-T0kwCb > div:nth-child(2) > button"
)

_GMAIL_MARKERS_SETTINGS_URL = "https://mail.google.com/mail/u/0/?tab=rm&ogbl#settings/inbox"
_GMAIL_LABELS_SETTINGS_URL = "https://mail.google.com/mail/u/0/#settings/labels"
_GOOGLE_ENHANCED_SAFE_BROWSING_URL = "https://myaccount.google.com/account-enhanced-safe-browsing"
_SAFE_BROWSING_PAGE_PATH = "/account-enhanced-safe-browsing"
_SAFE_BROWSING_TERMS = (
    "enhanced safe browsing",
    "use enhanced safe browsing",
    "enhanced protection",
    "navigation sécurisée améliorée",
    "navigation securisee amelioree",
    "protection renforcée",
    "protection renforcee",
    "navegación segura mejorada",
    "navegacion segura mejorada",
    "protección mejorada",
    "proteccion mejorada",
)
_SAFE_BROWSING_ACTIVATE_LABELS = (
    "turn on",
    "enable",
    "activate",
    "keep on",
    "keep it on",
    "activer",
    "garder activé",
    "maintenir activé",
    "activar",
    "mantener activado",
    "habilitar",
)
_GMAIL_MARKER_SECTION_LABELS = (
    "importance markers",
    "marqueurs d'importance",
    "marqueurs d’importance",
    "marcadores de importancia",
)
_GMAIL_SHOW_MARKER_LABELS = (
    "show markers",
    "afficher les marqueurs",
    "mostrar marcadores",
)
_GMAIL_NO_MARKER_LABELS = ("no markers", "aucun marqueur", "sin marcadores")
_GMAIL_SYSTEM_LABEL_SECTION_LABELS = (
    "system labels",
    "libellés système",
    "etiquetas del sistema",
)
_GMAIL_SHOW_LABELS = ("show", "afficher", "mostrar")
_GMAIL_HIDE_LABELS = ("hide", "masquer", "ocultar")
_GMAIL_SAVE_LABELS = (
    "save changes",
    "enregistrer les modifications",
    "guardar cambios",
)


def _handle_contacts_tick(observer: "BrowserObserver", page: "Page", host: str, signal) -> None:
    """Drive the Google Contacts import inside a contacts-purpose session.

    Flow: click the import entry point, upload the rendered CSV through the
    dialog's file input, then wait for the success copy before stopping the
    observer. Any stop releases the browser so the session closes cleanly.
    """
    observer._contacts_tick_count += 1
    if observer._contacts_tick_count > _CONTACTS_MAX_TICKS:
        observer._emit("contacts.failed", "import flow timed out before completion", result="failure")
        observer._stop.set()
        return
    if host not in {"contacts.google.com"}:
        return
    if not observer._contacts_uploaded:
        _contacts_advance_file_flow(observer, page)
        return
    # Two-step dialogs stay open after the file is picked and need an explicit
    # "Import" confirmation; a single-attempt click covers that without looping.
    if not observer._contacts_confirm_clicked and observer._contacts_tick_count - observer._contacts_upload_tick >= 2:
        if _contacts_click_final_import(page) or _contacts_click_label(page, _CONTACTS_CONFIRM_LABELS):
            observer._contacts_confirm_clicked = True
            observer._emit("contacts.confirmed", "confirmed the contacts import dialog")
    if observer._contacts_confirm_clicked:
        observer._contacts_post_confirm_ticks += 1
        if observer._contacts_post_confirm_ticks in {2, 5, 10, 20}:
            logger.info(
                "session %s: contacts import settle tick=%s dialog_visible=%s final_text_visible=%s done_marker_visible=%s",
                observer.session_id,
                observer._contacts_post_confirm_ticks,
                _contacts_import_dialog_visible(page),
                _contacts_has_visible_exact_text(page, ("import", "importar", "importer")),
                _visible_text_mentions_done(page),
            )
        # A Google dialog can re-render the action node immediately after the
        # file upload. Retry the exact visible action text a small number of
        # times if it is still present; this remains bounded and does not
        # submit any other control.
        if observer._contacts_post_confirm_ticks in {5, 10, 20} and observer._contacts_confirm_retries < 3:
            if _contacts_click_final_import(page) or _contacts_click_label(page, ("import", "importar", "importer")):
                observer._contacts_confirm_retries += 1
                observer._emit("contacts.confirm_retry", "retried the exact contacts import action text")
    if (
        signal.value == "CONTACTS_IMPORT_DONE"
        or _visible_text_mentions_done(page)
        or _contacts_dialog_closed_after_confirm(observer, page)
        or _contacts_import_result_route(observer, page)
    ):
        observer._contacts_imported = True
        observer._emit("contacts.imported", f"Google Contacts import completed for {observer.contacts_csv_path or 'uploaded file'}")
        observer._stop.set()


def _handle_labels_markers_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Enable importance markers and show Gmail's Spam system label.

    Gmail settings are rendered as dynamic tables and vary by locale. The
    observer uses the visible section/option text and the semantic row
    relationship instead of brittle coordinates. Changes are saved before
    moving to the next settings page, and success is reported only after both
    settings are verified in the authenticated profile.
    """
    observer._labels_markers_tick_count = getattr(observer, "_labels_markers_tick_count", 0) + 1
    if observer._labels_markers_tick_count > 120:
        observer._emit("labels_markers.failed", "Gmail settings did not reach a verified completion state", result="failure")
        observer._stop.set()
        return
    if host != "mail.google.com":
        return
    url = page.url or ""
    fragment = urlparse(url).fragment.lower()
    if not getattr(observer, "_markers_verified", False):
        if "settings/inbox" not in fragment:
            _navigate_settings(page, _GMAIL_MARKERS_SETTINGS_URL)
            return
        marker_state = _set_importance_markers(page)
        if marker_state in {"clicked", "already"}:
            observer._markers_verified = True
            observer._emit("labels_markers.markers_enabled", "verified Importance markers: Show markers")
            _gmail_click_save_changes(page)
        return
    if not getattr(observer, "_labels_verified", False):
        if "settings/labels" not in fragment:
            _navigate_settings(page, _GMAIL_LABELS_SETTINGS_URL)
            return
        label_state = _show_spam_system_label(page)
        if label_state in {"clicked", "already"}:
            observer._labels_verified = True
            observer._emit("labels_markers.spam_shown", "verified Spam is visible in System labels")
            _gmail_click_save_changes(page)
        return
    if getattr(observer, "_labels_markers_completion_ticks", 0) < 2:
        observer._labels_markers_completion_ticks = getattr(observer, "_labels_markers_completion_ticks", 0) + 1
        return
    observer._emit("labels_markers.completed", "Gmail Importance markers enabled and Spam shown")
    observer._stop.set()


def _handle_security_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Enable and verify Enhanced Safe Browsing in the signed-in profile.

    The My Account surface is a client-rendered page whose DOM changes across
    locales and releases. The helper below finds a semantic switch/checkbox
    in the container labelled for Enhanced Safe Browsing, rather than relying
    on a generated CSS path or a screen coordinate.
    """
    observer._security_tick_count = getattr(observer, "_security_tick_count", 0) + 1
    if observer._security_tick_count > 120:
        observer._emit("security.failed", "Enhanced Safe Browsing did not reach a verified enabled state", result="failure")
        observer._stop.set()
        return
    if host != "myaccount.google.com":
        return
    url = page.url or ""
    if _SAFE_BROWSING_PAGE_PATH not in urlparse(url).path.lower():
        _navigate_settings(page, _GOOGLE_ENHANCED_SAFE_BROWSING_URL)
        return
    confirmation = _confirm_enhanced_safe_browsing(page)
    if confirmation:
        observer._emit("security.activation_confirmed", f"confirmed Enhanced Safe Browsing with {confirmation}")
        return
    state = _set_enhanced_safe_browsing(page)
    if state == "clicked":
        observer._security_clicked = True
        observer._emit("security.toggle_clicked", "selected Enhanced Safe Browsing")
        return
    if state == "already":
        observer._security_verified = True
    if not observer._security_verified:
        return
    if getattr(observer, "_security_completion_ticks", 0) < 2:
        observer._security_completion_ticks = getattr(observer, "_security_completion_ticks", 0) + 1
        return
    observer._emit("security.completed", "Enhanced Safe Browsing enabled and verified")
    observer._stop.set()


def _confirm_enhanced_safe_browsing(page: "Page") -> str | None:
    """Click the confirmation dialog's localized activation action.

    Clicking the first toggle can open a second Google Account dialog. Its
    action is commonly ``Activate`` / ``Activer`` / ``Activar``. Search only
    visible controls inside a modal and require Enhanced Safe Browsing context
    when the dialog exposes it; this avoids pressing similarly named controls
    elsewhere on the account page.
    """
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const terms = config.terms.map(normalize);
                const labels = config.labels.map(normalize);
                const visible = node => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const textOf = node => normalize([
                    node?.innerText,
                    node?.textContent,
                    node?.getAttribute?.('aria-label'),
                    node?.getAttribute?.('title'),
                ].filter(Boolean).join(' '));
                const modals = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"], [aria-modal="true"]')]
                    .filter(visible)
                    .sort((a, b) => textOf(b).length - textOf(a).length);
                for (const modal of modals) {
                    const modalText = textOf(modal);
                    const hasSafeBrowsingContext = terms.some(term => modalText.includes(term));
                    const controls = [...modal.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]')]
                        .filter(visible);
                    for (const control of controls) {
                        const label = textOf(control);
                        const matches = labels.some(value => label === value || label.startsWith(value + ' '));
                        if (matches && (hasSafeBrowsingContext || labels.includes(label))) {
                            control.click();
                            return label;
                        }
                    }
                }
                return null;
            }""",
            {"terms": _SAFE_BROWSING_TERMS, "labels": _SAFE_BROWSING_ACTIVATE_LABELS},
        )
        if not isinstance(result, str):
            return None
        normalized = result.strip().lower()
        if normalized in _SAFE_BROWSING_ACTIVATE_LABELS or any(normalized.startswith(f"{label} ") for label in _SAFE_BROWSING_ACTIVATE_LABELS):
            return normalized
        return None
    except Exception:  # noqa: BLE001 - the next observer tick retries after a re-render
        return None


def _set_enhanced_safe_browsing(page: "Page") -> str | None:
    """Return ``clicked`` or ``already`` for the localized security control."""
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const terms = config.terms.map(normalize);
                const action = ['turn on', 'enable', 'activate', 'keep on', 'keep it on', 'activer', 'garder active', 'maintenir active', 'activa', 'activar', 'mantener activado', 'habilitar'];
                const controls = [...document.querySelectorAll('input[type="checkbox"], [role="switch"], [role="checkbox"], button')];
                const textOf = node => normalize([node?.innerText, node?.textContent, node?.getAttribute?.('aria-label'), node?.getAttribute?.('title')].filter(Boolean).join(' '));
                const hasTerm = value => terms.some(term => value.includes(term));
                const checked = node => node.checked === true || node.getAttribute('aria-checked') === 'true' || node.getAttribute('aria-pressed') === 'true' || /\\b(on|enabled|active|selected|checked)\\b/i.test(node.className || '');
                for (const control of controls) {
                    let scope = control;
                    let scopeText = '';
                    for (let depth = 0; scope && depth < 5; depth += 1, scope = scope.parentElement) {
                        const candidate = textOf(scope);
                        if (candidate.length > scopeText.length && candidate.length < 1200) scopeText = candidate;
                        if (hasTerm(candidate)) break;
                    }
                    const ownText = textOf(control);
                    const label = control.labels?.[0] ? textOf(control.labels[0]) : '';
                    const context = [ownText, label, scopeText].join(' ');
                    if (!hasTerm(context)) continue;
                    if (checked(control)) return 'already';
                    const actionText = context;
                    if (control.matches('button') && !action.some(term => actionText.includes(term)) && !control.getAttribute('aria-checked')) continue;
                    control.click();
                    return 'clicked';
                }
                const body = normalize(document.body?.innerText);
                const enabledPattern = new RegExp('(?:' + terms.join('|') + ').{0,100}(?:on|enabled|active|turned on|activee|activado|activada|active)', 'i');
                if (enabledPattern.test(body)) return 'already';
                return null;
            }""",
            {"terms": _SAFE_BROWSING_TERMS},
        )
        return result if result in {"clicked", "already"} else None
    except Exception:  # noqa: BLE001 - the next observer tick retries after a re-render
        return None


def _navigate_settings(page: "Page", url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:  # noqa: BLE001 - the next observer tick retries navigation
        logger.debug("Gmail settings navigation is still loading", exc_info=True)


def _set_importance_markers(page: "Page") -> str | None:
    """Select the localized Show markers radio, or confirm it is selected."""
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const sectionTerms = config.section;
                const showTerms = config.show;
                const noTerms = config.no;
                const radioNodes = [...document.querySelectorAll('input[type="radio"], [role="radio"]')];
                for (const node of radioNodes) {
                    const option = node.closest('label, tr, [role="radio"], [role="row"]') || node.parentElement;
                    const optionText = normalize(option?.innerText || node.getAttribute('aria-label'));
                    if (!showTerms.some(term => optionText.includes(term))) continue;
                    const scope = option?.closest('tr, [role="row"], [role="group"]') || option?.parentElement;
                    const scopeText = normalize(scope?.innerText || optionText);
                    if (!sectionTerms.some(term => scopeText.includes(term)) && !sectionTerms.some(term => document.body.innerText.toLowerCase().includes(term))) continue;
                    if (noTerms.some(term => optionText.includes(term))) continue;
                    const checked = node.checked === true || node.getAttribute('aria-checked') === 'true';
                    if (checked) return 'already';
                    node.click();
                    return 'clicked';
                }
                return null;
            }""",
            {"section": _GMAIL_MARKER_SECTION_LABELS, "show": _GMAIL_SHOW_MARKER_LABELS, "no": _GMAIL_NO_MARKER_LABELS},
        )
        return result if result in {"clicked", "already"} else None
    except Exception:  # noqa: BLE001 - fallback scans visible controls below
        return _gmail_click_option_text(page, _GMAIL_SHOW_MARKER_LABELS, _GMAIL_NO_MARKER_LABELS)


def _show_spam_system_label(page: "Page") -> str | None:
    """Show Spam in the System labels row in any supported locale."""
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const showTerms = config.show;
                const hideTerms = config.hide;
                const spamNodes = [...document.querySelectorAll('td, [role="cell"], span, div')].filter(node => normalize(node.innerText || node.textContent) === 'spam');
                for (const spam of spamNodes) {
                    const row = spam.closest('tr, [role="row"]');
                    if (!row) continue;
                    const controls = [...row.querySelectorAll('a, button, [role="button"], [role="link"]')];
                    const show = controls.find(control => showTerms.includes(normalize(control.innerText || control.getAttribute('aria-label'))));
                    if (show) { show.click(); return 'clicked'; }
                    if (controls.some(control => hideTerms.includes(normalize(control.innerText || control.getAttribute('aria-label'))))) return 'already';
                }
                return null;
            }""",
            {"show": _GMAIL_SHOW_LABELS, "hide": _GMAIL_HIDE_LABELS},
        )
        return result if result in {"clicked", "already"} else None
    except Exception:  # noqa: BLE001 - fallback scans visible row controls below
        return _gmail_click_spam_row_fallback(page)


def _gmail_click_option_text(page: "Page", wanted: tuple[str, ...], excluded: tuple[str, ...] = ()) -> str | None:
    try:
        for candidate in page.locator('label, [role="radio"], input[type="radio"]').all():
            text = (candidate.inner_text() or candidate.get_attribute("aria-label") or "").strip().lower()
            if any(term in text for term in wanted) and not any(term in text for term in excluded):
                candidate.click(timeout=1500)
                return "clicked"
    except Exception:  # noqa: BLE001
        return None
    return None


def _gmail_click_spam_row_fallback(page: "Page") -> str | None:
    try:
        for row in page.locator('tr, [role="row"]').all():
            row_text = (row.inner_text() or "").strip().lower()
            if re.search(r"\bspam\b", row_text):
                controls = row.locator('a, button, [role="button"], [role="link"]').all()
                for control in controls:
                    text = (control.inner_text() or control.get_attribute("aria-label") or "").strip().lower()
                    if text in _GMAIL_SHOW_LABELS:
                        control.click(timeout=1500)
                        return "clicked"
                    if text in _GMAIL_HIDE_LABELS:
                        return "already"
    except Exception:  # noqa: BLE001
        return None
    return None


def _gmail_click_save_changes(page: "Page") -> bool:
    try:
        return _contacts_click_label(page, _GMAIL_SAVE_LABELS)
    except Exception:  # noqa: BLE001 - settings may auto-save in some builds
        return False


def _contacts_advance_file_flow(observer: "BrowserObserver", page: "Page") -> None:
    """Progress the import toward the file upload: open the importer when it
    is closed, then pick a file when the importer is asking for one."""
    if not observer._contacts_open_clicked:
        if _contacts_click_label(page, _CONTACTS_IMPORT_LABELS):
            observer._contacts_open_clicked = True
            observer._emit("contacts.dialog_opened", "contacts import dialog opened")
            return
        if observer._contacts_tick_count >= 4:
            # The import entry can sit under the settings menu in recent UIs;
            # open settings once, then retry the import label next tick.
            if _contacts_click_label(page, _CONTACTS_SETTINGS_LABELS):
                observer._emit("contacts.settings_opened", "opened contacts settings menu")
        return
    if not observer.contacts_csv_path or not Path(observer.contacts_csv_path).exists():
        observer._emit("contacts.failed", "upload CSV file is missing from disk", result="failure")
        observer._stop.set()
        return
    try:
        file_input = page.locator("input[type=file]").first
        if file_input.count():
            observer._contacts_uploaded = True
            observer._contacts_upload_tick = observer._contacts_tick_count
            file_input.set_input_files(observer.contacts_csv_path)
            observer._emit("contacts.file_uploaded", "uploaded contacts CSV for import")
            return
    except Exception:  # noqa: BLE001
        pass
    _contacts_click_label(page, _CONTACTS_FILE_BUTTON_LABELS)


def _contacts_click_final_import(page: "Page") -> bool:
    """Click the exact final Google Contacts import button when available.

    The selector is intentionally executed through the page's own
    ``document.querySelector(...).click()`` path because this is the control
    Google renders for the final action. A locator click remains a compatibility
    fallback for test doubles and alternate Playwright page implementations.
    """
    try:
        clicked = page.evaluate(
            """selector => {
                const button = document.querySelector(selector);
                if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') {
                    return false;
                }
                button.click();
                return true;
            }""",
            _CONTACTS_FINAL_IMPORT_SELECTOR,
        )
        if clicked:
            return True
    except Exception:  # noqa: BLE001 - use locator fallback below
        pass
    try:
        candidate = page.locator(_CONTACTS_FINAL_IMPORT_SELECTOR).first
        if candidate.count() and candidate.is_visible():
            candidate.click(timeout=1500)
            return True
    except Exception:  # noqa: BLE001 - use localized text fallback below
        return False
    return False


def _contacts_click_label(page: "Page", labels: tuple[str, ...]) -> bool:
    """Click the visible localized label itself, then use an element fallback.

    Google renders the final Contacts action as a text label inside a
    clickable control. Targeting the exact text first avoids accidentally
    selecting the dialog title (for example, ``Importar contactos`` when the
    action is ``Importar``). The semantic-control scan remains a fallback for
    builds where the label is not exposed as a separate text node.
    """
    wanted = tuple(dict.fromkeys(label.strip() for label in labels if label.strip()))

    # Prefer the literal visible action text. The regex keeps matching
    # case-insensitive while retaining exact text boundaries and whitespace
    # normalization, and the clickable-ancestor check excludes dialog titles.
    try:
        get_by_text = getattr(page, "get_by_text")
        for label in wanted:
            text_locator = get_by_text(re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE))
            for candidate in text_locator.all():
                try:
                    if not candidate.is_visible():
                        continue
                    is_clickable = candidate.evaluate(
                        """el => Boolean(el.closest(
                            'button, a, [role="button"], [role="menuitem"],
                            [role="tab"], input[type="button"], input[type="submit"],
                            [tabindex]:not([tabindex="-1"])
                        ))"""
                    )
                    if not is_clickable:
                        continue
                    candidate.click(timeout=1500)
                    return True
                except Exception:  # noqa: BLE001 - keep scanning visible matches
                    continue
    except Exception:  # noqa: BLE001 - older/fake pages may not expose get_by_text
        pass

    # Fallback for Google builds that expose only the surrounding control.
    try:
        for candidate in page.locator("button, input[type=button], input[type=submit], [role=button], [role=menuitem], [role=tab], a, li").all():
            try:
                label = (candidate.inner_text() or "").strip().lower()
            except Exception:  # noqa: BLE001
                continue
            if any(w.lower() in label[:120] for w in wanted):
                try:
                    if not candidate.is_visible():
                        continue
                    candidate.click(timeout=1500)
                    return True
                except Exception:  # noqa: BLE001 - hidden/disabled; keep scanning
                    continue
    except Exception:  # noqa: BLE001
        return False
    return False


def _contacts_dialog_closed_after_confirm(observer: "BrowserObserver", page: "Page") -> bool:
    """Treat a disappeared import dialog as success after the click settles.

    Google often displays the success toast only briefly (and it may not be
    exposed through Playwright's visible-text snapshot). Requiring two ticks
    after the exact action-text click and confirming that no import dialog or
    final action remains prevents a click acknowledgement from being mistaken
    for a completed import.
    """
    if not observer._contacts_confirm_clicked or observer._contacts_post_confirm_ticks < 2:
        return False
    if _contacts_import_dialog_visible(page):
        return False
    return not _contacts_has_visible_exact_text(page, ("import", "importar", "importer"))


def _contacts_import_result_route(observer: "BrowserObserver", page: "Page") -> bool:
    """Recognize Google's durable post-import route when the toast is gone.

    The completion toast is transient and is not consistently exposed through
    Playwright's accessible body text. After the final Import action succeeds,
    Google navigates to the imported label (``/label/<id>``). Requiring the
    confirmation click and a short settle window makes this route a safe,
    durable completion signal without treating the initial Contacts page as
    success.
    """
    if not observer._contacts_confirm_clicked or observer._contacts_post_confirm_ticks < 2:
        return False
    try:
        parsed = urlparse(page.url or "")
    except Exception:  # noqa: BLE001 - a closing page is not a success signal
        return False
    if parsed.hostname != "contacts.google.com":
        return False
    return bool(re.fullmatch(r"/label/[^/]+/?", parsed.path or ""))


def _contacts_import_dialog_visible(page: "Page") -> bool:
    try:
        for selector in ('[role="dialog"]', '[aria-modal="true"]'):
            for dialog in page.locator(selector).all():
                try:
                    if dialog.is_visible():
                        return True
                except Exception:  # noqa: BLE001 - continue checking other dialog candidates
                    continue
    except Exception:  # noqa: BLE001 - fall back to the exact action check
        pass
    return False


def _contacts_has_visible_exact_text(page: "Page", labels: tuple[str, ...]) -> bool:
    try:
        get_by_text = getattr(page, "get_by_text")
        for label in labels:
            locator = get_by_text(re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE))
            for candidate in locator.all():
                try:
                    if candidate.is_visible():
                        return True
                except Exception:  # noqa: BLE001 - keep scanning matches
                    continue
    except Exception:  # noqa: BLE001 - old/fake pages do not expose get_by_text
        return False
    return False


def _visible_text_mentions_done(page: "Page") -> bool:
    text = _visible_text(page).lower()
    return any(marker in text for marker in _CONTACTS_DONE_MARKERS)
