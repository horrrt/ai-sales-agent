"""The VLM research step: homepage screenshot -> vision model -> sourced facts. No model calls, no network.

    cd mcp && uv run --extra dev pytest -q tests/test_vision.py
"""
import pytest

from sales_tools import decide as decide_mod
from sales_tools import store, vision
from sales_tools.tools import research

NORDVIK = "nordvik-health.example"  # has a committed fixture in data/screenshots/


class Seen:
    """A scripted vision model: returns these facts and remembers what it was shown."""

    def __init__(self, *facts, note=""):
        self.facts, self.note, self.calls = list(facts), note, []

    def __call__(self, png, context):
        self.calls.append({"png": png, "context": context})
        return {"facts": [{"fact": f, "where": "hero"} for f in self.facts], "note": self.note, "backend": "stub"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    saved = dict(store.LEADS)
    store.LEADS.clear()
    store.INTERACTIONS.clear()
    store.CLOCK["now"] = "2026-11-24T10:00:00+01:00"
    monkeypatch.delenv("SALES_SCREENSHOTS", raising=False)
    store.LEADS["L-1"] = lead("L-1", NORDVIK)
    yield
    store.LEADS.clear()
    store.LEADS.update(saved)
    store.INTERACTIONS.clear()
    store.CLOCK.clear()
    vision.BACKEND = None
    decide_mod.BACKEND = None


def lead(lead_id, domain, research=None):
    return {"lead_id": lead_id, "customer_id": store.BRIEF.get("customer_id"), "stage": "researched", "status": "active",
            "person": {"name": "Ingrid Solberg", "title": "CEO", "email": "ingrid@nordvik.sandbox.test"},
            "company": {"name": "Nordvik Health Data AS", "domain": domain, "country": "NO", "employees": 22,
                        "industry": "B2B SaaS"},
            "research": research or {"summary": "Health data", "facts": [
                {"fact": "Announced a pilot with a regional hospital trust", "source_url": "https://nordvik-health.example/news"}],
                "attempts": 0}}


def test_a_fixture_screenshot_adds_sourced_facts_to_the_research():
    vision.BACKEND = Seen("Homepage badge says Hosted in Norway", "Banner: hiring a first Information Security Officer")
    out = research.research_website_visual("L-1")
    assert out["ok"] and out["added"] == 2 and out["screenshot"] == "fixture" and out["next"] == "check_ready_to_write"
    facts = store.LEADS["L-1"]["research"]["facts"]
    assert len(facts) == 3  # the research_target fact stays
    assert facts[-1] == {"fact": "Banner: hiring a first Information Security Officer", "source_url": "https://nordvik-health.example/",
                         "source": "homepage_screenshot", "where": "hero"}
    row = store.INTERACTIONS[-1]
    assert (row["stage"], row["outcome"], row["screenshot"]) == ("research", "success", "fixture")
    assert vision.BACKEND.calls[0]["png"][:8] == b"\x89PNG\r\n\x1a\n"


def test_the_model_is_told_the_offer_the_triggers_and_which_company_to_expect():
    vision.BACKEND = Seen()
    research.research_website_visual("L-1")
    ctx = vision.BACKEND.calls[0]["context"]
    assert ctx["expected_company"] == "Nordvik Health Data AS" and ctx["domain"] == NORDVIK
    assert ctx["offer"] == store.BRIEF.get("offer") and ctx["buying_triggers"] == (store.BRIEF.get("icp") or {}).get("triggers")
    assert set(ctx) == {"offer", "buying_triggers", "expected_company", "domain"}  # nothing about the person


def test_no_screenshot_is_not_a_failure_and_changes_nothing():
    store.LEADS["L-2"] = lead("L-2", "unknown-company.example")
    before = [dict(f) for f in store.LEADS["L-2"]["research"]["facts"]]
    vision.BACKEND = Seen("should not be called")
    out = research.research_website_visual("L-2")
    assert out["ok"] and out["added"] == 0 and out["screenshot"] is None and "live screenshots are off" in out["reason"]
    assert store.LEADS["L-2"]["research"]["facts"] == before
    assert vision.BACKEND.calls == []
    assert store.INTERACTIONS[-1]["outcome"] == "skipped"


def test_sandbox_domains_are_never_fetched_live(monkeypatch):
    monkeypatch.setenv("SALES_SCREENSHOTS", "live")
    monkeypatch.setattr(vision, "capture", lambda d: pytest.fail("must not open a browser for a sandbox domain"))
    store.LEADS["L-2"] = lead("L-2", "unknown-company.example")
    out = research.research_website_visual("L-2")
    assert out["added"] == 0 and "sandbox domain" in out["reason"]


def test_live_mode_takes_one_screenshot_when_robots_allows_it(monkeypatch, tmp_path):
    monkeypatch.setenv("SALES_SCREENSHOTS", "live")
    monkeypatch.setattr(vision, "SHOTS", tmp_path)
    monkeypatch.setattr(vision, "REAL", tmp_path / "real")
    monkeypatch.setattr(vision, "robots_block", lambda d: None)
    taken = []

    def fake_capture(domain):
        taken.append(domain)
        path = tmp_path / "real" / f"{domain}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return path

    monkeypatch.setattr(vision, "capture", fake_capture)
    store.LEADS["L-3"] = lead("L-3", "https://www.realco.se/about")
    vision.BACKEND = Seen("Customer logos include two Swedish banks")
    out = research.research_website_visual("L-3")
    assert taken == ["realco.se"]
    assert out["screenshot"] == "live" and out["facts"][0]["source_url"] == "https://realco.se/"


def test_robots_txt_that_disallows_the_homepage_stops_a_live_screenshot(monkeypatch, tmp_path):
    monkeypatch.setenv("SALES_SCREENSHOTS", "live")
    monkeypatch.setattr(vision, "SHOTS", tmp_path)
    monkeypatch.setattr(vision, "REAL", tmp_path / "real")
    monkeypatch.setattr(vision, "robots_block", lambda d: f"robots.txt on {d} disallows the homepage")
    monkeypatch.setattr(vision, "capture", lambda d: pytest.fail("robots.txt said no"))
    store.LEADS["L-3"] = lead("L-3", "realco.se")
    out = research.research_website_visual("L-3")
    assert out["added"] == 0 and "robots.txt" in out["reason"]


def test_a_saved_real_screenshot_is_used_before_going_live(monkeypatch, tmp_path):
    monkeypatch.setattr(vision, "SHOTS", tmp_path)
    monkeypatch.setattr(vision, "REAL", tmp_path / "real")
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "realco.se.png").write_bytes(b"\x89PNG\r\n\x1a\nreal")
    store.LEADS["L-3"] = lead("L-3", "realco.se")
    vision.BACKEND = Seen("Hero says the product is used by 2,000 Nordic companies")
    out = research.research_website_visual("L-3")
    assert out["screenshot"] == "real" and out["added"] == 1


def test_without_a_vision_key_it_adds_nothing_and_says_why(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    out = research.research_website_visual("L-1")
    assert out["ok"] and out["added"] == 0 and "no vision model configured" in out["note"]
    assert store.INTERACTIONS[-1]["vision_backend"] == "none"


def test_a_vision_model_error_never_breaks_research():
    def broken(png, ctx):
        raise TimeoutError("model timed out")

    vision.BACKEND = broken
    out = research.research_website_visual("L-1")
    assert out["ok"] and out["added"] == 0 and "TimeoutError" in out["note"]


def test_at_most_three_facts_and_no_repeats_across_rounds():
    vision.BACKEND = Seen("A", "B", "", "C", "D")
    assert research.research_website_visual("L-1")["added"] == 3
    assert research.research_website_visual("L-1")["added"] == 0  # same page, same facts: nothing new
    texts = [f["fact"] for f in store.LEADS["L-1"]["research"]["facts"]]
    assert texts.count("A") == 1 and "D" not in texts and "" not in texts


def test_visual_facts_reach_the_enough_to_write_check():
    vision.BACKEND = Seen("Customer logos include two Norwegian hospital trusts")
    research.research_website_visual("L-1")
    seen_evidence = []

    def gate(q, evidence, options):
        seen_evidence.append(evidence)
        return {"answer": True, "confidence": 1, "reason": "stub", "backend": "stub"}

    decide_mod.BACKEND = gate
    assert research.check_ready_to_write("L-1")["ready"] is True
    facts = seen_evidence[0]["research"]["facts"]
    assert any(f.get("source") == "homepage_screenshot" for f in facts)


def test_every_sandbox_lead_has_a_committed_screenshot_fixture():
    from sales_tools import sim

    domains = {p["lead"]["company"]["domain"] for p in sim.PERSONAS.values()} or {
        "nordvik-health.example", "ledgerfox.example", "polarstack.example", "brightwork.example", "sisulearning.example",
        "kollektiv-it.example", "granitsoftware.example", "mokkibooking.example", "tidewater.example", "kustannus.example",
        "stigen.example", "arcticsignal.example"}
    missing = [d for d in domains if vision.find(d)[1] != "fixture"]
    assert not missing, f"no fixture for {missing}: run scripts/make_screenshot_fixtures.py"
