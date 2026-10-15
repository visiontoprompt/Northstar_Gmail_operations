"""Deterministic synthetic signup details for newsletter forms.

An operator can already approve per-account signup values through
``newsletter_account_identities``; this module removes the need to type them by
hand for the optional fields a public signup form asks for.

Two properties matter and are enforced here:

* **Determinism.** Values are seeded from the organization and account id, so
  the same account always presents the same details. Re-running a signup does
  not make the account look like a different person, and the values survive a
  lost database row.
* **Bounded scope.** Generated values only ever fill optional, non-verification
  fields. The account's own mailbox address is never generated, and nothing
  matching the forbidden-key list (passwords, OTP seeds, recovery material,
  cookies, tokens) is ever produced.

The values are placeholders. They are valid enough for a public form to accept
(country/state names that a ``<select>`` will match) without pretending to
describe a real person.
"""

from __future__ import annotations

import hashlib
import random
import re
from typing import Any, Mapping

# Field keys that may be auto-filled. Keys absent from this set are never
# generated, whatever a discovered form claims to need. The vocabulary matches
# ``newsletter_fields.FIELD_HINTS`` so every key a form can be classified into
# either has a generated value or is reported by name.
GENERATED_FIELD_KEYS = frozenset({
    "first_name",
    "last_name",
    "full_name",
    "phone",
    "address_line_1",
    "address_line_2",
    "city",
    "state",
    "postal_code",
    "country",
    "company",
    "job_title",
    "date_of_birth",
})

# Substrings that mark a value as secret. Shared with the request schema so the
# API and the generator cannot drift apart.
FORBIDDEN_KEY_PARTS = ("password", "recovery", "2fa", "totp", "otp", "cookie", "token", "secret")

MAX_IDENTITY_KEY_LENGTH = 320
MAX_IDENTITY_VALUE_LENGTH = 500

_GIVEN_NAMES = (
    "Alex", "Sam", "Jordan", "Casey", "Riley", "Morgan", "Taylor", "Jamie",
    "Avery", "Quinn", "Robin", "Dana", "Lee", "Kai", "Noor", "Emery",
)

_JOB_TITLES = (
    "Researcher", "Analyst", "Editor", "Coordinator", "Associate", "Consultant",
)

# Street names a generated address may use. Every one of them is deliberately
# not a real street: the placeholder must stay a syntactically valid address
# that also cannot be delivered to somebody's home. City, region and postal code
# are real values because forms check them against selects and format rules.
_STREET_NAMES = ("Placeholder Way", "Placeholder Lane", "Placeholder Avenue", "Placeholder Drive")

# Coherent country/region/city/postal tuples. Forms commonly render country and
# state as selects, and the observer passes the value to ``select_option`` by
# value first and then by label, so a full country or region name is what a
# real ``<select>`` will match.
_REGIONS = (
    {"country": "United States", "state": "Oregon", "city": "Portland", "postal_code": "97205"},
    {"country": "United States", "state": "Colorado", "city": "Denver", "postal_code": "80202"},
    {"country": "United States", "state": "Massachusetts", "city": "Boston", "postal_code": "02108"},
    {"country": "Canada", "state": "Ontario", "city": "Ottawa", "postal_code": "K1P 1J1"},
    {"country": "United Kingdom", "state": "Greater London", "city": "London", "postal_code": "EC1A 1BB"},
    {"country": "Ireland", "state": "County Dublin", "city": "Dublin", "postal_code": "D02 AF30"},
    {"country": "Australia", "state": "Victoria", "city": "Melbourne", "postal_code": "3000"},
    {"country": "Netherlands", "state": "North Holland", "city": "Amsterdam", "postal_code": "1012 JS"},
)

_NAME_CLEAN_RE = re.compile(r"[^A-Za-z]+")


def seed_for(organization_id: str, account_id: str) -> int:
    """Return the stable integer seed for one organization/account pair."""
    digest = hashlib.sha256(f"{organization_id}:{account_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _surname_from_mailbox(account_email: str | None) -> str | None:
    """Derive a placeholder surname from the mailbox so values read coherently."""
    local = str(account_email or "").split("@", 1)[0]
    letters = _NAME_CLEAN_RE.sub("", local)
    if len(letters) < 3:
        return None
    return letters[:14].capitalize()


def is_forbidden_identity_key(key: str) -> bool:
    """Return true when a field key names secret or verification material."""
    lowered = str(key or "").strip().lower()
    return any(part in lowered for part in FORBIDDEN_KEY_PARTS)


def clean_identity_values(values: Mapping[str, Any], message: str = "Only non-secret signup fields may be used") -> dict[str, str]:
    """Validate an identity mapping, rejecting secrets and unbounded values.

    Shared by the API request schemas and the generator so every path enforces
    exactly the same rule set and the two cannot drift apart.
    """
    cleaned: dict[str, str] = {}
    for key, item in values.items():
        key = str(key).strip()
        item = str(item).strip()
        if not key or len(key) > MAX_IDENTITY_KEY_LENGTH or is_forbidden_identity_key(key):
            raise ValueError(message)
        if len(item) > MAX_IDENTITY_VALUE_LENGTH:
            raise ValueError("Signup field values are too long")
        if item:
            cleaned[key] = item
    return cleaned


def build_newsletter_identity(
    organization_id: str,
    account_id: str,
    *,
    org_name: str | None = None,
    account_email: str | None = None,
) -> dict[str, str]:
    """Build the stable placeholder profile for one account.

    Only keys in ``GENERATED_FIELD_KEYS`` are ever returned, so a caller can
    merge the result into a signup config without any risk of supplying a
    verification field.
    """
    rng = random.Random(seed_for(organization_id, account_id))
    first_name = rng.choice(_GIVEN_NAMES)
    last_name = _surname_from_mailbox(account_email) or rng.choice(_GIVEN_NAMES)
    region = rng.choice(_REGIONS)
    identity: dict[str, str] = {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}",
        "company": str(org_name or "").strip() or "Media Research",
        "job_title": rng.choice(_JOB_TITLES),
        "address_line_1": f"{rng.randrange(100, 9999)} {rng.choice(_STREET_NAMES)}",
        "address_line_2": f"Suite {rng.randrange(1, 400)}",
        "city": region["city"],
        "state": region["state"],
        "postal_code": region["postal_code"],
        # Neutral, non-dialable placeholder in the reserved 555 range.
        "phone": f"555-01{rng.randrange(0, 100):02d}",
        # A form that gates on age reads an adult year; the day is capped at 28
        # so every month is valid.
        "date_of_birth": f"{rng.randrange(1982, 1996):04d}-{rng.randrange(1, 13):02d}-{rng.randrange(1, 29):02d}",
    }
    # ``country`` last so a caller can see that every key produced here is
    # drawable from the declared field-key set.
    identity["country"] = region["country"]
    return {key: value for key, value in identity.items() if key in GENERATED_FIELD_KEYS}


def merge_newsletter_identity(
    generated: Mapping[str, str] | None,
    saved: Mapping[str, str] | None,
    requested: Mapping[str, str] | None,
) -> dict[str, str]:
    """Merge identity layers by precedence: requested > saved > generated.

    An operator-approved value always wins over a generated placeholder, so
    enabling generation can never silently overwrite something a human set.
    Values outside ``GENERATED_FIELD_KEYS`` are passed through untouched when
    they came from ``saved`` or ``requested``.
    """
    merged: dict[str, str] = dict(generated or {})
    for layer in (saved, requested):
        for key, value in (layer or {}).items():
            if value not in (None, ""):
                merged[str(key)] = str(value)
    return merged
