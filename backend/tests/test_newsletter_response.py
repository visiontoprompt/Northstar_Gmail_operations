from datetime import datetime, timezone
from types import SimpleNamespace

from app import main
from app.models import BrowserSession
from app import service
from app.schemas import NewsletterSignupTestResponse


def test_newsletter_signup_status_does_not_leak_pending_result_as_api_500(monkeypatch) -> None:
    session = BrowserSession(
        id="session_response_test",
        organization_id="org_test",
        account_id="account_test",
        profile_id="profile_test",
        state="NEEDS_ATTENTION",
        login_state="REQUIRED",
        opened_at=datetime.now(timezone.utc),
        last_event_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        metadata_json={"purpose": "newsletter", "newsletter_result": "PENDING"},
    )

    monkeypatch.setattr(main, "session_response", lambda _session: {})
    response = main._newsletter_signup_test_response(None, session)

    assert response["result"] == "MANUAL_REVIEW"


def test_signup_response_exposes_adaptive_agent_invocation(monkeypatch) -> None:
    session = BrowserSession(
        id="session_adaptive_agent",
        organization_id="org_demo",
        account_id="account_test",
        profile_id="profile_test",
        state="NEEDS_ATTENTION",
        login_state="NEWSLETTER_MANUAL_REVIEW",
        metadata_json={
            "newsletter_id": "newsletter_test",
            "newsletter_signup_mode": "ADAPTIVE_LEARNING",
            "newsletter_result": "PENDING",
        },
    )

    class FakeResult:
        def all(self):
            return [SimpleNamespace(action="session.auto_login.newsletter.agent.invoked")]

    class FakeDb:
        def get(self, _model, _identifier):
            return None

        def scalars(self, _statement):
            return FakeResult()

    monkeypatch.setattr(main, "session_response", lambda _session: {})
    response = main._newsletter_signup_test_response(FakeDb(), session)

    assert response["signup_mode"] == "ADAPTIVE_LEARNING"
    assert response["agent_status"] == "INVOKED"


def test_signup_skip_does_not_reuse_another_newsletters_session(monkeypatch) -> None:
    wrong = BrowserSession(
        id="session_other_newsletter",
        organization_id="org_demo",
        account_id="account_test",
        profile_id="profile_test",
        state="NEEDS_ATTENTION",
        login_state="NEWSLETTER_MANUAL_REVIEW",
        created_at=datetime.now(timezone.utc),
        metadata_json={
            "purpose": "newsletter",
            "newsletter_id": "newsletter_a",
            "browser_opened": False,
        },
    )
    right = BrowserSession(
        id="session_matching_newsletter",
        organization_id="org_demo",
        account_id="account_test",
        profile_id="profile_test",
        state="RUNNING",
        login_state="AUTHENTICATED",
        created_at=datetime.now(timezone.utc),
        metadata_json={
            "purpose": "newsletter",
            "newsletter_id": "newsletter_b",
            "browser_opened": True,
        },
    )

    class FakeResult:
        def all(self):
            return [wrong, right]

    class FakeDb:
        def scalars(self, _statement):
            return FakeResult()

    monkeypatch.setattr(main, "local_browser_is_alive", lambda _profile_id, _session_id: True)

    assert main._active_newsletter_session(FakeDb(), "account_test", "newsletter_b") is right
    assert main._active_newsletter_session(FakeDb(), "account_test", "newsletter_c") is None


def test_submitted_signup_starts_gmail_confirmation_after_browser_close(monkeypatch) -> None:
    session = SimpleNamespace(
        id="signup_session",
        organization_id="org_demo",
        account_id="account_test",
        profile_id="profile_test",
        state="CLOSED",
        login_state="NEWSLETTER_SUBMITTED",
        metadata_json={
            "purpose": "newsletter",
            "newsletter_id": "newsletter_test",
            "newsletter_profile_id": "profile_test",
            "newsletter_subscription_id": "subscription_test",
            "newsletter_auto_confirm": True,
            "newsletter_confirmation_required": True,
            "browser_opened": False,
        },
    )

    class FakeDb:
        def get(self, model, identifier):
            if identifier == "subscription_test":
                return SimpleNamespace(state="SUBMITTED")
            return None

        def commit(self) -> None:
            pass

    calls: list[tuple[str, ...]] = []

    def fake_start_confirmation(db, organization_id, actor_id, newsletter_id, account_id, profile_id, subscription_id, **kwargs):
        calls.append((organization_id, actor_id, newsletter_id, account_id, profile_id, subscription_id, kwargs.get("job_id"), kwargs.get("task_id")))
        return SimpleNamespace(id="confirmation_session")

    monkeypatch.setattr(service, "start_newsletter_confirmation_test", fake_start_confirmation)

    service._maybe_start_newsletter_confirmation(FakeDb(), session)

    assert calls == [("org_demo", "system", "newsletter_test", "account_test", "profile_test", "subscription_test", None, None)]
    assert session.metadata_json["newsletter_confirmation_session_id"] == "confirmation_session"


def test_browser_close_callback_invokes_newsletter_confirmation_chain(monkeypatch) -> None:
    session = SimpleNamespace(
        id="signup_session",
        organization_id="org_demo",
        account_id="account_test",
        profile_id="profile_test",
        state="CLOSED",
        login_state="NEWSLETTER_SUBMITTED",
        metadata_json={"purpose": "newsletter", "browser_opened": True},
    )
    calls: list[str] = []

    class FakeDb:
        def get(self, model, identifier):
            return session if identifier == session.id else None

        def add(self, _item) -> None:
            calls.append("audit")

        def commit(self) -> None:
            calls.append("commit")

        def rollback(self) -> None:
            calls.append("rollback")

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(service, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(service, "_prune_unsaved_profile_folder", lambda _session: calls.append("prune"))
    monkeypatch.setattr(service, "_maybe_start_newsletter_confirmation", lambda _db, _session: calls.append("chain"))

    service._on_observer_browser_closed(session.id)

    assert calls == ["prune", "audit", "commit", "chain", "close"]


def test_signup_response_reflects_confirmation_subscription_state(monkeypatch) -> None:
    session = BrowserSession(
        id="session_confirmed",
        organization_id="org_demo",
        account_id="account_test",
        profile_id="profile_test",
        state="CLOSED",
        login_state="NEWSLETTER_SUBMITTED",
        metadata_json={
            "newsletter_id": "newsletter_test",
            "newsletter_profile_id": "profile_test",
            "newsletter_subscription_id": "subscription_test",
            "newsletter_auto_confirm": True,
            "newsletter_confirmation_session_id": "confirmation_session",
            "newsletter_result": "WAITING_CONFIRMATION",
        },
    )

    class FakeDb:
        def get(self, model, identifier):
            if identifier == "subscription_test":
                return SimpleNamespace(state="CONFIRMED")
            if identifier == "newsletter_test":
                return SimpleNamespace(confirmation_required=True)
            return None

    monkeypatch.setattr(main, "session_response", lambda _session: {})
    response = main._newsletter_signup_test_response(FakeDb(), session)

    assert response["result"] == "CONFIRMED"
    assert response["confirmation_required"] is True
    assert response["auto_confirm"] is True
    assert response["confirmation_session_id"] == "confirmation_session"


def test_live_form_signup_response_passes_session_schema() -> None:
    """A profile-free run records LIVE_FORM, which the nested session schema
    must accept; rejecting it made every signup response a 500."""
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        id="session_live_form",
        organization_id="org_demo",
        account_id="account_test",
        profile_id="profile_test",
        state="RUNNING",
        login_state="REQUIRED",
        runtime_type="LOCAL_PERSISTENT",
        opened_at=now,
        last_event_at=now,
        closed_at=None,
        created_at=now,
        failure_reason=None,
        account=SimpleNamespace(email="ops@example.com", group_id="group_test", secret=None),
        profile=SimpleNamespace(storage_reference="profile-reference", status="READY", authenticated_at=None),
        metadata_json={
            "purpose": "newsletter",
            "newsletter_id": "newsletter_test",
            "newsletter_signup_mode": "LIVE_FORM",
            "execution_mode": "LIVE_FORM",
            "newsletter_result": "IN_PROGRESS",
        },
    )

    class FakeDb:
        def get(self, _model, _identifier):
            return None

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [])

    response = main._newsletter_signup_test_response(FakeDb(), session)
    parsed = NewsletterSignupTestResponse.model_validate(response)

    assert parsed.execution_mode == "LIVE_FORM"
    assert parsed.session.execution_mode == "LIVE_FORM"
    assert parsed.session.operation_status == "IN_PROGRESS"
