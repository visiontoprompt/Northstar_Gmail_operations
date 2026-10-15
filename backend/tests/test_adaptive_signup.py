from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

import app.service as service
from app.config import Settings
from app.schemas import NEWSLETTER_SIGNABLE_PROFILE_STATUSES, NewsletterSignupTestRequest


class _ScriptedDb:
    """Answers ``scalar`` by position and records what the caller asked for.

    The signup path asks several different questions in sequence, so the double
    is scripted rather than generic: an exhausted script returns ``None``
    instead of pretending a missing row exists.
    """

    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.scalar_calls = 0
        self.added: list[Any] = []

    def scalar(self, _statement):
        self.scalar_calls += 1
        return self.results.pop(0) if self.results else None

    def add(self, item) -> None:
        self.added.append(item)


def _newsletter() -> SimpleNamespace:
    return SimpleNamespace(
        id="newsletter-1",
        status="ACTIVE",
        signup_url="https://news.example.com/subscribe",
        website_url="https://news.example.com",
        confirmation_required=False,
    )


def _profile(*, id: str = "nlprofile-1", version: int = 1, status: str = "CANDIDATE") -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        version=version,
        status=status,
        # ``begin_newsletter_profile_test`` audits the newsletter it belongs to.
        newsletter_id="newsletter-1",
        tested_at=None,
        configuration_json={"email_selector": "#email", "submit_selector": "button[type=submit]"},
    )


def _disabled_agent(_db, _organization_id) -> dict[str, Any]:
    """Every adaptive gate off, so only a profile can carry the run."""
    return {"enabled": False, "learning_mode": False, "live_execution_enabled": False, "shadow_mode": True}


def test_signup_request_allows_learning_without_profile() -> None:
    payload = NewsletterSignupTestRequest(account_id="account-1")

    assert payload.profile_id is None


def test_signup_without_a_profile_still_stops_when_nothing_was_discovered(monkeypatch) -> None:
    # No signable profile and no ready agent leaves no route at all, so the
    # request is rejected. The profile lookup must happen first: it is what
    # decides whether the agent is needed in the first place.
    #
    # Live-form resolution has to be switched off for this case to exist: while
    # it is on (the default) a profile-free run resolves the live page itself
    # and never reaches this gate. ``get_settings`` is cached, so the flag is
    # patched where the service reads it rather than through the environment.
    db: Any = _ScriptedDb(_newsletter(), None)
    monkeypatch.setattr(service, "_account_start_lock", lambda _account_id: nullcontext())
    monkeypatch.setattr(service, "_newsletter_agent_runtime_settings", _disabled_agent)
    monkeypatch.setattr(service, "get_settings", lambda: Settings(newsletter_live_form_enabled=False))

    with pytest.raises(service.ImportValidationError, match="No automation profile is available"):
        service.start_newsletter_signup_test(db, "org-1", "employee-1", "newsletter-1", "account-1", None)

    assert db.scalar_calls >= 2


def test_profile_free_signup_adopts_a_discovered_profile_instead_of_the_agent(monkeypatch) -> None:
    # Discovery auto-creates a CANDIDATE profile. Demanding the agent before a
    # first run made DETERMINISTIC unreachable, so a profile-free request must
    # pick up that profile and continue without any adaptive gate satisfied.
    profile = _profile()
    db: Any = _ScriptedDb(_newsletter(), profile, None, None)
    monkeypatch.setattr(service, "_account_start_lock", lambda _account_id: nullcontext())
    monkeypatch.setattr(service, "_newsletter_agent_runtime_settings", _disabled_agent)

    # Reaching the account lookup means the agent gate was passed, since no
    # agent setting is satisfied here.
    with pytest.raises(service.ImportValidationError, match="Account not found"):
        service.start_newsletter_signup_test(db, "org-1", "employee-1", "newsletter-1", "account-1", None)

    # The adopted profile enters testing so the run's outcome is recorded on it.
    assert profile.status == "TESTING"


def test_an_explicitly_requested_profile_is_used_rather_than_the_newest_one(monkeypatch) -> None:
    requested = _profile(id="nlprofile-requested", version=7)
    db: Any = _ScriptedDb(_newsletter(), requested, None, None)
    monkeypatch.setattr(service, "_account_start_lock", lambda _account_id: nullcontext())
    monkeypatch.setattr(service, "_newsletter_agent_runtime_settings", _disabled_agent)

    with pytest.raises(service.ImportValidationError, match="Account not found"):
        service.start_newsletter_signup_test(db, "org-1", "employee-1", "newsletter-1", "account-1", "nlprofile-requested")

    assert requested.status == "TESTING"


def test_only_adopted_profile_statuses_are_signable() -> None:
    # The adoption fallback selects on this set, so it is the authority for
    # which versions may run: a version nobody adopted (DISCOVERED), one a
    # session is already using (TESTING), or one an operator retired
    # (SUPERSEDED, REJECTED) must not be picked up silently.
    assert NEWSLETTER_SIGNABLE_PROFILE_STATUSES == ("CANDIDATE", "TEST_FAILED", "TEST_PASSED", "ACTIVE")
    assert not {"DISCOVERED", "TESTING", "SUPERSEDED", "REJECTED"} & set(NEWSLETTER_SIGNABLE_PROFILE_STATUSES)
