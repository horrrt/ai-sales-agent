"""A first-time customer's whole setup, and prices set per prospect by the customer's price rules.

Use cases in docs/use-cases.md. No model calls.

    cd mcp && uv run --extra dev pytest -q
"""
import json
from pathlib import Path

from sales_tools import decide as decide_mod
from sales_tools import store
from sales_tools.tools import deals, onboarding, outreach, positioning, research

from test_pipeline import fresh, meeting_held, stub  # noqa: F401
from test_customers import icp_judge

REPO = Path(__file__).resolve().parents[2]


def prospect(lead_id="L-001", employees=None, country=None, industry=None):
    meeting_held(lead_id)
    c = store.LEADS[lead_id]["company"]
    c.update({k: v for k, v in (("employees", employees), ("country", country), ("industry", industry)) if v is not None})
    return lead_id


# --- Price rules ---------------------------------------------------------------------------------

def test_a_small_startup_gets_the_startup_price_and_the_quote_says_why():
    prospect(employees=6)
    q = deals.draft_quote("L-001", "annual")["quote"]
    assert q["price_rules"] == [{"label": "Startup (under 10 people)", "pct": -10.0}]
    assert q["total_eur"] == round(7800 * 0.9, 2) and q["needs_admin"] is False
    assert "rules: Startup" in store.INTERACTIONS[-1]["message"]


def test_a_large_company_pays_more_and_a_two_year_contract_pays_less():
    prospect(employees=400)
    assert deals.draft_quote("L-001", "annual")["quote"]["total_eur"] == round(7800 * 1.15, 2)
    q = deals.draft_quote("L-001", "annual", term_months=24)["quote"]
    assert q["rules_pct"] == 5 and q["total_eur"] == round(650 * 24 * 1.05, 2)  # +15% size, -10% two years


def test_rules_and_a_reps_discount_add_up_and_the_limits_still_hold():
    prospect(employees=6)
    q = deals.draft_quote("L-001", "annual", term_months=24)["quote"]  # -10 startup, -10 two years
    assert q["adjustment_pct"] == -20 and q["needs_admin"] is True     # past the 10% a rep may give alone
    r = deals.draft_quote("L-001", "annual", term_months=24, discount_pct=5)
    assert r["ok"] is False and "more than the maximum" in r["error"]
    assert deals.draft_quote("L-001", "annual", apply_price_rules=False)["quote"]["total_eur"] == 7800


def test_rules_never_go_past_the_maximum_discount_on_their_own():
    store.BRIEF["pricing"]["adjustments"].append({"label": "Partner", "if": {"country_in": ["SE"]}, "pct": -15})
    prospect(employees=6, industry="Nonprofit")  # -10 -20 -15 = -45, capped at the 20% maximum
    q = deals.draft_quote("L-001", "annual")["quote"]
    assert q["rules_pct"] == -20 and q["total_eur"] == round(7800 * 0.8, 2)
    assert any("can reach 55% off" in f["text"] for f in onboarding.review_pricing()["findings"])


def test_suggest_price_answers_without_saving_anything():
    prospect(employees=6)
    r = deals.suggest_price("L-001", "monthly")
    assert r["total_eur"] == round(790 * 0.9, 2) and "quote" not in store.LEADS["L-001"]["deal"]


def test_auto_close_applies_the_rules_but_never_past_what_needs_an_admin():
    store.BRIEF["auto_close"]["enabled"] = True
    lead = prospect(employees=6)
    store.LEADS[lead]["deal"].update(small_deal=True, auto_close=True)
    r = deals.send_offer(lead)
    assert r["quote"]["total_eur"] == round(99 * 0.9, 2)  # Starter with the startup rule
    store.BRIEF["pricing"]["adjustments"].append({"label": "Spring offer", "if": {"plan_in": ["starter"]}, "pct": -5})
    store.LEADS[lead]["deal"]["quote"] = None
    assert deals.send_offer(lead)["next"] == "assign_rep"  # -15% is past the 10% limit: a rep sells it


def test_a_broken_price_rule_is_caught_before_saving():
    bad = {"adjustments": [{"label": "Big", "if": {"revenue_min": 10}, "pct": 10}, {"label": "no pct", "if": {"employees_min": 5}}]}
    texts = [f["text"] for f in onboarding.review_pricing(bad)["findings"]]
    assert any("unknown conditions ['revenue_min']" in t for t in texts) and any("needs a label" in t for t in texts)
    assert onboarding.update_brief("pricing", bad, by="rep-1")["error"] == "pricing_problems"


# --- A first-time customer's setup ------------------------------------------------------------------

def blank():
    store.BRIEF.clear()
    store.BRIEF.update(json.loads((REPO / "data/customer_brief.example.json").read_text()) | {"customer_id": "C-new"})
    store.REPS.clear()


def test_the_first_person_on_an_empty_team_is_an_admin_who_adds_themself():
    blank()
    r = onboarding.add_team_member("rep-1", "Mette Holm", "mette@lagerlys.sandbox.test", role="rep", countries=["SE"])
    assert r["error"] == "the first person on the team must be an admin"
    assert onboarding.add_team_member("rep-1", "Mette Holm", "mette@lagerlys.sandbox.test", role="admin",
                                      countries=["SE"], timezone="Europe/Stockholm")["ok"]
    assert onboarding.add_team_member("rep-2", "Jonas Lind", "jonas@lagerlys.sandbox.test", countries=["DK"])["error"] == "unknown_person"
    assert onboarding.add_team_member("rep-2", "Jonas Lind", "jonas@lagerlys.sandbox.test", countries=["DK"], by="rep-1")["ok"]


def test_a_team_member_needs_what_the_pipeline_uses():
    r = onboarding.add_team_member("rep-9", "Ola", "ola@gmail.com", role="boss", timezone="Mars/Olympus", countries=[],
                                   max_leads=0, working_hours=["17:00", "09:00"], by="rep-1")
    assert {"a sandbox .test email", "a role from ['admin', 'rep', 'junior', 'finance', 'viewer']",
            "a time zone like Europe/Oslo", 'working_hours like ["09:00", "17:00"]'} <= set(r["needs"])
    r = onboarding.add_team_member("rep-9", "Ola", "ola@x.sandbox.test", countries=[], max_leads=0, by="rep-1")
    assert set(r["needs"]) == {"the countries they sell to", "max_leads: how many open leads they can take"}
    assert onboarding.add_team_member("fin-2", "Pia", "pia@x.sandbox.test", role="finance", takes_leads=False, by="rep-1")["ok"]


def test_the_last_admin_cannot_be_removed_and_only_admins_change_the_team():
    assert onboarding.update_team_member("rep-2", {"max_leads": 10}, by="rep-2")["error"] == "not_allowed"
    assert onboarding.update_team_member("rep-2", {"max_leads": 10, "countries": ["SE", "NO"]}, by="rep-1")["ok"]
    assert onboarding.update_team_member("rep-1", {"role": "rep"}, by="rep-1")["error"].startswith("this is the only admin")


def test_setup_status_lists_what_is_left_until_it_is_ready():
    blank()
    s = onboarding.get_setup_status()
    assert s["ready"] is False and {i["item"] for i in s["items"] if not i["done"]} >= {"brief (product, offer, pricing, ICP)",
                                                                                         "an admin on the team", "reps who take leads"}
    store.BRIEF.clear()
    store.BRIEF.update(json.loads((REPO / "data/customer_brief.demo.json").read_text()))
    store.REPS.update(json.loads((REPO / "data/reps.demo.json").read_text()))
    assert onboarding.get_setup_status()["ready"] is True
    store.BRIEF["icp"]["countries"] = ["SE", "NO", "FI", "DE"]
    item = next(i for i in onboarding.get_setup_status()["items"] if i["item"] == "every ICP country has a rep")
    assert item["done"] is False and "DE" in item["needs"][0]


def test_a_first_time_customer_goes_from_nothing_to_the_first_draft():
    """Everything a new customer sets up with the agent, in the order @onboarding asks for it, then the pipeline."""
    blank()
    assert onboarding.add_team_member("rep-1", "Mette Holm", "mette@lagerlys.sandbox.test", role="admin", countries=["SE"],
                                      timezone="Europe/Stockholm", max_leads=6)["ok"]
    store.WEBSITES["lagerlys.example"] = {"title": "Lagerlys", "pages": [{"path": "/", "text": "One live stock count for every channel."}]}
    assert onboarding.import_website("https://www.lagerlys.example")["ok"]
    for section, value in [
        ("company_name", "Lagerlys AB"), ("website", "https://www.lagerlys.example"),
        ("product_info", "Lagerlys keeps Shopify, Amazon and the warehouse on one live stock count, warns before a product sells out "
                         "and suggests reorder quantities. Set up in a day."),
        ("offer", "Lagerlys stops stock-outs and overselling with one live stock count across every channel."),
        ("tone", "Plain and short. No exclamation marks. One ask."),
        ("pricing", {"plans": {"basic": {"price_per_month": 49, "min_term_months": 1, "max_quantity": 1},
                               "pro": {"price_per_month": 290, "min_term_months": 12}},
                     "adjustments": [{"label": "Startup", "if": {"employees_max": 9}, "pct": -10}],
                     "max_discount_pct_without_approval": 10, "max_discount_pct": 20, "deal_policy": "Discounts above 10% need an admin."}),
        ("auto_close", {"enabled": True, "max_annual_value_eur": 1000, "plans": ["basic"],
                        "checkout_url": "https://checkout.sandbox.test/lagerlys", "standard_terms": "Basic at list price"}),
        ("icp", {"countries": ["SE"], "employees_min": 3, "employees_max": 40, "industries": ["E-commerce"],
                 "titles": ["Founder", "COO"], "triggers": ["Opening a second sales channel"]}),
        ("sender", {"name": "Mette, Lagerlys", "email": "mette@lagerlys.sandbox.test"}),
        ("approvers", {"emails": "rep-1", "payments": "rep-1"}),
    ]:
        r = onboarding.update_brief(section, value, by="Mette")
        assert r["ok"], (section, r)
    s = onboarding.get_setup_status()
    assert s["ready"], [i for i in s["items"] if not i["done"]]
    store.SOURCE["N-1"] = {"lead_id": "N-1", "customer_id": "C-new",
                           "person": {"name": "Ida Ek", "title": "Founder", "email": "ida@n1.sandbox.test"},
                           "company": {"name": "Ekbutiken AB", "domain": "n1.example", "country": "SE", "employees": 8, "industry": "E-commerce"},
                           "research": {"summary": "An online shop", "enough_to_personalize": True,
                                        "facts": [{"fact": "Opened an Amazon store in October", "source_url": "https://n1.example/news"}]}}
    assert [c["lead_id"] for c in research.find_leads("C-new")] == ["N-1"]
    decide_mod.BACKEND = icp_judge
    assert research.add_lead("N-1")["status"] == "added"
    research.research_target("N-1")
    stub(True, True, True)
    research.check_ready_to_write("N-1")
    positioning.save_positioning("N-1", "A second channel means stock in two places", ["Opened an Amazon store in October"], 0.8)
    d = outreach.save_draft("N-1", "Your Amazon store", "Hi Ida, ...")
    assert d["ok"] and store.OUTBOX == []  # nothing goes out until Mette approves it
