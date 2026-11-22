"""Stream 1 positioning. Owner: Gyula.

The positioner subagent writes the angle itself (it's an LLM job). This tool checks it and stores it.
The checks match what the outreach readiness check (JD) expects downstream: a real angle, evidence that
comes from research.facts, and a confidence in [0, 1].
"""
from ..app import mcp
from ..store import LEADS, set_stage
from .positioning_checks import check_positioning


@mcp.tool()
def save_positioning(lead_id: str, angle: str, evidence: list[str], confidence: float, variant: str = "research") -> dict:
    """Save the positioning angle for a lead. evidence must be fact strings copied from the lead's research.facts.

    variant is the A/B arm: research (default) or template.
    """
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": f"unknown lead {lead_id}"}
    problems = check_positioning(lead, angle, evidence, confidence)
    if variant not in ("research", "template"):
        problems.append("variant must be research or template")
    if problems:
        return {"ok": False, "error": "invalid_positioning", "problems": problems}
    lead["positioning"] = {"angle": angle.strip(), "evidence": [e.strip() for e in evidence], "confidence": float(confidence),
                          "variant": variant}
    set_stage(lead, "positioned")
    return {"ok": True, "lead_id": lead_id, "low_confidence": confidence < 0.4}
