"""browser-use backed control resolution for newsletter signup forms.

This is the last automated resolver before a signup falls to manual review. It
runs only after the deterministic selector lookup, the replayed learned skill,
and the configured OpenRouter agent chain have all failed to resolve a control.

Integration shape
-----------------

browser-use supplies the model call and the agentic reasoning; **the existing
Playwright observer performs every browser action**. This module never opens a
browser and never touches the page beyond a read-only DOM snapshot.

That split is deliberate. browser-use manages its own browser session, and
pointing one at the same persistent Chrome profile the observer is already
driving would put two drivers on a single profile. That breaks the per-account
locks in ``app/worker/locks.py`` and the session guarantees
``BrowserObserver`` depends on. The selector this module returns is resolved
from the live DOM by ``_page_snapshot`` and then applied through the observer's
own ``_newsletter_locator``, so the existing validation, audit, and redaction
paths all still hold.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .newsletter_agent import _ALLOWED_TARGETS, _page_snapshot, _redact

logger = logging.getLogger(__name__)

# Kept small: this is a bounded fallback, not a step-by-step browser agent. One
# decision per session is enforced by the observer, and one attempt per decision
# is right here — re-asking the same model about the same snapshot only spends
# tokens to get the same answer.
_DEFAULT_MIN_CONFIDENCE = 0.70
_DEFAULT_TIMEOUT_SECONDS = 25.0

_SYSTEM_PROMPT = (
    "You resolve one control on a public newsletter signup page. "
    "You are given a list of controls already extracted from the live DOM, each with a "
    "control_id. Choose the single control that best matches the requested target, or "
    "answer none when no control matches. Reply with JSON only, shaped "
    '{"control_id": "<id>", "confidence": <0-1>}.'
)

# One entry per target in ``newsletter_agent._ALLOWED_TARGETS``; an unknown
# target is refused before any provider call is made.
_TARGET_DESCRIPTIONS = {
    "email_field": "the email address input where a subscriber types their address",
    "field": "the signup input matching the described field key",
    "submit_button": "the control that submits the newsletter signup form",
    "confirmation_message": "the message that confirms a subscription",
    "confirmation_action": "the control that confirms a subscription",
}


def browser_use_available() -> bool:
    """Return true when the optional browser-use package can be imported."""
    try:
        import browser_use  # noqa: F401
    except Exception:  # noqa: BLE001 - optional dependency
        return False
    return True


def _browser_use_llm(settings: dict[str, Any]):
    """Build a browser-use chat model, or return None when unavailable.

    All browser-use specific API surface is confined to this function so a
    version bump only needs one place updated. Any mismatch degrades to
    returning None, which the caller treats as "no decision".
    """
    try:
        from browser_use.llm import ChatOpenAI
    except Exception:  # noqa: BLE001 - package missing or API moved
        logger.debug("browser-use LLM layer unavailable")
        return None
    api_key = str(settings.get("api_key") or "").strip()
    if not api_key:
        return None
    model = str(settings.get("model") or "").strip() or "gpt-4o-mini"
    kwargs: dict[str, Any] = {"model": model, "api_key": api_key, "temperature": 0.0}
    raw_timeout = settings.get("timeout_seconds")
    try:
        timeout = max(2.0, min(float(raw_timeout), 120.0)) if raw_timeout is not None else _DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout = _DEFAULT_TIMEOUT_SECONDS
    # A hung provider call would stall the observer tick, so the timeout is
    # applied when the installed version accepts it. Older signatures raise
    # TypeError on the unexpected keyword, and those fall back to no timeout
    # rather than losing the resolver entirely.
    for attempt in ({**kwargs, "timeout": timeout}, kwargs):
        try:
            return ChatOpenAI(**attempt)
        except TypeError:
            continue
        except Exception:  # noqa: BLE001 - constructor drift across versions
            logger.debug("browser-use chat model could not be constructed")
            return None
    logger.debug("browser-use chat model rejected every supported signature")
    return None


def _choose_control(snapshot: dict[str, Any], target: str, settings: dict[str, Any]) -> dict[str, Any] | None:
    """Ask browser-use to select one control id from the live-DOM snapshot."""
    llm = _browser_use_llm(settings)
    if llm is None:
        return None
    controls = snapshot.get("controls") or []
    if not controls:
        return None
    # Only the redacted snapshot leaves this process: no credentials, no
    # cookies, and no Gmail content are reachable from here.
    lines = [
        f"- control_id={item.get('control_id')} tag={item.get('tag')} type={item.get('type')} "
        f"name={item.get('name')} id={item.get('id')} aria={item.get('aria')} "
        f"placeholder={item.get('placeholder')} text={item.get('text')}"
        for item in controls
    ]
    prompt = (
        f"Target: {target} — {_TARGET_DESCRIPTIONS.get(target, target)}\n"
        f"Field context: {snapshot.get('context') or {}}\n"
        f"Page title: {snapshot.get('page', {}).get('title')}\n"
        f"Page URL: {snapshot.get('page', {}).get('url')}\n\n"
        "Controls:\n" + "\n".join(lines) + "\n\nReply with JSON only."
    )
    try:
        from browser_use.llm.messages import SystemMessage, UserMessage
    except Exception:  # noqa: BLE001
        return None
    try:
        response = llm.invoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            UserMessage(content=prompt),
        ])
    except Exception as exc:  # noqa: BLE001 - a provider failure must not break the run
        logger.debug("browser-use selection call failed: %s", type(exc).__name__)
        return None
    return _parse_selection(getattr(response, "content", response))


def _parse_selection(content: Any) -> dict[str, Any] | None:
    if isinstance(content, list):
        content = "".join(str(item.get("text") or "") if isinstance(item, dict) else str(item) for item in content)
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def request_browser_use_suggestion(
    page: Any,
    target: str,
    context: dict[str, str] | None = None,
    allowed_domains: list[str] | None = None,
    agent_settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve one control with browser-use, or return None.

    Returns the same shape as
    ``newsletter_agent.request_newsletter_control_suggestion`` — a
    ``selector`` resolved from the live DOM plus the control it came from — so
    the observer call site treats both resolvers identically.
    """
    settings = dict(agent_settings or {})
    if not settings.get("browser_use_enabled"):
        return None
    if settings.get("shadow_mode"):
        return None
    if not settings.get("live_execution_enabled"):
        return None
    if not browser_use_available():
        return None
    if target not in _ALLOWED_TARGETS:
        return None
    if not str(settings.get("api_key") or "").strip():
        return None
    snapshot = _page_snapshot(page, target, context, allowed_domains or [])
    controls = snapshot.get("controls") or []
    by_id = {str(item.get("control_id")): item for item in controls if item.get("control_id")}
    if not by_id:
        return None
    selection = _choose_control(snapshot, target, settings)
    if not selection:
        return None
    control_id = str(selection.get("control_id") or "").strip()
    control = by_id.get(control_id)
    if not control:
        # The model named a control that is not in the live DOM. Refuse rather
        # than guessing, exactly as the OpenRouter path does.
        return None
    try:
        confidence = float(selection.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if confidence < float(settings.get("min_confidence", _DEFAULT_MIN_CONFIDENCE)):
        return None
    selector = str(control.get("selector") or "")
    if not selector:
        return None
    return {
        "selector": selector,
        "control": _redact(str(control.get("tag") or "control"), 40),
        "confidence": confidence,
    }
