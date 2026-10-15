import json
from types import SimpleNamespace

import app.newsletter_agent as newsletter_agent
from app.newsletter_execution import (
    AGENT_LEARNING,
    DETERMINISTIC,
    LIVE_FORM,
    MANUAL,
    SKILL_REPLAY,
    allows_newsletter_agent,
    choose_newsletter_execution_mode,
)
from app.observer import (
    CONFIRMED_CHALLENGE,
    NO_CHALLENGE,
    SUSPICIOUS_CHALLENGE,
    _newsletter_attach_challenge_listener,
    _newsletter_dom_challenge_reason,
    classify_newsletter_challenge,
)


def test_execution_mode_is_explicit_and_skill_first() -> None:
    assert choose_newsletter_execution_mode(profile_id=None, has_active_skill=True, agent_ready=True) == SKILL_REPLAY
    assert choose_newsletter_execution_mode(profile_id="profile_1", has_active_skill=False, agent_ready=True) == DETERMINISTIC
    assert choose_newsletter_execution_mode(profile_id=None, has_active_skill=False, agent_ready=True) == AGENT_LEARNING
    assert choose_newsletter_execution_mode(profile_id=None, has_active_skill=False, agent_ready=False) == LIVE_FORM
    assert choose_newsletter_execution_mode(profile_id=None, has_active_skill=False, agent_ready=False, live_form_enabled=False) == MANUAL


def test_a_saved_skill_and_a_selected_profile_both_beat_the_live_form() -> None:
    # The live route is the fallback for a newsletter with neither, so it must
    # never displace a cheaper or more certain one.
    assert choose_newsletter_execution_mode(profile_id=None, has_active_skill=True, agent_ready=False) == SKILL_REPLAY
    assert choose_newsletter_execution_mode(profile_id="profile_1", has_active_skill=False, agent_ready=False) == DETERMINISTIC


def test_a_ready_agent_still_learns_before_the_live_form_is_used() -> None:
    # A ready agent saves a replay skill, so it is preferred over a run that
    # introspects the page again on the next attempt.
    assert choose_newsletter_execution_mode(profile_id=None, has_active_skill=False, agent_ready=True, live_form_enabled=True) == AGENT_LEARNING


def test_the_live_form_mode_never_allows_a_provider_call() -> None:
    # The mode is the only authority for provider calls, so the live route must
    # stay outside it however the agent settings are configured.
    assert allows_newsletter_agent(LIVE_FORM) is False
    assert allows_newsletter_agent(AGENT_LEARNING) is True
    assert allows_newsletter_agent(DETERMINISTIC) is False
    assert allows_newsletter_agent(SKILL_REPLAY) is False


def test_profile_mode_never_becomes_agent_learning_from_workspace_learning_flag() -> None:
    assert choose_newsletter_execution_mode(profile_id="profile_1", has_active_skill=False, agent_ready=True) == DETERMINISTIC


def test_plan_validation_rejects_selectors_values_and_unknown_controls() -> None:
    controls = {"ctrl_email", "ctrl_submit"}
    valid = newsletter_agent.validate_newsletter_action_plan(
        {
            "decision": "EXECUTE_PLAN",
            "confidence": 0.94,
            "actions": [
                {"action": "FILL_EMAIL", "control_id": "ctrl_email"},
                {"action": "SUBMIT", "control_id": "ctrl_submit"},
            ],
            "expected_result": "SUBMITTED_OR_CONFIRMATION_REQUIRED",
        },
        controls,
    )
    assert valid is not None
    assert len(valid["actions"]) == 2

    assert newsletter_agent.validate_newsletter_action_plan(
        {
            "decision": "EXECUTE_PLAN",
            "confidence": 0.99,
            "actions": [{"action": "SUBMIT", "control_id": "missing", "selector": "#secret"}],
        },
        controls,
    ) is None
    assert newsletter_agent.validate_newsletter_action_plan(
        {
            "decision": "EXECUTE_PLAN",
            "confidence": 0.99,
            "actions": [{"action": "FILL_EMAIL", "control_id": "ctrl_email", "value": "user@example.com"}],
        },
        controls,
    ) is None


def test_openrouter_tier_fallback_stops_after_primary_success(monkeypatch) -> None:
    calls = []

    def fake_decide(_observation, _allowed_actions, model):
        calls.append(model)
        return {
            "decision": "EXECUTE_PLAN",
            "confidence": 0.95,
            "actions": [{"action": "SUBMIT", "control_id": "ctrl_submit"}],
            "expected_result": "SUBMITTED_OR_CONFIRMATION_REQUIRED",
        }

    provider = newsletter_agent.NewsletterAgentProvider(decide=fake_decide)
    result = provider.decide_with_tiers(
        {"task": "newsletter_signup", "controls": [{"control_id": "ctrl_submit"}]},
        {"SUBMIT"},
        {"primary_model": "primary-free", "secondary_model": "secondary-free", "free_router_model": "openrouter/free", "max_ai_calls": 4},
    )
    assert result is not None
    assert calls == ["primary-free"]


def test_openrouter_tier_fallback_uses_secondary_then_router(monkeypatch) -> None:
    calls = []

    def fake_decide(_observation, _allowed_actions, model):
        calls.append(model)
        if model == "router-free":
            return {
                "decision": "EXECUTE_PLAN",
                "confidence": 0.91,
                "actions": [{"action": "SUBMIT", "control_id": "ctrl_submit"}],
            }
        return None

    provider = newsletter_agent.NewsletterAgentProvider(decide=fake_decide)
    result = provider.decide_with_tiers(
        {"task": "newsletter_signup", "controls": [{"control_id": "ctrl_submit"}]},
        {"SUBMIT"},
        {"primary_model": "primary-free", "secondary_model": "secondary-free", "free_router_model": "router-free", "max_ai_calls": 4},
    )
    assert result is not None
    assert calls == ["primary-free", "primary-free", "secondary-free", "router-free"]


def test_all_provider_tiers_exhausted_returns_none() -> None:
    calls = []
    provider = newsletter_agent.NewsletterAgentProvider(decide=lambda *_args: calls.append(True) or None)
    result = provider.decide_with_tiers(
        {"task": "newsletter_signup"},
        {"SUBMIT"},
        {"primary_model": "primary-free", "secondary_model": "secondary-free", "free_router_model": "router-free", "max_ai_calls": 4},
    )
    assert result is None
    assert len(calls) == 4


def test_anti_bot_header_alone_is_suspicious() -> None:
    assert classify_newsletter_challenge(response_header=True, functional_form=True) == SUSPICIOUS_CHALLENGE
    assert classify_newsletter_challenge(response_header=False, functional_form=True) == NO_CHALLENGE
    assert classify_newsletter_challenge(challenge_url=True, interaction_blocked=True) == CONFIRMED_CHALLENGE


def test_rendered_widget_on_a_usable_form_is_only_suspicious() -> None:
    """A widget that has not displaced the form must not end the run.

    Signup forms routinely embed Turnstile or reCAPTCHA while leaving the email
    field and the submit button rendered and usable. Confirming a challenge
    there blocked deterministic runs before the email was ever typed, marked
    the reviewed profile TEST_FAILED and walked the newsletter toward
    AUTOMATION_BROKEN, while discovery read the same page as an ordinary 200.
    """
    assert classify_newsletter_challenge(visible_widget=True, functional_form=True) == SUSPICIOUS_CHALLENGE
    # With no usable form left, the same widget is still proof of a block.
    assert classify_newsletter_challenge(visible_widget=True, functional_form=False) == CONFIRMED_CHALLENGE


class _ChallengePage:
    """A page double exposing only what challenge detection reads."""

    def __init__(self, body: str = "", *, widget: bool = False, widget_visible: bool = True, widget_src: str = "") -> None:
        self.url = "https://news.example.com/subscribe"
        self.frames: list[object] = []
        self._body = body
        self._widget = widget
        self._widget_visible = widget_visible
        self._widget_src = widget_src

    def title(self) -> str:
        return ""

    def locator(self, selector: str):
        if selector == "body":
            return SimpleNamespace(inner_text=lambda timeout=None: self._body)
        # Only the widget query may report a hit; every other selector is absent.
        hit = self._widget and "iframe" in selector
        # A widget query that asks for visibility must miss a widget the page
        # never renders -- the invisible Turnstile/reCAPTCHA case.
        if hit and ":visible" in selector and not self._widget_visible:
            hit = False
        if not hit:
            return SimpleNamespace(count=lambda: 0)
        return SimpleNamespace(count=lambda: 1, first=SimpleNamespace(get_attribute=lambda _name: self._widget_src))


class _Observer:
    """The observer attributes the challenge listener touches."""

    def __init__(self) -> None:
        self._newsletter_challenge_reason: str | None = None
        self._newsletter_challenge_listener_attached = False


class _Response:
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status = status
        self.headers = headers


def _listening_page() -> tuple[_ChallengePage, list]:
    handlers: list = []
    page = _ChallengePage()
    page.on = lambda event, handler: handlers.append((event, handler))  # type: ignore[attr-defined]
    return page, handlers


def test_widget_words_in_page_copy_are_not_a_challenge() -> None:
    # An ad-technology site carries the words "turnstile" and "captcha" in its
    # own copy without any interstitial. Matching those as markers reported a
    # page whose signup form was perfectly usable as a confirmed challenge.
    page = _ChallengePage("how cloudflare turnstile and captcha protect signup forms")

    assert _newsletter_dom_challenge_reason(page) is None


def test_interstitial_copy_is_still_a_challenge() -> None:
    page = _ChallengePage("just a moment... checking your browser")

    assert _newsletter_dom_challenge_reason(page) == "DOM_JUST_A_MOMENT"


def test_rendered_widget_is_still_a_challenge() -> None:
    # Dropping the bare words must not lose the widget itself.
    page = _ChallengePage("subscribe to our newsletter", widget=True)

    assert _newsletter_dom_challenge_reason(page) == "CHALLENGE_IFRAME"


def test_invisible_widget_is_not_a_challenge() -> None:
    # Turnstile and reCAPTCHA v3 ship an invisible widget that sits in the
    # markup with no box and never gates the form. Treating its presence as a
    # rendered widget ended signups on a plainly usable form as BLOCKED_ANTIBOT.
    page = _ChallengePage("subscribe to our newsletter", widget=True, widget_visible=False)

    assert _newsletter_dom_challenge_reason(page) is None


def test_widget_reason_names_the_frame_it_blames() -> None:
    # The blocked run has to say which embed caused it; "CHALLENGE_IFRAME"
    # alone left an operator with a verdict and no evidence.
    page = _ChallengePage(
        "subscribe to our newsletter",
        widget=True,
        widget_src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/rcv0",
    )

    reason = _newsletter_dom_challenge_reason(page)

    assert reason is not None
    assert reason.startswith("CHALLENGE_IFRAME")
    assert "challenges.cloudflare.com" in reason


def test_cloudflare_proxy_headers_are_not_anti_bot_evidence() -> None:
    # Cloudflare sends these with every response it proxies, so matching the
    # bare ``cf-`` prefix flagged every Cloudflare-fronted newsletter before its
    # form had been read at all.
    page, handlers = _listening_page()
    observer = _Observer()
    _newsletter_attach_challenge_listener(page, observer)
    _, on_response = handlers[0]

    on_response(_Response(200, {"cf-ray": "8f2c1d4e5f6a7b8c-LHR", "cf-cache-status": "HIT"}))

    assert observer._newsletter_challenge_reason is None


def test_cloudflare_mitigation_header_is_anti_bot_evidence() -> None:
    # ``cf-mitigated`` is set only when Cloudflare actually intervened.
    page, handlers = _listening_page()
    observer = _Observer()
    _newsletter_attach_challenge_listener(page, observer)
    _, on_response = handlers[0]

    on_response(_Response(200, {"cf-mitigated": "challenge"}))

    assert observer._newsletter_challenge_reason == "ANTI_BOT_RESPONSE_HEADER"
