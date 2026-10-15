"""Direct, app-integrated newsletter signup runner adapted from newsbot.

This runner owns the public-form workflow. The app supplies its locked
Playwright page, authorized recipient, target URL, task lifecycle, and audit
callbacks. It does not launch a separate Selenium browser or read local input
files. A submit attempt is recorded as SUBMITTED only; confirmation remains a
separate state.
"""

from __future__ import annotations

import re
import time
import hashlib
import unicodedata
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from scrapling import Selector

from . import observer as _obs
from .newsletter_execution import AGENT_LEARNING, SKILL_REPLAY
from .newsletter_agent import request_newsletter_step_decision

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from .observer import BrowserObserver

_EMAIL_SELECTORS = (
    'input[type="email"]:visible',
    'input[autocomplete="email"]:visible',
    'input[name*="email" i]:visible',
    'input[id*="email" i]:visible',
    'input[placeholder*="email" i]:visible',
    'input[name*="courriel" i]:visible',
    'input[id*="courriel" i]:visible',
    'input[placeholder*="courriel" i]:visible',
    'input[name*="correo" i]:visible',
    'input[id*="correo" i]:visible',
    'input[placeholder*="correo" i]:visible',
    'input[aria-label*="email" i]:visible',
)
_ENTRY_WORDS = (
    "newsletter", "subscribe", "sign up for updates", "email updates",
    "mailing list", "join our list", "get updates", "join", "get the newsletter",
    "s'inscrire", "inscrivez-vous", "abonner", "abonnez-vous", "recevoir la newsletter",
    "suscribirse", "suscríbete", "boletín", "inscrever-se", "inscreva-se", "boletim",
    "anmelden", "abonnieren", "newsletter erhalten", "iscriviti", "abbonati", "aanmelden", "abonneren",
)
_SUBMIT_WORDS = (
    "subscribe", "sign up", "join", "join now", "join free", "submit", "register",
    "get updates", "get the newsletter", "send me updates", "count me in", "sign me up",
    "newsletter", "s'inscrire", "inscrivez-vous", "abonner", "abonnez-vous", "rejoindre", "rejoignez",
    "suscribir", "suscríbete", "suscribirme", "registrarse", "únete", "unete",
    "inscrever", "inscreva-se", "assinar", "anmelden", "abonnieren",
    "registrieren", "mitmachen", "iscriviti", "abbonati", "aanmelden", "abonneren",
)
_NEGATIVE_WORDS = ("unsubscribe", "opt out", "opt-out", "sign in", "log in", "login", "cancel", "se connecter", "iniciar sesion", "connexion", "desabonner", "abmelden")
_FORM_CONTEXT_WORDS = ("newsletter", "subscribe", "mailing list", "email updates", "stay updated", "abonnement", "inscription", "suscripcion", "boletin", "boletim", "anmelden", "abbonati")
_UNRELATED_CHECKBOX_WORDS = (
    "sms", "text message", "phone", "telephone", "third party", "third-party",
    "partner offers", "sponsor offers", "advertising", "share my data", "sell my data",
    "over 18", "18 years", "age confirmation", "i am an adult", "i'm an adult",
)
_ADDITIONAL_SUCCESS_TERMS = (
    "subscription successful", "successfully signed up", "you are now subscribed",
    "you're in!", "you are in!", "welcome aboard",
)
_SUCCESS_SURFACES = (
    '[role="dialog"]:visible', '[aria-modal="true"]:visible', 'dialog[open]:visible',
    '[class*="modal" i]:visible', '[class*="popup" i]:visible',
    '[role="status"]:visible', '[role="alert"]:visible', '[aria-live]:visible',
    'h1:visible, h2:visible, h3:visible, [role="heading"]:visible',
)
_TOPIC_CONTROLS_JS = """() => {
    const all = Array.from(document.querySelectorAll('input, select, textarea, [role="checkbox"]'));
    const visible = el => {
        const style = getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
    };
    return all.map((el, index) => {
        if (!((el.type || '').toLowerCase() === 'checkbox' || el.getAttribute('role') === 'checkbox') || !visible(el)) return null;
        const labels = Array.from(el.labels || []).map(label => label.innerText || label.textContent || '');
        const wrapping = el.closest('label');
        if (wrapping) labels.push(wrapping.innerText || wrapping.textContent || '');
        const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\\s+/);
        for (const id of labelledBy) {
            const node = id && document.getElementById(id);
            if (node) labels.push(node.innerText || node.textContent || '');
        }
        // Card layouts often put the checkbox beside a <strong> title rather
        // than inside a label. Use only the nearest compact, single-choice row.
        let row = el.parentElement;
        for (let depth = 0; row && depth < 4; depth++, row = row.parentElement) {
            if (row.querySelectorAll('input[type="checkbox"], [role="checkbox"]').length === 1) {
                const text = (row.innerText || '').trim();
                if (text && text.length <= 250) { labels.push(text); break; }
            }
        }
        return {
            index, tag: el.tagName.toLowerCase(), type: 'checkbox',
            form_index: el.closest('form') ? Array.from(document.forms).indexOf(el.closest('form')) : -1,
            id: el.id || '', name: el.getAttribute('name') || '',
            aria_label: el.getAttribute('aria-label') || '',
            title: el.getAttribute('title') || '', label: labels.join(' ').replace(/\\s+/g, ' ').trim().slice(0, 350),
            checked: el.checked === true || el.getAttribute('aria-checked') === 'true',
            disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true', hidden: false,
        };
    }).filter(Boolean);
}"""


def run_signup_tick(observer: "BrowserObserver", page: "Page", _host: str) -> None:
    """Find, fill, and submit one newsletter form step."""
    if observer._newsletter_manual_review_reported or observer._newsletter_submit_attempted:
        return
    if not observer._newsletter_started_at:
        observer._newsletter_started_at = time.monotonic()
        observer._newsletter_last_url = page.url or observer.start_url
        observer._emit("newsletter.signup.started", "Newsletter signup runner started")

    try:
        if str(page.evaluate("document.readyState") or "").lower() == "loading":
            return
    except AttributeError:  # lightweight page adapters may not expose evaluation
        pass
    except Exception:  # noqa: BLE001 - a navigating page is checked next tick
        return

    if _obs._handle_newsletter_popup(page, observer):
        return
    if _obs._newsletter_challenge_blocks_signup(page, observer):
        return

    email_field = _find_email_field(page, observer)
    if email_field is None:
        if _follow_signup_entry(page, observer):
            return
        if _adaptive_mode(observer):
            email_field = _obs._newsletter_agent_locator(page, observer, "email_field", {"field_key": "email", "field_type": "email"})
    if email_field is None:
        elapsed = time.monotonic() - _obs._newsletter_step_started_at(observer)
        if elapsed > max(10.0, observer._settle_seconds * 2):
            if observer._newsletter_entry_clicked:
                detail = "The configured entry step did not reveal the email field on the signup page"
            elif _obs._newsletter_config_lines(observer, "email_selector"):
                detail = "The configured email field was not visible on the signup page"
            else:
                detail = "No newsletter email field was found on the configured page"
            _obs._finish_newsletter(
                observer,
                "newsletter.signup.manual_review",
                detail,
                result="attention",
            )
        return

    if not observer._newsletter_fields_filled:
        email = str(observer.newsletter_config.get("account_email") or "").strip()
        if not email:
            _obs._finish_newsletter(observer, "newsletter.signup.failed", "The authorized recipient address is unavailable", result="failure")
            return
        try:
            if _safe_input_value(email_field) != email:
                _obs._newsletter_fill_text(email_field, email)
            observer._record_newsletter_skill_action({"kind": "fill", "target": "email_field", "field_key": "email"})
            observer._emit("newsletter.signup.email_filled", "The authorized recipient address was entered")
        except Exception as exc:  # noqa: BLE001
            _obs._finish_newsletter(observer, "newsletter.signup.manual_review", f"The newsletter email field could not be filled: {type(exc).__name__}", result="attention")
            return

        selected_topics, topic_detail = _select_newsletter_topics(page, observer)
        if topic_detail:
            _obs._finish_newsletter(observer, "newsletter.signup.manual_review", topic_detail, result="attention")
            return
        ready, detail, _filled = _obs._newsletter_fill_live_fields(page, observer)
        if not ready:
            _obs._finish_newsletter(observer, "newsletter.signup.manual_review", detail, result="attention")
            return
        if not _obs._newsletter_required_agreements_ready(page, observer):
            _obs._finish_newsletter(observer, "newsletter.signup.manual_review", "A required terms or privacy agreement could not be checked and verified", result="attention")
            return
        retained, detail = _obs._newsletter_verify_retained_fields(page, observer)
        if not retained:
            _obs._finish_newsletter(observer, "newsletter.signup.manual_review", detail, result="attention")
            return
        observer._newsletter_fields_filled = True
        observer._emit(
            "newsletter.signup.fields_filled",
            "The recipient address and approved required fields were retained"
            + (f"; selected {selected_topics} newsletter topic option(s)" if selected_topics else ""),
        )

    submit = _find_submit_control(page, email_field, observer)
    if submit is None and _adaptive_mode(observer):
        submit = _obs._newsletter_agent_locator(page, observer, "submit_button", {"control": "newsletter submit"})
    if submit is not None:
        _submit_once(observer, page, submit)
        return
    _submit_with_enter(observer, page, email_field)


def verify_signup_tick(observer: "BrowserObserver", page: "Page", _host: str) -> None:
    """Classify publisher response without mistaking submission for confirmation."""
    success = _visible_completion_signal(page, observer)
    if success:
        _obs._finish_newsletter(
            observer,
            "newsletter.signup.submitted",
            "The publisher displayed a signup completion message",
            result="success",
        )
        return
    if time.monotonic() < observer._newsletter_verify_not_before:
        return
    if _obs._newsletter_challenge_blocks_signup(page, observer):
        return

    body = _normalize("\n".join(_safe_body(surface) for surface in _surfaces(page)))
    baseline = _normalize(observer._newsletter_pre_submit_body or "")
    configured_failure = [_normalize(item) for item in _obs._newsletter_config_lines(observer, "failure_signals")]
    failure = next((term for term in configured_failure + [_normalize(item) for item in _obs._NEWSLETTER_FAILURE_TERMS] if term and term in body and term not in baseline), None)
    if failure:
        _obs._finish_newsletter(observer, "newsletter.signup.failed", "The publisher reported a signup validation error", result="failure")
        return
    if _obs._newsletter_continue_name_step(observer, page):
        return
    if _continue_topic_choice_step(observer, page):
        return
    if _continue_password_step(observer, page):
        return
    if _adaptive_post_submit_step(observer, page, body):
        return

    elapsed = time.monotonic() - (observer._newsletter_last_submit_at or observer._newsletter_started_at or time.monotonic())
    if elapsed < _obs._NEWSLETTER_POST_SUBMIT_SETTLE_SECONDS:
        return
    if elapsed >= max(20.0, observer._settle_seconds * 4):
        _obs._finish_newsletter(
            observer,
            "newsletter.signup.manual_review",
            "The form was submitted, but the publisher showed no completion or validation result",
            result="attention",
        )


def _adaptive_post_submit_step(observer, page, body: str) -> bool:
    """Ask the configured agent once per changed state, then verify locally."""
    if not _adaptive_mode(observer):
        return False
    settings = observer.newsletter_config.get("agent_settings")
    if not isinstance(settings, dict) or not settings.get("enabled"):
        return False
    budget = max(0, min(int(settings.get("max_ai_calls", 4)), 4))
    remaining = budget - observer._newsletter_step_ai_calls - observer._newsletter_agent_plan_calls
    if remaining <= 0:
        return False
    signature = hashlib.sha256(f"{page.url}\n{body}".encode("utf-8")).hexdigest()
    if signature in observer._newsletter_step_signatures:
        return False
    observer._newsletter_step_signatures.add(signature)
    observer._emit("newsletter.agent.step_invoked", "Classifying the current post-submit page state", result="started")
    decision = request_newsletter_step_decision(page, {**settings, "max_ai_calls": remaining})
    observer._newsletter_step_ai_calls += max(1, min(remaining, int(decision.get("_calls", remaining)) if decision else remaining))
    if not decision:
        observer._emit("newsletter.agent.no_decision", "The current page state had no validated agent decision", result="attention")
        return False
    action = decision["decision"]
    evidence = _normalize(decision.get("evidence") or "")
    if action == "COMPLETE":
        baseline = _normalize(observer._newsletter_pre_submit_body)
        if evidence and evidence in _normalize(body) and evidence not in baseline:
            _obs._finish_newsletter(observer, "newsletter.signup.submitted", "The publisher displayed a completion message verified on the current page", result="success")
            return True
        return False
    if action == "MANUAL_REVIEW":
        _obs._finish_newsletter(observer, "newsletter.signup.manual_review", "The next publisher step needs operator review", result="attention")
        return True


    if action != "CONTINUE" or observer._newsletter_adaptive_continuations >= 2:
        return False
    control = decision.get("control") or {}
    label = _normalize(" ".join(str(control.get(key) or "") for key in ("text", "aria", "title")))
    if not _has_word(label, ("continue", "next", "finish", "complete", "confirm", "continuer", "suivant", "terminer", "confirmer", "continuar", "siguiente", "confirmar", "weiter", "bestatigen", "avanti", "conferma")):
        return False
    if _has_word(label, _NEGATIVE_WORDS):
        return False
    try:
        locator = page.locator(str(decision["selector"])).first
        if not _obs._newsletter_locator_is_usable(locator):
            return False
        ready, detail, _filled = _obs._newsletter_fill_live_fields(page, observer)
        if not ready or not _obs._newsletter_required_agreements_ready(page, observer):
            _obs._finish_newsletter(observer, "newsletter.signup.manual_review", detail or "Required agreement could not be verified", result="attention")
            return True
        observer._newsletter_submission_response = False
        observer._newsletter_adaptive_continuations += 1
        locator.click(timeout=1800, no_wait_after=True)
        _mark_submitted(observer, page, "newsletter.signup.followup_submitted", "Activated a live, validated signup continuation")
        return True
    except Exception as exc:  # noqa: BLE001
        _obs._finish_newsletter(observer, "newsletter.signup.manual_review", f"The next signup step could not be activated: {type(exc).__name__}", result="attention")
        return True


def _adaptive_mode(observer) -> bool:
    mode = observer._newsletter_execution_mode
    if mode == AGENT_LEARNING:
        return True
    settings = observer.newsletter_config.get("agent_settings")
    return bool(mode == SKILL_REPLAY and isinstance(settings, dict)
                and settings.get("automatic_relearning_enabled") and settings.get("enabled")
                and settings.get("learning_mode") and settings.get("live_execution_enabled")
                and not settings.get("shadow_mode"))


def _visible_completion_signal(page, observer) -> bool:
    """Find completion evidence on the live page before considering more steps.

    Recommendation dialogs often appear immediately after a successful signup.
    Their optional checkboxes are never a continuation of the signup form.
    """
    terms = tuple(_normalize(term) for term in (
        _obs._newsletter_config_lines(observer, "success_signals")
        + list(_obs._NEWSLETTER_SUCCESS_TERMS)
        + list(_ADDITIONAL_SUCCESS_TERMS)
    ) if term)
    baseline = _normalize(observer._newsletter_pre_submit_body)
    for surface in _surfaces(page):
        for index, selector in enumerate(_SUCCESS_SURFACES):
            try:
                nodes = surface.locator(selector).all()[-20:]
            except Exception:  # noqa: BLE001 - a changing frame may lose its nodes
                continue
            for node in nodes:
                try:
                    if not node.is_visible(timeout=250):
                        continue
                    text = _normalize(node.inner_text(timeout=400)[:4000])
                    if any(term in text and (index < 5 or term not in baseline) for term in terms):
                        return True
                except Exception:  # noqa: BLE001 - one detached result is not evidence
                    continue
        body = _normalize(_safe_body(surface))
        if any(term in body and term not in baseline for term in terms):
            return True
    return False


def _surfaces(page):
    surfaces = [page]
    try:
        for frame in page.frames:
            if frame is not getattr(page, "main_frame", None):
                surfaces.append(frame)
    except Exception:  # noqa: BLE001
        pass
    return surfaces


def _find_email_field(page, observer):
    configured = _obs._newsletter_config_lines(observer, "email_selector")
    scrapling_hints = _scrapling_email_selector_hints(page)
    for surface in _surfaces(page):
        for selector in configured + scrapling_hints + list(_EMAIL_SELECTORS):
            try:
                locator = surface.locator(selector).first
                if _obs._newsletter_locator_is_usable(locator) and _obs._newsletter_locator_type_is_text(locator):
                    return locator
            except Exception:  # noqa: BLE001
                continue
        # newsbot's generic text-input fallback, constrained to a form whose
        # visible copy identifies it as newsletter signup.
        try:
            forms = surface.locator("form:visible").all()
        except Exception:  # noqa: BLE001
            forms = []
        for form in forms[:20]:
            try:
                context = _normalize(form.inner_text(timeout=500))
                if not _has_word(context, _FORM_CONTEXT_WORDS):
                    continue
                fields = form.locator(
                    'input[type="text"]:visible, input:not([type]):visible, input[type="search"]:visible'
                ).all()
                candidates = [field for field in fields[:10] if _obs._newsletter_locator_is_usable(field)]
                if len(candidates) == 1 and _obs._newsletter_locator_type_is_text(candidates[0]):
                    return candidates[0]
            except Exception:  # noqa: BLE001
                continue
    return None


def _scrapling_email_selector_hints(page) -> list[str]:
    """Use Scrapling to parse the *rendered* page and suggest safe live selectors.

    Scrapling reads the DOM snapshot; Playwright remains the authority for
    visibility, enablement, and all interactions. Values are never included in
    selector hints or persisted by this parser.
    """
    try:
        snapshot = Selector(page.content(), url=page.url or None)
        candidates = snapshot.css(
            'form input[type="email"], form input[autocomplete="email"], '
            'form input[name*="email"], form input[id*="email"], '
            'form input[placeholder*="email"], form input[aria-label*="email"]'
        )[:30]
    except Exception:  # noqa: BLE001 - parser hints are an optional locator aid
        return []
    hints: list[str] = []
    for candidate in candidates:
        try:
            attrs = getattr(candidate, "attrib", {}) or {}
            node_id = str(attrs.get("id") or "").strip()
            name = str(attrs.get("name") or "").strip()
            if node_id and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,119}", node_id):
                hints.append(f"#{node_id}")
            elif name and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", name):
                hints.append(f'input[name="{name}"]')
        except Exception:  # noqa: BLE001 - ignore malformed/unsafe attributes
            continue
    return list(dict.fromkeys(hints))[:12]


def _topic_key(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "", _normalize(value))
    return re.sub(r"(com|org|net|co|io|us)$", "", value)


def _topic_candidates(page) -> list[tuple[object, dict[str, object]]]:
    choices: list[tuple[object, dict[str, object]]] = []
    for surface in _surfaces(page):
        try:
            controls = surface.evaluate(_TOPIC_CONTROLS_JS)
        except Exception:  # noqa: BLE001 - a frame may be replacing its form
            continue
        if not isinstance(controls, list):
            continue
        for control in controls[:80]:
            if not isinstance(control, dict) or control.get("disabled"):
                continue
            if _obs.agreement_checkbox_key(control) is not None:
                continue
            label = _normalize(" ".join(str(control.get(key) or "") for key in (
                "name", "id", "aria_label", "title", "label",
            )))
            if not label or any(word in label for word in _UNRELATED_CHECKBOX_WORDS):
                continue
            choices.append((surface, control))
    return choices


def _target_topic_choices(observer, choices: list[tuple[object, dict[str, object]]]) -> list[tuple[object, dict[str, object]]]:
    name = _topic_key(str(observer.newsletter_config.get("newsletter_name") or ""))
    if not name or name in {"newsletter", "subscribe", "subscription"}:
        return []
    matched = []
    for surface, control in choices:
        label = _topic_key(str(control.get("label") or ""))
        if name in label:
            matched.append((surface, control))
    return matched


def _select_newsletter_topics(page, observer, *, require_target: bool = False) -> tuple[int, str | None]:
    """Select newsletter-topic checkboxes in the signup form, not unrelated opt-ins.

    The caller explicitly requested the available newsletter choices. Legal
    agreements are handled separately, and checkboxes for age, SMS, phone,
    partner advertising, or data sharing are never inferred as newsletter topics.
    """
    choices = _topic_candidates(page)
    if not choices:
        return 0, "No newsletter topic choice was found" if require_target else None
    matched = _target_topic_choices(observer, choices)
    if len(matched) == 1:
        target_surface, target_control = matched[0]
        form_index = target_control.get("form_index", -1)
        if form_index != -1:
            for surface, control in choices:
                if (surface is target_surface and control is not target_control
                        and control.get("form_index") == form_index and control.get("checked") is True):
                    other = _obs._newsletter_live_locator(surface, control)
                    if other is None:
                        return 0, "A different preselected newsletter could not be cleared"
                    try:
                        if other.is_checked(timeout=500):
                            try:
                                other.uncheck(timeout=1200)
                            except Exception:
                                other.click(timeout=1200)
                        if other.is_checked(timeout=700):
                            return 0, "A different preselected newsletter remained selected"
                    except Exception as exc:  # noqa: BLE001
                        return 0, f"A different preselected newsletter could not be cleared: {type(exc).__name__}"
        choices = matched
    elif len(choices) == 1 and not require_target:
        pass
    else:
        return 0, "Multiple newsletter choices were shown, but the saved newsletter could not be matched to exactly one checkbox"
    selected = 0
    for surface, control in choices:
        locator = _obs._newsletter_live_locator(surface, control)
        if locator is None or not _obs._newsletter_locator_is_usable(locator):
            return selected, "A newsletter topic checkbox was detected but could not be safely verified"
        try:
            if not locator.is_checked(timeout=500):
                _obs._newsletter_check_choice(locator)
                selected += 1
            if not locator.is_checked(timeout=700):
                return selected, "A newsletter topic checkbox did not remain selected"
        except Exception as exc:  # noqa: BLE001 - custom controls may need an operator
            return selected, f"A newsletter topic checkbox could not be selected: {type(exc).__name__}"
    return selected, None


def _continue_topic_choice_step(observer, page) -> bool:
    """Handle an email-first form whose next page contains only topic choices."""
    if observer._newsletter_adaptive_continuations >= 2:
        return False
    try:
        if page.locator('input[type="email"]:visible, input[autocomplete="email"]:visible').count():
            return False
    except Exception:  # noqa: BLE001
        return False
    choices = _topic_candidates(page)
    if not choices or len(_target_topic_choices(observer, choices)) != 1:
        return False
    submit = _find_submit_control(page, None, observer)
    signature = hashlib.sha256((str(page.url) + "\n" + _safe_body(page)).encode("utf-8")).hexdigest()
    if signature in observer._newsletter_step_signatures:
        return False
    observer._newsletter_step_signatures.add(signature)
    selected, detail = _select_newsletter_topics(page, observer, require_target=True)
    if detail:
        _obs._finish_newsletter(observer, "newsletter.signup.manual_review", detail, result="attention")
        return True
    if not _obs._newsletter_required_agreements_ready(page, observer):
        _obs._finish_newsletter(observer, "newsletter.signup.manual_review", "A required agreement on the topic-selection step could not be verified", result="attention")
        return True
    observer._newsletter_adaptive_continuations += 1
    observer._emit("newsletter.signup.topic_step", f"Selected the saved newsletter on a follow-up topic form ({selected} new check)")
    if submit is not None:
        _submit_once(observer, page, submit)
    else:
        _submit_followup_with_enter(observer, page, None)
    return True


def _continue_password_step(observer, page) -> bool:
    """Complete a password-only signup step with the site's saved secret."""
    if observer._newsletter_adaptive_continuations >= 2:
        return False
    try:
        fields = [field for surface in _surfaces(page) for field in surface.locator('input[type="password"]:visible').all()[:4]]
    except Exception:  # noqa: BLE001 - an embedded form may be navigating
        return False
    if not fields:
        return False
    signature = hashlib.sha256(("password\n" + str(page.url) + "\n" + _safe_body(page)).encode("utf-8")).hexdigest()
    if signature in observer._newsletter_step_signatures:
        return False
    observer._newsletter_step_signatures.add(signature)
    ready, detail, _filled = _obs._newsletter_fill_live_fields(page, observer)
    if not ready:
        _obs._finish_newsletter(observer, "newsletter.signup.manual_review", detail, result="attention")
        return True
    if not _obs._newsletter_required_agreements_ready(page, observer):
        _obs._finish_newsletter(observer, "newsletter.signup.manual_review", "A required agreement on the password step could not be verified", result="attention")
        return True
    observer._newsletter_adaptive_continuations += 1
    submit = _find_submit_control(page, fields[0], observer)
    if submit is not None:
        _submit_once(observer, page, submit)
    else:
        _submit_followup_with_enter(observer, page, fields[-1])
    return True


def _follow_signup_entry(page, observer) -> bool:
    if observer._newsletter_entry_clicked:
        return False
    for selector in _obs._newsletter_config_lines(observer, "entry_selector"):
        try:
            control = page.locator(selector).first
            if not _obs._newsletter_locator_is_usable(control):
                continue
            observer._newsletter_entry_clicked = True
            observer._newsletter_entry_at = time.monotonic()
            control.click(timeout=1800, no_wait_after=True)
            observer._record_newsletter_skill_action({"kind": "click", "target": "entry_control"})
            observer._emit("newsletter.signup.entry_activated", "Opened the configured newsletter signup form")
            return True
        except Exception as exc:  # noqa: BLE001 - one configured entry click is the limit
            if observer._newsletter_entry_clicked:
                _obs._finish_newsletter(observer, "newsletter.signup.manual_review", f"The configured entry step could not be activated: {type(exc).__name__}", result="attention")
                return False
    page_host = (urlparse(page.url or "").hostname or "").lower().rstrip(".")
    for surface in _surfaces(page):
        try:
            controls = surface.locator('a[href]:visible, button:visible, [role="button"]:visible').all()
        except Exception:  # noqa: BLE001
            continue
        for control in controls[:100]:
            try:
                if not control.is_enabled(timeout=300):
                    continue
                label = _normalize(" ".join((
                    control.inner_text(timeout=300) or "",
                    control.get_attribute("aria-label") or "",
                    control.get_attribute("title") or "",
                )))
                if _has_word(label, _NEGATIVE_WORDS) or not _has_word(label, _ENTRY_WORDS):
                    continue
                href = control.get_attribute("href") or ""
                if href:
                    target_host = (urlparse(href).hostname or page_host).lower().rstrip(".")
                    if page_host and target_host != page_host and not target_host.endswith("." + page_host):
                        continue
                observer._newsletter_entry_clicked = True
                observer._newsletter_entry_at = time.monotonic()
                control.click(timeout=1800, no_wait_after=True)
                observer._emit("newsletter.signup.entry_activated", "Opened the newsletter signup form")
                return True
            except Exception:  # noqa: BLE001 - a rerender is retried next tick
                continue
    return False


def _find_submit_control(page, email_field, observer):
    configured = _obs._newsletter_config_lines(observer, "submit_selector")
    surfaces = _surfaces(page)
    # Prefer the configured selector, then the containing form, then the page
    # surface. A click is allowed only for a clearly labelled signup control or
    # an actual submit input/button.
    for surface in surfaces:
        for selector in configured:
            try:
                locator = surface.locator(selector).first
                if _obs._newsletter_locator_is_usable(locator):
                    if not hasattr(locator, "get_attribute"):
                        return locator
                    label = _normalize(" ".join((
                        locator.inner_text(timeout=300) or "",
                        locator.get_attribute("value") or "",
                        locator.get_attribute("aria-label") or "",
                        locator.get_attribute("title") or "",
                    )))
                    kind = (locator.get_attribute("type") or "").casefold()
                    if not _has_word(label, _NEGATIVE_WORDS) and (
                        kind == "submit" or _has_word(label, _SUBMIT_WORDS)
                    ):
                        return locator
            except Exception:  # noqa: BLE001
                pass
        try:
            form = email_field.locator("xpath=ancestor::form[1]")
            scopes = [form] if form.count() else [surface]
        except Exception:  # noqa: BLE001
            scopes = [surface]
        for scope in scopes:
            try:
                controls = scope.locator(
                    'button:visible, input[type="submit"]:visible, [role="button"]:visible'
                ).all()
            except Exception:  # noqa: BLE001
                continue
            for control in controls[:60]:
                try:
                    if not control.is_enabled(timeout=300):
                        continue
                    label = _normalize(" ".join((
                        control.inner_text(timeout=300) or "",
                        control.get_attribute("value") or "",
                        control.get_attribute("aria-label") or "",
                        control.get_attribute("title") or "",
                    )))
                    if _has_word(label, _NEGATIVE_WORDS):
                        continue
                    kind = (control.get_attribute("type") or "").casefold()
                    if kind == "submit" or _has_word(label, _SUBMIT_WORDS):
                        if _obs._newsletter_locator_is_usable(control):
                            return control
                except Exception:  # noqa: BLE001
                    continue
    return None


def _submit_once(observer, page, control) -> None:
    before_url = str(page.url or "")
    try:
        _obs._newsletter_attach_submission_listener(page, observer)
        observer._newsletter_pre_submit_body = "\n".join(_safe_body(surface) for surface in _surfaces(page))
        observer._newsletter_submit_attempted = True
        control.click(timeout=1800, no_wait_after=True)
        _mark_submitted(observer, page, "newsletter.signup.submit_clicked", "Activated the newsletter submit control exactly once")
        observer._record_newsletter_skill_action({"kind": "click", "target": "submit_button"})
    except Exception as exc:  # noqa: BLE001
        # A click can navigate successfully and still time out while the old
        # element detaches. Verify the changed page before declaring failure;
        # never click again because that could submit a duplicate signup.
        after_body = "\n".join(_safe_body(surface) for surface in _surfaces(page))
        if str(page.url or "") != before_url or after_body != observer._newsletter_pre_submit_body:
            _mark_submitted(observer, page, "newsletter.signup.submit_uncertain", "The signup control changed the page; checking its result after a click timeout")
            return
        _obs._finish_newsletter(observer, "newsletter.signup.manual_review", f"The signup control could not be activated: {type(exc).__name__}", result="attention")


def _submit_with_enter(observer, page, email_field) -> None:
    if observer._newsletter_enter_submit_attempted:
        _obs._finish_newsletter(observer, "newsletter.signup.manual_review", "No usable submit control was found", result="attention")
        return
    observer._newsletter_enter_submit_attempted = True
    try:
        _obs._newsletter_attach_submission_listener(page, observer)
        observer._newsletter_pre_submit_body = "\n".join(_safe_body(surface) for surface in _surfaces(page))
        observer._newsletter_submit_attempted = True
        email_field.press("Enter", timeout=1800)
        _mark_submitted(observer, page, "newsletter.signup.submit_entered", "Submitted the newsletter form with Enter exactly once")
    except Exception as exc:  # noqa: BLE001
        _obs._finish_newsletter(observer, "newsletter.signup.manual_review", f"The signup form could not be submitted: {type(exc).__name__}", result="attention")


def _submit_followup_with_enter(observer, page, field) -> None:
    """Try Enter once when a later form offers no usable signup button."""
    try:
        _obs._newsletter_attach_submission_listener(page, observer)
        observer._newsletter_pre_submit_body = "\n".join(_safe_body(surface) for surface in _surfaces(page))
        observer._newsletter_submit_attempted = True
        if field is not None:
            field.press("Enter", timeout=1800)
        else:
            page.keyboard.press("Enter")
        _mark_submitted(observer, page, "newsletter.signup.followup_entered", "Tried Enter on the signup continuation without a labeled button")
    except Exception as exc:  # noqa: BLE001 - the next tick verifies navigation where possible
        if "\n".join(_safe_body(surface) for surface in _surfaces(page)) != observer._newsletter_pre_submit_body:
            _mark_submitted(observer, page, "newsletter.signup.followup_entered", "The signup continuation changed after Enter; checking its result")
            return
        _obs._finish_newsletter(observer, "newsletter.signup.manual_review", f"The signup continuation could not be submitted with Enter: {type(exc).__name__}", result="attention")


def _mark_submitted(observer, page, event, detail) -> None:
    observer._newsletter_last_submit_at = time.monotonic()
    observer._newsletter_verify_not_before = observer._newsletter_last_submit_at + 2.0
    observer._newsletter_last_url = page.url or observer.start_url
    observer._emit(event, detail)


def _safe_input_value(locator) -> str:
    try:
        return str(locator.input_value(timeout=500) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _safe_body(page) -> str:
    try:
        body = str(page.locator("body").inner_text(timeout=1000) or "")
        return body if len(body) <= 12000 else body[:6000] + "\n" + body[-6000:]
    except Exception:  # noqa: BLE001
        return ""


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "").casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    return " ".join(plain.split())


def _has_word(value: str, words: tuple[str, ...]) -> bool:
    normalized = _normalize(value)
    return any(_normalize(word) in normalized for word in words)
