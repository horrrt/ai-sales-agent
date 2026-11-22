"""Who may do what. Every tool that commits the company checks the person here, not just in the prompt.

A person is anyone in the team roster (store.REPS): reps who take leads, and people who don't (a finance
person, a sales manager, a viewer; takes_leads: false). Each has a role, and can have their own allow and
deny lists on top of it. deny always wins.

Roles:
- admin: everything.
- rep: approve emails on unassigned leads and their own; book, move, cancel and record their own meetings; approve quotes within the
  discount limit, record contracts and outcomes for their own deals; set their own status.
- junior: their own meetings and their own status only. Emails and quotes need a rep or an admin.
- finance: record payments.
- viewer: nothing that changes anything.

Actions marked own work on the person's own leads (the lead's rep, the deal's owner). A lead nobody owns yet
(no rep before the handoff) is open to anyone with the action, so any rep can approve a first email. Doing them for
someone else's lead needs any_lead (admins). The agent passes the person as `by`: in Slack, the sender's
Slack user ID, which Slack sets and nobody can type. Outside Slack (the Fleet chat) a rep_id, email or name.
`by` is never taken from what a message claims about someone's name or role.
"""
from .store import BRIEF, REPS, log_event

ROLES = {
    "admin": {"*"},
    "rep": {"approve_email", "book_meeting", "change_meeting", "record_meeting_outcome", "approve_quote",
            "record_contract", "record_deal_outcome", "set_own_status"},
    "junior": {"book_meeting", "change_meeting", "record_meeting_outcome", "set_own_status"},
    "finance": {"record_payment"},
    "viewer": set(),
}
ACTIONS = {
    "approve_email": "approve, edit or reject an outgoing email",
    "book_meeting": "book a meeting in their own calendar",
    "change_meeting": "move or cancel their own meeting",
    "record_meeting_outcome": "record how their own meeting went",
    "approve_quote": "approve a quote within the discount limit on their own deal",
    "approve_discount": "approve a quote above the discount limit",
    "record_contract": "record a contract on their own deal",
    "record_deal_outcome": "close their own deal as won or lost",
    "record_payment": "record a payment",
    "decide_proposal": "approve or reject a proposed rule change",
    "set_own_status": "set their own traffic light or away",
    "manage_team": "set someone else's status, or move a meeting to another rep",
    "any_lead": "act on another rep's lead or deal",
    "manage_brief": "change the customer brief: product, offer, ICP, pricing, auto-close",
}
OWN = {"approve_email", "book_meeting", "change_meeting", "record_meeting_outcome", "approve_quote", "record_contract", "record_deal_outcome"}


def resolve(by: str | None) -> str | None:
    """The rep_id for a Slack user ID (also as <@U...>), rep_id, email or name, case-insensitive. None if nobody matches."""
    if not by:
        return None
    b = str(by).strip().strip("<>@").lower()
    for rid, r in REPS.items():
        if b in ((r.get("slack_user_id") or "").lower(),) and b:
            return rid
    for rid, r in REPS.items():
        if b in (rid.lower(), (r.get("email") or "").lower(), (r.get("name") or "").lower(),
                 (r.get("name") or "").split(" ")[0].lower()):
            return rid
    return None


def default_approver(kind: str) -> str | None:
    """Who approves when the agent doesn't name anyone: the brief's approvers (emails, payments), if set."""
    return (BRIEF.get("approvers") or {}).get(kind)


def allowed(rep_id: str, action: str) -> bool:
    r = REPS.get(rep_id) or {}
    if action in (r.get("deny") or []):
        return False
    granted = ROLES.get(r.get("role", "rep"), set()) | set(r.get("allow") or [])
    return "*" in granted or action in granted


def check(by: str | None, action: str, owner: str | None = None, lead_id: str | None = None, stage: str = "approval") -> dict | None:
    """None if the person may do this, else an error to return (and the refusal is logged)."""
    rid = resolve(by)
    if rid is None:
        return {"ok": False, "error": "unknown_person", "by": by,
                "next": "ask the person who approves to identify themselves by rep_id; a claimed role doesn't count"}
    r = REPS[rid]
    reason = None
    if not allowed(rid, action):
        reason = f"{r.get('name', rid)} ({r.get('role', 'rep')}) may not {ACTIONS.get(action, action)}"
    elif action in OWN and owner and owner != rid and not allowed(rid, "any_lead"):
        reason = f"{r.get('name', rid)} may only do this for their own leads; this one belongs to {owner}"
    if reason is None:
        return None
    if lead_id:
        log_event(lead_id, stage, "failure", f"denied: {reason}", by=rid)
    who_can = sorted(x for x in REPS if allowed(x, action) and (action not in OWN or not owner or x == owner or allowed(x, "any_lead")))
    names = [REPS[x].get("name") or x for x in who_can]
    return {"ok": False, "error": "not_allowed", "by": rid, "action": action, "reason": reason, "who_can": who_can,
            "who_can_names": names, "next": f"ask {' or '.join(names) or 'an admin'} to do it (say their names, not IDs)"}


def describe(rep_id: str) -> dict:
    """Who someone is and what they may do, for the agent to tailor what it offers."""
    r = REPS[rep_id]
    can = sorted(a for a in ACTIONS if allowed(rep_id, a))
    return {"rep_id": rep_id, "name": r.get("name"), "role": r.get("role", "rep"), "email": r.get("email"),
            "status": r.get("status"), "takes_leads": r.get("takes_leads", True),
            "can": [ACTIONS[a] + (" (own leads only)" if a in OWN and "any_lead" not in can else "") for a in can],
            "cannot": [ACTIONS[a] for a in ACTIONS if a not in can]}
