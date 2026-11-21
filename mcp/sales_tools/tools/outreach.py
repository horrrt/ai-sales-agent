"""Stream 2: outreach. Owner: JD.

SANDBOX: send_email never sends real email. Replies come from the simulated prospects (simulator/, Gyula).
"""
from ..app import mcp
from .. import sim
from ..store import LEADS, now

SANDBOX_OUTBOX: list[dict] = []


@mcp.tool()
def save_draft(lead_id: str, subject: str, body: str) -> dict:
    """Save an email draft for a lead."""
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    LEADS[lead_id]["outreach"] = {"draft_subject": subject, "draft_body": body}
    LEADS[lead_id]["stage"] = "drafted"
    return {"ok": True}


@mcp.tool()
def send_email(lead_id: str) -> dict:
    """Send the saved draft to the lead's sandbox inbox. Needs approval in Fleet."""
    lead = LEADS.get(lead_id)
    if not lead or "outreach" not in lead:
        return {"error": "no draft saved for this lead"}
    SANDBOX_OUTBOX.append({"lead_id": lead_id, "to": lead["person"]["email"], **lead["outreach"], "sent_at": now()})
    lead["outreach"]["sent_at"] = now()
    lead["stage"] = "sent"
    return {"ok": True, "sandbox": True}


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
    """Label a reply: positive, negative, not_now, wrong_person, out_of_office or unsubscribe."""
    if lead_id not in LEADS:
        return {"error": f"unknown lead {lead_id}"}
    LEADS[lead_id].setdefault("outreach", {})["reply_label"] = label
    LEADS[lead_id]["stage"] = "replied"
    return {"ok": True}
