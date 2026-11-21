"""Unit tests for the pipeline gates. No model calls: decide() is replaced by a scripted stub.

    cd mcp && uv run --extra dev pytest -q
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from sales_tools import decide as decide_mod
from sales_tools import store
from sales_tools.tools import close, handoff, outreach, positioning, research, shared


class Script:
    """Answers decide() calls in order; a callable entry gets (question, evidence, options)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, question, evidence, options):
        self.calls.append(question)
        a = self.answers.pop(0) if self.answers else True
        if callable(a):
            a = a(question, evidence, options)
        return {"answer": a, "confidence": 0.9, "reason": "stub", "backend": "stub"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for obj in (store.LEADS, store.APPROVALS, store.PROPOSALS):
        obj.clear()
    for obj in (store.INTERACTIONS, store.QUEUE, outreach.SANDBOX_OUTBOX):
        obj.clear()
    store.REPS.clear()
    store.REPS.update(json.loads((store.DATA / "reps.demo.json").read_text()))
    store.BRIEF.clear()
    store.BRIEF.update(json.loads((store.DATA / "customer_brief.demo.json").read_text()))
    yield
    decide_mod.BACKEND = None


def stub(*answers):
    decide_mod.BACKEND = Script(*answers)
    return decide_mod.BACKEND


def lead_through_research(lead_id="L-001"):
    stub(True)
    assert research.add_lead(lead_id)["status"] == "added"
    research.research_target(lead_id)
    stub(True, True, True)
    assert research.check_ready_to_write(lead_id)["ready"] is True
    fact = store.LEADS[lead_id]["research"]["facts"][0]["fact"]
    assert positioning.save_positioning(lead_id, "A real angle for this lead", [fact], 0.7)["ok"]


def approved_draft(lead_id="L-001", subject="Subject line", body="Body of the email"):
    lead_through_research(lead_id)
    assert outreach.save_draft(lead_id, subject, body)["ok"]
    assert outreach.record_approval(lead_id, subject, body)["decision"] == "approve"


# --- 1. Find leads ---------------------------------------------------------------------------

def test_add_lead_fit_is_added_and_duplicates_skipped():
    cands = research.find_leads("C-demo")
    assert {c["lead_id"] for c in cands} == {"L-001", "L-002"}
    stub(True)
    assert research.add_lead("L-001")["status"] == "added"
    assert store.LEADS["L-001"]["stage"] == "new" and store.LEADS["L-001"]["status"] == "active"
    assert research.add_lead("L-001")["status"] == "duplicate"
    assert [c["lead_id"] for c in research.find_leads("C-demo")] == ["L-002"]


def test_add_lead_not_a_fit_is_recorded_with_a_loss_reason():
    stub(False)
    r = research.add_lead("L-002")
    assert r["status"] == "skipped"
    assert store.LEADS["L-002"]["loss_reason"] == "wrong_company_fit"
    assert store.INTERACTIONS[-1]["stage"] == "targeting" and store.INTERACTIONS[-1]["by"] == "tool"


def test_add_lead_without_a_decision_goes_to_a_human():
    stub(None)
    assert research.add_lead("L-001")["status"] == "needs_human"
    assert "L-001" not in store.LEADS


# --- 2. Research -----------------------------------------------------------------------------

def test_research_gate_retries_then_puts_the_lead_on_hold():
    stub(True)
    research.add_lead("L-001")
    research.research_target("L-001")
    for attempt in (1, 2):
        stub(True, False, True)
        r = research.check_ready_to_write("L-001")
        assert r["ready"] is False and r["attempts"] == attempt and r["missing"] == ["trigger"]
        research.research_target("L-001")  # retry keeps the attempt count
    stub(True, False, True)
    r = research.check_ready_to_write("L-001")
    assert r["status"] == "on_hold"
    assert store.LEADS["L-001"]["status"] == "on_hold" and store.LEADS["L-001"]["loss_reason"] is None


# --- 3 and 4. Write and the human check ------------------------------------------------------

def test_send_refuses_without_approval():
    lead_through_research()
    outreach.save_draft("L-001", "Subject line", "Body of the email")
    r = outreach.send_email("L-001")
    assert r["ok"] is False and r["error"] == "not_approved"


def test_approved_draft_sends_once_and_schedules_a_follow_up():
    approved_draft()
    r = outreach.send_email("L-001")
    assert r["ok"] and r["follow_up_due"]
    assert outreach.send_email("L-001")["error"] == "not_approved"  # one approval covers one send


def test_rep_edit_becomes_the_draft_and_is_logged():
    lead_through_research()
    outreach.save_draft("L-001", "Subject line", "Body of the email")
    r = outreach.record_approval("L-001", "Better subject", "Body of the email, shorter")
    assert r["decision"] == "edit"
    assert store.LEADS["L-001"]["outreach"]["draft_subject"] == "Better subject"
    ev = store.INTERACTIONS[-1]
    assert ev["stage"] == "approval" and ev["draft"]["subject"] == "Subject line" and ev["edited_to"]["subject"] == "Better subject"
    assert outreach.send_email("L-001")["ok"]


def test_rejected_draft_cannot_be_sent_and_a_new_draft_needs_new_approval():
    lead_through_research()
    outreach.save_draft("L-001", "Subject line", "Body of the email")
    outreach.record_approval("L-001", "Subject line", "Body of the email", decision="reject", note="too long")
    assert outreach.send_email("L-001")["error"] == "not_approved"
    outreach.save_draft("L-001", "Subject line", "Shorter body")
    assert outreach.send_email("L-001")["error"] == "not_approved"


# --- 5. Reply and hand-off ------------------------------------------------------------------

def sent_lead(lead_id="L-001"):
    approved_draft(lead_id)
    outreach.send_email(lead_id)


@pytest.mark.parametrize("label,why,status,reason", [
    ("negative", "price", "lost", "price"),
    ("negative", "has_competitor", "lost", "has_competitor"),
    ("wrong_person", None, "lost", "wrong_person"),
    ("unsubscribe", None, "lost", "negative"),
    ("not_now", None, "active", None),
    ("out_of_office", None, "active", None),
])
def test_reply_exits(label, why, status, reason):
    sent_lead()
    stub(label, why)
    r = outreach.read_reply("L-001", "reply text")
    lead = store.LEADS["L-001"]
    assert r["label"] == label and lead["status"] == status and lead["loss_reason"] == reason
    if status == "active":
        assert lead["outreach"]["follow_up_due"]
    if label == "unsubscribe":
        assert lead["do_not_contact"]


def test_positive_reply_goes_to_a_rep_when_auto_close_is_off():
    sent_lead()
    stub("positive")
    assert outreach.read_reply("L-001", "Yes, let's talk")["next"] == "assign_rep"


def test_assign_rep_prefers_green_then_yellow_then_queues():
    sent_lead()
    r = handoff.assign_rep("L-001")
    assert r["rep_id"] == "rep-1"  # the only green rep
    for rep in store.REPS.values():
        rep.update(status="red")
    sent_lead("L-002")
    r = handoff.assign_rep("L-002")
    assert r["status"] == "queued" and store.LEADS["L-002"]["status"] == "queued"
    assert store.LEADS["L-002"]["loss_reason"] is None
    store.REPS["rep-2"]["status"] = "yellow"
    released = handoff.release_queue()
    assert released[0]["rep_id"] == "rep-2" and handoff.get_wait_queue() == []


def test_free_slots_skip_weekends_and_booking_rejects_them():
    for s in handoff.find_free_slots("rep-1"):
        assert datetime.fromisoformat(s).weekday() < 5
    sent_lead()
    saturday = datetime(2026, 11, 28, 10, tzinfo=timezone.utc).isoformat()
    assert handoff.book_meeting("L-001", "rep-1", saturday)["ok"] is False


def test_auto_close_small_deal_paid_is_won():
    store.BRIEF["auto_close"]["enabled"] = True
    sent_lead()
    stub("positive")
    assert outreach.read_reply("L-001", "How do I sign up?")["next"] == "check_small_deal"
    stub(True)
    assert close.check_small_deal("L-001")["next"] == "send_offer"
    assert close.send_offer("L-001")["offer_link"].startswith("https://checkout.sandbox.test")
    close.record_payment("L-001", True)
    stub(True)
    assert close.check_deal_done("L-001")["done"] is True
    assert store.LEADS["L-001"]["status"] == "won"


def test_auto_close_off_never_sends_an_offer():
    sent_lead()
    assert close.check_small_deal("L-001")["next"] == "assign_rep"
    assert close.send_offer("L-001")["ok"] is False


# --- Follow-ups ------------------------------------------------------------------------------

def test_follow_up_due_then_no_reply_after_two():
    sent_lead()
    out = store.LEADS["L-001"]["outreach"]
    out["follow_up_due"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert outreach.due_follow_ups()[0]["follow_up"] == 1
    out.update(follow_ups=2, follow_up_due=None, sent_at=(datetime.now(timezone.utc) - timedelta(days=6)).isoformat())
    r = outreach.due_follow_ups()
    assert r[0]["loss_reason"] == "no_reply" and store.LEADS["L-001"]["status"] == "lost"


# --- Learning loop ---------------------------------------------------------------------------

def test_log_interaction_rejects_values_outside_the_schema():
    stub(True)
    research.add_lead("L-001")
    assert shared.log_interaction("L-001", "draft", "completed")["ok"] is False
    assert shared.log_interaction("L-001", "approval", "success", prompt_version="v1")["ok"]
    assert store.INTERACTIONS[-1]["by"] == "agent"


def test_event_log_and_proposals():
    lead_through_research()
    outreach.save_draft("L-001", "Subject line", "Body of the email")
    outreach.record_approval("L-001", "Edited subject", "Body of the email")
    edits = shared.get_event_log(stage="approval")
    assert len(edits) == 1 and edits[0]["edited_to"]
    p = shared.save_proposal("outreach", "old -> new", "3 of 5 drafts had the subject edited", "first-ask approval rate")
    assert p["proposal"]["status"] == "proposed"
    shared.decide_proposal(p["proposal"]["id"], True, by="rep-1")
    assert shared.get_changes("approved")[0]["decided_by"] == "rep-1"


# --- Plumbing --------------------------------------------------------------------------------

def test_decide_without_any_backend_returns_no_answer(monkeypatch):
    for k in ("LIQUID_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert decide_mod.decide("q?", "evidence")["answer"] is None


def test_d1_request_and_response_shape(monkeypatch):
    sent = {}

    class Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    def fake_urlopen(req, timeout):
        sent.update(json.loads(req.data))
        q = sent["questions"]["q"]
        ans = {"type": "noul", "noul": 0.2} if q["type"] == "noul" else {"type": "choice", "choice": "b", "confidence": 0.8}
        return Resp(json.dumps({"answers": {"q": ans}}).encode())

    monkeypatch.setenv("LIQUID_API_KEY", "liquid_test")
    monkeypatch.setattr(decide_mod.urllib.request, "urlopen", fake_urlopen)
    r = decide_mod.decide("Is it?", {"x": 1})
    assert sent["model"] == "d1.2" and sent["questions"]["q"]["type"] == "noul"
    assert r["answer"] is False and r["confidence"] == pytest.approx(0.8) and r["backend"] == "liquid:d1.2"
    r = decide_mod.decide("Which?", "text", {"a": "first", "b": "second"})
    assert sent["questions"]["q"]["criteria"] == {"a": "first", "b": "second"} and r["answer"] == "b"


def test_state_snapshot_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_FILE", str(tmp_path / "state.json"))
    stub(True)
    research.add_lead("L-001")
    store.save()
    store.LEADS.clear()
    store.load()
    assert "L-001" in store.LEADS
