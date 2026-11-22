
"""Stream 2: Outreach tools. Owner: JD.

Sandbox only: no real emails are sent, and every recipient (to and cc) must be on a .test domain.

Every email is a message record in the store (OUTBOX for ours, INBOX for theirs) with from, to, cc,
subject, body, thread_id and its time, so a reply is always read next to the email it answers.

- One thread per lead. A follow-up or an answer goes into the same thread ("Re: <subject>") and keeps
  everyone the prospect copied in. The person approving sees the final subject, to and cc: the approval
  covers all of them, and send_email refuses anything else.
- Business hours: an email goes out 08:00-17:00 on a working day in the lead's country. Outside that it
  is held (status scheduled) and goes out at the next 08:00. Holidays are not handled, only weekends.
- Follow-ups are spaced in working days: 3 after the first email, 5 after the first follow-up.
- read_reply records who wrote and who was copied in. A reply from someone other than the lead is
  flagged; new people in the conversation become contacts on the lead; a handover to a copied-in
  colleague keeps the lead active and addresses the next email to that colleague.
"""

from ..app import mcp
from .. import sim
import hashlib
import re
from datetime import datetime, time, timedelta, timezone
from email.utils import formataddr, parseaddr
from zoneinfo import ZoneInfo

from .. import access
from ..decide import decide
from ..workdays import COUNTRY_TZ as _TZ, add_working_days as _wd_add, holiday as _holiday, is_working_day
from ..store import BRIEF, INBOX, LEADS, OUTBOX, REPS, ask_person, clock, log_event, now, set_stage

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



COUNTRY_TZ = _TZ
DEFAULT_TZ = "Europe/Copenhagen"  # the sender's office, for a country not in the table
SEND_WINDOW = (8, 17)             # local hours: the first send at 08:00, the last before 17:00
MAX_FOLLOW_UPS = 2
FOLLOW_UP_WORKING_DAYS = {0: 3, 1: 5}  # after the first email, then after the first follow-up
NO_REPLY_AFTER_WORKING_DAYS = 5        # after the last follow-up


# --------------------------------------------------------------------------- addresses and time

def _addr(value: str) -> str:
    """The bare, lower-case address of "Name <a@b.test>" or "a@b.test"."""
    return parseaddr(value or "")[1].strip().lower()


def _is_sandbox(value: str) -> bool:
    a = _addr(value)
    return a.count("@") == 1 and bool(a.split("@")[0]) and a.split("@")[1].endswith(".test")


def _sender() -> dict:
    s = BRIEF.get("sender") or {}
    return {"name": s.get("name") or BRIEF.get("company_name") or "Sales",
            "email": s.get("email") or "outreach@sandbox.test", "timezone": s.get("timezone") or DEFAULT_TZ}


def _ours() -> set[str]:
    """Addresses on our side: the sender and the reps. They never become contacts."""
    return {_addr(_sender()["email"])} | {_addr(r["email"]) for r in REPS.values() if r.get("email")}


def _lead_tz(lead: dict) -> ZoneInfo:
    country = ((lead.get("company") or {}).get("country") or "").upper()
    try:
        return ZoneInfo(COUNTRY_TZ.get(country) or _sender()["timezone"])
    except Exception:  # an unknown zone name in the brief
        return ZoneInfo(DEFAULT_TZ)


def _country(lead: dict) -> str:
    return ((lead.get("company") or {}).get("country") or "").upper()


def _next_send_time(lead: dict, at: datetime) -> datetime | None:
    """None if `at` is inside business hours on a working day in the lead's country (weekends and public
    holidays there don't count), else the next 08:00 on a working day."""
    local, c = at.astimezone(_lead_tz(lead)), _country(lead)
    start, end = SEND_WINDOW
    if is_working_day(local.date(), c) and start <= local.hour < end:
        return None
    day = local.date() if (is_working_day(local.date(), c) and local.hour < start) else local.date() + timedelta(days=1)
    while not is_working_day(day, c):
        day += timedelta(days=1)
    return datetime.combine(day, time(start), local.tzinfo).astimezone(timezone.utc)


def _add_working_days(at: datetime, n: int, tz: ZoneInfo, *countries: str) -> datetime:
    return _wd_add(at, n, tz, *countries).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _when(m: dict) -> str:
    return m.get("sent_at") or m.get("scheduled_for") or m.get("received_at") or ""


# --------------------------------------------------------------------------- threads

def _messages(lead_id: str) -> list[dict]:
    """Every message with this lead, ours and theirs, oldest first."""
    return sorted([m for m in OUTBOX if m["lead_id"] == lead_id] + [m for m in INBOX if m["lead_id"] == lead_id],
                  key=lambda m: datetime.fromisoformat(_when(m)) if _when(m) else datetime.min.replace(tzinfo=timezone.utc))


def _delivered(lead_id: str) -> list[dict]:
    return [m for m in _messages(lead_id) if m["direction"] == "in" or m.get("status") == "sent"]


def _awaiting_reply(lead_id: str) -> bool:
    """We wrote last: no reply since our latest sent email."""
    msgs = _delivered(lead_id)
    return bool(msgs) and msgs[-1]["direction"] == "out"


def _message_id(lead_id: str, direction: str) -> str:
    box = OUTBOX if direction == "out" else INBOX
    return f"{lead_id}-{direction}-{sum(1 for m in box if m['lead_id'] == lead_id) + 1}"


def _public(m: dict) -> dict:
    keys = ("message_id", "thread_id", "lead_id", "direction", "from", "to", "cc", "subject", "body", "status",
            "sent_at", "scheduled_for", "received_at", "in_reply_to", "attachments", "from_lead")
    return {k: m[k] for k in keys if k in m}


def _add_contact(lead: dict, value: str, role: str, message_id: str | None) -> dict | None:
    """Remember someone new in the conversation. Returns the contact, or None for the lead and our side."""
    name, a = parseaddr(value or "")
    a = a.lower()
    if not a or a == _addr((lead.get("person") or {}).get("email")) or a in _ours():
        return None
    contacts = lead.setdefault("contacts", [])
    for c in contacts:
        if c["email"] == a:
            if role == "handover":
                c["role"] = "handover"
            return c
    c = {"name": name or None, "email": a, "role": role, "added_at": now(), "source_message_id": message_id}
    contacts.append(c)
    return c


def _deliver(m: dict, at: datetime) -> None:
    """The email actually goes out: bookkeeping on the lead happens here, for an immediate or a held send."""
    lead = LEADS[m["lead_id"]]
    out = lead.setdefault("outreach", {})
    if m.get("kind") in ("offer", "offer_reminder"):  # auto-close emails: the deal keeps their bookkeeping
        m.update(status="sent", sent_at=_iso(at))
        deal = lead.get("deal") or {}
        if m["kind"] == "offer":
            deal["offer_sent_at"] = m["sent_at"]
        else:
            deal["offer_reminders"] = deal.get("offer_reminders", 0) + 1
            deal["last_reminder_at"] = m["sent_at"]
        log_event(m["lead_id"], "deal", "success", f"{m['kind'].replace('_', ' ')} sent: {m['subject']!r}", channel="email",
                  message_id=m["message_id"])
        return
    chasing = _awaiting_reply(m["lead_id"])  # we wrote last and write again: a follow-up
    m.update(status="sent", sent_at=_iso(at))
    out["follow_ups"] = out.get("follow_ups", 0) + int(chasing)
    out["sent_count"] = out.get("sent_count", 0) + 1
    out["sent_at"] = m["sent_at"]
    out.pop("scheduled_for", None)
    out["follow_up_due"] = (_iso(_add_working_days(at, FOLLOW_UP_WORKING_DAYS[out["follow_ups"]], _lead_tz(lead), _country(lead)))
                            if out["follow_ups"] < MAX_FOLLOW_UPS else None)
    h = lead.get("handoff") or {}
    if h.get("follow_up_kind") == "rebook":  # this email is the rebooking follow-up
        h.update(follow_up_due=None, follow_up_kind="rebook_sent")
    if h.get("meeting_status") not in ("booked", "held"):
        set_stage(lead, "sent", at=m["sent_at"])
    kind = "follow-up sent" if chasing else ("reply sent" if out.get("reply_label") else "sent")
    log_event(m["lead_id"], "outreach", "success", f"{kind}: {m['subject']!r} to {', '.join(m['to'])}"
              + (f", cc {', '.join(m['cc'])}" if m["cc"] else ""), channel="email", message_id=m["message_id"])


def _release_held() -> list[dict]:
    """Send every held email whose time has come (called by the tools that read the mailbox)."""
    released = []
    for m in OUTBOX:
        if m.get("status") == "scheduled" and datetime.fromisoformat(m["scheduled_for"]) <= clock():
            _deliver(m, datetime.fromisoformat(m["scheduled_for"]))
            released.append(m)
    return released


# --------------------------------------------------------------------------- drafting and approval

def _draft_hash(subject: str, body: str, to=(), cc=()) -> str:
    rcpt = ",".join(sorted(_addr(a) for a in to)) + "|" + ",".join(sorted(_addr(a) for a in cc))
    return hashlib.sha256(f"{subject}\n\n{body}\n\n{rcpt}".encode()).hexdigest()[:16]


def _hash_of(outreach: dict) -> str:
    return _draft_hash(outreach["draft_subject"], outreach["draft_body"], outreach.get("draft_to") or [], outreach.get("draft_cc") or [])


def _dedupe(addrs, drop=()) -> list[str]:
    seen, out = {_addr(a) for a in drop}, []
    for a in addrs or []:
        if _addr(a) and _addr(a) not in seen:
            seen.add(_addr(a))
            out.append(a)
    return out


@mcp.tool()
def save_draft(lead_id: str, subject: str, body: str, cc: list[str] | None = None) -> dict:
    """Save a draft only when required lead data is complete.

    The draft is addressed for you: to the lead, or to whoever the conversation moved to (the colleague a
    lead handed over to, or the assistant who wrote back), with everyone the prospect copied in kept in cc.
    cc adds more people on top (for example a rep who asked to be copied in); sandbox .test addresses only.
    In an existing thread the subject becomes "Re: <first subject>" so the email stays in the thread.
    The result shows the final to, cc and subject: pass all of them to record_approval so the person sees them.
    """

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
    to = [previous.get("next_to") or lead["person"]["email"]]
    carried = previous.get("next_cc") or []
    all_cc = _dedupe(list(carried) + list(cc or []), drop=to)
    bad = [a for a in to + all_cc if not _is_sandbox(a)]
    if bad:
        return {"ok": False, "error": "not_sandbox_recipient", "addresses": bad}
    thread = _delivered(lead_id)
    in_reply_to = thread[-1]["message_id"] if thread else None
    if thread and previous.get("thread_subject"):
        if not subject.strip().lower().startswith("re:"):
            subject = f"Re: {previous['thread_subject']}"

    # A new draft replaces the old one and needs a new approval. Send history, thread and addressing carry over.
    keep = ("sent_at", "follow_ups", "sent_count", "thread_id", "thread_subject", "next_to", "next_cc",
            "reply_label", "reply_from", "reply_message_id", "handover_to")
    lead["outreach"] = {k: v for k, v in previous.items() if k in keep}
    lead["outreach"].update({"draft_subject": subject, "draft_body": body, "draft_to": to, "draft_cc": all_cc,
                             "draft_in_reply_to": in_reply_to})
    set_stage(lead, "drafted")
    log_event(lead_id, "outreach", "success", f"draft saved: {subject!r}")

    return {"ok": True, "lead_id": lead_id, "to": to, "cc": all_cc, "subject": subject, "in_reply_to": in_reply_to,
            "next": "record_approval with this subject, body, to and cc (the rep approves, edits or rejects)"}


@mcp.tool()
def record_approval(lead_id: str, subject: str, body: str, to: list[str] | None = None, cc: list[str] | None = None,
                    decision: str = "approve", note: str = "", by: str | None = None) -> dict:
    """The human check: a rep approves, edits or rejects the saved draft. This tool always asks a person first.

    Call it with the subject, body, to and cc that save_draft returned (cc may be an empty list), so the
    person sees exactly who gets the email. Approving needs both to and cc. The person can accept it, edit
    the subject, body, to or cc before accepting, or reject it. An edited email is saved as the new draft
    and counts as approved. The original and the edit are kept in the event log, so the weekly review can
    learn from them. send_email only sends a draft that has an approval, to exactly the approved recipients.
    by: who decided (rep_id, email or name). Juniors and viewers may not approve emails; left out, it is the
    lead's rep, or the brief's email approver.
    """
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": "unknown_lead"}
    outreach = lead.get("outreach") or {}
    if lead.get("stage") != "drafted" or not outreach.get("draft_body"):
        return {"ok": False, "error": "no_draft_to_approve"}
    if decision not in ("approve", "edit", "reject"):
        return {"ok": False, "error": "decision must be approve, edit or reject"}
    by = by or (lead.get("handoff") or {}).get("rep_id") or access.default_approver("emails")
    denied = access.check(by, "approve_email", lead_id=lead_id)
    if denied:
        return denied
    by = access.resolve(by)
    original = {"subject": outreach["draft_subject"], "body": outreach["draft_body"],
                "to": list(outreach.get("draft_to") or [lead["person"]["email"]]), "cc": list(outreach.get("draft_cc") or [])}
    if decision == "reject":
        outreach["approval"] = {"decision": "reject", "by": by, "at": now(), "note": note, "draft_hash": _hash_of(outreach)}
        log_event(lead_id, "approval", "failure", f"rejected: {note}", by=by, draft=original)
        return {"ok": True, "decision": "reject", "next": "redraft with the rep's note, then save_draft and record_approval again"}
    if to is None or cc is None:  # the person must see who gets the email, so the recipients are always in the ask
        return {"ok": False, "error": "recipients_missing", "to": original["to"], "cc": original["cc"],
                "next": "call record_approval again with to and cc exactly as save_draft returned them"}
    new_to = _dedupe(to)
    new_cc = _dedupe(cc, drop=new_to)
    bad = [a for a in new_to + new_cc if not _is_sandbox(a)]
    if bad or not new_to:
        return {"ok": False, "error": "not_sandbox_recipient", "addresses": bad or ["(no to)"]}
    edited = ((subject.strip(), body.strip()) != (original["subject"].strip(), original["body"].strip())
              or sorted(map(_addr, new_cc)) != sorted(map(_addr, original["cc"]))
              or sorted(map(_addr, new_to)) != sorted(map(_addr, original["to"])))
    if edited:
        outreach.update(draft_subject=subject, draft_body=body, draft_to=new_to, draft_cc=new_cc)
        decision = "edit"
    outreach["approval"] = {"decision": decision, "by": by, "at": now(), "note": note, "draft_hash": _hash_of(outreach)}
    set_stage(lead, "approved")
    log_event(lead_id, "approval", "success", "approved with edits" if edited else "approved", by=by,
              draft=original, edited_to={"subject": subject, "body": body, "to": new_to, "cc": new_cc} if edited else None)
    return {"ok": True, "decision": decision, "to": outreach.get("draft_to"), "cc": outreach["draft_cc"], "next": "send_email"}


# --------------------------------------------------------------------------- sending

@mcp.tool()
def send_email(lead_id: str) -> dict:
    """Send the approved draft, to exactly the approved to and cc, in the lead's thread. Sandbox inboxes only.

    Refuses a draft no person has approved. Outside 08:00-17:00 on a working day in the lead's country the
    email is held (status scheduled) and goes out at the next 08:00 there; nothing else to do.
    """
    _release_held()
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
    if outreach.get("scheduled_for"):
        return {"ok": False, "error": "already_scheduled", "scheduled_for": outreach["scheduled_for"]}
    if lead.get("do_not_contact"):
        return {"ok": False, "error": "do_not_contact"}

    if not _has_text(outreach.get("draft_subject")) or not _has_text(outreach.get("draft_body")):
        return {"ok": False, "error": "no_draft_ready"}

    approval = outreach.get("approval") or {}
    if (lead.get("stage") != "approved" or approval.get("decision") not in ("approve", "edit")
            or approval.get("draft_hash") != _hash_of(outreach)):
        return {"ok": False, "error": "not_approved", "next": "record_approval first: a person has to approve this exact draft"}

    to = list(outreach.get("draft_to") or [lead["person"]["email"]])
    cc = list(outreach.get("draft_cc") or [])
    bad = [a for a in to + cc if not _is_sandbox(a)]
    if bad:
        return {"ok": False, "error": "not_sandbox_recipient", "addresses": bad}

    sender = _sender()
    outreach.setdefault("thread_id", f"T-{lead_id}")
    outreach.setdefault("thread_subject", outreach["draft_subject"])
    at = clock()
    hold = _next_send_time(lead, at)
    m = {"message_id": _message_id(lead_id, "out"), "thread_id": outreach["thread_id"], "lead_id": lead_id,
         "direction": "out", "from": formataddr((sender["name"], sender["email"])), "to": to, "cc": cc,
         "subject": outreach["draft_subject"], "body": outreach["draft_body"],
         "in_reply_to": outreach.get("draft_in_reply_to"), "attachments": [],
         "status": "scheduled" if hold else "sending", "scheduled_for": _iso(hold) if hold else None, "sent_at": None}
    SANDBOX_OUTBOX.append(m)
    outreach.pop("approval", None)  # an approval covers one send
    if hold:
        outreach["scheduled_for"] = m["scheduled_for"]
        local = at.astimezone(_lead_tz(lead))
        why = _holiday(local.date(), _country(lead))
        log_event(lead_id, "outreach", "skipped", f"held until {m['scheduled_for']}: {local:%a %H:%M} in "
                  f"{(lead.get('company') or {}).get('country')} is " + (f"a public holiday ({why})" if why else "outside business hours"),
                  channel="email",
                  message_id=m["message_id"])
        return {"ok": True, "sandbox": True, "status": "scheduled", "scheduled_for": m["scheduled_for"],
                "local_time_now": local.isoformat(), "message_id": m["message_id"], "to": to, "cc": cc,
                "next": "nothing: it goes out at the next 08:00 on a working day in the lead's country"}
    _deliver(m, at)
    return {"ok": True, "sandbox": True, "status": "sent", "sent_at": m["sent_at"], "follow_up_due": outreach["follow_up_due"],
            "message_id": m["message_id"], "thread_id": m["thread_id"], "to": to, "cc": cc, "subject": m["subject"]}


def send_policy_email(lead_id: str, subject: str, body: str, kind: str) -> dict:
    """An email the auto-close policy sends without a per-email approval (the standard offer and its reminders).
    Same thread, same recipients and the same business hours as any other email."""
    _release_held()
    lead = LEADS[lead_id]
    out = lead.setdefault("outreach", {})
    if lead.get("do_not_contact"):
        return {"ok": False, "error": "do_not_contact"}
    to = [out.get("next_to") or lead["person"]["email"]]
    cc = list(out.get("next_cc") or [])
    bad = [a for a in to + cc if not _is_sandbox(a)]
    if bad:
        return {"ok": False, "error": "not_sandbox_recipient", "addresses": bad}
    thread = _delivered(lead_id)
    out.setdefault("thread_id", f"T-{lead_id}")
    if out.get("thread_subject") and not subject.lower().startswith("re:"):
        subject = f"Re: {out['thread_subject']}"
    out.setdefault("thread_subject", subject)
    sender, at = _sender(), clock()
    hold = _next_send_time(lead, at)
    m = {"message_id": _message_id(lead_id, "out"), "thread_id": out["thread_id"], "lead_id": lead_id, "direction": "out",
         "from": formataddr((sender["name"], sender["email"])), "to": to, "cc": cc, "subject": subject, "body": body,
         "in_reply_to": thread[-1]["message_id"] if thread else None, "attachments": [], "kind": kind, "asked": True,
         "status": "scheduled" if hold else "sending", "scheduled_for": _iso(hold) if hold else None, "sent_at": None}
    SANDBOX_OUTBOX.append(m)
    if hold:
        log_event(lead_id, "deal", "skipped", f"{kind.replace('_', ' ')} held until {m['scheduled_for']} (outside business hours)",
                  channel="email", message_id=m["message_id"])
        return {"ok": True, "status": "scheduled", "scheduled_for": m["scheduled_for"], "message_id": m["message_id"]}
    _deliver(m, at)
    return {"ok": True, "status": "sent", "sent_at": m["sent_at"], "message_id": m["message_id"]}


# --------------------------------------------------------------------------- the inbox

def _answers(m: dict) -> dict | None:
    """Our email that this incoming message answers."""
    ours = [o for o in OUTBOX if o["lead_id"] == m["lead_id"] and o.get("status") == "sent"]
    exact = [o for o in ours if o["message_id"] == m.get("in_reply_to")]
    if exact:
        return exact[0]
    before = [o for o in ours if o["sent_at"] <= (m.get("received_at") or o["sent_at"])]
    return before[-1] if before else (ours[-1] if ours else None)


def _receive(lead_id: str, reply: dict, answering: dict | None) -> dict:
    lead = LEADS[lead_id]
    sender = _sender()
    m = {"message_id": _message_id(lead_id, "in"), "lead_id": lead_id, "direction": "in",
         "thread_id": (answering or {}).get("thread_id") or (lead.get("outreach") or {}).get("thread_id"),
         "from": reply.get("from") or lead["person"]["email"], "to": reply.get("to") or [sender["email"]],
         "cc": list(reply.get("cc") or []),
         "subject": reply.get("subject") or ("Re: " + answering["subject"] if answering else ""),
         "body": reply["body"], "received_at": reply.get("received_at") or now(),
         "in_reply_to": reply.get("in_reply_to") or (answering or {}).get("message_id"),
         "attachments": list(reply.get("attachments") or []), "handled": False}
    m["from_lead"] = _addr(m["from"]) == _addr(lead["person"]["email"])
    INBOX.append(m)
    return m


@mcp.tool()
def get_replies(since: str | None = None) -> list[dict]:
    """New emails from prospects, each with who wrote (from, and from_lead: false when it isn't the lead),
    who was copied in (cc), subject, body, received_at, attachments and the email of ours it answers.

    Replies read_reply already handled are left out. A prospect who ignores the email has no reply here.
    """
    _release_held()
    for m in OUTBOX:  # ask each simulated prospect once per sent email
        if m.get("status") != "sent" or m.get("asked"):
            continue
        m["asked"] = True
        if any(i.get("in_reply_to") == m["message_id"] for i in INBOX):
            continue
        if sim.ENABLED:
            r = sim.reply(m["lead_id"], m["subject"], m["body"])
        else:  # STUB without simulated prospects: every sent email gets a canned positive reply
            r = {"body": "STUB: Sounds interesting, can we talk next week?"}
        if r and m["lead_id"] in LEADS:
            _receive(m["lead_id"], {"body": r["body"]}, m)
    out = []
    for m in INBOX:
        if m.get("handled") or (since and datetime.fromisoformat(m["received_at"]) < datetime.fromisoformat(since)):
            continue
        ours = _answers(m)
        out.append({**_public(m), "answers": {k: ours[k] for k in ("message_id", "subject", "sent_at")} if ours else None})
    return out


@mcp.tool()
def get_thread(lead_id: str) -> dict:
    """The whole email conversation with a lead, oldest first: every email both ways with from, to, cc,
    subject, body, time and attachments, plus the people on the lead (contacts) and who the next email goes to.
    """
    _release_held()
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": "unknown_lead"}
    out = lead.get("outreach") or {}
    return {"ok": True, "lead_id": lead_id, "thread_id": out.get("thread_id"), "lead": lead["person"],
            "messages": [_public(m) for m in _messages(lead_id)], "contacts": lead.get("contacts", []),
            "next_to": out.get("next_to") or lead["person"]["email"], "next_cc": out.get("next_cc") or []}


@mcp.tool()
def save_reply_label(lead_id: str, label: str) -> dict:
    """Save the classification of a prospect's reply."""

    allowed = set(REPLY_OPTIONS)

    if lead_id not in LEADS:
        return {"ok": False, "error": "unknown_lead"}

    if label not in allowed:
        return {"ok": False, "error": "invalid_label"}

    LEADS[lead_id].setdefault("outreach", {})["reply_label"] = label
    set_stage(LEADS[lead_id], "replied")
    log_event(lead_id, "reply", "success", f"labelled {label}", channel="email")

    return {"ok": True, "lead_id": lead_id, "label": label}


def _in_days(days: int) -> str:
    return _iso(clock() + timedelta(days=days))


REPLY_OPTIONS = {
    "positive": "Interested: agrees to talk, asks for a meeting or times, or asks how to buy or start. Still positive when they copy colleagues in but stay in the conversation themselves, when an assistant answers for the lead, or when they ask us to write to a different address",
    "negative": "Not interested, or declines (including because of price or an existing tool)",
    "not_now": "Interested in principle but asks to come back later",
    "handover": "Steps back and hands the conversation to a colleague they copied in on this reply, who takes it from here instead of them (e.g. 'Eva will take it from here')",
    "wrong_person": "Says they are not the right contact; may name someone else, but doesn't copy them in",
    "out_of_office": "An automatic absence reply",
    "unsubscribe": "Asks to stop emailing or to be removed",
    "meeting_change": "Asks to move or cancel a meeting that is already booked with us (only when meeting_booked_with_us is set); proposing a first time is positive",
}
WHY_NEGATIVE = {
    "has_competitor": "They already use or are choosing another tool",
    "price": "Too expensive or no budget",
    "negative": "Not interested, for any other reason",
}


@mcp.tool()
def read_reply(lead_id: str, reply_body: str | None = None, message_id: str | None = None) -> dict:
    """d1 reads a reply next to the email it answers: one label, then the exit for that label is applied and logged.

    Reads the newest unhandled email from get_replies (or message_id). reply_body is only for a reply
    pasted in by hand that isn't in the inbox. d1 sees who wrote, who was copied in, the attachments and
    our email it answers, so "yes to option 2" can be read. New people in the conversation are saved as
    contacts; a reply from someone other than the lead is flagged (from_lead: false).

    positive: continue (check_small_deal if auto-close is on, otherwise assign_rep).
    handover: stays active; the next email goes to the colleague copied in, the lead stays in cc.
    not_now and out_of_office: stop for now, follow up later in the same thread (not lost).
    negative: lost, with the reason (has_competitor, price or negative).
    wrong_person: lost (wrong_person); anyone copied in is saved as a referral contact for a person to follow up.
    unsubscribe: lost (negative) and never contacted again.
    """
    _release_held()
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": "unknown_lead"}
    if not (lead.get("outreach") or {}).get("sent_at"):
        return {"ok": False, "error": "no email was sent to this lead"}
    pending = [m for m in INBOX if m["lead_id"] == lead_id and not m.get("handled")]
    if not pending and lead.get("stage") == "replied":
        return {"ok": True, "label": (lead.get("outreach") or {}).get("reply_label"), "already_handled": True, "next": "stop"}
    if not pending:
        if not _has_text(reply_body):
            return {"ok": False, "error": "no reply waiting for this lead; call get_replies"}
        ours = [o for o in OUTBOX if o["lead_id"] == lead_id and o.get("status") == "sent"]
        pending = [_receive(lead_id, {"body": reply_body}, ours[-1] if ours else None)]
    chosen = next((m for m in pending if m["message_id"] == message_id), None) if message_id else None
    m = chosen or max(pending, key=lambda x: datetime.fromisoformat(x["received_at"]))
    ours = _answers(m)
    person = lead["person"]
    evidence = {
        "lead": {"name": person.get("name"), "email": person.get("email"), "title": person.get("title"),
                 "company": (lead.get("company") or {}).get("name")},
        "reply": {k: m[k] for k in ("from", "to", "cc", "subject", "body", "received_at", "attachments")}
                 | {"from_lead": m["from_lead"]},
        "our_email_it_answers": {k: ours[k] for k in ("from", "to", "cc", "subject", "body", "sent_at")} if ours else None,
        "meeting_booked_with_us": {k: (lead.get("handoff") or {}).get(k) for k in ("meeting_status", "meeting_start")}
                                  if (lead.get("handoff") or {}).get("meeting_status") == "booked" else None,
        "earlier_unhandled": [p["body"] for p in pending if p is not m and p["received_at"] <= m["received_at"]],
    }
    d = decide("Which kind of reply is this, from the prospect to our sales email? Read it together with our "
               "email it answers, and note who wrote it and who is copied in.", evidence, REPLY_OPTIONS)
    if d["answer"] is None:
        ask_person(lead, f"label the reply {m['message_id']}")
        return {"ok": True, "label": None, "decision": d, "message": _public(m), "our_email": _public(ours) if ours else None,
                "next": "Ask a person to label this reply"}
    label = d["answer"]
    for p in pending:  # this reply and older ones are handled; get_replies won't return them again
        if datetime.fromisoformat(p["received_at"]) <= datetime.fromisoformat(m["received_at"]):
            p["handled"] = True

    # Who is in the conversation now, and who our next email goes to (reply to all).
    added = []
    if not m["from_lead"]:
        c = _add_contact(lead, m["from"], "replied_for_lead", m["message_id"])
        added += [c] if c else []
    for a in m["cc"] + [x for x in m["to"] if _addr(x) not in _ours()]:
        c = _add_contact(lead, a, "cc", m["message_id"])
        if c and c not in added:
            added.append(c)
    everyone = [m["from"]] + m["to"] + m["cc"] + ([] if m["from_lead"] else [person["email"]])
    out = lead.setdefault("outreach", {})
    out["next_to"] = m["from"]
    out["next_cc"] = _dedupe([a for a in everyone if _addr(a) not in _ours()], drop=[m["from"]])
    out.update(reply_label=label, reply_body=m["body"], reply_from=m["from"], reply_message_id=m["message_id"],
               follow_up_due=None, follow_ups=0)
    if label == "meeting_change":  # a booked meeting stays the stage; @handoff moves or cancels it
        lead["updated_at"] = now()
    elif label == "out_of_office":  # an auto-reply: the stage moves, but it doesn't count as the prospect's reply time
        lead["stage"], lead["updated_at"] = "replied", now()
    else:
        set_stage(lead, "replied", at=m["received_at"])
    gate = {"question": "what kind of reply?", **d}
    base = {"ok": True, "label": label, "decision": d, "from": m["from"], "from_lead": m["from_lead"],
            "cc": m["cc"], "attachments": m["attachments"], "contacts_added": added,
            "our_email": {k: ours[k] for k in ("subject", "body", "sent_at")} if ours else None,
            "next_to": out["next_to"], "next_cc": out["next_cc"]}
    who = "" if m["from_lead"] else f" (written by {m['from']}, not the lead)"
    if label == "positive":
        nxt = "check_small_deal" if (BRIEF.get("auto_close") or {}).get("enabled") else "assign_rep"
        log_event(lead_id, "reply", "success", f"positive reply{who}", channel="email", decision=gate, message_id=m["message_id"])
        return {**base, "next": nxt}
    if label == "meeting_change":
        booked = (lead.get("handoff") or {}).get("meeting_status") == "booked"
        if not booked:
            ask_person(lead, "the prospect asks to change a meeting, but none is booked")
        log_event(lead_id, "reply", "success", f"asks to change the meeting{who}", channel="email", decision=gate,
                  message_id=m["message_id"])
        return {**base, "meeting": {k: (lead.get("handoff") or {}).get(k) for k in ("rep_id", "meeting_start", "meeting_status")},
                "next": "@handoff: reschedule_meeting to the time they asked for (find_free_slots with on_date), or "
                        "cancel_meeting if they want to call it off" if booked else
                        "Ask a person: they ask to change a meeting, but none is booked"}
    if label == "handover":
        new = [c for c in added if c["role"] in ("cc", "handover")]
        if not new:
            ask_person(lead, "the reply hands over to someone but gives no address")
            log_event(lead_id, "reply", "skipped", f"handover without an address{who}", channel="email", decision=gate)
            return {**base, "next": "Ask a person: the reply hands over to someone but gives no address to write to"}
        target = new[0]
        target["role"] = "handover"
        out["handover_to"] = target["email"]
        out["next_to"] = formataddr((target["name"] or "", target["email"])) if target["name"] else target["email"]
        out["next_cc"] = _dedupe([a for a in [m["from"]] + m["to"] + m["cc"] + [person["email"]] if _addr(a) not in _ours()],
                                 drop=[target["email"]])
        log_event(lead_id, "reply", "success", f"handover to {target['email']}{who}", channel="email", decision=gate,
                  message_id=m["message_id"])
        return {**base, "next_to": out["next_to"], "next_cc": out["next_cc"], "handover_to": target,
                "next": f"write to {target['email']} (save_draft addresses it; the person who handed over stays in cc), "
                        "then record_approval and send_email"}
    if label in ("not_now", "out_of_office"):
        out["follow_up_due"] = _in_days(90 if label == "not_now" else 7)
        log_event(lead_id, "reply", "skipped", f"{label}: follow up on {out['follow_up_due'][:10]}{who}", channel="email",
                  decision=gate, message_id=m["message_id"])
        return {**base, "follow_up_due": out["follow_up_due"], "next": "stop for now"}
    if label == "negative":
        why = decide("Why does the prospect decline?", evidence, WHY_NEGATIVE)
        reason = why["answer"] or "negative"
    else:
        reason = {"wrong_person": "wrong_person", "unsubscribe": "negative"}[label]
    lead.update(status="lost", loss_reason=reason)
    if label == "unsubscribe":
        lead["do_not_contact"] = True
    if label == "wrong_person":  # whoever they point to, copied in or named with an address, is a referral
        for a in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", m["body"]):
            c = _add_contact(lead, a, "referral", m["message_id"])
            if c and c not in added:
                added.append(c)
        for c in added:
            c["role"] = "referral"
    log_event(lead_id, "reply", "failure", f"{label}{who}: {m['body'][:200]}", channel="email", loss_reason=reason,
              decision=gate, message_id=m["message_id"])
    return {**base, "loss_reason": reason,
            "next": "stop" + ("; a person may follow up the referral in contacts" if label == "wrong_person" else "")}


@mcp.tool()
def due_follow_ups() -> list[dict]:
    """Every follow-up that is due now, each with a kind and what to do next:

    email: a follow-up email is due (spaced in working days; same thread, same cc).
    no_reply: ran out of follow-ups, marked lost (no_reply).
    rebook: a meeting was cancelled or missed; email the prospect new times.
    meeting_outcome: a meeting ended a working day ago and nobody recorded how it went.
    not_booked: a lead has had a rep for a working day but no meeting.
    needs_person: a lead is parked for a person's decision (a reply no one could label, a handover with no
      address, a meeting cancelled for good, a second no-show).
    quiet_deal: an open deal with no activity for 7 days.
    offer_unpaid: an auto-close offer is unpaid after 2 working days (at most 2 reminders).
    payment_failed: the checkout payment failed; send the link again the next working day.
    offer_expired: an auto-close offer is unpaid after 7 working days; it goes to a rep.
    queue_waiting: a positive reply has waited more than a working day for a rep.
    """
    _release_held()
    now_dt = clock()
    due = []
    for lead in LEADS.values():
        out = lead.get("outreach") or {}
        if lead.get("status") != "active" or lead.get("do_not_contact") or not out.get("sent_at") or out.get("scheduled_for"):
            continue
        if (lead.get("handoff") or {}).get("meeting_status") in ("booked", "held"):
            continue  # a meeting is booked or held: no outreach follow-ups
        if out.get("follow_up_due") and datetime.fromisoformat(out["follow_up_due"]) <= now_dt:
            due.append({"lead_id": lead["lead_id"], "kind": "email", "follow_up": out.get("follow_ups", 0) + 1,
                        "to": out.get("next_to") or lead["person"]["email"], "cc": out.get("next_cc") or [],
                        "next": "write a short follow-up (save_draft keeps the thread and cc), then record_approval and send_email"})
        elif (out.get("follow_ups", 0) >= MAX_FOLLOW_UPS and _awaiting_reply(lead["lead_id"])
              and _add_working_days(datetime.fromisoformat(out["sent_at"]), NO_REPLY_AFTER_WORKING_DAYS, _lead_tz(lead), _country(lead)) <= now_dt):
            lead.update(status="lost", loss_reason="no_reply")
            log_event(lead["lead_id"], "outreach", "failure", "no reply after all follow-ups", loss_reason="no_reply")
            due.append({"lead_id": lead["lead_id"], "kind": "no_reply", "status": "lost", "loss_reason": "no_reply"})
    from .handoff import handoff_follow_ups
    return due + handoff_follow_ups()
