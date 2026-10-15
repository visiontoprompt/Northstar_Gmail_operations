"""The shared field vocabulary: classification and the never-fill boundary."""

import pytest

from app.newsletter_fields import (
    LIVE_FILL_KEYS,
    agreement_checkbox_key,
    attribute_field_hints,
    blocked_live_control_reason,
    preferred_option_index,
    semantic_field_key,
)


def test_autocomplete_wins_over_a_conflicting_name() -> None:
    # The token a form declares is the only unambiguous signal it gives, so it
    # outranks the name even when the name says something else.
    assert semantic_field_key({"tag": "input", "type": "text", "name": "email", "autocomplete": "family-name"}) == "last_name"
    assert semantic_field_key({"tag": "input", "autocomplete": "address-line2"}) == "address_line_2"
    assert semantic_field_key({"tag": "input", "autocomplete": "street-address"}) == "address_line_1"


def test_input_type_classifies_without_any_name_or_label() -> None:
    assert semantic_field_key({"tag": "input", "type": "tel"}) == "phone"
    assert semantic_field_key({"tag": "input", "type": "date"}) == "date_of_birth"
    assert semantic_field_key({"tag": "input", "type": "email"}) == "email"


def test_label_and_placeholder_carry_the_field_key() -> None:
    assert semantic_field_key({"tag": "input", "label": "Date of birth"}) == "date_of_birth"
    assert semantic_field_key({"tag": "input", "placeholder": "Suite or apartment"}) == "address_line_2"
    assert semantic_field_key({"tag": "input", "name": "job-title"}) == "job_title"


def test_a_choice_control_is_never_classified_as_the_email_field() -> None:
    # ``mailing_list_optin`` contains the substring "mail". Classifying it as
    # the email field would point the one control that must never be touched at
    # a checkbox that usually precedes the real address input.
    assert semantic_field_key({"tag": "input", "type": "checkbox", "name": "mailing_list_optin"}) == "custom_checkbox"
    assert semantic_field_key({"tag": "input", "type": "checkbox", "name": "email_optin", "required": True}) == "custom_checkbox"
    assert semantic_field_key({"tag": "input", "type": "checkbox", "name": "email_updates"}) == "marketing_preference"


def test_a_required_consent_checkbox_keeps_its_own_key() -> None:
    assert semantic_field_key({"tag": "input", "type": "checkbox", "label": "I agree to the privacy policy", "required": True}) == "consent"
    assert semantic_field_key({"tag": "input", "type": "checkbox", "label": "I agree to the terms of service", "required": True}) == "terms"
    # An optional one is a preference, not a gate, so it is not a consent key.
    assert semantic_field_key({"tag": "input", "type": "checkbox", "label": "I agree to the privacy policy"}) == "custom_checkbox"
    assert semantic_field_key({"tag": "input", "type": "radio", "name": "format"}) == "custom_radio"


@pytest.mark.parametrize(
    "label, expected",
    [
        ("I agree to the terms of service", "terms"),
        ("J'accepte les conditions générales", "terms"),
        ("Acepto la política de privacidad", "consent"),
        ("Ich stimme den Nutzungsbedingungen zu", "terms"),
        ("Concordo com a política de privacidade", "consent"),
        ("I understand and agree to the rules", "terms"),
        ("Sí, acepto la política de privacidad", "consent"),
        ("Yes, I understand the rules", "terms"),
    ],
)
def test_live_agreement_checkbox_detection_supports_common_languages(label: str, expected: str) -> None:
    assert agreement_checkbox_key({"tag": "input", "type": "checkbox", "label": label}) == expected


def test_aria_checkbox_is_classified_as_an_agreement_control() -> None:
    assert agreement_checkbox_key({
        "tag": "div",
        "type": "checkbox",
        "role": "checkbox",
        "aria_label": "I agree to the terms of service",
    }) == "terms"


def test_live_agreement_detection_does_not_select_marketing_opt_in() -> None:
    assert agreement_checkbox_key({
        "tag": "input",
        "type": "checkbox",
        "label": "I agree to receive newsletters and marketing offers",
    }) is None


def test_bare_yes_checkbox_is_not_treated_as_legal_consent() -> None:
    assert agreement_checkbox_key({"tag": "input", "type": "checkbox", "label": "Yes"}) is None


def test_an_unrecognised_select_is_a_custom_select_and_a_textarea_is_free_text() -> None:
    assert semantic_field_key({"tag": "select", "name": "sector"}) == "custom_select"
    assert semantic_field_key({"tag": "select", "name": "country"}) == "country"
    assert semantic_field_key({"tag": "textarea", "name": "comments"}) == "custom_text"


@pytest.mark.parametrize(
    "descriptors",
    [
        {"tag": "input", "type": "password", "name": "user_email"},
        {"tag": "input", "autocomplete": "one-time-code"},
        {"tag": "input", "autocomplete": "cc-number"},
        {"tag": "input", "name": "verify_code"},
        {"tag": "input", "name": "verification_code_sent_to_your_email"},
        {"tag": "input", "id": "client_secret"},
        {"tag": "input", "name": "recovery_email"},
        {"tag": "input", "aria_label": "Card security code (CVC)"},
    ],
)
def test_credential_and_verification_controls_are_blocked(descriptors: dict) -> None:
    assert blocked_live_control_reason(descriptors) is not None


@pytest.mark.parametrize(
    "descriptors",
    [
        {"tag": "input", "name": "company"},
        {"tag": "input", "type": "email", "name": "confirm_email"},
        {"tag": "input", "name": "verify_email_address"},
        {"tag": "input", "name": "zip_code"},
        {"tag": "input", "name": "postal_code"},
        {"tag": "input", "name": "state_province"},
        {"tag": "input", "type": "checkbox", "label": "I agree to the privacy policy"},
        {"tag": "select", "name": "country"},
    ],
)
def test_ordinary_signup_controls_are_not_blocked(descriptors: dict) -> None:
    assert blocked_live_control_reason(descriptors) is None


def test_posting_code_is_not_a_verification_field() -> None:
    # "zip code" is an address, so the bare word "code" cannot be the test on
    # its own; only a verification word around it makes it one.
    assert blocked_live_control_reason({"tag": "input", "name": "postal_code"}) is None
    assert blocked_live_control_reason({"tag": "input", "name": "code"}) is None
    assert blocked_live_control_reason({"tag": "input", "name": "sms_code"}) is not None


def test_the_generated_identity_never_reaches_the_email_or_a_secret_key() -> None:
    assert "address_line_1" in LIVE_FILL_KEYS
    assert "address_line_2" in LIVE_FILL_KEYS
    assert "date_of_birth" in LIVE_FILL_KEYS
    assert "custom_select" in LIVE_FILL_KEYS
    # The mailbox comes from the account record alone, never from generation.
    assert "email" not in LIVE_FILL_KEYS
    for key in LIVE_FILL_KEYS:
        assert not any(part in key for part in ("password", "otp", "secret", "token", "cookie"))


def test_preferred_option_beats_the_first_real_option() -> None:
    assert preferred_option_index(["Select one", "Female", "Prefer not to say", "Other"]) == 2
    assert preferred_option_index(["", "Choose an option", "Enterprise"]) == 2
    assert preferred_option_index(["None", "Enterprise"]) == 0


def test_preferred_option_refuses_a_list_with_nothing_choosable() -> None:
    assert preferred_option_index([]) is None
    assert preferred_option_index([""]) is None
    assert preferred_option_index(["Select one", "Choose one", "--"]) is None


def test_attribute_hints_carry_the_hyphenated_and_underscored_spellings() -> None:
    # The locator fallback matches these against ``name``/``id``/``placeholder``
    # with a CSS substring selector, where the same field is spelled
    # ``first-name`` or ``first_name`` rather than ``first name``.
    hints = attribute_field_hints("first_name")
    assert "first-name" in hints and "first_name" in hints and "firstname" in hints
    assert "given-name" in attribute_field_hints("first_name")
    assert "street-address" in attribute_field_hints("address_line_1")
    assert attribute_field_hints("custom_text") == ()
    assert attribute_field_hints("") == ()
