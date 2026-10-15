from app.newsletter_agent import (
    DEFAULT_NEWSLETTER_AGENT_CHAIN,
    NewsletterAgentProvider,
    normalize_newsletter_agent_chain,
    request_newsletter_action_plan,
)
import app.newsletter_agent as agent_module


def test_default_chain_has_main_two_free_fallbacks_and_disabled_fourth() -> None:
    chain = normalize_newsletter_agent_chain(None)
    assert [entry["name"] for entry in chain] == ["Main", "Fallback 1", "Fallback 2", "Fallback 3"]
    assert [entry["enabled"] for entry in chain] == [True, True, True, False]
    assert chain[0]["model"] == "inclusionai/ling-3.0-flash-vl:free"
    assert chain[1]["model"] == "nex-agi/Nex-N2.5-Mini:free"
    assert chain[2]["model"] == "openrouter/free"
    assert all("deepseek" not in str(entry["model"]).lower() for entry in chain)


def test_chain_is_ordered_dynamic_and_bounded_to_eight_entries() -> None:
    raw = [
        {"priority": 9, "name": "Ninth", "enabled": True, "provider": "openrouter", "model": "model-9"},
        {"priority": 1, "name": "Main", "enabled": True, "provider": "openrouter", "model": "model-1"},
    ] + [{"priority": index, "name": f"Agent {index}", "enabled": True, "provider": "openrouter", "model": f"model-{index}"} for index in range(2, 10)]
    chain = normalize_newsletter_agent_chain(raw)
    assert len(chain) == 8
    assert [entry["priority"] for entry in chain] == list(range(1, 9))
    assert chain[0]["model"] == "model-1"


def test_chain_skips_disabled_entries_and_stops_after_first_valid_plan() -> None:
    calls = []

    def decide(_observation, _allowed_actions, entry):
        calls.append(entry["name"])
        if entry["name"] == "Fallback 2":
            return {"decision": "EXECUTE_PLAN", "confidence": 0.95, "actions": [{"action": "SUBMIT", "control_id": "ctrl_submit"}]}
        return None

    provider = NewsletterAgentProvider(decide=decide)
    result = provider.decide_with_chain(
        {"task": "newsletter_signup"},
        {"SUBMIT"},
        [
            {"priority": 1, "name": "Main", "enabled": True, "provider": "openrouter", "model": "main", "max_calls": 1, "min_confidence": 0.75},
            {"priority": 2, "name": "Fallback 1", "enabled": False, "provider": "openrouter", "model": "disabled", "max_calls": 1, "min_confidence": 0.75},
            {"priority": 3, "name": "Fallback 2", "enabled": True, "provider": "openrouter", "model": "fallback", "max_calls": 2, "min_confidence": 0.75},
            {"priority": 4, "name": "Fallback 3", "enabled": True, "provider": "openrouter", "model": "never-called", "max_calls": 1, "min_confidence": 0.75},
        ],
        global_max_calls=4,
    )
    assert result["_agent_name"] == "Fallback 2"
    assert result["_provider"] == "openrouter"
    assert calls == ["Main", "Fallback 2"]


def test_chain_global_budget_wins_over_per_agent_budgets() -> None:
    calls = []
    provider = NewsletterAgentProvider(decide=lambda _observation, _actions, entry: calls.append(entry["name"]) or None)
    assert provider.decide_with_chain(
        {"task": "newsletter_signup"},
        {"SUBMIT"},
        [
            {"priority": 1, "name": "Main", "enabled": True, "provider": "openrouter", "model": "main", "max_calls": 8, "min_confidence": 0.75},
            {"priority": 2, "name": "Fallback", "enabled": True, "provider": "openrouter", "model": "fallback", "max_calls": 8, "min_confidence": 0.75},
        ],
        global_max_calls=4,
    ) is None
    assert calls == ["Main", "Main", "Main", "Main"]


def test_action_plan_falls_through_invalid_agent_and_stops_after_valid_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_module,
        "_page_snapshot",
        lambda *_args, **_kwargs: {"target": "signup_plan", "controls": [{"control_id": "control_submit", "selector": "#submit", "tag": "button"}], "page": {"body": "signup"}},
    )
    calls = []

    def fake_request(_observation, entry, _settings):
        calls.append(entry["name"])
        if entry["name"] == "Main":
            return {"decision": "EXECUTE_PLAN", "confidence": 0.99, "actions": [{"action": "SUBMIT", "control_id": "invented"}]}, 10, None
        return {"decision": "EXECUTE_PLAN", "confidence": 0.91, "actions": [{"action": "SUBMIT", "control_id": "control_submit"}]}, 20, None

    monkeypatch.setattr(agent_module, "_request_action_plan_once", fake_request)
    plan = request_newsletter_action_plan(
        object(),
        agent_settings={
            "enabled": True,
            "api_key": "test-key",
            "max_ai_calls": 4,
            "agent_chain": [
                {"priority": 1, "name": "Main", "enabled": True, "provider": "openrouter", "model": "main", "max_calls": 1, "min_confidence": 0.75},
                {"priority": 2, "name": "Fallback 1", "enabled": True, "provider": "openrouter", "model": "fallback", "max_calls": 1, "min_confidence": 0.75},
                {"priority": 3, "name": "Never called", "enabled": True, "provider": "openrouter", "model": "never", "max_calls": 1, "min_confidence": 0.75},
            ],
        },
    )
    assert plan["_agent_name"] == "Fallback 1"
    assert plan["_calls"] == 2
    assert calls == ["Main", "Fallback 1"]
    assert plan["_control_map"]["control_submit"]["selector"] == "#submit"


def test_action_plan_accepts_openai_only_configuration(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_module,
        "_page_snapshot",
        lambda *_args, **_kwargs: {"target": "signup_plan", "controls": [{"control_id": "control_submit", "selector": "#submit", "tag": "button"}], "page": {"body": "signup"}},
    )
    monkeypatch.setattr(
        agent_module,
        "_request_action_plan_once",
        lambda _observation, _entry, _settings: (
            {"decision": "EXECUTE_PLAN", "confidence": 0.91, "actions": [{"action": "SUBMIT", "control_id": "control_submit"}]},
            15,
            None,
        ),
    )
    plan = request_newsletter_action_plan(
        object(),
        agent_settings={
            "enabled": True,
            "openai_api_key": "test-key",
            "max_ai_calls": 4,
            "agent_chain": [{"priority": 1, "name": "OpenAI", "enabled": True, "provider": "openai", "model": "gpt-4o-mini", "max_calls": 1, "min_confidence": 0.75}],
        },
    )
    assert plan["_provider"] == "openai"
    assert plan["_control_map"]["control_submit"]["selector"] == "#submit"
