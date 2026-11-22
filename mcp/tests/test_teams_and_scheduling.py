"""Sales teams from 1 to 10 reps, rep calendars (book, reschedule, cancel, no-show) and follow-ups.

Each test is a use case in docs/use-cases.md. The clock is pinned (Tuesday 2026-11-24 10:00 Stockholm)
and d1 is a scripted stub, as in test_pipeline.py.

    cd mcp && uv run --extra dev pytest -q
"""
from datetime import datetime, timedelta

import pytest

from sales_tools import store
from sales_tools.tools import deals, handoff, outreach, shared

from test_pipeline import at, fresh, sent_lead, stub  # noqa: F401 (fresh is autouse)

TZ = {"SE": "Europe/Stockholm", "NO": "Europe/Oslo", "FI": "Europe/Helsinki", "DK": "Europe/Copenhagen", "DE": "Europe/Berlin"}


def team(n: int, countries=None, load=0, max_leads=8, hours=("09:00", "17:00")) -> list[str]:
    """A team of n reps. Countries cycle SE, NO, FI, DK, DE unless given; the traffic light follows the load."""
    store.REPS.clear()
    countries = countries or [list(TZ)[i % len(TZ)] for i in range(n)]
    for i in range(1, n + 1):
        c = countries[i - 1]
        rep = {"name": f"Rep {i}", "email": f"rep{i}@kvitto.sandbox.test", "timezone": TZ[c], "countries": [c],
               "active_leads": load, "max_leads": max_leads, "working_hours": list(hours)}
        rep["status"] = handoff._traffic_light(rep)
        store.REPS[f"rep-{i}"] = rep
    return list(store.REPS)


def positive_lead(i: int, country: str = "SE") -> str:
    """A lead whose positive reply was just read: ready for assign_rep."""
    lid = f"T-{i:03d}"
    store.LEADS[lid] = {"lead_id": lid, "customer_id": "C-demo", "stage": "replied", "status": "active",
                        "person": {"name": f"Person {i}", "title": "CEO", "email": f"p{i}@co{i}.sandbox.test"},
                        "company": {"name": f"Company {i}", "domain": f"co{i}.example", "country": country,
                                    "employees": 20, "industry": "B2B SaaS"},
                        "outreach": {"reply_label": "positive"}, "stage_times": {"replied": store.now()}}
    return lid


def booked(lid: str, rep_id: str = "rep-1") -> dict:
    handoff.assign_rep(lid)
    slot = handoff.find_free_slots(store.LEADS[lid]["handoff"]["rep_id"], lead_id=lid)[0]
    r = handoff.book_meeting(lid, store.LEADS[lid]["handoff"]["rep_id"], slot)
    assert r["ok"], r
    return r


def kinds(lid: str) -> list[str]:
    return [d["kind"] for d in outreach.due_follow_ups() if d["lead_id"] == lid]


def busy(rep_id: str, start: str, end: str, title="Busy") -> None:
    store.CALENDARS.setdefault(rep_id, []).append({"event_id": f"B-{len(store.CALENDARS[rep_id])}", "rep_id": rep_id,
                                                   "kind": "busy", "status": "booked", "title": title,
                                                   "start": datetime.fromisoformat(start).isoformat(),
                                                   "end": datetime.fromisoformat(end).isoformat(), "history": []})


# --- Team sizes --------------------------------------------------------------------------------

def test_solo_rep_takes_leads_until_full_then_the_queue_waits_and_is_flagged():
    team(1, load=0, max_leads=3)
    for i in range(4):
        positive_lead(i)
    r = [handoff.assign_rep(f"T-{i:03d}") for i in range(4)]
    assert [x.get("rep_id") for x in r] == ["rep-1", "rep-1", "rep-1", None] and r[3]["status"] == "queued"
    assert store.REPS["rep-1"]["status"] == "red" and store.REPS["rep-1"]["active_leads"] == 3
    assert kinds("T-003") == []
    at("2026-11-25T10:00:00+01:00")  # a working day later
    assert "queue_waiting" in kinds("T-003")
    store.REPS["rep-1"].update(active_leads=2, status="yellow")
    assert handoff.release_queue()[0]["rep_id"] == "rep-1" and handoff.get_wait_queue() == []


def test_solo_rep_with_a_full_week_gets_slots_the_week_after():
    team(1)
    for d in ("2026-11-26", "2026-11-27", "2026-11-30", "2026-12-01"):
        busy("rep-1", f"{d}T09:00:00+01:00", f"{d}T17:00:00+01:00", "Customer workshop")
    assert handoff.find_free_slots("rep-1") == []
    later = handoff.find_free_slots("rep-1", days_ahead=10)
    assert later and later[0][:10] == "2026-12-02"


def test_two_reps_country_comes_before_load():
    team(2, countries=["SE", "NO"])
    store.REPS["rep-1"].update(active_leads=4)  # still green
    store.REPS["rep-1"]["status"] = handoff._traffic_light(store.REPS["rep-1"])
    assert handoff.assign_rep(positive_lead(1, "SE"))["rep_id"] == "rep-1"
    assert handoff.assign_rep(positive_lead(2, "NO"))["rep_id"] == "rep-2"


def test_three_reps_an_away_rep_gets_no_leads_and_no_slots():
    team(3, countries=["SE", "SE", "NO"])
    assert handoff.set_rep_status("rep-1", "away")["ok"]
    for i in range(4):
        assert handoff.assign_rep(positive_lead(i, "SE"))["rep_id"] in ("rep-2", "rep-3")
    assert handoff.find_free_slots("rep-1") == []


def test_five_reps_slots_follow_each_reps_time_zone_and_the_prospects_hours():
    team(5, hours=("08:00", "16:00"))  # SE, NO, FI, DK, DE
    fi_rep = "rep-3"  # Helsinki
    lid = positive_lead(1, "DE")  # a Berlin prospect: 08:00 Helsinki is 07:00 in Berlin
    slots = handoff.find_free_slots(fi_rep, lead_id=lid)
    first = datetime.fromisoformat(slots[0])
    assert first.astimezone(handoff.ZoneInfo("Europe/Helsinki")).hour == 9  # the first hour both sides are working
    assert first.astimezone(handoff.ZoneInfo("Europe/Berlin")).hour == 8
    late = datetime.fromisoformat("2026-11-26T15:30:00+02:00")  # 15:30 Helsinki, 14:30 Berlin: fine
    assert handoff._slot_problem(fi_rep, store.LEADS[lid], late, 30) is None
    early = datetime.fromisoformat("2026-11-26T08:00:00+02:00")
    assert "prospect" in handoff._slot_problem(fi_rep, store.LEADS[lid], early, 30)


def test_ten_reps_share_leads_evenly_and_never_go_over_capacity():
    team(10)  # two reps per country
    n = 0
    for c in TZ:
        for _ in range(7):
            handoff.assign_rep(positive_lead(n, c))
            n += 1
    for c in TZ:
        loads = [r["active_leads"] for r in store.REPS.values() if r["countries"] == [c]]
        assert max(loads) - min(loads) <= 1, (c, loads)
    for _ in range(60):  # far more leads than the team can take
        handoff.assign_rep(positive_lead(n, list(TZ)[n % 5]))
        n += 1
    assert all(r["active_leads"] <= r["max_leads"] for r in store.REPS.values())
    assert sum(r["active_leads"] for r in store.REPS.values()) == 80 and len(handoff.get_wait_queue()) == n - 80


@pytest.mark.parametrize("size", [1, 2, 3, 5, 10])
def test_every_team_size_books_without_double_booking(size):
    team(size)
    for i in range(size * 3):
        lid = positive_lead(i, list(TZ)[i % 5])
        r = handoff.assign_rep(lid)
        if r.get("rep_id"):
            slot = handoff.find_free_slots(r["rep_id"], lead_id=lid, days_ahead=10)[0]
            assert handoff.book_meeting(lid, r["rep_id"], slot)["ok"]
    for rid, evs in store.CALENDARS.items():
        live = sorted((e for e in evs if e["status"] != "cancelled"), key=lambda e: e["start"])
        assert all(b["start"] >= a["end"] for a, b in zip(live, live[1:])), rid


# --- Booking against a calendar ----------------------------------------------------------------

def test_slots_skip_busy_time_and_booked_meetings():
    team(1)
    busy("rep-1", "2026-11-26T09:00:00+01:00", "2026-11-26T11:00:00+01:00", "Pipeline review")
    first = handoff.find_free_slots("rep-1")[0]
    assert first == "2026-11-26T10:00:00+00:00"  # 11:00 Stockholm, right after the busy block
    a = positive_lead(1)
    handoff.assign_rep(a)
    assert handoff.book_meeting(a, "rep-1", first)["ok"]
    assert handoff.find_free_slots("rep-1")[0] == "2026-11-26T10:30:00+00:00"


def test_booking_refuses_a_clash_a_past_time_after_hours_and_a_second_meeting():
    team(1)
    a, b = positive_lead(1), positive_lead(2)
    handoff.assign_rep(a), handoff.assign_rep(b)
    slot = handoff.find_free_slots("rep-1", lead_id=a)[0]
    assert handoff.book_meeting(a, "rep-1", slot)["ok"]
    assert "busy" in handoff.book_meeting(b, "rep-1", slot)["error"]  # no double booking
    assert handoff.book_meeting(b, "rep-1", "2026-11-20T10:00:00+00:00")["error"] == "that time has already passed"
    assert "working hours" in handoff.book_meeting(b, "rep-1", "2026-11-26T18:00:00+01:00")["error"]
    assert handoff.book_meeting(a, "rep-1", handoff.find_free_slots("rep-1")[1])["error"] == "already_booked"


def test_meeting_invite_includes_the_people_in_the_thread():
    team(1)
    lid = positive_lead(1)
    store.LEADS[lid]["contacts"] = [{"email": "cfo@co1.sandbox.test", "role": "cc"}, {"email": "x@co1.sandbox.test", "role": "referral"}]
    r = booked(lid)
    assert r["attendees"] == ["rep1@kvitto.sandbox.test", "p1@co1.sandbox.test", "cfo@co1.sandbox.test"]


# --- Rescheduling ------------------------------------------------------------------------------

def test_reschedule_moves_the_meeting_and_frees_the_old_slot():
    team(1)
    a, b = positive_lead(1), positive_lead(2)
    old = booked(a)["start"]
    new = handoff.find_free_slots("rep-1", lead_id=a)[2]
    r = handoff.reschedule_meeting(a, new, "prospect", "board meeting moved")
    assert r["ok"] and r["start"] == new and store.LEADS[a]["handoff"]["meeting_start"] == new
    handoff.assign_rep(b)
    assert handoff.book_meeting(b, "rep-1", old)["ok"]  # the freed slot can be booked again
    ev = [e for e in store.CALENDARS["rep-1"] if e["lead_id"] == a][0]
    assert ev["history"][0]["from"] == old and ev["history"][0]["requested_by"] == "prospect"


def test_reschedule_to_the_day_the_prospect_named_even_weeks_out():
    team(1)
    lid = positive_lead(1)
    booked(lid)
    day = handoff.find_free_slots("rep-1", lead_id=lid, on_date="2026-12-15")
    assert day and all(s.startswith("2026-12-15") for s in day) and len(day) > 2
    assert handoff.reschedule_meeting(lid, day[-1], "prospect", "asked for the 15th")["ok"]


def test_reschedule_refuses_a_clash_and_after_two_moves_asks_the_rep():
    team(1)
    a, b = positive_lead(1), positive_lead(2)
    booked(a)
    taken = booked(b)["start"]
    assert "busy" in handoff.reschedule_meeting(a, taken)["error"]
    slots = handoff.find_free_slots("rep-1", lead_id=a, days_ahead=8)
    for i, s in enumerate(slots[1:4], 1):
        r = handoff.reschedule_meeting(a, s, "prospect")
    assert r["reschedules"] == 3 and "ask the rep" in r["next"]


def test_a_sick_reps_meeting_moves_to_a_colleague():
    team(2, countries=["SE", "SE"])
    lid = positive_lead(1)
    r = booked(lid)
    sick = store.LEADS[lid]["handoff"]["rep_id"]
    other = "rep-2" if sick == "rep-1" else "rep-1"
    handoff.set_rep_status(sick, "away")
    assert "away" in handoff.reschedule_meeting(lid, r["start"], "rep", "sick", new_rep_id=sick)["error"]
    m = handoff.reschedule_meeting(lid, r["start"], "rep", "rep is sick", new_rep_id=other)
    assert m["ok"] and m["rep_id"] == other and store.LEADS[lid]["handoff"]["rep_id"] == other
    assert [e["lead_id"] for e in store.CALENDARS[other]] == [lid] and store.CALENDARS[sick] == []


# --- Cancellation and no-shows -----------------------------------------------------------------

def test_cancellation_frees_the_slot_and_schedules_a_rebooking_follow_up():
    team(1)
    a, b = positive_lead(1), positive_lead(2)
    slot = booked(a)["start"]
    r = handoff.cancel_meeting(a, "prospect", "travelling that week")
    assert r["ok"] and store.LEADS[a]["handoff"]["meeting_status"] == "cancelled"
    handoff.assign_rep(b)
    assert handoff.book_meeting(b, "rep-1", slot)["ok"]
    assert kinds(a) == []
    at("2026-11-25T10:00:00+01:00")
    assert kinds(a) == ["rebook"]
    assert handoff.book_meeting(a, "rep-1", handoff.find_free_slots("rep-1", lead_id=a)[0])["ok"]  # rebooked
    assert kinds(a) == []


def test_cancellation_for_good_waits_for_a_person():
    team(1)
    lid = positive_lead(1)
    booked(lid)
    handoff.cancel_meeting(lid, "prospect", "chose another vendor", rebook=False)
    assert kinds(lid) == ["needs_person"]
    assert shared.tag_loss_reason(lid, "has_competitor", "chose another vendor")["ok"]
    assert kinds(lid) == [] and "needs_person" not in store.LEADS[lid]


def test_no_show_schedules_a_rebook_and_a_second_no_show_asks_a_person():
    team(1)
    lid = positive_lead(1)
    start = booked(lid)["start"]
    at((datetime.fromisoformat(start) + timedelta(hours=1)).isoformat())
    r = handoff.record_meeting_outcome(lid, "no_show")
    assert r["follow_up_due"] and store.LEADS[lid]["handoff"]["no_shows"] == 1
    at((datetime.fromisoformat(start) + timedelta(days=1, hours=2)).isoformat())
    assert "rebook" in kinds(lid)
    second = handoff.find_free_slots("rep-1", lead_id=lid)[0]
    handoff.book_meeting(lid, "rep-1", second)
    at((datetime.fromisoformat(second) + timedelta(hours=1)).isoformat())
    r = handoff.record_meeting_outcome(lid, "no_show")
    assert "close the lead" in r["next"] and kinds(lid) == ["needs_person"]


def test_the_rebooking_email_counts_as_the_follow_up():
    sent_lead()
    stub("positive")
    outreach.read_reply("L-001", "Yes, let's talk")
    handoff.assign_rep("L-001")
    start = handoff.find_free_slots("rep-1", lead_id="L-001")[0]
    handoff.book_meeting("L-001", "rep-1", start)
    handoff.cancel_meeting("L-001", "prospect", "sick")
    at("2026-11-25T10:00:00+01:00")
    assert "rebook" in kinds("L-001")
    d = outreach.save_draft("L-001", "x", "Sorry you were ill. Would Monday 10:00 or Tuesday 14:00 work?")
    outreach.record_approval("L-001", d["subject"], "Sorry you were ill. Would Monday 10:00 or Tuesday 14:00 work?", to=d["to"], cc=d["cc"])
    assert outreach.send_email("L-001")["status"] == "sent"
    assert "rebook" not in kinds("L-001") and d["subject"].startswith("Re:")


# --- Follow-ups after the reply -----------------------------------------------------------------

def test_a_meeting_nobody_recorded_shows_up_the_next_working_day():
    team(1)
    lid = positive_lead(1)
    r = booked(lid)
    at((datetime.fromisoformat(r["end"]) + timedelta(hours=2)).isoformat())
    assert "meeting_outcome" not in kinds(lid)
    at((datetime.fromisoformat(r["end"]) + timedelta(days=1, hours=1)).isoformat())
    assert "meeting_outcome" in kinds(lid)
    assert handoff.record_meeting_outcome(lid, "held")["deal"] == "qualification"
    assert "meeting_outcome" not in kinds(lid)


def test_a_lead_with_a_rep_but_no_meeting_shows_up():
    team(1)
    lid = positive_lead(1)
    handoff.assign_rep(lid)
    assert kinds(lid) == []
    at("2026-11-25T10:30:00+01:00")
    assert kinds(lid) == ["not_booked"]


def test_no_outreach_follow_up_while_a_meeting_is_booked():
    sent_lead()
    stub("positive")
    outreach.read_reply("L-001", "Yes")
    handoff.assign_rep("L-001")
    handoff.book_meeting("L-001", "rep-1", handoff.find_free_slots("rep-1", lead_id="L-001")[0])
    store.LEADS["L-001"]["outreach"]["follow_up_due"] = store.now()
    assert "email" not in kinds("L-001")


def test_a_request_to_move_the_meeting_goes_to_handoff_and_a_first_proposal_stays_positive():
    sent_lead()
    s = stub("positive")
    outreach.read_reply("L-001", "Yes, Thursday works")
    assert s.evidence[0]["meeting_booked_with_us"] is None  # no meeting yet: proposing a time is positive
    handoff.assign_rep("L-001")
    handoff.book_meeting("L-001", "rep-1", handoff.find_free_slots("rep-1", lead_id="L-001")[0])
    outreach._receive("L-001", {"body": "Something came up, can we move it to Friday?"}, store.OUTBOX[-1])
    s = stub("meeting_change")
    r = outreach.read_reply("L-001")
    assert s.evidence[0]["meeting_booked_with_us"]["meeting_status"] == "booked"
    assert r["label"] == "meeting_change" and "reschedule_meeting" in r["next"]
    assert store.LEADS["L-001"]["stage"] == "handed_off"


def test_an_unlabelled_reply_waits_for_a_person_until_it_moves_on():
    sent_lead()
    stub(None)
    outreach.read_reply("L-001", "???")
    assert kinds("L-001") == ["needs_person"]
    assert shared.update_lead("L-001", {"needs_person": None})["ok"]
    assert kinds("L-001") == []


def test_a_quiet_deal_shows_up_in_follow_ups():
    team(1)
    lid = positive_lead(1)
    r = booked(lid)
    at((datetime.fromisoformat(r["end"]) + timedelta(minutes=5)).isoformat())
    handoff.record_meeting_outcome(lid, "held")
    at((datetime.fromisoformat(r["end"]) + timedelta(days=8)).isoformat())
    assert "quiet_deal" in kinds(lid)


def test_calendar_survives_a_restart(tmp_path, monkeypatch):
    team(1)
    lid = positive_lead(1)
    booked(lid)
    saved = [dict(e) for e in store.CALENDARS["rep-1"]]
    monkeypatch.setattr(store, "STATE_FILE", str(tmp_path / "state.json"))
    store.save()
    store.CALENDARS.clear()
    store.load()
    assert store.CALENDARS["rep-1"] == saved


def test_every_active_lead_always_has_a_next_step():
    """Drive every outcome, then jump ahead: each lead still active or queued must show up in due_follow_ups."""
    team(2, countries=["SE", "SE"], max_leads=4)
    leads = [positive_lead(i) for i in range(12)]
    for lid in leads:
        handoff.assign_rep(lid)  # 8 get a rep, 4 wait in the queue
    assigned = [l for l in leads if (store.LEADS[l].get("handoff") or {}).get("rep_id")]
    for lid in assigned[:6]:
        rid = store.LEADS[lid]["handoff"]["rep_id"]
        assert handoff.book_meeting(lid, rid, handoff.find_free_slots(rid, lead_id=lid, days_ahead=10)[0])["ok"]
    a, b, c, d, e, f = assigned[:6]
    handoff.reschedule_meeting(a, handoff.find_free_slots(store.LEADS[a]["handoff"]["rep_id"], lead_id=a, days_ahead=10)[-1])
    handoff.cancel_meeting(b, "prospect", "travel")
    handoff.cancel_meeting(c, "prospect", "not interested", rebook=False)
    at("2026-12-10T10:00:00+01:00")  # every meeting above is over
    handoff.record_meeting_outcome(d, "no_show")
    handoff.record_meeting_outcome(e, "held")
    # f: the meeting passed and nobody recorded it; assigned[6:]: a rep but no meeting; the rest: queued
    at("2026-12-31T10:00:00+01:00")
    listed = {x["lead_id"] for x in outreach.due_follow_ups()}
    stuck = [l for l in leads if store.LEADS[l]["status"] in ("active", "queued") and l not in listed]
    assert stuck == [], [(l, store.LEADS[l].get("handoff")) for l in stuck]
