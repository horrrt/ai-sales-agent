
"""Stream 2: Outreach tools. Owner: JD.

Week 1: In-memory sandbox implementation.
No real emails are sent.
"""

from ..app import mcp
from .. import sim
import hashlib
from datetime import datetime, timedelta, timezone

from ..decide import decide
from ..store import BRIEF, LEADS, OUTBOX, log_event, now, set_stage

SANDBOX_OUTBOX = OUTBOX  # kept in the store, so a restart doesn't lose sent emails


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

    previous = lead.get("outreach") or {}
    # A new draft replaces the old one and needs a new approval. Send history and follow-up count carry over.
    lead["outreach"] = {k: v for k, v in previous.items() if k in ("sent_at", "follow_ups", "sent_count")}
    lead["outreach"].update({"draft_subject": subject, "draft_body": body})
    set_stage(lead, "drafted")
    log_event(lead_id, "outreach", "success", f"draft saved: {subject!r}")

    return {"ok": True, "lead_id": lead_id, "next": "record_approval (the rep approves, edits or rejects)"}


def _draft_hash(subject: str, body: str) -> str:
    return hashlib.sha256(f"{subject}\n\n{body}".encode()).hexdigest()[:16]


@mcp.tool()
def record_approval(lead_id: str, subject: str, body: str, decision: str = "approve", note: str = "", by: str = "rep") -> dict:
    """The human check: a rep approves, edits or rejects the saved draft. This tool always asks a person first.

    Call it with the current draft's subject and body. The person sees the whole email and can accept it,
    edit the subject or body before accepting, or reject it. An edited email is saved as the new draft and
    counts as approved. The original and the edit are kept in the event log, so the weekly review can learn
    from them. send_email only sends a draft that has an approval.
    """
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": "unknown_lead"}
    outreach = lead.get("outreach") or {}
    if lead.get("stage") != "drafted" or not outreach.get("draft_body"):
        return {"ok": False, "error": "no_draft_to_approve"}
    if decision not in ("approve", "edit", "reject"):
        return {"ok": False, "error": "decision must be approve, edit or reject"}
    original = {"subject": outreach["draft_subject"], "body": outreach["draft_body"]}
    if decision == "reject":
        outreach["approval"] = {"decision": "reject", "by": by, "at": now(), "note": note,
                                "draft_hash": _draft_hash(original["subject"], original["body"])}
        log_event(lead_id, "approval", "failure", f"rejected: {note}", by=by, draft=original)
        return {"ok": True, "decision": "reject", "next": "redraft with the rep's note, then save_draft and record_approval again"}
    edited = (subject.strip(), body.strip()) != (original["subject"].strip(), original["body"].strip())
    if edited:
        outreach["draft_subject"], outreach["draft_body"] = subject, body
        decision = "edit"
    outreach["approval"] = {"decision": decision, "by": by, "at": now(), "note": note,
                            "draft_hash": _draft_hash(outreach["draft_subject"], outreach["draft_body"])}
    set_stage(lead, "approved")
    log_event(lead_id, "approval", "success", "approved with edits" if edited else "approved", by=by,
              draft=original, edited_to={"subject": subject, "body": body} if edited else None)
    return {"ok": True, "decision": decision, "next": "send_email"}


@mcp.tool()
def send_email(lead_id: str) -> dict:
    """Send the approved draft to the lead's sandbox inbox. Refuses a draft no person has approved."""

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

    if not _has_text(outreach.get("draft_subject")) or not _has_text(outreach.get("draft_body")):
        return {"ok": False, "error": "no_draft_ready"}

    approval = outreach.get("approval") or {}
    if (lead.get("stage") != "approved" or approval.get("decision") not in ("approve", "edit")
            or approval.get("draft_hash") != _draft_hash(outreach["draft_subject"], outreach["draft_body"])):
        return {"ok": False, "error": "not_approved", "next": "record_approval first: a person has to approve this exact draft"}

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

    is_follow_up = bool(outreach.get("sent_at"))
    outreach["follow_ups"] = outreach.get("follow_ups", 0) + int(is_follow_up)
    outreach["sent_count"] = outreach.get("sent_count", 0) + 1
    outreach["sent_at"] = sent_at
    outreach.pop("approval", None)  # an approval covers one send
    outreach["follow_up_due"] = _in_days(FOLLOW_UP_DAYS[outreach["follow_ups"]]) if outreach["follow_ups"] < MAX_FOLLOW_UPS else None
    set_stage(lead, "sent")
    log_event(lead_id, "outreach", "success", "follow-up sent" if is_follow_up else "sent", channel="email")

    return {"ok": True, "sandbox": True, "sent_at": sent_at, "follow_up_due": outreach["follow_up_due"]}


@mcp.tool()
def get_replies(since: str | None = None) -> list[dict]:
    """New prospect replies from the sandbox inbox (replies read_reply already handled are left out).

    A prospect who ignores the email has no reply here.
    """
    replies = []
    for m in SANDBOX_OUTBOX:
        if m.get("handled") or (since and m.get("sent_at", "") < since):
            continue
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
    set_stage(LEADS[lead_id], "replied")
    log_event(lead_id, "reply", "success", f"labelled {label}", channel="email")

    return {"ok": True, "lead_id": lead_id, "label": label}


MAX_FOLLOW_UPS = 2
FOLLOW_UP_DAYS = {0: 3, 1: 7}  # days after the first email, then after the first follow-up
NO_REPLY_AFTER_DAYS = 5         # after the last follow-up


def _in_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


REPLY_OPTIONS = {
    "positive": "Interested: agrees to talk, asks for a meeting, or asks how to buy or start",
    "negative": "Not interested, or declines (including because of price or an existing tool)",
    "not_now": "Interested in principle but asks to come back later",
    "wrong_person": "Says they are not the right contact or points to a colleague",
    "out_of_office": "An automatic absence reply",
    "unsubscribe": "Asks to stop emailing or to be removed",
}
WHY_NEGATIVE = {
    "has_competitor": "They already use or are choosing another tool",
    "price": "Too expensive or no budget",
    "negative": "Not interested, for any other reason",
}


@mcp.tool()
def read_reply(lead_id: str, reply_body: str) -> dict:
    """d1 reads a reply: one label, then the exit for that label is applied and logged.

    positive: continue (check_small_deal if auto-close is on, otherwise assign_rep).
    not_now and out_of_office: stop for now, follow up later (not lost).
    negative: lost, with the reason (has_competitor, price or negative).
    wrong_person: lost (wrong_person); the referral is in the note for a person to follow up.
    unsubscribe: lost (negative) and never contacted again.
    """
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": "unknown_lead"}
    if not (lead.get("outreach") or {}).get("sent_at"):
        return {"ok": False, "error": "no email was sent to this lead"}
    pending = [m for m in SANDBOX_OUTBOX if m["lead_id"] == lead_id and m.get("reply") and not m.get("handled")]
    if lead.get("stage") == "replied" and not pending:
        return {"ok": True, "label": (lead.get("outreach") or {}).get("reply_label"), "already_handled": True, "next": "stop"}
    d = decide("Which kind of reply is this, from the prospect to our sales email?", reply_body, REPLY_OPTIONS)
    if d["answer"] is None:
        return {"ok": True, "label": None, "decision": d, "next": "Ask a person to label this reply"}
    label = d["answer"]
    for m in pending:  # this reply is handled; get_replies won't return it again
        m["handled"] = True
    out = lead.setdefault("outreach", {})
    out["reply_label"], out["reply_body"], out["follow_up_due"] = label, reply_body, None
    set_stage(lead, "replied")
    gate = {"question": "what kind of reply?", **d}
    if label == "positive":
        nxt = "check_small_deal" if (BRIEF.get("auto_close") or {}).get("enabled") else "assign_rep"
        log_event(lead_id, "reply", "success", "positive reply", channel="email", decision=gate)
        return {"ok": True, "label": label, "decision": d, "next": nxt}
    if label in ("not_now", "out_of_office"):
        out["follow_up_due"] = _in_days(90 if label == "not_now" else 7)
        log_event(lead_id, "reply", "skipped", f"{label}: follow up on {out['follow_up_due'][:10]}", channel="email", decision=gate)
        return {"ok": True, "label": label, "decision": d, "follow_up_due": out["follow_up_due"], "next": "stop for now"}
    if label == "negative":
        why = decide("Why does the prospect decline?", reply_body, WHY_NEGATIVE)
        reason = why["answer"] or "negative"
    else:
        reason = {"wrong_person": "wrong_person", "unsubscribe": "negative"}[label]
    lead.update(status="lost", loss_reason=reason)
    if label == "unsubscribe":
        lead["do_not_contact"] = True
    log_event(lead_id, "reply", "failure", f"{label}: {reply_body[:200]}", channel="email", loss_reason=reason, decision=gate)
    return {"ok": True, "label": label, "loss_reason": reason, "decision": d,
            "next": "stop" + ("; a person may follow up the referral" if label == "wrong_person" else "")}


@mcp.tool()
def due_follow_ups() -> list[dict]:
    """Leads whose follow-up is due now, and leads that just ran out of follow-ups (marked lost: no_reply)."""
    now_dt = datetime.now(timezone.utc)
    due = []
    for lead in LEADS.values():
        out = lead.get("outreach") or {}
        if lead.get("status") != "active" or lead.get("do_not_contact") or not out.get("sent_at"):
            continue
        if out.get("follow_up_due") and datetime.fromisoformat(out["follow_up_due"]) <= now_dt:
            due.append({"lead_id": lead["lead_id"], "follow_up": out.get("follow_ups", 0) + 1,
                        "next": "write a short follow-up (save_draft), then record_approval and send_email"})
        elif (out.get("follow_ups", 0) >= MAX_FOLLOW_UPS and not out.get("reply_label")
              and datetime.fromisoformat(out["sent_at"]) + timedelta(days=NO_REPLY_AFTER_DAYS) <= now_dt):
            lead.update(status="lost", loss_reason="no_reply")
            log_event(lead["lead_id"], "outreach", "failure", "no reply after all follow-ups", loss_reason="no_reply")
            due.append({"lead_id": lead["lead_id"], "status": "lost", "loss_reason": "no_reply"})
    return due
