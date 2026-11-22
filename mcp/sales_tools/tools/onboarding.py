"""Onboarding: the customer who buys the agent sets up what it sells, to whom, and at what price.

What the customer gives: the offer and pricing, the ICP (the company and the person inside it), and
their product or service (in their own words, or from their website). The onboarding agent helps with
each part: it drafts product info from what they say or from their site, checks the pricing (and that
auto-close can actually fire), and proposes an ICP from their won and lost deals.

- check_brief: what's missing or inconsistent before the pipeline can run
- import_website: the customer's site (sandbox: pages stored in the state; no live fetching)
- review_pricing: yearly values per plan, discount rules, whether auto-close fits under its cap
- suggest_icp_from_deals: won and lost leads by country, size, industry and title
- update_brief: change one section; asks a person first, and only admins may do it
"""
import re

from .. import access
from ..app import mcp
from ..store import BRIEF, LEADS, WEBSITES, log_event

SECTIONS = {"company_name": str, "website": (str, list), "offer": str, "product_info": str, "tone": str,
            "exclusions": list, "won_deals": list, "icp": dict, "pricing": dict, "auto_close": dict, "sender": dict,
            "approvers": dict}
SIZE_BANDS = ((1, 9, "1-9"), (10, 49, "10-49"), (50, 249, "50-249"), (250, 10 ** 9, "250+"))
MIN_DEALS_FOR_ICP = 5


def _todo(v) -> bool:
    return isinstance(v, str) and (not v.strip() or v.strip().upper().startswith("TODO"))


def _todos(obj, path="") -> list[str]:
    if isinstance(obj, dict):
        return [p for k, v in obj.items() if not str(k).startswith("_") for p in _todos(v, f"{path}.{k}" if path else k)]
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in _todos(v, f"{path}[{i}]")]
    return [path] if _todo(obj) else []


def _plan_year(spec: dict, quantity: int | None = None) -> float | None:
    """First-year value of a plan at its minimum quantity: 12 months (or its minimum term, if longer) plus setup."""
    if spec.get("price_per_month") is None:
        return None
    q = quantity or spec.get("min_quantity", 1)
    months = max(12, spec.get("min_term_months", 1))
    return round(spec["price_per_month"] * q * 12 + (spec.get("setup_fee") or 0), 2) if months == 12 else \
        round(spec["price_per_month"] * q * months + (spec.get("setup_fee") or 0), 2)


def _pricing_findings(pricing: dict, auto_close: dict) -> tuple[list[dict], dict]:
    findings, plans, values = [], (pricing or {}).get("plans") or {}, {}
    if not plans:
        findings.append({"severity": "error", "text": "no plans: add at least one plan with a monthly price (or custom)"})
    for name, spec in plans.items():
        if spec.get("price_per_month") is None and not spec.get("custom"):
            findings.append({"severity": "error", "text": f"{name}: no price_per_month; set one, or custom: true for a price a rep sets"})
        elif isinstance(spec.get("price_per_month"), (int, float)) and spec["price_per_month"] < 0:
            findings.append({"severity": "error", "text": f"{name}: the price can't be negative"})
        if spec.get("min_term_months", 1) < 1:
            findings.append({"severity": "error", "text": f"{name}: min_term_months must be at least 1"})
        if spec.get("min_quantity") and spec.get("max_quantity") and spec["min_quantity"] > spec["max_quantity"]:
            findings.append({"severity": "error", "text": f"{name}: min_quantity is above max_quantity"})
        values[name] = _plan_year(spec)
    lim, top = (pricing or {}).get("max_discount_pct_without_approval", 0), (pricing or {}).get("max_discount_pct", 0)
    if lim > top:
        findings.append({"severity": "error", "text": f"the discount a rep may give alone ({lim}%) is above the maximum ({top}%)"})
    if top > 40:
        findings.append({"severity": "warning", "text": f"a maximum discount of {top}% is unusually high"})
    ac = auto_close or {}
    if ac.get("enabled"):
        cap = ac.get("max_annual_value_eur")
        allowed = [p for p in ac.get("plans") or [] if p in plans]
        unknown = [p for p in ac.get("plans") or [] if p not in plans]
        if unknown:
            findings.append({"severity": "error", "text": f"auto-close lists plans that don't exist: {unknown}"})
        if not isinstance(cap, (int, float)):
            findings.append({"severity": "error", "text": "auto-close is on but max_annual_value_eur is not a number"})
        elif not allowed:
            findings.append({"severity": "error", "text": "auto-close is on but auto_close.plans names no plan it may sell"})
        else:
            fits = [p for p in allowed if values.get(p) is not None and values[p] <= cap]
            if not fits:
                cheapest = min((values[p] for p in allowed if values.get(p) is not None), default=None)
                findings.append({"severity": "error",
                                 "text": f"auto-close can never fire: the cheapest auto-close plan is EUR {cheapest:,.0f} in the "
                                         f"first year, over the EUR {cap:,.0f} cap. Add a self-serve plan under EUR {cap / 12:,.0f} "
                                         f"a month, or raise the cap" if cheapest is not None else
                                         "auto-close can never fire: its plans have no fixed price"})
            for p in allowed:
                if plans[p].get("custom") or plans[p].get("price_per_month") is None:
                    findings.append({"severity": "error", "text": f"{p} has a custom price, so auto-close can't sell it"})
        if not str(ac.get("checkout_url", "")).startswith("https://"):
            findings.append({"severity": "error", "text": "auto-close needs a checkout_url (https)"})
        elif ".sandbox.test" not in ac.get("checkout_url", ""):
            findings.append({"severity": "warning", "text": "the checkout_url isn't a sandbox link: real payments during the course?"})
    return findings, values


@mcp.tool()
def check_brief() -> dict:
    """Is the customer brief complete enough to run the pipeline? Lists what's missing (blocks outreach) and
    what looks wrong (warnings), section by section: product, offer, ICP company, ICP person, pricing,
    auto-close, sender and approvers."""
    missing, warnings = [], []
    b = BRIEF
    for f, n in (("company_name", 2), ("offer", 40), ("product_info", 80), ("tone", 20)):
        v = b.get(f)
        if not isinstance(v, str) or _todo(v) or len(v.strip()) < n:
            missing.append(f"{f}: missing or too short")
    icp = b.get("icp") or {}
    if not icp.get("countries"):
        missing.append("icp.countries (the company): which countries to sell in")
    elif any(not re.fullmatch(r"[A-Z]{2}", str(c)) for c in icp["countries"]):
        warnings.append("icp.countries should be two-letter codes, like SE or DE")
    lo, hi = icp.get("employees_min"), icp.get("employees_max")
    if not isinstance(lo, int) or not isinstance(hi, int):
        missing.append("icp.employees_min and employees_max (the company): the size range")
    elif lo > hi:
        missing.append("icp.employees_min is above employees_max")
    if not icp.get("industries"):
        missing.append("icp.industries (the company)")
    if not icp.get("titles"):
        missing.append("icp.titles (the person): who in the company to write to")
    if not icp.get("triggers"):
        warnings.append("icp.triggers: no buying triggers, so research can't tell a good moment from a bad one")
    if not b.get("website"):
        warnings.append("website: none, so product info can only come from what the customer writes")
    findings, values = _pricing_findings(b.get("pricing") or {}, b.get("auto_close") or {})
    missing += [f"pricing: {f['text']}" for f in findings if f["severity"] == "error"]
    warnings += [f"pricing: {f['text']}" for f in findings if f["severity"] == "warning"]
    sender = b.get("sender") or {}
    if not str(sender.get("email", "")).endswith(".test"):
        missing.append("sender.email: a sandbox .test address during the course")
    for kind, who in (b.get("approvers") or {}).items():
        if access.resolve(who) is None:
            warnings.append(f"approvers.{kind}: {who} is not in the team roster")
    todo = [p for p in _todos(b) if not any(p.startswith(m.split(":")[0].split(" ")[0]) for m in missing)]
    missing += [f"{p}: still TODO" for p in todo]
    return {"ok": True, "ready": not missing, "missing": missing, "warnings": warnings,
            "plan_first_year_eur": values, "customer_id": b.get("customer_id"),
            "next": "ask the customer for what's missing, one section at a time" if missing else "ready: find-leads can run"}


@mcp.tool()
def import_website(url: str) -> dict:
    """The customer's website, to draft product info from. Sandbox: only sites stored for the run; no live
    fetching. Returns the pages' text. Summarize it into product_info with the customer, then update_brief."""
    key = re.sub(r"^https?://", "", url.strip().lower()).split("/")[0].removeprefix("www.")
    site = WEBSITES.get(key)
    if not site:
        return {"ok": False, "error": "not available in the sandbox",
                "next": "ask the customer to paste the text of their product and pricing pages"}
    return {"ok": True, "domain": key, **site,
            "next": "draft product_info and the offer from these pages, show the customer, then update_brief"}


@mcp.tool()
def review_pricing(pricing: dict | None = None, auto_close: dict | None = None) -> dict:
    """Check a pricing proposal (or the brief's current pricing): first-year value of every plan, discount
    rules, and whether auto-close can fire under its cap. Pass the customer's proposal to try it before
    saving: it is merged into the saved pricing and auto-close, as update_brief would. Nothing is saved here."""
    p = {**(BRIEF.get("pricing") or {}), **(pricing or {})}         # a proposal changes what's saved, like update_brief
    ac = {**(BRIEF.get("auto_close") or {}), **(auto_close or {})}
    findings, values = _pricing_findings(p, ac)
    examples = {name: {"monthly_eur": (spec.get("price_per_month") or 0) * spec.get("min_quantity", 1) if spec.get("price_per_month") is not None else None,
                       "first_year_eur": values.get(name), "setup_fee_eur": spec.get("setup_fee", 0),
                       "min_quantity": spec.get("min_quantity", 1), "min_term_months": spec.get("min_term_months", 1),
                       "custom": bool(spec.get("custom"))}
                for name, spec in (p.get("plans") or {}).items()}
    return {"ok": not any(f["severity"] == "error" for f in findings), "findings": findings, "plans": examples,
            "auto_close_enabled": bool(ac.get("enabled")), "cap_eur": ac.get("max_annual_value_eur"),
            "next": "explain the findings to the customer; save with update_brief pricing (and auto_close) once they agree"}


def _band(n) -> str:
    for lo, hi, name in SIZE_BANDS:
        if isinstance(n, int) and lo <= n <= hi:
            return name
    return "unknown"


@mcp.tool()
def suggest_icp_from_deals() -> dict:
    """Evidence for the ICP from the customer's own results: won and lost leads by country, company size,
    industry and the person's title, with loss reasons. Propose an ICP from it with the customer; save with
    update_brief icp. With fewer than 5 closed leads, ask the customer about their best customers instead."""
    rows = [l for l in LEADS.values() if l.get("status") in ("won", "lost")]
    def tally(key):
        out = {}
        for l in rows:
            k = key(l)
            out.setdefault(k, {"won": 0, "lost": 0})
            out[k]["won" if l["status"] == "won" else "lost"] += 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["won"]))
    c = lambda l: l.get("company") or {}  # noqa: E731
    reasons = {}
    for l in rows:
        if l["status"] == "lost" and l.get("loss_reason"):
            reasons[l["loss_reason"]] = reasons.get(l["loss_reason"], 0) + 1
    enough = len(rows) >= MIN_DEALS_FOR_ICP
    return {"ok": True, "closed_leads": len(rows), "won": sum(l["status"] == "won" for l in rows),
            "lost": len(rows) - sum(l["status"] == "won" for l in rows),
            "by_country": tally(lambda l: c(l).get("country")), "by_size": tally(lambda l: _band(c(l).get("employees"))),
            "by_industry": tally(lambda l: c(l).get("industry")),
            "by_title": tally(lambda l: (l.get("person") or {}).get("title")), "loss_reasons": reasons,
            "won_deals_notes": BRIEF.get("won_deals") or [], "current_icp": BRIEF.get("icp"),
            "enough_data": enough,
            "next": "propose an ICP: where they win, the size they win at, who signs; update_brief icp" if enough else
                    "too few closed leads to trust: ask the customer about their three best customers and why they bought"}


@mcp.tool()
def update_brief(section: str, value, by: str = "") -> dict:
    """Change one section of the customer brief. This tool always asks a person first, and only an admin
    (by) may change the brief. Sections: company_name, website, offer, product_info, tone, exclusions,
    won_deals, icp, pricing, auto_close, sender, approvers. A dict (icp, pricing, auto_close, sender,
    approvers) is merged into what's there. Returns check_brief afterwards, so you see what's still missing."""
    if section not in SECTIONS:
        return {"ok": False, "error": f"unknown section {section!r}", "sections": sorted(SECTIONS)}
    if not isinstance(value, SECTIONS[section]):
        return {"ok": False, "error": f"{section} must be a {SECTIONS[section]}"}
    denied = access.check(by, "manage_brief")
    if denied:
        return denied
    if section == "pricing" or section == "auto_close":
        merged_p = {**(BRIEF.get("pricing") or {}), **(value if section == "pricing" else {})}
        merged_a = {**(BRIEF.get("auto_close") or {}), **(value if section == "auto_close" else {})}
        errors = [f["text"] for f in _pricing_findings(merged_p, merged_a)[0] if f["severity"] == "error"]
        if errors:
            return {"ok": False, "error": "pricing_problems", "problems": errors,
                    "next": "fix these with the customer (review_pricing helps), then save again"}
    old = BRIEF.get(section)
    if isinstance(value, dict) and isinstance(old, dict):
        BRIEF[section] = {**old, **value}
    else:
        BRIEF[section] = value
    log_event("brief", "onboarding", "success", f"brief.{section} updated", by=access.resolve(by),
              before=old, after=BRIEF[section])
    return {"ok": True, "section": section, "value": BRIEF[section], "check": check_brief()}
