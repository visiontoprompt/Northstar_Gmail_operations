from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class PageSignal(StrEnum):
    LOGIN_FORM = "LOGIN_FORM"
    GMAIL_HOME = "GMAIL_HOME"
    ORDINARY_INTERSTITIAL = "ORDINARY_INTERSTITIAL"
    CAPTCHA = "CAPTCHA"
    MFA_OR_SECURITY_CHECK = "MFA_OR_SECURITY_CHECK"
    UNKNOWN = "UNKNOWN"


class PageAction(StrEnum):
    SAFE_TO_OBSERVE = "SAFE_TO_OBSERVE"
    WAIT_FOR_HUMAN = "WAIT_FOR_HUMAN"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


@dataclass(frozen=True)
class PageObservation:
    url: str
    title: str = ""
    text: str = ""


def classify_page(observation: PageObservation) -> tuple[PageSignal, PageAction]:
    parsed = urlparse(observation.url)
    host = parsed.hostname or ""
    haystack = f"{observation.url} {observation.title} {observation.text}".lower()
    if any(marker in haystack for marker in ("captcha", "recaptcha", "i'm not a robot", "unusual traffic")):
        return PageSignal.CAPTCHA, PageAction.WAIT_FOR_HUMAN
    if any(marker in haystack for marker in ("2-step verification", "two-step verification", "security key", "verify it's you", "phone verification", "security check", "enter the code", "enter your code", "google authenticator", "one-time code")):
        return PageSignal.MFA_OR_SECURITY_CHECK, PageAction.WAIT_FOR_HUMAN
    if host in {"mail.google.com", "inbox.google.com"}:
        mailbox_markers = ("inbox", "compose", "sent mail", "drafts", "primary")
        if parsed.path.startswith("/mail/") and any(marker in haystack for marker in mailbox_markers):
            return PageSignal.GMAIL_HOME, PageAction.SAFE_TO_OBSERVE
        return PageSignal.UNKNOWN, PageAction.NEEDS_ATTENTION
    if host in {"accounts.google.com", "consent.google.com", "myaccount.google.com"}:
        if any(marker in haystack for marker in ("sign in", "email or phone", "enter your password")):
            return PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE
        if any(marker in haystack for marker in ("continue", "welcome", "terms of service", "privacy", "review settings")):
            return PageSignal.ORDINARY_INTERSTITIAL, PageAction.SAFE_TO_OBSERVE
    return PageSignal.UNKNOWN, PageAction.NEEDS_ATTENTION
