import time
from unittest.mock import MagicMock, patch

from app.login_policy import PageAction, PageSignal
from app.observer import BrowserObserver, _CONTACTS_FINAL_IMPORT_SELECTOR, _GMAIL_ADDONS_GET_SELECTOR, _GMAIL_ADDONS_SIDEBAR_SELECTOR, _active_addons_surface, _addons_authorize_google_oauth, _addons_authorize_marketplace_install, _addons_click_install, _click_gmail_addons_sidebar_toggle, _click_gmail_get_addons, _confirm_enhanced_safe_browsing, _contacts_click_final_import, _contacts_click_label, _contacts_dialog_closed_after_confirm, _contacts_import_result_route, _fill_otp_code, _handle_security_tick, _is_safe_interstitial, _open_start_page, _otp_should_attempt, _page_rank


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
    def __init__(self, url: str, title: str = "", text: str = "", inputs: int = 0, buttons: list | None = None, identifier: str | None = None, password: str | None = None, otp: str | None = None):
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
        if otp is not None:
            self.cred_fields["otp"] = _FakeField(otp)

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
        if "totppin" in low or "inputmode" in low or "one-time-code" in low:
            return _FakeLocator([self.cred_fields["otp"]] if "otp" in self.cred_fields else [])
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

    def is_visible(self) -> bool:
        return bool(self._elements) and getattr(self._elements[0], "is_visible", lambda: True)()

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


class _FakeText(_FakeButton):
    def __init__(self, label: str, clickable: bool = True):
        super().__init__(label)
        self._clickable = clickable

    def is_visible(self) -> bool:
        return True

    def evaluate(self, _script: str) -> bool:
        return self._clickable


class _FakeTextPage:
    def __init__(self, *texts: _FakeText):
        self._texts = list(texts)

    def get_by_text(self, pattern):
        return _FakeLocator([text for text in self._texts if pattern.search(text.inner_text())])

    def locator(self, _selector: str):
        return _FakeLocator([])


class _FakeFinalImportPage:
    def __init__(self):
        self.button = _FakeText("Importar", clickable=True)

    def locator(self, _selector: str):
        return _FakeLocator([self.button])

    def evaluate(self, _script: str, selector: str):
        self.evaluated_selector = selector
        self.button.click()
        return True


class _FakeAddonsSidebarPage:
    def __init__(self):
        self.selector = None

    def evaluate(self, _script: str, selector: str):
        self.selector = selector
        return True


class _FakeAddonsSidebarFallbackPage:
    def __init__(self):
        self.evaluate_calls = 0

    def locator(self, _selector: str):
        return _FakeLocator([])

    def evaluate(self, _script: str, _argument):
        self.evaluate_calls += 1
        return self.evaluate_calls > 1


class _FakeMarketplaceInstallPage:
    def __init__(self, result: str):
        self.result = result

    def evaluate(self, _script: str, _config: dict):
        return self.result


class _FakeInstallControl(_FakeButton):
    def __init__(self, label: str, aria_label: str | None = None):
        super().__init__(label)
        self.aria_label = aria_label

    def get_attribute(self, name: str):
        return self.aria_label if name == "aria-label" else None

    def is_visible(self) -> bool:
        return True


class _FakeInstallPage:
    def __init__(self):
        self.controls = [_FakeInstallControl("Installer", "Install first card"), _FakeInstallControl("Désinstaller", "Uninstall another card")]

    def locator(self, _selector: str):
        return _FakeLocator(self.controls)


class _FakeGetAddonsPage:
    def __init__(self):
        self.selector = None

    def evaluate(self, _script: str, selector: str):
        self.selector = selector
        return selector == _GMAIL_ADDONS_GET_SELECTOR


class _FakeGmailWithGetAddonsFrame:
    def __init__(self):
        self.get_addons_frame = _FakeGetAddonsPage()
        self.frames = [self.get_addons_frame]

    def locator(self, _selector: str):
        return _FakeLocator([])

    def evaluate(self, _script: str, _argument):
        return False


class _FakeFrame:
    def __init__(self, url: str):
        self.url = url


class _FakeMarketplaceTextFrame:
    url = "https://mail.google.com/mail/u/0/#inbox"

    class _Body:
        def count(self):
            return 1

        def inner_text(self, timeout: int = 1000):
            return "Google Workspace Marketplace — Installer des applications"

    def locator(self, selector: str):
        assert selector == "body"
        return self._Body()


class _FakeFramedGmailPage:
    url = "https://mail.google.com/mail/u/0/#inbox"

    def __init__(self):
        self.marketplace_frame = _FakeFrame("https://workspace.google.com/marketplace/")
        self.frames = [self.marketplace_frame]


class _FakeSecurityPage:
    def __init__(self, url: str):
        self.url = url
        self.goto_calls: list[str] = []

    def goto(self, url: str, **_kwargs):
        self.goto_calls.append(url)
        self.url = url

    def evaluate(self, _script: str, _config: dict):
        return "already"


class _FakeSecurityConfirmationPage:
    url = "https://myaccount.google.com/account-enhanced-safe-browsing"

    def evaluate(self, _script: str, _config: dict):
        return "activer"


class _FakeBlankPage:
    url = "about:blank"

    def goto(self, _url: str, **_kwargs):
        raise TimeoutError("navigation timed out")


def test_start_page_navigation_uses_commit_and_exact_target():
    page = _FakeSecurityPage("about:blank")
    _open_start_page(page, "https://myaccount.google.com/account-enhanced-safe-browsing")
    assert page.goto_calls == ["https://myaccount.google.com/account-enhanced-safe-browsing"]


def test_start_page_navigation_reports_blank_page_as_failure():
    assert _open_start_page(_FakeBlankPage(), "https://myaccount.google.com/account-enhanced-safe-browsing") is False


def test_security_operation_navigates_to_exact_page_and_waits_for_verified_completion():
    events: list[str] = []
    observer = BrowserObserver("s-security", "p-security", 9222, purpose="security", report_event=lambda _sid, event, _detail, _result: events.append(event))
    page = _FakeSecurityPage("https://mail.google.com/mail/u/0/")

    _handle_security_tick(observer, page, "mail.google.com")
    assert page.goto_calls == []
    _handle_security_tick(observer, page, "myaccount.google.com")
    assert page.goto_calls == ["https://myaccount.google.com/account-enhanced-safe-browsing"]
    _handle_security_tick(observer, page, "myaccount.google.com")
    _handle_security_tick(observer, page, "myaccount.google.com")
    _handle_security_tick(observer, page, "myaccount.google.com")
    assert "security.completed" in events


def test_security_operation_confirms_localized_activation_dialog():
    events: list[str] = []
    observer = BrowserObserver("s-security", "p-security", 9222, purpose="security", report_event=lambda _sid, event, _detail, _result: events.append(event))
    page = _FakeSecurityConfirmationPage()

    _handle_security_tick(observer, page, "myaccount.google.com")

    assert "security.activation_confirmed" in events
    assert _confirm_enhanced_safe_browsing(page) == "activer"


def test_contacts_clicks_the_configured_final_import_button_selector():
    page = _FakeFinalImportPage()

    assert _contacts_click_final_import(page) is True
    assert getattr(page.button, "clicked", False) is True
    assert page.evaluated_selector == _CONTACTS_FINAL_IMPORT_SELECTOR


def test_addons_clicks_the_configured_gmail_sidebar_selector():
    page = _FakeAddonsSidebarPage()

    assert _click_gmail_addons_sidebar_toggle(page) is True
    assert page.selector == _GMAIL_ADDONS_SIDEBAR_SELECTOR


def test_addons_sidebar_falls_back_when_configured_node_is_not_actionable():
    page = _FakeAddonsSidebarFallbackPage()

    assert _click_gmail_addons_sidebar_toggle(page) is True
    assert page.evaluate_calls == 2


def test_addons_clicks_the_explicit_get_addons_selector_after_rail_opens():
    page = _FakeGetAddonsPage()

    assert _click_gmail_get_addons(page) is True
    assert page.selector == _GMAIL_ADDONS_GET_SELECTOR


def test_addons_clicks_explicit_get_addons_selector_inside_revealed_sidebar_frame():
    page = _FakeGmailWithGetAddonsFrame()

    assert _click_gmail_get_addons(page) is True
    assert page.get_addons_frame.selector == _GMAIL_ADDONS_GET_SELECTOR


def test_addons_uses_embedded_marketplace_frame_from_gmail_side_panel():
    page = _FakeFramedGmailPage()

    surface, host = _active_addons_surface(page, "mail.google.com")

    assert surface is page.marketplace_frame
    assert host == "workspace.google.com"


def test_addons_uses_marketplace_copy_when_embedded_frame_url_is_not_stable():
    page = _FakeFramedGmailPage()
    page.marketplace_frame = _FakeMarketplaceTextFrame()
    page.frames = [page.marketplace_frame]

    surface, host = _active_addons_surface(page, "mail.google.com")

    assert surface is page.marketplace_frame
    assert host == "workspace.google.com"


def test_addons_authorizes_localized_marketplace_install_confirmation():
    page = _FakeMarketplaceInstallPage("clicked")

    assert _addons_authorize_marketplace_install(page, "Any.do for Gmail") == "clicked"


def test_addons_authorizes_spanish_google_oauth_popup():
    page = _FakeMarketplaceInstallPage("clicked")
    page.url = "https://accounts.google.com/signin/oauth/id?authuser=0"

    assert _addons_authorize_google_oauth(page, "Any.do for Gmail") == "clicked"


def test_addons_prioritizes_google_oauth_popup_over_marketplace_tab():
    assert _page_rank("https://accounts.google.com/signin/oauth/id", "addons") < _page_rank("https://workspace.google.com/marketplace/app/1", "addons")


def test_addons_clicks_first_card_install_using_localized_or_aria_label():
    page = _FakeInstallPage()

    assert _addons_click_install(page) is True
    assert page.controls[0].clicked is True
    assert getattr(page.controls[1], "clicked", False) is not True


def test_addons_pauses_unrelated_consent_confirmation():
    page = _FakeMarketplaceInstallPage("pause")

    assert _addons_authorize_marketplace_install(page, "Any.do for Gmail") == "pause"


def test_contacts_clicks_exact_visible_action_text_before_title_or_container():
    title = _FakeText("Importar contactos", clickable=False)
    action = _FakeText("Importar", clickable=True)
    page = _FakeTextPage(title, action)

    assert _contacts_click_label(page, ("importar", "importar contactos")) is True
    assert getattr(action, "clicked", False) is True
    assert getattr(title, "clicked", False) is not True


def test_contacts_import_waits_for_dialog_to_disappear_after_click():
    observer = BrowserObserver("s1", "p1", 9222)
    observer._contacts_confirm_clicked = True
    observer._contacts_post_confirm_ticks = 2

    # The page has returned to Contacts and only the non-clickable page title
    # remains; the import dialog and its final action are gone.
    page = _FakeTextPage(_FakeText("Importar contactos", clickable=False))
    assert _contacts_dialog_closed_after_confirm(observer, page) is True


def test_contacts_import_does_not_finish_while_final_action_is_visible():
    observer = BrowserObserver("s1", "p1", 9222)
    observer._contacts_confirm_clicked = True
    observer._contacts_post_confirm_ticks = 2
    page = _FakeTextPage(_FakeText("Importar", clickable=True))

    assert _contacts_dialog_closed_after_confirm(observer, page) is False


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
    assert getattr(button, "clicked", False) is True


def test_auto_login_retries_a_transient_submit_failure():
    button = _FakeButton("Continue")
    page = _FakePage("https://accounts.google.com/", "sign in", "Email or phone", identifier="", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"), report_event=lambda *a: None)
    browser = _fake_browser([page])
    with patch("app.observer.classify_page", return_value=(PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)), patch("app.observer._click_submit", side_effect=[TimeoutError("boom"), None]):
        observer._tick(browser)
        assert "https://accounts.google.com/" not in observer._auto_attempted_urls
        assert page.cred_fields["email"].value == "alice@example.com"
        observer._tick(browser)
    assert "https://accounts.google.com/" in observer._submitted_urls
    assert "https://accounts.google.com/" not in observer._auto_attempted_urls


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


def test_auto_login_fills_identifier_even_when_classified_interstitial():
    button = _FakeButton("Continue")
    page = _FakePage("https://accounts.google.com/v3/signin/identifier", "One Google Account", "Welcome back to Gmail. Continue to Gmail.", identifier="", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"))
    with patch("app.observer.classify_page", return_value=(PageSignal.ORDINARY_INTERSTITIAL, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["email"].value == "alice@example.com"
    assert getattr(button, "clicked", False) is True


def test_auto_login_fills_otp_from_stored_secret():
    events = []
    button = _FakeButton("Next")
    page = _FakePage("https://accounts.google.com/v3/signin/challenge/totp/xyz", "Enter the code", "Enter the code from the Google Authenticator app", otp="", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"), resolve_otp_secret=lambda sid: "JBSWY3DPEHPK3PXP", report_event=lambda sid, event, detail, result: events.append(event))
    with patch("app.observer.classify_page", return_value=(PageSignal.MFA_OR_SECURITY_CHECK, PageAction.WAIT_FOR_HUMAN)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["otp"].value.isdigit() and len(page.cred_fields["otp"].value) == 6
    assert "otp_filled" in events
    assert getattr(button, "clicked", False) is True


def test_auto_login_retries_rejected_otp_with_a_fresh_time_window_code():
    events = []
    url = "https://accounts.google.com/v3/signin/challenge/totp/xyz"
    page = _FakePage(url, "Enter the code", "Incorrect code. Try again.", otp="")
    observer = BrowserObserver("s1", "p1", 9222, resolve_otp_secret=lambda sid: "JBSWY3DPEHPK3PXP", report_event=lambda sid, event, detail, result: events.append(event))

    with patch("app.observer.time.time", return_value=60.0), patch("app.observer.time.monotonic", return_value=10.0):
        _fill_otp_code(page, observer, url)
    assert "otp_filled" in events

    page.cred_fields["otp"].value = ""
    with patch("app.observer.time.time", return_value=90.0), patch("app.observer.time.monotonic", return_value=20.0):
        assert _otp_should_attempt(page, observer, url) is True
        _fill_otp_code(page, observer, url)

    assert "otp_retry_filled" in events
    assert observer._otp_retry_counts[url] == 1
    assert len(page.cred_fields["otp"].value) == 6


def test_auto_login_does_not_resubmit_same_otp_time_window():
    url = "https://accounts.google.com/v3/signin/challenge/totp/xyz"
    page = _FakePage(url, "Enter the code", "Incorrect code. Try again.", otp="")
    observer = BrowserObserver("s1", "p1", 9222, resolve_otp_secret=lambda sid: "JBSWY3DPEHPK3PXP")
    observer._otp_attempted_urls.add(url)
    observer._otp_submitted_at[url] = 0.0
    observer._otp_last_code_counters[url] = 2

    with patch("app.observer.time.time", return_value=89.0), patch("app.observer.time.monotonic", return_value=10.0):
        assert _otp_should_attempt(page, observer, url) is False


def test_auto_login_skips_otp_without_stored_secret():
    events = []
    page = _FakePage("https://accounts.google.com/v3/signin/challenge/totp/xyz", "Enter the code", "Google Authenticator", otp="")
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_otp_secret=lambda sid: None, report_event=lambda sid, event, detail, result: events.append(event))
    with patch("app.observer.classify_page", return_value=(PageSignal.MFA_OR_SECURITY_CHECK, PageAction.WAIT_FOR_HUMAN)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["otp"].value == ""
    assert "skipped" in events


def test_login_policy_classifies_google_code_entry_as_mfa():
    from app.login_policy import PageObservation, classify_page

    signal, action = classify_page(PageObservation("https://accounts.google.com/v3/signin/challenge/totp/1", "Enter code", "Enter the code from the Google Authenticator app"))
    assert signal is PageSignal.MFA_OR_SECURITY_CHECK
    assert action is PageAction.WAIT_FOR_HUMAN


def test_login_policy_classifies_text_sparse_totp_route_as_mfa():
    from app.login_policy import PageObservation, classify_page

    signal, action = classify_page(PageObservation("https://accounts.google.com/v3/signin/challenge/totp", "Google", "Privacy policy"))
    assert signal is PageSignal.MFA_OR_SECURITY_CHECK
    assert action is PageAction.WAIT_FOR_HUMAN


def test_login_policy_classifies_french_contacts_completion_to_close_session():
    from app.login_policy import PageObservation, classify_page

    signal, action = classify_page(PageObservation("https://contacts.google.com/label/import", "Contacts", "Le processus est terminé."))
    assert signal is PageSignal.CONTACTS_IMPORT_DONE
    assert action is PageAction.SAFE_TO_OBSERVE


def test_login_policy_classifies_gmail_settings_as_safe_to_observe():
    from app.login_policy import PageObservation, classify_page

    signal, action = classify_page(PageObservation("https://mail.google.com/mail/u/0/?tab=rm&ogbl#settings/inbox", "Settings", "Importance markers"))
    assert signal is PageSignal.GMAIL_SETTINGS
    assert action is PageAction.SAFE_TO_OBSERVE


def test_contacts_import_result_route_is_completion_after_confirm():
    observer = BrowserObserver("s1", "p1", 9222, purpose="contacts")
    observer._contacts_confirm_clicked = True
    observer._contacts_post_confirm_ticks = 2
    page = _FakePage("https://contacts.google.com/label/274fc7528c1cc1f0")

    assert _contacts_import_result_route(observer, page) is True


def test_observer_clicks_use_another_account_on_chooser():
    button = _FakeButton("Use another account")
    page = _FakePage("https://accounts.google.com/signin/v2/identifier", "choose an account", "Choose an account to continue", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"), report_event=lambda *a: None)
    with patch("app.observer.classify_page", return_value=(PageSignal.ORDINARY_INTERSTITIAL, PageAction.SAFE_TO_OBSERVE)):
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


def test_observer_clicks_post_login_continue_handoff():
    button = _FakeButton("Continue")
    page = _FakePage("https://accounts.google.com/v3/signin/continue", "Google", "Continue to Gmail", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, report_event=lambda *a: None)
    with patch("app.observer.classify_page", return_value=(PageSignal.GOOGLE_ACCOUNT_HOME, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert getattr(button, "clicked", False) is True
    assert "https://accounts.google.com/v3/signin/continue" in observer._continue_clicked_urls


def test_observer_clicks_localized_post_login_continue_handoff():
    button = _FakeButton("Continuer")
    page = _FakePage("https://consent.google.com/continue", "Google", "Continuer vers Gmail", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, report_event=lambda *a: None)
    with patch("app.observer.classify_page", return_value=(PageSignal.ORDINARY_INTERSTITIAL, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert getattr(button, "clicked", False) is True


def test_observer_clicks_account_specific_continue_handoff():
    button = _FakeButton("Continuer en tant que Alfredo")
    page = _FakePage("https://accounts.google.com/v3/signin/continue", "Google", "Continuer en tant que Alfredo", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, report_event=lambda *a: None)
    with patch("app.observer.classify_page", return_value=(PageSignal.GOOGLE_ACCOUNT_HOME, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert getattr(button, "clicked", False) is True


def test_observer_checks_saved_profile_handoff_inside_gmail():
    button = _FakeButton("Continuar como Alfredo")
    page = _FakePage("https://mail.google.com/mail/u/0/", "Gmail", "Continuar como Alfredo", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, report_event=lambda *a: None)
    with patch("app.observer.classify_page", return_value=(PageSignal.GMAIL_HOME, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert getattr(button, "clicked", False) is True


def test_observer_does_not_click_generic_gmail_continue():
    button = _FakeButton("Continue")
    page = _FakePage("https://mail.google.com/mail/u/0/", "Gmail", "Continue", buttons=[button])
    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, report_event=lambda *a: None)
    with patch("app.observer.classify_page", return_value=(PageSignal.GMAIL_HOME, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert getattr(button, "clicked", False) is not True


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


def test_input_value_guards_against_non_input_nodes():
    from app.observer import _input_value

    class _NonInput:
        def input_value(self, timeout: int = 1000):
            raise RuntimeError("Node is not an <input>")

    assert _input_value(_NonInput()) == ""


def test_selector_constraints_only_match_inputs_for_password_ids():
    from app.observer import _CREDENTIAL_FIELD_SELECTOR, _OTP_FIELD_SELECTOR

    credential_parts = {part.strip() for part in _CREDENTIAL_FIELD_SELECTOR.split(",")}
    assert "#password" not in credential_parts
    assert "input#password" in credential_parts
    otp_parts = {part.strip() for part in _OTP_FIELD_SELECTOR.split(",")}
    assert "#totpPin" not in otp_parts
    assert any(part.startswith("input#totpPin") for part in otp_parts)


def test_observer_stop_joins_thread():
    def _block_until_stopped(_pw, _observer):
        _observer._stop.wait(10)
        return MagicMock()

    observer = BrowserObserver("s1", "p1", 9222, observe_session_page=lambda *a: None, interval=0.05)
    with patch("app.observer._launch_profile_context", side_effect=_block_until_stopped), patch("app.observer._open_start_page", return_value=None):
        observer.start()
        assert observer._thread is not None and observer._thread.is_alive()
        observer.stop()
    assert not observer._thread.is_alive()


def test_observer_stop_from_its_own_thread_does_not_try_to_join_itself():
    observer = BrowserObserver("s1", "p1", 9222)
    observer._thread = __import__("threading").current_thread()
    observer.stop()
    assert observer._stop.is_set()
