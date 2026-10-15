"""One vocabulary for signup form fields, shared by discovery and the observer.

The field vocabulary used to live in two places: ``newsletter_discovery``
classified static HTML nodes while ``observer`` carried a smaller map of its
own for live controls. Two copies of the same idea drifted — the observer's map
never learned ``address_line_1``, ``address_line_2`` or ``date_of_birth`` — and
every drift here surfaces as a field that is silently left unfilled.

Everything in this module is pure: it takes a plain mapping of control
descriptors and returns a decision. That keeps it testable without a browser or
a parsed document, and lets the discovery adapter and the live observer path
call exactly the same classifier.

Descriptor keys, all optional, all strings unless noted::

    tag, type, name, id, aria_label, placeholder, autocomplete, label
    required: bool | None
"""

from __future__ import annotations

from typing import Any, Mapping
from unicodedata import normalize as unicode_normalize

from .newsletter_identity import GENERATED_FIELD_KEYS


AUTOCOMPLETE_FIELD_KEYS = {
    "email": "email",
    "given-name": "first_name",
    "family-name": "last_name",
    "name": "full_name",
    "tel": "phone",
    "street-address": "address_line_1",
    "address-line1": "address_line_1",
    "address-line2": "address_line_2",
    "address-level2": "city",
    "address-level1": "state",
    "postal-code": "postal_code",
    "country": "country",
    "organization": "company",
    "organization-title": "job_title",
}

FIELD_HINTS = {
    "email": (
        "email", "e-mail", "mail", "courriel", "correo electronico",
        "direccion de correo", "endereco de email", "adresse e-mail",
        "adresse mail", "elektronische mail",
    ),
    "first_name": (
        "first name", "firstname", "given name", "given_name", "forename",
        "prenom", "nombre", "nome", "vorname", "voornaam",
    ),
    "last_name": (
        "last name", "lastname", "family name", "family_name", "surname",
        "nom de famille", "apellido", "apellidos", "nachname", "cognome",
        "sobrenome", "achternaam",
    ),
    "full_name": (
        "full name", "fullname", "your name", "name", "nom complet",
        "nombre completo", "nombre y apellidos", "nome completo", "nome e cognome",
        "vollstandiger name", "volledige naam",
    ),
    "phone": ("phone", "telephone", "mobile", "tel", "telephone number", "telefono", "telefone", "telefon", "telephone portable"),
    "address_line_1": (
        "street address", "address line 1", "address1", "address", "street",
        "adresse", "direccion", "endereco", "straat", "strasse",
    ),
    "address_line_2": ("address line 2", "address2", "suite", "apt", "apartment", "complement", "apartamento"),
    "city": ("city", "town", "ville", "ciudad", "cidade", "stadt", "plaats"),
    "state": ("state", "province", "region", "county", "estado", "provincia", "bundesland"),
    "postal_code": ("postal code", "postcode", "zip code", "zipcode", "zip", "code postal", "codigo postal", "postleitzahl", "cap postal"),
    "country": ("country", "nation", "pays", "pais", "pais/region", "país", "land", "country/region"),
    "company": ("company", "organization", "organisation", "business", "entreprise", "empresa", "societe", "sociedad", "unternehmen"),
    "job_title": ("job title", "job_title", "title", "role", "position", "poste", "cargo", "puesto", "berufsbezeichnung"),
    "date_of_birth": ("date of birth", "birth date", "birthday", "dob", "date de naissance", "fecha de nacimiento", "data de nascimento", "geburtsdatum"),
    "consent": (
        "consent", "privacy", "permission", "agree", "accept", "understand",
        "autorisation", "autoriza", "consentement", "confidentialite", "privacidad",
        "acepto", "aceptar", "entiendo", "comprendo", "privacidade", "concordo",
        "entendo", "datenschutz", "einverstanden", "zustimmen", "verstanden",
        "j'accepte", "j accepte", "ich akzeptiere", "ik ga akkoord", "accetto",
    ),
    "terms": (
        "terms", "conditions", "terms of service", "rules",
        "conditions generales", "conditions d'utilisation", "conditions d utilisation",
        "termes", "termos", "termos de servico", "terminos", "terminos del servicio",
        "condiciones", "nutzungsbedingungen", "bedingungen", "regole",
    ),
    "marketing_preference": ("marketing", "promotional", "offers", "updates"),
}

# Keys the live fill may type a generated value into. ``custom_select`` has no
# generated value of its own — it is filled from a real option of the live
# element — but it belongs here because it is filled rather than reported.
LIVE_FILL_KEYS = frozenset(GENERATED_FIELD_KEYS) | {"custom_select"}

# Autocomplete tokens that name credentials or payment instruments. A control
# carrying one of these is never typed into, whatever its name or label says.
BLOCKED_AUTOCOMPLETE_TOKENS = frozenset({
    "current-password",
    "new-password",
    "one-time-code",
    "cc-number",
    "cc-csc",
    "cc-exp",
    "cc-name",
    "cc-type",
})

# Substrings that always name secret or verification material, whatever else
# the control says. Deliberately narrower than
# ``newsletter_identity.FORBIDDEN_KEY_PARTS`` in one direction: a bare "code" is
# not here, because "zip code" and "postal code" are ordinary address fields.
BLOCKED_TEXT_PARTS = (
    "password",
    "passcode",
    "passphrase",
    "recovery",
    "secret",
    "token",
    "cookie",
    "otp",
    "2fa",
    "two-factor",
    "two factor",
    "one-time",
    "one time",
    "authenticat",
    "captcha",
    "cvv",
    "cvc",
)

# Words that turn a bare "code" into a verification field. "zip code" and
# "postal code" carry none of them, so an address survives this check.
_CODE_CONTEXT_PARTS = ("verif", "one-time", "one time", "otp", "security", "sms", "authentic", "2fa", "two-factor")

# The one verification word that is not automatically a block. A form that asks
# the visitor to verify or confirm the address they just typed wants the
# account's own mailbox, not a generated value, and is filled accordingly.
_VERIFY_WORDS = ("verif",)

# Words that mean "type the email again". These resolve to the account's own
# mailbox, so they are filled rather than reported.
_EMAIL_REPEAT_PARTS = ("confirm", "retype", "re-enter", "reenter", "repeat", "again", "verify")

# Kept separate from FIELD_HINTS because the live observer must recognize a
# freshly-rendered agreement control even when discovery's selector is stale.
_AGREEMENT_PHRASES = (
    "i agree", "i accept", "i understand", "yes i agree", "yes, i agree", "yes i understand",
    "j accepte", "j'accepte", "j ai compris", "oui j accepte",
    "acepto", "acepto los", "acepto las", "entiendo", "si acepto", "sí acepto",
    "concordo", "concordo com", "entendo", "sim concordo", "ich stimme", "ich stimme zu",
    "ich akzeptiere", "ich verstehe", "ik ga akkoord", "acconsento", "accetto",
)
_NEWSLETTER_EMAIL_CONSENT_PHRASES = (
    "i consent to receive newsletters via email",
    "i agree to receive newsletters via email",
    "i consent to receive email newsletters",
    "i agree to receive email newsletters",
)
_LEGAL_AGREEMENT_TERMS = (
    "terms", "conditions", "privacy", "consent", "permission", "policy", "rules",
    "terms of service", "terms of use", "conditions generales", "conditions d utilisation",
    "confidentialite", "privacidad", "condiciones", "terminos", "termos", "privacidade",
    "nutzungsbedingungen", "datenschutz", "bedingungen", "regole",
)
_MARKETING_TERMS = (
    "marketing", "newsletter", "mailing list", "email updates", "promotional",
    "promotions", "offers", "special offers", "news and updates", "commercial",
    "publicite", "publicidad", "promocionais", "werbung",
)


def _agreement_text(descriptors: Mapping[str, Any]) -> str:
    """Normalize checkbox metadata so matching is stable across accents/case."""
    raw = " ".join(
        str(descriptors.get(key) or "").strip()
        for key in ("name", "id", "aria_label", "title", "placeholder", "label")
    )
    raw = raw.replace(",", " ").replace(";", " ").replace(":", " ")
    return " ".join(
        unicode_normalize("NFKD", raw)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .split()
    )


def agreement_checkbox_key(descriptors: Mapping[str, Any]) -> str | None:
    """Return the safe agreement action for a live checkbox, if any.

    A clear legal agreement is actionable even when a site omitted the HTML
    ``required`` attribute. Marketing/newsletter opt-ins are excluded unless
    the same label also contains an explicit legal-policy reference. A bare
    ``yes`` is deliberately not enough because it may answer another question.
    """
    if _input_type(descriptors) != "checkbox":
        return None
    text = _agreement_text(descriptors)
    required = descriptors.get("required") is True
    legal = any(term in text for term in _LEGAL_AGREEMENT_TERMS)
    affirmative = any(phrase in text for phrase in _AGREEMENT_PHRASES)
    explicit_newsletter_email_consent = any(phrase in text for phrase in _NEWSLETTER_EMAIL_CONSENT_PHRASES)
    marketing = any(term in text for term in _MARKETING_TERMS)
    # An explicit consent to receive this newsletter by email is within the
    # operator's requested signup action. Do not generalize that permission to
    # broad marketing, SMS, phone, or third-party sharing choices.
    if marketing and not legal and not explicit_newsletter_email_consent:
        return None
    if not (affirmative or explicit_newsletter_email_consent) or (not required and not legal and not explicit_newsletter_email_consent):
        return None
    if explicit_newsletter_email_consent:
        return "consent"
    terms = ("terms", "conditions", "rules", "policy", "terms of service", "conditions generales", "terminos", "termos", "nutzungsbedingungen", "bedingungen")
    return "terms" if any(term in text for term in terms) else "consent"

# Options a select may offer that explicitly decline to answer. Choosing one of
# these is the honest answer for a generated identity, so it beats the first
# real option in the list.
NEVER_GENERATED_OPTION_LABELS = (
    "prefer not to say",
    "prefer not to answer",
    "prefer not to disclose",
    "rather not say",
    "decline to state",
    "no preference",
    "not specified",
    "unspecified",
    "none",
    "other",
    "n/a",
)


def _text(descriptors: Mapping[str, Any]) -> str:
    """The searchable text of a control: every identifying attribute at once."""
    raw = " ".join(
        str(descriptors.get(key) or "").strip().lower()
        for key in ("name", "id", "aria_label", "title", "placeholder", "label")
    ).strip()
    normalized = " ".join(
        unicode_normalize("NFKD", raw)
        .encode("ascii", "ignore")
        .decode("ascii")
        .split()
    )
    # Keep the original separators for security-word checks (for example
    # ``one-time-code``), while also making labels such as ``first-name``
    # match the human-readable ``first name`` vocabulary.
    expanded = normalized.replace("_", " ").replace("-", " ")
    return f"{normalized} {expanded}".strip()


def _tag(descriptors: Mapping[str, Any]) -> str:
    return str(descriptors.get("tag") or "input").strip().lower()


def _input_type(descriptors: Mapping[str, Any]) -> str:
    return str(descriptors.get("type") or "").strip().lower()


def _autocomplete(descriptors: Mapping[str, Any]) -> str:
    parts = str(descriptors.get("autocomplete") or "").strip().lower().split()
    return parts[0] if parts else ""


def blocked_live_control_reason(descriptors: Mapping[str, Any]) -> str | None:
    """Why this live control must never be typed into, or ``None`` when it may.

    Checked per control rather than per field key: a control's own attributes
    are the evidence, so a password box is refused even when its name is
    innocuous and a field named ``company`` is refused when it is really a
    one-time-code input.
    """
    if _tag(descriptors) == "input" and _input_type(descriptors) == "password":
        return "password field"
    autocomplete = _autocomplete(descriptors)
    if autocomplete in BLOCKED_AUTOCOMPLETE_TOKENS:
        return f"autocomplete={autocomplete}"
    text = _text(descriptors)
    for part in BLOCKED_TEXT_PARTS:
        if part in text:
            return f"mentions {part}"
    if "code" in text and any(part in text for part in _CODE_CONTEXT_PARTS):
        return "verification code field"
    # Checked after the code rule, so "verify the code sent to your email" is
    # blocked while "confirm your email address" is filled with the mailbox.
    if any(hint in text for hint in FIELD_HINTS["email"]) and any(part in text for part in _EMAIL_REPEAT_PARTS):
        return None
    for part in _VERIFY_WORDS:
        if part in text:
            return f"mentions {part}"
    return None


def _choice_field_key(descriptors: Mapping[str, Any], input_type: str, text: str) -> str:
    """Classify a checkbox or radio, which never holds a typed value."""
    if input_type == "radio":
        # A radio group is a set of alternatives. The generator has no basis
        # for picking one, so a required group is reported by name.
        return "custom_radio"
    required = descriptors.get("required") is True
    if required and any(hint in text for hint in FIELD_HINTS["terms"]):
        return "terms"
    if required and any(hint in text for hint in FIELD_HINTS["consent"]):
        return "consent"
    if any(hint in text for hint in FIELD_HINTS["marketing_preference"]):
        return "marketing_preference"
    return "custom_checkbox"


def semantic_field_key(descriptors: Mapping[str, Any]) -> str:
    """Classify one form control into the shared field vocabulary.

    The order is deliberate: a checkbox or radio is settled first because a
    control that cannot hold a typed value must never be named as a text field,
    then an explicit ``autocomplete`` token — the only unambiguous signal a form
    gives — then the input type, then the hint table. Anything still unknown
    keeps a ``custom_`` sentinel so the caller can name it in a failure rather
    than guess at it.
    """
    input_type = _input_type(descriptors)
    text = _text(descriptors)

    if input_type in {"checkbox", "radio"}:
        # ``input[type=checkbox][name=mailing_list_optin]`` carries the
        # substring "mail", so classifying it by text would name it the email
        # field — the one control the live fill must never type into.
        return _choice_field_key(descriptors, input_type, text)

    # A publisher password is never inferred from a label that might name an
    # OTP. Only the browser's actual password input can use the separately
    # approved, per-account/per-newsletter secret.
    if input_type == "password":
        return "signup_password"

    autocomplete = _autocomplete(descriptors)
    if autocomplete in AUTOCOMPLETE_FIELD_KEYS:
        return AUTOCOMPLETE_FIELD_KEYS[autocomplete]

    tag = _tag(descriptors)

    # A repeated email field needs the account's own mailbox, not a generated
    # value, so it resolves to ``email`` before anything can block or invent it.
    if any(hint in text for hint in FIELD_HINTS["email"]) and any(part in text for part in _EMAIL_REPEAT_PARTS):
        return "email"

    if input_type == "email" or any(hint in text for hint in FIELD_HINTS["email"]):
        return "email"
    if input_type == "tel" or any(hint in text for hint in FIELD_HINTS["phone"]):
        return "phone"
    if input_type == "date" or any(hint in text for hint in FIELD_HINTS["date_of_birth"]):
        return "date_of_birth"
    for key in (
        "first_name",
        "last_name",
        "address_line_1",
        "address_line_2",
        "postal_code",
        "country",
        "city",
        "state",
        "company",
        "job_title",
        "full_name",
    ):
        if any(hint in text for hint in FIELD_HINTS[key]):
            return key

    if tag == "select":
        if any(hint in text for hint in FIELD_HINTS["country"]):
            return "country"
        return "custom_select"
    if tag == "textarea":
        return "custom_text"
    return "custom_text"


def attribute_field_hints(field_key: str) -> tuple[str, ...]:
    """Attribute-shaped substrings for one field key, for locator fallbacks.

    ``FIELD_HINTS`` holds human phrases ("first name") because it is matched
    against visible text and labels. A locator fallback matches the same field
    against its ``name``/``id``/``placeholder``/``aria-label`` attribute with a
    CSS substring selector, where the very same field is spelled ``first-name``
    or ``first_name``. Both spellings are produced here so one vocabulary
    serves the classifier and the locator builder, and neither can drift from
    the other.
    """
    hints: list[str] = []
    for hint in FIELD_HINTS.get(str(field_key or "").strip().lower(), ()):
        hints.append(hint)
        for separator in ("-", "_"):
            joined = separator.join(hint.split())
            if joined != hint:
                hints.append(joined)
    return tuple(dict.fromkeys(hints))


def preferred_option_index(labels: list[str]) -> int | None:
    """Index of the option a generated identity should choose, or ``None``.

    An explicit opt-out beats the first real option: "Prefer not to say" is a
    truthful answer for a placeholder identity, while "Female" or "Enterprise"
    would be a fabricated attribute about a person. Placeholder options (empty
    text, or the usual "select one") are never chosen.
    """
    for index, label in enumerate(labels):
        lowered = str(label or "").strip().lower()
        if not lowered or lowered.startswith(("select ", "choose ", "--", "—")):
            continue
        if any(term == lowered for term in NEVER_GENERATED_OPTION_LABELS):
            return index
    for index, label in enumerate(labels):
        lowered = str(label or "").strip().lower()
        if not lowered or lowered.startswith(("select ", "choose ", "--", "—")):
            continue
        return index
    return None
