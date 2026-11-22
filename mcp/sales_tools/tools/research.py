"""Stages 1 and 2 of the pipeline: find leads and research them. Owner: Kyriakos.

1. Find leads: find_leads lists candidates from the lead source, add_lead asks d1 "fits our ICP?"
   and puts the lead in the lead sheet (skipping duplicates), or records why it was skipped.
2. Research: next_lead picks the next lead, research_target researches it and saves the profile,
   check_ready_to_write asks d1 "enough to write?" per required fact. Missing info: research again,
   at most 2 retries, then the lead goes on hold (not lost).

STUB: the lead source is the sample leads or the personas (SALES_LEADS=personas). Swap find_leads for
Clay, Salesforce or a web search agent, and research_target for real web research; the gates stay.
Screenshots of the company site (the diagram's VLM step) are not built yet.
"""
import copy

from .. import sim
from ..app import mcp
from ..decide import decide
from ..store import BRIEF, LEADS, SOURCE, log_event, now, set_stage

MAX_RESEARCH_RETRIES = 2


def _key(lead: dict) -> tuple[str, str]:
    return ((lead.get("person") or {}).get("email", "").lower(), (lead.get("company") or {}).get("domain", "").lower())


def _icp_text() -> dict:
    icp = BRIEF.get("icp", {})
    return {"countries": icp.get("countries"), "employees": [icp.get("employees_min"), icp.get("employees_max")],
            "industries": icp.get("industries"), "titles": icp.get("titles"), "exclusions": BRIEF.get("exclusions")}


@mcp.tool()
def find_leads(customer_id: str, limit: int = 10) -> list[dict]:
    """Search the lead source for candidates for this customer that aren't in the lead sheet yet.

    Pass each one to add_lead; nothing is saved until then.
    """
    known = {k for lead in LEADS.values() for k in _key(lead) if k}
    out = []
    for cand in SOURCE.values():
        if cand.get("customer_id") != customer_id or any(k and k in known for k in _key(cand)):
            continue
        out.append({k: copy.deepcopy(v) for k, v in cand.items() if k in ("lead_id", "customer_id", "source", "source_id", "person", "company")})
        if len(out) >= limit:
            break
    return out


@mcp.tool()
def add_lead(lead_id: str) -> dict:
    """d1 gate "fits our ICP?" for one candidate from find_leads, then add it to the lead sheet.

    Returns status added, skipped (not a fit, recorded with loss reason wrong_company_fit),
    duplicate (already in the sheet) or needs_human (no decision could be made).
    """
    cand = SOURCE.get(lead_id)
    if cand is None:
        return {"ok": False, "error": f"unknown candidate {lead_id}; use a lead_id from find_leads"}
    if lead_id in LEADS or any(k and k in {kk for l in LEADS.values() for kk in _key(l)} for k in _key(cand)):
        return {"ok": True, "status": "duplicate", "lead_id": lead_id}
    question = ("Does this company and contact fit the ideal customer profile: country, company size, industry and "
                "the contact's title all inside the ICP, and the company not on the exclusion list?")
    evidence = {"icp": _icp_text(), "candidate": {"person": cand.get("person"), "company": cand.get("company")}}
    d = decide(question, evidence)
    lead = copy.deepcopy(cand)
    lead.pop("research", None)
    if d["answer"] is None:
        return {"ok": True, "status": "needs_human", "lead_id": lead_id, "decision": d,
                "next": "Ask a person whether this lead fits the ICP"}
    if not d["answer"]:
        lead.update(status="lost", loss_reason="wrong_company_fit", created_at=now())
        set_stage(lead, "new")
        LEADS[lead_id] = lead
        log_event(lead_id, "targeting", "skipped", f"not a fit: {d['reason']}", loss_reason="wrong_company_fit",
                  decision={"question": "fits our ICP?", **d})
        return {"ok": True, "status": "skipped", "lead_id": lead_id, "decision": d}
    lead.update(status="active", loss_reason=None, created_at=now())
    set_stage(lead, "new")
    lead.setdefault("source", "salesforce" if lead.get("source_id") else "manual")
    LEADS[lead_id] = lead
    log_event(lead_id, "targeting", "success", "added to the lead sheet", decision={"question": "fits our ICP?", **d})
    return {"ok": True, "status": "added", "lead_id": lead_id, "decision": d, "next": "research_target"}


@mcp.tool()
def next_lead(customer_id: str) -> dict:
    """The next lead to work on: the oldest active lead in the sheet that hasn't been drafted yet."""
    for lead in LEADS.values():
        if lead.get("customer_id") == customer_id and lead.get("status") == "active" and lead.get("stage") in ("new", "researched", "positioned"):
            return {"lead_id": lead["lead_id"], "stage": lead["stage"],
                    "next": {"new": "research_target", "researched": "check_ready_to_write", "positioned": "save_draft"}[lead["stage"]]}
    return {"lead_id": None, "next": "find_leads"}


@mcp.tool()
def get_targets_from_clay(customer_id: str, limit: int = 10) -> list[dict]:
    """Leads in the lead sheet for this customer that still need research (stage new, status active)."""
    return [l for l in LEADS.values() if l["customer_id"] == customer_id and l.get("stage") == "new"
            and l.get("status") == "active"][:limit]


@mcp.tool()
def research_target(lead_id: str) -> dict:
    """Research one person and company (website, blog, news) and save the profile on the lead."""
    lead = LEADS.get(lead_id)
    if not lead:
        return {"error": f"unknown lead {lead_id}"}
    attempts = (lead.get("research") or {}).get("attempts", 0)
    # The personas, or a lead-source entry that carries its own research (test fixtures), else the stub.
    profile = sim.research(lead_id) or copy.deepcopy((SOURCE.get(lead_id) or {}).get("research")) or {
        "summary": f"STUB: {lead['company']['name']} is a {lead['company']['industry']} company with {lead['company']['employees']} employees.",
        "facts": [{"fact": "STUB: recently hired two account executives", "source_url": "https://example.com"}],
        "enough_to_personalize": True,
    }
    # Save it on the lead: positioning and outreach read research from get_lead, not from this return value.
    lead["research"] = {**profile, "attempts": attempts}
    set_stage(lead, "researched")
    return profile


REQUIRED_FACTS = {
    "decision_maker": "Is the contact someone who can decide on buying this offer (their title is in the ICP titles or clearly owns this budget)?",
    "trigger": "Is there at least one sourced fact (with a source URL) that points to a buying trigger or pain the customer's offer answers?",
    "specific": "Is at least one sourced fact specific to this company or person, not a generic statement about its industry?",
}


@mcp.tool()
def check_ready_to_write(lead_id: str) -> dict:
    """d1 gate "enough to write?": one yes/no per required fact on the saved research.

    All yes: ready, go to positioning. Any no: research again (research_target), at most 2 retries;
    after that the lead goes on hold. On hold is not lost: no loss reason, a person can add research later.
    """
    lead = LEADS.get(lead_id)
    if not lead:
        return {"ok": False, "error": f"unknown lead {lead_id}"}
    research = lead.get("research")
    if not research:
        return {"ok": False, "error": "no research saved; call research_target first"}
    evidence = {"icp_titles": BRIEF.get("icp", {}).get("titles"), "icp_triggers": BRIEF.get("icp", {}).get("triggers"),
                "offer": BRIEF.get("offer"), "person": lead.get("person"), "company": lead.get("company"),
                "research": {k: v for k, v in research.items() if k != "attempts"}}
    answers = {name: decide(q, evidence) for name, q in REQUIRED_FACTS.items()}
    if any(a["answer"] is None for a in answers.values()):
        return {"ok": True, "ready": None, "answers": answers, "next": "Ask a person whether research is enough"}
    missing = [name for name, a in answers.items() if not a["answer"]]
    if not missing:
        log_event(lead_id, "research", "success", "enough to write", decision={"question": "enough to write?", "answers": answers})
        return {"ok": True, "ready": True, "answers": answers, "next": "positioning"}
    research["attempts"] = research.get("attempts", 0) + 1
    if research["attempts"] > MAX_RESEARCH_RETRIES:
        lead["status"] = "on_hold"
        log_event(lead_id, "research", "skipped", f"on hold after {MAX_RESEARCH_RETRIES} retries, missing: {', '.join(missing)}",
                  decision={"question": "enough to write?", "answers": answers})
        return {"ok": True, "ready": False, "missing": missing, "status": "on_hold", "answers": answers,
                "next": "stop: lead is on hold for a person to add research"}
    log_event(lead_id, "research", "failure", f"missing: {', '.join(missing)} (attempt {research['attempts']})",
              decision={"question": "enough to write?", "answers": answers})
    return {"ok": True, "ready": False, "missing": missing, "attempts": research["attempts"], "answers": answers,
            "next": "research_target again, looking for the missing facts"}
