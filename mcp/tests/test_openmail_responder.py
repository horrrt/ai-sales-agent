"""The fake prospect on OpenMail (simulator/openmail_responder.py): it answers the agent's emails with every kind of
reply, and the agent reads those answers. No network: a fake post office stands in for OpenMail.

    cd mcp && uv run --extra dev pytest -q
"""
import csv
import random
import sys
from datetime import date
from pathlib import Path

import pytest

from sales_tools import openmail, store
from sales_tools.tools import outreach

from test_pipeline import fresh, lead_through_research, rcpt  # noqa: F401 (fresh is autouse)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "simulator"))
import openmail_responder as resp  # noqa: E402

AGENT, PROSPECT, COLLEAGUE = "agent@omail.sh", "prospect@omail.sh", "colleague@omail.sh"
IDS = {AGENT: "inb-agent", PROSPECT: "inb-prospect", COLLEAGUE: "inb-colleague"}


class PostOffice:
    """Delivers every send to the recipients' inboxes (and keeps the sender's thread), like OpenMail would."""

    def __init__(self):
        self.boxes, self.n = {i: [] for i in IDS.values()}, 0

    def send(self, to, subject, body, cc=None, thread_id=None, idempotency_key=None, inbox_id=None):
        sender = next(a for a, i in IDS.items() if i == (inbox_id or IDS[AGENT]))
        self.n += 1
        thread = thread_id or f"thr-{self.n}"
        for a in [to] + list(cc or []):
            self.boxes[IDS[a]].append({"id": f"m-{self.n}-{a}", "threadId": thread, "fromAddr": sender, "toAddr": a,
                                       "cc": list(cc or []), "subject": subject or "Re: ...", "createdAt": f"2026-11-24T09:{self.n:02d}:00.000Z",
                                       "bodyText": body + "\n\n-- \nSent with OpenMail"})
        return {"messageId": f"om-{self.n}", "threadId": thread, "status": "sent"}

    def inbound(self, limit=50, inbox_id=None):
        return list(self.boxes[inbox_id or IDS[AGENT]])


@pytest.fixture
def post(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENMAIL_API_KEY", "om_test")
    monkeypatch.setenv("OPENMAIL_INBOX_ID", IDS[AGENT])
    monkeypatch.setenv("OPENMAIL_ADDRESS", AGENT)
    monkeypatch.setenv("OPENMAIL_TEST_RECIPIENTS", f"{PROSPECT},{COLLEAGUE}")
    po = PostOffice()
    monkeypatch.setattr(openmail, "send", po.send)
    monkeypatch.setattr(openmail, "inbound", po.inbound)
    monkeypatch.setattr(openmail, "list_inboxes", lambda: [{"address": a, "id": i} for a, i in IDS.items()])
    for name in ("OUT", "STATE", "TRUTH"):
        monkeypatch.setattr(resp, name, tmp_path / Path(getattr(resp, name)).name)
    return po


def agent_writes(body="A short email"):
    lead_through_research("L-001")
    store.LEADS["L-001"]["person"]["email"] = PROSPECT
    r = outreach.save_draft("L-001", "Quick question", body)
    assert outreach.record_approval("L-001", r["subject"], body, **rcpt("L-001"))["ok"]
    assert outreach.send_email("L-001")["via"] == "openmail"


def run_responder(kind, seed=1):
    weights = {k: (1.0 if k == kind else 0.0) for k in resp.KINDS}
    state = resp._load()
    inboxes = {PROSPECT: IDS[PROSPECT], COLLEAGUE: IDS[COLLEAGUE]}
    resp.plan_new(state, inboxes, random.Random(seed), weights, (0, 0), "2026-01-01T00:00:00+00:00")
    for p in state["plans"]:
        p["due"] = "2000-01-01T00:00:00+00:00"  # out-of-office plans wait a few seconds; don't
    sent = resp.send_due(state, inboxes, random.Random(seed), dry_run=False)
    resp._save(state)
    return state, sent


def test_every_kind_of_reply_has_made_up_details_and_a_label():
    rng, today = random.Random(3), date(2026, 11, 24)
    for kind in resp.KINDS:
        if kind == "no_reply":
            continue
        body, cc = resp.compose(kind, rng, today, "colleague@omail.sh")
        assert body.strip() and resp.KINDS[kind][0]
        assert (cc == ["colleague@omail.sh"]) == (kind == "handover")
    _, cc = resp.compose("handover", rng, today, None)  # no second inbox: the colleague is only named
    assert cc == []
    slots = {resp._slot(random.Random(s), today) for s in range(20)}
    assert len(slots) > 10  # different times, not one canned slot


def test_prospect_answers_in_the_thread_and_the_agent_reads_it(post):
    agent_writes()
    state, sent = run_responder("positive_time")
    assert len(sent) == 1 and sent[0]["label"] == "positive"
    replies = outreach.get_replies()
    assert len(replies) == 1 and replies[0]["from"] == PROSPECT and replies[0]["from_lead"] is True
    assert replies[0]["answers"]["subject"] == "Quick question" and "Sent with OpenMail" not in replies[0]["body"]
    rows = list(csv.DictReader(resp.TRUTH.open()))
    assert rows[0]["expected_label"] == "positive" and rows[0]["reply_body"] == sent[0]["body"]
    _, again = run_responder("positive_time")  # a second pass answers nothing twice
    assert again == []


def test_handover_copies_the_second_inbox_in(post):
    agent_writes()
    _, sent = run_responder("handover")
    assert sent[0]["cc"] == [COLLEAGUE]
    assert outreach.get_replies()[0]["cc"] == [COLLEAGUE]


def test_silence_is_logged_but_nothing_is_sent(post):
    agent_writes()
    state, sent = run_responder("no_reply")
    assert sent == [] and state["plans"] == []
    assert list(csv.DictReader(resp.TRUTH.open()))[0]["expected_label"] == "no_reply"
    assert outreach.get_replies() == []
