import time
from unittest.mock import MagicMock, patch

from app.login_policy import PageAction, PageSignal
from app.observer import BrowserObserver, _CONTACTS_FINAL_IMPORT_SELECTOR, _contacts_click_final_import, _contacts_click_label, _contacts_dialog_closed_after_confirm, _contacts_import_result_route, _is_safe_interstitial


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


def test_contacts_clicks_the_configured_final_import_button_selector():
    page = _FakeFinalImportPage()

    assert _contacts_click_final_import(page) is True
    assert getattr(page.button, "clicked", False) is True
    assert page.evaluated_selector == _CONTACTS_FINAL_IMPORT_SELECTOR


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
