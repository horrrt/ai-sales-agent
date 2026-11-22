"""Different kinds of customers buying the agent: 10 ICPs, 5 products, 4 pricing models.

data/customers/ has 10 customer types (two per product). The matrix test combines every ICP with every
product and every pricing model (200 briefs) and runs what the code decides on each: the brief check, the
pricing review, the ICP gate (d1 is a stub that applies the ICP it is sent, so the test checks the right
ICP reaches it), drafting, a quote and auto-close.

    cd mcp && uv run --extra dev pytest -q
"""
import copy
import json
from pathlib import Path

import pytest

from sales_tools import decide as decide_mod
from sales_tools import store
from sales_tools.tools import deals, onboarding, outreach, positioning, research

from test_pipeline import fresh, stub  # noqa: F401

REPO = Path(__file__).resolve().parents[2]
CUSTOMERS = {p.stem: json.loads(p.read_text()) for p in sorted((REPO / "data/customers").glob("*.json"))}
PRODUCT_OF = {cid: b["company_name"] for cid, b in CUSTOMERS.items()}
PRODUCTS = {b["company_name"]: {k: b[k] for k in ("company_name", "offer", "product_info", "website", "keywords",
                                                  "exclusions", "sender")} for b in CUSTOMERS.values()}
PRICINGS = {}
for b in CUSTOMERS.values():
    PRICINGS.setdefault(b["_note"].split("pricing ")[1].split(" ")[0], {"pricing": b["pricing"], "auto_close": b["auto_close"]})
ICPS = {cid: b["icp"] for cid, b in CUSTOMERS.items()}


def test_the_test_customers_cover_10_icps_5_products_and_4_pricing_models():
    assert len(ICPS) == 10 and len({json.dumps(i, sort_keys=True) for i in ICPS.values()}) == 10
    assert len(PRODUCTS) == 5 and len(PRICINGS) == 4
    assert set(PRICINGS) == {"flat", "per_seat", "tiered", "setup_retainer"}


def use(brief: dict) -> None:
    store.BRIEF.clear()
    store.BRIEF.update(copy.deepcopy(brief))


def icp_judge(question, evidence, options):
    """Stand-in for d1 at the ICP gate: applies the ICP it was sent, so a wrong or missing ICP fails."""
    icp, c = evidence["icp"], evidence["candidate"]
    lo, hi = icp["employees"]
    return {"answer": c["company"]["country"] in icp["countries"] and lo <= c["company"]["employees"] <= hi
            and c["company"]["industry"] in icp["industries"] and c["person"]["title"] in icp["titles"],
            "confidence": 0.9, "reason": "stub", "backend": "stub"}


def candidates(brief: dict, tag: str) -> tuple[str, str]:
    """A prospect inside the brief's ICP and one outside it (wrong country), with research to write from."""
    icp, cid = brief["icp"], brief["customer_id"]
    fit, miss = f"F-{tag}", f"N-{tag}"
    size = (icp["employees_min"] + icp["employees_max"]) // 2
    outside = next(c for c in ("US", "BR", "JP") if c not in icp["countries"])
    for lid, country in ((fit, icp["countries"][0]), (miss, outside)):
        store.SOURCE[lid] = {
            "lead_id": lid, "customer_id": cid,
            "person": {"name": "Alex Moe", "title": icp["titles"][0], "email": f"alex@{lid.lower()}.sandbox.test"},
            "company": {"name": f"{lid} Co", "domain": f"{lid.lower()}.example", "country": country, "employees": size,
                        "industry": icp["industries"][0]},
            "research": {"summary": "A company in the ICP.", "enough_to_personalize": True,
                         "facts": [{"fact": f"{icp['triggers'][0]} (announced this month)", "source_url": f"https://{lid.lower()}.example/news"}]}}
    return fit, miss


def expected_total(spec: dict, quantity: int) -> float:
    return round(spec["price_per_month"] * quantity * spec.get("min_term_months", 1) + (spec.get("setup_fee") or 0), 2)


def run_customer(brief: dict, tag: str) -> None:
    use(brief)
    check = onboarding.check_brief()
    assert check["ready"], check["missing"]
    assert onboarding.review_pricing()["ok"]
    fit, miss = candidates(brief, tag)
    found = {c["lead_id"] for c in research.find_leads(brief["customer_id"], 50)}
    assert {fit, miss} <= found and all(store.SOURCE[x]["customer_id"] == brief["customer_id"] for x in found)
    decide_mod.BACKEND = icp_judge
    assert research.add_lead(fit)["status"] == "added"
    assert research.add_lead(miss)["status"] == "skipped"
    research.research_target(fit)
    stub(True, True, True)
    assert research.check_ready_to_write(fit)["ready"]
    fact = store.LEADS[fit]["research"]["facts"][0]["fact"]
    assert positioning.save_positioning(fit, f"{brief['company_name']} fits: {fact}", [fact], 0.7)["ok"]
    d = outreach.save_draft(fit, f"A note from {brief['company_name']}", brief["offer"])
    assert d["ok"], d
    assert d["to"][0].endswith(".sandbox.test") and store.OUTBOX == []
    # A quote on every plan with a fixed price, at the plan's minimum quantity; custom plans are left to a rep.
    deals.open_deal(fit)
    for name, spec in brief["pricing"]["plans"].items():
        q = spec.get("min_quantity", 1)
        r = deals.draft_quote(fit, name, quantity=q)
        if spec.get("custom"):
            assert "custom price" in r["error"]
        else:
            assert r["ok"] and r["quote"]["total_eur"] == expected_total(spec, q), (name, r)
    # Auto-close: offered on an allowed plan under the cap, or never.
    ac = brief["auto_close"]
    store.LEADS[fit].pop("deal")
    stub(True)
    small = deals.check_small_deal(fit, estimated_value_eur=min(
        (onboarding._plan_year(brief["pricing"]["plans"][p]) for p in ac.get("plans") or []), default=10 ** 9))
    if ac.get("enabled"):
        assert small["next"] == "send_offer", small
        offer = deals.send_offer(fit)
        assert offer["ok"] and offer["quote"]["plan"] in ac["plans"]
        mail = store.OUTBOX[-1]
        brand = brief["company_name"].split(" ")[0]
        assert brand in mail["body"] or brand in mail["subject"] or mail["subject"].startswith("Re:")
        assert "Kvitto" not in mail["body"] or brand == "Kvitto"  # no other customer's name in the offer
        assert onboarding._plan_year(brief["pricing"]["plans"][offer["quote"]["plan"]]) <= ac["max_annual_value_eur"]
    else:
        assert small["next"] == "assign_rep"


@pytest.mark.parametrize("cid", list(CUSTOMERS))
def test_each_customer_type_runs_end_to_end(cid):
    run_customer(CUSTOMERS[cid], cid[:12])


@pytest.mark.parametrize("pricing", list(PRICINGS))
@pytest.mark.parametrize("product", list(PRODUCTS))
@pytest.mark.parametrize("icp", list(ICPS))
def test_matrix_every_icp_with_every_product_and_pricing(icp, product, pricing):
    brief = {**CUSTOMERS[icp], **PRODUCTS[product], **PRICINGS[pricing], "icp": ICPS[icp],
             "customer_id": f"C-mx-{icp}-{product[:4]}-{pricing}"}
    run_customer(brief, f"{icp[:6]}{product[:3]}{pricing[:3]}")


def test_quotes_follow_each_pricing_model():
    use(CUSTOMERS["kvitto-nordic"])
    fit, _ = candidates(store.BRIEF, "q")
    decide_mod.BACKEND = icp_judge
    research.add_lead(fit)
    deals.open_deal(fit)
    use(CUSTOMERS["byggtid-contractors"])  # per user, at least 5
    assert "at least 5" in deals.draft_quote(fit, "team", quantity=3)["error"]
    assert deals.draft_quote(fit, "team", quantity=8, term_months=12)["quote"]["total_eur"] == 12 * 8 * 12
    use(CUSTOMERS["lagerlys-ecom"])  # tiered with a custom Enterprise
    assert "custom price" in deals.draft_quote(fit, "enterprise")["error"]
    assert deals.draft_quote(fit, "pro")["quote"]["total_eur"] == 290 * 12
    use(CUSTOMERS["bokfor-startups"])  # setup fee + monthly, the discount never touches the setup fee
    q = deals.draft_quote(fit, "standard", discount_pct=10)["quote"]
    assert q["setup_fee_eur"] == 500 and q["total_eur"] == round(350 * 6 * 0.9 + 500, 2) and q["needs_admin"] is True


def test_a_brief_for_one_customer_never_drafts_for_another_customers_lead():
    use(CUSTOMERS["klinikflow-dental"])
    fit, _ = candidates(store.BRIEF, "x")
    decide_mod.BACKEND = icp_judge
    research.add_lead(fit)
    research.research_target(fit)
    store.LEADS[fit]["positioning"] = {"angle": "an angle", "evidence": ["x"], "confidence": 0.5}
    use(CUSTOMERS["bokfor-agencies"])
    r = outreach.save_draft(fit, "Subject", "Body")
    assert r["ok"] is False and "customer_id mismatch" in r["missing_fields"]
