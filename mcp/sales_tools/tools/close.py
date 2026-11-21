"""Auto-close for small, standard deals (the Notion stretch goal). Owner: Slava, payment tracking Ayaan.

Off unless the customer brief sets auto_close.enabled. With it off, read_reply sends every positive reply
to assign_rep. With it on, a positive reply goes through:

1. check_small_deal: d1 "small, standard deal?" (under the brief's price cap, no custom terms asked for).
   No: assign_rep.
2. send_offer: emails the offer and the brief's checkout link. SANDBOX: the link is a sandbox URL and
   no payment provider is called.
3. check_deal_done: d1 "deal done?" on the payment status. Paid: closed won, the rep gets a summary in
   Slack. Not paid: assign_rep, so a person takes it from there.

Payment is recorded by hand with record_payment until a payment provider (in test mode) is wired up.
"""
from ..app import mcp
from ..decide import decide
from ..store import BRIEF, LEADS, log_event, now


def _auto_close() -> dict:
    return BRIEF.get("auto_close") or {}


@mcp.tool()
def check_small_deal(lead_id: str) -> dict:
    """d1 gate "small, standard deal?" for a positive reply. Only used when the brief enables auto-close."""
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": f"unknown lead {lead_id}"}
    cfg = _auto_close()
    if not cfg.get("enabled"):
        return {"ok": True, "small_deal": False, "next": "assign_rep", "note": "auto-close is switched off in the brief"}
    evidence = {"price_cap_eur_per_year": cfg.get("max_annual_value_eur"), "standard_terms": cfg.get("standard_terms"),
                "offer": BRIEF.get("offer"), "company": lead.get("company"),
                "reply": (lead.get("outreach") or {}).get("reply_body", "")}
    d = decide("Can this deal close at the standard offer: its value is under the price cap and the prospect asks "
               "for no custom terms, security review, discount or contract changes?", evidence)
    small = bool(d["answer"])
    lead.setdefault("close", {})["small_deal"] = small
    log_event(lead_id, "close", "success" if small else "skipped",
              "small standard deal: auto-close" if small else "not a small standard deal: to a rep",
              decision={"question": "small, standard deal?", **d})
    return {"ok": True, "small_deal": small, "decision": d, "next": "send_offer" if small else "assign_rep"}


@mcp.tool()
def send_offer(lead_id: str) -> dict:
    """Email the standard offer with the checkout link (sandbox). Only after check_small_deal said yes."""
    lead = LEADS.get(lead_id)
    if lead is None:
        return {"ok": False, "error": f"unknown lead {lead_id}"}
    if not (lead.get("close") or {}).get("small_deal"):
        return {"ok": False, "error": "check_small_deal has not approved this lead for auto-close"}
    link = f"{_auto_close().get('checkout_url', 'https://checkout.sandbox.test')}?lead={lead_id}"
    lead["close"].update(offer_link=link, paid=False)
    lead["stage"] = "offer_sent"
    log_event(lead_id, "close", "success", f"offer sent with checkout link {link}", channel="email")
    return {"ok": True, "offer_link": link, "sandbox": True, "next": "check_deal_done once the prospect had time to pay"}


@mcp.tool()
def record_payment(lead_id: str, paid: bool) -> dict:
    """Record whether the checkout was paid (by a person, or later by the payment provider's webhook)."""
    lead = LEADS.get(lead_id)
    if lead is None or "close" not in lead:
        return {"ok": False, "error": "no offer was sent to this lead"}
    lead["close"]["paid"] = bool(paid)
    return {"ok": True}


@mcp.tool()
def check_deal_done(lead_id: str) -> dict:
    """d1 gate "deal done?": paid means closed won and a summary for the rep; anything else goes to a rep."""
    lead = LEADS.get(lead_id)
    if lead is None or "close" not in lead:
        return {"ok": False, "error": "no offer was sent to this lead"}
    d = decide("Has the prospect paid through the checkout link?", {"payment": {"paid": lead["close"].get("paid")}})
    if d["answer"]:
        lead["close"]["closed_at"] = now()
        lead.update(stage="closed", status="won")
        log_event(lead_id, "close", "success", "paid: closed automatically", decision={"question": "deal done?", **d})
        return {"ok": True, "done": True, "decision": d, "next": "post a short summary for the rep in #sales-desk"}
    log_event(lead_id, "close", "skipped", "not paid: handing to a rep", decision={"question": "deal done?", **d})
    return {"ok": True, "done": False, "decision": d, "next": "assign_rep"}
