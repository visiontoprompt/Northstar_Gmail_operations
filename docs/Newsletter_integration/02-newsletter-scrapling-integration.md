# Newsletter Automation — Scrapling Integration Specification

## 1. Purpose

Scrapling should be integrated into the Newsletter Automation module as a **public-page discovery, parsing, and adaptive recovery system**.

It must NOT replace:

- the existing Playwright browser framework
- persistent Gmail browser profiles
- account proxy assignments
- BrowserObserver
- Gmail UI automation
- account/profile locks
- manual-review controls

The existing persistent browser infrastructure remains responsible for real account execution.

---

# 2. Scrapling Responsibilities

Scrapling should be used for:

```text
newsletter discovery
signup-page discovery
form detection
field detection
submit-control detection
consent-control detection
success/failure signal extraction
confirmation-domain discovery
public-page structure parsing
adaptive element recovery
candidate selector generation
automation-profile refresh
```

Scrapling operates mainly before or outside account-specific execution.

---

# 3. Not Allowed as Primary Execution Layer

Do not redesign the SaaS so that Scrapling becomes the account browser controller.

Avoid:

```text
Scrapling controlling Gmail sessions
Scrapling replacing persistent Chrome profiles
Scrapling owning Gmail credentials
Scrapling bypassing account security checks
Scrapling bypassing CAPTCHA
Scrapling replacing BrowserObserver
```

The existing Playwright/profile model must stay authoritative for Gmail-account operations.

---

# 4. Discovery Architecture

```text
Newsletter Website
       │
       ▼
Scrapling Discovery
       │
       ├── identify signup URLs
       ├── parse candidate forms
       ├── identify email input
       ├── identify submit button
       ├── identify consent control
       ├── identify success signals
       └── identify confirmation hints
       │
       ▼
Candidate NewsletterAutomationProfile
       │
       ▼
Single-account Playwright Test
       │
   ┌───┴────┐
  pass     fail
   │         │
Promote   Review / Rediscover
```

---

# 5. New Discovery Service

Recommended module:

```text
backend/app/newsletters/discovery.py
```

or after backend modularization:

```text
backend/app/domains/newsletters/discovery.py
```

Main service concepts:

```text
discover_newsletter_site()
discover_signup_pages()
inspect_signup_page()
extract_form_candidates()
score_email_field()
score_submit_control()
extract_consent_candidate()
extract_success_signals()
discover_confirmation_domains()
create_candidate_profile()
rediscover_broken_profile()
```

---

# 6. Newsletter URL Discovery

Given:

```text
https://example.com
```

Scrapling should inspect likely locations such as:

```text
/newsletter
/subscribe
/newsletters
/email
/updates
```

and likely navigation/footer links containing language similar to:

```text
Newsletter
Subscribe
Email updates
Stay updated
Get updates
Join our mailing list
```

Do not assume the first matching path is correct.

Return ranked candidates.

Example:

```json
{
  "candidates": [
    {
      "url": "https://example.com/newsletter",
      "confidence": 0.95
    },
    {
      "url": "https://example.com/subscribe",
      "confidence": 0.76
    },
    {
      "url": "https://example.com/",
      "source": "footer_form",
      "confidence": 0.63
    }
  ]
}
```

---

# 7. Form Discovery

For each candidate page, inspect:

```text
form
input[type=email]
input[autocomplete=email]
input names/ids
labels
aria-labels
placeholders
button text
submit inputs
nearby newsletter/subscribe copy
consent controls
success/error containers
```

The output should be normalized into a candidate automation profile.

Example:

```json
{
  "signup_url": "https://example.com/newsletter",
  "email_selector": "input[type='email']",
  "submit_selector": "form.newsletter button[type='submit']",
  "consent_selector": null,
  "success_signals": [
    "Thank you for subscribing"
  ],
  "confirmation_required": true,
  "confidence": 0.94
}
```

---

# 8. Candidate Scoring

Email-field candidates can consider:

```text
type=email                  strong positive
autocomplete=email          strong positive
name contains email         positive
id contains email           positive
label contains email        positive
aria-label contains email   positive
placeholder contains email  moderate
inside signup form          positive
visible                     required
enabled                     required
```

Submit-control scoring:

```text
type=submit
inside same form
button text contains subscribe/join/signup
visible
enabled
close to email field
```

Consent-control scoring:

```text
checkbox
inside same form
privacy/newsletter consent text
required field
associated label
```

---

# 9. Candidate Profile Status

New discovery must not automatically become production-active.

Recommended lifecycle:

```text
DISCOVERED
   ↓
CANDIDATE
   ↓
TESTING
   ↓
TEST_PASSED
   ↓
ACTIVE
```

Failure:

```text
TEST_FAILED
MANUAL_REVIEW
```

---

# 10. Adaptive Recovery

When an active profile fails in Playwright:

```text
known selector fails
       ↓
try deterministic alternatives
       ↓
still unresolved
       ↓
Scrapling rediscovery
       ↓
compare current page with prior profile
       ↓
find likely replacement element
       ↓
create candidate vNext
       ↓
single-account test
       ↓
promote only if successful
```

This provides adaptive recovery without an LLM.

---

# 11. Versioning

Example:

```text
Example Daily

Profile v1
status: SUPERSEDED

Profile v2
status: ACTIVE

Profile v3
status: CANDIDATE
```

Never overwrite v2 while v3 is still unverified.

Required operations:

```text
Create candidate
Test candidate
Promote candidate
Reject candidate
Rollback
```

---

# 12. Breakage Detection

Rediscovery should be triggered by factual conditions such as:

```text
repeated email selector failures
repeated submit selector failures
success signal no longer observed
significant increase in manual review
repeated page mismatch
confirmation workflow failures
```

Do not trigger profile replacement from a single transient network error.

---

# 13. Newsletter Discovery UI

The dedicated Newsletters page should expose discovery.

Example:

```text
Add Newsletter

Website URL
[ https://example.com ]

[ Discover ]
```

Result:

```text
Discovery Results

Possible signup pages

✓ example.com/newsletter       95%
✓ example.com/subscribe        76%
? homepage footer form         63%

Recommended candidate:
example.com/newsletter

[Inspect]
[Test]
[Create Automation Profile]
```

---

# 14. Rediscovery UI

On Automation tab:

```text
Automation status:
AUTOMATION_BROKEN

Last successful run:
2026-09-18

Current profile:
v4

Failure:
Email field not found

[Rediscover]
```

After discovery:

```text
Candidate v5

Email selector changed
Submit selector unchanged

Confidence:
0.92

[Test with 1 account]
```

---

# 15. Security and Data Boundaries

Scrapling should not receive:

```text
Gmail passwords
recovery credentials
2FA seeds
browser cookies
Gmail email bodies
workspace secrets
proxy passwords unless required for a public discovery fetch
```

Public discovery output may store:

```text
URLs
selectors
labels
bounded text signals
domain names
confidence
structural metadata
```

Avoid storing large raw HTML snapshots indefinitely unless a specific debugging/retention policy is defined.

---

# 16. Gmail Confirmation Boundary

Scrapling is NOT responsible for reading Gmail confirmation messages.

That remains:

```text
BrowserObserver
+
Playwright
+
persistent Gmail profile
```

Scrapling may assist with understanding the public confirmation landing page after an allowlisted URL is opened, but the main confirmation execution remains in the existing browser workflow.

---

# 17. AI

No AI or LLM should be required for Scrapling recovery in the current phase.

Current fallback:

```text
deterministic locator
       ↓
Scrapling adaptive rediscovery
       ↓
single-account validation
       ↓
manual review
```

Future AI may be introduced later behind a separate adapter.

---

# 18. Recommended Implementation Order

1. Add Scrapling dependency.
2. Create isolated discovery module.
3. Implement public site fetch.
4. Implement signup-link discovery.
5. Implement form candidate extraction.
6. Implement field/button scoring.
7. Create candidate NewsletterAutomationProfile.
8. Add discovery endpoint.
9. Add discovery UI.
10. Add single-account validation.
11. Add candidate promotion.
12. Add rediscovery on repeated automation failure.
13. Add version comparison.
14. Add rollback.
15. Add health-triggered rediscovery.

---

# 19. Key Principle

Scrapling should improve the system's ability to **understand and recover from newsletter website changes**.

It should not replace the mature Gmail/browser execution system already present in the SaaS.
