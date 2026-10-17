import time
from unittest.mock import MagicMock, patch

from app.login_policy import PageAction, PageSignal
from app.observer import BrowserObserver, _MAIL_CONFIRMATION_CONTROL_WAIT_TICKS, _MAIL_CONFIRMATION_LANDING_WAIT_TICKS, _MAIL_CONFIRMATION_MAX_TICKS, _MAIL_CONFIRMATION_SUCCESS_TERMS, _MAIL_REVIEW_EMPTY_SETTLE_TICKS, _MAIL_REVIEW_MIN_COLLECTION_TICKS, _MAIL_REVIEW_ROUTE_WAIT_TICKS, _mail_collect_review_candidates, _mail_confirmation_score, _mail_confirmation_tick_budget, _mail_navigate_to_review_candidate, _ACCOUNT_PREFERENCES_EXISTING_IMAGE_PICKER_MARKERS, _ACCOUNT_PREFERENCES_ILLUSTRATION_LABELS, _ACCOUNT_PREFERENCES_PROFILE_PICKER_MARKERS, _ACCOUNT_PREFERENCES_PROFILE_SAVE_LABELS, _CONTACTS_FINAL_IMPORT_SELECTOR, _GMAIL_ADDONS_GET_SELECTOR, _GMAIL_ADDONS_SIDEBAR_SELECTOR, _PASSWORD_CHANGE_SUCCESS_JS_RE, _RECOVERY_PAGE_PATHS, _RECOVERY_URL, _TWO_STEP_AUTH_NAME_TERMS, _TWO_STEP_CONFIRM_REMOVE_LABELS, _TWO_STEP_MANUAL_LABELS, _TWO_STEP_PAGE_PATHS, _TWO_STEP_REMOVE_LABELS, _TWO_STEP_ROW_EDIT_LABELS, _TWO_STEP_URL, _active_page, _complete_credential_change, _active_addons_surface, _addons_authorize_google_oauth, _addons_authorize_marketplace_install, _addons_click_install, _click_gmail_addons_sidebar_toggle, _click_gmail_get_addons, _click_random_profile_illustration, _confirm_enhanced_safe_browsing, _contacts_click_final_import, _contacts_click_label, _contacts_dialog_closed_after_confirm, _contacts_import_result_route, _credential_change_is_confirmed, _drive_recovery_page, _drive_two_step_setup, _fill_otp_code, _fill_two_step_code, _finish_password_change, _handle_addons_loading_stall, _handle_addons_missing_surface, _handle_credential_change_tick, _handle_mail_tick, _handle_security_tick, _is_safe_interstitial, _mail_action_confirmation_visible, _mail_click_reply_send, _mail_fill_reply, _mail_reply_surface_ready, _mail_review_control_summary, _mail_scroll_reply_thread, _mail_search_applied, _mail_subject_query, _open_start_page, _otp_should_attempt, _page_rank, _visible_picker_root


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

    def locator(self, _selector: str):
        return _FakeLocator(self._elements)

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


class _GmailSearchPage(_FakePage):
    """A Gmail page that is showing the result list for a submitted query."""

    def __init__(self, fragment: str | None = None, text: str = ""):
        route = "search/in%3Aanywhere+is%3Aunread+subject%3A%22confirm%22" if fragment is None else fragment
        super().__init__(f"https://mail.google.com/mail/u/0/#{route}", text=text)


class _SearchBoxLocator(_FakeLocator):
    def is_visible(self, timeout: int = 500) -> bool:
        return bool(self._elements)

    @property
    def first(self):
        return _SearchBoxLocator(self._elements[:1])


class _GmailSearchBoxPage(_GmailSearchPage):
    """A Gmail page whose search box already holds the submitted query."""

    def __init__(self, query: str, fragment: str | None = "inbox"):
        super().__init__(fragment=fragment)
        self.query = query

    def locator(self, selector: str):
        if "Search mail" in selector:
            return _SearchBoxLocator([_FakeField(self.query)])
        return super().locator(selector)


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


def test_addons_retries_a_popup_that_stays_loading():
    events = []
    observer = BrowserObserver("s1", "p1", 9222, purpose="addons", report_event=lambda *args: events.append(args))
    page = _FakePage("https://workspace.google.com/marketplace/app/1", "", "Loading")
    observer._loading_signature = (page.url, "", "Loading")
    observer._loading_since = 0.0

    with patch("app.observer.time.monotonic", return_value=46.0):
        assert _handle_addons_loading_stall(observer, page) is True

    assert observer._retry_requested is True
    assert observer._stop.is_set()
    assert events[0][1] == "addons.popup_stalled"


def test_addons_retries_when_marketplace_popup_disappears():
    events = []
    observer = BrowserObserver("s1", "p1", 9222, purpose="addons", report_event=lambda *args: events.append(args))
    observer._addons_get_opened = True
    owner = type("_FakeContext", (), {"pages": [_FakePage("https://mail.google.com/mail/u/0/#inbox", "Gmail", "Inbox")]})()

    with patch("app.observer.time.monotonic", side_effect=[100.0, 107.0]):
        assert _handle_addons_missing_surface(observer, owner) is False
        assert _handle_addons_missing_surface(observer, owner) is True

    assert observer._retry_requested is True
    assert observer._stop.is_set()
    assert events[0][1] == "addons.popup_closed"


def test_addons_retries_a_crashed_marketplace_page():
    events = []
    observer = BrowserObserver("s1", "p1", 9222, purpose="addons", report_event=lambda *args: events.append(args))
    page = _FakePage("https://workspace.google.com/marketplace/app/1", "", "Aw, Snap!")
    observer._loading_signature = (page.url, "", "Aw, Snap!")
    observer._loading_since = 0.0

    with patch("app.observer.time.monotonic", return_value=4.0):
        assert _handle_addons_loading_stall(observer, page) is True

    assert events[0][1] == "addons.popup_stalled"


def test_addons_retries_a_localized_google_403_page():
    events = []
    observer = BrowserObserver("s1", "p1", 9222, purpose="addons", report_event=lambda *args: events.append(args))
    page = _FakePage("https://workspace.google.com/marketplace/app/1", "Google", "403. Se trata de un error. Lo sentimos, no tienes acceso a esta página.")
    observer._loading_signature = (page.url, "", page._text)
    observer._loading_since = 0.0

    with patch("app.observer.time.monotonic", return_value=3.0):
        assert _handle_addons_loading_stall(observer, page) is True

    assert observer._retry_requested is True
    assert observer._stop.is_set()
    assert events[0][1] == "addons.popup_error"


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


def test_captcha_does_not_drive_still_present_sign_in_controls():
    button = _FakeButton("Continue")
    page = _FakePage("https://accounts.google.com/v3/signin/challenge", "Security check", "I'm not a robot", identifier="", buttons=[button])
    observer = BrowserObserver("captcha-session", "captcha-profile", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("reader@example.com", "secret"))

    observer._tick(_fake_browser([page]))

    assert page.cred_fields["email"].value == ""
    assert getattr(button, "clicked", False) is False
    assert not observer._stop.is_set()


def test_phone_verification_waits_even_with_saved_totp_secret():
    button = _FakeButton("Continue")
    page = _FakePage("https://accounts.google.com/v3/signin/challenge/phone", "Verify your phone number", "Enter a phone number to receive a code", otp="", buttons=[button])
    observer = BrowserObserver("phone-session", "phone-profile", 9222, observe_session_page=lambda *a: None, resolve_otp_secret=lambda sid: "JBSWY3DPEHPK3PXP")

    observer._tick(_fake_browser([page]))

    assert page.cred_fields["otp"].value == ""
    assert getattr(button, "clicked", False) is False
    assert not observer._stop.is_set()


def test_sparse_google_captcha_is_detected_from_visible_iframe():
    from app.login_policy import PageObservation, classify_page
    from app.observer import _login_challenge_hint

    page = MagicMock()
    page.locator.return_value.count.return_value = 1
    url = "https://accounts.google.com/v3/signin/challenge"
    hint = _login_challenge_hint(page, url, "accounts.google.com")

    assert hint == "captcha verification required"
    assert classify_page(PageObservation(url, "Sign in", hint)) == (PageSignal.CAPTCHA, PageAction.WAIT_FOR_HUMAN)


def test_supported_captcha_solves_in_background_then_applies_token_once():
    from app.observer import _handle_supported_captcha_tick

    observer = BrowserObserver("captcha-solver", "captcha-profile", 9222, captcha_api_key="configured-key")
    page = MagicMock()
    with patch("app.observer.detect_widget", return_value=("recaptcha_v2", "site-key")), patch("app.observer.response_present", return_value=False), patch("app.observer.solve_widget", return_value="solved-token") as solve, patch("app.observer.apply_token", return_value=True) as apply:
        assert _handle_supported_captcha_tick(observer, page, "https://example.com/signup") is True
        for _ in range(100):
            if observer._captcha_attempt["token"]:
                break
            time.sleep(0.001)
        assert _handle_supported_captcha_tick(observer, page, "https://example.com/signup") is False
        assert _handle_supported_captcha_tick(observer, page, "https://example.com/signup") is False
        solve.assert_called_once()
        apply.assert_called_once_with(page, "recaptcha_v2", "solved-token")


def test_login_policy_distinguishes_phone_challenge_from_identifier_form():
    from app.login_policy import PageObservation, classify_page

    assert classify_page(PageObservation("https://accounts.google.com/v3/signin/challenge/phone", "Google", "")) == (PageSignal.PHONE_VERIFICATION, PageAction.WAIT_FOR_HUMAN)
    assert classify_page(PageObservation("https://accounts.google.com/", "Sign in", "Email or phone")) == (PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)
    assert classify_page(PageObservation("https://accounts.google.com/v3/signin/challenge/unknown", "Google", "")) == (PageSignal.MFA_OR_SECURITY_CHECK, PageAction.WAIT_FOR_HUMAN)
    assert classify_page(PageObservation("https://accounts.google.com/v3/signin/challenge/unknown", "Sign in", "")) == (PageSignal.MFA_OR_SECURITY_CHECK, PageAction.WAIT_FOR_HUMAN)
    assert classify_page(PageObservation("https://accounts.google.com/v3/signin/challenge/pwd", "Google", "Enter your password")) == (PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE)
    recovery = "Récupération de compte. Obtenir un code de validation. Confirmez l'adresse e-mail de récupération que vous avez ajoutée à votre compte. Saisissez l'adresse e-mail de récupération."
    assert classify_page(PageObservation("https://accounts.google.com/v3/signin/challenge/selection", "Récupération de compte", recovery)) == (PageSignal.RECOVERY_VERIFICATION, PageAction.WAIT_FOR_HUMAN)
    assert classify_page(PageObservation("https://www.google.com/sorry/index", "Google", "Unusual traffic")) == (PageSignal.CAPTCHA, PageAction.WAIT_FOR_HUMAN)
    assert classify_page(PageObservation("https://mail.google.com/mail/u/0/#inbox", "Inbox", "Inbox Compose: an email about CAPTCHA and phone verification")) == (PageSignal.GMAIL_HOME, PageAction.SAFE_TO_OBSERVE)


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
    assert "https://accounts.google.com/#identifier" in observer._auto_attempted_urls


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
    observer = BrowserObserver("s1", "p1", 9222, purpose="security", observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"), resolve_otp_secret=lambda sid: "JBSWY3DPEHPK3PXP", report_event=lambda sid, event, detail, result: events.append(event))
    with patch("app.observer.classify_page", return_value=(PageSignal.MFA_OR_SECURITY_CHECK, PageAction.WAIT_FOR_HUMAN)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["otp"].value.isdigit() and len(page.cred_fields["otp"].value) == 6
    assert "otp_filled" in events
    assert getattr(button, "clicked", False) is True


def test_explicit_login_automatically_handles_mfa_when_totp_is_saved():
    page = _FakePage("https://accounts.google.com/v3/signin/challenge/totp/xyz", "Enter the code", "Enter the code from the Google Authenticator app", otp="")
    observer = BrowserObserver("s-manual-login", "p-manual-login", 9222, purpose="login", resolve_otp_secret=lambda sid: "JBSWY3DPEHPK3PXP")
    with patch("app.observer.classify_page", return_value=(PageSignal.MFA_OR_SECURITY_CHECK, PageAction.WAIT_FOR_HUMAN)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["otp"].value.isdigit() and len(page.cred_fields["otp"].value) == 6
    assert not observer._stop.is_set()


def test_explicit_login_waits_for_manual_mfa_when_totp_is_missing():
    page = _FakePage("https://accounts.google.com/v3/signin/challenge/totp/xyz", "Enter the code", "Enter the code from the Google Authenticator app", otp="")
    observer = BrowserObserver("s-manual-mfa", "p-manual-mfa", 9222, purpose="login", resolve_otp_secret=lambda sid: None)
    with patch("app.observer.classify_page", return_value=(PageSignal.MFA_OR_SECURITY_CHECK, PageAction.WAIT_FOR_HUMAN)):
        observer._tick(_fake_browser([page]))
    assert page.cred_fields["otp"].value == ""
    assert not observer._stop.is_set()


def test_explicit_login_does_not_auto_solve_captcha():
    page = _FakePage("https://accounts.google.com/v3/signin/challenge", "Security check", "I'm not a robot")
    observer = BrowserObserver("s-manual-captcha", "p-manual-captcha", 9222, purpose="login", captcha_api_key="configured-key")
    with patch("app.observer.classify_page", return_value=(PageSignal.CAPTCHA, PageAction.WAIT_FOR_HUMAN)), patch("app.observer._handle_supported_captcha_tick") as solve:
        observer._tick(_fake_browser([page]))
    solve.assert_not_called()
    assert not observer._stop.is_set()


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
    observer = BrowserObserver("s1", "p1", 9222, purpose="security", observe_session_page=lambda *a: None, resolve_otp_secret=lambda sid: None, report_event=lambda sid, event, detail, result: events.append(event))
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


def test_observer_clicks_french_use_another_account_on_chooser():
    button = _FakeButton("Utiliser un autre compte")
    page = _FakePage("https://accounts.google.com/v3/signin/accountchooser", "Sélectionnez un compte", "Sélectionnez un compte", buttons=[button])
    observer = BrowserObserver("s-fr", "p-fr", 9222, observe_session_page=lambda *a: None, resolve_credentials=lambda sid: ("alice@example.com", "s3cret"), report_event=lambda *a: None)
    with patch("app.observer.classify_page", return_value=(PageSignal.ORDINARY_INTERSTITIAL, PageAction.SAFE_TO_OBSERVE)):
        observer._tick(_fake_browser([page]))
    assert getattr(button, "clicked", False) is True
    assert "https://accounts.google.com/v3/signin/accountchooser" in observer._chooser_clicked_urls


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


def test_newsletter_unknown_public_page_reaches_profile_handler_without_attention():
    seen = []
    page = _FakePage("https://example.com/newsletter", "Newsletter", "Sign up for updates")
    observer = BrowserObserver("s1", "p1", 9222, purpose="newsletter", observe_session_page=lambda *a: seen.append(a), settle_seconds=0.0)
    with patch("app.observer.classify_page", return_value=(PageSignal.UNKNOWN, PageAction.NEEDS_ATTENTION)), patch("app.observer._handle_newsletter_signup_tick") as handler:
        observer._tick(_fake_browser([page]))
    handler.assert_called_once()
    assert seen == []
    assert not observer._stop.is_set()


def test_newsletter_task_deadline_marks_only_the_stalled_item_for_review():
    events = []
    observer = BrowserObserver(
        "stalled-newsletter-session",
        "newsletter-profile",
        9222,
        purpose="newsletter",
        report_event=lambda _sid, event, detail, result: events.append((event, detail, result)),
    )
    observer._newsletter_started_at = time.monotonic() - 181

    observer._tick(_fake_browser([]))

    assert observer._stop.is_set()
    assert events and events[-1][0] == "newsletter.signup.manual_review"
    assert "3-minute task limit" in events[-1][1]


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
    assert "input#password:visible" in credential_parts
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


class _FakeTwoStepPage:
    def __init__(self, drive_result: dict, fill_result: str = "submitted"):
        self.url = "https://myaccount.google.com/two-step-verification/authenticator"
        self.drive_result = drive_result
        self.fill_result = fill_result
        self.fill_calls = 0

    def evaluate(self, script: str, _config: dict):
        if "rejectionTerms" in script:
            self.fill_calls += 1
            return self.fill_result
        return self.drive_result


def test_two_step_setup_uses_the_authenticator_url_and_matches_legacy_path():
    assert _TWO_STEP_URL == "https://myaccount.google.com/two-step-verification/authenticator"
    assert any(path == "/signinoptions/two-step-verification" for path in _TWO_STEP_PAGE_PATHS)


def test_two_step_labels_target_the_manual_secret_key_option():
    labels = " ".join(_TWO_STEP_MANUAL_LABELS)
    assert "secret key" in labels or "setup key" in labels


def test_two_step_tick_captures_googles_key_and_replaces_the_generated_secret():
    events: list[str] = []
    stored: dict[str, object] = {}

    def store(_sid: str, material: dict):
        stored["material"] = material

    observer = BrowserObserver(
        "s-two-step", "p-two-step", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"2fa": "SERVERSECRET123456", "two_fa_requested": True},
        store_credential_change=store,
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    page = _FakeTwoStepPage({"state": "waiting", "key": "JBSWY3DPEHPK3PXP", "action": "key"})

    _handle_credential_change_tick(observer, page, "myaccount.google.com")

    assert observer._two_step_key == "JBSWY3DPEHPK3PXP"
    assert stored["material"]["2fa"] == "JBSWY3DPEHPK3PXP"
    assert observer._two_step_code_attempts == 1
    assert page.fill_calls == 1
    assert "two_step.secret_captured" in events


def test_two_step_tick_marks_an_existing_authenticator_as_removed():
    observer = BrowserObserver(
        "s-two-step", "p-two-step", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"2fa": "SERVERSECRET123456", "two_fa_requested": True},
    )
    page = _FakeTwoStepPage({"state": "waiting", "key": None, "action": "remove"})

    _handle_credential_change_tick(observer, page, "myaccount.google.com")

    assert observer._two_step_removed is True


def test_two_step_tick_fills_the_current_account_code_on_a_reauth_prompt():
    events: list[str] = []
    observer = BrowserObserver(
        "s-two-step-reauth", "p-two-step-reauth", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"2fa": "SERVERSECRET123456", "two_fa_requested": True},
        resolve_otp_secret=lambda _sid: "JBSWY3DPEHPK3PXP",
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    page = _FakeTwoStepPage({"state": "waiting", "key": None, "action": "reauth-code"})

    _handle_credential_change_tick(observer, page, "myaccount.google.com")

    assert page.fill_calls == 1  # the stored (current) TOTP was entered
    assert observer._two_step_code_attempts == 1
    assert observer._two_step_key is None  # no new secret is captured from a re-auth prompt
    assert observer._two_step_removed is False


def test_two_step_tick_does_not_resubmit_or_restart_after_new_code_submission():
    class _SettlingPage(_FakeTwoStepPage):
        def __init__(self):
            super().__init__({"state": "waiting", "key": None, "action": "code"}, fill_result="submitted")
            self.drive_calls = 0

        def evaluate(self, script: str, config: dict):
            if "rejectionTerms" in script:
                self.fill_calls += 1
                return self.fill_result
            self.drive_calls += 1
            if self.drive_calls == 1:
                return {"state": "waiting", "key": None, "action": "code", "scan": {"hasCodeInput": True}}
            # Google has accepted the OTP, but its success surface is still
            # rendering. A later tick must not click Remove/Add or Verify again.
            return {"state": "waiting", "key": None, "action": "verification-pending", "scan": {"hasCodeInput": True}}

    observer = BrowserObserver(
        "s-two-step-settling", "p-two-step-settling", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"2fa": "JBSWY3DPEHPK3PXP", "two_fa_requested": True},
    )
    observer._two_step_key = "JBSWY3DPEHPK3PXP"
    page = _SettlingPage()

    _handle_credential_change_tick(observer, page, "myaccount.google.com")
    _handle_credential_change_tick(observer, page, "myaccount.google.com")

    assert observer._two_step_code_submitted is True
    assert page.drive_calls == 2
    assert page.fill_calls == 1
    assert observer._stop.is_set() is False


def test_two_step_tick_fails_when_the_reauth_code_is_rejected_repeatedly():
    events: list[str] = []
    observer = BrowserObserver(
        "s-two-step-reauth-reject", "p-two-step-reauth-reject", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"2fa": "SERVERSECRET123456", "two_fa_requested": True},
        resolve_otp_secret=lambda _sid: "JBSWY3DPEHPK3PXP",
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    page = _FakeTwoStepPage({"state": "waiting", "key": None, "action": "reauth-code"}, fill_result="rejected")

    for _ in range(6):
        _handle_credential_change_tick(observer, page, "myaccount.google.com")

    assert "credential_change.failed" in events
    assert observer._stop.is_set()


def test_two_step_tick_completes_once_google_confirms_the_authenticator():
    events: list[str] = []
    observer = BrowserObserver(
        "s-two-step", "p-two-step", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"2fa": "SERVERSECRET123456", "two_fa_requested": True},
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    observer._two_step_key = "JBSWY3DPEHPK3PXP"
    page = _FakeTwoStepPage({"state": "done", "key": None, "action": None})

    _handle_credential_change_tick(observer, page, "myaccount.google.com")

    assert "credential_change.completed" in events
    assert observer._stop.is_set()


def test_two_step_tick_reports_the_page_scan_when_it_stalls():
    events: list[str] = []
    observer = BrowserObserver(
        "s-two-step-scan", "p-two-step-scan", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"2fa": "SERVERSECRET123456", "two_fa_requested": True},
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    page = _FakeTwoStepPage({
        "state": "waiting",
        "key": None,
        "action": "idle",
        "scan": {"hasPassword": False, "hasSetupKey": False, "hasCodeInput": False, "hasDialog": False, "hasExistingRow": True, "hasGetStarted": False, "hasManual": False},
    })

    _handle_credential_change_tick(observer, page, "myaccount.google.com")
    assert "two_step.scan" in events
    assert observer._stop.is_set() is False  # a stall is reported, not fatal

    _handle_credential_change_tick(observer, page, "myaccount.google.com")
    assert events.count("two_step.scan") == 1  # same scan is not re-emitted every tick


def test_two_fa_only_plan_routes_to_the_authenticator_page_not_the_password_page():
    calls: list[str] = []

    class _NavPage:
        url = "https://mail.google.com/mail/u/0/"

        def goto(self, url: str, **_kwargs):
            calls.append(url)

    observer = BrowserObserver(
        "s-two-step", "p-two-step", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"2fa": "JBSWY3DPEHPK3PXP", "two_fa_requested": True},
    )

    _handle_credential_change_tick(observer, _NavPage(), "mail.google.com")

    assert calls == ["https://myaccount.google.com/two-step-verification/authenticator"]


def test_combined_plan_starts_with_the_password_page_then_hands_off_to_two_fa():
    calls: list[str] = []

    class _NavPage:
        url = "https://mail.google.com/mail/u/0/"

        def goto(self, url: str, **_kwargs):
            calls.append(url)

    observer = BrowserObserver(
        "s-combined", "p-combined", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"password": "NewPass123!", "2fa": "SERVERSECRET123456", "password_requested": True, "two_fa_requested": True},
    )

    _handle_credential_change_tick(observer, _NavPage(), "mail.google.com")

    assert calls == ["https://myaccount.google.com/signinoptions/password"]


def test_completion_never_fires_until_google_confirms_each_requested_field():
    events: list[str] = []
    observer = BrowserObserver(
        "s-guard", "p-guard", 9222, purpose="credential_change",
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    page = _FakeTwoStepPage({"state": "waiting", "key": None, "action": "code"})

    _complete_credential_change(observer, page, {"password": "NewPass123!", "password_requested": True}, "the new password was applied")

    assert "credential_change.completed" not in events
    assert "credential_change.failed" in events
    assert observer._stop.is_set()


def test_password_leg_reports_success_even_when_the_two_fa_leg_is_still_pending():
    events: list[str] = []
    observer = BrowserObserver(
        "s-pw-leg", "p-pw-leg", 9222, purpose="credential_change",
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    page = _FakeTwoStepPage({"state": "waiting", "key": None, "action": "idle"})

    _finish_password_change(observer, page, {"password": "NewPass123!", "password_requested": True, "two_fa_requested": True})

    assert observer._password_change_confirmed is True
    assert "password.change_completed" in events  # password persisted independently
    assert "credential_change.completed" not in events  # 2FA leg has not finished yet


def test_password_only_change_completes_through_the_shared_finisher():
    events: list[str] = []
    observer = BrowserObserver(
        "s-pw-only", "p-pw-only", 9222, purpose="credential_change",
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    page = _FakeTwoStepPage({"state": "waiting", "key": None, "action": "idle"})

    _finish_password_change(observer, page, {"password": "NewPass123!", "password_requested": True})

    assert "password.change_completed" in events
    assert "credential_change.completed" in events  # no 2FA leg: the plan finishes here


def test_password_tick_marks_an_enter_submit_as_in_flight():
    observer = BrowserObserver(
        "s-pw-enter", "p-pw-enter", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"password": "NewPass123!", "password_requested": True},
        resolve_current_password=lambda _sid: "old-secret",
    )
    page = _FakeTwoStepPage("keyboard2")
    page.url = "https://myaccount.google.com/signinoptions/password"

    _handle_credential_change_tick(observer, page, "myaccount.google.com")

    # The final submit went through Enter: detection must start instead of the
    # tick re-driving (and re-submitting) the form forever.
    assert observer._password_change_clicked is True
    assert observer._credential_change_stage == 2


def test_password_tick_accepts_a_settled_submit_without_a_success_banner():
    events: list[str] = []
    observer = BrowserObserver(
        "s-pw-likely", "p-pw-likely", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"password": "NewPass123!", "password_requested": True},
        resolve_current_password=lambda _sid: "old-secret",
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    page = _FakeTwoStepPage("likely")
    page.url = "https://myaccount.google.com/signinoptions/password"
    observer._password_change_clicked = True

    for _ in range(3):
        _handle_credential_change_tick(observer, page, "myaccount.google.com")

    assert observer._password_change_confirmed is False  # still settling
    _handle_credential_change_tick(observer, page, "myaccount.google.com")
    assert observer._password_change_confirmed is True
    assert "credential_change.completed" in events


def test_password_success_matcher_covers_variant_and_localized_phrases():
    import re

    # The matcher is authored for the browser engine ([^] matches any char);
    # translate that one construct so Python can validate the phrase coverage.
    pattern = re.compile(_PASSWORD_CHANGE_SUCCESS_JS_RE.replace("[^]", r"[\s\S]"))
    for phrase in (
        "password changed",
        "Password changed successfully",
        "your password has been updated",
        "passwort wurde geandert",
        "senha alterada com sucesso",
        "mot de passe a ete modifie",
        "パスワードを変更しました",
        "비밀번호가 변경되었습니다",
        "密码已更改",
        "пароль был изменен",
    ):
        assert pattern.search(phrase.lower()), phrase


def test_two_step_tick_still_drives_the_legacy_signinoptions_path():
    observer = BrowserObserver(
        "s-two-step", "p-two-step", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"2fa": "SERVERSECRET123456", "two_fa_requested": True},
    )
    page = _FakeTwoStepPage({"state": "waiting", "key": None, "action": "manual"})
    page.url = "https://myaccount.google.com/signinoptions/two-step-verification"

    _handle_credential_change_tick(observer, page, "myaccount.google.com")

    assert _drive_two_step_setup(page, None)["state"] == "waiting"


def test_fill_two_step_code_returns_the_submit_status():
    page = _FakeTwoStepPage({"state": "waiting", "key": None, "action": None}, fill_result="submitted")

    assert _fill_two_step_code(page, "JBSWY3DPEHPK3PXP") == "submitted"
    assert page.fill_calls == 1


def test_two_step_page_paths_include_googles_new_twosv_slug():
    assert _TWO_STEP_URL == "https://myaccount.google.com/two-step-verification/authenticator"
    assert any(path == "/signinoptions/twosv" for path in _TWO_STEP_PAGE_PATHS)
    assert any(path == "/signinoptions/two-step-verification" for path in _TWO_STEP_PAGE_PATHS)


def _label_matches(label: str, terms: tuple[str, ...]) -> bool:
    import unicodedata

    def normalize(value: str) -> str:
        stripped = unicodedata.normalize("NFD", value).encode("ascii", "ignore").decode()
        return " ".join(stripped.split()).lower()

    normalized = normalize(label)
    return any(
        normalized == term or normalized.startswith(term + " ") or normalized.endswith(" " + term)
        for term in (normalize(term) for term in terms)
    )


def test_two_step_remove_detects_the_localized_authenticator_row():
    # Spanish management page (the user's container): the row is named
    # "Autenticador" and its trash button is aria-labelled
    # "Quitar el Autenticador de Google". Detection must recognise it as an
    # existing authenticator AND as the Remove affordance.
    delete_label = "Quitar el Autenticador de Google"
    assert any(term in delete_label.lower() for term in _TWO_STEP_AUTH_NAME_TERMS)
    assert _label_matches(delete_label, _TWO_STEP_REMOVE_LABELS)
    assert any("autenticador" in term for term in _TWO_STEP_AUTH_NAME_TERMS)


def test_two_step_change_affordance_is_not_mistaken_for_remove():
    # "Cambiar la app de autenticador" (Change authenticator app) sits before
    # the trash button; it must map to the edit affordance, never Remove.
    change_label = "Cambiar la app de autenticador"
    assert not _label_matches(change_label, _TWO_STEP_REMOVE_LABELS)
    assert _label_matches(change_label, _TWO_STEP_ROW_EDIT_LABELS)


def test_two_step_confirm_remove_matches_the_popup_button():
    assert _label_matches("Quitar", _TWO_STEP_CONFIRM_REMOVE_LABELS)
    assert _label_matches("Remove authenticator", _TWO_STEP_CONFIRM_REMOVE_LABELS)


def test_two_step_manual_option_matches_the_punctuated_i_cant_scan_it():
    import re
    import unicodedata

    def js_normalize(value: str) -> str:
        stripped = unicodedata.normalize("NFD", value).encode("ascii", "ignore").decode()
        stripped = re.sub(r"""[?¿!¡.,:;()\[\]{}"'\u00ab\u00bb\u2026]""", " ", stripped)
        return " ".join(stripped.split()).lower()

    def matches(label: str) -> bool:
        normalized = js_normalize(label)
        return any(
            normalized == term or normalized.startswith(term + " ") or normalized.endswith(" " + term)
            for term in (js_normalize(term) for term in _TWO_STEP_MANUAL_LABELS)
        )

    # Google renders the manual-key affordance with a trailing "?" and the
    # leading "I"; both used to defeat the strict label match.
    assert matches("Can't scan it?")
    assert matches("I can't scan it")
    assert matches("Cannot scan the QR code")
    assert matches("No puedes escanearlo?")
    assert matches("Je ne peux pas scanner le code")
    assert matches("¿No se puede escanear?")
    assert matches("Ne peut-on pas le scanner ?")
    assert matches("Can't it be scanned?")
    assert matches("can't scan it?")


def test_two_step_manual_keyword_fallback_covers_passive_locale_phrases():
    import re
    import unicodedata

    def js_normalize(value: str) -> str:
        stripped = unicodedata.normalize("NFD", value).encode("ascii", "ignore").decode()
        stripped = re.sub(r"""[?¿!¡.,:;()\[\]{}"'\u00ab\u00bb\u2026]""", " ", stripped)
        return " ".join(stripped.split()).lower()

    # Mirrors the in-page fallback predicate: ANY visible label coupling a scan
    # keyword with a cannot/not phrasing is the manual-key option, even when it
    # is not (yet) an exact list term.
    def predicate(label: str) -> bool:
        normalized = js_normalize(label)
        if not normalized:
            return False
        return bool(re.search(r"(scan|scann|escan)", normalized)) and bool(
            re.search(r"(\bcan\s*'?\s*t\b|cannot|not scan|no se puede|no puedes|no puedo|ne peut|pas scanner|impossible|trouble|problem)", normalized)
        )

    for phrase in (
        "¿No se puede escanear?",
        "Ne peut-on pas le scanner ?",
        "Can't it be scanned?",
        "can't scan it?",
        "No puedes escanear el código QR?",
        "Vous ne pouvez pas scanner le code ?",
    ):
        assert predicate(phrase), phrase

    # A plain scanning hint or unrelated link must never be mistaken for it.
    assert not predicate("Scan this QR code with your phone")
    assert not predicate("Use Google Authenticator")


def test_recovery_only_plan_navigates_to_the_recovery_email_page():
    calls: list[str] = []

    class _NavPage:
        url = "https://mail.google.com/mail/u/0/"

        def goto(self, url: str, **_kwargs):
            calls.append(url)

    observer = BrowserObserver(
        "s-recovery", "p-recovery", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"recovery": "rotated@example.com", "recovery_requested": True},
    )

    _handle_credential_change_tick(observer, _NavPage(), "mail.google.com")

    assert calls == [_RECOVERY_URL]
    assert any(path == "/recovery/email" for path in _RECOVERY_PAGE_PATHS)


def test_recovery_tick_confirms_after_google_accepts_the_new_address():
    events: list[str] = []
    observer = BrowserObserver(
        "s-recovery-done", "p-recovery-done", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"recovery": "rotated@example.com", "recovery_requested": True},
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    page = _FakeTwoStepPage({"state": "done", "action": "success"})
    page.url = "https://myaccount.google.com/recovery/email"

    _handle_credential_change_tick(observer, page, "myaccount.google.com")

    assert observer._recovery_change_confirmed is True
    assert "recovery.change_completed" in events


def test_recovery_leg_must_finish_before_the_plan_can_complete():
    observer = BrowserObserver("s-recovery-guard", "p-recovery-guard", 9222, purpose="credential_change")
    material = {"recovery_requested": True, "recovery": "rotated@example.com"}

    assert _credential_change_is_confirmed(observer, material) is False
    observer._recovery_change_confirmed = True
    assert _credential_change_is_confirmed(observer, material) is True


def test_drive_recovery_page_fills_the_address_then_saves():
    class _RecoveryPage:
        def __init__(self):
            self.calls = 0

        def evaluate(self, _script: str, _config: dict):
            self.calls += 1
            if self.calls == 1:
                return {"state": "waiting", "action": "email-filled"}
            if self.calls == 2:
                return {"state": "waiting", "action": "save"}
            return {"state": "done", "action": "success"}

    page = _RecoveryPage()
    assert _drive_recovery_page(page, "rotated@example.com")["action"] == "email-filled"
    assert _drive_recovery_page(page, "rotated@example.com")["action"] == "save"
    assert _drive_recovery_page(page, "rotated@example.com")["state"] == "done"


def test_drive_recovery_page_forwards_the_saved_flag():
    captured: dict = {}

    class _CapturePage:
        def evaluate(self, _script: str, config: dict):
            captured.update(config)
            return {"state": "waiting", "action": "idle"}

    _drive_recovery_page(_CapturePage(), "rotated@example.com", already_saved=True)
    assert captured["alreadySaved"] is True


def test_recovery_tick_remembers_the_save_so_it_never_reopens_the_form():
    observer = BrowserObserver(
        "s-recovery-save", "p-recovery-save", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"recovery": "rotated@example.com", "recovery_requested": True},
    )
    page = _FakeTwoStepPage({"state": "waiting", "action": "save"})
    page.url = "https://myaccount.google.com/recovery/email"

    _handle_credential_change_tick(observer, page, "myaccount.google.com")

    assert observer._recovery_saved is True
    assert observer._recovery_change_confirmed is False


def test_password_in_flight_confirmation_runs_on_a_login_host():
    events: list[str] = []
    observer = BrowserObserver(
        "s-pw-loginhost", "p-pw-loginhost", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"password": "NewPass123!", "password_requested": True},
        resolve_current_password=lambda _sid: "old-secret",
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )
    page = _FakeTwoStepPage("done")
    # Google briefly lands the post-change confirmation on the sign-in host.
    page.url = "https://accounts.google.com/signin/v2/challenge/pwd"
    observer._password_change_clicked = True

    _handle_credential_change_tick(observer, page, "accounts.google.com")

    # The host guards must not swallow the in-flight confirmation.
    assert observer._password_change_confirmed is True
    assert "password.change_completed" in events


def test_password_in_flight_re_auth_uses_the_new_password():
    observer = BrowserObserver(
        "s-pw-reauth", "p-pw-reauth", 9222, purpose="credential_change",
        resolve_credential_change=lambda _sid: {"password": "NewPass123!", "password_requested": True},
        resolve_current_password=lambda _sid: "old-secret",
    )
    url = "https://accounts.google.com/signin/v2/challenge/pwd"
    page = _FakeTwoStepPage("pending")
    page.url = url
    observer._password_change_clicked = True
    observer._credentials = ("user@example.com", "old-secret")
    observer._credentials_tried = True
    observer._auto_attempted_urls.add(url)
    observer._submitted_urls.add(url)

    _handle_credential_change_tick(observer, page, "accounts.google.com")

    # Re-auth must use the freshly provisioned password, not the stale stored one.
    assert observer._credentials == ("user@example.com", "NewPass123!")
    assert url not in observer._auto_attempted_urls
    assert url not in observer._submitted_urls


def test_mail_reply_search_uses_all_gmail_folders():
    assert _mail_subject_query({"action": "reply", "subject": "Quarterly planning"}) == 'in:anywhere subject:"Quarterly planning"'


def test_mail_confirmation_review_searches_all_gmail_folders():
    assert _mail_subject_query({"action": "confirmation_review", "subject": "Confirm your subscription"}) == 'in:anywhere is:unread subject:"Confirm your subscription"'


def test_mail_confirmation_review_can_target_only_read_emails():
    assert _mail_subject_query({"action": "confirmation_review", "review_email_state": "read", "subject": "Confirm your subscription"}) == 'in:anywhere is:read subject:"Confirm your subscription"'


def test_mail_confirmation_review_summarizes_visible_controls_without_exposing_content():
    captured: dict = {}

    class _ReviewPage:
        def evaluate(self, script: str, confirmation_cues: list[str]):
            captured["script"] = script
            captured["confirmation_cues"] = confirmation_cues
            return {"controlCount": 4, "confirmationCount": 1, "buttonCount": 1, "textLinkCount": 3}

    summary = _mail_review_control_summary(_ReviewPage())  # type: ignore[arg-type]

    assert summary == {"control_count": 4, "confirmation_count": 1, "button_count": 1, "text_link_count": 3}
    assert "subscribe" in captured["confirmation_cues"]
    assert "href" in captured["script"]
    assert "control.click" not in captured["script"]


def test_mail_confirmation_review_waits_for_stable_control_scan_before_marking_ready():
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-controls",
        "profile-review-controls",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "subject": "Confirm your subscription"},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    observer._mail_search_submitted = True
    observer._mail_review_collection_done = True
    observer._mail_review_candidates = [{"key": "gmail:thread-one", "href": "#all/thread-one"}]
    summary = {"control_count": 3, "confirmation_count": 1, "button_count": 1, "text_link_count": 2}

    with patch("app.observer._mail_navigate_to_review_candidate", return_value=True), patch("app.observer._mail_click_confirmation_control", return_value=None), patch("app.observer._mail_review_control_summary", return_value=summary):
        _handle_mail_tick(observer, MagicMock(), "mail.google.com")  # opens the matched message
        _handle_mail_tick(observer, MagicMock(), "mail.google.com")  # first scan
        assert observer._mail_review_ready is False
        for _ in range(_MAIL_CONFIRMATION_CONTROL_WAIT_TICKS + 1):
            _handle_mail_tick(observer, MagicMock(), "mail.google.com")

    assert observer._mail_review_ready is True
    assert [event for event, _detail, _result, _metadata in events] == [
        "mail.matches_found",
        "mail.thread_opened",
        "mail.review_controls_detected",
        "mail.confirmation_control_unavailable",
        "mail.review_ready",
    ]
    assert events[-1][3] == {
        "message_controls_rendered": True,
        "reviewed_message_count": 1,
        "visible_message_controls": 3,
        "candidate_confirmation_controls": 1,
        "visible_message_buttons": 1,
        "visible_message_text_links": 2,
        "message_index": 1,
        "confirmed_messages": 0,
        "messages_needing_review": 1,
    }


def test_mail_confirmation_review_queue_confirms_each_matched_message_from_captured_routes():
    """The queue keeps its matches after Gmail drops the opened thread.

    A read-state filtered search loses a message once it is opened, so the
    remaining matches are re-opened from the routes captured before the first
    message was opened instead of from a re-run search.
    """
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-queue",
        "profile-review-queue",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 2},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    observer._mail_search_submitted = True
    opened: list[str] = []

    def _navigate(_page, candidate):
        opened.append(candidate["key"])
        return True

    candidates = [{"key": "gmail:thread-one", "href": "#all/thread-one"}, {"key": "gmail:thread-two", "href": "#all/thread-two"}]

    def _rows(_page, _config):
        # Gmail empties the result list as soon as a message is opened.
        return candidates if not opened else []

    summary = {"control_count": 3, "confirmation_count": 1, "button_count": 1, "text_link_count": 2}
    landing = _FakePage("https://news.example.com/confirm?token=one", text="Thanks for confirming. Subscription confirmed.")

    with patch("app.observer._mail_review_candidate_rows", side_effect=_rows), patch("app.observer._mail_navigate_to_review_candidate", side_effect=_navigate), patch("app.observer._mail_review_control_summary", return_value=summary), patch("app.observer._mail_click_confirmation_control", return_value="button"), patch("app.observer._mail_confirmation_landing_target", side_effect=lambda _observer, _page, _host: landing if (observer._mail_review_awaiting_landing or observer._mail_review_landing_page is landing) else None):
        for _ in range(20):
            _handle_mail_tick(observer, _GmailSearchPage(), "mail.google.com")
            if observer._stop.is_set():
                break

    assert opened == ["gmail:thread-one", "gmail:thread-two"]
    assert observer._mail_review_ready is False
    assert observer._mail_review_processed_count == 2
    assert observer._mail_review_confirmed_count == 2
    assert observer._stop.is_set()
    assert [event for event, _detail, _result, _metadata in events].count("mail.confirmation_clicked") == 2
    assert [event for event, _detail, _result, _metadata in events][-2:] == ["mail.confirmation_succeeded", "mail.completed"]
    assert events[-1][3] == {
        "reviewed_message_count": 2,
        "candidate_confirmation_messages": 2,
        "messages_without_rendered_controls": 0,
        "visible_message_controls": 6,
        "visible_message_buttons": 2,
        "visible_message_text_links": 4,
        "confirmation_links_followed": 2,
        "matched_confirmation_emails": 2,
        "confirmed_messages": 2,
        "messages_needing_review": 0,
    }


def test_mail_confirmation_review_queue_keeps_going_after_a_message_without_a_usable_control():
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-mixed",
        "profile-review-mixed",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 3},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    observer._mail_search_submitted = True
    summaries = [
        {"control_count": 2, "confirmation_count": 0, "button_count": 0, "text_link_count": 2},
        {"control_count": 3, "confirmation_count": 1, "button_count": 1, "text_link_count": 2},
    ]
    candidates = [{"key": "gmail:thread-one", "href": "#all/thread-one"}, {"key": "gmail:thread-two", "href": "#all/thread-two"}]
    landing = _FakePage("https://news.example.com/confirm", text="Thanks for confirming. Subscription confirmed.")

    with patch("app.observer._mail_review_candidate_rows", return_value=candidates), patch("app.observer._mail_review_scroll_results", return_value=False), patch("app.observer._mail_navigate_to_review_candidate", return_value=True), patch("app.observer._mail_review_control_summary", side_effect=lambda _page: summaries[0] if observer._mail_review_processed_count == 0 else summaries[1]), patch("app.observer._mail_click_confirmation_control", side_effect=lambda _page: None if observer._mail_review_processed_count == 0 else "text_link"), patch("app.observer._mail_confirmation_landing_target", side_effect=lambda _observer, _page, _host: landing if (observer._mail_review_awaiting_landing or observer._mail_review_landing_page is landing) else None):
        for _ in range(40):
            _handle_mail_tick(observer, _GmailSearchPage(), "mail.google.com")
            if observer._stop.is_set():
                break

    names = [event for event, _detail, _result, _metadata in events]
    assert names.count("mail.confirmation_control_missing") == 1
    assert names.count("mail.confirmation_succeeded") == 1
    assert observer._mail_review_processed_count == 2
    assert observer._mail_review_confirmed_count == 1
    assert observer._mail_review_attention_count == 1
    assert observer._stop.is_set()
    # A run that confirmed only part of the queue is reported as a failure with
    # both counts, never as a completed subscription.
    assert names[-1] == "mail.failed"
    assert "1 of 2" in events[-1][1]
    assert "1 still need review" in events[-1][1]
    assert events[-1][3]["messages_needing_review"] == 1
    assert events[-1][3]["confirmed_messages"] == 1


def test_mail_confirmation_review_queue_completes_when_no_captured_match_is_left():
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-empty-search",
        "profile-review-empty-search",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 5},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    observer._mail_search_submitted = True
    observer._mail_review_collection_done = True
    observer._mail_review_candidates = [{"key": "gmail:thread-one", "href": "#all/thread-one"}]
    observer._mail_review_processed_count = 1
    observer._mail_review_confirmed_count = 1
    observer._mail_review_candidate_count = 1

    _handle_mail_tick(
        observer,
        _FakePage("https://mail.google.com/mail/u/0/#search", text="No messages matched your search. Try using search options."),
        "mail.google.com",
    )

    assert observer._stop.is_set()
    assert events[-1][0] == "mail.completed"
    assert events[-1][3]["reviewed_message_count"] == 1
    assert events[-1][3]["candidate_confirmation_messages"] == 1
    assert events[-1][3]["matched_confirmation_emails"] == 1


def test_mail_confirmation_review_follows_a_multi_step_confirmation_page_to_success():
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-steps",
        "profile-review-steps",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 1},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    observer._mail_search_submitted = True
    observer._mail_review_collection_done = True
    observer._mail_review_candidates = [{"key": "gmail:thread-one", "href": "#all/thread-one"}]
    summary = {"control_count": 1, "confirmation_count": 1, "button_count": 1, "text_link_count": 0}
    first = _FakePage("https://news.example.com/confirm", text="Review your subscription and continue")
    done = _FakePage("https://news.example.com/confirm/done", text="Subscription confirmed")

    def _landing(_observer, _page, _host):
        if observer._mail_review_landing_steps >= 2:
            return done
        return first if (observer._mail_review_awaiting_landing or observer._mail_review_landing_page is not None) else None

    with patch("app.observer._mail_navigate_to_review_candidate", return_value=True), patch("app.observer._mail_review_control_summary", return_value=summary), patch("app.observer._mail_click_confirmation_control", return_value="button"), patch("app.observer._mail_confirmation_landing_target", side_effect=_landing), patch("app.observer._mail_click_landing_step", return_value=True) as landing_step, patch("app.observer._mail_visible_text_terms", side_effect=lambda page, terms: ("subscription confirmed",) if (terms is _MAIL_CONFIRMATION_SUCCESS_TERMS and page is done) else ()):
        for _ in range(20):
            _handle_mail_tick(observer, MagicMock(), "mail.google.com")
            if observer._stop.is_set():
                break

    names = [event for event, _detail, _result, _metadata in events]
    assert landing_step.call_count == 2
    assert names.count("mail.confirmation_step_completed") == 2
    assert names[-1] == "mail.completed"
    assert observer._mail_review_confirmed_count == 1
    assert observer._mail_review_confirmation_steps == 3
    assert events[-1][3]["confirmed_messages"] == 1


def test_mail_confirmation_review_reports_a_confirmation_page_that_refuses_the_request():
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-refused",
        "profile-review-refused",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 1},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    observer._mail_search_submitted = True
    observer._mail_review_collection_done = True
    observer._mail_review_candidates = [{"key": "gmail:thread-one", "href": "#all/thread-one"}]
    summary = {"control_count": 1, "confirmation_count": 1, "button_count": 1, "text_link_count": 0}
    refused = _FakePage("https://news.example.com/confirm", text="This link has expired")

    with patch("app.observer._mail_navigate_to_review_candidate", return_value=True), patch("app.observer._mail_review_control_summary", return_value=summary), patch("app.observer._mail_click_confirmation_control", return_value="button"), patch("app.observer._mail_confirmation_landing_target", side_effect=lambda _observer, _page, _host: refused if (observer._mail_review_awaiting_landing or observer._mail_review_landing_page is refused) else None):
        for _ in range(20):
            _handle_mail_tick(observer, MagicMock(), "mail.google.com")
            if observer._stop.is_set():
                break

    names = [event for event, _detail, _result, metadata in events]
    assert "mail.confirmation_failed" in names
    assert names[-1] == "mail.failed"
    assert observer._mail_review_confirmed_count == 0
    assert observer._mail_review_attention_count == 1
    assert events[-1][3]["messages_needing_review"] == 1


def test_mail_confirmation_review_reports_a_sign_in_page_instead_of_waiting_for_it():
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-signin",
        "profile-review-signin",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 1},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    observer._mail_search_submitted = True
    observer._mail_review_collection_done = True
    observer._mail_review_candidates = [{"key": "gmail:thread-one", "href": "#all/thread-one"}]
    summary = {"control_count": 1, "confirmation_count": 1, "button_count": 1, "text_link_count": 0}
    sign_in = _FakePage("https://accounts.google.com/ServiceLogin", text="Sign in to continue")

    with patch("app.observer._mail_navigate_to_review_candidate", return_value=True), patch("app.observer._mail_review_control_summary", return_value=summary), patch("app.observer._mail_click_confirmation_control", return_value="button"), patch("app.observer._mail_confirmation_landing_target", side_effect=lambda _observer, _page, _host: sign_in if observer._mail_review_awaiting_landing else None):
        for _ in range(20):
            _handle_mail_tick(observer, MagicMock(), "mail.google.com")
            if observer._stop.is_set():
                break

    names = [event for event, _detail, _result, _metadata in events]
    assert names.count("mail.confirmation_clicked") == 1
    assert "mail.confirmation_message_review" in names
    assert names[-1] == "mail.failed"
    assert observer._mail_review_attention_count == 1


def test_mail_confirmation_review_reports_a_confirmation_control_that_opens_nothing():
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-nopage",
        "profile-review-nopage",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 1},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    observer._mail_search_submitted = True
    observer._mail_review_collection_done = True
    observer._mail_review_candidates = [{"key": "gmail:thread-one", "href": "#all/thread-one"}]
    summary = {"control_count": 1, "confirmation_count": 1, "button_count": 1, "text_link_count": 0}

    with patch("app.observer._mail_navigate_to_review_candidate", return_value=True), patch("app.observer._mail_review_control_summary", return_value=summary), patch("app.observer._mail_click_confirmation_control", return_value="text_link"), patch("app.observer._mail_confirmation_landing_target", return_value=None):
        for _ in range(_MAIL_CONFIRMATION_LANDING_WAIT_TICKS + 12):
            _handle_mail_tick(observer, MagicMock(), "mail.google.com")
            if observer._stop.is_set():
                break

    names = [event for event, _detail, _result, _metadata in events]
    assert "mail.confirmation_message_review" in names
    assert names[-1] == "mail.failed"
    assert observer._mail_tick_count <= _MAIL_CONFIRMATION_LANDING_WAIT_TICKS + 10


def test_mail_confirmation_review_does_not_reprocess_a_same_tab_confirmation_page():
    """A control that navigated the mailbox tab must not be counted twice."""
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-sametab",
        "profile-review-sametab",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 2},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    observer._mail_search_submitted = True
    observer._mail_review_collection_done = True
    observer._mail_review_candidates = [{"key": "gmail:thread-one", "href": "#all/thread-one"}]
    summary = {"control_count": 1, "confirmation_count": 1, "button_count": 1, "text_link_count": 0}
    same_tab = _FakePage("https://news.example.com/confirm", text="Thanks for confirming. Subscription confirmed.")

    with patch("app.observer._mail_navigate_to_review_candidate", return_value=True), patch("app.observer._mail_review_control_summary", return_value=summary), patch("app.observer._mail_click_confirmation_control", return_value="button"), patch("app.observer._mail_confirmation_landing_target", side_effect=lambda _observer, page, host: page if host != "mail.google.com" else None):
        navigated = False
        for _ in range(30):
            # The mailbox tab never returns to Gmail after the control navigated it.
            navigated = navigated or observer._mail_review_control_clicked
            _handle_mail_tick(observer, same_tab if navigated else MagicMock(), "news.example.com" if navigated else "mail.google.com")
            if observer._stop.is_set():
                break

    names = [event for event, _detail, _result, _metadata in events]
    assert names.count("mail.confirmation_succeeded") == 1
    assert names.count("mail.confirmation_step_completed") == 0
    assert observer._mail_review_processed_count == 1
    assert observer._mail_review_confirmed_count == 1


def test_mail_confirmation_tick_budget_scales_with_the_selected_message_count():
    assert _mail_confirmation_tick_budget({"action": "confirmation_review", "max_messages": 1}) == 190
    assert _mail_confirmation_tick_budget({"action": "confirmation_review", "max_messages": 4}) == 400
    assert _mail_confirmation_tick_budget({"action": "confirmation_review", "max_messages": 500}) == _MAIL_CONFIRMATION_MAX_TICKS


def test_mail_review_candidate_route_must_stay_inside_gmail():
    class _RoutePage(_FakePage):
        def __init__(self):
            super().__init__("https://mail.google.com/mail/u/0/")
            self.visited: list[str] = []

        def goto(self, url: str, **_kwargs) -> None:
            self.visited.append(url)
            self.url = url

    page = _RoutePage()
    assert _mail_navigate_to_review_candidate(page, {"key": "row:https://evil.example/#all/1", "href": "https://evil.example/#all/1"}) is False
    assert _mail_navigate_to_review_candidate(page, {"key": "gmail:thread-one", "href": ""}) is False or page.url.endswith("thread-one")
    assert all(url.startswith("https://mail.google.com/") for url in page.visited)


def test_mail_confirmation_label_ranking_prefers_an_exact_confirmation_label():
    exact = _mail_confirmation_score("Confirm subscription")
    inside = _mail_confirmation_score("Please confirm subscription now")
    unrelated = _mail_confirmation_score("Manage your preferences")
    assert exact > inside > unrelated
    assert unrelated == 0


def test_mail_confirmation_review_stops_collecting_matches_at_the_selected_limit():
    observer = BrowserObserver(
        "mail-review-limit",
        "profile-review-limit",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 2},
        report_event=lambda _session, event, detail, result, metadata=None: None,
    )
    rows = [{"key": f"gmail:thread-{index}", "href": f"#all/thread-{index}"} for index in range(5)]

    with patch("app.observer._mail_review_candidate_rows", return_value=rows):
        _mail_collect_review_candidates(observer, _GmailSearchPage(), observer.mail_workflow)

    assert len(observer._mail_review_candidates) == 2
    assert observer._mail_review_collection_done is True


def test_mail_confirmation_review_fails_when_gmail_reports_the_search_matched_nothing():
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-noresults",
        "profile-review-noresults",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 3},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )

    with patch("app.observer._mail_review_candidate_rows", return_value=[]), patch("app.observer._mail_search_list_status", return_value={"visible_rows": 0, "no_results": True}):
        for _ in range(_MAIL_REVIEW_EMPTY_SETTLE_TICKS):
            _mail_collect_review_candidates(observer, _GmailSearchPage(), observer.mail_workflow)

    assert observer._mail_review_collection_done is True
    assert observer._stop.is_set()
    assert [event for event, _detail, _result, _metadata in events][-1] == "mail.failed"
    assert "matched no email at all" in events[-1][1]


def test_mail_confirmation_review_waits_for_the_result_list_before_reporting_no_match():
    """A result list that has not rendered yet must not read as an empty mailbox."""
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-none",
        "profile-review-none",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 3},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    status = {"visible_rows": 6, "no_results": False}
    rows: list[dict[str, str]] = []

    with patch("app.observer._mail_review_candidate_rows", side_effect=lambda _page, _config: list(rows)), patch("app.observer._mail_search_list_status", side_effect=lambda _page: dict(status)), patch("app.observer._mail_review_scroll_results", return_value=False):
        for _ in range(_MAIL_REVIEW_MIN_COLLECTION_TICKS - 1):
            _mail_collect_review_candidates(observer, _GmailSearchPage(), observer.mail_workflow)
            assert observer._mail_review_collection_done is False
            assert observer._stop.is_set() is False
            # Gmail finishes rendering a late result list while the run waits.
            if len(rows) == 0 and observer._mail_review_collection_ticks == 3:
                rows.append({"key": "gmail:thread-one", "href": "#all/thread-one"})
        _mail_collect_review_candidates(observer, _GmailSearchPage(), observer.mail_workflow)

    assert observer._mail_review_collection_done is True
    assert observer._stop.is_set() is False
    assert [candidate["key"] for candidate in observer._mail_review_candidates] == ["gmail:thread-one"]
    assert [event for event, _detail, _result, _metadata in events] == ["mail.candidates_collected"]
    assert events[-1][3]["matched_confirmation_emails"] == 1
    assert events[-1][3]["search_list_rows"] == 6


def test_mail_search_route_is_required_before_a_result_list_is_trusted():
    """An inbox route is not a search result, even with a full mailbox rendered."""
    config = {"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription"}

    assert _mail_search_applied(_GmailSearchPage(), config)[0] is True
    applied, route = _mail_search_applied(_GmailSearchPage(fragment="inbox"), config)

    assert applied is False
    assert route == "inbox"


def test_mail_search_route_falls_back_to_the_applied_query_in_gmail_search_box():
    """Gmail may keep its usual route while the submitted query is already applied."""
    page = _GmailSearchBoxPage(query='in:anywhere is:unread subject:"Confirm your subscription"')
    applied, route = _mail_search_applied(page, {"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription"})

    assert applied is True
    assert route == "inbox"

    # A different query in the box says nothing about this subject.
    other = _GmailSearchBoxPage(query="in:anywhere is:unread")
    assert _mail_search_applied(other, {"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription"})[0] is False

    # With no route at all the applied query in the box is the only signal.
    applied, route = _mail_search_applied(_GmailSearchBoxPage(query='in:anywhere is:unread subject:"Confirm your subscription"', fragment=""), {"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription"})

    assert (applied, route) == (True, "search-box")


def test_mail_confirmation_review_does_not_scan_the_mailbox_while_gmail_is_still_on_it():
    """The rows of the mailbox Gmail was on must not be read as search results."""
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-route",
        "profile-review-route",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 3},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    inbox = _GmailSearchPage(fragment="inbox")
    rows = MagicMock(return_value=[{"key": "gmail:inbox-row", "href": "#inbox/inbox-row"}])

    with patch("app.observer._mail_review_candidate_rows", rows), patch("app.observer._mail_search_list_status", return_value={"visible_rows": 12, "no_results": False}):
        for _ in range(_MAIL_REVIEW_ROUTE_WAIT_TICKS - 1):
            _mail_collect_review_candidates(observer, inbox, observer.mail_workflow)
            assert observer._mail_review_candidates == []
            assert observer._mail_review_collection_ticks == 0
            assert observer._mail_review_collection_done is False
            assert observer._stop.is_set() is False
            assert rows.call_count == 0

        _mail_collect_review_candidates(observer, inbox, observer.mail_workflow)

    assert rows.call_count == 0
    assert observer._stop.is_set()
    assert [event for event, _detail, _result, _metadata in events][-1] == "mail.failed"
    assert "did not open a result list" in events[-1][1]
    assert events[-1][3]["search_route"] == "inbox"

    # Gmail finally leaves the mailbox, so the same run collects from the results.
    observer._stop.clear()
    with patch("app.observer._mail_review_candidate_rows", return_value=[{"key": "gmail:thread-one", "href": "#all/thread-one"}]), patch("app.observer._mail_search_list_status", return_value={"visible_rows": 4, "no_results": False}):
        _mail_collect_review_candidates(observer, _GmailSearchPage(), observer.mail_workflow)

    assert [candidate["key"] for candidate in observer._mail_review_candidates] == ["gmail:thread-one"]
    assert observer._mail_review_collection_done is True


def test_mail_confirmation_review_ignores_a_transient_empty_search_result_list():
    """Gmail can render the empty state while the previous list is still visible."""
    events: list[tuple[str, str, str, dict | None]] = []
    observer = BrowserObserver(
        "mail-review-transient",
        "profile-review-transient",
        9222,
        purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "Confirm your subscription", "max_messages": 3},
        report_event=lambda _session, event, detail, result, metadata=None: events.append((event, detail, result, metadata)),
    )
    status = {"visible_rows": 5, "no_results": True}
    rows: list[dict[str, str]] = []

    with patch("app.observer._mail_review_candidate_rows", side_effect=lambda _page, _config: list(rows)), patch("app.observer._mail_search_list_status", side_effect=lambda _page: dict(status)), patch("app.observer._mail_review_scroll_results", return_value=False):
        for tick in range(1, _MAIL_REVIEW_MIN_COLLECTION_TICKS):
            # The real result list renders while the run is still waiting.
            if tick == 3:
                status["no_results"] = False
                rows.append({"key": "gmail:thread-one", "href": "#all/thread-one"})
            _mail_collect_review_candidates(observer, _GmailSearchPage(), observer.mail_workflow)
            assert observer._mail_review_collection_done is False
            assert observer._stop.is_set() is False

        _mail_collect_review_candidates(observer, _GmailSearchPage(), observer.mail_workflow)

    assert observer._mail_review_collection_done is True
    assert observer._stop.is_set() is False
    assert [candidate["key"] for candidate in observer._mail_review_candidates] == ["gmail:thread-one"]
    assert [event for event, _detail, _result, _metadata in events] == ["mail.candidates_collected"]
    assert events[-1][3]["result_list_settled"] is False


def test_mail_reply_does_not_require_a_scrollable_thread_before_scanning_reply():
    class _ReplyControl(_FakeButton):
        def is_visible(self, timeout: int = 300) -> bool:
            return True

        def get_attribute(self, name: str):
            return "Reply" if name in {"aria-label", "data-tooltip"} else None

    class _ShortThreadPage:
        def __init__(self):
            self.control = _ReplyControl("Reply")

        def locator(self, selector: str):
            if '[role="button"]' in selector:
                return _FakeLocator([self.control])
            return _FakeLocator([])

        def evaluate(self, _script: str):
            return False

    page = _ShortThreadPage()

    assert _mail_reply_surface_ready(page) is True  # type: ignore[arg-type]
    assert _mail_scroll_reply_thread(page) is True  # type: ignore[arg-type]


def test_mail_reply_does_not_treat_a_generic_sent_label_as_delivery_confirmation():
    page = _FakePage("https://mail.google.com/mail/u/0/#inbox", text="Sent")

    assert _mail_action_confirmation_visible(page, "reply") is False


def test_mail_reply_fill_retries_with_trusted_typing_after_editor_rerender():
    class _RerenderingEditor:
        def __init__(self):
            self.value = ""

        def is_visible(self, timeout: int = 300) -> bool:
            return True

        def fill(self, _value: str, timeout: int = 1500):
            # Simulate Gmail replacing the contenteditable immediately after
            # Playwright fill() dispatches its input event.
            self.value = ""

        def click(self, timeout: int = 1200):
            return None

        def press(self, _key: str, timeout: int = 1200):
            if _key == "Backspace":
                self.value = ""

        def type(self, value: str, delay: int = 5, timeout: int = 5000):
            self.value = value

        def inner_text(self, timeout: int = 800) -> str:
            return self.value

    class _EditorPage:
        def __init__(self):
            self.editor = _RerenderingEditor()

        def locator(self, selector: str):
            if "contenteditable" in selector:
                return _FakeLocator([self.editor])
            return _FakeLocator([])

    page = _EditorPage()

    assert _mail_fill_reply(page, "The reply survived the Gmail re-render.") is True  # type: ignore[arg-type]
    assert page.editor.value == "The reply survived the Gmail re-render."


def test_mail_reply_send_accepts_gmail_data_tooltip_text_variant():
    class _SendControl(_FakeButton):
        def is_visible(self, timeout: int = 300) -> bool:
            return True

        def get_attribute(self, name: str):
            return "Send" if name == "data-tooltip-text" else None

    class _SendPage:
        def __init__(self):
            self.control = _SendControl("")

        def locator(self, _selector: str):
            return _FakeLocator([self.control])

    page = _SendPage()

    assert _mail_click_reply_send(page) is True  # type: ignore[arg-type]
    assert getattr(page.control, "clicked", False) is True


def test_mail_move_search_scopes_remain_unchanged():
    assert _mail_subject_query({"action": "spam_to_inbox", "subject": "Invoice"}) == 'in:spam subject:"Invoice"'
    assert _mail_subject_query({"action": "promotions_to_spam", "subject": "Offer"}) == 'category:promotions subject:"Offer"'


def test_account_preferences_prefers_a_picker_popup_over_personal_info_page():
    personal_info = _FakePage(
        "https://myaccount.google.com/personal-info",
        text="Personal info Profile picture",
    )
    picker_popup = _FakePage(
        "about:blank",
        text="Add profile picture Browse Illustrations See more illustrations",
    )
    owner = type("Owner", (), {"pages": [personal_info, picker_popup]})()

    assert _active_page(owner, "account_preferences") is picker_popup  # type: ignore[arg-type]


def test_account_preferences_detects_nonsemantic_picker_inside_frame():
    class _PickerBody:
        def inner_text(self, timeout: int = 1000):
            return "Add profile picture Browse Illustrations"

        def locator(self, _selector: str):
            return _FakeLocator([self])

    class _PickerFrame:
        frames = []

        def locator(self, selector: str):
            return _FakeLocator([_PickerBody()]) if selector == "body" else _FakeLocator([])

        def get_by_text(self, _pattern):
            return _FakeLocator([])

    class _Page:
        def __init__(self):
            self.frames = [_PickerFrame()]

        def locator(self, _selector: str):
            return _FakeLocator([])

        def get_by_text(self, _pattern):
            return _FakeLocator([])

    root = _visible_picker_root(_Page(), r"browse illustrations|add profile picture")  # type: ignore[arg-type]

    assert root is not None


def test_profile_illustration_selection_uses_image_tile_not_carousel_button(monkeypatch):
    class _Tile:
        def __init__(self):
            self.clicked = False

        def bounding_box(self):
            return {"x": 10, "y": 10, "width": 94, "height": 94}

        def get_attribute(self, _name: str):
            return None

        def locator(self, _selector: str):
            return _FakeLocator([])

        def click(self, **_kwargs):
            self.clicked = True

    class _Root:
        def __init__(self, tile):
            self.tile = tile

        def inner_text(self):
            return "Start exploring"

        def locator(self, selector: str):
            self.selector = selector
            return _FakeLocator([self.tile])

    tile = _Tile()
    root = _Root(tile)
    monkeypatch.setattr("app.observer._visible_picker_root", lambda *_args: root)

    assert _click_random_profile_illustration(object()) is True  # type: ignore[arg-type]
    assert tile.clicked is True
    assert "img" in root.selector
    assert "tabindex" not in root.selector
    assert "button" not in root.selector


def test_account_preferences_profile_save_labels_cover_localized_confirmation():
    assert "guardar como foto de perfil" in _ACCOUNT_PREFERENCES_PROFILE_SAVE_LABELS
    assert "save as profile picture" in _ACCOUNT_PREFERENCES_PROFILE_SAVE_LABELS
    assert "enregistrer comme photo de profil" in _ACCOUNT_PREFERENCES_PROFILE_SAVE_LABELS


def test_account_preferences_existing_image_menu_has_localized_illustration_entry():
    assert "explorez les illustrations" in _ACCOUNT_PREFERENCES_ILLUSTRATION_LABELS
    assert "explore illustrations" in _ACCOUNT_PREFERENCES_ILLUSTRATION_LABELS
    assert "changez votre photo de profil" in _ACCOUNT_PREFERENCES_EXISTING_IMAGE_PICKER_MARKERS
    assert "ilustraciones" in _ACCOUNT_PREFERENCES_PROFILE_PICKER_MARKERS
