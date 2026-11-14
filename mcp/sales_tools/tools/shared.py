"""Shared tools every stage uses. Owner: Ayaan (stream 4)."""
import json
from pathlib import Path

from ..app import mcp
from ..store import BRIEF, INTERACTIONS, LEADS, now

REASONS = {r["id"] for r in json.loads((Path(__file__).resolve().parents[3] / "schemas" / "loss_reasons.json").read_text())["reasons"]}


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
    """Merge fields into a lead (see schemas/lead.schema.json)."""
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    LEADS[lead_id].update(fields)
    return {"ok": True, "lead": LEADS[lead_id]}


@mcp.tool()
def log_interaction(lead_id: str, stage: str, outcome: str, message: str = "", channel: str = "internal", loss_reason: str | None = None) -> dict:
    """Log one stage for one lead (see schemas/interaction.schema.json). Call this for every stage, including failures."""
    entry = {"timestamp": now(), "lead_id": lead_id, "stage": stage, "channel": channel,
             "message": message, "outcome": outcome, "loss_reason": loss_reason, "trace_id": None}
    INTERACTIONS.append(entry)
    return {"ok": True}


@mcp.tool()
def tag_loss_reason(lead_id: str, reason: str, evidence: str) -> dict:
    """Mark a lead lost with exactly one reason from schemas/loss_reasons.json."""
    if reason not in REASONS:
        return {"error": f"unknown reason {reason}", "allowed": sorted(REASONS)}
    LEADS.setdefault(lead_id, {}).update({"status": "lost", "loss_reason": reason})
    INTERACTIONS.append({"timestamp": now(), "lead_id": lead_id, "stage": "close", "channel": "internal",
                         "message": evidence, "outcome": "failure", "loss_reason": reason, "trace_id": None})
    return {"ok": True}


@mcp.tool()
def get_funnel_metrics() -> dict:
    """Counts per stage and per loss reason. STUB: counts the in-memory log only."""
    by_stage: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for i in INTERACTIONS:
        by_stage[i["stage"]] = by_stage.get(i["stage"], 0) + 1
        if i["loss_reason"]:
            by_reason[i["loss_reason"]] = by_reason.get(i["loss_reason"], 0) + 1
    return {"interactions_by_stage": by_stage, "losses_by_reason": by_reason}
