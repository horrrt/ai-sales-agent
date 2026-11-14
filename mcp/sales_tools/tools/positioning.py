"""Stream 1 positioning. Owner: Gyula.

The positioner subagent writes the angle itself (it's an LLM job). This tool only stores it.
"""
from ..app import mcp
from ..store import LEADS


@mcp.tool()
def save_positioning(lead_id: str, angle: str, evidence: list[str], confidence: float) -> dict:
    """Save the positioning angle for a lead, with the evidence from the research profile."""
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    LEADS[lead_id]["positioning"] = {"angle": angle, "evidence": evidence, "confidence": confidence}
    LEADS[lead_id]["stage"] = "positioned"
    return {"ok": True}
