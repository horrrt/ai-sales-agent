"""Stage 6: closing the deal, after the meeting. Owner: Slava (close or escalate), payments Ayaan.

A deal is like a Salesforce Opportunity, kept on the lead under `deal`. Stages:
qualification -> quote -> negotiation -> closed_won | closed_lost.

The rep runs the deal. The agent records what the rep says, scores BANT from the rep's call notes,
drafts quotes from the brief's price list, flags deals that went quiet and writes summaries. Every step
that leaves the company or commits it is checked in code and needs a person:
- approve_quote: before any quote goes out (an admin, if the discount is above the policy limit)
- send_quote: refuses a quote without an approval for that exact quote
- record_contract and record_deal_outcome: a person confirms signed, won and lost
Won and lost are recorded on the lead (status, loss_reason), so the funnel counts each lead once.

Auto-close (the stretch goal, off unless the brief enables it) is one path through the same deal:
check_small_deal -> send_offer (standard quote + sandbox checkout link) -> record_payment -> check_deal_done.

Prices and totals are computed here, never by a model. d1 only judges BANT from the call notes.
Not built: CRM sync, e-signature, invoicing, real payments.
"""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from .. import access
from ..app import mcp
from ..decide import decide
from ..store import BRIEF, LEADS, ask_person, clock, log_event, now, set_stage
from .shared import REASONS

PROBABILITY = {"qualification": 20, "quote": 40, "negotiation": 70, "closed_won": 100, "closed_lost": 0}
STALE_DAYS = 7
OFFER_REMINDER_WORKING_DAYS = 2   # an unpaid offer gets a reminder after this, at most MAX_OFFER_REMINDERS times
MAX_OFFER_REMINDERS = 2
OFFER_EXPIRY_WORKING_DAYS = 7     # then it goes to a rep
BANT = {
    "budget": "Do the notes show they have, or can get, budget for this offer at our price?",
    "authority": "Do the notes show the person can decide, or that the decision maker is involved?",
    "need": "Do the notes show a real need or pain this offer answers?",
    "timeline": "Do the notes show they want to start within the next 3 months?",
}
BANT_OPTIONS = {"yes": "The notes clearly say yes", "no": "The notes clearly say no",
                "unknown": "The notes don't say, or it's unclear"}


def _pricing() -> dict:
    return BRIEF.get("pricing") or {}


def _deal(lead_id: str) -> tuple[dict | None, dict | None, str | None]:
    lead = LEADS.get(lead_id)
    if lead is None:
        return None, None, f"unknown lead {lead_id}"
    if "deal" not in lead:
        return lead, None, "no deal for this lead; a deal opens when a meeting is held (or through auto-close)"
    return lead, lead["deal"], None


def _set_deal_stage(lead: dict, stage: str) -> None:
    deal = lead["deal"]
    deal["stage"] = stage
    deal.setdefault("stage_times", {}).setdefault(stage, now())
    deal["probability"] = PROBABILITY[stage]
    lead["updated_at"] = now()


def open_deal(lead_id: str, auto_close: bool = False) -> dict:
    """Open the deal for a lead (called by record_meeting_outcome on a held meeting, and by auto-close)."""
    lead = LEADS[lead_id]
    if "deal" not in lead:
        lead["deal"] = {"owner": (lead.get("handoff") or {}).get("rep_id"), "auto_close": auto_close,
                        "call_notes": [], "bant": {}, "contract": {"status": "none", "signed_at": None},
                        "paid": False, "amount_eur": None, "closed_at": None, "expected_close_date": None}
        _set_deal_stage(lead, "qualification")
        log_event(lead_id, "deal", "success", "deal opened" + (" (auto-close)" if auto_close else ""))
    return lead["deal"]


def _quote_hash(q: dict) -> str:
    keys = ("plan", "quantity", "term_months", "discount_pct", "total_eur")
    return hashlib.sha256(json.dumps({k: q.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()[:16]


MAX_SURCHARGE_PCT = 30
RULE_KEYS = {"employees_min", "employees_max", "country_in", "country_not_in", "industry_in", "quantity_min",
             "term_months_min", "plan_in"}


def _matches(cond: dict, lead: dict | None, plan: str, quantity: int, term: int) -> bool:
    c = (lead or {}).get("company") or {}
    emp, country, industry = c.get("employees"), (c.get("country") or "").upper(), (c.get("industry") or "").lower()
    for k, v in cond.items():
        if k == "employees_min" and not (isinstance(emp, int) and emp >= v):
            return False
        if k == "employees_max" and not (isinstance(emp, int) and emp <= v):
            return False
        if k == "country_in" and country not in [x.upper() for x in v]:
            return False
        if k == "country_not_in" and country in [x.upper() for x in v]:
            return False
        if k == "industry_in":
            from .. import industries as ind
            mine = {industry} | {x.lower() for x in (c.get("industries") or [])} | \
                   {x.lower() for x in ind.normalize(c.get("industry") or "")[0]}
            if not mine & {x.lower() for x in v}:
                return False
        if k == "quantity_min" and quantity < v:
            return False
        if k == "term_months_min" and term < v:
            return False
        if k == "plan_in" and plan not in v:
            return False
    return True


def price_rules(lead: dict | None, plan: str, quantity: int, term: int) -> tuple[list[dict], float]:
    """The brief's price rules that match this prospect and deal, and their total (negative: cheaper).

    A rule: {"label": "Startup", "if": {"employees_max": 9}, "pct": -10}. Every condition must hold.
    Discounts from rules together never go past the brief's max_discount_pct; surcharges stop at 30%."""
    p = _pricing()
    hits = [{"label": r.get("label", "rule"), "pct": float(r.get("pct", 0))} for r in p.get("adjustments") or []
            if isinstance(r.get("if"), dict) and _matches(r["if"], lead, plan, quantity, term)]
    total = sum(h["pct"] for h in hits)
    return hits, max(-float(p.get("max_discount_pct", 0)), min(MAX_SURCHARGE_PCT, total))


def _price(plan: str, quantity: int, term_months: int | None, discount_pct: float, lead: dict | None = None,
           use_rules: bool = True) -> dict:
    p = _pricing()
    plans = p.get("plans") or {}
    if plan not in plans:
        return {"error": f"unknown plan {plan!r}; the brief has {sorted(plans)}"}
    spec = plans[plan]
    if spec.get("custom") or spec.get("price_per_month") is None:
        return {"error": f"{plan} has a custom price: the rep sets it with the customer, not draft_quote"}
    term = term_months or spec.get("min_term_months", 1)
    if term < spec.get("min_term_months", 1):
        return {"error": f"{plan} needs at least {spec['min_term_months']} months"}
    if quantity < 1:
        return {"error": "quantity must be at least 1"}
    if quantity < spec.get("min_quantity", 1):
        return {"error": f"{plan} needs at least {spec['min_quantity']} (seats, users or locations)"}
    if spec.get("max_quantity") and quantity > spec["max_quantity"]:
        return {"error": f"{plan} is for at most {spec['max_quantity']}; use a bigger plan"}
    if discount_pct < 0 or discount_pct > p.get("max_discount_pct", 0):
        return {"error": f"discount must be between 0 and {p.get('max_discount_pct', 0)}%"}
    rules, rules_pct = price_rules(lead, plan, quantity, term) if use_rules else ([], 0.0)
    adjustment = rules_pct - discount_pct          # what the prospect pays against the list price, in %
    effective_discount = max(0.0, -adjustment)
    if effective_discount > p.get("max_discount_pct", 0) + 1e-9:
        return {"error": f"the price rules ({rules_pct:+g}%) and the discount ({discount_pct:g}%) together are more than "
                         f"the maximum discount of {p.get('max_discount_pct', 0)}%"}
    recurring = round(spec["price_per_month"] * quantity * term, 2)
    setup = float(spec.get("setup_fee") or 0)  # one-off; discounts and rules apply to the recurring part only
    list_total = round(recurring + setup, 2)
    return {"plan": plan, "quantity": quantity, "term_months": term, "list_total_eur": list_total,
            "setup_fee_eur": setup, "recurring_eur": recurring, "price_rules": rules, "rules_pct": rules_pct,
            "discount_pct": discount_pct, "adjustment_pct": round(adjustment, 2),
            "total_eur": round(recurring * (1 + adjustment / 100) + setup, 2),
            "needs_admin": effective_discount > p.get("max_discount_pct_without_approval", 0)}


# --- Rep-driven closing -----------------------------------------------------------------------

@mcp.tool()
def record_call_notes(lead_id: str, notes: str, by: str = "rep") -> dict:
    """Save the rep's notes from a call or meeting. They are the only evidence qualify_deal uses."""
    lead, deal, err = _deal(lead_id)
    if err:
        return {"ok": False, "error": err}
    deal["call_notes"].append({"at": now(), "by": by, "notes": notes})
    log_event(lead_id, "deal", "success", f"call notes from {by}", by=by)
    return {"ok": True, "next": "qualify_deal"}


@mcp.tool()
def qualify_deal(lead_id: str) -> dict:
    """d1 scores BANT (budget, authority, need, timeline) from the rep's call notes: yes, no or unknown each.

    unknown never blocks a quote. A no is reported for the rep to decide; the deal doesn't move by itself.
    """
    lead, deal, err = _deal(lead_id)
    if err:
        return {"ok": False, "error": err}
    if not deal["call_notes"]:
        return {"ok": False, "error": "no call notes yet; the rep adds them with record_call_notes"}
    evidence = {"call_notes": [n["notes"] for n in deal["call_notes"]], "offer": BRIEF.get("offer"),
                "pricing": _pricing(), "company": lead.get("company"), "person": lead.get("person")}
    for factor, question in BANT.items():
        d = decide(question, evidence, BANT_OPTIONS)
        deal["bant"][factor] = {"answer": d["answer"] or "unknown", "confidence": d["confidence"], "backend": d["backend"]}
    failing = [f for f, v in deal["bant"].items() if v["answer"] == "no"]
    unknown = [f for f, v in deal["bant"].items() if v["answer"] == "unknown"]
    log_event(lead_id, "deal", "success" if not failing else "skipped",
              f"BANT: failing {failing or 'none'}, unknown {unknown or 'none'}", decision={"question": "BANT", "bant": deal["bant"]})
    return {"ok": True, "bant": deal["bant"], "failing": failing, "unknown": unknown,
            "next": "ask the rep whether to quote or close it lost" if failing else "draft_quote"}


@mcp.tool()
def suggest_price(lead_id: str, plan: str, quantity: int | None = None, term_months: int | None = None) -> dict:
    """What this prospect would pay on a plan, from the brief's price list and price rules, with each rule that
    applied. Nothing is saved; use it to answer "what would it cost us?" or before draft_quote."""
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": f"unknown lead {lead_id}"}
    spec = (_pricing().get("plans") or {}).get(plan) or {}
    q = _price(plan, quantity or spec.get("min_quantity", 1), term_months, 0, lead)
    if "error" in q:
        return {"ok": False, "error": q["error"]}
    return {"ok": True, **{k: q[k] for k in ("plan", "quantity", "term_months", "list_total_eur", "price_rules", "rules_pct",
                                             "total_eur", "needs_admin", "setup_fee_eur")}}


@mcp.tool()
def draft_quote(lead_id: str, plan: str, quantity: int = 1, term_months: int | None = None, discount_pct: float = 0,
                apply_price_rules: bool = True) -> dict:
    """Draft a quote from the brief's price list. Totals are computed here. Every quote needs approve_quote before send_quote.

    The brief's price rules (pricing.adjustments) set this prospect's price from their size, country, industry,
    the quantity and the term; the quote lists which rules applied. discount_pct is the rep's extra discount on
    top. Rules plus discount past the no-approval limit need an admin; past the maximum they're refused."""
    lead, deal, err = _deal(lead_id)
    if err:
        return {"ok": False, "error": err}
    if deal["stage"] in ("closed_won", "closed_lost"):
        return {"ok": False, "error": "the deal is closed"}
    q = _price(plan, quantity, term_months, discount_pct, lead, apply_price_rules)
    if "error" in q:
        return {"ok": False, "error": q["error"]}
    q.update(status="draft", approval=None)
    q["quote_hash"] = _quote_hash(q)
    deal["quote"] = q
    deal["estimated_value_eur"] = round(q["total_eur"] * 12 / q["term_months"], 2) if q["term_months"] else q["total_eur"]
    if deal["stage"] == "qualification":
        _set_deal_stage(lead, "quote")
    rules = ", ".join(f"{r['label']} {r['pct']:+g}%" for r in q["price_rules"])
    log_event(lead_id, "deal", "success", f"quote drafted: {plan} x{quantity}, {q['term_months']} months, "
              f"{discount_pct:g}% off" + (f", rules: {rules}" if rules else "") + f", EUR {q['total_eur']:,.2f}")
    return {"ok": True, "quote": q, "next": "approve_quote" + (" (an admin: the discount is above the policy limit)" if q["needs_admin"] else "")}


@mcp.tool()
def approve_quote(lead_id: str, by: str, note: str = "") -> dict:
    """A person approves the current quote before it goes out. This tool always asks a person first.

    If the quote's discount is above the brief's limit, the approver must be an admin (by = their rep_id or name).
    """
    lead, deal, err = _deal(lead_id)
    if err:
        return {"ok": False, "error": err}
    q = deal.get("quote")
    if not q or q["status"] != "draft":
        return {"ok": False, "error": "no draft quote to approve"}
    denied = access.check(by, "approve_quote", owner=deal.get("owner"), lead_id=lead_id) or \
        (access.check(by, "approve_discount", lead_id=lead_id) if q["needs_admin"] else None)
    if denied:
        return denied
    by = access.resolve(by)
    q["approval"] = {"by": by, "at": now(), "note": note, "quote_hash": q["quote_hash"], "admin_needed": q["needs_admin"]}
    q["status"] = "approved"
    log_event(lead_id, "approval", "success", f"quote approved by {by}" + (" (above the discount limit)" if q["needs_admin"] else ""), by=by)
    return {"ok": True, "next": "send_quote"}


@mcp.tool()
def send_quote(lead_id: str) -> dict:
    """Send the approved quote to the prospect (sandbox). Refuses a quote nobody approved, or one changed after approval."""
    lead, deal, err = _deal(lead_id)
    if err:
        return {"ok": False, "error": err}
    q = deal.get("quote") or {}
    if q.get("status") != "approved" or (q.get("approval") or {}).get("quote_hash") != _quote_hash(q):
        return {"ok": False, "error": "not_approved", "next": "approve_quote first"}
    q["status"] = "sent"
    deal["expected_close_date"] = deal.get("expected_close_date") or (clock().date() + timedelta(days=14)).isoformat()
    _set_deal_stage(lead, "negotiation")
    log_event(lead_id, "deal", "success", f"quote sent: EUR {q['total_eur']:,.2f}", channel="email")
    return {"ok": True, "sandbox": True, "expected_close_date": deal["expected_close_date"]}


@mcp.tool()
def record_contract(lead_id: str, status: str, by: str = "") -> dict:
    """Record the contract status: sent or signed. This tool always asks a person first."""
    lead, deal, err = _deal(lead_id)
    if err:
        return {"ok": False, "error": err}
    if status not in ("sent", "signed"):
        return {"ok": False, "error": "status must be sent or signed"}
    denied = access.check(by, "record_contract", owner=deal.get("owner"), lead_id=lead_id, stage="deal")
    if denied:
        return denied
    by = access.resolve(by)
    deal["contract"] = {"status": status, "signed_at": now() if status == "signed" else None}
    log_event(lead_id, "deal", "success", f"contract {status}", by=by)
    return {"ok": True, "next": "record_deal_outcome" if status == "signed" else "wait for the signature"}


@mcp.tool()
def record_deal_outcome(lead_id: str, outcome: str, by: str, amount_eur: float | None = None,
                        loss_reason: str | None = None, note: str = "") -> dict:
    """A person closes the deal as won or lost. This tool always asks a person first.

    won: amount_eur is the final value (default: the sent quote's total). lost: loss_reason from loss_reasons.json.
    The lead's status and loss_reason are the record, so funnel and loss counts see each lead once.
    """
    lead, deal, err = _deal(lead_id)
    if err:
        return {"ok": False, "error": err}
    if deal["stage"] in ("closed_won", "closed_lost"):
        return {"ok": False, "error": f"already {deal['stage']}"}
    denied = access.check(by, "record_deal_outcome", owner=deal.get("owner"), lead_id=lead_id, stage="deal")
    if denied:
        return denied
    by = access.resolve(by)
    if outcome == "won":
        amount = amount_eur if amount_eur is not None else (deal.get("quote") or {}).get("total_eur")
        if amount is None:
            return {"ok": False, "error": "amount_eur is needed: there is no quote to take it from"}
        deal.update(amount_eur=amount, closed_at=now())
        _set_deal_stage(lead, "closed_won")
        lead.update(status="won", loss_reason=None)
        set_stage(lead, "closed")
        log_event(lead_id, "deal", "success", f"won: EUR {amount:,.2f}. {note}".strip(), by=by)
        return {"ok": True, "deal": "closed_won", "amount_eur": amount, "next": "post a short summary in #sales-desk"}
    if outcome == "lost":
        if loss_reason not in REASONS:
            return {"ok": False, "error": f"loss_reason must be one of {sorted(REASONS)}"}
        deal["closed_at"] = now()
        _set_deal_stage(lead, "closed_lost")
        lead.update(status="lost", loss_reason=loss_reason)
        set_stage(lead, "closed")
        log_event(lead_id, "deal", "failure", f"lost: {note}".strip(), loss_reason=loss_reason, by=by)
        return {"ok": True, "deal": "closed_lost", "loss_reason": loss_reason}
    return {"ok": False, "error": "outcome must be won or lost"}


@mcp.tool()
def get_pipeline(rep_id: str | None = None) -> dict:
    """Open and closed deals: count and value per stage, the weighted pipeline, and deals that went quiet (no activity in 7 days)."""
    stages: dict[str, dict] = {s: {"count": 0, "value_eur": 0.0} for s in PROBABILITY}
    weighted, stale = 0.0, []
    cutoff = clock() - timedelta(days=STALE_DAYS)
    for lead in LEADS.values():
        deal = lead.get("deal")
        if not deal or (rep_id and deal.get("owner") != rep_id):
            continue
        value = deal.get("amount_eur") or (deal.get("quote") or {}).get("total_eur") or deal.get("estimated_value_eur") or 0
        stages[deal["stage"]]["count"] += 1
        stages[deal["stage"]]["value_eur"] += value
        if deal["stage"] not in ("closed_won", "closed_lost"):
            weighted += value * deal["probability"] / 100
            last = lead.get("updated_at") or lead.get("created_at")
            if last and datetime.fromisoformat(last) < cutoff:
                stale.append({"lead_id": lead["lead_id"], "company": lead["company"]["name"], "stage": deal["stage"], "last_activity": last})
    return {"by_stage": stages, "weighted_pipeline_eur": round(weighted, 2), "stale_deals": stale}


# --- Auto-close: the agent closes a small, standard deal alone (off unless the brief enables it) --------

def _brand() -> str:
    """The customer's name as prospects know it ("Lagerlys AB" is "Lagerlys")."""
    return (BRIEF.get("company_name") or "our").replace(" ApS", "").replace(" AB", "").replace(" AS", "").replace(" Oy", "")


def _auto_close() -> dict:
    return BRIEF.get("auto_close") or {}


@mcp.tool()
def check_small_deal(lead_id: str, estimated_value_eur: float | None = None) -> dict:
    """d1 gate "small, standard deal?" for a positive reply. Only used when the brief enables auto-close.

    estimated_value_eur: what the deal would be worth per year on the auto-close plan (auto_close.plans in
    the brief, e.g. starter at EUR 99 a month = EUR 1,188). Above the brief's price cap it's not a small deal,
    whatever else the reply says. A reply that needs a bigger plan (more teams, the compliance lead, an
    annual contract) is not a small deal either: d1 judges that against the plan description.
    """
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": f"unknown lead {lead_id}"}
    cfg = _auto_close()
    if not cfg.get("enabled"):
        return {"ok": True, "small_deal": False, "next": "assign_rep", "note": "auto-close is switched off in the brief"}
    cap = cfg.get("max_annual_value_eur")
    if estimated_value_eur is not None and isinstance(cap, (int, float)) and estimated_value_eur > cap:
        log_event(lead_id, "deal", "skipped", f"estimated EUR {estimated_value_eur:g}/year is over the cap of {cap:g}: to a rep")
        return {"ok": True, "small_deal": False, "next": "assign_rep", "note": "over the price cap"}
    plans = _pricing().get("plans") or {}
    evidence = {"price_cap_eur_per_year": cap, "estimated_value_eur_per_year": estimated_value_eur,
                "auto_close_plans": {p: plans.get(p) for p in cfg.get("plans") or []},
                "standard_terms": cfg.get("standard_terms"), "company": lead.get("company"),
                "reply": (lead.get("outreach") or {}).get("reply_body", "")}
    d = decide("Can this deal close on one of the auto-close plans (auto_close_plans) at its list price and standard "
               "terms: the company fits what the plan is for (size included), the value is under the price cap, and the "
               "prospect asks for no custom terms, security review, discount or contract changes?", evidence)
    if d["answer"] is None:  # no decision: a person decides, nothing is opened or sent
        ask_person(lead, "decide whether this positive reply can close at the standard offer (auto-close) or goes to a rep")
        log_event(lead_id, "deal", "skipped", "small standard deal? no decision: ask a person", decision={"question": "small, standard deal?", **d})
        return {"ok": True, "small_deal": None, "decision": d, "next": "Ask a person: auto-close or a rep?"}
    small = bool(d["answer"])
    if small:
        deal = open_deal(lead_id, auto_close=True)
        deal.update(small_deal=True, estimated_value_eur=estimated_value_eur)
    log_event(lead_id, "deal", "success" if small else "skipped",
              "small standard deal: auto-close" if small else "not a small standard deal: to a rep",
              decision={"question": "small, standard deal?", **d})
    return {"ok": True, "small_deal": small, "decision": d, "next": "send_offer" if small else "assign_rep"}


@mcp.tool()
def send_offer(lead_id: str, plan: str | None = None) -> dict:
    """Send the standard offer on the auto-close plan (list price, no discount) with the checkout link (sandbox).
    Only after check_small_deal said yes, and only for a plan in the brief's auto_close.plans."""
    lead, deal, err = _deal(lead_id)
    if err or not deal.get("small_deal"):
        return {"ok": False, "error": "check_small_deal has not approved this lead for auto-close"}
    plans = (_pricing().get("plans") or {})
    allowed = [p for p in (_auto_close().get("plans") or []) if p in plans] or \
              [min(plans, key=lambda p: plans[p].get("min_term_months", 1))] if plans else []
    plan = plan or (allowed[0] if allowed else None)
    if plan and plan not in allowed:
        return {"ok": False, "error": f"{plan} isn't an auto-close plan (the brief allows {allowed}); a rep sells it"}
    q = _price(plan, plans[plan].get("min_quantity", 1), None, 0, lead) if plan else {"error": "the brief has no pricing"}
    if "error" in q:
        return {"ok": False, "error": q["error"]}
    if q["needs_admin"]:  # the price rules for this prospect go past what may be given without an admin
        return {"ok": False, "error": "the price rules for this prospect need an admin's approval: a rep sells it",
                "next": "assign_rep"}
    if (deal.get("quote") or {}).get("checkout_url"):
        return {"ok": False, "error": "offer_already_sent", "offer_link": deal["quote"]["checkout_url"],
                "next": "wait for the payment; due_follow_ups sends the reminders"}
    if lead.get("do_not_contact"):
        return {"ok": False, "error": "do_not_contact"}
    link = f"{_auto_close().get('checkout_url', 'https://checkout.sandbox.test')}?lead={lead_id}"
    q.update(status="sent", checkout_url=link, approval={"by": "auto_close policy", "at": now(), "quote_hash": None})
    q["quote_hash"] = _quote_hash(q)
    deal["quote"] = q
    deal.update(payment_status="unpaid", offer_reminders=0)
    from .outreach import send_policy_email
    seats = f", {q['quantity']} users" if q["quantity"] > 1 else ""
    setup = f", including the EUR {q['setup_fee_eur']:,.2f} setup" if q.get("setup_fee_eur") else ""
    body = (f"Thanks, {lead['person']['name'].split()[0]}. Here is the standard {plan} plan at EUR {q['total_eur']:,.2f} "
            f"({q['term_months']} months{seats}{setup}). You can start straight away here: {link}")
    mail = send_policy_email(lead_id, f"Your {_brand()} offer", body, "offer")
    if not mail.get("ok"):
        return {"ok": False, "error": mail.get("error"), "addresses": mail.get("addresses")}
    _set_deal_stage(lead, "negotiation")
    set_stage(lead, "offer_sent")
    log_event(lead_id, "deal", "success", f"standard offer with checkout link {link}: {mail['status']}", channel="email")
    return {"ok": True, "offer_link": link, "quote": q, "sandbox": True, "email": mail,
            "next": "check_deal_done once the prospect had time to pay; due_follow_ups sends reminders"}


@mcp.tool()
def record_payment(lead_id: str, paid: bool, by: str, amount_eur: float | None = None, status: str | None = None) -> dict:
    """Record what happened at the checkout. This tool always asks a person first (later: the payment provider).

    status: paid (default when paid is true), failed (a declined card: a retry follow-up is due the next
    working day) or refunded (after a win: a person decides what happens to the customer). A payment below
    the offer's total is recorded as partial and waits for a person; a later payment adds to it. Only finance and
    admins record payments: by is required (their rep_id, email or name)."""
    lead, deal, err = _deal(lead_id)
    if err:
        return {"ok": False, "error": err}
    denied = access.check(by, "record_payment", lead_id=lead_id, stage="deal")
    if denied:
        return denied
    status = status or ("paid" if paid else "failed")
    if status not in ("paid", "failed", "refunded"):
        return {"ok": False, "error": "status must be paid, failed or refunded"}
    total = (deal.get("quote") or {}).get("total_eur")
    if status == "paid" and deal.get("payment_status") == "paid":
        return {"ok": False, "error": "already_paid", "amount_eur": deal.get("amount_eur"), "next": "a second payment needs a person: refund one"}
    if status == "refunded" and deal.get("payment_status") not in ("paid", "partial"):
        return {"ok": False, "error": "nothing was paid, so nothing can be refunded"}
    by = access.resolve(by)
    if status == "paid":
        amount = amount_eur if amount_eur is not None else (total - (deal.get("amount_eur") or 0) if deal.get("payment_status") == "partial" else total)
        if deal.get("payment_status") == "partial":  # the rest of a partial payment
            amount = (deal.get("amount_eur") or 0) + amount
            lead.pop("needs_person", None)
        partial = total is not None and amount is not None and amount + 0.005 < total
        deal.update(paid=not partial, amount_eur=amount, payment_status="partial" if partial else "paid", paid_at=now())
        if partial:
            ask_person(lead, f"partial payment: EUR {amount:,.2f} of EUR {total:,.2f}")
        log_event(lead_id, "deal", "success" if not partial else "skipped",
                  f"payment {'partial ' if partial else ''}EUR {amount or 0:,.2f}", by=by)
        return {"ok": True, "payment_status": deal["payment_status"],
                "next": "check_deal_done" if not partial else "Ask a person: the amount paid is below the offer"}
    if status == "failed":
        from .handoff import _iso, _next_working_morning
        deal.update(paid=False, payment_status="failed", payment_retry_due=_iso(_next_working_morning(lead)))
        log_event(lead_id, "deal", "failure", "payment failed at the checkout", by=by)
        return {"ok": True, "payment_status": "failed", "retry_due": deal["payment_retry_due"],
                "next": "due_follow_ups sends the checkout link again the next working day"}
    deal.update(paid=False, payment_status="refunded", refunded=True, refunded_at=now())
    ask_person(lead, "the customer was refunded: decide whether the deal is lost and why")
    log_event(lead_id, "deal", "failure", "payment refunded", by=by)
    return {"ok": True, "payment_status": "refunded", "next": "Ask a person: refunded, lost or keep?"}


@mcp.tool()
def send_offer_reminder(lead_id: str) -> dict:
    """Send the checkout link again for an unpaid or failed auto-close offer (sandbox, same thread, business
    hours). Only when due_follow_ups lists offer_unpaid or payment_failed; at most 2 reminders."""
    lead, deal, err = _deal(lead_id)
    if err:
        return {"ok": False, "error": err}
    q = deal.get("quote") or {}
    if not deal.get("auto_close") or not q.get("checkout_url"):
        return {"ok": False, "error": "no auto-close offer was sent"}
    if deal.get("paid") or deal.get("stage") in ("closed_won", "closed_lost"):
        return {"ok": False, "error": "already paid or closed"}
    from .handoff import _add_working_days, _lead_country, _lead_tz, _parse
    from .outreach import _release_held
    _release_held()  # an offer held overnight goes out first
    failed = deal.get("payment_status") == "failed"
    if failed:
        if deal.get("payment_retry_due") and _parse(deal["payment_retry_due"]) > clock():
            return {"ok": False, "error": "too_early", "due": deal["payment_retry_due"]}
    else:
        if deal.get("offer_reminders", 0) >= MAX_OFFER_REMINDERS:
            return {"ok": False, "error": "no reminders left", "next": "check_deal_done: it goes to a rep"}
        last = deal.get("last_reminder_at") or deal.get("offer_sent_at")
        if not last:
            return {"ok": False, "error": "the offer hasn't gone out yet (scheduled for business hours)"}
        due = _add_working_days(_parse(last), OFFER_REMINDER_WORKING_DAYS, _lead_tz(lead), _lead_country(lead))
        if due > clock():
            return {"ok": False, "error": "too_early", "due": due.isoformat(),
                    "next": "reminders go out 2 working days apart; due_follow_ups lists it when it's due"}
    from .outreach import send_policy_email
    first = lead["person"]["name"].split()[0]
    body = (f"Hi {first}, the payment didn't go through. You can try again here: {q['checkout_url']}" if failed else
            f"Hi {first}, a quick reminder: your {_brand()} offer (EUR {q['total_eur']:,.2f}) is ready whenever you are: {q['checkout_url']}")
    mail = send_policy_email(lead_id, f"Your {_brand()} offer", body, "offer_reminder")
    if failed and mail.get("ok"):
        deal.update(payment_status="unpaid", payment_retry_due=None)
    return {**mail, "reminders": deal.get("offer_reminders", 0)}


@mcp.tool()
def check_deal_done(lead_id: str) -> dict:
    """Paid: the auto-close deal is won and the rep gets a summary. Not paid: it goes to a rep."""
    lead, deal, err = _deal(lead_id)
    if err:
        return {"ok": False, "error": err}
    if deal.get("stage") in ("closed_won", "closed_lost"):
        return {"ok": True, "done": deal["stage"] == "closed_won", "already": deal["stage"], "next": "stop"}
    if deal.get("payment_status") == "partial":
        return {"ok": True, "done": False, "next": "Ask a person: the amount paid is below the offer"}
    if deal.get("paid"):
        deal["closed_at"] = now()
        _set_deal_stage(lead, "closed_won")
        lead.update(status="won", loss_reason=None)
        set_stage(lead, "closed")
        log_event(lead_id, "deal", "success", f"paid: closed automatically, EUR {deal.get('amount_eur') or 0:,.2f}")
        return {"ok": True, "done": True, "next": "post a short summary for the rep in #sales-desk"}
    from .handoff import _add_working_days, _lead_country, _lead_tz, _parse
    from .outreach import _release_held
    _release_held()
    sent = deal.get("offer_sent_at")
    if not sent and (deal.get("quote") or {}).get("checkout_url"):
        return {"ok": True, "done": False, "payment_status": deal.get("payment_status"),
                "next": "the offer is scheduled for business hours and hasn't gone out yet: wait"}
    expired = not sent or _add_working_days(_parse(sent), OFFER_EXPIRY_WORKING_DAYS, _lead_tz(lead), _lead_country(lead)) <= clock()
    if not expired:
        return {"ok": True, "done": False, "payment_status": deal.get("payment_status"),
                "next": "not paid yet: wait; due_follow_ups sends reminders, and after 7 working days it goes to a rep"}
    log_event(lead_id, "deal", "skipped", "not paid: handing to a rep")
    return {"ok": True, "done": False, "next": "assign_rep"}
