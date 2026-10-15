from sqlalchemy import create_engine, inspect, text

import app.db as db_module


def test_legacy_workspace_settings_get_newsletter_agent_columns(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "_newsletter_agent_ensured", False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE workspace_settings (
                    id VARCHAR(96) PRIMARY KEY,
                    organization_id VARCHAR(96) NOT NULL,
                    default_group_capacity INTEGER NOT NULL DEFAULT 300,
                    default_login_concurrency INTEGER NOT NULL DEFAULT 4,
                    max_concurrent_operations INTEGER NOT NULL DEFAULT 4,
                    max_active_profiles INTEGER NOT NULL DEFAULT 50,
                    observer_interval_ms INTEGER NOT NULL DEFAULT 1000,
                    session_idle_timeout_minutes INTEGER NOT NULL DEFAULT 30,
                    auto_close_completed_sessions BOOLEAN NOT NULL DEFAULT 1,
                    retain_audit_days INTEGER NOT NULL DEFAULT 365,
                    updated_at TIMESTAMP NOT NULL
                )
                """
            )
        )

    db_module._ensure_workspace_settings_agent_columns()

    columns = {column["name"] for column in inspect(engine).get_columns("workspace_settings")}
    assert {
        "newsletter_agent_enabled",
        "newsletter_agent_provider",
        "newsletter_agent_model",
        "newsletter_agent_temperature",
        "newsletter_agent_max_steps",
        "newsletter_agent_timeout_seconds",
        "newsletter_agent_shadow_mode",
        "newsletter_agent_live_execution_enabled",
        "openrouter_api_key_encrypted",
    } <= columns

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspace_settings (id, organization_id, updated_at) "
                "VALUES ('settings_test', 'org_test', CURRENT_TIMESTAMP)"
            )
        )
        row = connection.execute(
            text(
                "SELECT newsletter_agent_enabled, newsletter_agent_provider, "
                "newsletter_agent_model, newsletter_agent_max_steps, "
                "newsletter_agent_timeout_seconds, newsletter_agent_shadow_mode, "
                "newsletter_agent_live_execution_enabled "
                "FROM workspace_settings WHERE id = 'settings_test'"
            )
        ).one()

    assert row == (0, "openrouter", "openrouter/free", 12, 30, 1, 0)
