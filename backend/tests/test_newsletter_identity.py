import datetime

import pytest

from app.newsletter_identity import (
    _REGIONS,
    GENERATED_FIELD_KEYS,
    build_newsletter_identity,
    clean_identity_values,
    is_forbidden_identity_key,
    merge_newsletter_identity,
    seed_for,
)


def test_seed_is_stable_per_organization_and_account() -> None:
    assert seed_for("org_demo", "acct_1") == seed_for("org_demo", "acct_1")
    assert seed_for("org_demo", "acct_1") != seed_for("org_demo", "acct_2")
    assert seed_for("org_demo", "acct_1") != seed_for("org_other", "acct_1")


def test_identity_is_identical_across_calls_for_one_account() -> None:
    first = build_newsletter_identity("org_demo", "acct_1", account_email="walker.ob@gmail.com")
    second = build_newsletter_identity("org_demo", "acct_1", account_email="walker.ob@gmail.com")
    assert first == second
    assert first["full_name"] == f"{first['first_name']} {first['last_name']}"


def test_identity_varies_across_accounts() -> None:
    identities = {
        tuple(sorted(build_newsletter_identity("org_demo", f"acct_{index}").items()))
        for index in range(12)
    }
    assert len(identities) > 1


def test_identity_only_produces_declared_non_verification_keys() -> None:
    identity = build_newsletter_identity("org_demo", "acct_1", account_email="walker.ob@gmail.com")
    assert set(identity) <= GENERATED_FIELD_KEYS
    # The mailbox address is owned by the account and is never generated.
    assert "email" not in identity
    for key in identity:
        assert not is_forbidden_identity_key(key)


def test_identity_uses_the_mailbox_for_a_coherent_surname() -> None:
    identity = build_newsletter_identity("org_demo", "acct_1", account_email="walkerdoug.ob@gmail.com")
    assert identity["last_name"] == "Walkerdougob"


def test_identity_falls_back_to_a_generated_surname_without_a_usable_mailbox() -> None:
    identity = build_newsletter_identity("org_demo", "acct_1", account_email="a1@example.com")
    assert identity["last_name"]


def test_identity_regions_are_select_safe() -> None:
    # The observer passes the value to ``select_option`` by value and then by
    # label, so a generated region must be the human-readable option text
    # rather than an uppercase code.
    for index in range(12):
        identity = build_newsletter_identity("org_demo", f"acct_{index}")
        assert identity["country"] and identity["country"] != identity["country"].upper()
        assert identity["state"] and identity["state"] != identity["state"].upper()
        assert identity["city"] and identity["postal_code"]


def test_identity_company_prefers_the_organization_name() -> None:
    identity = build_newsletter_identity("org_demo", "acct_1", org_name="Northstar Media")
    assert identity["company"] == "Northstar Media"


def test_identity_address_is_placeable_and_coherent_with_its_region() -> None:
    # A public form often renders country and state as selects and validates the
    # postal code, so the four address keys must come from one real region
    # rather than four independent draws.
    for index in range(12):
        identity = build_newsletter_identity("org_demo", f"acct_{index}")
        assert any(
            identity["country"] == region["country"]
            and identity["state"] == region["state"]
            and identity["city"] == region["city"]
            and identity["postal_code"] == region["postal_code"]
            for region in _REGIONS
        )
        assert identity["address_line_1"].split(" ", 1)[0].isdigit()
        assert identity["address_line_2"]


def test_identity_addresses_are_not_deliverable_to_a_real_home() -> None:
    # The street name is a placeholder even though the city and postal code are
    # real, so the address passes a form's format checks without describing an
    # actual residence.
    for index in range(12):
        identity = build_newsletter_identity("org_demo", f"acct_{index}")
        assert "Placeholder" in identity["address_line_1"]


def test_identity_date_of_birth_is_a_valid_adult_date() -> None:
    for index in range(12):
        identity = build_newsletter_identity("org_demo", f"acct_{index}")
        born = datetime.date.fromisoformat(identity["date_of_birth"])
        # An age gate must never read the generated visitor as a minor, and the
        # day is capped at 28 so the date is valid in every month.
        assert born <= datetime.date(1995, 12, 31)
        assert born >= datetime.date(1982, 1, 1)
        assert 1 <= born.day <= 28


def test_identity_is_stable_for_the_new_keys_too() -> None:
    first = build_newsletter_identity("org_demo", "acct_1", account_email="walker.ob@gmail.com")
    second = build_newsletter_identity("org_demo", "acct_1", account_email="walker.ob@gmail.com")
    for key in ("address_line_1", "address_line_2", "date_of_birth"):
        assert first[key] == second[key]


def test_merge_precedence_is_requested_then_saved_then_generated() -> None:
    generated = {"first_name": "Generated", "city": "Portland"}
    saved = {"first_name": "Saved"}
    requested = {"first_name": "Requested"}

    assert merge_newsletter_identity(generated, saved, requested)["first_name"] == "Requested"
    assert merge_newsletter_identity(generated, saved, {})["first_name"] == "Saved"
    assert merge_newsletter_identity(generated, {}, {})["first_name"] == "Generated"
    # Keys only the generator knows about still come through.
    assert merge_newsletter_identity(generated, saved, requested)["city"] == "Portland"


def test_merge_ignores_blank_layers_rather_than_clearing_values() -> None:
    merged = merge_newsletter_identity({"city": "Portland"}, {"city": ""}, {"city": None})
    assert merged["city"] == "Portland"


@pytest.mark.parametrize(
    "key",
    ["password", "otp", "totp_seed", "recovery_email", "session_token", "cookie", "client_secret", "2fa_code"],
)
def test_clean_identity_values_rejects_secret_keys(key: str) -> None:
    with pytest.raises(ValueError):
        clean_identity_values({key: "value"})


def test_clean_identity_values_rejects_overlong_input() -> None:
    with pytest.raises(ValueError):
        clean_identity_values({"first_name": "x" * 501})
    with pytest.raises(ValueError):
        clean_identity_values({"k" * 321: "value"})


def test_clean_identity_values_drops_blank_entries() -> None:
    assert clean_identity_values({"first_name": "", "city": "Portland"}) == {"city": "Portland"}


def test_clean_identity_values_uses_the_caller_message() -> None:
    with pytest.raises(ValueError, match="may be saved"):
        clean_identity_values({"password": "x"}, message="Only non-secret signup fields may be saved")
