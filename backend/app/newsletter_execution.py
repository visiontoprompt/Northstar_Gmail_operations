"""Explicit execution modes for newsletter signup sessions.

The mode is chosen once when a session is created and is the only authority
for whether provider calls are allowed. Workspace learning settings configure
an adaptive request; they must never silently convert a profile run into an
agent run.
"""

from __future__ import annotations

SKILL_REPLAY = "SKILL_REPLAY"
DETERMINISTIC = "DETERMINISTIC"
AGENT_LEARNING = "AGENT_LEARNING"
LIVE_FORM = "LIVE_FORM"
MANUAL = "MANUAL"

NEWSLETTER_EXECUTION_MODES = frozenset({SKILL_REPLAY, DETERMINISTIC, AGENT_LEARNING, LIVE_FORM, MANUAL})


def choose_newsletter_execution_mode(
    *,
    profile_id: str | None,
    has_active_skill: bool,
    agent_ready: bool,
    skill_replay_enabled: bool = True,
    deterministic_enabled: bool = True,
    live_form_enabled: bool = True,
) -> str:
    """Choose the immutable mode for a signup session.

    A saved skill wins before any other route. A selected profile is strictly
    deterministic; global learning settings never override that choice.
    Profile-free requests prefer the learning agent when its explicit
    prerequisites are ready, then fall back to resolving the live form with no
    provider involved at all, and reach manual review only when live-form runs
    are switched off.
    """
    if has_active_skill and skill_replay_enabled:
        return SKILL_REPLAY
    if profile_id and deterministic_enabled:
        return DETERMINISTIC
    if agent_ready:
        return AGENT_LEARNING
    if live_form_enabled:
        return LIVE_FORM
    return MANUAL


def allows_newsletter_agent(execution_mode: str) -> bool:
    return execution_mode == AGENT_LEARNING


def is_valid_newsletter_execution_mode(value: str | None) -> bool:
    return str(value or "") in NEWSLETTER_EXECUTION_MODES
