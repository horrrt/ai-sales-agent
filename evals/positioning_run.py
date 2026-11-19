"""Run the positioner on every persona's lead, check each angle and grade it with the LLM judge. Owner: Gyula.

    uv run --project simulator python evals/positioning_run.py
    uv run --project simulator python evals/positioning_run.py --leads L-001 L-009

Writes evals/results/positioning-<timestamp>.jsonl, plus a -to-label.csv you fill in by hand (your label
for each actual angle) so the judge itself can be checked against a human.

expected_angle_quality in positioning.csv is the best outcome a careful salesperson could reach for that
lead (good, weak or correct_stop). Where it's filled in, the run reports how often the positioner reached it.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "evaluators"))
import common  # noqa: E402
from positioning_judge import judge  # noqa: E402

DATASET = common.ROOT / "evals/datasets/positioning.csv"


def human_labels() -> dict[str, str]:
    with DATASET.open() as f:
        return {r["lead_id"]: r["expected_angle_quality"].strip() for r in csv.DictReader(f) if r["expected_angle_quality"].strip()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leads", nargs="*", help="lead ids to run (default: all personas)")
    a = ap.parse_args()
    if not common.llm.has_key():
        sys.exit("ANTHROPIC_API_KEY is not set (env or .env). This eval needs real model calls.")

    brief = common.load_brief()
    personas = [p for p in common.simulate.all_personas() if not a.leads or p["lead_id"] in a.leads]
    labels = human_labels()
    out = common.RESULTS / f"positioning-{common.stamp()}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out.open("w") as jf:
        for p in personas:
            lead = p["lead"]
            row = {"lead_id": lead["lead_id"], "company": lead["company"]["name"], "positioning": None, "judge": None,
                   "error": None, "human_label": labels.get(lead["lead_id"]),
                   "models": {"positioner": common.llm.POSITIONER_MODEL, "judge": common.llm.JUDGE_MODEL}}
            try:
                row["positioning"] = common.run_positioner(lead, brief)
                row["judge"] = judge(lead, brief, row["positioning"])
            except Exception as e:  # noqa: BLE001  (recorded, run continues)
                row["error"] = f"{type(e).__name__}: {e}"
            rows.append(row)
            jf.write(json.dumps(row, ensure_ascii=False) + "\n")
            jf.flush()
            pos, verdict = row["positioning"], row["judge"]
            if row["error"]:
                print(f"{lead['lead_id']} {lead['company']['name'][:28]:28} ERROR {row['error'][:80]}")
                continue
            stop = f"STOP ({pos['loss_reason']})" if pos["no_fit"] else f"conf {pos['confidence']:.2f}"
            flag = " INVALID" if pos["validation_problems"] else ""
            print(f"{lead['lead_id']} {lead['company']['name'][:28]:28} {stop:28} judge={verdict['overall']}{flag}")

    errored = [r for r in rows if r["error"]]
    rows = [r for r in rows if not r["error"]]
    if errored:
        print(f"\n{len(errored)} leads errored and are left out below")
    print(f"\njudge overall: {dict(Counter(r['judge']['overall'] for r in rows))}  (n={len(rows)})")
    print(f"failed save_positioning checks: {sum(bool(r['positioning']['validation_problems']) for r in rows)} of {len(rows)}")
    labelled = [r for r in rows if r["human_label"]]
    if labelled:
        hit = sum(r["human_label"] == r["judge"]["overall"] for r in labelled)
        print(f"positioner reached the expected quality (as judged): {hit} of {len(labelled)}")
    else:
        print("expected_angle_quality is empty in positioning.csv, so there is nothing to compare against yet")
    to_label = out.with_name(out.stem + "-to-label.csv")
    with to_label.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lead_id", "angle", "evidence", "confidence", "judge_overall", "judge_critique", "your_label", "your_note"])
        for r in rows:
            pos = r["positioning"]
            w.writerow([r["lead_id"], pos["angle"], " | ".join(pos["evidence"]), pos["confidence"],
                        r["judge"]["overall"], r["judge"]["critique"], "", ""])
    print(f"wrote {out.relative_to(common.ROOT)} and {to_label.relative_to(common.ROOT)}")


if __name__ == "__main__":
    main()
