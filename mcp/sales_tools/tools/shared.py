"""Shared tools every stage uses. Owner: Ayaan (stream 4)."""
import json
from pathlib import Path

from ..app import mcp
from ..store import BRIEF, INTERACTIONS, LEADS, now

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
    """Merge fields into a lead (see schemas/lead.schema.json)."""
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    LEADS[lead_id].update(fields)
    return {"ok": True, "lead": LEADS[lead_id]}


@mcp.tool()
def log_interaction(lead_id: str, stage: str, outcome: str, message: str = "", channel: str = "internal",
                    loss_reason: str | None = None, prompt_version: str | None = None, variant: str | None = None) -> dict:
    """Log one stage for one lead (see schemas/interaction.schema.json). Call this for every stage, including failures.

    stage: research, positioning, outreach, reply, handoff or close. outcome: success, failure or skipped.
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
    entry = {"timestamp": now(), "lead_id": lead_id, "stage": stage, "channel": channel, "message": message,
             "outcome": outcome, "loss_reason": loss_reason, "prompt_version": prompt_version, "variant": variant,
             "trace_id": None}
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
