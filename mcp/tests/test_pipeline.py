"""Unit tests for the pipeline gates. No model calls: decide() is replaced by a scripted stub.

    cd mcp && uv run --extra dev pytest -q
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from sales_tools import decide as decide_mod
from sales_tools import store
from sales_tools.tools import deals, handoff, outreach, positioning, research, shared


class Script:
    """Answers decide() calls in order; a callable entry gets (question, evidence, options)."""

    def __init__(self, *answers):
        self.answers, self.calls, self.evidence = list(answers), [], []

    def __call__(self, question, evidence, options):
        self.calls.append(question)
        self.evidence.append(evidence)
        a = self.answers.pop(0) if self.answers else True
        if callable(a):
            a = a(question, evidence, options)
        return {"answer": a, "confidence": 0.9, "reason": "stub", "backend": "stub"}


IN_HOURS = "2026-11-24T10:00:00+01:00"


def at(iso: str) -> None:
    """Move the pinned clock."""
    store.CLOCK["now"] = iso


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for obj in (store.LEADS, store.APPROVALS, store.PROPOSALS):
        obj.clear()
    for obj in (store.INTERACTIONS, store.QUEUE, store.OUTBOX, store.INBOX):
        obj.clear()
    store.CALENDARS.clear()
    store.CLOCK.clear()
    store.CLOCK["now"] = IN_HOURS  # a Tuesday morning in Stockholm: inside business hours
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


def rcpt(lead_id="L-001") -> dict:
    """The draft's recipients, as the agent passes them to record_approval."""
    out = store.LEADS[lead_id]["outreach"]
    return {"to": out["draft_to"], "cc": out["draft_cc"]}


def approved_draft(lead_id="L-001", subject="Subject line", body="Body of the email"):
    lead_through_research(lead_id)
    assert outreach.save_draft(lead_id, subject, body)["ok"]
    assert outreach.record_approval(lead_id, subject, body, **rcpt(lead_id))["decision"] == "approve"


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
    r = outreach.record_approval("L-001", "Better subject", "Body of the email, shorter", **rcpt())
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
    assert deals.check_small_deal("L-001", estimated_value_eur=1500)["next"] == "send_offer"
    assert deals.send_offer("L-001")["offer_link"].startswith("https://checkout.sandbox.test")
    deals.record_payment("L-001", True, by="fin-1")
    stub(True)
    assert deals.check_deal_done("L-001")["done"] is True
    assert store.LEADS["L-001"]["status"] == "won"


def test_auto_close_off_never_sends_an_offer():
    sent_lead()
    assert deals.check_small_deal("L-001")["next"] == "assign_rep"
    assert deals.send_offer("L-001")["ok"] is False


# --- Follow-ups ------------------------------------------------------------------------------

def test_follow_up_due_then_no_reply_after_two():
    sent_lead()
    out = store.LEADS["L-001"]["outreach"]
    out["follow_up_due"] = (store.clock() - timedelta(minutes=1)).isoformat()
    assert outreach.due_follow_ups()[0]["follow_up"] == 1
    out.update(follow_ups=2, follow_up_due=None, sent_at=(store.clock() - timedelta(days=8)).isoformat())
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
    outreach.record_approval("L-001", "Edited subject", "Body of the email", **rcpt())
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


# --- Guards added after review ---------------------------------------------------------------

def test_update_lead_cannot_fake_an_approval():
    lead_through_research()
    outreach.save_draft("L-001", "Subject line", "Body of the email")
    h = outreach._draft_hash("Subject line", "Body of the email")
    r = shared.update_lead("L-001", {"stage": "approved", "outreach": {"approval": {"decision": "approve", "draft_hash": h}}})
    assert r["ok"] is False
    assert outreach.send_email("L-001")["error"] == "not_approved"
    assert shared.update_lead("L-001", {"status": "insufficient_data"})["ok"]


def test_a_reply_is_handled_once_and_a_rep_assigned_once():
    sent_lead()
    outreach.get_replies()  # the stub inbox gives every sent email a positive reply
    assert len(outreach.get_replies()) == 1
    stub("positive")
    assert outreach.read_reply("L-001", "Sounds interesting")["label"] == "positive"
    assert outreach.get_replies() == []
    assert outreach.read_reply("L-001", "Sounds interesting")["already_handled"] is True
    before = store.REPS["rep-1"]["active_leads"]
    handoff.assign_rep("L-001")
    assert handoff.assign_rep("L-001")["already_assigned"] is True
    assert store.REPS["rep-1"]["active_leads"] == before + 1


def test_read_reply_refuses_a_lead_that_was_never_emailed():
    lead_through_research()
    assert outreach.read_reply("L-001", "hi")["ok"] is False


# --- Timestamps, meeting outcome, deal value -------------------------------------------------

def test_stage_times_and_created_at_are_recorded():
    sent_lead()
    lead = store.LEADS["L-001"]
    assert lead["created_at"] and lead["updated_at"]
    assert list(lead["stage_times"]) == ["new", "researched", "positioned", "drafted", "approved", "sent"]
    first = lead["stage_times"]["drafted"]
    outreach.save_draft("L-001", "Follow-up", "Short follow-up body")  # back to drafted later
    assert lead["stage_times"]["drafted"] == first  # keeps the first time it got there


def test_meeting_outcome_counts_toward_the_north_star():
    sent_lead()
    stub("positive")
    outreach.read_reply("L-001", "Yes, let's talk")
    handoff.assign_rep("L-001")
    assert handoff.record_meeting_outcome("L-001", "held", by=owner("L-001"))["ok"] is False  # nothing booked yet
    handoff.book_meeting("L-001", "rep-1", handoff.find_free_slots("rep-1")[0])
    assert handoff.record_meeting_outcome("L-001", "maybe", by=owner("L-001"))["ok"] is False
    assert handoff.record_meeting_outcome("L-001", "held", by=owner("L-001"))["error"].startswith("the meeting hasn't started")
    after_meeting()
    assert handoff.record_meeting_outcome("L-001", "held", by=owner("L-001"))["ok"]
    m = shared.get_funnel_metrics()
    assert m["north_star_meetings_held"] == 1
    assert m["median_hours_reply_to_handoff"] is not None and m["median_hours_reply_to_handoff"] >= 0


def test_deal_over_the_price_cap_goes_to_a_rep_without_asking_d1():
    store.BRIEF["auto_close"]["enabled"] = True
    sent_lead()
    script = stub()
    r = deals.check_small_deal("L-001", estimated_value_eur=9480)
    assert r["small_deal"] is False and r["next"] == "assign_rep" and script.calls == []
    assert "deal" not in store.LEADS["L-001"]  # no deal opens for a deal that goes to a rep


def test_payment_amount_is_kept():
    store.BRIEF["auto_close"]["enabled"] = True
    sent_lead()
    stub(True)
    deals.check_small_deal("L-001", estimated_value_eur=1500)
    deals.send_offer("L-001")
    deals.record_payment("L-001", True, by="fin-1", amount_eur=1500)
    stub(True)
    deals.check_deal_done("L-001")
    lead = store.LEADS["L-001"]
    assert lead["deal"]["amount_eur"] == 1500 and lead["deal"]["stage"] == "closed_won" and lead["deal"]["auto_close"]
    assert "closed" in lead["stage_times"] and lead["status"] == "won"


# --- 6. Deal closing ---------------------------------------------------------------------------

def owner(lead_id: str) -> str:
    """The rep who owns the lead's meeting: the person passed as by for meeting changes."""
    return (store.LEADS[lead_id].get("handoff") or {}).get("rep_id") or "rep-1"


def after_meeting(lead_id="L-001"):
    """Move the clock to an hour after the lead's booked meeting starts."""
    start = datetime.fromisoformat(store.LEADS[lead_id]["handoff"]["meeting_start"])
    at((start + timedelta(hours=1)).isoformat())


def meeting_held(lead_id="L-001"):
    sent_lead(lead_id)
    stub("positive")
    outreach.read_reply(lead_id, "Yes, let's talk")
    handoff.assign_rep(lead_id)
    handoff.book_meeting(lead_id, store.LEADS[lead_id]["handoff"]["rep_id"], handoff.find_free_slots("rep-1")[0])
    after_meeting(lead_id)
    r = handoff.record_meeting_outcome(lead_id, "held", by=owner(lead_id))
    assert r["deal"] == "qualification"
    return store.LEADS[lead_id]["deal"]


def test_held_meeting_opens_a_deal_owned_by_the_rep():
    deal = meeting_held()
    assert deal["stage"] == "qualification" and deal["owner"] == "rep-1" and deal["probability"] == 20
    assert deal["auto_close"] is False


def test_no_deal_before_a_meeting_is_held():
    sent_lead()
    assert deals.draft_quote("L-001", "annual")["ok"] is False
    assert deals.record_call_notes("L-001", "notes")["ok"] is False


def test_bant_comes_from_call_notes_and_unknown_does_not_block():
    meeting_held()
    assert deals.qualify_deal("L-001")["ok"] is False  # no notes yet
    deals.record_call_notes("L-001", "CEO decides. Needs ISO 27001 by Q2. Budget not discussed.", by="rep-1")
    stub("unknown", "yes", "yes", "yes")
    r = deals.qualify_deal("L-001")
    assert r["failing"] == [] and r["unknown"] == ["budget"] and r["next"] == "draft_quote"
    stub("no", "yes", "yes", "no")
    assert deals.qualify_deal("L-001")["failing"] == ["budget", "timeline"]


def test_quote_totals_are_computed_in_code_and_policy_is_enforced():
    meeting_held()
    assert deals.draft_quote("L-001", "enterprise")["ok"] is False          # not in the price list
    assert deals.draft_quote("L-001", "annual", term_months=6)["ok"] is False  # under the minimum term
    assert deals.draft_quote("L-001", "annual", discount_pct=25)["ok"] is False  # above the maximum discount
    r = deals.draft_quote("L-001", "annual", discount_pct=5)
    q = r["quote"]
    assert q["list_total_eur"] == 650 * 12 and q["total_eur"] == 7410.0 and q["needs_admin"] is False
    assert store.LEADS["L-001"]["deal"]["stage"] == "quote"
    assert deals.draft_quote("L-001", "annual", discount_pct=15)["quote"]["needs_admin"] is True


def test_quote_cannot_be_sent_without_approval_or_after_a_change():
    meeting_held()
    deals.draft_quote("L-001", "annual")
    assert deals.send_quote("L-001")["error"] == "not_approved"
    deals.approve_quote("L-001", by="rep-1")
    store.LEADS["L-001"]["deal"]["quote"]["total_eur"] = 1.0  # changed after approval
    assert deals.send_quote("L-001")["error"] == "not_approved"
    deals.draft_quote("L-001", "annual")
    deals.approve_quote("L-001", by="rep-1")
    r = deals.send_quote("L-001")
    assert r["ok"] and store.LEADS["L-001"]["deal"]["stage"] == "negotiation"


def test_won_deal_is_recorded_once_on_the_lead():
    meeting_held()
    deals.draft_quote("L-001", "annual")
    deals.approve_quote("L-001", by="rep-1")
    deals.send_quote("L-001")
    deals.record_contract("L-001", "signed", by="rep-1")
    r = deals.record_deal_outcome("L-001", "won", by="rep-1")
    lead = store.LEADS["L-001"]
    assert r["amount_eur"] == 7800 and lead["status"] == "won" and lead["deal"]["stage"] == "closed_won"
    assert deals.record_deal_outcome("L-001", "lost", by="rep-1", loss_reason="price")["ok"] is False
    m = shared.get_funnel_metrics()
    assert m["deals_won"] == 1 and m["won_revenue_eur"] == 7800 and m["closed_automatically"] == 0
    assert m["median_days_meeting_to_won"] is not None


def test_lost_deal_needs_a_reason_from_the_list():
    meeting_held()
    assert deals.record_deal_outcome("L-001", "lost", by="rep-1", loss_reason="went quiet")["ok"] is False
    deals.record_deal_outcome("L-001", "lost", by="rep-1", loss_reason="has_competitor")
    lead = store.LEADS["L-001"]
    assert lead["status"] == "lost" and lead["loss_reason"] == "has_competitor"
    assert shared.get_funnel_metrics()["losses_by_reason"] == {"has_competitor": 1}


def test_pipeline_weights_open_deals_and_flags_quiet_ones():
    meeting_held()
    deals.draft_quote("L-001", "annual")
    p = deals.get_pipeline()
    assert p["by_stage"]["quote"]["count"] == 1 and p["weighted_pipeline_eur"] == round(7800 * 0.4, 2)
    store.LEADS["L-001"]["updated_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    assert deals.get_pipeline("rep-1")["stale_deals"][0]["lead_id"] == "L-001"
    assert deals.get_pipeline("rep-2")["by_stage"]["quote"]["count"] == 0


def test_funnel_metrics_with_a_deal_that_never_set_auto_close():
    sent_lead()
    store.LEADS["L-001"]["deal"] = {"stage": "closed_won", "auto_close": None, "amount_eur": 7800}
    m = shared.get_funnel_metrics()
    assert m["closed_automatically"] == 0 and m["deals_won"] == 1


def test_free_slots_follow_the_clock_and_reach_the_day_asked_for():
    at("2026-11-24T10:00:00+01:00")  # Tuesday
    slots = handoff.find_free_slots("rep-1")  # Mette, Copenhagen, 09:00-17:00: a morning and an afternoon slot a day
    assert slots[:2] == ["2026-11-26T08:00:00+00:00", "2026-11-26T11:00:00+00:00"]  # 09:00 and 12:00 Copenhagen
    assert {s[:10] for s in slots} == {"2026-11-26", "2026-11-27", "2026-11-30", "2026-12-01"}
    assert handoff.find_free_slots("rep-1", days_ahead=7)[-1][:10] == "2026-12-03"
