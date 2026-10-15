"""Bounded 2Captcha token tasks for supported page widgets.

The browser observer keeps the page open while this network work runs in a
separate thread. Unsupported challenges remain a manual handoff.
"""

import json
import threading
import time
from urllib.parse import parse_qs, urlparse, urlunsplit
from urllib.request import Request, urlopen


API_ROOT = "https://api.2captcha.com"


class CaptchaSolveError(Exception):
    pass


def _post(path: str, payload: dict) -> dict:
    request = Request(f"{API_ROOT}/{path}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=12) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get("errorId") != 0:
        raise CaptchaSolveError("2Captcha did not accept or complete this challenge")
    return result


def solve_widget(api_key: str, kind: str, page_url: str, sitekey: str, cancel: threading.Event, timeout_seconds: int = 120) -> str:
    parsed = urlparse(page_url)
    if kind not in {"recaptcha_v2", "turnstile"} or not sitekey or parsed.scheme != "https" or not parsed.hostname:
        raise CaptchaSolveError("This CAPTCHA widget is not supported")
    task_type = "RecaptchaV2TaskProxyless" if kind == "recaptcha_v2" else "TurnstileTaskProxyless"
    # Challenge URLs can carry account or campaign tokens in the query and
    # fragment. A widget solver needs the page origin/path and sitekey only.
    public_page_url = urlunsplit((parsed.scheme, parsed.hostname, parsed.path, "", ""))
    created = _post("createTask", {"clientKey": api_key, "task": {"type": task_type, "websiteURL": public_page_url, "websiteKey": sitekey}})
    task_id = created.get("taskId")
    if not isinstance(task_id, int):
        raise CaptchaSolveError("2Captcha did not return a task ID")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and not cancel.wait(5):
        result = _post("getTaskResult", {"clientKey": api_key, "taskId": task_id})
        if result.get("status") == "processing":
            continue
        solution = result.get("solution") or {}
        token = (solution.get("gRecaptchaResponse") or solution.get("token")) if kind == "recaptcha_v2" else solution.get("token")
        if result.get("status") == "ready" and isinstance(token, str) and token:
            return token
        raise CaptchaSolveError("2Captcha returned no usable token")
    raise CaptchaSolveError("2Captcha timed out or the browser session ended")


def detect_widget(page) -> tuple[str, str] | None:
    """Read only recognized widget sitekeys; never submit a generic challenge."""
    for kind, selector in (
        ("recaptcha_v2", '.g-recaptcha[data-sitekey]:visible, iframe[src*="recaptcha/api2/anchor"]:visible'),
        ("turnstile", '.cf-turnstile[data-sitekey]:visible'),
    ):
        try:
            locator = page.locator(selector)
            if not locator.count():
                continue
            element = locator.first
            key = element.get_attribute("data-sitekey", timeout=1000)
            if not key:
                source = element.get_attribute("src", timeout=1000) or ""
                key = (parse_qs(urlparse(source).query).get("k") or parse_qs(urlparse(source).query).get("sitekey") or [None])[0]
            if key and isinstance(key, str) and len(key) <= 256:
                return kind, key
        except Exception:  # noqa: BLE001 - widgets may detach during render
            continue
    return None


def response_present(page, kind: str) -> bool:
    name = "g-recaptcha-response" if kind == "recaptcha_v2" else "cf-turnstile-response"
    try:
        field = page.locator(f'[name="{name}"]').first
        return bool(field.count() and field.input_value(timeout=1000))
    except Exception:  # noqa: BLE001
        return False


def apply_token(page, kind: str, token: str) -> bool:
    """Deliver a widget token and its declared callback on the live page."""
    try:
        return bool(page.evaluate(r"""({kind, token}) => {
          const name = kind === 'recaptcha_v2' ? 'g-recaptcha-response' : 'cf-turnstile-response';
          const field = document.querySelector(`[name="${name}"]`);
          if (!field) return false;
          field.value = token;
          field.dispatchEvent(new Event('input', {bubbles: true}));
          field.dispatchEvent(new Event('change', {bubbles: true}));
          const widget = field.closest('form')?.querySelector(kind === 'recaptcha_v2' ? '.g-recaptcha' : '.cf-turnstile') || document.querySelector(kind === 'recaptcha_v2' ? '.g-recaptcha' : '.cf-turnstile');
          const callbackName = widget?.getAttribute('data-callback');
          if (callbackName && /^[a-zA-Z_$][\w$]*(\.[a-zA-Z_$][\w$]*)*$/.test(callbackName)) {
            const callback = callbackName.split('.').reduce((value, part) => value?.[part], window);
            if (typeof callback === 'function') callback(token);
          }
          return true;
        }""", {"kind": kind, "token": token}))
    except Exception:  # noqa: BLE001 - page can navigate during token delivery
        return False
