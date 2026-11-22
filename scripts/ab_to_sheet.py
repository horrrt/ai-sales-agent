#!/usr/bin/env python3
"""Turn an A/B run (evals/ab_test.py) into rows for the funnel simulator sheet's Personas tab.

    python3 scripts/ab_to_sheet.py                       # latest evals/results/ab-*.jsonl, TSV to stdout
    python3 scripts/ab_to_sheet.py evals/results/ab-20261121-222921.jsonl
    python3 scripts/ab_to_sheet.py --counts-only | pbcopy  # just the count columns (F:N)

Paste the output at Personas!A5 (or F5 with --counts-only). The tab's totals, the Beta-based Persona Sim
and the summary all recompute. One row per persona and arm, in the sheet's order (P-001 research,
P-001 template, P-002 research, ...), with these count columns:

    Runs, not_sent, positive, not_now, negative, wrong_person, out_of_office, unsubscribe, no_reply

Runs that errored are left out of every column and reported on stderr, so a flaky model call doesn't
count as a lost lead. A label the sheet has no column for stops the script rather than vanish silently.

Sheet: "AI Sales Agent - Funnel Simulator (Kvitto demo)" in Gyula's Drive.
"""
import argparse
import collections
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "evals" / "results"
PERSONAS = REPO / "simulator" / "personas"
ARMS = ("research", "template")
LABELS = ("positive", "not_now", "negative", "wrong_person", "out_of_office", "unsubscribe")


def load_personas():
    """lead_id -> (persona id, company, design note), sorted by persona id."""
    out = {}
    for f in sorted(PERSONAS.glob("P-*.json")):
        d = json.loads(f.read_text())
        out[d["lead"]["lead_id"]] = (f.stem, d["lead"]["company"]["name"], d.get("design_note", ""))
    return out


def outcome(run):
    if not run.get("sent"):
        return "not_sent"
    reply = run.get("reply")
    if not reply:
        return "no_reply"
    label = reply.get("intended_label")
    if label not in LABELS:
        sys.exit(f"{run['lead_id']} ({run['arm']}): label {label!r} has no column in the sheet")
    return label


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", nargs="?", type=Path, help="ab-*.jsonl (default: the latest in evals/results/)")
    ap.add_argument("--counts-only", action="store_true", help="only the count columns, for pasting at F5")
    args = ap.parse_args()

    real_runs = [p for p in RESULTS.glob("ab-*.jsonl") if "OFFLINE" not in p.name]
    path = args.path or max(real_runs, default=None)
    if not path or not path.exists():
        sys.exit("no A/B results found; run evals/ab_test.py first (with ANTHROPIC_API_KEY set)")
    if "OFFLINE" in path.name:
        sys.exit(f"{path.name} is an offline run: its replies are stubs and its numbers mean nothing")

    personas = load_personas()
    counts = collections.defaultdict(collections.Counter)
    errors = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        run = json.loads(line)
        if run.get("error"):
            errors += 1
            continue
        if (run.get("reply") or {}).get("source") == "stub":
            sys.exit(f"{path.name} has stub replies (no model calls); its numbers mean nothing")
        if run["lead_id"] not in personas:
            sys.exit(f"{run['lead_id']} is not one of the personas in simulator/personas/")
        counts[(run["lead_id"], run["arm"])][outcome(run)] += 1

    columns = ("not_sent",) + LABELS + ("no_reply",)
    for lead_id, (pid, company, note) in personas.items():
        for arm in ARMS:
            c = counts[(lead_id, arm)]
            row = [sum(c.values())] + [c[k] for k in columns]
            if not args.counts_only:
                row = [pid, lead_id, company, note, arm] + row
            print("\t".join(str(v).replace("\t", " ") for v in row))

    total = {arm: sum(sum(counts[(l, arm)].values()) for l in personas) for arm in ARMS}
    print(f"{path.name}: {total['research']} research runs, {total['template']} template runs"
          + (f", {errors} errored runs left out" if errors else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
