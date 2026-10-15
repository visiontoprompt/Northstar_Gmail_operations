import time

from app.newsletter_identity import build_newsletter_identity
from app.newsletter_runner import _adaptive_post_submit_step
from app.observer import BrowserObserver, _finish_newsletter, _handle_newsletter_popup, _handle_newsletter_signup_tick, _newsletter_attach_submission_listener, _newsletter_email_field, _newsletter_fill_discovered_fields, _newsletter_fill_live_fields, _newsletter_field_locator, _newsletter_locator_is_usable, _newsletter_locator_type_is_text, _newsletter_missing_required_fields, _newsletter_test_identity_value, _newsletter_verify_retained_fields, _verify_newsletter_signup_tick


class _BodyLocator:
    def __init__(self, text: str) -> None:
        self.text = text

    def count(self) -> int:
        return 1

    def inner_text(self, timeout: int = 1000) -> str:
        return self.text


class _PopupControl:
    def __init__(self, label: str) -> None:
        self.label = label
        self.clicked = False

    def inner_text(self, timeout: int = 400) -> str:
        return self.label

    def get_attribute(self, name: str):
        return self.label if name in {"aria-label", "title", "value"} else None

    def is_enabled(self, timeout: int = 300) -> bool:
        return True

    def is_visible(self, timeout: int = 300) -> bool:
        return True

    def click(self, timeout: int = 1800, no_wait_after: bool = True) -> None:
        self.clicked = True


class _PopupNode:
    def __init__(self, text: str, controls: list[_PopupControl], email_count: int = 0) -> None:
        self.text = text
        self.controls = controls
        self.email_count = email_count

    def is_visible(self, timeout: int = 300) -> bool:
        return True

    def inner_text(self, timeout: int = 500) -> str:
        return self.text

    def locator(self, selector: str):
        if "input[type=\"email\"]" in selector or "autocomplete=\"email\"" in selector or "name*=\"email\"" in selector:
            return _PopupCollection([], count=self.email_count)
        return _PopupCollection(self.controls)


class _PopupCollection:
    def __init__(self, items: list, count: int | None = None) -> None:
        self.items = items
        self._count = count

    def count(self) -> int:
        return self._count if self._count is not None else len(self.items)

    def all(self):
        return list(self.items)


class _PopupPage:
    def __init__(self, popup: _PopupNode) -> None:
        self.url = "https://publisher.example/subscribe"
        self.frames = []
        self.popup = popup

    def locator(self, selector: str):
        return _PopupCollection([self.popup])


def test_newsletter_dismisses_localized_cookie_popup_once() -> None:
    events: list[tuple[str, str]] = []
    accept = _PopupControl("Tout accepter")
    page = _PopupPage(_PopupNode("Nous utilisons des cookies pour respecter votre confidentialité", [accept]))
    observer = BrowserObserver(
        "session-popup",
        "profile-popup",
        0,
        purpose="newsletter",
        report_event=lambda _sid, event, detail, _result: events.append((event, detail)),
    )

    assert _handle_newsletter_popup(page, observer) is True
    assert accept.clicked is True
    assert observer._newsletter_popup_actions == 1
    assert events[0][0] == "newsletter.signup.popup_dismissed"


def test_newsletter_leaves_subscription_popup_with_email_field_open() -> None:
    subscribe = _PopupControl("Subscribe")
    page = _PopupPage(_PopupNode("Subscribe to our newsletter", [subscribe], email_count=1))
    observer = BrowserObserver("session-subscribe-popup", "profile-subscribe-popup", 0, purpose="newsletter")

    assert _handle_newsletter_popup(page, observer) is False
    assert subscribe.clicked is False


class _Page:
    def __init__(self, text: str) -> None:
        self.url = "https://example.com/thanks"
        self._body = _BodyLocator(text)

    def locator(self, selector: str) -> _BodyLocator:
        return self._body


def _observer(events: list[tuple[str, str]]) -> BrowserObserver:
    observer = BrowserObserver(
        "session-newsletter",
        "profile-newsletter",
        0,
        purpose="newsletter",
        newsletter_config={"success_signals": "", "failure_signals": ""},
        report_event=lambda _sid, event, detail, _result: events.append((event, detail)),
    )
    observer._newsletter_submit_attempted = True
    observer._newsletter_verify_not_before = 0
    observer._newsletter_started_at = time.monotonic() - 3
    return observer


def test_newsletter_signup_requires_success_evidence() -> None:
    events: list[tuple[str, str]] = []
    observer = _observer(events)

    _verify_newsletter_signup_tick(observer, _Page("Thanks for subscribing. Check your inbox."), "example.com")

    assert events[0][0] == "newsletter.signup.submitted"
    assert observer._stop.is_set()


def test_success_modal_finishes_before_optional_creator_recommendations(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    observer = _observer(events)
    observer._newsletter_verify_not_before = time.monotonic() + 30
    recommendation = _PopupControl("Select all")
    modal = _PopupNode(
        "Thanks for subscribing! These are fellow creators that I follow and you should too. Maybe Later",
        [recommendation],
    )

    class SuccessPage:
        url = "https://publisher.example/subscribe"
        frames = []

        def locator(self, selector: str):
            if selector == "body":
                return _BodyLocator("Original signup page without a completion message")
            return _PopupCollection([modal])

    def no_followup(*_args):
        raise AssertionError("Optional creator recommendations are not a signup step")

    monkeypatch.setattr("app.observer._newsletter_continue_name_step", no_followup)
    _verify_newsletter_signup_tick(observer, SuccessPage(), "publisher.example")

    assert events == [("newsletter.signup.submitted", "The publisher displayed a signup completion message")]
    assert observer._stop.is_set()
    assert recommendation.clicked is False


def test_newsletter_signup_does_not_report_success_on_unknown_result() -> None:
    events: list[tuple[str, str]] = []
    observer = _observer(events)
    observer._newsletter_started_at = time.monotonic() - 25

    _verify_newsletter_signup_tick(observer, _Page("Your request is being processed."), "example.com")

    assert events[0][0] == "newsletter.signup.manual_review"
    assert observer._stop.is_set()


def test_learning_mode_verifies_new_completion_phrase_on_changed_page(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    observer = _observer(events)
    observer._newsletter_execution_mode = "AGENT_LEARNING"
    observer.newsletter_config["agent_settings"] = {"enabled": True, "learning_mode": True, "live_execution_enabled": True, "max_ai_calls": 2}
    observer._newsletter_pre_submit_body = "Enter your address to receive updates"
    calls = []

    def decide(page, settings):
        calls.append(page)
        return {"decision": "COMPLETE", "evidence": "Your weekly dispatch is now active", "confidence": 0.94}

    monkeypatch.setattr("app.newsletter_runner.request_newsletter_step_decision", decide)
    _verify_newsletter_signup_tick(observer, _Page("Your weekly dispatch is now active. Browse other creators."), "example.com")

    assert len(calls) == 1
    assert events[-1][0] == "newsletter.signup.submitted"
    assert observer._stop.is_set()


def test_learning_mode_rejects_model_success_without_new_visible_evidence(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    observer = _observer(events)
    observer._newsletter_execution_mode = "AGENT_LEARNING"
    observer.newsletter_config["agent_settings"] = {"enabled": True, "learning_mode": True, "live_execution_enabled": True, "max_ai_calls": 2}
    observer._newsletter_pre_submit_body = "Your weekly dispatch is now active"
    observer._newsletter_submission_response = True
    observer._newsletter_started_at = time.monotonic() - 25
    monkeypatch.setattr("app.newsletter_runner.request_newsletter_step_decision", lambda *_args: {"decision": "COMPLETE", "evidence": "Your weekly dispatch is now active", "confidence": 0.94})

    _verify_newsletter_signup_tick(observer, _Page("Your weekly dispatch is now active"), "example.com")

    assert events[-1][0] == "newsletter.signup.manual_review"
    assert observer._stop.is_set()


def test_saved_skill_can_relearn_a_changed_post_submit_step(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    observer = _observer(events)
    observer._newsletter_execution_mode = "SKILL_REPLAY"
    observer.newsletter_config["agent_settings"] = {
        "enabled": True, "learning_mode": True, "live_execution_enabled": True,
        "automatic_relearning_enabled": True, "shadow_mode": False, "max_ai_calls": 2,
    }
    monkeypatch.setattr("app.newsletter_runner.request_newsletter_step_decision", lambda *_args: {
        "decision": "COMPLETE", "evidence": "Your weekly dispatch is now active", "confidence": 0.94,
    })

    _verify_newsletter_signup_tick(observer, _Page("Your weekly dispatch is now active"), "example.com")

    assert events[-1][0] == "newsletter.signup.submitted"
    assert observer._stop.is_set()


def test_adaptive_continuation_clicks_only_a_validated_next_step_once(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    observer = _observer(events)
    observer._newsletter_execution_mode = "AGENT_LEARNING"
    observer.newsletter_config["agent_settings"] = {"enabled": True, "max_ai_calls": 2}
    clicks = []

    class Button:
        first = None

        def click(self, **_kwargs):
            clicks.append(True)

    button = Button()
    button.first = button

    class Page:
        url = "https://example.com/signup/step-2"

        def locator(self, selector):
            assert selector == "#next"
            return button

    monkeypatch.setattr("app.newsletter_runner.request_newsletter_step_decision", lambda *_args: {
        "decision": "CONTINUE", "selector": "#next", "control": {"text": "Continue"}, "confidence": 0.92,
    })
    monkeypatch.setattr("app.observer._newsletter_locator_is_usable", lambda *_args: True)
    monkeypatch.setattr("app.observer._newsletter_fill_live_fields", lambda *_args: (True, "", 0))
    monkeypatch.setattr("app.observer._newsletter_required_agreements_ready", lambda *_args: True)
    page = Page()

    assert _adaptive_post_submit_step(observer, page, "Step 2: continue setup") is True
    assert _adaptive_post_submit_step(observer, page, "Step 2: continue setup") is False
    assert len(clicks) == 1
    assert events[-1][0] == "newsletter.signup.followup_submitted"


def test_required_non_email_field_without_approved_identity_blocks_before_submit() -> None:
    observer = BrowserObserver(
        "session-fields",
        "profile-fields",
        0,
        purpose="newsletter",
        newsletter_config={
            "account_email": "test@example.com",
            "fields": [
                {"field_key": "email", "selector": "#email", "required": True},
                {"field_key": "first_name", "selector": "#first", "required": True},
                {"field_key": "city", "selector": "#city", "required": False},
            ],
        },
    )

    assert _newsletter_missing_required_fields(observer) == ["first_name"]


def test_optional_unmapped_field_does_not_block_and_secret_values_are_not_in_detail() -> None:
    observer = BrowserObserver(
        "session-optional",
        "profile-optional",
        0,
        purpose="newsletter",
        newsletter_config={
            "account_email": "test@example.com",
            "test_identity": {"first_name": "Approved", "phone": "555-0100"},
            "fields": [{"field_key": "company", "selector": "#company", "required": False}],
        },
    )
    assert _newsletter_missing_required_fields(observer) == []
    assert "555-0100" not in str(observer.newsletter_config.get("fields"))


def test_generated_field_keyed_identity_satisfies_required_fields() -> None:
    # ``start_newsletter_signup_test`` merges the generated placeholders as the
    # base layer, so the observer sees plain field keys next to any
    # selector-scoped values an operator approved. Both must count as supplied.
    generated = build_newsletter_identity("org_demo", "account_1", account_email="walker.ob@gmail.com")
    observer = BrowserObserver(
        "session-generated",
        "profile-generated",
        0,
        purpose="newsletter",
        newsletter_config={
            "account_email": "test@example.com",
            "test_identity": generated,
            "fields": [
                {"field_key": "email", "selector": "#email", "required": True},
                {"field_key": "first_name", "selector": "#first", "required": True},
                {"field_key": "postal_code", "selector": "#zip", "required": True},
                {"field_key": "full_name", "selector": "#name", "required": True},
            ],
        },
    )

    assert _newsletter_missing_required_fields(observer) == []
    # The mailbox stays the only source for the email field, generated or not.
    assert _newsletter_test_identity_value(observer, "email") == "test@example.com"
    assert _newsletter_test_identity_value(observer, "full_name") == generated["full_name"]


def test_selector_scoped_value_wins_over_a_generated_field_key() -> None:
    generated = build_newsletter_identity("org_demo", "account_1")
    observer = BrowserObserver(
        "session-precedence",
        "profile-precedence",
        0,
        purpose="newsletter",
        newsletter_config={
            "account_email": "test@example.com",
            "test_identity": {**generated, "selector:#city": "Approved City"},
            "fields": [{"field_key": "city", "selector": "#city", "required": True}],
        },
    )

    assert _newsletter_test_identity_value(observer, "city", "#city") == "Approved City"


class _FillLocator:
    def __init__(self, flaky: bool = False, raise_on_fill: bool = False) -> None:
        self.value = ""
        self.flaky = flaky
        self.raise_on_fill = raise_on_fill
        self.fill_calls: list[str] = []
        self.type_calls: list[str] = []
        self.click_calls = 0

    @property
    def first(self):
        return self

    def count(self) -> int:
        return 1

    def is_visible(self, timeout: int = 800) -> bool:
        return True

    def is_enabled(self, timeout: int = 800) -> bool:
        return True

    def fill(self, value: str, timeout: int = 1500) -> None:
        self.fill_calls.append(value)
        if self.raise_on_fill:
            raise TimeoutError("detached during hydration")
        self.value = value[:1] if self.flaky else value

    def input_value(self, timeout: int = 800) -> str:
        return self.value

    def press(self, key: str, timeout: int = 800) -> None:
        if key == "Control+A":
            self.value = ""

    def type(self, value: str, delay: int = 0, timeout: int = 4000) -> None:
        self.type_calls.append(value)
        self.value += value

    def check(self, timeout: int = 1200) -> None:
        self.value = "checked"

    def is_checked(self, timeout: int = 500) -> bool:
        return self.value == "checked"

    def click(self, timeout: int = 1800, no_wait_after: bool = False) -> None:
        self.click_calls += 1


class _FieldsPage:
    def __init__(self, locators: dict[str, _FillLocator]) -> None:
        self.locators = locators
        self.url = "https://example.com/signup"

    def locator(self, selector: str):
        return self.locators[selector]


class _TypedLocator(_FillLocator):
    """A control that reports its ``type``, as a real Playwright locator does."""

    def __init__(self, input_type: str | None = None, readonly: str | None = None) -> None:
        super().__init__()
        self.attributes = {"type": input_type, "readonly": readonly}

    def get_attribute(self, name: str):
        return self.attributes.get(name)


class _LabelledPage:
    """A form whose id and name were rewritten, leaving only its label intact."""

    def __init__(self, labels: dict[str, _FillLocator], selectors: dict[str, _FillLocator] | None = None) -> None:
        self.labels = labels
        self.selectors = selectors or {}
        self.url = "https://example.com/signup"

    def locator(self, selector: str):
        return self.selectors[selector]

    def get_by_label(self, text: str):
        return self.labels[text]


class _FramePage:
    def __init__(self, frame):
        self.main_frame = self
        self.frames = [self, frame]

    def locator(self, selector: str):
        raise AssertionError(f"unexpected top-level selector: {selector}")


def test_newsletter_fill_retries_a_re_rendered_field_until_the_complete_value_is_retained() -> None:
    first = _FillLocator(flaky=True)
    page = _FieldsPage({"#first": first})
    observer = BrowserObserver(
        "session-fill",
        "profile-fill",
        0,
        purpose="newsletter",
        newsletter_config={
            "test_identity": {"selector:#first": "Alexandra"},
            "fields": [{"field_key": "first_name", "selector": "#first", "required": True, "field_type": "text"}],
        },
    )

    ready, detail = _newsletter_fill_discovered_fields(page, observer)

    assert ready is True, detail
    assert first.fill_calls == ["Alexandra"]
    assert first.type_calls == ["Alexandra"]
    assert first.value == "Alexandra"


def test_newsletter_fill_uses_keyboard_fallback_after_transient_fill_timeout() -> None:
    field = _FillLocator(raise_on_fill=True)

    from app.observer import _newsletter_fill_text
    _newsletter_fill_text(field, "Amina Test")

    assert field.fill_calls == ["Amina Test"]
    assert field.type_calls == ["Amina Test"]
    assert field.value == "Amina Test"


def test_newsletter_rechecks_the_live_control_after_a_reactive_form_rerender() -> None:
    field = _FillLocator()
    page = _FieldsPage({"#first": field})
    observer = BrowserObserver(
        "session-retained",
        "profile-retained",
        0,
        purpose="newsletter",
        newsletter_config={
            "test_identity": {"selector:#first": "Amina Morgan"},
            "fields": [{"field_key": "first_name", "selector": "#first", "required": True, "field_type": "text"}],
        },
    )
    field.value = "A"

    ready, detail = _newsletter_verify_retained_fields(page, observer)

    assert ready is True, detail
    assert field.value == "Amina Morgan"


class _ResponseRequest:
    method = "POST"


class _Response:
    request = _ResponseRequest()
    url = "https://example.com/api/subscribe"
    status = 202


class _ResponsePage:
    url = "https://example.com/signup"

    def __init__(self) -> None:
        self.handler = None

    def on(self, event: str, handler) -> None:
        assert event == "response"
        self.handler = handler

    def locator(self, selector: str):
        return self.locators[selector]


def test_newsletter_records_only_a_same_origin_submission_response_fact() -> None:
    observer = BrowserObserver("session-response", "profile-response", 0, purpose="newsletter")
    page = _ResponsePage()

    _newsletter_attach_submission_listener(page, observer)
    assert page.handler is not None
    page.handler(_Response())

    assert observer._newsletter_submission_response is True


def test_newsletter_late_submit_path_attaches_response_listener_before_click() -> None:
    email = _FillLocator()
    submit = _FillLocator()
    page = _ResponsePage()
    page.locators = {"#email": email, "#submit": submit}
    observer = BrowserObserver(
        "session-late-submit",
        "profile-late-submit",
        0,
        purpose="newsletter",
        newsletter_config={
            "account_email": "test@example.com",
            "email_selector": "#email",
            "submit_selector": "#submit",
            "fields": [{"field_key": "email", "field_type": "email", "selector": "#email", "required": True}],
        },
    )
    observer._newsletter_started_at = time.monotonic()
    observer._newsletter_fields_filled = True

    _handle_newsletter_signup_tick(observer, page, "example.com")
    # A second observer tick is the production failure mode: once the first
    # click has happened, the form must remain in verification-only mode.
    _handle_newsletter_signup_tick(observer, page, "example.com")

    assert page.handler is not None
    assert submit.click_calls == 1


def test_newsletter_manual_review_closes_the_completed_attempt() -> None:
    events: list[str] = []
    observer = BrowserObserver(
        "session-review",
        "profile-review",
        0,
        purpose="newsletter",
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )

    _finish_newsletter(observer, "newsletter.signup.manual_review", "BLOCKED_ANTIBOT: human action is required", result="attention")

    assert events == ["newsletter.signup.manual_review"]
    assert observer._stop.is_set()


def test_newsletter_bulk_manual_review_releases_browser_slot() -> None:
    observer = BrowserObserver(
        "session-bulk-review",
        "profile-bulk-review",
        0,
        purpose="newsletter",
        newsletter_config={"task_id": "nltask-bulk-review"},
    )

    _finish_newsletter(observer, "newsletter.signup.manual_review", "The signup result requires review", result="attention")

    assert observer._stop.is_set()


def test_newsletter_bulk_terminal_result_keeps_browser_when_next_site_is_scheduled() -> None:
    observer = BrowserObserver(
        "session-bulk-reuse",
        "profile-bulk-reuse",
        0,
        purpose="newsletter",
        newsletter_config={"task_id": "nltask-current", "job_id": "nljob"},
        report_event=lambda *_args: True,
    )

    _finish_newsletter(observer, "newsletter.signup.submitted", "Signup completed")

    assert not observer._stop.is_set()


def test_newsletter_field_resolution_falls_back_to_live_frame_and_semantic_name() -> None:
    field = _FillLocator()
    frame = _FieldsPage({'input[name*="first-name" i]:visible': field})
    page = _FramePage(frame)

    resolved = _newsletter_field_locator(
        page,
        {
            "field_key": "first_name",
            "field_type": "text",
            "selector": "#stale-generated-id",
            "label": "First name",
        },
    )

    assert resolved is field


def test_newsletter_field_resolution_prefers_live_semantics_over_stale_selector() -> None:
    stale = _FillLocator()
    live = _FillLocator()
    page = _FieldsPage({
        "#generated-id": stale,
        'input[name*="first-name" i]:visible': live,
    })

    resolved = _newsletter_field_locator(
        page,
        {
            "field_key": "first_name",
            "field_type": "text",
            "selector": "#generated-id",
            "label": "First name",
        },
    )

    assert resolved is live


def test_newsletter_field_resolution_uses_the_associated_label() -> None:
    # A React/Vue form rewrites both id and name, so neither matches; the
    # human label is the only stable handle left on the control.
    email = _TypedLocator("email")
    page = _LabelledPage({"Email address": email})

    resolved = _newsletter_field_locator(
        page,
        {
            "field_key": "email",
            "field_type": "email",
            "selector": "#input_3480456411_1",
            "label": "Email address",
        },
    )

    assert resolved is email


def test_newsletter_field_resolution_ignores_a_labelled_control_that_cannot_hold_text() -> None:
    # Playwright associates labels by case-insensitive substring, so a loose
    # label match can land on a consent checkbox. Filling it would silently
    # tick a box instead of typing the field.
    consent = _TypedLocator("checkbox")
    name = _TypedLocator("text")
    page = _LabelledPage({"Full name": consent}, {"#name-field": name})

    resolved = _newsletter_field_locator(
        page,
        {
            "field_key": "full_name",
            "field_type": "text",
            "selector": "#name-field",
            "label": "Full name",
        },
    )

    assert resolved is name


def test_newsletter_field_resolution_skips_a_checkbox_that_matches_the_hint() -> None:
    # A substring hint such as ``input[name*="email" i]`` also matches
    # ``mailing_list_optin``, which usually precedes the real email input.
    optin = _TypedLocator("checkbox")
    email = _TypedLocator("email")
    page = _FieldsPage({
        'input[name*="email" i]:visible': optin,
        'input[placeholder*="email" i]:visible': email,
    })

    resolved = _newsletter_field_locator(
        page,
        {"field_key": "email", "field_type": "email", "placeholder": "you@example.com"},
    )

    assert resolved is email


def test_newsletter_field_resolution_does_not_guess_from_a_generic_text_type() -> None:
    # ``input[type="text"]:visible`` is the first text box on the page, which is
    # very unlikely to be the field being resolved. Guessing from it is worse
    # than reporting no match, which lets the agent fallback take over.
    first_text_box = _TypedLocator("text")

    resolved = _newsletter_field_locator(
        _FieldsPage({'input[type="text"]:visible': first_text_box}),
        {"field_key": "city", "field_type": "text"},
    )

    assert resolved is None


def test_newsletter_locator_usability_treats_a_present_readonly_attribute_as_unusable() -> None:
    # Playwright reports a valueless boolean attribute as "" and an absent one
    # as None, so only None means the control is writable.
    assert _newsletter_locator_is_usable(_TypedLocator("text", readonly=None)) is True
    assert _newsletter_locator_is_usable(_TypedLocator("text", readonly="")) is False
    assert _newsletter_locator_is_usable(_TypedLocator("text", readonly="readonly")) is False
    assert _newsletter_locator_is_usable(_TypedLocator("text", readonly="true")) is False
    assert _newsletter_locator_is_usable(_TypedLocator("text", readonly="false")) is True


def test_newsletter_locator_type_check_accepts_a_control_without_a_type() -> None:
    assert _newsletter_locator_type_is_text(_TypedLocator(None)) is True
    assert _newsletter_locator_type_is_text(_TypedLocator("search")) is True
    assert _newsletter_locator_type_is_text(_TypedLocator("checkbox")) is False


def test_newsletter_email_resolution_falls_back_when_discovered_id_is_generated() -> None:
    field = _FillLocator()
    frame = _FieldsPage({'input[placeholder*="email" i]:visible': field})
    page = _FramePage(frame)
    observer = BrowserObserver(
        "session-email",
        "profile-email",
        0,
        purpose="newsletter",
        newsletter_config={
            "email_selector": "#input_3480456411_1",
            "fields": [{
                "field_key": "email",
                "field_type": "email",
                "selector": "#input_3480456411_1",
                "placeholder": "Email Address",
                "required": True,
            }],
        },
    )

    assert _newsletter_email_field(page, observer) is field


def test_optional_hidden_consent_does_not_block_signup_submission() -> None:
    email = _FillLocator()
    submit = _FillLocator()
    page = _FieldsPage({"#email": email, "#submit": submit})
    events: list[str] = []
    observer = BrowserObserver(
        "session-consent",
        "profile-consent",
        0,
        purpose="newsletter",
        newsletter_config={
            "account_email": "test@example.com",
            "email_selector": "#email",
            "consent_selector": "#optional-consent",
            "submit_selector": "#submit",
            "fields": [
                {"field_key": "email", "field_type": "email", "selector": "#email", "required": True},
                {"field_key": "custom_checkbox", "field_type": "checkbox", "selector": "#optional-consent", "required": False},
            ],
            "test_identity": {},
        },
        report_event=lambda _sid, event, _detail, _result: events.append(event),
    )

    _handle_newsletter_signup_tick(observer, page, "example.com")

    assert "newsletter.signup.submit_clicked" in events
    assert "newsletter.signup.manual_review" not in events
    assert submit.click_calls == 1


class _LiveLocator(_FillLocator):
    """One control of a live form double, carrying its own option list."""

    def __init__(self) -> None:
        super().__init__()
        self.checked = False
        self.selected: str | None = None
        self.select_calls: list[str] = []

    def check(self, timeout: int = 1200) -> None:
        self.checked = True

    def is_checked(self, timeout: int = 500) -> bool:
        return self.checked

    def select_option(self, value: str | None = None, label: str | None = None, timeout: int = 1500) -> None:
        self.select_calls.append(str(value if value is not None else label))
        self.selected = str(value if value is not None else label)


class _LiveForm:
    """A live signup page double: descriptors on ``evaluate``, controls on ``locator``.

    The descriptors are rebuilt from the control state on every read, so a fill
    is visible to the verification pass exactly as it is on a real page.
    """

    def __init__(self, controls: list[dict], *, drops_after_fill: bool = False) -> None:
        self.url = "https://example.com/signup"
        self.controls = controls
        self.drops_after_fill = drops_after_fill
        self.locators = {f"#{control['id']}": _LiveLocator() for control in controls}

    def evaluate(self, script: str, _config: dict | None = None) -> list[dict]:
        described: list[dict] = []
        for control in self.controls:
            locator = self.locators[f"#{control['id']}"]
            value = locator.value
            if self.drops_after_fill and locator.fill_calls:
                value = ""
            selected_index = control["selected_index"]
            if locator.selected is not None:
                for index, option in enumerate(control["options"]):
                    if option["value"] == locator.selected or option["label"] == locator.selected:
                        selected_index = index
                        value = option["value"]
                        break
            described.append({**control, "value": value, "checked": locator.checked, "selected_index": selected_index})
        return described

    def locator(self, selector: str):
        if selector in self.locators:
            return self.locators[selector]
        # A qualified form such as ``select#id`` addresses the same control.
        for control_id, locator in self.locators.items():
            if control_id in selector:
                return locator
        raise AssertionError(f"unexpected selector: {selector}")


def _live_control(control_id: str, **overrides: object) -> dict:
    control = {
        "index": 0,
        "tag": "input",
        "type": "text",
        "name": "",
        "id": control_id,
        "aria_label": "",
        "placeholder": "",
        "autocomplete": "",
        "label": "",
        "required": False,
        "hidden": False,
        "disabled": False,
        "value": "",
        "checked": False,
        "selected_index": -1,
        "options": [],
    }
    control.update(overrides)
    return control


def _live_observer(identity: dict[str, str]) -> BrowserObserver:
    return BrowserObserver(
        "session-live",
        "profile-live",
        0,
        purpose="newsletter",
        newsletter_config={"account_email": "test@example.com", "test_identity": identity},
    )


def test_live_form_fills_a_required_field_the_profile_never_mapped() -> None:
    # No saved profile exists for this newsletter, so the live controls are the
    # only record of what the form asks for.
    generated = build_newsletter_identity("org_demo", "account_1", account_email="test@example.com")
    observer = _live_observer(generated)
    page = _LiveForm([
        _live_control("street", name="street", label="Street address", required=True),
        _live_control("city", name="city", label="City", required=True),
        _live_control("dob", name="dob", label="Date of birth", required=True),
    ])

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert ready is True, detail
    assert filled == 3
    assert page.locators["#street"].value == generated["address_line_1"]
    assert page.locators["#city"].value == generated["city"]
    assert page.locators["#dob"].value == generated["date_of_birth"]


def test_live_form_checks_a_multilingual_agreement_checkbox_after_email_render() -> None:
    observer = _live_observer(build_newsletter_identity("org_demo", "account_1"))
    page = _LiveForm([
        _live_control("email", type="email", name="email", label="Adresse e-mail", required=True),
        _live_control("rules", type="checkbox", name="rules", label="J'accepte les conditions générales"),
    ])

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert ready is True, detail
    assert filled == 2
    assert page.locators["#rules"].checked is True


def test_live_form_checks_an_aria_agreement_checkbox() -> None:
    observer = _live_observer(build_newsletter_identity("org_demo", "account_1"))
    page = _LiveForm([
        _live_control("email", type="email", name="email", label="Email address", required=True),
        _live_control("rules", tag="div", type="checkbox", name="rules", label="I agree to the terms of service"),
    ])

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert ready is True, detail
    assert filled == 2
    assert page.locators["#rules"].checked is True


def test_live_form_does_not_check_an_optional_marketing_checkbox() -> None:
    observer = _live_observer(build_newsletter_identity("org_demo", "account_1"))
    page = _LiveForm([
        _live_control("email", type="email", name="email", label="Email address", required=True),
        _live_control("marketing", type="checkbox", name="newsletter_opt_in", label="I agree to receive newsletters and marketing offers"),
    ])

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert ready is True, detail
    assert filled == 1
    assert page.locators["#marketing"].checked is False


def test_live_form_never_writes_to_a_credential_control() -> None:
    # The blocked check runs per control, so a password box named after the
    # mailbox is refused rather than given the account address.
    observer = _live_observer(build_newsletter_identity("org_demo", "account_1"))
    page = _LiveForm([
        _live_control("pw", type="password", name="email", label="Confirm your email", required=True),
    ])

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert ready is False
    assert filled == 0
    assert page.locators["#pw"].fill_calls == []
    assert "Publisher signup password is required" in detail


def test_live_form_fills_a_repeated_email_control_from_the_account_mailbox() -> None:
    observer = _live_observer(build_newsletter_identity("org_demo", "account_1"))
    page = _LiveForm([
        _live_control("email", type="email", name="email", label="Email address", required=True),
        _live_control("email-confirm", type="email", name="email_confirm", label="Confirm your email address", required=True),
    ])

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert ready is True, detail
    assert filled == 2
    assert page.locators["#email"].value == "test@example.com"
    assert page.locators["#email-confirm"].value == "test@example.com"


def test_live_form_names_the_free_text_field_it_will_not_invent_an_answer_for() -> None:
    observer = _live_observer(build_newsletter_identity("org_demo", "account_1"))
    page = _LiveForm([
        _live_control("comments", tag="textarea", type="textarea", name="comments", label="How did you hear about us?", required=True),
    ])

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert ready is False
    assert filled == 0
    assert "How did you hear about us?" in detail


def test_live_form_leaves_an_already_filled_control_alone() -> None:
    observer = _live_observer(build_newsletter_identity("org_demo", "account_1"))
    page = _LiveForm([_live_control("city", name="city", label="City", required=True)])
    page.locators["#city"].value = "Operator Approved Town"

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert ready is True, detail
    assert filled == 0
    assert page.locators["#city"].fill_calls == []
    assert page.locators["#city"].value == "Operator Approved Town"


def test_live_form_chooses_a_real_option_of_a_required_select() -> None:
    observer = _live_observer(build_newsletter_identity("org_demo", "account_1"))
    page = _LiveForm([
        _live_control(
            "sector",
            tag="select",
            type="select",
            name="sector",
            label="Sector",
            required=True,
            selected_index=0,
            options=[
                {"value": "", "label": "Select one"},
                {"value": "media", "label": "Media"},
                {"value": "other", "label": "Prefer not to say"},
            ],
        ),
    ])

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert ready is True, detail
    assert filled == 1
    # The opt-out is the honest answer for a placeholder identity.
    assert page.locators["#sector"].select_calls == ["other"]


def test_live_form_reports_a_control_that_did_not_retain_its_value() -> None:
    # Reactive forms replace the input node on the first input event, so the
    # original node can report the filled value while the live form holds none.
    observer = _live_observer(build_newsletter_identity("org_demo", "account_1"))
    page = _LiveForm([_live_control("city", name="city", label="City", required=True)], drops_after_fill=True)

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert ready is False
    assert "city" in detail
    assert "retain" in detail


class _StepControl:
    """One resolved control on the two-step page double."""

    def __init__(self, page: "_StepPage", role: str) -> None:
        self._page = page
        self._role = role

    @property
    def first(self) -> "_StepControl":
        # ``_newsletter_locator`` resolves ``.first`` before usability checks.
        return self

    def count(self) -> int:
        return 1 if self._page.present(self._role) else 0

    def is_visible(self, timeout: int = 0) -> bool:
        return self._page.present(self._role)

    def is_enabled(self, timeout: int = 0) -> bool:
        return True

    def click(self, timeout: int = 0, no_wait_after: bool = False) -> None:
        self._page.click(self._role)

    def fill(self, value: str, timeout: int = 0) -> None:
        self._page.filled = value

    def input_value(self, timeout: int = 0) -> str:
        return self._page.filled

    def press(self, key: str, timeout: int = 0) -> None:
        pass


class _MissingControl:
    @property
    def first(self) -> "_MissingControl":
        return self

    def count(self) -> int:
        return 0


class _StepPage:
    """A newsletter grid whose email field only appears after a card is chosen.

    Matches ``dailyaibrief.com/newsletters``: the landing page is a set of
    newsletter cards, and the signup field mounts only once one is selected.
    """

    def __init__(self, *, entry_selector: str | None = None, email_selector: str = "#email", click_error: bool = False) -> None:
        self.url = "https://news.example.com/newsletters"
        self.entry_selector = entry_selector
        self.email_selector = email_selector
        self.email_visible = False
        self.entry_clicks = 0
        self.click_error = click_error
        self.filled = ""

    def evaluate(self, _script: str) -> str:
        return "complete"

    def title(self) -> str:
        return "Example newsletters"

    def locator(self, selector: str):
        if selector == "body":
            return _BodyLocator("Subscribe to our newsletters")
        role = self._role_for(selector)
        return _MissingControl() if role is None else _StepControl(self, role)

    def _role_for(self, selector: str) -> str | None:
        if self.entry_selector and selector == self.entry_selector:
            return "entry"
        # Every email query the observer tries -- the configured selector and
        # the generic ``:visible`` fallbacks -- resolves to the same control.
        if "email" in selector:
            return "email"
        return None

    def present(self, role: str) -> bool:
        if role == "entry":
            return self.entry_selector is not None
        return self.email_visible

    def click(self, role: str) -> None:
        if self.click_error:
            raise RuntimeError("the card could not be activated")
        if role == "entry":
            self.entry_clicks += 1
            self.email_visible = True


def _step_observer(events: list[tuple[str, str]], **config: object) -> BrowserObserver:
    observer = BrowserObserver(
        "session-entry",
        "profile-entry",
        0,
        purpose="newsletter",
        newsletter_config={"account_email": "ops@example.com", "email_selector": "#email", **config},
        report_event=lambda _sid, event, detail, _result: events.append((event, detail)),
    )
    observer._newsletter_started_at = time.monotonic()
    return observer


def _events(events: list[tuple[str, str]], name: str) -> list[str]:
    return [detail for event, detail in events if event == name]


def test_configured_entry_step_reveals_the_form_instead_of_reporting_manual_review() -> None:
    """A card grid must be entered, not abandoned.

    ``dailyaibrief.com/newsletters`` renders its email field only after a card
    is selected, so the first tick found no field and reported manual review
    before anything was typed -- and the profile was then marked TEST_FAILED.
    """
    events: list[tuple[str, str]] = []
    observer = _step_observer(events, entry_selector=".newsletter-card")
    page = _StepPage(entry_selector=".newsletter-card")

    _handle_newsletter_signup_tick(observer, page, "news.example.com")

    assert page.entry_clicks == 1
    assert page.email_visible is True
    assert len(_events(events, "newsletter.signup.entry_activated")) == 1
    assert _events(events, "newsletter.signup.manual_review") == []
    # The entry click becomes a replayable action, so the second run for this
    # newsletter does not have to introspect the page again.
    assert {"kind": "click", "target": "entry_control"} in observer._newsletter_skill_actions


def test_entry_step_is_clicked_only_once_across_ticks() -> None:
    events: list[tuple[str, str]] = []
    observer = _step_observer(events, entry_selector=".newsletter-card")
    page = _StepPage(entry_selector=".newsletter-card")

    _handle_newsletter_signup_tick(observer, page, "news.example.com")
    _handle_newsletter_signup_tick(observer, page, "news.example.com")

    assert page.entry_clicks == 1


def test_entry_step_is_never_retried_after_a_failed_click() -> None:
    events: list[tuple[str, str]] = []
    observer = _step_observer(events, entry_selector=".newsletter-card")
    page = _StepPage(entry_selector=".newsletter-card", click_error=True)

    _handle_newsletter_signup_tick(observer, page, "news.example.com")
    _handle_newsletter_signup_tick(observer, page, "news.example.com")

    assert page.entry_clicks == 0
    # One attempt, one report -- a page that keeps throwing must not loop.
    reported = _events(events, "newsletter.signup.manual_review")
    assert len(reported) == 1
    assert "RuntimeError" in reported[0]


def test_a_profile_without_an_entry_step_behaves_exactly_as_before() -> None:
    events: list[tuple[str, str]] = []
    observer = _step_observer(events)
    observer._newsletter_started_at = time.monotonic() - 30
    page = _StepPage()

    _handle_newsletter_signup_tick(observer, page, "news.example.com")

    assert page.entry_clicks == 0
    assert _events(events, "newsletter.signup.manual_review") == ["The configured email field was not visible on the signup page"]


def test_an_entry_step_that_reveals_nothing_is_reported_as_the_entry_step() -> None:
    # A configured-but-wrong selector must say so, or the operator is told the
    # email field is missing when the real fault is the entry step.
    events: list[tuple[str, str]] = []
    observer = _step_observer(events, entry_selector=".newsletter-card")
    observer._newsletter_entry_clicked = True
    observer._newsletter_entry_at = time.monotonic() - 30
    page = _StepPage()

    _handle_newsletter_signup_tick(observer, page, "news.example.com")

    assert _events(events, "newsletter.signup.manual_review") == ["The configured entry step did not reveal the email field on the signup page"]
