"""Stream 1: targets and research. Owner: Kyriakos.

STUB: returns fake data in the right shape. Replace with Clay and web research.
"""
from ..app import mcp
from .. import sim
from ..store import LEADS


@mcp.tool()
def get_targets_from_clay(customer_id: str, limit: int = 10) -> list[dict]:
    """Get targets from Clay that match the customer's ICP. STUB: returns the sample leads."""
    return [l for l in LEADS.values() if l["customer_id"] == customer_id][:limit]


@mcp.tool()
def research_target(lead_id: str) -> dict:
    """Research one person and company (website, blog, LinkedIn via Clay). STUB: returns a fixed profile."""
    lead = LEADS.get(lead_id)
    if not lead:
        return {"error": f"unknown lead {lead_id}"}
    profile = sim.research(lead_id) or {
        "summary": f"STUB: {lead['company']['name']} is a {lead['company']['industry']} company with {lead['company']['employees']} employees.",
        "facts": [{"fact": "STUB: recently hired two account executives", "source_url": "https://example.com"}],
        "enough_to_personalize": True,
    }
    # Save it on the lead: positioning and outreach read research from get_lead, not from this return value.
    lead["research"] = profile
    lead["stage"] = "researched"
    return profile
