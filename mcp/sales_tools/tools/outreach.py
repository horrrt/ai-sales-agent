
"""Stream 2: Outreach tools. Owner: JD.

Week 1: In-memory sandbox implementation.
No real emails are sent.
"""

from ..app import mcp
from .. import sim
from ..store import BRIEF, LEADS, now

SANDBOX_OUTBOX: list[dict] = []


def _has_text(value) -> bool:
    """Reject missing, empty and TODO placeholder strings."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not value.strip().upper().startswith("TODO")
    )


def _missing_required_fields(lead: dict) -> list[str]:
    """Check minimum data requirements before drafting."""

    missing = []

    person = lead.get("person") or {}
    company = lead.get("company") or {}
    research = lead.get("research") or {}
    positioning = lead.get("positioning") or {}

    required = {
        "lead_id": lead.get("lead_id"),
        "customer_id": lead.get("customer_id"),
        "person.name": person.get("name"),
        "person.email": person.get("email"),
        "person.title": person.get("title"),
        "company.name": company.get("name"),
        "company.industry": company.get("industry"),
        "company.country": company.get("country"),
        "positioning.angle": positioning.get("angle"),
        "customer.offer": BRIEF.get("offer"),
        "customer.product_info": BRIEF.get("product_info"),
        "customer.tone": BRIEF.get("tone"),
    }

    for field, value in required.items():
        if not _has_text(value):
            missing.append(field)

    if BRIEF.get("customer_id") != lead.get("customer_id"):
        missing.append("customer_id mismatch")

    if research.get("enough_to_personalize") is not True:
        missing.append("research.enough_to_personalize")

    facts = research.get("facts") or []
    valid_facts = [
        fact for fact in facts
        if isinstance(fact, dict)
        and _has_text(fact.get("fact"))
        and _has_text(fact.get("source_url"))
        and fact["source_url"].startswith(("https://", "http://"))
    ]

    if not valid_facts:
        missing.append("research.facts")

    evidence = positioning.get("evidence") or []
    if not any(_has_text(item) for item in evidence):
        missing.append("positioning.evidence")

    return missing


@mcp.tool()
def save_draft(lead_id: str, subject: str, body: str) -> dict:
    """Save a draft only when required lead data is complete."""

    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": "unknown_lead"}

    missing = _missing_required_fields(lead)

    if missing:
        return {
            "ok": False,
            "status": "insufficient_data",
            "missing_fields": missing,
            "suggested_next_stage": "research_or_positioning",
        }

    if not _has_text(subject) or not _has_text(body):
        return {"ok": False, "error": "empty_draft"}

    lead["outreach"] = {
        "draft_subject": subject,
        "draft_body": body,
    }
    lead["stage"] = "drafted"

    return {"ok": True, "lead_id": lead_id}


@mcp.tool()
def send_email(lead_id: str) -> dict:
    """Record a sandbox send. Fleet approval is also required."""

    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": "unknown_lead"}

    missing = _missing_required_fields(lead)
    if missing:
        return {
            "ok": False,
            "status": "insufficient_data",
            "missing_fields": missing,
        }

    outreach = lead.get("outreach") or {}

    if (
        lead.get("stage") != "drafted"
        or not _has_text(outreach.get("draft_subject"))
        or not _has_text(outreach.get("draft_body"))
    ):
        return {"ok": False, "error": "no_draft_ready"}

    recipient = lead["person"]["email"]

    # Week 1 sandbox: only reserved .test email domains.
    if (
        recipient.count("@") != 1
        or not recipient.split("@")[0]
        or not recipient.split("@")[1].lower().endswith(".test")
    ):
        return {"ok": False, "error": "not_sandbox_recipient"}

    sent_at = now()

    SANDBOX_OUTBOX.append({
        "lead_id": lead_id,
        "to": recipient,
        "draft_subject": outreach["draft_subject"],
        "draft_body": outreach["draft_body"],
        "sent_at": sent_at,
    })

    outreach["sent_at"] = sent_at
    lead["stage"] = "sent"

    return {"ok": True, "sandbox": True, "sent_at": sent_at}


@mcp.tool()
def get_replies(since: str | None = None) -> list[dict]:
    """Get prospect replies from the sandbox inbox. A prospect who ignores the email has no reply here."""
    replies = []
    for m in SANDBOX_OUTBOX:
        if "reply" not in m:  # ask each prospect once per sent email
            if sim.ENABLED:
                m["reply"] = sim.reply(m["lead_id"], m.get("draft_subject", ""), m.get("draft_body", ""))
            else:  # STUB without simulated prospects: every sent email gets a canned positive reply
                m["reply"] = {"body": "STUB: Sounds interesting, can we talk next week?"}
        if m["reply"]:
            replies.append({"lead_id": m["lead_id"], "from": m["to"], "body": m["reply"]["body"]})
    return replies


@mcp.tool()
def save_reply_label(lead_id: str, label: str) -> dict:
    """Save the classification of a prospect's reply."""

    allowed = {
        "positive",
        "negative",
        "not_now",
        "wrong_person",
        "out_of_office",
        "unsubscribe",
    }

    if lead_id not in LEADS:
        return {"ok": False, "error": "unknown_lead"}

    if label not in allowed:
        return {"ok": False, "error": "invalid_label"}

    LEADS[lead_id].setdefault("outreach", {})["reply_label"] = label
    LEADS[lead_id]["stage"] = "replied"

    return {"ok": True, "lead_id": lead_id, "label": label}
