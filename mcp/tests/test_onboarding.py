"""Onboarding: the customer's product, pricing and ICP, and the packaging script. Use cases in docs/use-cases.md.

    cd mcp && uv run --extra dev pytest -q
"""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from sales_tools import store
from sales_tools.tools import onboarding

from test_pipeline import fresh  # noqa: F401

REPO = Path(__file__).resolve().parents[2]


def load(path):
    store.BRIEF.clear()
    store.BRIEF.update(json.loads((REPO / path).read_text()))


def test_the_demo_brief_is_ready():
    r = onboarding.check_brief()
    assert r["ready"] is True, r["missing"]


def test_an_empty_brief_lists_what_the_customer_still_has_to_give():
    load("data/customer_brief.example.json")
    r = onboarding.check_brief()
    text = " ".join(r["missing"])
    assert r["ready"] is False
    assert not any(m.startswith("auto_close") for m in r["missing"])  # switched off: its placeholders don't block
    for part in ("offer", "product_info", "tone", "pricing: the plans are still the placeholder", "sender.email"):  # the example ICP is filled in
        assert part in text, part


def test_the_icp_needs_both_the_company_and_the_person():
    store.BRIEF["icp"] = {**store.BRIEF["icp"], "titles": []}
    assert any("icp.titles (the person)" in m for m in onboarding.check_brief()["missing"])
    store.BRIEF["icp"] = {**store.BRIEF["icp"], "titles": ["CEO"], "employees_min": 80, "employees_max": 20}
    assert any("employees_min is above" in m for m in onboarding.check_brief()["missing"])


def test_pricing_review_finds_a_cap_auto_close_can_never_reach():
    old = {"plans": {"annual": {"price_per_month": 650, "min_term_months": 12}, "monthly": {"price_per_month": 790, "min_term_months": 1}},
           "max_discount_pct_without_approval": 10, "max_discount_pct": 20}
    r = onboarding.review_pricing(old, {"enabled": True, "max_annual_value_eur": 2000, "plans": ["monthly"],
                                        "checkout_url": "https://checkout.sandbox.test/x"})
    assert r["ok"] is False and any("can never fire" in f["text"] and "EUR 167 a month" in f["text"] for f in r["findings"])
    assert r["plans"]["monthly"]["first_year_eur"] == 9480
    ok = onboarding.review_pricing()  # the demo's own pricing, with Starter
    assert ok["ok"] is True and ok["plans"]["starter"]["first_year_eur"] == 1188


def test_pricing_review_catches_inconsistent_discounts_and_custom_auto_close_plans():
    p = {"plans": {"enterprise": {"price_per_month": None, "custom": True}, "x": {"min_term_months": 1}},
         "max_discount_pct_without_approval": 30, "max_discount_pct": 20}
    texts = [f["text"] for f in onboarding.review_pricing(p, {"enabled": True, "max_annual_value_eur": 5000,
                                                              "plans": ["enterprise"], "checkout_url": "https://x"})["findings"]]
    assert any("above the maximum" in t for t in texts) and any("x: no price_per_month" in t for t in texts)
    assert any("custom price, so auto-close can't sell it" in t for t in texts)


def test_only_an_admin_can_change_the_brief_and_it_survives_a_restart(tmp_path, monkeypatch):
    assert onboarding.update_brief("tone", "Warm and short.", by="rep-2")["error"] == "not_allowed"
    assert onboarding.update_brief("tone", "Warm and short.")["error"] == "unknown_person"
    r = onboarding.update_brief("tone", "Warm, short and specific. One ask per email.", by="Mette")
    assert r["ok"] and store.BRIEF["tone"].startswith("Warm") and "check" in r
    assert store.INTERACTIONS[-1]["stage"] == "onboarding" and store.INTERACTIONS[-1]["by"] == "rep-1"
    monkeypatch.setattr(store, "STATE_FILE", str(tmp_path / "s.json"))
    store.save()
    store.BRIEF["tone"] = "changed"
    store.load()
    assert store.BRIEF["tone"].startswith("Warm")


def test_a_broken_pricing_change_is_refused_with_the_reasons():
    r = onboarding.update_brief("auto_close", {"enabled": True, "plans": ["monthly"]}, by="rep-1")
    assert r["error"] == "pricing_problems" and any("can never fire" in p for p in r["problems"])
    assert store.BRIEF["auto_close"]["enabled"] is False  # nothing saved
    assert onboarding.update_brief("icp", {"countries": ["SE", "DK"]}, by="rep-1")["value"]["titles"]  # merged, not replaced


def test_the_website_is_read_when_stored_and_otherwise_the_customer_is_asked():
    store.WEBSITES["lagerlys.example"] = {"title": "Lagerlys", "pages": [{"path": "/", "text": "One live stock count."}]}
    r = onboarding.import_website("https://www.lagerlys.example/pricing")
    assert r["ok"] and r["pages"][0]["text"] == "One live stock count."
    assert "paste" in onboarding.import_website("https://unknown.example")["next"]


def test_icp_evidence_comes_from_won_and_lost_leads():
    def lead(i, status, country, employees, industry, title, reason=None):
        store.LEADS[f"X-{i}"] = {"lead_id": f"X-{i}", "status": status, "loss_reason": reason,
                                 "company": {"country": country, "employees": employees, "industry": industry},
                                 "person": {"title": title}}
    assert onboarding.suggest_icp_from_deals()["enough_data"] is False
    for i in range(4):
        lead(i, "won", "SE", 20 + i, "B2B SaaS", "CTO")
    lead(5, "lost", "DE", 300, "Manufacturing", "CFO", "wrong_company_fit")
    lead(6, "lost", "SE", 15, "B2B SaaS", "CEO", "price")
    r = onboarding.suggest_icp_from_deals()
    assert r["enough_data"] and r["won"] == 4 and r["by_country"]["SE"] == {"won": 4, "lost": 1}
    assert r["by_size"]["10-49"]["won"] == 4 and r["by_title"]["CTO"]["won"] == 4 and r["loss_reasons"]["price"] == 1


def test_the_package_script_validates_and_builds_an_upload_ready_zip(tmp_path):
    out = tmp_path / "fleet.zip"
    r = subprocess.run([sys.executable, str(REPO / "scripts/package_fleet.py"), "--mcp-url", "https://tools.example.run.app/mcp",
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"AGENTS.md", "config.json", "tools.json", "subagents/onboarding/AGENTS.md", "skills/onboard-customer/SKILL.md"} <= names
        tools = json.loads(z.read("subagents/outreach/tools.json"))
    assert all(t["mcp_server_url"] == "https://tools.example.run.app/mcp" for t in tools["tools"])
    # only Fleet's export layout: what Fleet itself exports (see ai-sales-agent.zip, an export from Fleet)
    for n in names:
        parts = n.split("/")
        assert (n in ("AGENTS.md", "config.json", "tools.json") or (parts[0] == "subagents" and len(parts) == 3 and
                parts[2] in ("AGENTS.md", "tools.json")) or (parts[0] == "skills" and n.endswith(".md"))), n
    assert all("invalid" not in k for k in tools["interrupt_config"])
    assert subprocess.run([sys.executable, str(REPO / "scripts/package_fleet.py")], capture_output=True).returncode == 2  # no URL


def test_the_package_script_refuses_a_broken_fleet_folder(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "scripts"))
    import package_fleet
    import shutil
    copy = tmp_path / "fleet"
    shutil.copytree(REPO / "fleet", copy)
    (copy / "subagents/outreach/AGENTS.md").write_text("---\ndescription: Writes emails: and more\n---\n")
    t = json.loads((copy / "subagents/outreach/tools.json").read_text())
    t["interrupt_config"] = {k: False for k in t["interrupt_config"]}  # record_approval no longer asks
    t["tools"].append({"name": "delete_everything", "mcp_server_url": "https://sales-tools.invalid/mcp", "mcp_server_name": "sales_tools"})
    (copy / "subagents/outreach/tools.json").write_text(json.dumps(t))
    (copy / "skills/find-leads/SKILL.md").write_text("---\nname: find-leads\ndescription: x\n---\nsk-ant-api03-abcdefghijklmnopqrstuvwxyz\n@nobody")
    errors, _ = package_fleet.validate(copy)
    text = "\n".join(errors)
    for bit in ("contains a colon", "record_approval must ask a person", "delete_everything is not a tool", "secret", "@nobody"):
        assert bit in text, bit
