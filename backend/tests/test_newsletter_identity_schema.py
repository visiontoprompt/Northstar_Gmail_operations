from pydantic import ValidationError

from app.schemas import NewsletterAccountIdentityUpdate, NewsletterSignupTestRequest


def test_newsletter_identity_accepts_approved_non_secret_fields() -> None:
    payload = NewsletterAccountIdentityUpdate(values={"first_name": "Alexandra", "selector:#company": "Northstar"})

    assert payload.values == {"first_name": "Alexandra", "selector:#company": "Northstar"}


def test_newsletter_identity_rejects_secret_like_fields_for_saved_and_one_off_runs() -> None:
    for model in (NewsletterAccountIdentityUpdate,):
        try:
            model(values={"password": "not allowed"})
        except ValidationError:
            pass
        else:
            raise AssertionError("secret-like fields must not be persisted")

    try:
        NewsletterSignupTestRequest(account_id="acct_test", profile_id="profile_test", test_identity={"otp": "123456"})
    except ValidationError:
        pass
    else:
        raise AssertionError("secret-like fields must not be accepted for a one-off run")
