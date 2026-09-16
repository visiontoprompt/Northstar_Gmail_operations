from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class PageSignal(StrEnum):
    LOGIN_FORM = "LOGIN_FORM"
    GMAIL_HOME = "GMAIL_HOME"
    GOOGLE_ACCOUNT_HOME = "GOOGLE_ACCOUNT_HOME"
    ORDINARY_INTERSTITIAL = "ORDINARY_INTERSTITIAL"
    CAPTCHA = "CAPTCHA"
    MFA_OR_SECURITY_CHECK = "MFA_OR_SECURITY_CHECK"
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    CONTACTS_HOME = "CONTACTS_HOME"
    CONTACTS_IMPORT_DIALOG = "CONTACTS_IMPORT_DIALOG"
    CONTACTS_IMPORT_DONE = "CONTACTS_IMPORT_DONE"
    GMAIL_SETTINGS = "GMAIL_SETTINGS"
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
    if any(marker in haystack for marker in (
        # English Google sign-in copy.
        "couldn't find your google account",
        "couldn't find your account",
        "no google account found",
        "no account found with that email",
        "no account found",
        "find your google account",
        "did you mean",
        "that email address doesn't",
        "this email address doesn't",
        "isn't associated with a google account",
        "no account exists",
        # French Google sign-in copy, including the exact text shown in the
        # reported production failure: "Impossible de trouver ce compte".
        "impossible de trouver ce compte",
        "impossible de trouver votre compte",
        "impossible de trouver votre compte google",
        "aucun compte google trouvé",
        "aucun compte trouvé",
        "aucun compte google n'a été trouvé",
        "cette adresse e-mail n'est associée à aucun compte google",
        "cette adresse e-mail n’est associée à aucun compte google",
        "n'est associée à aucun compte google",
        "n’est associée à aucun compte google",
        "ce compte google n'existe pas",
        "ce compte google n’existe pas",
    )):
        return PageSignal.ACCOUNT_NOT_FOUND, PageAction.NEEDS_ATTENTION
    if host in {"mail.google.com", "inbox.google.com"}:
        fragment = parsed.fragment.lower()
        if "settings/" in fragment or "/settings/" in parsed.path.lower():
            return PageSignal.GMAIL_SETTINGS, PageAction.SAFE_TO_OBSERVE
        mailbox_markers = ("inbox", "compose", "sent mail", "drafts", "primary")
        if parsed.path.startswith("/mail/") and any(marker in haystack for marker in mailbox_markers):
            return PageSignal.GMAIL_HOME, PageAction.SAFE_TO_OBSERVE
        return PageSignal.UNKNOWN, PageAction.NEEDS_ATTENTION
    if host in {"accounts.google.com", "consent.google.com", "myaccount.google.com"}:
        # Google sometimes renders the TOTP challenge with little or no
        # accessible text while the URL still identifies the challenge.
        # Recognize the route before generic interstitial markers (such as
        # "privacy") can misclassify it and close an otherwise valid login.
        if host == "accounts.google.com" and any(marker in parsed.path.lower() for marker in ("/challenge/totp", "/challenge/otp")):
            return PageSignal.MFA_OR_SECURITY_CHECK, PageAction.WAIT_FOR_HUMAN
        if any(marker in haystack for marker in ("sign in", "email or phone", "enter your password")):
            return PageSignal.LOGIN_FORM, PageAction.SAFE_TO_OBSERVE
        if host == "myaccount.google.com" and parsed.path.lower().rstrip("/") == "/account-enhanced-safe-browsing":
            return PageSignal.GOOGLE_ACCOUNT_HOME, PageAction.SAFE_TO_OBSERVE
        if host == "myaccount.google.com" and any(marker in haystack for marker in (
            "google account",
            "compte google",
            "cuenta de google",
            "personal info",
            "información personal",
            "informacion personal",
            "security",
            "data & privacy",
            "people & sharing",
            "payments & subscriptions",
            "privacy & personalization",
            "inicio",
            "accueil",
            "informations personnelles",
            "sécurité",
            "données et confidentialité",
        )):
            # This is positive post-authentication evidence, distinct from an
            # inbox observation. Logged-out My Account pages are handled by
            # the sign-in markers above.
            return PageSignal.GOOGLE_ACCOUNT_HOME, PageAction.SAFE_TO_OBSERVE
        # After a successful OTP/chooser hand-off, Google can briefly render
        # the canonical account-home route with almost no accessible body
        # text. It is still a strong post-authentication signal: a logged-out
        # request to this route is redirected to accounts.google.com, while
        # the account-home route itself remains on myaccount.google.com. Keep
        # this limited to the root route and only after login/challenge terms
        # have been checked above.
        if host == "myaccount.google.com" and parsed.path.lower().rstrip("/") == "":
            return PageSignal.GOOGLE_ACCOUNT_HOME, PageAction.SAFE_TO_OBSERVE
        if any(marker in haystack for marker in ("continue", "welcome", "terms of service", "privacy", "review settings")):
            return PageSignal.ORDINARY_INTERSTITIAL, PageAction.SAFE_TO_OBSERVE
    if host == "contacts.google.com":
        if any(marker in haystack for marker in (
            "contacts imported",
            "successfully imported",
            "imported successfully",
            "import terminée",
            "importation terminée",
            "contacts importés",
            "contactos importados",
            "contacto importado",
            "importado correctamente",
            "importación completada",
            "importación finalizada",
            "se importaron",
            "le processus est terminé",
            "processus terminé",
            "a été importé",
            "importé avec succès",
            "the process is complete",
            "the process has finished",
            "process completed",
            "el proceso ha terminado",
            "el proceso se completó",
        )):
            return PageSignal.CONTACTS_IMPORT_DONE, PageAction.SAFE_TO_OBSERVE
        if any(marker in haystack for marker in (
            # English.
            "import contacts",
            "imported contacts",
            "choose a file",
            "select a file",
            "select contacts file",
            # French.
            "importer les contacts",
            "importer des contacts",
            "sélectionnez un fichier",
            "choisir un fichier",
            # Spanish.
            "importar contactos",
            "seleccionar un archivo",
            "seleccionar archivo",
            "elegir un archivo",
        )):
            return PageSignal.CONTACTS_IMPORT_DIALOG, PageAction.SAFE_TO_OBSERVE
        return PageSignal.CONTACTS_HOME, PageAction.SAFE_TO_OBSERVE
    return PageSignal.UNKNOWN, PageAction.NEEDS_ATTENTION
