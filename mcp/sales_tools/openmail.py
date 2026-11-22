"""OpenMail: the agent's real inbox (https://docs.openmail.sh), for test addresses only.

On when OPENMAIL_API_KEY and OPENMAIL_INBOX_ID are set. Even then, only an email whose every recipient is
one of our own test inboxes goes through it: the addresses in OPENMAIL_TEST_RECIPIENTS (default: just
OPENMAIL_ADDRESS). Not the whole omail.sh domain: every OpenMail customer's inbox is on it. Anything to a
.test address stays in the in-memory sandbox, so the unit tests and the simulated prospects work as before.
Real people's addresses are refused by outreach either way.

    OPENMAIL_API_KEY=om_...                an inbox-scoped key is enough (send and read this one inbox)
    OPENMAIL_INBOX_ID=...                  GET /v1/inboxes shows it
    OPENMAIL_ADDRESS=...@omail.sh          the inbox's address: what prospects see as From and write back to
    OPENMAIL_TEST_RECIPIENTS=a@omail.sh,.. our other test inboxes a lead may have as its address (optional)

Plain urllib, no extra dependency. Replies are read by polling (get_replies), not by webhook.
"""
import json
import os
import urllib.error
import urllib.request
from email.utils import parseaddr

API = os.environ.get("OPENMAIL_API_URL", "https://api.openmail.sh")
FOOTER = "\n-- \nSent with OpenMail"  # appended by OpenMail on the free plan
TIMEOUT = 20


class OpenMailError(Exception):
    """The API refused or couldn't be reached. str() is safe to show: it never contains the key."""


def enabled() -> bool:
    return bool(os.environ.get("OPENMAIL_API_KEY") and os.environ.get("OPENMAIL_INBOX_ID"))


def address() -> str:
    return (os.environ.get("OPENMAIL_ADDRESS") or "").strip().lower()


def test_recipients() -> set[str]:
    """Our own OpenMail inboxes that may receive a real email: OPENMAIL_TEST_RECIPIENTS plus OPENMAIL_ADDRESS."""
    raw = os.environ.get("OPENMAIL_TEST_RECIPIENTS", "")
    return {parseaddr(a)[1].strip().lower() for a in raw.split(",") if "@" in a} | ({address()} if address() else set())


def is_test_address(value: str) -> bool:
    """An address OpenMail may deliver to: one of our own test inboxes, and only when OpenMail is on."""
    a = parseaddr(value or "")[1].strip().lower()
    return enabled() and bool(a) and a in test_recipients()


def _request(method: str, path: str, body: dict | None = None, headers: dict | None = None) -> dict:
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {os.environ['OPENMAIL_API_KEY']}",
                                          "Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "sales-tools", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}")
            detail = detail.get("error") or detail.get("message") or detail
        except Exception:
            detail = e.reason
        raise OpenMailError(f"OpenMail {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise OpenMailError(f"OpenMail unreachable: {getattr(e, 'reason', e)}") from None


def send(to: str, subject: str, body: str, cc: list[str] | None = None, thread_id: str | None = None,
         idempotency_key: str | None = None, inbox_id: str | None = None) -> dict:
    """Send from our inbox (or inbox_id: the simulated prospects send from theirs). Returns {messageId, threadId,
    status}. A repeat with the same idempotency key within 24 hours returns the first send instead of sending
    again, so a retried tool call can't send twice."""
    payload = {"to": to, "body": body}
    if subject:
        payload["subject"] = subject
    if cc:
        payload["cc"] = list(cc)
    if thread_id:
        payload.update(threadId=thread_id, includeQuote=False)  # our body already says what it needs to
    return _request("POST", f"/v1/inboxes/{inbox_id or os.environ['OPENMAIL_INBOX_ID']}/send", payload,
                    {"Idempotency-Key": idempotency_key} if idempotency_key else None)


def inbound(limit: int = 50, inbox_id: str | None = None) -> list[dict]:
    """Recent emails that arrived in our inbox (or inbox_id), newest first, as OpenMail returns them."""
    out = _request("GET", f"/v1/inboxes/{inbox_id or os.environ['OPENMAIL_INBOX_ID']}/messages?direction=inbound&limit={limit}")
    return list(out.get("data") or [])


def list_inboxes() -> list[dict]:
    """Every inbox the key can see (an inbox-scoped key sees none but its own, or gets an error)."""
    return list(_request("GET", "/v1/inboxes").get("data") or [])


def clean_body(text: str) -> str:
    """The body without OpenMail's footer."""
    text = text or ""
    i = text.find(FOOTER)
    return (text[:i] if i >= 0 else text).strip()
