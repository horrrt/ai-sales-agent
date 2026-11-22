"""The pipeline at different times: nights, weekends, public holidays, daylight-saving changes.

Each test is a use case in docs/use-cases.md. The sweep runs the same flow at many pinned times and
checks what must hold at any time: emails only go out in business hours on a working day, follow-ups
and meetings land on working days, and nothing is booked on anyone's public holiday.

    cd mcp && uv run --extra dev pytest -q
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sales_tools import store, workdays
from sales_tools.tools import handoff, outreach

from test_pipeline import owner, approved_draft, at, fresh, sent_lead, stub  # noqa: F401

OSLO, STOCKHOLM = ZoneInfo("Europe/Oslo"), ZoneInfo("Europe/Stockholm")


def lead_in(country, lead_id="L-001"):
    approved_draft(lead_id)
    store.LEADS[lead_id]["company"]["country"] = country


def test_holiday_tables():
    assert workdays.easter(2026) == date(2026, 4, 5) and workdays.easter(2027) == date(2027, 3, 28)
    assert workdays.holiday(date(2027, 5, 17), "NO") and not workdays.holiday(date(2027, 5, 17), "SE")
    assert workdays.holiday(date(2027, 6, 25), "SE") == "Midsummer Eve"
    assert workdays.holiday(date(2027, 12, 6), "FI") == "Independence Day"
    assert workdays.holiday(date(2026, 10, 3), "DE") == "German Unity Day"
    assert workdays.holiday(date(2026, 12, 24), "DE") is None  # a normal working day in Germany
    assert workdays.holiday(date(2026, 12, 24), "XX") is None  # unknown countries only skip weekends


def test_christmas_eve_send_waits_until_after_christmas():
    lead_in("NO")
    at("2026-12-24T10:00:00+01:00")  # Thursday, Christmas Eve
    r = outreach.send_email("L-001")
    assert r["status"] == "scheduled" and r["scheduled_for"] == "2026-12-28T07:00:00+00:00"  # Monday 08:00 Oslo
    assert "public holiday (Christmas Eve)" in store.INTERACTIONS[-1]["message"]


def test_a_national_holiday_holds_one_country_and_not_another():
    lead_in("NO")
    approved_draft("L-002")  # Sweden
    at("2027-05-17T10:00:00+02:00")  # Constitution Day in Norway, a Monday in Sweden
    assert outreach.send_email("L-001")["scheduled_for"] == "2027-05-18T06:00:00+00:00"
    assert outreach.send_email("L-002")["status"] == "sent"


def test_follow_up_spacing_skips_christmas():
    lead_in("SE")
    at("2026-12-23T10:00:00+01:00")  # Wednesday
    r = outreach.send_email("L-001")
    assert r["follow_up_due"] == "2026-12-30T09:00:00+00:00"  # 28, 29, 30: the 24th to the 27th don't count


def test_slots_skip_holidays_where_the_rep_works_and_where_the_prospect_is():
    sent_lead()
    stub("positive")
    outreach.read_reply("L-001", "Yes")
    store.LEADS["L-001"]["company"]["country"] = "DE"  # Germany works on Christmas Eve, Denmark doesn't
    at("2026-12-21T10:00:00+01:00")  # Monday
    days = {s[:10] for s in handoff.find_free_slots("rep-1", lead_id="L-001", days_ahead=6)}  # Mette works in Denmark
    # 6 working days in Denmark and Germany: 22 (notice day), 23, 28, 29, 30 and 4 January (31 December and 1 January off)
    assert days == {"2026-12-23", "2026-12-28", "2026-12-29", "2026-12-30", "2027-01-04"}
    christmas_eve = "2026-12-24T09:00:00+00:00"
    assert "public holiday where Mette" in handoff.book_meeting("L-001", "rep-1", christmas_eve)["error"]
    store.LEADS["L-001"]["company"]["country"] = "FI"
    assert "public holiday for the prospect" in handoff._slot_problem("rep-2", store.LEADS["L-001"],
                                                                         datetime.fromisoformat("2027-12-06T10:00:00+01:00"), 30)


def test_a_reply_on_saturday_gets_slots_from_tuesday():
    sent_lead()
    stub("positive")
    outreach.read_reply("L-001", "Yes")
    at("2026-11-28T11:00:00+01:00")  # Saturday
    assert handoff.find_free_slots("rep-1", lead_id="L-001")[0][:10] == "2026-12-01"  # Monday is the notice day


def test_slots_keep_local_time_across_the_daylight_saving_change():
    sent_lead()
    at("2027-03-25T10:00:00+01:00")  # Thursday before Easter and the clock change (Maundy Thursday in Denmark)
    slots = handoff.find_free_slots("rep-1", days_ahead=8)
    first = slots[0]  # Tuesday 30 March is the notice day, so the first slot is Wednesday 31 March
    assert datetime.fromisoformat(first).astimezone(ZoneInfo("Europe/Copenhagen")).hour == 9
    assert first == "2027-03-31T07:00:00+00:00"  # summer time: 09:00 is 07:00 UTC, not 08:00
    assert not any(s[:10] in ("2027-03-25", "2027-03-26", "2027-03-29") for s in slots)  # Easter in Denmark


def test_a_rebook_follow_up_after_a_friday_cancellation_is_due_monday():
    sent_lead()
    stub("positive")
    outreach.read_reply("L-001", "Yes")
    handoff.assign_rep("L-001")
    handoff.book_meeting("L-001", "rep-1", handoff.find_free_slots("rep-1", lead_id="L-001")[0])
    at("2026-11-27T16:30:00+01:00")  # Friday afternoon
    handoff.cancel_meeting("L-001", "prospect", "sick", by=owner("L-001"))
    due = datetime.fromisoformat(store.LEADS["L-001"]["handoff"]["follow_up_due"]).astimezone(STOCKHOLM)
    assert due.date() == date(2026, 11, 30) and due.hour == 9


SWEEP = {
    "weekday morning": "2026-11-24T10:00:00+01:00",
    "weekday 07:59": "2026-11-24T07:59:00+01:00",
    "weekday 17:00": "2026-11-24T17:00:00+01:00",
    "weekday 22:00": "2026-11-24T22:00:00+01:00",
    "Friday 23:30": "2026-11-27T23:30:00+01:00",
    "Saturday noon": "2026-11-28T12:00:00+01:00",
    "Sunday 23:59": "2026-11-29T23:59:00+01:00",
    "Christmas Eve": "2026-12-24T10:00:00+01:00",
    "New Year's Eve 23:00": "2026-12-31T23:00:00+01:00",
    "Good Friday": "2027-03-26T10:00:00+01:00",
    "DST night": "2027-03-28T02:30:00+01:00",
    "Midsummer Eve": "2027-06-25T10:00:00+02:00",
    "Norway 17 May": "2027-05-17T10:00:00+02:00",
    "Finland 6 December": "2027-12-06T10:00:00+02:00",
}


@pytest.mark.parametrize("country", ["SE", "NO", "FI", "DK", "DE"])
@pytest.mark.parametrize("when", list(SWEEP), ids=list(SWEEP))
def test_time_sweep_nothing_goes_out_or_is_booked_at_the_wrong_time(when, country):
    lead_in(country)
    tz = ZoneInfo(workdays.COUNTRY_TZ[country])
    at(SWEEP[when])
    r = outreach.send_email("L-001")
    went = datetime.fromisoformat(r.get("sent_at") or r["scheduled_for"]).astimezone(tz)
    assert workdays.is_working_day(went.date(), country) and 8 <= went.hour < 17, (r, went)
    outreach.get_replies()  # releases a held email once its time comes; here it may still be held
    at(went.isoformat())
    outreach.due_follow_ups()
    out = store.LEADS["L-001"]["outreach"]
    assert out["sent_at"] and workdays.is_working_day(datetime.fromisoformat(out["follow_up_due"]).astimezone(tz).date(), country)
    stub("positive")
    outreach.read_reply("L-001", "Yes, let's talk")
    handoff.assign_rep("L-001")
    rid = store.LEADS["L-001"]["handoff"]["rep_id"] or "rep-1"
    rep_tz, rep_country = handoff._rep_tz(store.REPS[rid]), handoff._rep_country(store.REPS[rid])
    for s in handoff.find_free_slots(rid, lead_id="L-001", days_ahead=10):
        t = datetime.fromisoformat(s)
        assert workdays.is_working_day(t.astimezone(tz).date(), country), s
        assert workdays.is_working_day(t.astimezone(rep_tz).date(), rep_country), s
        assert 8 <= t.astimezone(tz).hour < 17 and t > store.clock()
