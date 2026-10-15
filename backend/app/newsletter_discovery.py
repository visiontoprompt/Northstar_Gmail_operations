"""Bounded public-page newsletter discovery using Scrapling.

This module intentionally has no access to Gmail accounts, browser profiles,
cookies, credentials, or the application database. It fetches a small set of
public, same-origin pages and returns bounded form candidates for review.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

from scrapling.fetchers import Fetcher

from .newsletter_fields import semantic_field_key


MAX_PAGES = 8
MAX_BODY_BYTES = 1_500_000
FETCH_TIMEOUT_SECONDS = 12
# A challenge-protected public page is re-read with a real browser. That is
# slow and process-heavy, so one discovery run gets a small fixed allowance
# rather than a per-page one. See ``_StealthBudget``.
MAX_STEALTH_FETCHES = 2
STEALTH_FETCH_TIMEOUT_SECONDS = 25
MAX_DISCOVERY_RETRIES = 2
MAX_RETRY_AFTER_SECONDS = 30
MAX_CANDIDATES = 6
MAX_SIGNALS = 5
MAX_EVIDENCE = 6

_PATH_HINTS = ("newsletter", "subscribe", "newsletters", "email", "updates", "join")
_LINK_HINTS = ("newsletter", "subscribe", "email updates", "stay updated", "get updates", "mailing list", "join")
_SUCCESS_HINTS = ("thank", "subscribed", "subscription confirmed", "check your email", "you're in", "merci", "inscription confirmée", "gracias", "suscripción confirmada", "obrigado", "inscrição confirmada", "danke", "anmeldung erfolgreich", "grazie", "iscrizione confermata", "bedankt", "inschrijving bevestigd")
_FAILURE_HINTS = ("error", "invalid", "required", "failed", "unable", "erreur", "invalide", "obligatoire", "error", "inválido", "obligatorio", "erro", "inválido", "obrigatório", "ungültig", "pflichtfeld", "non valida", "ongeldig")
_EMAIL_HINTS = ("email", "e-mail", "mail")
_SUBMIT_HINTS = ("subscribe", "sign up", "signup", "join", "submit", "updates", "notify", "register", "s'inscrire", "abonner", "rejoindre", "suscribir", "suscríbete", "únete", "inscrever", "assinar", "anmelden", "abonnieren", "mitmachen", "iscriviti", "abbonati", "aanmelden", "abonneren")
_CONSENT_HINTS = (
    "privacy", "consent", "terms", "agree", "permission", "accept", "understand",
    "conditions", "rules", "policy", "confidentialite", "privacidad", "acepto",
    "entiendo", "concordo", "entendo", "datenschutz", "einverstanden", "zustimmen",
    "j'accepte", "ich akzeptiere", "ik ga akkoord", "accetto",
)
_SAFE_SELECTOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_MAX_FORM_FIELDS = 40
# Copy an interstitial shows on its own page. A bare "turnstile" or "captcha"
# occurs on any page that merely embeds the widget -- or, on a site that writes
# about ad technology, simply discusses it -- so matching those reported a
# working signup form as blocked. The widget iframe itself is still detected by
# the observer's frame check.
_DISCOVERY_BLOCK_MARKERS = (
    "cf-chl-",
    "challenge-platform",
    "just a moment",
    "checking your browser",
    "verify you are human",
)
# Headers an edge sets when it *mitigates* a request — as opposed to merely
# proxying it. Matching the bare ``cf-`` prefix was a false-positive machine:
# Cloudflare sets ``cf-cache-status``, ``cf-ray`` and friends on every
# successful response it fronts, so every Cloudflare-hosted newsletter was
# reported as BLOCKED_ANTIBOT on each request. ``cf-mitigated`` is the header
# Cloudflare actually sets when it challenged or blocked the request.
_MITIGATION_HEADER_MARKERS = ("cf-mitigated", "x-captcha", "x-challenge", "x-bot")

class NewsletterDiscoveryError(ValueError):
    """A safe, user-facing discovery failure."""


@dataclass(frozen=True)
class _FetchedPage:
    url: str
    response: object
    status: int
    fetch_mode: str = "normal"
    blocked_reason: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ``64:ff9b::/96`` is the well-known NAT64 prefix (RFC 6052): its last 32 bits
# carry an ordinary IPv4 address. It is how a host with no native IPv4 route
# reaches IPv4-only sites, and on such a network it is the *only* address DNS
# returns for them -- so classifying the wrapper instead of the destination it
# carries rejected every public site on that network at once.
# ``64:ff9b:1::/48`` (local-use NAT64, RFC 8215) is deliberately not unwrapped:
# by definition it translates inside a private network.
_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL_USE = ipaddress.IPv6Network("64:ff9b:1::/48")


def _is_non_public(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    """Whether ``ip`` is an address a discovery run must never be pointed at."""
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _carried_ipv4(
    ip: "ipaddress.IPv4Address | ipaddress.IPv6Address",
) -> ipaddress.IPv4Address | None:
    """The IPv4 destination an IPv6 transition address stands for, if any.

    NAT64 and IPv4-mapped addresses exist precisely to stand in for an IPv4
    destination, so the destination is what decides whether the host is public.
    The standard library classifies both ranges wholesale: IPv4-mapped is
    private as ``::ffff:0:0/96``, and ``64:ff9b::/96`` falls inside the ``::/8``
    reserved block. Either way an ordinary public IPv4 site reads as non-public
    the moment it is reached through one of them.
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64_WELL_KNOWN:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _host_public_failure(hostname: str) -> str | None:
    """Why ``hostname`` cannot be treated as public, or ``None`` when it can.

    The reason is returned rather than only a boolean because a bare "private
    address" verdict on an ordinary public domain is impossible to act on: the
    rejected address is what identifies a filtering DNS resolver, a captive
    portal, or an address family this guard is misjudging.
    """
    host = (hostname or "").strip().lower().rstrip(".")
    if not host or host in {"localhost", "localhost.localdomain"} or ".local" in host:
        return f"{host or 'the host'} is a local name"
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except (OSError, socket.gaierror):
        # Let Scrapling provide the normal unreachable error for an otherwise
        # syntactically valid public hostname.
        return None
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return f"{host} resolved to {address}, which is not a readable address"
        carried = _carried_ipv4(ip)
        if carried is not None:
            # Only as public as the IPv4 address it carries, so a NAT64 or
            # IPv4-mapped address that stands for a private destination is
            # still refused -- unwrapping narrows the guard to the destination
            # rather than loosening it.
            if _is_non_public(carried):
                return f"{host} resolved to {address}, which carries the non-public address {carried}"
            continue
        if ip.version == 6 and ip in _NAT64_LOCAL_USE:
            # Checked explicitly rather than left to ``is_private``: whether the
            # standard library treats this prefix as special varies by version,
            # and a guard whose verdict depends on that is how the well-known
            # prefix ended up rejecting public sites in the first place.
            return f"{host} resolved to {address}, which is a local-use NAT64 address"
        if _is_non_public(ip):
            return f"{host} resolved to {address}, which is a local, private, or reserved address"
    return None


def _validate_public_url(value: str, expected_host: str | None = None) -> str:
    parsed = urlsplit((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise NewsletterDiscoveryError("Discovery accepts a public http(s) URL without embedded credentials")
    failure = _host_public_failure(parsed.hostname)
    if failure is not None:
        raise NewsletterDiscoveryError(f"Discovery cannot inspect local or private network addresses ({failure})")
    host = parsed.hostname.lower().rstrip(".")
    if expected_host and host != expected_host:
        raise NewsletterDiscoveryError("Discovery is limited to the newsletter website's origin")
    if len(value) > 500:
        raise NewsletterDiscoveryError("The discovery URL is too long")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def validate_newsletter_runtime_url(value: str) -> str:
    """Validate a stored signup URL before Playwright is allowed to open it."""
    return _validate_public_url(value)


def _same_origin(url: str, expected_host: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower().rstrip(".") == expected_host


def _text(node: object) -> str:
    value = getattr(node, "text", "") or ""
    return " ".join(str(value).split())[:400]


def _attr(node: object, name: str) -> str:
    value = (getattr(node, "attrib", {}) or {}).get(name, "")
    return str(value or "").strip()


def _selector_candidates(tag: str, node: object) -> list[str]:
    candidates: list[str] = []
    node_id = _attr(node, "id")
    if node_id and _SAFE_SELECTOR.match(node_id):
        candidates.append(f"#{node_id}")
    name = _attr(node, "name")
    if name and _SAFE_SELECTOR.match(name):
        candidates.append(f'{tag}[name="{name}"]')
    autocomplete = _attr(node, "autocomplete")
    if autocomplete and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", autocomplete):
        candidates.append(f'{tag}[autocomplete="{autocomplete}"]')
    input_type = _attr(node, "type")
    if input_type and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", input_type):
        candidates.append(f'{tag}[type="{input_type}"]')
    if _attr(node, "aria-label"):
        candidates.append(f'{tag}[aria-label]')
    if _attr(node, "placeholder"):
        candidates.append(f'{tag}[placeholder]')
    candidates.append(tag)
    return list(dict.fromkeys(candidates))


def _selector_for(tag: str, node: object) -> str:
    candidates = _selector_candidates(tag, node)
    return candidates[0] if candidates else tag


def _is_hidden_or_disabled(node: object) -> bool:
    autocomplete = _attr(node, "autocomplete").lower()
    node_type = _attr(node, "type").lower()
    if _attr(node, "disabled") or _attr(node, "readonly") or node_type == "hidden" or ("password" in autocomplete and node_type != "password"):
        return True
    if _attr(node, "hidden").lower() in {"", "hidden", "true"} and "hidden" in (getattr(node, "attrib", {}) or {}):
        return True
    style = _attr(node, "style").replace(" ", "").lower()
    classes = set(_attr(node, "class").lower().split())
    return "display:none" in style or "visibility:hidden" in style or bool(classes & {"hidden", "gform_hidden", "sr-only"})


def _label_for(page: object, node: object) -> str:
    node_id = _attr(node, "id")
    if node_id and _SAFE_SELECTOR.match(node_id):
        for label in page.css("label")[:80]:
            if _attr(label, "for") == node_id:
                return _text(label)
    try:
        labels = node.xpath("ancestor::label[1]")
        if labels:
            return _text(labels[0])
    except Exception:  # pragma: no cover - parser-specific fallback
        pass
    return ""


def _required_state(node: object, label: str) -> bool | None:
    attrs = getattr(node, "attrib", {}) or {}
    if "required" in attrs or _attr(node, "aria-required").lower() == "true":
        return True
    if _attr(node, "aria-required").lower() == "false":
        return False
    if "*" in label or "required" in label.lower():
        return True
    return None


def _semantic_field_key(node: object, label: str) -> str:
    """Classify one parsed control through the shared field vocabulary.

    The decision itself lives in ``newsletter_fields`` so the live observer
    path reaches exactly the same verdict for the same control. This adapter
    only reads the attributes off a parsed Scrapling node.
    """
    return semantic_field_key(
        {
            "tag": str(getattr(node, "tag", "") or "input"),
            "type": _attr(node, "type"),
            "name": _attr(node, "name"),
            "id": _attr(node, "id"),
            "aria_label": _attr(node, "aria-label"),
            "placeholder": _attr(node, "placeholder"),
            "autocomplete": _attr(node, "autocomplete"),
            "label": label,
            "required": _required_state(node, label),
        }
    )


def _field_type(node: object) -> str:
    tag = str(getattr(node, "tag", "") or "input").lower()
    if tag == "select":
        return "select"
    if tag == "textarea":
        return "textarea"
    return _attr(node, "type").lower() or "text"


def _discover_fields(page: object, form: object | None) -> list[dict]:
    controls = (form.css("input, select, textarea") if form is not None else page.css("input, select, textarea"))[:120]
    fields: list[dict] = []
    for node in controls:
        if _is_hidden_or_disabled(node):
            continue
        if _attr(node, "type").lower() in {"submit", "button", "reset", "image"}:
            continue
        label = _label_for(page, node)
        key = _semantic_field_key(node, label)
        tag = str(getattr(node, "tag", "input") or "input")
        selector_candidates = _selector_candidates(tag, node)
        selector = selector_candidates[0]
        record = {
            "field_key": key,
            "field_type": _field_type(node),
            "selector": selector,
            "selector_candidates": selector_candidates[:8],
            "name": _attr(node, "name")[:120],
            "id": _attr(node, "id")[:120],
            "aria_label": _attr(node, "aria-label")[:200],
            "label": label[:200],
            "placeholder": _attr(node, "placeholder")[:200],
            "autocomplete": _attr(node, "autocomplete")[:80],
            "required": _required_state(node, label),
            "visible": True,
            "enabled": True,
            "confidence": _confidence(0.72 + (0.18 if _attr(node, "autocomplete") else 0.0) + (0.06 if label else 0.0)),
            "source": "scrapling",
        }
        if str(getattr(node, "tag", "")).lower() == "select":
            options = [option for option in node.css("option")[:20] if _text(option)]
            record["options"] = [_text(option)[:120] for option in options]
            record["option_values"] = [_attr(option, "value")[:120] for option in options]
        fields.append(record)
        if len(fields) >= _MAX_FORM_FIELDS:
            break
    return fields


def _form_selector(form: object) -> str:
    form_id = _attr(form, "id")
    if form_id and _SAFE_SELECTOR.match(form_id):
        return f"#{form_id}"
    classes = [item for item in _attr(form, "class").split() if _SAFE_SELECTOR.match(item)]
    if classes:
        return "form." + ".".join(classes[:2])
    return "form"


def _confidence(score: float) -> float:
    return round(max(0.0, min(0.99, score)), 2)


def _candidate_links(page: object, base_url: str, host: str) -> list[tuple[str, str, float]]:
    links: list[tuple[str, str, float]] = []
    for anchor in page.css("a[href]")[:60]:
        href = _attr(anchor, "href")
        if not href or href.startswith(("mailto:", "javascript:", "tel:")):
            continue
        candidate = urljoin(base_url, href)
        try:
            candidate = _validate_public_url(candidate, host)
        except NewsletterDiscoveryError:
            continue
        text = _text(anchor).lower()
        path = urlsplit(candidate).path.lower()
        score = 0.52
        if any(word in text for word in _LINK_HINTS):
            score += 0.22
        if any(word in path for word in _PATH_HINTS):
            score += 0.16
        if score >= 0.62:
            links.append((candidate, "navigation_link", _confidence(score)))
    return links


def _inspect_page(fetched: _FetchedPage, source: str, link_confidence: float) -> dict:
    return _inspect_page_structural(fetched, source, link_confidence)


def _inspect_page_structural(fetched: _FetchedPage, source: str, link_confidence: float) -> dict:
    page = fetched.response
    body = getattr(page, "body", b"") or b""
    if len(body) > MAX_BODY_BYTES:
        raise NewsletterDiscoveryError("The public page is larger than the safe discovery limit")
    body_nodes = page.css("body")
    body_text = " ".join((_text(body_nodes[0]) if body_nodes else "").split())[:5000].lower()
    forms = page.css("form")[:20]
    email_nodes = page.css('input[type="email"], input[autocomplete="email"], input[name*="mail"], input[id*="mail"], input[placeholder*="mail"], input[aria-label*="mail"]')[:40]
    best_email = None
    best_email_score = 0.0
    best_form = None
    for node in email_nodes:
        if _is_hidden_or_disabled(node):
            continue
        attrs = " ".join(_attr(node, key).lower() for key in ("type", "autocomplete", "name", "id", "placeholder", "aria-label"))
        score = 0.44
        if _attr(node, "type").lower() == "email":
            score += 0.22
        if _attr(node, "autocomplete").lower() == "email":
            score += 0.12
        if any(hint in attrs for hint in _EMAIL_HINTS):
            score += 0.10
        ancestors = node.xpath("ancestor::form[1]")
        form = ancestors[0] if ancestors else None
        if form is not None:
            score += 0.08
        if score > best_email_score:
            best_email, best_email_score, best_form = node, score, form

    submit_controls: list[dict[str, object]] = []
    submit = None
    submit_score = 0.0
    submit_nodes = page.css("button, input[type=submit], input[type=button], [role=button]")[:80]
    for node in submit_nodes:
        if _is_hidden_or_disabled(node):
            continue
        label = " ".join((_text(node), _attr(node, "value"), _attr(node, "aria-label"), _attr(node, "title"))).strip()
        lowered_label = label.lower()
        node_type = _attr(node, "type").lower()
        role = _attr(node, "role").lower()
        in_best_form = bool(best_form is not None and node.xpath("ancestor::form[1]") and node.xpath("ancestor::form[1]")[0] is best_form)
        score = 0.28
        if node_type in {"submit", "button"} or role == "button":
            score += 0.16
        if any(hint in lowered_label for hint in _SUBMIT_HINTS):
            score += 0.26
        if in_best_form:
            score += 0.12
        tag = str(getattr(node, "tag", "button") or "button").lower()
        control = {
            "selector": _selector_for(tag, node),
            "selector_candidates": _selector_candidates(tag, node)[:8],
            "label": label[:200],
            "field_type": node_type or tag,
            "visible": True,
            "enabled": True,
            "confidence": _confidence(score),
        }
        submit_controls.append(control)
        if score > submit_score:
            submit, submit_score = node, score
    submit_controls = submit_controls[:20]

    consent_controls: list[dict[str, object]] = []
    consent = None
    consent_score = 0.0
    consent_nodes = page.css('input[type="checkbox"]')[:40]
    for node in consent_nodes:
        if _is_hidden_or_disabled(node):
            continue
        label = " ".join((_label_for(page, node), _attr(node, "name"), _attr(node, "id"), _attr(node, "aria-label"))).strip()
        lowered_label = label.lower()
        required = _required_state(node, label) is True
        explicit = any(hint in lowered_label for hint in _CONSENT_HINTS)
        control = {
            "selector": _selector_for("input", node),
            "label": label[:200],
            "required": required,
            "visible": True,
            "enabled": True,
            "confidence": _confidence(0.3 + (0.34 if explicit else 0.0) + (0.18 if required else 0.0)),
        }
        consent_controls.append(control)
        if required and explicit:
            score = 0.5 + (0.2 if required else 0.0)
            if score > consent_score:
                consent, consent_score = node, score

    signals_success = [hint for hint in _SUCCESS_HINTS if hint in body_text][:MAX_SIGNALS]
    signals_failure = [hint for hint in _FAILURE_HINTS if hint in body_text][:MAX_SIGNALS]
    email_selector = _selector_for("input", best_email) if best_email is not None else None
    submit_tag = str(getattr(submit, "tag", "button") or "button").lower() if submit is not None else "button"
    submit_selector = _selector_for(submit_tag, submit) if submit is not None else None
    consent_selector = _selector_for("input", consent) if consent is not None else None
    fields = _discover_fields(page, best_form)
    challenge_indicators = _challenge_indicators(page, fetched.response)
    form_action = ""
    form_method = ""
    if best_form is not None:
        candidate_action = urljoin(fetched.url, _attr(best_form, "action") or fetched.url)
        try:
            if _same_origin(candidate_action, urlsplit(fetched.url).hostname or ""):
                form_action = candidate_action
        except Exception:
            form_action = ""
        form_method = (_attr(best_form, "method") or "GET").upper()
    evidence: list[str] = []
    if best_email is not None:
        evidence.append("email field detected")
    if submit is not None:
        evidence.append("submit control detected")
    if consent is not None:
        evidence.append("required consent checkbox detected")
    for field_key in sorted({item["field_key"] for item in fields if item["field_key"] != "email"})[:3]:
        evidence.append(f"{field_key.replace('_', ' ')} field detected")
    if signals_success:
        evidence.append("success language detected")
    if challenge_indicators:
        evidence.append("anti-bot challenge indicator detected")
    confidence = 0.24 + min(best_email_score, 0.9) * 0.42 + min(submit_score, 0.9) * 0.24
    confidence += 0.08 if best_form is not None else 0
    confidence += min(link_confidence, 0.2)
    if best_email is None:
        confidence *= 0.55
    config = {
        "signup_url": fetched.url,
        "form_selector": _form_selector(best_form) if best_form is not None else "",
        "form_action": form_action,
        "form_method": form_method,
        "email_selector": email_selector or "",
        "submit_selector": submit_selector or "",
        "consent_selector": consent_selector or "",
        "success_signals": "\n".join(signals_success),
        "failure_signals": "\n".join(signals_failure),
        "challenge_indicators": challenge_indicators,
        "confirmation_required": "true" if not signals_success else "false",
        "fields": fields,
    }
    unsupported_reason = ""
    return {
        "url": fetched.url,
        "source": source,
        "confidence": _confidence(confidence),
        "page_reachable": 200 <= fetched.status < 400,
        "http_status": fetched.status,
        "fetch_mode": fetched.fetch_mode,
        "form_action": form_action,
        "form_method": form_method,
        "child_frame_present": bool(page.css("iframe, frame")),
        "challenge_indicators": challenge_indicators,
        "email_selector": email_selector,
        "submit_selector": submit_selector,
        "submit_controls": submit_controls,
        "consent_selector": consent_selector,
        "consent_controls": consent_controls[:20],
        "fields": fields,
        "success_signals": signals_success,
        "failure_signals": signals_failure,
        "unsupported_reason": unsupported_reason,
        "confirmation_domain_hints": [urlsplit(fetched.url).hostname or ""],
        "evidence": evidence[:MAX_EVIDENCE],
        "configuration": config,
    }


def _challenge_indicators(page: object, response: object) -> list[str]:
    indicators: list[str] = []
    status = int(getattr(response, "status", 0) or 0)
    if status == 403:
        indicators.append("HTTP_403")
    elif status == 429:
        indicators.append("HTTP_429")
    headers = getattr(response, "headers", {}) or {}
    if hasattr(headers, "items"):
        for key in headers:
            lowered = str(key).lower()
            if lowered.startswith(_MITIGATION_HEADER_MARKERS):
                indicators.append(f"header:{lowered[:40]}")
    try:
        title = " ".join(_text(node) for node in page.css("title")[:2]).lower()
        body = " ".join(_text(node) for node in page.css("body")[:1]).lower()
        sample = f"{title} {body}"[:MAX_BODY_BYTES]
    except Exception:
        sample = ""
    for marker in _DISCOVERY_BLOCK_MARKERS:
        if marker in sample:
            indicators.append(f"text:{marker}")
    try:
        if page.css('iframe[src*="turnstile"], iframe[src*="challenge"], iframe[src*="captcha"]'):
            indicators.append("challenge_iframe")
    except Exception:
        pass
    return list(dict.fromkeys(indicators))[:MAX_SIGNALS]


def _challenge_detected(response: object) -> bool:
    """Recognize common challenge/interstitial responses without solving them here."""
    status = int(getattr(response, "status", 0) or 0)
    if status in {403, 429}:
        return True
    headers = getattr(response, "headers", {}) or {}
    if hasattr(headers, "keys") and any(str(key).lower().startswith(_MITIGATION_HEADER_MARKERS) for key in headers.keys()):
        return True
    body = getattr(response, "body", b"") or b""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="ignore")
    sample = str(body)[:MAX_BODY_BYTES].lower()
    return any(marker in sample for marker in _DISCOVERY_BLOCK_MARKERS)


def _retry_after_seconds(response: object) -> float | None:
    headers = getattr(response, "headers", {}) or {}
    if not hasattr(headers, "items"):
        return None
    raw = next((value for key, value in headers.items() if str(key).lower() == "retry-after"), None)
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(str(raw).strip())
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if seconds < 0 or seconds > MAX_RETRY_AFTER_SECONDS:
        return None
    return seconds


class _StealthBudget:
    """Bounded allowance of browser-backed re-reads for one discovery run.

    Each stealth read spawns a real browser, so the allowance is spent per run
    rather than per page. ``take()`` both checks and consumes in one step so a
    caller cannot forget to decrement.
    """

    def __init__(self, remaining: int = 0) -> None:
        self.remaining = max(0, int(remaining))

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _stealth_fetch(url: str):
    """Re-read a challenge-protected public page with a real browser.

    Scrapling's ``StealthyFetcher`` is imported lazily and needs the browser
    binaries installed by ``scrapling install``. Every failure mode here is
    expected to be survivable: when the browser is unavailable or the page
    still challenges, discovery reports the challenge exactly as it did
    before rather than raising into the caller.
    """
    try:
        from scrapling.fetchers import StealthyFetcher
    except Exception:  # noqa: BLE001 - an optional engine must never break discovery
        return None
    try:
        return StealthyFetcher.fetch(
            url,
            headless=True,
            solve_cloudflare=True,
            timeout=STEALTH_FETCH_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - missing binaries, navigation errors, timeouts
        return None


def _fetch(url: str, expected_host: str, stealth_budget: _StealthBudget | None = None) -> _FetchedPage:
    """Fetch one public page, escalating to a real browser when challenged.

    The plain HTTP fetcher is always the first attempt. Only when it returns a
    recognizable challenge do we spend one unit of the run's stealth budget on
    a browser-backed re-read of the same public URL.
    """
    safe_url = _validate_public_url(url, expected_host)
    budget = stealth_budget if stealth_budget is not None else _StealthBudget(0)
    for attempt in range(MAX_DISCOVERY_RETRIES + 1):
        response = Fetcher.get(
            safe_url,
            timeout=FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": "Northstar-Newsletter-Discovery/1.0"},
        )
        status = int(getattr(response, "status", 0) or 0)
        final_url = _validate_public_url(str(getattr(response, "url", safe_url)), expected_host)
        retry_after = _retry_after_seconds(response) if status == 429 else None
        if status == 429 and retry_after is not None and attempt < MAX_DISCOVERY_RETRIES:
            time.sleep(retry_after)
            continue
        if _challenge_detected(response):
            if budget.take():
                stealth_page = _stealth_fetch(final_url)
                if stealth_page is not None:
                    # A challenge solve can redirect; the public-URL guards must
                    # hold for wherever the browser actually landed.
                    stealth_status = int(getattr(stealth_page, "status", 0) or 0)
                    stealth_url = _validate_public_url(str(getattr(stealth_page, "url", final_url)), expected_host)
                    if not _challenge_detected(stealth_page) and 200 <= stealth_status < 400:
                        return _FetchedPage(stealth_url, stealth_page, stealth_status, fetch_mode="stealth")
            return _FetchedPage(final_url, response, status, blocked_reason="BLOCKED_ANTIBOT")
        return _FetchedPage(final_url, response, status)
    raise NewsletterDiscoveryError("The public page could not be fetched within the bounded retry limit")


def discover_newsletter(website_url: str | None, signup_url: str | None = None, max_stealth_fetches: int | None = None) -> dict:
    """Discover and rank public signup candidates without executing signup."""
    seed = signup_url or website_url
    if not seed:
        raise NewsletterDiscoveryError("Add a website URL before starting discovery")
    parsed = urlsplit(seed)
    if not parsed.hostname:
        raise NewsletterDiscoveryError("The newsletter website URL is invalid")
    host = parsed.hostname.lower().rstrip(".")
    _validate_public_url(seed, host)
    started = _now()
    warnings: list[str] = []
    candidates: list[dict] = []
    stealth_budget = _StealthBudget(MAX_STEALTH_FETCHES if max_stealth_fetches is None else max_stealth_fetches)
    urls: list[tuple[str, str, float]] = [(_validate_public_url(seed, host), "configured_url", 0.72)]
    if website_url and signup_url and urlsplit(website_url).hostname:
        root = _validate_public_url(website_url, host)
        if root not in {item[0] for item in urls}:
            urls.append((root, "website_root", 0.58))
    root_url = _validate_public_url(website_url or seed, host)
    likely_paths = [
        (urljoin(root_url, f"/{path}"), "likely_path", 0.62 if path in {"newsletter", "subscribe"} else 0.54)
        for path in _PATH_HINTS
    ]
    inspected = 0
    stealth_used = 0
    blocked_reasons: list[str] = []
    cursor = 0
    while cursor < len(urls) and inspected < MAX_PAGES:
        url, source, link_confidence = urls[cursor]
        cursor += 1
        try:
            fetched = _fetch(url, host, stealth_budget)
        except Exception as exc:
            warnings.append(f"Could not inspect {urlsplit(url).path or '/'}")
            continue
        inspected += 1
        if fetched.blocked_reason:
            blocked_reasons.append(fetched.blocked_reason)
            warnings.append(f"Discovery blocked at {urlsplit(url).path or '/'}: {fetched.blocked_reason}")
            continue
        if fetched.fetch_mode == "stealth":
            stealth_used += 1
        if fetched.status < 200 or fetched.status >= 400:
            continue
        try:
            result = _inspect_page(fetched, source, link_confidence)
        except Exception:
            warnings.append(f"Could not parse {urlsplit(url).path or '/'}")
            continue
        if result.get("unsupported_reason"):
            warnings.append(f"Skipped {urlsplit(fetched.url).path or '/'}: {result['unsupported_reason']}")
        elif result["email_selector"] or result["submit_selector"]:
            candidates.append(result)
        if source in {"configured_url", "website_root"}:
            for linked_url, linked_source, linked_score in _candidate_links(fetched.response, fetched.url, host):
                if linked_url not in {item[0] for item in urls}:
                    urls.append((linked_url, linked_source, linked_score))
            if cursor >= 1 and not any(item[1] == "likely_path" for item in urls):
                for path_candidate in likely_paths:
                    if path_candidate[0] not in {item[0] for item in urls}:
                        urls.append(path_candidate)
    candidates.sort(key=lambda item: (-item["confidence"], item["url"]))
    candidates = candidates[:MAX_CANDIDATES]
    return {
        "newsletter_id": "",
        "started_at": started,
        "completed_at": _now(),
        "website_reachable": inspected > 0,
        "discovery_blocked": bool(blocked_reasons),
        "blocked_reasons": list(dict.fromkeys(blocked_reasons))[:3],
        "inspected_url_count": inspected,
        "stealth_fetch_count": stealth_used,
        "recommended_candidate_url": candidates[0]["url"] if candidates else None,
        "candidates": candidates,
        "warnings": warnings[:MAX_EVIDENCE],
    }
