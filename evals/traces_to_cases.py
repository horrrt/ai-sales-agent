"""Turn LangSmith traces into hill-climbing cases: what the agent saw, what it did, and what happened next.
Owner: Ayaan (stream 4). Step 3 of the loop in docs/fleet-prompts-v2.md §3.3.

The tool server traces every call (mcp/sales_tools/tracing.py) and posts outcomes as feedback on a lead's
traces: email_approval, reply, meeting_booked, meeting_held, lost, deal_won. This script reads them back:

  1. The tool runs tagged "sales-tools" in LANGSMITH_PROJECT from the last --days, grouped by lead_id.
  2. Per lead: the lead as the positioner saw it (the last get_lead output before save_positioning, which has
     the research), the angle it saved, the email the rep approved or changed, and the prompt_version.
  3. The feedback on that lead's traces, latest value per key, and a verdict for the positioning stage:
     bad   lost to wrong_company_fit, negative, has_competitor, price or no_reply; a negative or not-now reply;
           or the rep rejected the email
     good  a positive reply, a meeting booked or held, or a deal won
     open  nothing decisive yet
  4. One JSON line per lead in evals/datasets/trace_cases.jsonl, which hill_climb.py --trace-cases reads.
     --to-langsmith also writes them to a LangSmith dataset, so the cases are visible next to the traces.

    cd mcp && uv run --env-file ../.env python ../evals/traces_to_cases.py --days 30
    cd mcp && uv run --env-file ../.env python ../evals/traces_to_cases.py --to-langsmith sales-agent-positioning-traces

Needs LANGSMITH_API_KEY. Makes no model calls.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "datasets" / "trace_cases.jsonl"

# Losses the positioning stage could have prevented. wrong_person, insufficient_data and bounced are not its fault.
POSITIONING_LOSSES = {"wrong_company_fit", "negative", "has_competitor", "price", "no_reply"}
LEAD_FIELDS = ("lead_id", "customer_id", "person", "company", "research")  # what the positioner is given


# ---------- pure: runs + feedback -> cases ----------

def _lead_of(run: dict) -> str | None:
    return (run.get("metadata") or {}).get("lead_id") or (run.get("inputs") or {}).get("lead_id")


def _output(run: dict):
    out = run.get("outputs") or {}
    return out.get("output", out)


def _public(lead: dict) -> dict:
    out = {k: lead[k] for k in LEAD_FIELDS if k in lead}
    if isinstance(out.get("person"), dict):  # the positioner never needs the address
        out["person"] = {k: v for k, v in out["person"].items() if k != "email"}
    if isinstance(out.get("research"), dict):
        out["research"] = {k: v for k, v in out["research"].items() if k != "attempts"}
    return out


def outcomes_of(feedback: list[dict]) -> dict:
    """Latest feedback per key: {key: {"score", "value"}}."""
    latest: dict[str, dict] = {}
    for f in sorted(feedback, key=lambda f: str(f.get("created_at") or "")):
        latest[f["key"]] = {"score": f.get("score"), "value": f.get("value")}
    return latest


def verdict(outcomes: dict) -> tuple[str, list[str]]:
    """good | bad | open for the positioning stage, with the reasons."""
    bad, good = [], []
    lost = (outcomes.get("lost") or {}).get("value")
    if lost in POSITIONING_LOSSES:
        bad.append(f"lost: {lost}")
    reply = outcomes.get("reply") or {}
    if reply.get("score") is not None and reply["score"] < 0.5:
        bad.append(f"reply: {reply.get('value')}")
    if (outcomes.get("email_approval") or {}).get("value") == "rejected":
        bad.append("rep rejected the email")
    if reply.get("score") is not None and reply["score"] >= 0.75:
        good.append(f"reply: {reply.get('value')}")
    for key in ("meeting_booked", "meeting_held", "deal_won"):
        if (outcomes.get(key) or {}).get("score") == 1.0:
            good.append(key)
    if bad:
        return "bad", bad
    return ("good", good) if good else ("open", [])


def build_cases(runs: list[dict], feedback: list[dict]) -> list[dict]:
    """One case per lead that reached positioning and whose research we can replay."""
    by_lead: dict[str, list[dict]] = {}
    for r in runs:
        lid = _lead_of(r)
        if lid:
            by_lead.setdefault(lid, []).append(r)
    fb_by_trace: dict[str, list[dict]] = {}
    for f in feedback:
        fb_by_trace.setdefault(str(f.get("trace_id") or f.get("run_id")), []).append(f)

    cases = []
    for lid, rs in sorted(by_lead.items()):
        rs.sort(key=lambda r: str(r.get("start_time") or ""))
        pos = [r for r in rs if r["name"] == "save_positioning" and (_output(r) or {}).get("ok")]
        if not pos:
            continue
        pos_run = pos[-1]
        seen = [r for r in rs if r["name"] == "get_lead" and str(r.get("start_time")) <= str(pos_run.get("start_time"))
                and isinstance(_output(r), dict) and _output(r).get("research")]
        if seen:
            lead = _output(seen[-1])
        else:  # no get_lead with research: rebuild it from research_target's output and the lead's other calls
            research = [r for r in rs if r["name"] == "research_target" and isinstance(_output(r), dict) and "facts" in _output(r)]
            if not research:
                continue
            any_lead = next((_output(r) for r in reversed(rs) if r["name"] == "get_lead" and isinstance(_output(r), dict)), {})
            lead = {**any_lead, "lead_id": lid, "research": _output(research[-1])}
        visual = [f for r in rs if r["name"] == "research_website_visual" and str(r.get("start_time")) <= str(pos_run.get("start_time"))
                  for f in ((_output(r) or {}).get("facts") or [])]
        if visual and isinstance(lead.get("research"), dict):
            known = {f.get("fact") for f in lead["research"].get("facts") or []}
            lead = {**lead, "research": {**lead["research"],
                                         "facts": list(lead["research"].get("facts") or []) + [f for f in visual if f.get("fact") not in known]}}
        approval = next((r for r in reversed(rs) if r["name"] == "record_approval"), None)
        versions = [(r.get("inputs") or {}).get("prompt_version") for r in rs
                    if r["name"] == "log_interaction" and (r.get("inputs") or {}).get("stage") == "positioning"]
        traces = sorted({str(r["trace_id"]) for r in rs if r.get("trace_id")})
        outcomes = outcomes_of([f for t in traces for f in fb_by_trace.get(t, [])])
        status, reasons = verdict(outcomes)
        cases.append({
            "lead_id": lid,
            "lead": _public(lead),
            "positioning_used": {k: (pos_run.get("inputs") or {}).get(k) for k in ("angle", "evidence", "confidence", "variant")},
            "email_used": approval and {k: (approval.get("inputs") or {}).get(k) for k in ("subject", "body", "decision", "note")},
            "prompt_version": next((v for v in reversed(versions) if v), None),
            "outcomes": outcomes,
            "status": status,
            "reasons": reasons,
            "trace_ids": traces,
            "positioned_at": str(pos_run.get("start_time")),
        })
    return cases


# ---------- LangSmith I/O ----------

def _run_dict(r) -> dict:
    return {"id": str(r.id), "trace_id": str(r.trace_id), "name": r.name, "start_time": r.start_time.isoformat() if r.start_time else "",
            "inputs": r.inputs or {}, "outputs": r.outputs or {}, "metadata": (r.extra or {}).get("metadata") or {}}


def fetch(project: str, days: int) -> tuple[list[dict], list[dict]]:
    import warnings

    from langsmith import Client

    warnings.simplefilter("ignore", DeprecationWarning)
    client = Client()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    runs = [_run_dict(r) for r in client.list_runs(project_name=project, run_type="tool", start_time=since)
            if "sales-tools" in (r.tags or [])]
    traces = sorted({r["trace_id"] for r in runs})
    feedback = []
    for i in range(0, len(traces), 50):
        for f in client.list_feedback(run_ids=traces[i:i + 50]):
            feedback.append({"trace_id": str(f.run_id), "key": f.key, "score": f.score, "value": f.value,
                             "created_at": f.created_at.isoformat() if f.created_at else ""})
    return runs, feedback


def to_langsmith(cases: list[dict], dataset: str) -> int:
    from langsmith import Client

    client = Client()
    ds = client.read_dataset(dataset_name=dataset) if client.has_dataset(dataset_name=dataset) else client.create_dataset(
        dataset, description="Positioning cases from production traces (evals/traces_to_cases.py)")
    existing = {(e.metadata or {}).get("case_key") for e in client.list_examples(dataset_id=ds.id)}
    new = [c for c in cases if f"{c['lead_id']}@{c['positioned_at']}" not in existing]
    if new:
        client.create_examples(dataset_id=ds.id,
                               inputs=[{"lead": c["lead"]} for c in new],
                               outputs=[{"positioning_used": c["positioning_used"], "status": c["status"], "reasons": c["reasons"],
                                         "outcomes": c["outcomes"]} for c in new],
                               metadata=[{"case_key": f"{c['lead_id']}@{c['positioned_at']}", "prompt_version": c["prompt_version"],
                                          "trace_ids": c["trace_ids"]} for c in new])
    return len(new)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", default=os.environ.get("LANGSMITH_PROJECT", "sales-agent"))
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--to-langsmith", metavar="DATASET", help="also add new cases to this LangSmith dataset")
    a = ap.parse_args()
    if not (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
        sys.exit("LANGSMITH_API_KEY is not set (run with --env-file ../.env)")
    runs, feedback = fetch(a.project, a.days)
    cases = build_cases(runs, feedback)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    print(f"{len(runs)} tool runs, {len(feedback)} feedback rows in {a.project} (last {a.days} days)")
    for c in cases:
        print(f"  {c['lead_id']}: {c['status']:<4} {', '.join(c['reasons']) or '-'}  (prompt {c['prompt_version'] or '?'})")
    print(f"wrote {len(cases)} cases to {a.out.relative_to(ROOT) if a.out.is_relative_to(ROOT) else a.out}")
    if a.to_langsmith:
        print(f"added {to_langsmith(cases, a.to_langsmith)} new examples to LangSmith dataset {a.to_langsmith}")


if __name__ == "__main__":
    main()
