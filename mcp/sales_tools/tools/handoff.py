"""Stage 5 hand-off: give a positive reply to a free rep, book a slot, write the call brief. Owner: Slava.

assign_rep picks the rep in code: green first, then yellow, preferring a rep who covers the lead's country
and has the fewest active leads. If every rep is red the lead waits in the queue (status queued, not lost),
and release_queue hands queued leads out once someone has room.

Slack messages use Fleet's built-in Slack tools, not this server.
STUB: the roster is data/reps.demo.json and free slots are fixed working-day mornings. Replace with the
team's sheet and Google Calendar free/busy.
"""
from datetime import datetime, timedelta, timezone

from ..app import mcp
from ..store import LEADS, QUEUE, REPS, log_event, now


def _traffic_light(rep: dict) -> str:
    active, cap = rep.get("active_leads", 0), max(rep.get("max_leads", 1), 1)
    return "red" if active >= cap else "yellow" if active / cap >= 0.7 else "green"


def _pick(lead: dict) -> str | None:
    country = (lead.get("company") or {}).get("country")
    for colour in ("green", "yellow"):
        reps = [(rid, r) for rid, r in REPS.items() if r.get("status") == colour]
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
    """Set a rep's traffic light to green, yellow or red."""
    if rep_id not in REPS or status not in {"green", "yellow", "red"}:
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
    lead.setdefault("handoff", {})["rep_id"] = rep_id
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


@mcp.tool()
def find_free_slots(rep_id: str, days_ahead: int = 5) -> list[str]:
    """Free 30-minute slots for a rep, on working days, at least one working day out. STUB: 10:00 UTC each day."""
    working_days, day = [], datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    while len(working_days) < 4:
        day += timedelta(days=1)
        if day.weekday() < 5:
            working_days.append(day.isoformat())
    return working_days[1:]  # skip the next working day, so the rep gets at least one working day's notice


@mcp.tool()
def book_meeting(lead_id: str, rep_id: str, start: str) -> dict:
    """Book a meeting between a lead and a rep. Needs approval in Fleet. STUB: records it only."""
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    try:
        if datetime.fromisoformat(start).weekday() >= 5:
            return {"ok": False, "error": "start is on a weekend; pick a slot from find_free_slots"}
    except ValueError:
        return {"ok": False, "error": "start must be an ISO date-time from find_free_slots"}
    LEADS[lead_id]["handoff"] = {**LEADS[lead_id].get("handoff", {}), "rep_id": rep_id, "meeting_start": start}
    LEADS[lead_id]["stage"] = "handed_off"
    log_event(lead_id, "handoff", "success", f"meeting booked with {rep_id} at {start}", channel="calendar")
    return {"ok": True, "stub": True}


@mcp.tool()
def save_call_brief(lead_id: str, brief: str) -> dict:
    """Save the call brief (person, company, positioning angle, likely objections)."""
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    LEADS[lead_id].setdefault("handoff", {})["call_brief"] = brief
    return {"ok": True}
