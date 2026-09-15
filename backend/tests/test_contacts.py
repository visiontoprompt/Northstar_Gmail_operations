import pytest

from app.contacts import build_google_contacts_csv, parse_emails


def test_parse_emails_basic():
    emails = parse_emails("  Alice@Example.COM \n bob@other.org\n")
    assert emails == ["alice@example.com", "bob@other.org"]


def test_parse_emails_deduplicates():
    emails = parse_emails("a@b.com\nA@b.com\na@b.com")
    assert emails == ["a@b.com"]


def test_parse_emails_rejects_invalid():
    with pytest.raises(ValueError, match="Invalid email"):
        parse_emails("not-an-email\nok@example.com")


def test_parse_emails_rejects_empty():
    with pytest.raises(ValueError, match="No emails"):
        parse_emails("   \n \n")


def test_parse_emails_rejects_too_many():
    with pytest.raises(ValueError, match="Too many"):
        parse_emails("\n".join(f"user{i}@example.com" for i in range(501)))


def test_build_csv_header_and_rows():
    csv = build_google_contacts_csv(["alice@example.com", "bob@example.org"])
    lines = csv.strip().splitlines()
    assert lines[0] == "Name,Given Name,Family Name,E-mail 1 - Value"
    assert '"Alice","Alice","","alice@example.com"' in lines
    assert '"Bob","Bob","","bob@example.org"' in lines