"""OpenMail: emails to our own OpenMail test inboxes go out for real, and their replies come back into the inbox.

No network: openmail.send and openmail.inbound are replaced by fakes. The clock is pinned as in test_pipeline.

    cd mcp && uv run --extra dev pytest -q
"""
import pytest

from sales_tools import openmail, store
from sales_tools.tools import outreach

from test_pipeline import at, fresh, lead_through_research, rcpt  # noqa: F401 (fresh is autouse)

LEAD = "L-001"
OURS = "agent@omail.sh"
PROSPECT = "anna-test@omail.sh"


class FakeOpenMail:
    """Records sends; inbound() returns whatever the test put in `incoming`."""

    def __init__(self):
        self.sent, self.incoming, self.fail, self.tried = [], [], None, []

    def send(self, to, subject, body, cc=None, thread_id=None, idempotency_key=None):
        self.tried.append(idempotency_key)
        if self.fail:
            raise openmail.OpenMailError(self.fail)
        self.sent.append({"to": to, "subject": subject, "body": body, "cc": cc, "thread_id": thread_id, "key": idempotency_key})
        return {"messageId": f"om-{len(self.sent)}", "threadId": thread_id or "thr-1", "status": "sent"}

    def inbound(self, limit=50):
        return list(self.incoming)


@pytest.fixture
def om(monkeypatch):
    monkeypatch.setenv("OPENMAIL_API_KEY", "om_test")
    monkeypatch.setenv("OPENMAIL_INBOX_ID", "inb-1")
    monkeypatch.setenv("OPENMAIL_ADDRESS", OURS)
    monkeypatch.setenv("OPENMAIL_TEST_RECIPIENTS", PROSPECT)
    fake = FakeOpenMail()
    monkeypatch.setattr(openmail, "send", fake.send)
    monkeypatch.setattr(openmail, "inbound", fake.inbound)
    return fake


def openmail_lead():
    lead_through_research(LEAD)
    store.LEADS[LEAD]["person"]["email"] = PROSPECT


def approve_and_send(subject="Quick question", body="A short email", cc=None):
    r = outreach.save_draft(LEAD, subject, body, cc=cc)
    assert r["ok"], r
    assert outreach.record_approval(LEAD, r["subject"], body, **rcpt(LEAD))["ok"]
    return outreach.send_email(LEAD)


def test_email_to_an_openmail_test_inbox_goes_out_through_openmail(om):
    openmail_lead()
    r = approve_and_send()
    assert r["ok"] and r["status"] == "sent" and r["via"] == "openmail"
    assert om.sent == [{"to": PROSPECT, "subject": "Quick question", "body": "A short email", "cc": [], "thread_id": None,
                        "key": store.OUTBOX[-1]["send_key"]}]
    m = store.OUTBOX[-1]
    assert m["openmail"] == {"message_id": "om-1", "thread_id": "thr-1"} and OURS in m["from"]
    # the follow-up goes into the same OpenMail thread
    at("2026-11-27T10:00:00+01:00")
    assert approve_and_send(body="Following up")["ok"]
    assert om.sent[-1]["thread_id"] == "thr-1" and om.sent[-1]["subject"].startswith("Re: ")


def test_openmail_failure_sends_nothing_and_keeps_the_approval(om):
    openmail_lead()
    om.fail = "OpenMail 429: rate_limit_exceeded"
    r = approve_and_send()
    assert r == {"ok": False, "error": "send_failed", "detail": "OpenMail 429: rate_limit_exceeded",
                 "next": r["next"]}
    assert store.OUTBOX == [] and store.LEADS[LEAD]["stage"] == "approved"
    om.fail = None
    assert outreach.send_email(LEAD)["status"] == "sent" and len(om.sent) == 1
    # the retry carries the failed attempt's idempotency key: had OpenMail sent and only its answer been lost,
    # OpenMail returns the first email instead of sending a second
    assert om.tried == [om.sent[0]["key"], om.sent[0]["key"]]


def test_reply_in_the_openmail_thread_reaches_get_replies_once(om):
    openmail_lead()
    approve_and_send()
    ours = store.OUTBOX[-1]
    om.incoming = [
        {"id": "in-1", "threadId": "thr-1", "fromAddr": OURS, "subject": "Quick question",  # our own copy, if we wrote to ourselves
         "bodyText": "A short email\n\n-- \nSent with OpenMail (https://openmail.sh), email for agents.\n",
         "createdAt": "2026-11-24T09:00:05.000Z"},
        {"id": "in-2", "threadId": "thr-1", "fromAddr": PROSPECT, "toAddr": OURS, "cc": ["erik@omail.sh"],
         "subject": "Re: Quick question", "bodyText": "Sounds good, call me Thursday.\n\n-- \nSent with OpenMail",
         "createdAt": "2026-11-24T09:30:00.000Z", "attachments": [{"filename": "security.pdf"}]},
        {"id": "in-3", "threadId": "thr-other", "fromAddr": "someone@omail.sh", "bodyText": "Not ours",
         "createdAt": "2026-11-24T09:31:00.000Z"},
    ]
    replies = outreach.get_replies()
    assert len(replies) == 1
    r = replies[0]
    assert r["body"] == "Sounds good, call me Thursday." and r["from"] == PROSPECT and r["from_lead"] is True
    assert r["cc"] == ["erik@omail.sh"] and r["attachments"] == ["security.pdf"]
    assert r["answers"]["message_id"] == ours["message_id"] and r["in_reply_to"] == ours["message_id"]
    outreach.get_replies()
    assert len(store.INBOX) == 1  # read again, copied once


def test_held_openmail_email_goes_out_when_released(om):
    openmail_lead()
    at("2026-11-24T22:00:00+01:00")
    assert approve_and_send()["status"] == "scheduled" and om.sent == []
    at("2026-11-25T08:05:00+01:00")
    outreach.get_replies()
    assert len(om.sent) == 1 and store.OUTBOX[-1]["status"] == "sent"


def test_reply_waiting_in_openmail_stops_the_follow_up(om):
    openmail_lead()
    approve_and_send()
    om.incoming = [{"id": "in-1", "threadId": "thr-1", "fromAddr": PROSPECT, "subject": "Re: Quick question",
                    "bodyText": "Not now, maybe next quarter.", "createdAt": "2026-11-25T09:00:00.000Z"}]
    at("2026-11-28T10:00:00+01:00")  # the first follow-up is due by now
    om.incoming, waiting = [], om.incoming
    assert LEAD in [d["lead_id"] for d in outreach.due_follow_ups() if d["kind"] == "email"]  # no reply yet
    om.incoming = waiting  # the reply has arrived in OpenMail, unread by the agent
    assert LEAD not in [d["lead_id"] for d in outreach.due_follow_ups() if d["kind"] == "email"]
    assert len(store.INBOX) == 1


def test_only_our_own_openmail_inboxes_get_real_email(om):
    openmail_lead()
    store.LEADS[LEAD]["person"]["email"] = "someone-else@omail.sh"  # on OpenMail's shared domain, but not ours
    assert outreach.save_draft(LEAD, "Quick question", "A short email")["error"] == "not_sandbox_recipient"
    assert om.sent == []


def test_openmail_addresses_are_refused_without_openmail_and_never_mixed(om, monkeypatch):
    openmail_lead()
    r = outreach.save_draft(LEAD, "Quick question", "A short email", cc=["mette@kvitto.sandbox.test"])
    assert r["ok"]
    assert outreach.record_approval(LEAD, r["subject"], "A short email", **rcpt(LEAD))["ok"]
    assert outreach.send_email(LEAD)["error"] == "mixed_recipients" and om.sent == []
    monkeypatch.delenv("OPENMAIL_API_KEY")
    assert outreach.save_draft(LEAD, "Quick question", "A short email")["error"] == "not_sandbox_recipient"
