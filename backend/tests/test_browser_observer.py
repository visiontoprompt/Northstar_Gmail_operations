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


class _FakeField:
    def __init__(self, value: str = ""):
        self.value = value


class _FakePage:
    def __init__(self, url: str, title: str = "", text: str = "", inputs: int = 0, buttons: list | None = None, identifier: str | None = None, password: str | None = None):
        self.url = url
        self._title = title
        self._text = text
        self._inputs = inputs
        self.buttons = buttons or []
        self.clicks = 0
        self.cred_fields: dict[str, _FakeField] = {}
        if identifier is not None:
            self.cred_fields["email"] = _FakeField(identifier)
        if password is not None:
            self.cred_fields["password"] = _FakeField(password)

    def title(self) -> str:
        return self._title

    def inner_text(self, timeout: int = 1000) -> str:
        return self._text

    def locator(self, selector: str):
        low = selector.lower()
        if selector == "body":
            return _FakeLocator([self])
        if "button" in low and ("role=button" in low or " a" in low or low.endswith(", a") or low == "button, [role=button], a"):
            return _FakeLocator(self.buttons)
        if "password" in low and "email" in low:
            present = 1 if ("email" in self.cred_fields or "password" in self.cred_fields) else self._inputs
            return _FakeLocator([], count=present)
        if "password" in low:
            return _FakeLocator([self.cred_fields["password"]] if "password" in self.cred_fields else [])
        if "email" in low or "identifier" in low:
            return _FakeLocator([self.cred_fields["email"]] if "email" in self.cred_fields else [])
        if "input" in low:
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

    @property
    def first(self):
        return _FakeLocator(self._elements[:1]) if self._elements else _FakeLocator([])

    def inner_text(self, timeout: int = 1000) -> str:
        return self._elements[0].inner_text(timeout=timeout) if self._elements else ""

    def input_value(self, timeout: int = 1000) -> str:
        return getattr(self._elements[0], "value", "") if self._elements else ""

    def fill(self, value: str, timeout: int = 1500):
        if self._elements:
            self._elements[0].value = value
        return self

    def click(self, timeout: int = 1500):
        if self._elements:
            self._elements[0].click(timeout=timeout)
        return self


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


def test_observer_clicks_workspace_speedbump_understand():
    button = _FakeButton("I understand")
    page = _FakePage("https://workspace.google.com/workspacetermsofservice", "Google Workspace", "Google Workspace terms of service before you continue", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, interval=0.05)
    with patch("app.observer.classify_page", return_value=(PageSignal.ORDINARY_INTERSTITIAL, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert getattr(button, "clicked", False) is True


def test_observer_skips_speedbump_on_unknown_host():
    button = _FakeButton("I understand")
    page = _FakePage("https://evil.example/login", "terms", "i understand before you continue", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, interval=0.05)
    with patch("app.observer.classify_page", return_value=(PageSignal.ORDINARY_INTERSTITIAL, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert getattr(button, "clicked", False) is not True


def test_observer_reports_captcha_to_session_but_keeps_watching():
    seen = []
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: seen.append(a), interval=0.05)
    page = _FakePage("https://accounts.google.com/v3/signin/challenge", "security check", "verify it's you")
    with patch("app.observer.classify_page", return_value=(PageSignal.CAPTCHA, PageAction.WAIT_FOR_HUMAN)):
        observer._tick(_fake_browser([page]))
    assert seen, "the challenge must be reported to the session"
    assert not observer._stop.is_set(), "observer must stay alive to catch Gmail after the human clears the challenge"


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


def test_auto_login_fills_identifier_and_submits():
    button = _FakeButton("Continue")
    page = _FakePage("https://accounts.google.com/", "sign in", "Email or phone", identifier="", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"))
    with patch("app.observer.classify_page", return_value=(PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["email"].value == "alice@example.com"
    assert getattr(button, "clicked", False) is True
    assert "https://accounts.google.com/" in observer._auto_attempted_urls


def test_auto_login_fills_password_on_password_page():
    button = _FakeButton("Sign in")
    page = _FakePage("https://accounts.google.com/v3/signin/challenge/pwd", "Enter your password", "Enter your password", password="", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"))
    with patch("app.observer.classify_page", return_value=(PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["password"].value == "s3cret"
    assert getattr(button, "clicked", False) is True


def test_auto_login_does_not_overwrite_filled_field():
    button = _FakeButton("Continue")
    page = _FakePage("https://accounts.google.com/", "sign in", "Email or phone", identifier="alice@example.com", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"))
    with patch("app.observer.classify_page", return_value=(PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["email"].value == "alice@example.com"
    assert getattr(button, "clicked", False) is not True


def test_auto_login_skips_when_no_credentials_stored():
    page = _FakePage("https://accounts.google.com/", "sign in", "Email or phone", identifier="")
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: None)
    with patch("app.observer.classify_page", return_value=(PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["email"].value == ""


def test_auto_login_only_on_google_login_host():
    button = _FakeButton("Continue")
    page = _FakePage("https://evil.example/", "sign in", "Email or phone", identifier="", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"))
    with patch("app.observer.classify_page", return_value=(PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["email"].value == ""
    assert "https://evil.example/" not in observer._auto_attempted_urls


def test_auto_login_attempts_each_page_once():
    button = _FakeButton("Continue")
    page = _FakePage("https://accounts.google.com/", "sign in", "Email or phone", identifier="", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"))
    browser = _fake_browser([page])
    with patch("app.observer.classify_page", return_value=(PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(browser)
        observer._tick(browser)
    assert page.cred_fields["email"].value == "alice@example.com"
    assert getattr(button, "clicked", False) is True


def test_auto_login_reports_events():
    events = []
    page = _FakePage("https://accounts.google.com/", "sign in", "Email or phone", identifier="")
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"), report_event=lambda sid, event, detail, result: events.append((sid, event, detail, result)))
    with patch("app.observer.classify_page", return_value=(PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert any(event == "identifier_filled" for _, event, _, _ in events)


def test_observer_clicks_use_another_account_on_chooser():
    button = _FakeButton("Use another account")
    page = _FakePage("https://accounts.google.com/signin/v2/identifier", "choose an account", "Choose an account to continue", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"), report_event=lambda *a: None)
    with patch("app.observer.classify_page", return_value=(PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert getattr(button, "clicked", False) is True
    assert "https://accounts.google.com/signin/v2/identifier" in observer._chooser_clicked_urls


def test_observer_fills_identifier_after_chooser_is_expanded():
    clicked = [_FakeButton("Use another account")]
    page = _FakePage("https://accounts.google.com/signin/v2/identifier", "sign in", "Email or phone", identifier="", buttons=clicked)
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"))
    with patch("app.observer.classify_page", return_value=(PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["email"].value == "alice@example.com"


def test_observer_ignores_transient_unknown_during_cold_page_load():
    seen = []
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: seen.append(a), interval=0.05, settle_seconds=5.0)
    page = _FakePage("https://accounts.google.com/", "", "")
    browser = _fake_browser([page])
    with patch("app.observer.classify_page", return_value=(PageSignal.UNKNOWN, PageAction.NEEDS_ATTENTION)):
        observer._tick(browser)
    assert seen == []
    assert not observer._stop.is_set()
    assert observer._unknown_since is not None


def test_observer_reports_unknown_after_settle_window():
    seen = []
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: seen.append(a), interval=0.05, settle_seconds=0.0)
    page = _FakePage("https://accounts.google.com/", "unexpected page", "some unexpected content")
    browser = _fake_browser([page])
    with patch("app.observer.classify_page", return_value=(PageSignal.UNKNOWN, PageAction.NEEDS_ATTENTION)):
        observer._tick(browser)
    assert seen, "a settled unknown page must be reported"
    assert not observer._stop.is_set()


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
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, interval=0.05, connect_timeout=0.1)
    observer.start()
    assert observer._thread is not None and observer._thread.is_alive()
    observer.stop()
    assert not observer._thread.is_alive()
