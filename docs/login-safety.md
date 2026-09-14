# Login safety and page-state handling

The login worker boundary is observation-first. It may classify normal Google pages and keep the profile session alive, but it must never defeat an anti-abuse or identity check.

## Supported page signals

- `LOGIN_FORM` — Google sign-in surface detected; human-controlled sign-in may continue.
- `GMAIL_HOME` — Gmail surface detected.
- `ORDINARY_INTERSTITIAL` — known consent/welcome/terms-style page; safe to observe and hand to the approved worker flow.
- `CAPTCHA` — pause and require a human to complete the challenge.
- `MFA_OR_SECURITY_CHECK` — pause and require a human to complete the verification.
- `UNKNOWN` — stop and send the session to attention rather than guessing.

`POST /api/v1/sessions/{id}/observations` accepts a page observation from a future worker. The API stores only the signal, host, and bounded path in the audit event; it does not persist page text, query strings, fragments, or credential material.

CAPTCHA-solving services, CAPTCHA bypasses, MFA bypasses, fingerprint manipulation, and automated security-check circumvention are not part of this system. The safe recovery path is: pause the worker, keep the profile/session reserved, notify an operator, let the operator complete the step in the dedicated browser, then record the browser result and resume.
