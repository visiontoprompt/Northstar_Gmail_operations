from __future__ import annotations

from app.newsletter_service import _sanitize_newsletter_skill


def test_skill_sanitizer_keeps_replay_facts_and_drops_values() -> None:
    sanitized = _sanitize_newsletter_skill(
        {
            "website_host": "news.example",
            "actions": [
                {
                    "kind": "locate",
                    "target": "email_field",
                    "field_key": "email",
                    "selector": "#email",
                    "control": {"tag": "input", "id": "email", "aria_label": "Email"},
                    "value": "employee@example.com",
                },
                {
                    "kind": "fill",
                    "target": "email_field",
                    "field_key": "email",
                    "value": "employee@example.com",
                },
            ],
        }
    )

    assert sanitized is not None
    assert sanitized["website_host"] == "news.example"
    assert len(sanitized["actions"]) == 2
    assert all("value" not in action for action in sanitized["actions"])
    assert sanitized["actions"][0]["control"] == {
        "tag": "input",
        "id": "email",
        "aria_label": "Email",
    }


def test_skill_sanitizer_bounds_actions_and_rejects_empty_skill() -> None:
    assert _sanitize_newsletter_skill({"actions": [{"kind": "noop", "target": "page"}]}) is None
    payload = {
        "actions": [{"kind": "click", "target": "submit_button", "selector": "#submit"}] * 40,
    }
    sanitized = _sanitize_newsletter_skill(payload)
    assert sanitized is not None
    assert len(sanitized["actions"]) == 24
