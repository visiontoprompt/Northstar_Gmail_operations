"""Rendered-form and mailbox fixtures for newsletter continuation steps."""

import shutil
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from app.newsletter_runner import _continue_topic_choice_step, _continue_password_step, _find_submit_control, _submit_once, _submit_with_enter
from app.observer import BrowserObserver, _mail_collect_review_candidates, _mail_fill_confirmation_details, _mail_review_candidate_rows, _mail_subject_query, _newsletter_fill_live_fields
from app.newsletter_fields import semantic_field_key
from app.schemas import MailWorkflowRequest


@pytest.fixture
def page():
    executable = shutil.which("google-chrome") or shutil.which("chromium")
    if not executable:
        pytest.skip("A local Chromium browser is unavailable")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox"])
        try:
            yield browser.new_page()
        finally:
            browser.close()


def test_email_first_topic_form_selects_only_the_saved_newsletter(page) -> None:
    page.set_content("""
        <form>
          <div><input type="checkbox" id="javascript" checked><strong>JavaScript Weekly</strong></div>
          <div><input type="checkbox" id="ruby"><strong>Ruby Weekly</strong></div>
          <div><input type="checkbox" id="react"><strong>React Status</strong></div>
          <button type="button" onclick="window.submits=(window.submits||0)+1">Click here to subscribe</button>
        </form>
    """)
    observer = BrowserObserver("topic-session", "topic-profile", 0, purpose="newsletter", newsletter_config={"newsletter_name": "Rubyweekly Com"})

    assert _continue_topic_choice_step(observer, page) is True
    assert page.locator("#ruby").is_checked()
    assert not page.locator("#javascript").is_checked()
    assert not page.locator("#react").is_checked()
    assert page.evaluate("window.submits") == 1
    assert _continue_topic_choice_step(observer, page) is False
    assert page.evaluate("window.submits") == 1


def test_ambiguous_topic_form_does_not_subscribe_to_every_choice(page) -> None:
    page.set_content("""
        <form>
          <div><input type="checkbox" id="javascript"><strong>JavaScript Weekly</strong></div>
          <div><input type="checkbox" id="ruby"><strong>Ruby Weekly</strong></div>
          <button type="button" onclick="window.submits=(window.submits||0)+1">Click here to subscribe</button>
        </form>
    """)
    observer = BrowserObserver("ambiguous-session", "ambiguous-profile", 0, purpose="newsletter", newsletter_config={"newsletter_name": "Unrelated Digest"})

    assert _continue_topic_choice_step(observer, page) is False
    assert not page.locator("#javascript").is_checked()
    assert not page.locator("#ruby").is_checked()
    assert page.evaluate("window.submits || 0") == 0


def test_bulk_confirmation_auto_detect_preserves_per_account_limit() -> None:
    payload = MailWorkflowRequest(action="confirmation_review", review_mode="queue", max_messages=7)

    assert payload.subject == ""
    assert payload.max_messages == 7
    assert _mail_subject_query(payload.model_dump()) == "in:anywhere is:unread {subject:subscription subject:newsletter subject:subscribe}"
    with pytest.raises(ValueError, match="Enter a subject line"):
        MailWorkflowRequest(action="confirmation_review", review_mode="handoff", subject="")


def test_auto_detect_ignores_account_security_email(page) -> None:
    page.set_content("""
        <div role="main"><table>
          <tr class="zA" data-thread-id="security1"><td><span class="bog">Confirm your account password</span></td></tr>
          <tr class="zA" data-thread-id="newsletter1"><td><span class="bog">Confirm your newsletter subscription</span></td></tr>
        </table></div>
    """)

    rows = _mail_review_candidate_rows(page, {"subject": "", "match_mode": "contains"})

    assert [row["key"] for row in rows] == ["gmail:newsletter1"]


def test_confirmation_limit_collects_across_gmail_result_pages(page) -> None:
    page.set_content("""
        <div role="main"><table id="results">
          <tr class="zA" data-thread-id="newsletter1"><td><span class="bog">Confirm newsletter subscription</span></td></tr>
        </table></div>
        <button aria-label="Older" onclick="
          const row=document.createElement('tr');
          row.className='zA'; row.dataset.threadId='newsletter2';
          row.innerHTML='<td><span class=bog>Confirm another subscription</span></td>';
          document.querySelector('#results').appendChild(row);
          this.disabled=true;
        ">Older</button>
    """)
    gmail = SimpleNamespace(
        url="https://mail.google.com/mail/u/0/#search/newsletter",
        evaluate=page.evaluate,
        locator=page.locator,
    )
    observer = BrowserObserver(
        "queue-session", "queue-profile", 0, purpose="mail",
        mail_workflow={"action": "confirmation_review", "review_mode": "queue", "subject": "", "max_messages": 2},
    )

    for _ in range(20):
        _mail_collect_review_candidates(observer, gmail, observer.mail_workflow)
        if observer._mail_review_collection_done:
            break

    assert observer._mail_review_collection_done
    assert [row["key"] for row in observer._mail_review_candidates] == ["gmail:newsletter1", "gmail:newsletter2"]
    assert observer._mail_review_pages_advanced == 1


def test_submit_timeout_after_navigation_waits_for_result(monkeypatch, page) -> None:
    page.set_content('<form><button type="button">Subscribe</button></form>')
    events = []
    observer = BrowserObserver(
        "timeout-session", "timeout-profile", 0, purpose="newsletter",
        report_event=lambda _session, event, _detail, _result, _metadata=None: events.append(event),
    )
    monkeypatch.setattr("app.observer._newsletter_attach_submission_listener", lambda *_args: None)

    class NavigatingControl:
        def click(self, **_kwargs):
            page.set_content("<h1>Thanks for subscribing</h1>")
            raise TimeoutError("the old button detached")

    _submit_once(observer, page, NavigatingControl())

    assert "newsletter.signup.submit_uncertain" in events
    assert not observer._stop.is_set()


def test_confirmation_page_fills_approved_name_email_and_agreement(page) -> None:
    page.set_content("""
        <form>
          <label>First name <input name="first_name" required></label>
          <label>Last name <input name="last_name" required></label>
          <label>Email address <input type="email" name="email" required></label>
          <div><input type="checkbox" id="terms" required><span>I accept the Terms of Service</span></div>
          <button type="button">Confirm subscription</button>
        </form>
    """)
    observer = BrowserObserver(
        "details-session", "details-profile", 0, purpose="mail",
        mail_workflow={"action": "confirmation_review", "_confirmation_email": "reader@example.com", "_confirmation_identity": {"first_name": "Avery", "last_name": "Reader"}},
    )

    ready, detail, filled = _mail_fill_confirmation_details(observer, page)

    assert (ready, detail) == (True, "")
    assert filled == 4
    assert page.locator('[name="first_name"]').input_value() == "Avery"
    assert page.locator('[name="last_name"]').input_value() == "Reader"
    assert page.locator('[name="email"]').input_value() == "reader@example.com"
    assert page.locator("#terms").is_checked()


def test_confirmation_page_leaves_verification_code_for_review(page) -> None:
    page.set_content("""
        <form>
          <label>Your name <input name="full_name" required></label>
          <label>Verification code <input name="one_time_code" autocomplete="one-time-code" required></label>
          <button type="button">Confirm subscription</button>
        </form>
    """)
    observer = BrowserObserver(
        "code-session", "code-profile", 0, purpose="mail",
        mail_workflow={"action": "confirmation_review", "_confirmation_identity": {"full_name": "Avery Reader"}},
    )

    ready, detail, filled = _mail_fill_confirmation_details(observer, page)

    assert ready is False
    assert "Verification code" in detail
    assert filled == 1
    assert page.locator('[name="one_time_code"]').input_value() == ""


def test_confirmation_page_reports_unknown_required_field(page) -> None:
    page.set_content("""
        <form>
          <label>Company registration number <input name="registration_number" required></label>
          <button type="button">Confirm subscription</button>
        </form>
    """)
    observer = BrowserObserver(
        "unknown-session", "unknown-profile", 0, purpose="mail",
        mail_workflow={"action": "confirmation_review", "_confirmation_identity": {"full_name": "Avery Reader"}},
    )

    ready, detail, filled = _mail_fill_confirmation_details(observer, page)

    assert ready is False
    assert "Company registration number" in detail
    assert filled == 0


def test_confirmation_page_fills_name_in_embedded_form(page) -> None:
    page.set_content("""<iframe srcdoc='<form><label>Full name <input name="full_name" required></label>
        <button type="button">Confirm subscription</button></form>'></iframe>""")
    frame = page.frame_locator("iframe")
    frame.locator('[name="full_name"]').wait_for()
    observer = BrowserObserver(
        "frame-session", "frame-profile", 0, purpose="mail",
        mail_workflow={"action": "confirmation_review", "_confirmation_identity": {"full_name": "Avery Reader"}},
    )

    ready, detail, filled = _mail_fill_confirmation_details(observer, page)

    assert (ready, detail, filled) == (True, "", 1)
    assert frame.locator('[name="full_name"]').input_value() == "Avery Reader"


def test_newsletter_confirmation_uses_its_account_identity(page) -> None:
    page.set_content("""
        <form>
          <label>Your name <input name="full_name" required></label>
          <label>Email <input name="email" type="email" required></label>
          <button type="button">Confirm subscription</button>
        </form>
    """)
    observer = BrowserObserver(
        "newsletter-confirm-session", "newsletter-confirm-profile", 0,
        purpose="newsletter_confirm",
        newsletter_config={"test_identity": {"full_name": "Avery Reader"}, "account_email": "reader@example.com"},
    )

    ready, detail, filled = _mail_fill_confirmation_details(observer, page)

    assert (ready, detail, filled) == (True, "", 2)
    assert page.locator('[name="full_name"]').input_value() == "Avery Reader"
    assert page.locator('[name="email"]').input_value() == "reader@example.com"


def test_newsletter_confirmation_uses_saved_publisher_password_only(page) -> None:
    page.set_content('<form><input type="password" name="email" required></form>')
    observer = BrowserObserver(
        "newsletter-password-confirm", "password-profile", 0,
        purpose="newsletter_confirm",
        newsletter_config={"signup_password": " Publisher-only password "},
    )

    assert _mail_fill_confirmation_details(observer, page) == (True, "", 1)
    assert page.locator('input[type="password"]').input_value() == " Publisher-only password "


def test_signup_password_requires_separate_publisher_secret(page) -> None:
    page.set_content('<form><input type="email" name="email"><input type="password" name="email" required><button>Join</button></form>')
    assert semantic_field_key({"type": "password", "name": "email"}) == "signup_password"
    observer = BrowserObserver(
        "password-session", "password-profile", 0, purpose="newsletter",
        newsletter_config={"account_email": "reader@example.com", "signup_password": " Site-only passphrase "},
    )

    ready, detail, filled = _newsletter_fill_live_fields(page, observer)

    assert (ready, detail) == (True, "")
    assert filled >= 2
    assert page.locator('input[type="password"]').input_value() == " Site-only passphrase "
    assert page.locator('input[type="email"]').input_value() == "reader@example.com"


def test_password_only_followup_uses_enter_when_no_button(page) -> None:
    page.set_content('''<form onsubmit="event.preventDefault(); window.submits=(window.submits||0)+1">
        <label>Passwort <input type="password" name="password" required></label></form>''')
    observer = BrowserObserver(
        "password-followup-session", "password-followup-profile", 0, purpose="newsletter",
        newsletter_config={"signup_password": "Site-only passphrase"},
    )
    observer._newsletter_submit_attempted = True

    assert _continue_password_step(observer, page) is True
    assert page.locator('input[type="password"]').input_value() == "Site-only passphrase"
    assert page.evaluate("window.submits") == 1
    assert _continue_password_step(observer, page) is False


@pytest.mark.parametrize("label", ["Join", "Suscríbete", "Abonnez-vous", "Iscriviti", "Aanmelden"])
def test_multilingual_signup_controls_are_detected(page, label: str) -> None:
    page.set_content(f'<form><input type="email" id="email"><button type="button">{label}</button></form>')
    observer = BrowserObserver("language-session", "language-profile", 0, purpose="newsletter")

    assert _find_submit_control(page, page.locator("#email"), observer) is not None


def test_email_form_uses_enter_if_button_is_missing(page) -> None:
    page.set_content('''<form onsubmit="event.preventDefault(); window.submits=(window.submits||0)+1">
        <input type="email" id="email"></form>''')
    observer = BrowserObserver("enter-session", "enter-profile", 0, purpose="newsletter")

    _submit_with_enter(observer, page, page.locator("#email"))

    assert page.evaluate("window.submits") == 1
