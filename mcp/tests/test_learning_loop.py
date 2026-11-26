"""The learning loop from traces: traces -> cases (evals/traces_to_cases.py) -> hill-climbing (evals/hill_climb.py).
No network and no model calls: runs and feedback are written by hand in the shape LangSmith returns.

    cd mcp && uv run --extra dev pytest -q tests/test_learning_loop.py
"""
import json
import random
import sys
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(EVALS))
import hill_climb  # noqa: E402  (common.py puts simulator/ on the path)
import traces_to_cases as ttc  # noqa: E402


def run(name, trace, at, lead_id="L-1", inputs=None, output=None):
    return {"id": f"{name}-{at}", "trace_id": trace, "name": name, "start_time": f"2026-11-26T10:{at:02d}:00+00:00",
            "inputs": {"lead_id": lead_id, **(inputs or {})}, "outputs": {"output": output}, "metadata": {"lead_id": lead_id}}


LEAD = {"lead_id": "L-1", "customer_id": "C-demo", "stage": "researched", "send_salt": "x", "owner_name": "Mette",
        "person": {"name": "Ingrid Solberg", "title": "CEO", "email": "ingrid@nordvik.sandbox.test"},
        "company": {"name": "Nordvik Health Data AS", "domain": "nordvik-health.example"},
        "research": {"summary": "s", "attempts": 1, "facts": [{"fact": "Hospital pilot", "source_url": "https://n.example/a"}]}}


def runs_for_one_lead():
    return [
        run("get_lead", "t1", 1, output=LEAD),
        run("research_website_visual", "t2", 2, output={"ok": True, "facts": [
            {"fact": "Badge: Hosted in Norway", "source_url": "https://nordvik-health.example/", "source": "homepage_screenshot"}]}),
        run("log_interaction", "t3", 3, inputs={"stage": "positioning", "prompt_version": "v2", "outcome": "success"}, output={"ok": True}),
        run("save_positioning", "t3", 4, inputs={"angle": "Pilot pressure means audit-ready now", "evidence": ["Hospital pilot"],
                                                 "confidence": 0.8, "variant": "research"}, output={"ok": True}),
        run("record_approval", "t4", 5, inputs={"subject": "Audit-ready", "body": "Hi Ingrid", "decision": "approve"}, output={"ok": True}),
        run("get_lead", "t5", 6, lead_id="L-2", output={**LEAD, "lead_id": "L-2"}),  # never positioned: no case
    ]


def fb(trace, key, score=None, value=None, at="2026-11-26T11:00:00"):
    return {"trace_id": trace, "key": key, "score": score, "value": value, "created_at": at}


def test_a_lost_lead_becomes_a_bad_case_with_what_the_positioner_saw_and_did():
    cases = ttc.build_cases(runs_for_one_lead(), [fb("t1", "email_approval", 1.0, "approved"),
                                                  fb("t4", "lost", 0.0, "has_competitor"), fb("t4", "reply", 0.0, "negative")])
    assert [c["lead_id"] for c in cases] == ["L-1"]
    c = cases[0]
    assert c["status"] == "bad" and c["reasons"] == ["lost: has_competitor", "reply: negative"]
    assert c["positioning_used"]["angle"] == "Pilot pressure means audit-ready now"
    assert c["email_used"]["subject"] == "Audit-ready" and c["prompt_version"] == "v2"
    assert c["trace_ids"] == ["t1", "t2", "t3", "t4"]
    assert set(c["lead"]) == {"lead_id", "customer_id", "person", "company", "research"}  # no send_salt, owner, stage
    assert "email" not in c["lead"]["person"] and "attempts" not in c["lead"]["research"]
    assert [f["fact"] for f in c["lead"]["research"]["facts"]] == ["Hospital pilot", "Badge: Hosted in Norway"]


@pytest.mark.parametrize("outcomes, status", [
    ({"meeting_booked": {"score": 1.0}}, "good"),
    ({"reply": {"score": 1.0, "value": "positive"}}, "good"),
    ({"email_approval": {"score": 1.0, "value": "approved"}}, "open"),
    ({"email_approval": {"score": 0.5, "value": "edited"}}, "open"),
    ({"email_approval": {"score": 0.0, "value": "rejected"}}, "bad"),
    ({"reply": {"score": 0.25, "value": "not_now"}}, "bad"),
    ({"lost": {"score": 0.0, "value": "wrong_person"}}, "open"),  # not the positioner's fault
    ({"lost": {"score": 0.0, "value": "price"}, "meeting_booked": {"score": 1.0}}, "bad"),
])
def test_verdict_for_the_positioning_stage(outcomes, status):
    assert ttc.verdict(outcomes)[0] == status


def test_the_latest_feedback_per_key_wins():
    out = ttc.outcomes_of([fb("t", "reply", 0.25, "not_now", "2026-11-20"), fb("t", "reply", 1.0, "positive", "2026-11-25")])
    assert out == {"reply": {"score": 1.0, "value": "positive"}}


def test_without_get_lead_the_research_comes_from_research_target():
    rs = [run("research_target", "t1", 1, output={"summary": "s", "facts": [{"fact": "F", "source_url": "u"}]}),
          run("save_positioning", "t2", 2, inputs={"angle": "A"}, output={"ok": True})]
    assert ttc.build_cases(rs, [])[0]["lead"]["research"]["facts"][0]["fact"] == "F"


def test_a_lead_with_no_replayable_research_or_no_saved_angle_is_skipped():
    assert ttc.build_cases([run("save_positioning", "t1", 1, inputs={"angle": "A"}, output={"ok": True})], []) == []
    assert ttc.build_cases([run("get_lead", "t1", 1, output=LEAD),
                            run("save_positioning", "t1", 2, inputs={"angle": "A"}, output={"ok": False})], []) == []


# ---------- hill_climb ----------

def personas(n=12):
    return [{"lead_id": f"L-1{i:02d}", "research": {"facts": [{"fact": "f"}]}} for i in range(1, n + 1)]


def test_the_holdout_stays_personas_only_and_matches_the_old_split():
    leads = personas()
    ids = sorted(l["lead_id"] for l in leads)
    old = list(ids)
    random.Random(7).shuffle(old)
    old_holdout = set(old[:int(len(old) * 0.33)])
    merged, observed, trace_only = hill_climb.merge_trace_cases(leads, [
        {"lead_id": "L-900", "lead": {"research": {"facts": [{"fact": "x"}]}}, "status": "bad"},
        {"lead_id": "L-101", "lead": {"research": {"facts": [{"fact": "y"}]}}, "status": "good"}])
    assert trace_only == {"L-900"} and set(observed) == {"L-900", "L-101"}
    assert len(merged) == 13 and next(l for l in merged if l["lead_id"] == "L-101")["research"]["facts"][0]["fact"] == "f"
    train, holdout = hill_climb.split(sorted(l["lead_id"] for l in merged), trace_only, 0.33, 7)
    assert holdout == old_holdout and "L-900" in train and not train & holdout


def test_the_proposer_sees_production_failures_even_when_the_reviewer_liked_the_angle():
    leads = {l["lead_id"]: l for l in personas(4)}
    rows = [{"lead_id": lid, "positioning": {"no_fit": False, "loss_reason": None, "angle": "a", "evidence": [], "confidence": 0.7,
                                             "validation_problems": []}, "judge": {"overall": "weak" if lid == "L-101" else "good",
                                                                                   "critique": "c"}, "error": None,
             "score": 0.5 if lid == "L-101" else 1.0} for lid in leads]
    case = {"lead_id": "L-103", "status": "bad", "reasons": ["lost: has_competitor"], "outcomes": {"lost": {"value": "has_competitor"}},
            "positioning_used": {"angle": "the angle we sent"}, "prompt_version": "v2", "trace_ids": ["t-secret"]}
    holdout_case = {**case, "lead_id": "L-104"}
    text = hill_climb.proposer_input("PROMPT", {"rows": rows}, {"L-101", "L-102", "L-103"}, leads, [], k=1,
                                     observed={"L-103": case, "L-104": holdout_case})
    shown = json.loads(text.split("<lowest_scoring_outputs>\n")[1].split("\n</lowest_scoring_outputs>")[0])
    assert [s["lead"]["lead_id"] for s in shown] == ["L-101", "L-103"]  # the worst, then the production failure
    assert shown[1]["in_production"] == {"angle_used": "the angle we sent", "status": "bad", "what_happened": ["lost: has_competitor"],
                                         "outcomes": {"lost": {"value": "has_competitor"}}, "prompt_version": "v2"}
    assert "L-104" not in text and "t-secret" not in text  # holdout outcomes and trace IDs never reach the proposer


def test_trace_case_files_keep_the_latest_case_per_lead(tmp_path):
    p = tmp_path / "cases.jsonl"
    rows = [{"lead_id": "L-1", "positioned_at": "2026-11-20", "lead": {"research": {"facts": []}}, "status": "open"},
            {"lead_id": "L-1", "positioned_at": "2026-11-25", "lead": {"research": {"facts": []}}, "status": "bad"},
            {"lead_id": "L-2", "positioned_at": "2026-11-25", "lead": {}, "status": "bad"}]  # no research: unusable
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert [(c["lead_id"], c["status"]) for c in hill_climb.load_trace_cases(p)] == [("L-1", "bad")]


def test_a_coach_proposal_is_given_as_text_or_a_file_and_must_match_the_prompt_once(tmp_path):
    (tmp_path / "old.txt").write_text("## Return", encoding="utf-8")
    assert hill_climb.read_arg_text(f"@{tmp_path / 'old.txt'}") == "## Return"
    assert hill_climb.read_arg_text("plain text") == "plain text"
    edit = hill_climb.Edit(diagnosis="d", old_text="## Return", new_text="One rule.\n\n## Return", rationale="r")
    assert hill_climb.apply_edit("intro\n## Return\n", edit) == "intro\nOne rule.\n\n## Return\n"
    with pytest.raises(ValueError):
        hill_climb.apply_edit("no such passage", edit)
