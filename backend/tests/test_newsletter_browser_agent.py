"""The optional browser-use resolver must never be load-bearing.

Every test here asserts the same property from a different angle: when
browser-use is missing, disabled, in shadow mode, or simply wrong, the resolver
returns None and the signup falls through to manual review exactly as it did
before this module existed.
"""

import app.newsletter_browser_agent as browser_agent


def _snapshot(control_id: str = "control_0") -> dict:
    return {
        "target": "email_field",
        "context": {},
        "allowed_confirmation_domains": [],
        "page": {"title": "Join our newsletter", "url": "https://news.example.com", "body": ""},
        "controls": [
            {
                "control_id": control_id,
                "selector": "#subscriber-email",
                "tag": "input",
                "type": "email",
                "name": "email",
                "id": "subscriber-email",
                "aria": "",
                "title": "",
                "placeholder": "",
                "text": "",
                "href_host": "",
                "has_href": False,
            }
        ],
    }


def _enabled_settings(**overrides) -> dict:
    return {
        "browser_use_enabled": True,
        "live_execution_enabled": True,
        "shadow_mode": False,
        "min_confidence": 0.70,
        "api_key": "test-key",
        "model": "gpt-4o-mini",
        **overrides,
    }


def test_disabled_setting_short_circuits() -> None:
    assert browser_agent.request_browser_use_suggestion(None, "email_field", agent_settings={}) is None


def test_shadow_mode_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent, "browser_use_available", lambda: True)
    settings = _enabled_settings(shadow_mode=True)
    assert browser_agent.request_browser_use_suggestion(None, "email_field", agent_settings=settings) is None


def test_live_execution_disabled_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent, "browser_use_available", lambda: True)
    settings = _enabled_settings(live_execution_enabled=False)
    assert browser_agent.request_browser_use_suggestion(None, "email_field", agent_settings=settings) is None


def test_missing_api_key_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent, "browser_use_available", lambda: True)
    settings = _enabled_settings(api_key="")
    assert browser_agent.request_browser_use_suggestion(None, "email_field", agent_settings=settings) is None


def test_unavailable_package_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent, "browser_use_available", lambda: False)
    monkeypatch.setattr(browser_agent, "_page_snapshot", lambda *a, **k: _snapshot())
    assert browser_agent.request_browser_use_suggestion(None, "email_field", agent_settings=_enabled_settings()) is None


def test_unknown_target_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent, "browser_use_available", lambda: True)
    assert browser_agent.request_browser_use_suggestion(None, "not_a_target", agent_settings=_enabled_settings()) is None


def test_validated_control_id_returns_the_live_dom_selector(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent, "browser_use_available", lambda: True)
    monkeypatch.setattr(browser_agent, "_page_snapshot", lambda *a, **k: _snapshot())
    monkeypatch.setattr(browser_agent, "_choose_control", lambda *a, **k: {"control_id": "control_0", "confidence": 0.91})

    result = browser_agent.request_browser_use_suggestion(None, "email_field", agent_settings=_enabled_settings())

    assert result is not None
    # The selector always comes from the live-DOM snapshot, never from the model.
    assert result["selector"] == "#subscriber-email"
    assert result["confidence"] == 0.91


def test_control_id_absent_from_the_live_dom_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent, "browser_use_available", lambda: True)
    monkeypatch.setattr(browser_agent, "_page_snapshot", lambda *a, **k: _snapshot())
    monkeypatch.setattr(browser_agent, "_choose_control", lambda *a, **k: {"control_id": "control_99", "confidence": 0.99})

    assert browser_agent.request_browser_use_suggestion(None, "email_field", agent_settings=_enabled_settings()) is None


def test_low_confidence_selection_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent, "browser_use_available", lambda: True)
    monkeypatch.setattr(browser_agent, "_page_snapshot", lambda *a, **k: _snapshot())
    monkeypatch.setattr(browser_agent, "_choose_control", lambda *a, **k: {"control_id": "control_0", "confidence": 0.20})

    assert browser_agent.request_browser_use_suggestion(None, "email_field", agent_settings=_enabled_settings()) is None


def test_unparseable_confidence_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent, "browser_use_available", lambda: True)
    monkeypatch.setattr(browser_agent, "_page_snapshot", lambda *a, **k: _snapshot())
    monkeypatch.setattr(browser_agent, "_choose_control", lambda *a, **k: {"control_id": "control_0", "confidence": "high"})

    assert browser_agent.request_browser_use_suggestion(None, "email_field", agent_settings=_enabled_settings()) is None


def test_empty_control_list_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent, "browser_use_available", lambda: True)
    empty = _snapshot()
    empty["controls"] = []
    monkeypatch.setattr(browser_agent, "_page_snapshot", lambda *a, **k: empty)

    assert browser_agent.request_browser_use_suggestion(None, "email_field", agent_settings=_enabled_settings()) is None


def test_parse_selection_handles_fenced_and_prose_wrapped_json() -> None:
    assert browser_agent._parse_selection('```json\n{"control_id": "c1", "confidence": 0.8}\n```') == {
        "control_id": "c1",
        "confidence": 0.8,
    }
    assert browser_agent._parse_selection('Here you go: {"control_id": "c1"} — hope that helps') == {"control_id": "c1"}
    assert browser_agent._parse_selection("no json here") is None
    assert browser_agent._parse_selection([{"text": '{"control_id": "c1"}'}]) == {"control_id": "c1"}


def test_missing_browser_use_package_reports_unavailable() -> None:
    # browser-use is an optional extra and is not installed in the test
    # environment, so the real import check must report unavailable rather
    # than raising.
    assert browser_agent.browser_use_available() is False
