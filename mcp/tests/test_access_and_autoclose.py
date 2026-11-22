"""Who may do what, and every auto-close path. Each test is a use case in docs/use-cases.md.

The demo team: rep-1 Mette (admin), rep-2 Jonas (rep), rep-3 Aino (junior), fin-1 Lena (finance).

    cd mcp && uv run --extra dev pytest -q
"""
from datetime import datetime, timedelta

from sales_tools import store
from sales_tools.tools import deals, handoff, outreach, shared

from test_pipeline import (owner, approved_draft, at, fresh, lead_through_research, meeting_held, sent_lead,  # noqa: F401
                           stub)


def drafted(lead_id="L-001"):
    lead_through_research(lead_id)
    d = outreach.save_draft(lead_id, "Subject line", "Body of the email")
    return {"to": d["to"], "cc": d["cc"]}


def denied(r, action=None):
    return r.get("error") == "not_allowed" and (action is None or r["action"] == action)


# --- Who may approve an email -----------------------------------------------------------------

def test_a_junior_or_viewer_cannot_approve_an_email_and_a_rep_can():
    rc = drafted()
    store.REPS["view-1"] = {"name": "Vera", "role": "viewer", "takes_leads": False}
    assert denied(outreach.record_approval("L-001", "Subject line", "Body of the email", by="rep-3", **rc), "approve_email")
    assert denied(outreach.record_approval("L-001", "Subject line", "Body of the email", by="Vera", **rc))
    assert outreach.send_email("L-001")["error"] == "not_approved"
    assert outreach.record_approval("L-001", "Subject line", "Body of the email", by="jonas@kvitto.sandbox.test", **rc)["ok"]
    assert outreach.send_email("L-001")["status"] == "sent"


def test_an_unknown_person_or_a_claimed_role_is_refused():
    rc = drafted()
    r = outreach.record_approval("L-001", "Subject line", "Body of the email", by="the CEO", **rc)
    assert r["error"] == "unknown_person"
    assert outreach.record_approval("L-001", "Subject line", "Body of the email", by="admin", **rc)["error"] == "unknown_person"


def test_per_person_allow_and_deny_override_the_role():
    rc = drafted()
    store.REPS["rep-3"]["allow"] = ["approve_email"]   # this junior may approve emails
    store.REPS["rep-2"]["deny"] = ["approve_email"]    # this rep may not
    assert denied(outreach.record_approval("L-001", "Subject line", "Body of the email", by="rep-2", **rc))
    assert outreach.record_approval("L-001", "Subject line", "Body of the email", by="rep-3", **rc)["ok"]
    store.REPS["rep-1"]["deny"] = ["approve_email"]    # deny wins, even for an admin
    outreach.save_draft("L-001", "Subject 2", "Body 2")
    assert denied(outreach.record_approval("L-001", "Subject 2", "Body 2", by="rep-1", **rc))


def test_a_refusal_is_logged_with_who_was_refused_and_who_can():
    rc = drafted()
    r = outreach.record_approval("L-001", "Subject line", "Body of the email", by="rep-3", **rc)
    assert "rep-1" in r["who_can"] and "rep-2" in r["who_can"] and "rep-3" not in r["who_can"]
    ev = store.INTERACTIONS[-1]
    assert ev["outcome"] == "failure" and ev["by"] == "rep-3" and ev["message"].startswith("denied")


# --- Deals --------------------------------------------------------------------------------------

def test_a_discount_above_the_limit_needs_an_admin():
    meeting_held()  # owned by rep-1 (Mette, admin)
    store.LEADS["L-001"]["deal"]["owner"] = "rep-2"
    deals.draft_quote("L-001", "annual", discount_pct=15)
    assert denied(deals.approve_quote("L-001", by="rep-2"), "approve_discount")  # the owner, but only a rep
    assert deals.approve_quote("L-001", by="Mette")["ok"]


def test_a_rep_cannot_approve_or_close_someone_elses_deal():
    meeting_held()  # owned by rep-1
    deals.draft_quote("L-001", "annual")
    assert denied(deals.approve_quote("L-001", by="rep-2"), "approve_quote")
    assert deals.approve_quote("L-001", by="rep-1")["ok"]
    deals.send_quote("L-001")
    assert denied(deals.record_contract("L-001", "signed", by="rep-2"))
    assert denied(deals.record_deal_outcome("L-001", "won", by="rep-2"))
    assert deals.record_deal_outcome("L-001", "won", by="rep-1")["ok"]


def test_only_finance_or_an_admin_records_a_payment():
    store.BRIEF["auto_close"]["enabled"] = True
    small_deal_offer()
    assert deals.record_payment("L-001", True, by="")["error"] == "unknown_person"  # nobody named: refused
    assert denied(deals.record_payment("L-001", True, by="rep-2"), "record_payment")
    assert deals.record_payment("L-001", True, by="Lena")["ok"]
    assert store.INTERACTIONS[-1]["by"] == "fin-1"


def test_only_an_admin_decides_a_rule_change():
    p = shared.save_proposal("outreach", "Shorter first emails", "rep edits", "2 of 3 edits cut the length")
    assert denied(shared.decide_proposal(p["proposal"]["id"], True, by="rep-2"), "decide_proposal")
    assert shared.decide_proposal(p["proposal"]["id"], True, by="rep-1")["ok"]


# --- Meetings and the team --------------------------------------------------------------------

def booked_with(rep_id="rep-2", lead_id="L-001"):
    sent_lead(lead_id)
    stub("positive")
    outreach.read_reply(lead_id, "Yes")
    store.LEADS[lead_id].setdefault("handoff", {}).update(rep_id=rep_id, assigned_at=store.now())
    slot = handoff.find_free_slots(rep_id, lead_id=lead_id)[0]
    assert handoff.book_meeting(lead_id, rep_id, slot)["ok"]
    return slot


def test_a_rep_cannot_move_or_cancel_a_colleagues_meeting_but_an_admin_can():
    booked_with("rep-2")
    later = handoff.find_free_slots("rep-2", lead_id="L-001")[2]
    assert denied(handoff.reschedule_meeting("L-001", later, by="rep-3"), "change_meeting")
    assert denied(handoff.cancel_meeting("L-001", by="rep-3"))
    assert handoff.reschedule_meeting("L-001", later, by="rep-1")["ok"]          # admin
    assert handoff.cancel_meeting("L-001", by="rep-2")["ok"]                     # the owner


def test_a_junior_runs_their_own_meetings():
    slot = booked_with("rep-3")
    assert handoff.reschedule_meeting("L-001", handoff.find_free_slots("rep-3", lead_id="L-001")[1], by="rep-3")["ok"]
    at((datetime.fromisoformat(store.LEADS["L-001"]["handoff"]["meeting_start"]) + timedelta(hours=1)).isoformat())
    assert handoff.record_meeting_outcome("L-001", "held", by="rep-3")["ok"] and slot


def test_status_is_your_own_unless_you_are_an_admin():
    assert handoff.set_rep_status("rep-2", "red", by="rep-2")["ok"]
    assert denied(handoff.set_rep_status("rep-3", "away", by="rep-2"), "manage_team")
    assert handoff.set_rep_status("rep-3", "away", by="rep-1")["ok"]


def test_a_finance_person_never_gets_a_lead():
    sent_lead()
    for rid in ("rep-1", "rep-2", "rep-3"):
        store.REPS[rid]["status"] = "red"
    assert handoff.assign_rep("L-001")["status"] == "queued"  # Lena is green but doesn't take leads


# --- Auto-close ---------------------------------------------------------------------------------

def small_deal_offer(lead_id="L-001", reply="How do I sign up for the monthly plan?"):
    store.BRIEF["auto_close"]["enabled"] = True
    sent_lead(lead_id)
    stub("positive")
    assert outreach.read_reply(lead_id, reply)["next"] == "check_small_deal"
    stub(True)
    assert deals.check_small_deal(lead_id, estimated_value_eur=790 * 12 / 12 * 2)["next"] == "send_offer"
    return deals.send_offer(lead_id)


def test_auto_close_a_request_for_custom_terms_goes_to_a_rep():
    store.BRIEF["auto_close"]["enabled"] = True
    sent_lead()
    stub("positive")
    outreach.read_reply("L-001", "Interested, but we'd need 20% off and our own contract.")
    stub(False)
    r = deals.check_small_deal("L-001", estimated_value_eur=1580)
    assert r["next"] == "assign_rep" and "deal" not in store.LEADS["L-001"]


def test_auto_close_without_a_decision_waits_for_a_person():
    store.BRIEF["auto_close"]["enabled"] = True
    sent_lead()
    stub("positive")
    outreach.read_reply("L-001", "Sounds good")
    stub(None)
    r = deals.check_small_deal("L-001", estimated_value_eur=1580)
    assert r["small_deal"] is None and "deal" not in store.LEADS["L-001"]
    assert [d["kind"] for d in outreach.due_follow_ups() if d["lead_id"] == "L-001"] == ["needs_person"]


def test_auto_close_offer_goes_in_the_thread_once():
    r = small_deal_offer()
    m = store.OUTBOX[-1]
    assert r["email"]["status"] == "sent" and m["kind"] == "offer" and m["subject"] == "Re: Subject line"
    assert r["offer_link"] in m["body"] and store.LEADS["L-001"]["stage"] == "offer_sent"
    assert deals.send_offer("L-001")["error"] == "offer_already_sent"


def test_auto_close_offer_at_night_waits_for_the_morning():
    store.BRIEF["auto_close"]["enabled"] = True
    sent_lead()
    stub("positive")
    outreach.read_reply("L-001", "How do I sign up?")
    stub(True)
    deals.check_small_deal("L-001", estimated_value_eur=1580)
    at("2026-11-24T23:15:00+01:00")
    r = deals.send_offer("L-001")
    assert r["email"]["status"] == "scheduled" and r["email"]["scheduled_for"] == "2026-11-25T07:00:00+00:00"
    at("2026-11-25T08:05:00+01:00")
    outreach.get_replies()
    assert store.OUTBOX[-1]["status"] == "sent" and store.LEADS["L-001"]["deal"]["offer_sent_at"] == "2026-11-25T07:00:00+00:00"


def test_auto_close_unpaid_offer_gets_two_reminders_then_goes_to_a_rep():
    small_deal_offer()  # Tuesday
    kinds = lambda: [d["kind"] for d in outreach.due_follow_ups() if d["lead_id"] == "L-001"]  # noqa: E731
    assert kinds() == []
    assert deals.check_deal_done("L-001")["next"].startswith("not paid yet")
    at("2026-11-26T10:00:00+01:00")  # Thursday: 2 working days
    assert kinds() == ["offer_unpaid"]
    assert deals.send_offer_reminder("L-001")["status"] == "sent"
    assert kinds() == []
    at("2026-11-30T10:00:00+01:00")  # Monday
    assert kinds() == ["offer_unpaid"]
    deals.send_offer_reminder("L-001")
    assert deals.send_offer_reminder("L-001")["error"] == "no reminders left"
    at("2026-12-03T10:00:00+01:00")  # 7 working days after the offer
    assert kinds() == ["offer_expired"]
    assert deals.check_deal_done("L-001")["next"] == "assign_rep"


def test_auto_close_failed_payment_sends_the_link_again():
    small_deal_offer()
    r = deals.record_payment("L-001", False, by="fin-1", status="failed")
    assert r["payment_status"] == "failed" and r["retry_due"]
    assert not [d for d in outreach.due_follow_ups() if d["kind"] == "payment_failed"]
    at("2026-11-25T10:00:00+01:00")
    assert [d["kind"] for d in outreach.due_follow_ups() if d["lead_id"] == "L-001"] == ["payment_failed"]
    assert deals.send_offer_reminder("L-001")["status"] == "sent"
    assert "didn't go through" in store.OUTBOX[-1]["body"]
    assert store.LEADS["L-001"]["deal"]["payment_status"] == "unpaid"
    deals.record_payment("L-001", True, by="fin-1")
    assert deals.check_deal_done("L-001")["done"] is True


def test_auto_close_partial_payment_waits_for_a_person_until_the_rest_is_paid():
    small_deal_offer()
    total = store.LEADS["L-001"]["deal"]["quote"]["total_eur"]
    r = deals.record_payment("L-001", True, by="fin-1", amount_eur=total / 2)
    assert r["payment_status"] == "partial"
    assert deals.check_deal_done("L-001")["done"] is False and store.LEADS["L-001"]["status"] == "active"
    assert [d["kind"] for d in outreach.due_follow_ups() if d["lead_id"] == "L-001"] == ["needs_person"]
    assert deals.record_payment("L-001", True, by="fin-1")["payment_status"] == "paid"  # the rest
    assert store.LEADS["L-001"]["deal"]["amount_eur"] == total and deals.check_deal_done("L-001")["done"] is True


def test_auto_close_reminders_cannot_be_sent_early_and_a_held_offer_is_not_expired():
    store.BRIEF["auto_close"]["enabled"] = True
    sent_lead()
    stub("positive")
    outreach.read_reply("L-001", "How do I sign up?")
    stub(True)
    deals.check_small_deal("L-001", estimated_value_eur=1580)
    at("2026-11-24T22:00:00+01:00")
    deals.send_offer("L-001")  # held until the morning
    assert deals.check_deal_done("L-001")["next"].startswith("the offer is scheduled")
    assert deals.send_offer_reminder("L-001")["ok"] is False
    at("2026-11-25T09:00:00+01:00")
    assert deals.send_offer_reminder("L-001")["error"] == "too_early"
    at("2026-11-27T09:00:00+01:00")  # 2 working days after it went out
    assert deals.send_offer_reminder("L-001")["status"] == "sent"
    assert deals.send_offer_reminder("L-001")["error"] == "too_early"


def test_auto_close_a_second_payment_is_refused():
    small_deal_offer()
    deals.record_payment("L-001", True, by="fin-1")
    assert deals.record_payment("L-001", True, by="fin-1")["error"] == "already_paid"


def test_auto_close_refund_after_a_win_comes_off_revenue_and_waits_for_a_person():
    small_deal_offer()
    deals.record_payment("L-001", True, by="fin-1")
    deals.check_deal_done("L-001")
    assert shared.get_funnel_metrics()["won_revenue_eur"] > 0
    assert deals.record_payment("L-001", False, by="fin-1", status="refunded")["payment_status"] == "refunded"
    assert shared.get_funnel_metrics()["won_revenue_eur"] == 0
    assert store.LEADS["L-001"]["needs_person"]["reason"].startswith("the customer was refunded")
    assert deals.check_deal_done("L-001")["already"] == "closed_won"


def test_auto_close_no_reminder_after_an_unsubscribe():
    small_deal_offer()
    store.LEADS["L-001"]["do_not_contact"] = True
    at("2026-11-26T10:00:00+01:00")
    assert deals.send_offer_reminder("L-001")["error"] == "do_not_contact"


def test_auto_close_cannot_skip_the_gate():
    store.BRIEF["auto_close"]["enabled"] = True
    sent_lead()
    assert deals.send_offer("L-001")["ok"] is False
    assert deals.send_offer_reminder("L-001")["ok"] is False
