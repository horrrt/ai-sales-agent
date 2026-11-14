"""Stream 3: handoff (Slack + calendar). Owner: Slava.

Slack messages use Fleet's built-in Slack tools, not this server.
STUB: reps and calendars are fake. Replace with real availability (e.g. Google Calendar).
"""
from datetime import datetime, timedelta, timezone

from ..app import mcp
from ..store import LEADS

REPS = {
    "rep-1": {"name": "TODO rep 1", "slack_user_id": "TODO", "status": "green"},
    "rep-2": {"name": "TODO rep 2", "slack_user_id": "TODO", "status": "yellow"},
}


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
def find_free_slots(rep_id: str, days_ahead: int = 5) -> list[str]:
    """Free 30-minute slots for a rep. STUB: three fixed slots on the next working days."""
    start = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return [(start + timedelta(days=i)).isoformat() for i in range(3)]


@mcp.tool()
def book_meeting(lead_id: str, rep_id: str, start: str) -> dict:
    """Book a meeting between a lead and a rep. Needs approval in Fleet. STUB: records it only."""
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    LEADS[lead_id]["handoff"] = {**LEADS[lead_id].get("handoff", {}), "rep_id": rep_id, "meeting_start": start}
    LEADS[lead_id]["stage"] = "handed_off"
    return {"ok": True, "stub": True}


@mcp.tool()
def save_call_brief(lead_id: str, brief: str) -> dict:
    """Save the call brief (person, company, positioning angle, likely objections)."""
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    LEADS[lead_id].setdefault("handoff", {})["call_brief"] = brief
    return {"ok": True}
