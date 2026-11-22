"""Shared tools every stage uses. Owner: Ayaan (stream 4)."""
import json
from pathlib import Path

from ..app import mcp
from ..store import BRIEF, INTERACTIONS, LEADS, PROPOSALS, log_event, now

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"
REASONS = {r["id"] for r in json.loads((SCHEMAS / "loss_reasons.json").read_text())["reasons"]}
_INTERACTION = json.loads((SCHEMAS / "interaction.schema.json").read_text())["properties"]
STAGES = _INTERACTION["stage"]["enum"]
OUTCOMES = _INTERACTION["outcome"]["enum"]
CHANNELS = _INTERACTION["channel"]["enum"]


@mcp.tool()
def get_customer_brief(customer_id: str) -> dict:
    """Get the customer's offer, ICP and product info."""
    return BRIEF


@mcp.tool()
def get_lead(lead_id: str) -> dict:
    """Get one lead with everything the pipeline knows about it."""
    return LEADS.get(lead_id, {"error": f"unknown lead {lead_id}"})


@mcp.tool()
def update_lead(lead_id: str, fields: dict) -> dict:
    """Set a lead's status to insufficient_data, or add a note. Everything else is written by the stage tools.

    Stage, approval, outreach, hand-off, close and loss reason can only change through their own tools,
    so no agent can mark its own draft as approved or a lead as handed off.
    """
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    allowed = {"status": {"insufficient_data"}, "notes": None}
    bad = [k for k, v in fields.items() if k not in allowed or (allowed[k] is not None and v not in allowed[k])]
    if bad:
        return {"ok": False, "error": "field_not_allowed", "fields": bad,
                "allowed": "status=insufficient_data, notes. Use the stage tools for everything else."}
    LEADS[lead_id].update(fields)
    log_event(lead_id, "outreach", "skipped" if fields.get("status") else "success", f"update_lead: {fields}", by="agent")
    return {"ok": True, "lead": LEADS[lead_id]}


@mcp.tool()
def log_interaction(lead_id: str, stage: str, outcome: str, message: str = "", channel: str = "internal",
                    loss_reason: str | None = None, prompt_version: str | None = None, variant: str | None = None) -> dict:
    """Log one stage for one lead (see schemas/interaction.schema.json). Call this for every stage, including failures.

    stage: targeting, research, positioning, outreach, approval, reply, close or handoff.
    outcome: success, failure or skipped.
    Put details in message. prompt_version is the one at the top of your instructions.
    """
    problems = []
    if stage not in STAGES:
        problems.append(f"stage must be one of {STAGES}")
    if outcome not in OUTCOMES:
        problems.append(f"outcome must be one of {OUTCOMES}; put details in message")
    if channel not in CHANNELS:
        problems.append(f"channel must be one of {CHANNELS}")
    if loss_reason is not None and loss_reason not in REASONS:
        problems.append(f"loss_reason must be null or one of {sorted(REASONS)}")
    if problems:
        return {"ok": False, "error": "invalid_interaction", "problems": problems}
    log_event(lead_id, stage, outcome, message, channel, loss_reason, prompt_version=prompt_version,
              variant=variant, by="agent")
    return {"ok": True}


@mcp.tool()
def tag_loss_reason(lead_id: str, reason: str, evidence: str) -> dict:
    """Mark a lead lost with exactly one reason from schemas/loss_reasons.json."""
    if reason not in REASONS:
        return {"error": f"unknown reason {reason}", "allowed": sorted(REASONS)}
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    LEADS[lead_id].update({"status": "lost", "loss_reason": reason})
    log_event(lead_id, "close", "failure", evidence, loss_reason=reason)
    return {"ok": True}


@mcp.tool()
def get_funnel_metrics() -> dict:
    """Counts per stage and per loss reason. STUB: counts the in-memory log only."""
    by_stage: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for i in INTERACTIONS:
        by_stage[i["stage"]] = by_stage.get(i["stage"], 0) + 1
    for lead in LEADS.values():  # one reason per lost lead, from the lead sheet, not the log
        if lead.get("status") == "lost" and lead.get("loss_reason"):
            by_reason[lead["loss_reason"]] = by_reason.get(lead["loss_reason"], 0) + 1
    by_status: dict[str, int] = {}
    for lead in LEADS.values():
        by_status[lead.get("status", "?")] = by_status.get(lead.get("status", "?"), 0) + 1
    return {"interactions_by_stage": by_stage, "losses_by_reason": by_reason, "leads_by_status": by_status,
            "north_star_meetings_held": sum((l.get("handoff") or {}).get("meeting_outcome") == "held" for l in LEADS.values()),
            "deals_won": sum((l.get("deal") or {}).get("stage") == "closed_won" for l in LEADS.values()),
            "deals_lost": sum((l.get("deal") or {}).get("stage") == "closed_lost" for l in LEADS.values()),
            "won_revenue_eur": round(sum((l.get("deal") or {}).get("amount_eur") or 0 for l in LEADS.values()
                                         if (l.get("deal") or {}).get("stage") == "closed_won"), 2),
            "closed_automatically": sum(bool((l.get("deal") or {}).get("auto_close")) and (l.get("deal") or {}).get("stage") == "closed_won"
                                        for l in LEADS.values()),
            "median_days_meeting_to_won": _median_deal_days("qualification", "closed_won"),
            "median_hours_reply_to_handoff": _median_hours("replied", "handed_off"),
            "median_days_added_to_sent": _median_hours("new", "sent", days=True)}


def _median_deal_days(start: str, end: str) -> float | None:
    """Median days between two deal stages (from deal.stage_times)."""
    from datetime import datetime
    spans = sorted((datetime.fromisoformat(t[end]) - datetime.fromisoformat(t[start])).total_seconds() / 86400
                   for t in ((l.get("deal") or {}).get("stage_times") or {} for l in LEADS.values()) if start in t and end in t)
    if not spans:
        return None
    return round(spans[len(spans) // 2] if len(spans) % 2 else (spans[len(spans) // 2 - 1] + spans[len(spans) // 2]) / 2, 2)


def _median_hours(start: str, end: str, days: bool = False) -> float | None:
    """Median time between two stages over the leads that reached both (from stage_times)."""
    from datetime import datetime
    spans = sorted((datetime.fromisoformat(t[end]) - datetime.fromisoformat(t[start])).total_seconds() / 3600
                   for t in (l.get("stage_times") or {} for l in LEADS.values()) if start in t and end in t)
    if not spans:
        return None
    mid = spans[len(spans) // 2] if len(spans) % 2 else (spans[len(spans) // 2 - 1] + spans[len(spans) // 2]) / 2
    return round(mid / 24 if days else mid, 2)


@mcp.tool()
def get_event_log(since: str | None = None, stage: str | None = None) -> list[dict]:
    """The event log for the weekly review: what happened, including rep edits to drafts and every reply.

    since: ISO date-time, only events after it. stage: only one stage, e.g. approval for rep edits.
    """
    return [e for e in INTERACTIONS if (not since or e["timestamp"] >= since) and (not stage or e["stage"] == stage)]


@mcp.tool()
def save_proposal(stage: str, change: str, evidence: str, metric: str) -> dict:
    """Propose one rule change from the weekly review, for a person to approve. Never applied automatically.

    change: the exact rule or prompt line, old and new. evidence: counts and examples (lead ids) from the event log.
    metric: which number should move, and which must not drop.
    """
    proposal = {"id": f"P-{len(PROPOSALS) + 1:03d}", "created_at": now(), "stage": stage, "change": change,
                "evidence": evidence, "metric": metric, "status": "proposed", "decided_by": None, "decided_at": None}
    PROPOSALS.append(proposal)
    return {"ok": True, "proposal": proposal, "next": "post it in #sales-desk and wait for a person to approve or reject it"}


@mcp.tool()
def get_changes(status: str | None = None) -> list[dict]:
    """Rule changes proposed so far, with their status (proposed, approved, rejected), newest last."""
    return [p for p in PROPOSALS if not status or p["status"] == status]


@mcp.tool()
def decide_proposal(proposal_id: str, approve: bool, by: str, note: str = "") -> dict:
    """A person approves or rejects a proposed rule change. This tool always asks a person first.

    An approved change is applied by a person as a new SKILL.md or prompt line in a PR, with a new prompt_version.
    """
    for p in PROPOSALS:
        if p["id"] == proposal_id:
            p.update(status="approved" if approve else "rejected", decided_by=by, decided_at=now(), note=note)
            return {"ok": True, "proposal": p}
    return {"ok": False, "error": f"unknown proposal {proposal_id}"}
