"""Stage 5 hand-off: give a positive reply to a free rep, book a slot, write the call brief. Owner: Slava.

assign_rep picks the rep in code: green first, then yellow, preferring a rep who covers the lead's country
and has the fewest active leads. If every rep is red the lead waits in the queue (status queued, not lost),
and release_queue hands queued leads out once someone has room.

Scheduling works on each rep's calendar (store.CALENDARS): busy blocks and booked meetings, the rep's
working hours in their own time zone, and business hours (08:00-17:00) in the lead's country. Slots
start the working day after next, so the rep gets a day's notice. A meeting can be booked, moved
(reschedule_meeting) or cancelled (cancel_meeting); all three ask a person first. A cancellation or a
no-show schedules a follow-up to rebook, and a meeting whose outcome nobody recorded shows up in
due_follow_ups the working day after it.

Slack messages use Fleet's built-in Slack tools, not this server.
STUB: the roster is data/reps.demo.json and calendars are in the store. Replace with the team's sheet
and Google Calendar free/busy; the rules stay.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..app import mcp
from ..store import CALENDARS, LEADS, QUEUE, REPS, ask_person, clock, log_event, now, set_stage

MEETING_MINUTES = 30
DEFAULT_HOURS = ("09:00", "17:00")
LEAD_HOURS = (8, 17)          # the prospect's business hours, local
MAX_RESCHEDULES = 2           # after this, ask the rep whether the lead is still real
MAX_NO_SHOWS = 2
QUEUE_WAIT_WORKING_DAYS = 1   # a queued lead waiting longer than this is flagged
STALE_DAYS = 7                # an open deal with no activity this long is a quiet deal (as in get_pipeline)


def _traffic_light(rep: dict) -> str:
    active, cap = rep.get("active_leads", 0), max(rep.get("max_leads", 1), 1)
    return "red" if active >= cap else "yellow" if active / cap >= 0.7 else "green"


def _pick(lead: dict) -> str | None:
    country = (lead.get("company") or {}).get("country")
    for colour in ("green", "yellow"):
        reps = [(rid, r) for rid, r in REPS.items() if r.get("status") == colour]  # "away" never matches
        if reps:
            reps.sort(key=lambda x: (country not in (x[1].get("countries") or []), x[1].get("active_leads", 0)))
            return reps[0][0]
    return None


@mcp.tool()
def get_rep_capacity() -> list[dict]:
    """Traffic light per rep: green has capacity, yellow is busy, red is full."""
    return [{"rep_id": k, **v} for k, v in REPS.items()]


@mcp.tool()
def set_rep_status(rep_id: str, status: str) -> dict:
    """Set a rep's traffic light to green, yellow or red, or away (sick or on leave: no new leads, no slots;
    move their booked meetings with reschedule_meeting and new_rep_id; get_rep_calendar lists them)."""
    if rep_id not in REPS or status not in {"green", "yellow", "red", "away"}:
        return {"error": "unknown rep or status"}
    REPS[rep_id]["status"] = status
    return {"ok": True}


@mcp.tool()
def assign_rep(lead_id: str) -> dict:
    """Hand a positive reply to a free rep: green first, then yellow. If every rep is red, the lead joins the wait queue."""
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": f"unknown lead {lead_id}"}
    if (lead.get("handoff") or {}).get("rep_id"):  # already assigned: don't count the lead twice
        rep_id = lead["handoff"]["rep_id"]
        return {"ok": True, "rep_id": rep_id, "rep": REPS.get(rep_id), "already_assigned": True,
                "next": "find_free_slots, book_meeting, save_call_brief, then tell the rep in Slack"}
    rep_id = _pick(lead)
    if rep_id is None:
        if not any(q["lead_id"] == lead_id for q in QUEUE):
            QUEUE.append({"lead_id": lead_id, "since": now()})
        lead["status"] = "queued"
        log_event(lead_id, "handoff", "skipped", "every rep is red: waiting in the queue")
        return {"ok": True, "rep_id": None, "status": "queued", "position": len(QUEUE),
                "next": "post in #sales-desk that the lead is waiting; release_queue hands it out when a rep has room"}
    rep = REPS[rep_id]
    rep["active_leads"] = rep.get("active_leads", 0) + 1
    rep["status"] = _traffic_light(rep)
    lead["status"] = "active"
    lead.setdefault("handoff", {}).update(rep_id=rep_id, assigned_at=now())
    log_event(lead_id, "handoff", "success", f"assigned to {rep_id} ({rep['name']})")
    return {"ok": True, "rep_id": rep_id, "rep": rep, "next": "find_free_slots, book_meeting, save_call_brief, then tell the rep in Slack"}


@mcp.tool()
def release_queue() -> list[dict]:
    """Hand queued leads to reps that now have room, oldest first. Returns what was assigned."""
    released = []
    for item in list(QUEUE):
        lead = LEADS.get(item["lead_id"])
        if lead is None or _pick(lead) is None:
            continue
        QUEUE.remove(item)
        released.append(assign_rep(item["lead_id"]))
    return released


@mcp.tool()
def get_wait_queue() -> list[dict]:
    """Positive replies waiting for a rep because every rep was red."""
    return list(QUEUE)


def _rep_tz(rep: dict) -> ZoneInfo:
    try:
        return ZoneInfo(rep.get("timezone") or "Europe/Copenhagen")
    except Exception:
        return ZoneInfo("Europe/Copenhagen")


def _hours(rep: dict) -> tuple[time, time]:
    a, b = rep.get("working_hours") or DEFAULT_HOURS
    return time.fromisoformat(a), time.fromisoformat(b)


def _lead_tz(lead: dict) -> ZoneInfo:
    from .outreach import _lead_tz as tz
    return tz(lead)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(start: str) -> datetime:
    t = datetime.fromisoformat(start)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _add_working_days(at: datetime, n: int, tz: ZoneInfo) -> datetime:
    day = at.astimezone(tz)
    while n > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def _next_working_morning(tz: ZoneInfo, hour: int = 9) -> datetime:
    day = _add_working_days(clock(), 1, tz)
    return datetime.combine(day.date(), time(hour), tz)


def _events(rep_id: str) -> list[dict]:
    return CALENDARS.setdefault(rep_id, [])


def _clash(rep_id: str, start: datetime, end: datetime, ignore: str | None = None) -> dict | None:
    for e in _events(rep_id):
        if e.get("status") == "cancelled" or e.get("event_id") == ignore:
            continue
        if _parse(e["start"]) < end and start < _parse(e["end"]):
            return e
    return None


def _slot_problem(rep_id: str, lead: dict | None, start: datetime, minutes: int, ignore: str | None = None) -> str | None:
    """Why this slot can't be booked, or None if it can."""
    rep = REPS.get(rep_id)
    if rep is None:
        return f"unknown rep {rep_id}"
    end = start + timedelta(minutes=minutes)
    if start <= clock():
        return "that time has already passed"
    local, (h0, h1) = start.astimezone(_rep_tz(rep)), _hours(rep)
    if local.weekday() >= 5:
        return "start is on a weekend; pick a slot from find_free_slots"
    if not (h0 <= local.time() and (local + timedelta(minutes=minutes)).time() <= h1 and local.date() == (local + timedelta(minutes=minutes)).date()):
        return f"outside {rep.get('name', rep_id)}'s working hours ({h0:%H:%M}-{h1:%H:%M} {_rep_tz(rep).key})"
    if lead is not None:
        ll = start.astimezone(_lead_tz(lead))
        if ll.weekday() >= 5 or not (LEAD_HOURS[0] <= ll.hour and (ll + timedelta(minutes=minutes)).hour <= LEAD_HOURS[1]
                                       and (ll + timedelta(minutes=minutes)).time() <= time(LEAD_HOURS[1])):
            return f"outside business hours for the prospect ({ll:%a %H:%M} in {_lead_tz(lead).key})"
    e = _clash(rep_id, start, end, ignore)
    if e:
        return f"{rep.get('name', rep_id)} is busy then ({e.get('title') or e.get('kind')} {e['start']} to {e['end']})"
    return None


@mcp.tool()
def find_free_slots(rep_id: str, days_ahead: int = 5, lead_id: str | None = None,
                    duration_minutes: int = MEETING_MINUTES, on_date: str | None = None) -> list[str]:
    """Free slots in a rep's calendar: up to two a day (the first free morning slot and the first free
    afternoon slot), on working days up to days_ahead working days out, skipping the next working day so the
    rep gets a day's notice. With lead_id, only times inside the prospect's business hours too.
    on_date (YYYY-MM-DD) returns every free slot that day, for a prospect who asked for a day."""
    rep = REPS.get(rep_id)
    if rep is None:
        return []
    tz, (h0, h1) = _rep_tz(rep), _hours(rep)
    lead = LEADS.get(lead_id) if lead_id else None
    days_ahead = max(2, min(int(days_ahead), 30))
    if rep.get("status") == "away":
        return []
    if on_date:  # reach the day asked for, however far out (up to 30 working days)
        days_ahead = 30
    days, day = [], clock().astimezone(tz)
    while len(days) < days_ahead:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day.date())
    days = days[1:]  # a working day's notice
    if on_date:
        days = [d for d in days if d.isoformat() == on_date]
    out = []
    for d in days:
        t, picked = datetime.combine(d, h0, tz), []
        while t.time() < h1:
            if not _slot_problem(rep_id, lead, t, duration_minutes):
                if on_date or not picked or (picked[-1].hour < 12 <= t.hour and len(picked) < 2):
                    picked.append(t)
            t += timedelta(minutes=30)
        out += [_iso(x) for x in picked]
    return out


@mcp.tool()
def get_rep_calendar(rep_id: str, days_ahead: int = 10) -> dict:
    """A rep's calendar from now to days_ahead working days out: booked meetings (with lead_id and company)
    and busy blocks. Use it to find a sick or away rep's meetings before moving them."""
    rep = REPS.get(rep_id)
    if rep is None:
        return {"ok": False, "error": f"unknown rep {rep_id}"}
    until = _add_working_days(clock(), max(1, min(int(days_ahead), 30)), _rep_tz(rep))
    events = sorted((e for e in _events(rep_id) if e.get("status") != "cancelled"
                     and clock() <= _parse(e["end"]) and _parse(e["start"]) <= until), key=lambda e: e["start"])
    return {"ok": True, "rep_id": rep_id, "name": rep.get("name"), "status": rep.get("status"), "timezone": _rep_tz(rep).key,
            "meetings": [{k: e.get(k) for k in ("event_id", "lead_id", "title", "start", "end", "status")} | 
                         {"company": (LEADS.get(e.get("lead_id")) or {}).get("company", {}).get("name")}
                         for e in events if e.get("kind") == "meeting"],
            "busy": [{k: e.get(k) for k in ("title", "start", "end")} for e in events if e.get("kind") != "meeting"]}


def _meeting(lead: dict) -> dict | None:
    h = lead.get("handoff") or {}
    for e in _events(h.get("rep_id") or ""):
        if e.get("event_id") == h.get("event_id"):
            return e
    return None


@mcp.tool()
def book_meeting(lead_id: str, rep_id: str, start: str, duration_minutes: int = MEETING_MINUTES) -> dict:
    """Book a meeting between a lead and a rep in the rep's calendar. Needs approval in Fleet.

    Refuses a time that has passed, is outside the rep's working hours or the prospect's business hours,
    or clashes with something in the rep's calendar. A lead with a booked meeting: use reschedule_meeting.
    STUB: the calendar invite isn't sent anywhere."""
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"error": f"unknown lead {lead_id}"}
    h = lead.get("handoff") or {}
    if h.get("meeting_status") == "booked":
        return {"ok": False, "error": "already_booked", "meeting_start": h.get("meeting_start"),
                "next": "reschedule_meeting to move it, or cancel_meeting"}
    try:
        t = _parse(start)
    except ValueError:
        return {"ok": False, "error": "start must be an ISO date-time from find_free_slots"}
    problem = _slot_problem(rep_id, lead, t, duration_minutes)
    if problem:
        return {"ok": False, "error": problem, "next": "pick another slot from find_free_slots"}
    end = t + timedelta(minutes=duration_minutes)
    n = sum(1 for e in _events(rep_id) if e.get("lead_id") == lead_id) + 1
    attendees = [REPS[rep_id].get("email"), lead["person"]["email"]] + \
                [c["email"] for c in lead.get("contacts", []) if c.get("role") in ("handover", "replied_for_lead", "cc")]
    event = {"event_id": f"E-{lead_id}-{n}", "rep_id": rep_id, "lead_id": lead_id, "kind": "meeting", "status": "booked",
             "title": f"Intro call: {lead['company']['name']}", "start": _iso(t), "end": _iso(end),
             "attendees": [a for a in attendees if a], "history": []}
    _events(rep_id).append(event)
    lead["handoff"] = {**h, "rep_id": rep_id, "event_id": event["event_id"], "meeting_start": event["start"],
                       "meeting_end": event["end"], "meeting_status": "booked", "meeting_outcome": None,
                       "follow_up_due": None, "follow_up_kind": None}
    set_stage(lead, "handed_off")
    log_event(lead_id, "handoff", "success", f"meeting booked with {rep_id} at {event['start']}", channel="calendar")
    return {"ok": True, "stub": True, "event_id": event["event_id"], "start": event["start"], "end": event["end"],
            "rep_local": t.astimezone(_rep_tz(REPS[rep_id])).isoformat(), "prospect_local": t.astimezone(_lead_tz(lead)).isoformat(),
            "attendees": event["attendees"]}


@mcp.tool()
def reschedule_meeting(lead_id: str, new_start: str, requested_by: str = "prospect", reason: str = "",
                       new_rep_id: str | None = None) -> dict:
    """Move a booked meeting to a new time (same rules as book_meeting). This tool always asks a person first.

    requested_by: prospect or rep. new_rep_id moves it to another rep too (the rep is sick or away); check
    that rep's find_free_slots first. After the second move, the result says to ask the rep whether to keep going."""
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": f"unknown lead {lead_id}"}
    h, e = lead.get("handoff") or {}, _meeting(lead)
    if h.get("meeting_status") != "booked" or e is None:
        return {"ok": False, "error": "no booked meeting to move; use book_meeting"}
    try:
        t = _parse(new_start)
    except ValueError:
        return {"ok": False, "error": "new_start must be an ISO date-time from find_free_slots"}
    minutes = int((_parse(e["end"]) - _parse(e["start"])).total_seconds() // 60)
    to_rep = new_rep_id or h["rep_id"]
    if to_rep not in REPS:
        return {"ok": False, "error": f"unknown rep {to_rep}"}
    if REPS[to_rep].get("status") == "away":
        return {"ok": False, "error": f"{REPS[to_rep].get('name', to_rep)} is away; pick another rep"}
    problem = _slot_problem(to_rep, lead, t, minutes, ignore=e["event_id"] if to_rep == h["rep_id"] else None)
    if problem:
        return {"ok": False, "error": problem, "next": "pick another slot from find_free_slots"}
    old, old_rep = e["start"], h["rep_id"]
    e["history"].append({"from": old, "to": _iso(t), "requested_by": requested_by, "reason": reason, "at": now(),
                         **({"from_rep": old_rep, "to_rep": to_rep} if to_rep != old_rep else {})})
    e.update(start=_iso(t), end=_iso(t + timedelta(minutes=minutes)))
    if to_rep != old_rep:  # move the event to the other rep's calendar and the lead with it
        _events(old_rep).remove(e)
        e.update(rep_id=to_rep, attendees=[REPS[to_rep].get("email")] + [a for a in e["attendees"] if a != REPS[old_rep].get("email")])
        _events(to_rep).append(e)
        for rid, d in ((old_rep, -1), (to_rep, 1)):
            REPS[rid]["active_leads"] = max(0, REPS[rid].get("active_leads", 0) + d)
            if REPS[rid].get("status") != "away":
                REPS[rid]["status"] = _traffic_light(REPS[rid])
    h.update(rep_id=to_rep, meeting_start=e["start"], meeting_end=e["end"], reschedules=h.get("reschedules", 0) + 1)
    log_event(lead_id, "handoff", "success", f"meeting moved from {old} to {e['start']}"
              + (f", from {old_rep} to {to_rep}" if to_rep != old_rep else "") + f" ({requested_by}: {reason})",
              channel="calendar", by=requested_by)
    out = {"ok": True, "stub": True, "start": e["start"], "end": e["end"], "rep_id": to_rep, "reschedules": h["reschedules"],
           "next": "confirm the new time in the email thread and tell the rep in Slack"}
    if h["reschedules"] > MAX_RESCHEDULES:
        out["next"] = f"moved {h['reschedules']} times: ask the rep whether to keep this meeting or close the lead"
    return out


@mcp.tool()
def cancel_meeting(lead_id: str, cancelled_by: str = "prospect", reason: str = "", rebook: bool = True) -> dict:
    """Cancel a booked meeting and free the rep's slot. This tool always asks a person first.

    rebook true (the prospect still wants to talk): a follow-up to offer new times is due the next working
    day and shows up in due_follow_ups. rebook false: no follow-up; a person decides whether the lead is lost."""
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": f"unknown lead {lead_id}"}
    h, e = lead.get("handoff") or {}, _meeting(lead)
    if h.get("meeting_status") != "booked":
        return {"ok": False, "error": "no booked meeting to cancel"}
    if e is not None:
        e["status"] = "cancelled"
        e["history"].append({"cancelled_by": cancelled_by, "reason": reason, "at": now()})
    h.update(meeting_status="cancelled", meeting_outcome="cancelled", meeting_outcome_at=now())
    _rebook_follow_up(lead, rebook)
    log_event(lead_id, "handoff", "failure", f"meeting cancelled by {cancelled_by}: {reason}", channel="calendar", by=cancelled_by)
    return {"ok": True, "stub": True, "follow_up_due": h.get("follow_up_due"),
            "next": "due_follow_ups lists the rebooking follow-up" if rebook else
                    "ask the rep whether to close the lead (tag_loss_reason) or keep it"}


def _rebook_follow_up(lead: dict, rebook: bool) -> None:
    h = lead["handoff"]
    if rebook:
        h["follow_up_due"] = _iso(_next_working_morning(_lead_tz(lead)))
        h["follow_up_kind"] = "rebook"
    else:
        h["follow_up_due"], h["follow_up_kind"] = None, None
        ask_person(lead, f"meeting {h.get('meeting_outcome')}: close the lead (tag_loss_reason) or keep it")


@mcp.tool()
def record_meeting_outcome(lead_id: str, outcome: str, by: str = "rep") -> dict:
    """The rep reports how the booked meeting went: held, no_show or cancelled. This tool always asks a person first.

    held is the North Star: a qualified meeting that actually happened. The rep takes the deal from here.
    held and no_show only after the meeting's start time. A no-show or a cancellation schedules a
    follow-up to rebook (next working day); after a second no-show, ask the rep whether to close the lead.
    """
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": f"unknown lead {lead_id}"}
    h = lead.get("handoff") or {}
    if not h.get("meeting_start"):
        return {"ok": False, "error": "no meeting was booked for this lead"}
    if outcome not in ("held", "no_show", "cancelled"):
        return {"ok": False, "error": "outcome must be held, no_show or cancelled"}
    if outcome in ("held", "no_show") and _parse(h["meeting_start"]) > clock():
        return {"ok": False, "error": f"the meeting hasn't started yet ({h['meeting_start']})",
                "next": "record the outcome after the meeting; to call it off now, use cancel_meeting"}
    lead["handoff"].update(meeting_outcome=outcome, meeting_outcome_at=now(),
                           meeting_status={"held": "held", "no_show": "no_show", "cancelled": "cancelled"}[outcome])
    e = _meeting(lead)
    if e is not None:
        e["status"] = lead["handoff"]["meeting_status"]
    log_event(lead_id, "handoff", "success" if outcome == "held" else "failure", f"meeting {outcome}", channel="calendar", by=by)
    if outcome == "held":
        lead["handoff"].update(follow_up_due=None, follow_up_kind=None)
        from .deals import open_deal  # a held meeting opens the deal (stage qualification)
        open_deal(lead_id)
        return {"ok": True, "meeting_outcome": outcome, "deal": "qualification",
                "next": "the rep adds call notes (record_call_notes), then qualify_deal and draft_quote"}
    if outcome == "no_show":
        lead["handoff"]["no_shows"] = lead["handoff"].get("no_shows", 0) + 1
    again = outcome == "cancelled" or lead["handoff"].get("no_shows", 0) < MAX_NO_SHOWS
    _rebook_follow_up(lead, again)
    return {"ok": True, "meeting_outcome": outcome, "follow_up_due": lead["handoff"].get("follow_up_due"),
            "next": "due_follow_ups lists the rebooking follow-up: email the prospect new times in the thread" if again
                    else f"{MAX_NO_SHOWS} no-shows: ask the rep whether to close the lead"}


def handoff_follow_ups() -> list[dict]:
    """Follow-ups after the reply: rebook a cancelled or missed meeting, record an outcome nobody recorded,
    book a lead that has a rep but no meeting, positive replies waiting too long in the queue, leads waiting
    for a person's decision, and open deals that went quiet. due_follow_ups returns these with the email ones."""
    due, t = [], clock()
    for lead in LEADS.values():
        h = lead.get("handoff") or {}
        if lead.get("status") not in ("active", "queued") or lead.get("do_not_contact"):
            continue
        if h.get("follow_up_kind") == "rebook" and h.get("follow_up_due") and _parse(h["follow_up_due"]) <= t:
            due.append({"lead_id": lead["lead_id"], "kind": "rebook", "rep_id": h.get("rep_id"),
                        "reason": f"meeting {h.get('meeting_outcome')}",
                        "next": "@outreach: email the prospect in the thread with two new times from find_free_slots; "
                                "@handoff books when they answer"})
        if (h.get("meeting_status") == "booked" and h.get("meeting_end") and not h.get("meeting_outcome")
                and _add_working_days(_parse(h["meeting_end"]), 1, _lead_tz(lead)) <= t):
            due.append({"lead_id": lead["lead_id"], "kind": "meeting_outcome", "rep_id": h.get("rep_id"),
                        "meeting_start": h["meeting_start"],
                        "next": "@handoff: ask the rep in #sales-desk how the meeting went; once they answer, "
                                "record it with record_meeting_outcome. Never guess the outcome"})
        assigned_at = h.get("assigned_at")
        if (h.get("rep_id") and not h.get("meeting_status") and assigned_at
                and _add_working_days(_parse(assigned_at), 1, _lead_tz(lead)) <= t):
            due.append({"lead_id": lead["lead_id"], "kind": "not_booked", "rep_id": h["rep_id"],
                        "next": "@handoff: the lead said yes but has no meeting; book the first free slot (find_free_slots with "
                                "lead_id, then book_meeting), as for any positive reply. The invite lets them accept or ask for another time"})
        if lead.get("needs_person"):
            due.append({"lead_id": lead["lead_id"], "kind": "needs_person", **lead["needs_person"],
                        "next": "ask a person in #sales-desk; clear it with update_lead(needs_person=null) once decided"})
        deal = lead.get("deal") or {}
        if deal and deal.get("stage") not in ("closed_won", "closed_lost"):
            last = lead.get("updated_at") or lead.get("created_at")
            if last and _parse(last) + timedelta(days=STALE_DAYS) <= t:
                due.append({"lead_id": lead["lead_id"], "kind": "quiet_deal", "stage": deal["stage"], "last_activity": last,
                            "rep_id": deal.get("owner"), "next": "@deal-desk: nudge the rep about this deal"})
    for q in QUEUE:
        lead = LEADS.get(q["lead_id"]) or {}
        if _add_working_days(_parse(q["since"]), QUEUE_WAIT_WORKING_DAYS, _lead_tz(lead) if lead else ZoneInfo("Europe/Copenhagen")) <= t:
            due.append({"lead_id": q["lead_id"], "kind": "queue_waiting", "since": q["since"],
                        "next": "@handoff: post in #sales-desk that a positive reply has waited over a working day for a rep"})
    return due


@mcp.tool()
def save_call_brief(lead_id: str, brief: str) -> dict:
    """Save the call brief (person, company, positioning angle, likely objections)."""
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    LEADS[lead_id].setdefault("handoff", {})["call_brief"] = brief
    return {"ok": True}
