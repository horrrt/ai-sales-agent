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
ICP_KEYS = {"countries", "employees_min", "employees_max", "industries", "titles", "triggers"}
# Targeting people by these is discrimination (and, in the EU, unlawful): the ICP describes companies and roles.
PROTECTED = re.compile(r"\b(gender|women|woman|female|females|male|males|men|man|mothers?|fathers?|pregnan\w*|"
                       r"aged?\s*\d+|\d+\s*years?\s*old|young(er)?\s+(founders?|people|ceos?)|old(er)?\s+(founders?|people|ceos?)|"
                       r"religio\w*|christian|muslim|jewish|hindu|ethnic\w*|race|racial|nationality|immigrants?|"
                       r"disab\w*|sexual\w*|gay|lesbian|married|single parents?)\b", re.I)


def _protected(value) -> list[str]:
    text = value if isinstance(value, str) else __import__("json").dumps(value, ensure_ascii=False)
    return sorted({m.group(0).lower() for m in PROTECTED.finditer(text)})


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
    from .deals import RULE_KEYS
    for i, r in enumerate((pricing or {}).get("adjustments") or []):
        cond = r.get("if") if isinstance(r, dict) else None
        if not isinstance(cond, dict) or not cond or not isinstance(r.get("pct"), (int, float)):
            findings.append({"severity": "error", "text": f"price rule {i + 1}: needs a label, an 'if' with conditions and a pct"})
        elif not -100 < r["pct"] <= 100:
            findings.append({"severity": "error", "text": f"price rule {r.get('label', i + 1)}: pct must be above -100 and at most 100"})
        elif set(cond) - RULE_KEYS:
            findings.append({"severity": "error", "text": f"price rule {r.get('label', i + 1)}: unknown conditions {sorted(set(cond) - RULE_KEYS)}; "
                                                          f"use {sorted(RULE_KEYS)}"})
    neg = sum(r["pct"] for r in (pricing or {}).get("adjustments") or [] if isinstance(r, dict) and isinstance(r.get("pct"), (int, float)) and r["pct"] < 0)
    if neg and -neg > (pricing or {}).get("max_discount_pct", 0):
        findings.append({"severity": "warning", "text": f"the price rules together can reach {-neg:g}% off; quotes stop at the "
                                                        f"maximum of {(pricing or {}).get('max_discount_pct', 0)}%"})
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
    if _protected({k: icp.get(k) for k in ("industries", "titles", "triggers")}):
        missing.append(f"icp targets people by {', '.join(_protected(icp))}: describe companies and roles, never personal traits")
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
    optional = ("won_deals", "exclusions", "website")  # a new customer may have none yet
    missing += [f"{p}: still TODO" for p in todo if not p.startswith(optional)]
    warnings += [f"{p}: still the example placeholder (optional)" for p in todo if p.startswith(optional)]
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
    if section == "icp":
        extra = set(value) - ICP_KEYS
        if extra:
            return {"ok": False, "error": f"the ICP has no {sorted(extra)}; it describes companies (countries, size, industries, "
                                          f"triggers) and roles (titles)", "allowed": sorted(ICP_KEYS)}
        if _protected(value):
            return {"ok": False, "error": "not_allowed_targeting", "words": _protected(value),
                    "next": "the ICP can't target people by gender, age, religion, ethnicity, nationality, disability, family "
                            "status or sexuality; describe companies and roles instead"}
    if section == "sender" and "email" in value and not str(value["email"]).endswith(".test"):
        return {"ok": False, "error": "the sender must be a sandbox .test address during the course"}
    if section == "approvers":
        unknown = [f"{k}: {v}" for k, v in value.items() if access.resolve(v) is None]
        if unknown:
            return {"ok": False, "error": "approvers must be people on the team", "unknown": unknown}
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


# --------------------------------------------------------------------------- the team and the rest of the setup

ROLES_ALLOWED = ("admin", "rep", "junior", "finance", "viewer")
MEMBER_FIELDS = {"name", "email", "role", "countries", "languages", "timezone", "working_hours", "max_leads", "takes_leads",
                 "slack_user_id", "status", "allow", "deny"}


def _member_problems(m: dict) -> list[str]:
    from zoneinfo import ZoneInfo
    out = []
    if not m.get("name"):
        out.append("a name")
    if not str(m.get("email", "")).endswith(".test"):
        out.append("a sandbox .test email")
    if m.get("role", "rep") not in ROLES_ALLOWED:
        out.append(f"a role from {list(ROLES_ALLOWED)}")
    try:
        ZoneInfo(m.get("timezone") or "")
    except Exception:
        out.append("a time zone like Europe/Oslo")
    wh = m.get("working_hours")
    if wh is not None and (not isinstance(wh, list) or len(wh) != 2 or not all(re.fullmatch(r"\d\d:\d\d", str(x)) for x in wh) or wh[0] >= wh[1]):
        out.append("working_hours like [\"09:00\", \"17:00\"]")
    if m.get("takes_leads", True) and m.get("role", "rep") not in ("finance", "viewer"):
        if not m.get("countries"):
            out.append("the countries they sell to")
        if not isinstance(m.get("max_leads"), int) or m["max_leads"] < 1:
            out.append("max_leads: how many open leads they can take")
    return out


@mcp.tool()
def add_team_member(rep_id: str, name: str, email: str, role: str = "rep", countries: list[str] | None = None,
                    timezone: str = "Europe/Copenhagen", max_leads: int = 8, working_hours: list[str] | None = None,
                    takes_leads: bool = True, languages: list[str] | None = None, by: str = "") -> dict:
    """Add someone to the team: a rep who takes leads (countries, capacity, time zone, working hours), or
    someone who doesn't (finance, a viewer). This tool always asks a person first; only an admin may change
    the team. The first person added to an empty team must be an admin, and adds themself."""
    from ..store import REPS
    from .handoff import _traffic_light
    if rep_id in REPS:
        return {"ok": False, "error": f"{rep_id} already exists; use update_team_member"}
    if not re.fullmatch(r"[a-z0-9-]{2,20}", rep_id):
        return {"ok": False, "error": "rep_id: 2-20 lower-case letters, digits or dashes, like rep-4"}
    first = not REPS
    if first and role != "admin":
        return {"ok": False, "error": "the first person on the team must be an admin"}
    m = {"name": name, "email": email, "role": role, "countries": [c.upper() for c in countries or []], "timezone": timezone,
         "max_leads": max_leads if takes_leads else 0, "working_hours": working_hours or ["09:00", "17:00"],
         "takes_leads": takes_leads, "languages": languages or ["en"], "active_leads": 0}
    problems = _member_problems(m)
    if problems:
        return {"ok": False, "error": "missing_or_wrong", "needs": problems}
    if not first:
        denied = access.check(by, "manage_team")
        if denied:
            return denied
    m["status"] = _traffic_light(m) if takes_leads else "green"
    REPS[rep_id] = m
    log_event("team", "onboarding", "success", f"{rep_id} ({name}, {role}) added", by=access.resolve(by) or rep_id)
    return {"ok": True, "rep_id": rep_id, "member": m, "setup": get_setup_status()}


@mcp.tool()
def update_team_member(rep_id: str, fields: dict, by: str = "") -> dict:
    """Change a team member: role, countries, capacity (max_leads), time zone, working hours, whether they
    take leads, or their own allow/deny actions. Asks a person first; only an admin may do it. An admin
    can't take away the last admin."""
    from ..store import REPS
    if rep_id not in REPS:
        return {"ok": False, "error": f"unknown team member {rep_id}"}
    bad = set(fields) - MEMBER_FIELDS
    if bad:
        return {"ok": False, "error": f"unknown fields {sorted(bad)}", "allowed": sorted(MEMBER_FIELDS)}
    denied = access.check(by, "manage_team")
    if denied:
        return denied
    new = {**REPS[rep_id], **fields}
    problems = _member_problems(new)
    if problems:
        return {"ok": False, "error": "missing_or_wrong", "needs": problems}
    if REPS[rep_id].get("role") == "admin" and new.get("role") != "admin" and \
            sum(1 for r in REPS.values() if r.get("role") == "admin") == 1:
        return {"ok": False, "error": "this is the only admin; add another admin first"}
    REPS[rep_id] = new
    log_event("team", "onboarding", "success", f"{rep_id} updated: {sorted(fields)}", by=access.resolve(by))
    return {"ok": True, "rep_id": rep_id, "member": new}


@mcp.tool()
def get_setup_status() -> dict:
    """Everything that has to be in place before the agent runs on its own, as a checklist: the brief
    (check_brief), the team (an admin, reps who take leads, every ICP country covered), who approves
    emails and payments, auto-close, the sender. Each item is done or says what's needed next."""
    from ..store import REPS
    brief = check_brief()
    takes = {rid: r for rid, r in REPS.items() if r.get("takes_leads", True) and r.get("role", "rep") not in ("finance", "viewer")}
    admins = [rid for rid, r in REPS.items() if r.get("role") == "admin"]
    countries = set((BRIEF.get("icp") or {}).get("countries") or [])
    covered = {c for r in takes.values() for c in r.get("countries") or []}
    ac = BRIEF.get("auto_close") or {}
    appr = BRIEF.get("approvers") or {}
    items = [
        {"item": "brief (product, offer, pricing, ICP)", "done": brief["ready"], "needs": brief["missing"]},
        {"item": "an admin on the team", "done": bool(admins), "needs": [] if admins else ["add_team_member with role admin"]},
        {"item": "reps who take leads", "done": bool(takes), "needs": [] if takes else ["add at least one rep with countries and max_leads"]},
        {"item": "every ICP country has a rep", "done": countries <= covered,
         "needs": [f"no rep covers {sorted(countries - covered)}"] if countries - covered else []},
        {"item": "who approves emails", "done": bool(appr.get("emails")) and access.resolve(appr.get("emails")) is not None,
         "needs": [] if access.resolve(appr.get("emails")) else ["update_brief approvers {emails: <a rep or admin>}"]},
        {"item": "auto-close", "done": not ac.get("enabled") or (access.resolve(appr.get("payments")) is not None),
         "needs": [] if not ac.get("enabled") or access.resolve(appr.get("payments")) else
                  ["auto-close is on: add a finance person or admin and set approvers.payments"]},
        {"item": "sender (who the emails come from)", "done": str((BRIEF.get("sender") or {}).get("email", "")).endswith(".test"),
         "needs": [] if str((BRIEF.get("sender") or {}).get("email", "")).endswith(".test") else ["update_brief sender {name, email}"]},
    ]
    ready = all(i["done"] for i in items)
    return {"ok": True, "ready": ready, "items": items, "warnings": brief["warnings"],
            "in_fleet": ["the #sales-desk Slack channel ID in the handoff prompt", "the schedules (python3 scripts/package_fleet.py prints them)"],
            "next": "everything is set: the lead search and the first outreach batch can start" if ready else
                    "work through the items that aren't done, one at a time"}
