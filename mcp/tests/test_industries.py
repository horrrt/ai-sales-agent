"""ICP industries come from one list (data/industries.json), so the ICP check, price rules and the won-deal
evidence compare the same names. Use cases in docs/use-cases.md.

    cd mcp && uv run --extra dev pytest -q
"""
import json
import re
from pathlib import Path

import pytest

from sales_tools import decide as decide_mod
from sales_tools import industries, store
from sales_tools.tools import deals, onboarding, research

from test_pipeline import fresh, meeting_held  # noqa: F401

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("text,want", [
    ("Online brands / e-commerce (online shops)", ["E-commerce"]),  # what the agent wrote in the Fleet journey
    ("D2C", ["E-commerce"]), ("Nordic e-commerce brands", ["E-commerce"]), ("Food retail (B2C)", ["Retail"]),
    ("Marketing & creative agencies", ["Marketing agencies"]), ("B2B SaaS companies", ["B2B SaaS"]),
    ("Insurtech", ["Fintech"]), ("Electricians and building contractors", ["Electrical installation", "Construction"]),
])
def test_free_text_maps_onto_the_list(text, want):
    assert industries.normalize(text) == (want, [])


def test_the_icp_is_saved_with_list_names_and_unknown_industries_are_refused():
    r = onboarding.update_brief("icp", {"industries": ["Online brands / e-commerce (online shops)", "DTC"]}, by="rep-1")
    assert r["ok"] and store.BRIEF["icp"]["industries"] == ["E-commerce"]
    r = onboarding.update_brief("icp", {"industries": ["Startups"]}, by="rep-1")
    assert r["error"] == "unknown_industries" and "Startups" in r["unknown"]
    assert onboarding.list_industries()["industries"] == industries.names()


def test_check_brief_flags_names_that_are_not_on_the_list():
    store.BRIEF["icp"]["industries"] = ["Online brands / e-commerce (online shops)"]  # written into the file by hand
    assert any("not on the industry list" in m for m in onboarding.check_brief()["missing"])


def test_the_icp_gate_compares_list_names_whatever_the_source_calls_the_industry():
    store.BRIEF["icp"]["industries"] = ["E-commerce"]
    store.SOURCE["S-1"] = {"lead_id": "S-1", "customer_id": "C-demo", "person": {"title": "CEO", "email": "a@s1.sandbox.test"},
                           "company": {"name": "Shop", "country": "SE", "employees": 20, "industry": "Online shops"}}
    seen = []
    decide_mod.BACKEND = lambda q, e, o: seen.append(e) or {"answer": True, "confidence": 0.9, "reason": "", "backend": "stub"}
    research.add_lead("S-1")
    assert seen[0]["industry_on_list"] == ["E-commerce"] and seen[0]["industry_in_icp"] is True
    assert store.LEADS["S-1"]["company"]["industries"] == ["E-commerce"]


def test_a_price_rule_on_an_industry_matches_its_aliases():
    meeting_held()
    store.LEADS["L-001"]["company"]["industry"] = "Charity"  # the Nonprofit rule: -20%
    assert deals.draft_quote("L-001", "annual")["quote"]["price_rules"] == [{"label": "Nonprofit", "pct": -20.0}]


def _industries_in(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "industry" and isinstance(v, str):
                yield v
            elif k == "industries" and isinstance(v, list):
                yield from (x for x in v if isinstance(x, str))
            else:
                yield from _industries_in(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _industries_in(x)


def test_every_industry_in_the_briefs_is_a_list_name_and_every_prospect_maps():
    for p in [REPO / "data/customer_brief.demo.json", *sorted((REPO / "data/customers").glob("*.json"))]:
        icp = json.loads(p.read_text())["icp"]["industries"]
        assert set(icp) <= set(industries.names()), (p.name, icp)
    unmapped = set()
    found = [(p.name, v) for p in [REPO / "data/sample_leads.json"] for v in _industries_in(json.loads(p.read_text()))]
    for p in (REPO / "evals/fleet/cases").glob("*/*.yaml"):  # prospects in the Fleet cases ("industry: ..." lines)
        found += [(p.name, m.strip().strip("'\"")) for m in re.findall(r"^\s*industry:\s*(.+)$", p.read_text(), re.M)]
    for name, v in found:
        if not industries.normalize(v)[0]:
            unmapped.add((name, v))
    assert not unmapped, unmapped
