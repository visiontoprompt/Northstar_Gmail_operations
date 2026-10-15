import socket
from types import SimpleNamespace

import pytest
from scrapling import Selector

import app.newsletter_discovery as discovery
from app.newsletter_discovery import NewsletterDiscoveryError, _FetchedPage, _StealthBudget, _challenge_detected, _challenge_indicators, _fetch, _inspect_page, _stealth_fetch, _validate_public_url, discover_newsletter, validate_newsletter_runtime_url


def _response(html: str, *, status: int = 200, headers: dict[str, str] | None = None) -> SimpleNamespace:
    page = Selector(html)
    return SimpleNamespace(url="https://news.example.com/subscribe", status=status, headers=headers or {}, body=html.encode(), css=page.css, xpath=page.xpath)


def test_discovery_extracts_email_submit_consent_and_bounded_signals() -> None:
    html = """
    <html><body>
      <form id="newsletter-form" class="signup-form">
        <label for="subscriber-email">Email address</label>
        <input id="subscriber-email" type="email" autocomplete="email" required>
        <label><input id="privacy-consent" type="checkbox" required> I agree to the privacy policy</label>
        <button type="submit">Subscribe for updates</button>
      </form>
    </body></html>
    """
    result = _inspect_page(_FetchedPage("https://news.example.com/subscribe", _response(html), 200), "configured_url", 0.72)

    assert result["email_selector"] == "#subscriber-email"
    assert result["submit_selector"] == 'button[type="submit"]'
    assert result["consent_selector"] == "#privacy-consent"
    assert result["configuration"]["form_selector"] == "#newsletter-form"
    assert result["confidence"] > 0.5
    assert "email field detected" in result["evidence"]


def test_discovery_rejects_private_and_credential_bearing_urls() -> None:
    with pytest.raises(NewsletterDiscoveryError):
        _validate_public_url("http://127.0.0.1:8000/subscribe")
    with pytest.raises(NewsletterDiscoveryError):
        _validate_public_url("https://user:password@example.com/subscribe")
    with pytest.raises(NewsletterDiscoveryError):
        validate_newsletter_runtime_url("http://192.168.1.20/subscribe")


def _resolving(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    monkeypatch.setattr(
        discovery.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 0, 0, 0)) for address in addresses
        ],
    )


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        # A public IPv4 destination reached over NAT64 or through an IPv4-mapped
        # address is an ordinary public site. On a NAT64 network this is the
        # only address DNS returns, so judging the wrapper refused every
        # newsletter on that network at once.
        ("64:ff9b::4c4c:1515", None),  # 76.76.21.21
        ("64:ff9b::d896:181", None),  # 216.150.1.129
        ("::ffff:76.76.21.21", None),
        # Unwrapping narrows the guard to the destination; it does not relax it.
        ("64:ff9b::a00:1", "carries the non-public address 10.0.0.1"),
        ("::ffff:169.254.169.254", "carries the non-public address 169.254.169.254"),
        # Local-use NAT64 translates inside a private network by definition.
        ("64:ff9b:1::a00:1", "local-use NAT64"),
        # An address carrying no IPv4 destination is still judged on itself.
        ("fe80::1", "local, private, or reserved"),
        ("2606:4700::1111", None),
    ],
)
def test_a_resolved_address_is_judged_by_the_destination_it_carries(
    monkeypatch: pytest.MonkeyPatch, address: str, reason: str | None
) -> None:
    _resolving(monkeypatch, address)

    failure = discovery._host_public_failure("news.example.com")

    if reason is None:
        assert failure is None
    else:
        assert failure is not None
        assert reason in failure


def test_a_host_resolving_to_both_a_public_and_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The loop continues past an acceptable address rather than returning on the
    # first one, so a public answer cannot mask a private one beside it.
    _resolving(monkeypatch, "64:ff9b::4c4c:1515", "64:ff9b::a00:1")

    failure = discovery._host_public_failure("news.example.com")

    assert failure is not None
    assert "10.0.0.1" in failure


def test_discovery_normalizes_name_address_select_and_custom_controls() -> None:
    html = """
    <html><body>
      <form id="complete-signup">
        <label for="given">First name</label>
        <input id="given" autocomplete="given-name" required>
        <label for="family">Last name</label>
        <input id="family" autocomplete="family-name" required>
        <label for="street">Street address</label>
        <input id="street" autocomplete="address-line1" required>
        <label for="city">City</label>
        <input id="city" autocomplete="address-level2">
        <label for="country">Country</label>
        <select id="country" autocomplete="country"><option value="US">United States</option></select>
        <label><input name="terms" type="checkbox" required> I agree to the terms</label>
        <label><input name="format" type="radio" value="html"> HTML</label>
        <input name="website" autocomplete="new-password">
        <input name="email" type="email" autocomplete="email" required>
        <button type="submit">Subscribe</button>
      </form>
    </body></html>
    """
    result = _inspect_page(_FetchedPage("https://news.example.com/subscribe", _response(html), 200), "configured_url", 0.72)

    fields = {field["field_key"]: field for field in result["fields"]}
    assert fields["first_name"]["required"] is True
    assert fields["last_name"]["selector"] == "#family"
    assert fields["address_line_1"]["autocomplete"] == "address-line1"
    assert fields["city"]["required"] is None
    assert fields["country"]["field_type"] == "select"
    assert fields["terms"]["field_type"] == "checkbox"
    assert fields["custom_radio"]["field_type"] == "radio"
    assert "custom_text" not in fields or fields["custom_text"]["selector"] != 'input[name="website"]'
    assert result["configuration"]["fields"] == result["fields"]


def test_discovery_collects_required_signup_password_field() -> None:
    html = """
    <html><body>
      <form id="register">
        <input id="email" type="email" required>
        <input id="password" type="password" required>
        <button type="submit">Join</button>
      </form>
    </body></html>
    """
    result = _inspect_page(_FetchedPage("https://news.example.com/register", _response(html), 200), "configured_url", 0.72)

    assert result["unsupported_reason"] == ""
    password = next(field for field in result["fields"] if field["field_key"] == "signup_password")
    assert password["field_type"] == "password"
    assert password["required"] is True


def test_discovery_collects_structural_metadata_and_rejects_unrelated_checkboxes() -> None:
    html = """
    <html><body>
      <form id="signup" action="/api/subscribe" method="post">
        <label for="mail">Email</label>
        <input id="mail" type="email" autocomplete="email" required>
        <label for="name">Full name</label>
        <input id="name" autocomplete="name">
        <input id="readonly" name="readonly" readonly>
        <input id="hidden-check" type="checkbox" hidden required>
        <label><input id="analytics" type="checkbox"> Privacy analytics</label>
        <button id="subscribe" type="submit" aria-label="Join the newsletter">Join</button>
      </form>
      <button id="unrelated">Open menu</button>
      <iframe src="/embedded"></iframe>
    </body></html>
    """

    result = _inspect_page(_FetchedPage("https://news.example.com/subscribe", _response(html), 200), "configured_url", 0.72)

    assert result["form_action"] == "https://news.example.com/api/subscribe"
    assert result["form_method"] == "POST"
    assert result["child_frame_present"] is True
    assert any(control["selector"] == "#subscribe" for control in result["submit_controls"])
    assert "#readonly" not in {field["selector"] for field in result["fields"]}
    assert "#hidden-check" not in {field["selector"] for field in result["fields"]}
    assert result["consent_selector"] is None
    fields = {field["field_key"]: field for field in result["fields"]}
    assert fields["full_name"]["selector_candidates"]


def test_fetch_does_not_attempt_anti_bot_solving(monkeypatch: pytest.MonkeyPatch) -> None:
    challenge = _response("<html><title>Just a moment...</title></html>", status=403)
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: challenge)

    fetched = _fetch("https://news.example.com/subscribe", "news.example.com")

    assert fetched.blocked_reason == "BLOCKED_ANTIBOT"
    assert fetched.fetch_mode == "normal"


def test_fetch_retries_429_only_with_bounded_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        _response("rate limited", status=429, headers={"Retry-After": "0"}),
        _response("<html><body><form><input type='email'><button type='submit'>Subscribe</button></form></body></html>"),
    ]
    calls: list[float] = []
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr("app.newsletter_discovery.time.sleep", lambda seconds: calls.append(seconds))

    fetched = _fetch("https://news.example.com/subscribe", "news.example.com")

    assert fetched.blocked_reason is None
    assert fetched.status == 200
    assert calls == [0.0]


def test_fetch_blocks_429_without_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.newsletter_discovery.Fetcher.get",
        lambda *args, **kwargs: _response("rate limited", status=429),
    )

    fetched = _fetch("https://news.example.com/subscribe", "news.example.com")

    assert fetched.blocked_reason == "BLOCKED_ANTIBOT"


def test_fetch_reports_persistent_public_challenge_without_creating_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    challenge = SimpleNamespace(url="https://news.example.com/subscribe", status=403, body=b"challenge-platform", css=lambda *_: [], xpath=lambda *_: [])
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: challenge)

    fetched = _fetch("https://news.example.com/subscribe", "news.example.com")

    assert fetched.fetch_mode == "normal"
    assert fetched.blocked_reason == "BLOCKED_ANTIBOT"


def test_discovery_marks_blocked_pages_and_returns_no_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = _FetchedPage(
        "https://news.example.com/subscribe",
        SimpleNamespace(status=403, body=b"challenge-platform"),
        403,
        fetch_mode="stealth",
        blocked_reason="BLOCKED_ANTIBOT",
    )
    monkeypatch.setattr("app.newsletter_discovery._fetch", lambda *args, **kwargs: blocked)

    result = discover_newsletter("https://news.example.com")

    assert result["discovery_blocked"] is True
    assert result["candidates"] == []
    assert result["blocked_reasons"] == ["BLOCKED_ANTIBOT"]


# --- challenge detection ----------------------------------------------------


def test_cloudflare_proxying_alone_is_not_treated_as_a_challenge() -> None:
    # Cloudflare sets these on every successful response it fronts. Matching
    # the bare ``cf-`` prefix reported every Cloudflare-hosted newsletter as
    # BLOCKED_ANTIBOT before it could ever be profiled.
    healthy = _response(
        "<html><body><form><input type='email'><button type='submit'>Subscribe</button></form></body></html>",
        headers={"cf-cache-status": "HIT", "cf-ray": "8f2c1d4e5f6a7b8c-LHR", "server": "cloudflare"},
    )

    assert _challenge_detected(healthy) is False
    assert _challenge_indicators(healthy, healthy) == []


def test_cloudflare_mitigation_header_is_treated_as_a_challenge() -> None:
    # ``cf-mitigated`` is the header Cloudflare sets when it actually
    # intervened, so it must still be recognized.
    mitigated = _response("<html><body>nope</body></html>", status=403, headers={"cf-mitigated": "challenge"})

    assert _challenge_detected(mitigated) is True
    assert "header:cf-mitigated" in _challenge_indicators(mitigated, mitigated)


def test_cloudflare_fronted_page_still_produces_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <html><body>
      <form id="newsletter-form">
        <input id="subscriber-email" type="email" autocomplete="email" required>
        <button type="submit">Subscribe</button>
      </form>
    </body></html>
    """
    headers = {"cf-cache-status": "DYNAMIC", "cf-ray": "8f2c1d4e5f6a7b8c-LHR"}
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: _response(html, headers=headers))

    result = discover_newsletter("https://news.example.com")

    assert result["discovery_blocked"] is False
    assert result["candidates"]
    # No false challenge means no browser is spawned to re-read a healthy page.
    assert result["stealth_fetch_count"] == 0


# --- stealth tier -----------------------------------------------------------


def _challenge() -> SimpleNamespace:
    return SimpleNamespace(
        url="https://news.example.com/subscribe",
        status=403,
        body=b"challenge-platform",
        headers={},
        css=lambda *_: [],
        xpath=lambda *_: [],
    )


def test_fetch_escalates_to_stealth_when_the_plain_fetch_is_challenged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: _challenge())
    monkeypatch.setattr("app.newsletter_discovery._stealth_fetch", lambda url: _response("<html><body><form></form></body></html>"))

    fetched = _fetch("https://news.example.com/subscribe", "news.example.com", _StealthBudget(1))

    assert fetched.fetch_mode == "stealth"
    assert fetched.blocked_reason is None
    assert fetched.status == 200


def test_stealth_is_not_attempted_without_a_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: _challenge())
    attempted: list[str] = []
    monkeypatch.setattr("app.newsletter_discovery._stealth_fetch", lambda url: attempted.append(url))

    fetched = _fetch("https://news.example.com/subscribe", "news.example.com", _StealthBudget(0))

    assert attempted == []
    assert fetched.fetch_mode == "normal"
    assert fetched.blocked_reason == "BLOCKED_ANTIBOT"


def test_stealth_budget_is_consumed_once_per_challenged_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: _challenge())
    monkeypatch.setattr("app.newsletter_discovery._stealth_fetch", lambda url: None)
    budget = _StealthBudget(2)

    _fetch("https://news.example.com/subscribe", "news.example.com", budget)

    assert budget.remaining == 1
    assert budget.take() is True
    assert budget.take() is False


def test_stealth_failure_degrades_to_the_previous_blocked_result(monkeypatch: pytest.MonkeyPatch) -> None:
    # A missing browser engine must not turn a challenge into an exception.
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: _challenge())
    monkeypatch.setattr("app.newsletter_discovery._stealth_fetch", lambda url: None)

    fetched = _fetch("https://news.example.com/subscribe", "news.example.com", _StealthBudget(1))

    assert fetched.blocked_reason == "BLOCKED_ANTIBOT"
    assert fetched.fetch_mode == "normal"


def test_stealth_read_still_challenged_is_reported_as_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: _challenge())
    still_blocked = _response("<html><body>Just a moment...</body></html>", status=403)
    monkeypatch.setattr("app.newsletter_discovery._stealth_fetch", lambda url: still_blocked)

    fetched = _fetch("https://news.example.com/subscribe", "news.example.com", _StealthBudget(1))

    assert fetched.blocked_reason == "BLOCKED_ANTIBOT"


def test_stealth_redirect_to_a_private_address_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # The public-URL guards must hold for wherever the browser actually landed.
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: _challenge())
    redirected = _response("<html><body>ok</body></html>")
    redirected.url = "http://127.0.0.1:8000/subscribe"
    monkeypatch.setattr("app.newsletter_discovery._stealth_fetch", lambda url: redirected)

    with pytest.raises(NewsletterDiscoveryError):
        _fetch("https://news.example.com/subscribe", "news.example.com", _StealthBudget(1))


def test_stealth_redirect_to_another_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: _challenge())
    redirected = _response("<html><body>ok</body></html>")
    redirected.url = "https://evil.example.net/subscribe"
    monkeypatch.setattr("app.newsletter_discovery._stealth_fetch", lambda url: redirected)

    with pytest.raises(NewsletterDiscoveryError):
        _fetch("https://news.example.com/subscribe", "news.example.com", _StealthBudget(1))


def test_stealth_fetch_returns_none_when_the_browser_engine_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "scrapling.fetchers":
            raise ImportError("browser engine not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert _stealth_fetch("https://news.example.com/subscribe") is None


def test_candidate_reports_how_the_page_was_read(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <html><body>
      <form id="newsletter-form">
        <input id="subscriber-email" type="email" autocomplete="email" required>
        <button type="submit">Subscribe</button>
      </form>
    </body></html>
    """
    fetched = _FetchedPage("https://news.example.com/subscribe", _response(html), 200, fetch_mode="stealth")
    monkeypatch.setattr("app.newsletter_discovery._fetch", lambda *args, **kwargs: fetched)

    result = discover_newsletter("https://news.example.com")

    # Every URL this double serves comes back as a browser read, so the count
    # must track the pages actually inspected rather than being a constant.
    assert result["inspected_url_count"] > 0
    assert result["stealth_fetch_count"] == result["inspected_url_count"]
    assert result["candidates"][0]["fetch_mode"] == "stealth"


def test_discovery_never_exceeds_the_stealth_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each browser re-read spawns a real browser, so the per-run cap has to hold
    # even when every probe URL is challenged. The previous test replaces
    # ``_fetch`` outright, which bypasses the budget entirely; this one exercises
    # the real escalation path.
    monkeypatch.setattr("app.newsletter_discovery.Fetcher.get", lambda *args, **kwargs: _challenge())
    stealth_calls: list[str] = []
    monkeypatch.setattr(
        "app.newsletter_discovery._stealth_fetch",
        lambda url: stealth_calls.append(url) or _response("<html><body></body></html>"),
    )

    result = discover_newsletter("https://news.example.com", max_stealth_fetches=2)

    assert result["inspected_url_count"] > 2
    assert len(stealth_calls) == 2
    assert result["stealth_fetch_count"] <= 2
