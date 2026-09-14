import time
from unittest.mock import MagicMock, patch

from app.login_policy import PageAction, PageSignal
from app.observer import BrowserObserver, _is_safe_interstitial


class _FakeButton:
    def __init__(self, label: str):
        self._label = label

    def inner_text(self) -> str:
        return self._label

    def click(self, timeout: int = 1500) -> None:
        self.clicked = True


class _FakePage:
    def __init__(self, url: str, title: str = "", text: str = "", inputs: int = 0, buttons: list | None = None):
        self.url = url
        self._title = title
        self._text = text
        self._inputs = inputs
        self.buttons = buttons or []
        self.clicks = 0

    def title(self) -> str:
        return self._title

    def inner_text(self, timeout: int = 1000) -> str:
        return self._text

    def locator(self, selector: str):
        if selector == "body":
            return _FakeLocator([self])
        if selector == "button, [role=button], a":
            return _FakeLocator(self.buttons)
        if "input" in selector:
            return _FakeLocator([], count=self._inputs)
        return _FakeLocator([])

    def count(self) -> int:
        return 1


class _FakeLocator:
    def __init__(self, elements: list, count: int | None = None):
        self._elements = elements
        self._count = count

    def count(self) -> int:
        return self._count if self._count is not None else len(self._elements)

    def all(self):
        return list(self._elements)

    def inner_text(self, timeout: int = 1000) -> str:
        return self._elements[0].inner_text(timeout=timeout) if self._elements else ""


def _fake_browser(pages: list) -> MagicMock:
    browser = MagicMock()
    context = MagicMock()
    context.pages = pages
    browser.contexts = [context]
    return browser


def test_observer_skips_consent_when_credential_field_present():
    page = _FakePage("https://accounts.google.com/", "sign in", "enter your password", inputs=1)
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, interval=0.05)
    with patch("app.observer.classify_page", return_value=(PageSignal.ORDINARY_INTERSTITIAL, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert page.clicks == 0


def test_observer_clicks_safe_consent():
    button = _FakeButton("I agree")
    page = _FakePage("https://consent.google.com/", "consent", "review settings welcome continue", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, interval=0.05)
    with patch("app.observer.classify_page", return_value=(PageSignal.ORDINARY_INTERSTITIAL, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert getattr(button, "clicked", False) is True


def test_observer_pauses_for_human_on_captcha():
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, interval=0.05)
    page = _FakePage("https://accounts.google.com/v3/signin/challenge", "security check", "verify it's you")
    with patch("app.observer.classify_page", return_value=(PageSignal.CAPTCHA, PageAction.WAIT_FOR_HUMAN)):
        observer._tick(_fake_browser([page]))
    assert observer._stop.is_set()


def test_observer_records_observations():
    seen = []
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda sid, url, title, text: seen.append((sid, url, title, text)), interval=0.05)
    page = _FakePage("https://mail.google.com/", "Inbox", "you have mail")
    with patch("app.observer.classify_page", return_value=(PageSignal.GMAIL_HOME, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert seen == [("s1", "https://mail.google.com/", "Inbox", "you have mail")]


def test_observer_deduplicates_unchanged_observations():
    seen = []
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *args: seen.append(args), interval=0.05)
    page = _FakePage("https://mail.google.com/", "Inbox", "you have mail")
    with patch("app.observer.classify_page", return_value=(PageSignal.GMAIL_HOME, PageAction.SAFE_TO_OBSERVE)):
        browser = _fake_browser([page])
        observer._tick(browser)
        observer._tick(browser)
    assert len(seen) == 1


def test_observer_requires_an_exact_google_host_for_consent():
    assert _is_safe_interstitial("https://accounts.google.com/continue", "ORDINARY_INTERSTITIAL") is True
    assert _is_safe_interstitial("https://evil.example/accounts.google.com/continue", "UNKNOWN") is False


def test_login_policy_does_not_treat_gmail_root_as_authenticated():
    from app.login_policy import PageObservation, classify_page

    signal, action = classify_page(PageObservation("https://mail.google.com/", "Gmail", "Welcome to Gmail"))
    assert signal is PageSignal.UNKNOWN
    assert action is PageAction.NEEDS_ATTENTION


def test_login_policy_requires_a_mailbox_route_and_marker():
    from app.login_policy import PageObservation, classify_page

    signal, action = classify_page(PageObservation("https://mail.google.com/mail/u/0/#inbox", "Inbox", "Inbox Compose Sent Mail"))
    assert signal is PageSignal.GMAIL_HOME
    assert action is PageAction.SAFE_TO_OBSERVE


def test_observer_stop_joins_thread():
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, interval=0.05)
    observer.start()
    assert observer._thread is not None and observer._thread.is_alive()
    observer.stop()
    assert not observer._thread.is_alive()
