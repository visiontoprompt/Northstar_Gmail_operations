# Login safety and page-state handling

The login worker boundary is observation-first. It may classify normal Google pages and keep the profile session alive, but it must never defeat an anti-abuse or identity check.

## Supported page signals

- `LOGIN_FORM` — Google sign-in surface detected; human-controlled sign-in may continue.
- `GMAIL_HOME` — Gmail surface detected.
- `ORDINARY_INTERSTITIAL` — known consent/welcome/terms-style page (including the Google Workspace "I understand" / terms-of-service speedbump on `accounts.google.com` and `workspace.google.com`); safe to observe and hand to the approved worker flow.
- `CAPTCHA` — pause (the session is marked `NEEDS_ATTENTION`) and require a human to complete the challenge; the observer keeps watching so Gmail is still detected after it is cleared.
- `MFA_OR_SECURITY_CHECK` — same pause-and-keep-watching behavior.
- `UNKNOWN` — a page is only judged after a settle window (`BROWSER_OBSERVE_SETTLE_SECONDS`, default 5) so a still-loading page can never fail a fresh session; a page that stays unknown is sent to attention rather than guessed.

## Automatic credential entry

When `BROWSER_AUTO_LOGIN` is enabled (default) the observer drives Google's own
sign-in form with the account's stored encrypted secret:

- The identifier (email) and password come only from the account's encrypted
  vault (`AccountSecret`); nothing is passed through the observer API.
- Typing happens only on the Google login hosts `accounts.google.com` and
  `consent.google.com`, and only after `classify_page` reports `LOGIN_FORM`.
- Each step is entered and submitted (Next/Continue/Sign in) and recorded as a
  `session.auto_login.*` audit event.
- If the saved profile already remembers an account, Google shows its
  "Choose an account" chooser; the observer clicks "Use another account" so the
  identifier form appears, then proceeds with the same stored credentials.
- Fields that are already filled are never overwritten, and each page URL is
  attempted once — a rejected password cannot be retried in a loop.
- Recovery codes, 2FA secrets, and CAPTCHA/security-check answers are never
  entered automatically; those pages pause for a human. The observer keeps
  watching so the mailbox is detected once the human clears the challenge.

Setting `BROWSER_AUTO_LOGIN=false` restores observation-only behavior.

`POST /api/v1/sessions/{id}/observations` accepts a page observation from a future worker. The API stores only the signal, host, and bounded path in the audit event; it does not persist page text, query strings, fragments, or credential material.

CAPTCHA-solving services, CAPTCHA bypasses, MFA bypasses, fingerprint manipulation, and automated security-check circumvention are not part of this system. The safe recovery path is: pause the worker, keep the profile/session reserved, notify an operator, let the operator complete the step in the dedicated browser, then record the browser result and resume.
