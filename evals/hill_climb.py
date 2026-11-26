"""Hill-climb the positioner prompt: change one thing, measure it on the whole set, keep it only if it helps. Owner: Gyula.

This is steps 4 to 6 of the loop in docs/fleet-prompts-v2.md §3.3, run offline on the simulated personas:

  1. Score the current prompt (fleet/subagents/positioner/AGENTS.md) on every persona lead, --samples times each.
  2. A proposer model reads the prompt and the worst-scored angles from the train half of the leads, and proposes
     ONE edit: an exact passage of the prompt and its replacement.
  3. Score the edited prompt on every lead (train and holdout).
  4. Accept rule: keep it only if the primary score goes up by more than --min-delta and the guardrail
     (share of angles that pass save_positioning's checks) drops by no more than --max-guardrail-drop.
     Otherwise revert and record why.
  5. Repeat --steps times from whichever prompt is current.

    uv run --project simulator python evals/hill_climb.py --steps 3
    uv run --project simulator python evals/hill_climb.py --offline          # plumbing check, no model calls
    uv run --project simulator python evals/hill_climb.py --steps 3 --apply  # write the winner into fleet/

Score per sample (from the LLM judge in evaluators/positioning_judge.py):
  good, correct_stop = 1   weak = 0.5   bad, wrong_stop = 0
  An angle that fails save_positioning's checks scores 0 whatever the judge says, because the tool would refuse it.
  An errored call scores 0 and is counted.

The proposer only sees failures from the train half. The holdout score is logged with every step, so if the
train score climbs and the holdout doesn't, the edit is fitting these personas (or the judge), not the task.
With 12 personas the numbers are noisy: treat an accepted step as a candidate for the A/B test
(evals/ab_test.py), not as proof.

Learning from traces (--trace-cases evals/datasets/trace_cases.jsonl, written by evals/traces_to_cases.py):
  Each case is a lead from a real run: the lead as the positioner saw it, the angle it saved, and what happened
  next (reply, loss reason, meeting, the rep's approval). Leads that aren't personas join the train set; the
  holdout stays personas only, so production cases never leak into it. The proposer sees the production
  outcomes of train leads next to the reviewer's grade, which is where the reviewer and real prospects disagree.

Checking a proposal before an admin approves it (--edit-old/--edit-new, text or @file):
  Skips the proposer and measures that one edit with the same accept rule, e.g. the exact old -> new prompt
  lines the weekly review (@coach) saved with save_proposal:
    uv run --project simulator python evals/hill_climb.py --edit-old @old.txt --edit-new @new.txt --trace-cases evals/datasets/trace_cases.jsonl

Outputs in evals/results/:
  hill-climb-<stamp>.jsonl     one row per step: the edit, scores, per-lead results, accepted or why not
  hill-climb-<stamp>-best.md   the best AGENTS.md (prompt_version bumped per accepted step)
  hill-climb-<stamp>.md        the changes table: old -> new, before/after, decision. This is the before/after.
--apply copies the best AGENTS.md into fleet/subagents/positioner/ when at least one step was accepted.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "evaluators"))
import common  # noqa: E402
from common import llm  # noqa: E402

SCORE = {"good": 1.0, "correct_stop": 1.0, "weak": 0.5, "bad": 0.0, "wrong_stop": 0.0}
PROPOSER_MODEL = os.environ.get("PROPOSER_MODEL", llm.POSITIONER_MODEL)


# ---------- scoring ----------

def score_sample(pos: dict | None, verdict: dict | None, error: str | None) -> float:
    if error or pos is None or verdict is None:
        return 0.0
    if pos.get("validation_problems"):
        return 0.0
    return SCORE[verdict["overall"]]


def evaluate(raw_prompt: str, leads: list[dict], brief: dict, samples: int, workers: int, runner) -> dict:
    """Run the prompt on every lead `samples` times. Returns per-lead results and aggregate scores."""
    system = common.positioner_prompt(raw_prompt)
    jobs = [(lead, i) for lead in leads for i in range(samples)]

    def one(job):
        lead, _ = job
        pos = verdict = err = None
        try:
            pos, verdict = runner(lead, brief, system)
        except Exception as e:  # noqa: BLE001  (recorded, run continues)
            err = f"{type(e).__name__}: {e}"
        return {"lead_id": lead["lead_id"], "positioning": pos, "judge": verdict, "error": err,
                "score": score_sample(pos, verdict, err)}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(one, jobs))
    return {"rows": rows, **aggregate(rows)}


def aggregate(rows: list[dict], ids: set[str] | None = None) -> dict:
    rs = [r for r in rows if ids is None or r["lead_id"] in ids]
    n = len(rs) or 1
    ok = [r for r in rs if not r["error"] and r["positioning"]]
    return {
        "score": sum(r["score"] for r in rs) / n,
        "valid_rate": sum(not r["positioning"]["validation_problems"] for r in ok) / n,
        "errors": sum(bool(r["error"]) for r in rs),
        "n": len(rs),
    }


def split_scores(result: dict, train: set[str], holdout: set[str]) -> dict:
    return {"all": aggregate(result["rows"]), "train": aggregate(result["rows"], train),
            "holdout": aggregate(result["rows"], holdout) if holdout else None}


def real_runner(lead: dict, brief: dict, system: str):
    from positioning_judge import judge
    pos = common.run_positioner(lead, brief, system)
    return pos, judge(lead, brief, pos)


# ---------- proposing one change ----------

class Edit(BaseModel):
    diagnosis: str = Field(description="What the failing angles have in common, in one or two sentences")
    old_text: str = Field(description="An exact passage copied from the current prompt, long enough to occur only once")
    new_text: str = Field(description="What replaces it. Change one thing: one rule, one step, one sentence")
    rationale: str = Field(description="Why this should raise the score without making other angles worse")


PROPOSER_SYSTEM = """You improve the system prompt of a sales agent's Positioner by hill-climbing: you propose exactly ONE small edit, it gets measured on a fixed set of leads, and it is kept only if the score goes up and nothing else gets worse.

The Positioner reads a lead's research and the customer's brief and either saves one evidence-backed positioning angle or stops the lead. A strict reviewer grades each output: good (specific, every claim backed by a research fact or the brief, a real trigger), weak (true but generic), bad (an unsupported claim, or an angle forced onto a lead that should have been stopped), correct_stop, wrong_stop. An angle also fails if its evidence strings aren't copied word for word from research.facts.

Rules for your edit:
- One change only: add, reword or remove a single rule, step or sentence. No rewrites of whole sections.
- old_text must be copied exactly from the prompt (same whitespace and punctuation) and occur exactly once. To add a rule, quote the line it goes after as old_text and repeat it at the start of new_text.
- Fix the cause shared by several failures, not one lead. Never name a specific lead, company or person from the examples: the prompt has to work for leads it has never seen.
- Keep every safety rule (sandbox only, no invented facts, no claims beyond the brief). Never weaken them to please the reviewer.
- Don't repeat an edit that was already tried and rejected.

Some leads come from real runs and show what happened in production: the angle that was used, the prospect's reply, the loss reason, whether the rep approved the email. Weigh those: an angle the reviewer liked but the prospect rejected (or lost on has_competitor, price, wrong_company_fit) points at something the reviewer misses. Still fix the shared cause, never the single lead."""


def production_view(case: dict) -> dict:
    """What the proposer may see of a production case: the angle used and what happened, no trace IDs."""
    return {"angle_used": (case.get("positioning_used") or {}).get("angle"), "status": case.get("status"),
            "what_happened": case.get("reasons") or [], "outcomes": case.get("outcomes") or {},
            "prompt_version": case.get("prompt_version")}


def proposer_input(raw_prompt: str, result: dict, train: set[str], leads_by_id: dict, history: list[dict], k: int,
                   observed: dict | None = None) -> str:
    observed = {lid: c for lid, c in (observed or {}).items() if lid in train}  # never show holdout outcomes
    worst = sorted((r for r in result["rows"] if r["lead_id"] in train), key=lambda r: r["score"])[:k]
    shown = {r["lead_id"] for r in worst}
    # production failures go in even when the reviewer scores the current angle well: that disagreement is the signal
    worst += [next(r for r in result["rows"] if r["lead_id"] == lid) for lid, c in sorted(observed.items())
              if c.get("status") == "bad" and lid not in shown and any(r["lead_id"] == lid for r in result["rows"])]
    cases = []
    for r in worst:
        lead = leads_by_id[r["lead_id"]]
        case = {
            "lead": common.public_lead(lead),
            "positioner_output": r["positioning"] and {k2: r["positioning"][k2] for k2 in ("no_fit", "loss_reason", "angle", "evidence", "confidence")},
            "save_positioning_problems": r["positioning"] and r["positioning"]["validation_problems"],
            "reviewer": r["judge"] and {"overall": r["judge"]["overall"], "critique": r["judge"]["critique"]},
            "error": r["error"],
            "score": r["score"],
        }
        if r["lead_id"] in observed:
            case["in_production"] = production_view(observed[r["lead_id"]])
        cases.append(case)
    tried = [{"old_text": h["edit"]["old_text"], "new_text": h["edit"]["new_text"], "accepted": h["accepted"], "why": h["decision"]}
             for h in history if h.get("edit")]
    return (f"<current_prompt>\n{raw_prompt}\n</current_prompt>\n\n"
            f"<lowest_scoring_outputs>\n{json.dumps(cases, indent=2, ensure_ascii=False)}\n</lowest_scoring_outputs>\n\n"
            f"<edits_already_tried>\n{json.dumps(tried, indent=2, ensure_ascii=False)}\n</edits_already_tried>\n\n"
            "Propose one edit.")


# ---------- production cases from traces ----------

def load_trace_cases(path: Path) -> list[dict]:
    """Cases written by evals/traces_to_cases.py, latest per lead."""
    latest: dict[str, dict] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            if c.get("lead_id") and (c.get("lead") or {}).get("research"):
                prev = latest.get(c["lead_id"])
                if prev is None or str(c.get("positioned_at")) >= str(prev.get("positioned_at")):
                    latest[c["lead_id"]] = c
    return list(latest.values())


def merge_trace_cases(leads: list[dict], cases: list[dict]) -> tuple[list[dict], dict, set[str]]:
    """Add production cases to the lead set. Returns (leads, observed outcomes by lead_id, lead_ids only seen in traces).

    A persona that also ran in production keeps its persona lead (same research) and gains the observed outcome.
    A lead that is only in the traces is added as it was seen, and always goes to the train set.
    """
    known = {l["lead_id"] for l in leads}
    merged, observed, trace_only = list(leads), {}, set()
    for c in cases:
        lid = c["lead_id"]
        observed[lid] = c
        if lid not in known:
            merged.append({**c["lead"], "lead_id": lid})
            trace_only.add(lid)
            known.add(lid)
    return merged, observed, trace_only


def split(ids: list[str], trace_only: set[str], holdout_share: float, seed: int) -> tuple[set[str], set[str]]:
    """(train, holdout). The holdout is drawn from the personas only, with the same seed as before."""
    base = sorted(i for i in ids if i not in trace_only)
    random.Random(seed).shuffle(base)
    n_hold = int(len(base) * holdout_share) if len(base) > 2 else 0
    holdout = set(base[:n_hold])
    return set(ids) - holdout, holdout


def read_arg_text(value: str) -> str:
    """--edit-old/--edit-new: the text itself, or @path to read it from a file."""
    return Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value


def apply_edit(raw: str, edit: Edit) -> str:
    count = raw.count(edit.old_text)
    if count != 1:
        raise ValueError(f"old_text occurs {count} times in the prompt (needs exactly 1)")
    if edit.old_text == edit.new_text:
        raise ValueError("new_text is the same as old_text")
    if re.search(r"\A---.*?---", edit.old_text, flags=re.S) or "<!--" in edit.old_text:
        raise ValueError("old_text touches the frontmatter or a maintainer comment")
    return raw.replace(edit.old_text, edit.new_text, 1)


def propose(raw_prompt: str, user: str, proposer, tries: int = 3) -> tuple[Edit, str]:
    problems = []
    for _ in range(tries):
        note = f"\n\nYour previous proposal was unusable: {problems[-1]}. Try again." if problems else ""
        edit = proposer(user + note)
        try:
            return edit, apply_edit(raw_prompt, edit)
        except ValueError as e:
            problems.append(str(e))
    raise RuntimeError(f"no usable edit after {tries} tries: {problems}")


def real_proposer(user: str) -> Edit:
    return llm.structured(PROPOSER_MODEL, PROPOSER_SYSTEM, user, Edit, effort="high")


def bump_version(raw: str) -> str:
    return re.sub(r"prompt_version: v(\d+)", lambda m: f"prompt_version: v{int(m.group(1)) + 1}", raw, count=1)


# ---------- offline stand-ins (plumbing check) ----------

def offline_runner(lead: dict, brief: dict, system: str):
    rng = random.Random(hash((lead["lead_id"], len(system))))
    facts = [f["fact"] for f in lead["research"]["facts"]] or ["none"]
    pos = {"no_fit": False, "loss_reason": None, "angle": "offline", "evidence": facts[:1], "confidence": 0.6,
           "validation_problems": []}
    return pos, {"overall": rng.choice(["good", "weak", "bad"]), "critique": "offline"}


def offline_proposer(user: str) -> Edit:
    return Edit(diagnosis="offline", old_text="## Return to the manager",
                new_text="Never pad the angle with claims you can't trace to a fact.\n\n## Return to the manager",
                rationale="offline plumbing check")


# ---------- the loop ----------

def fmt(s: dict | None) -> str:
    return "-" if s is None else f"{s['score']:.3f} (valid {s['valid_rate']:.0%}, n={s['n']}, errors {s['errors']})"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--steps", type=int, default=3, help="edits to try")
    ap.add_argument("--samples", type=int, default=2, help="runs per lead per evaluation (noise control)")
    ap.add_argument("--holdout", type=float, default=0.33, help="share of leads the proposer never sees")
    ap.add_argument("--seed", type=int, default=7, help="seed for the train/holdout split")
    ap.add_argument("--min-delta", type=float, default=0.02, help="primary score must rise by more than this")
    ap.add_argument("--max-guardrail-drop", type=float, default=0.02, help="valid-angle rate may drop at most this (2 points)")
    ap.add_argument("--worst", type=int, default=6, help="failing outputs shown to the proposer")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--leads", nargs="*", help="lead ids to use (default: all personas)")
    ap.add_argument("--offline", action="store_true", help="fake model calls, to check the plumbing")
    ap.add_argument("--apply", action="store_true", help="write the best prompt into fleet/subagents/positioner/AGENTS.md")
    ap.add_argument("--trace-cases", type=Path, help="production cases from evals/traces_to_cases.py (JSONL)")
    ap.add_argument("--edit-old", help="check this one edit instead of proposing: the exact passage to replace (text or @file)")
    ap.add_argument("--edit-new", help="what replaces --edit-old (text or @file)")
    a = ap.parse_args()
    if not a.offline and not llm.has_key():
        sys.exit("ANTHROPIC_API_KEY is not set (env or .env). Use --offline for a plumbing check.")
    if bool(a.edit_old) != bool(a.edit_new):
        sys.exit("--edit-old and --edit-new go together")

    runner, proposer = (offline_runner, offline_proposer) if a.offline else (real_runner, real_proposer)
    if a.edit_old:  # check a given proposal (e.g. the weekly review's) with the same accept rule, once
        fixed = Edit(diagnosis="Proposed by the weekly review", old_text=read_arg_text(a.edit_old),
                     new_text=read_arg_text(a.edit_new), rationale="checked offline before an admin decides")
        proposer, a.steps = (lambda user: fixed), 1
    if not a.offline:
        llm.client()  # build the shared client before the worker threads do

    brief = common.load_brief()
    leads = [p["lead"] for p in common.simulate.all_personas() if not a.leads or p["lead"]["lead_id"] in a.leads]
    cases = load_trace_cases(a.trace_cases) if a.trace_cases else []
    leads, observed, trace_only = merge_trace_cases(leads, cases)
    if not leads:
        sys.exit("no leads matched")
    leads_by_id = {l["lead_id"]: l for l in leads}
    train, holdout = split(sorted(leads_by_id), trace_only, a.holdout, a.seed)

    stamp = common.stamp()
    log_path = common.RESULTS / f"hill-climb-{stamp}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"models": {"positioner": llm.POSITIONER_MODEL, "judge": llm.JUDGE_MODEL, "proposer": PROPOSER_MODEL},
            "offline": a.offline, "samples": a.samples, "train": sorted(train), "holdout": sorted(holdout),
            "min_delta": a.min_delta, "max_guardrail_drop": a.max_guardrail_drop,
            "mode": "check one proposal" if a.edit_old else "propose",
            "trace_cases": {"file": str(a.trace_cases) if a.trace_cases else None, "n": len(cases),
                            "bad": sorted(l for l, c in observed.items() if c.get("status") == "bad"),
                            "good": sorted(l for l, c in observed.items() if c.get("status") == "good"),
                            "only_in_traces": sorted(trace_only)}}
    print(f"{len(leads)} leads ({len(train)} train, {len(holdout)} holdout), {a.samples} samples each"
          + (f"; {len(cases)} production cases ({len(meta['trace_cases']['bad'])} bad)" if cases else ""))

    current = common.POSITIONER_FILE.read_text(encoding="utf-8")
    base = evaluate(current, leads, brief, a.samples, a.workers, runner)
    cur_scores = split_scores(base, train, holdout)
    start_scores = cur_scores
    print(f"baseline  all {fmt(cur_scores['all'])}  holdout {fmt(cur_scores['holdout'])}")

    history: list[dict] = []
    with log_path.open("w", encoding="utf-8") as jf:
        jf.write(json.dumps({"step": 0, "kind": "baseline", "meta": meta, "scores": cur_scores, "rows": base["rows"]},
                            ensure_ascii=False) + "\n")
        cur_result = base
        for step in range(1, a.steps + 1):
            entry: dict = {"step": step, "kind": "edit", "edit": None, "accepted": False}
            try:
                user = proposer_input(current, cur_result, train, leads_by_id, history, a.worst, observed)
                edit, candidate = propose(current, user, proposer)
            except Exception as e:  # noqa: BLE001
                entry["decision"] = f"no edit: {type(e).__name__}: {e}"
                print(f"step {step}: {entry['decision']}")
                history.append(entry)
                jf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                jf.flush()
                continue
            entry["edit"] = edit.model_dump()
            result = evaluate(candidate, leads, brief, a.samples, a.workers, runner)
            new_scores = split_scores(result, train, holdout)
            before, after = cur_scores["all"], new_scores["all"]
            delta = after["score"] - before["score"]
            guard = before["valid_rate"] - after["valid_rate"]
            if delta <= a.min_delta:
                decision = f"rejected: score {before['score']:.3f} -> {after['score']:.3f} (needs > +{a.min_delta})"
            elif guard > a.max_guardrail_drop:
                decision = f"rejected: valid-angle rate dropped {guard:.0%} (max {a.max_guardrail_drop:.0%})"
            else:
                entry["accepted"] = True
                decision = f"accepted: score {before['score']:.3f} -> {after['score']:.3f}"
            entry.update(decision=decision, before=cur_scores, after=new_scores, rows=result["rows"])
            print(f"step {step}: {decision}  holdout {fmt(cur_scores['holdout'])} -> {fmt(new_scores['holdout'])}")
            print(f"         edit: {edit.diagnosis[:160]}")
            if entry["accepted"]:
                current, cur_scores, cur_result = bump_version(candidate), new_scores, result
            history.append(entry)
            jf.write(json.dumps(entry, ensure_ascii=False) + "\n")
            jf.flush()

    best_path = log_path.with_name(f"hill-climb-{stamp}-best.md")
    best_path.write_text(current, encoding="utf-8")
    report_path = log_path.with_suffix(".md")
    report_path.write_text(report(meta, start_scores, cur_scores, history), encoding="utf-8")
    accepted = sum(h["accepted"] for h in history)
    print(f"\n{accepted} of {len(history)} edits accepted. all {fmt(start_scores['all'])} -> {fmt(cur_scores['all'])}")
    print(f"wrote {log_path.relative_to(common.ROOT)}, {report_path.relative_to(common.ROOT)}, {best_path.relative_to(common.ROOT)}")
    if a.apply:
        if accepted and not a.offline:
            common.POSITIONER_FILE.write_text(current, encoding="utf-8")
            print(f"applied to {common.POSITIONER_FILE.relative_to(common.ROOT)}: review the diff, then PR it")
        else:
            print("--apply: nothing written (no accepted edit, or an offline run)")


def report(meta: dict, start: dict, end: dict, history: list[dict]) -> str:
    lines = ["# Positioner hill-climb", "",
             f"Models: positioner `{meta['models']['positioner']}`, judge `{meta['models']['judge']}`, proposer `{meta['models']['proposer']}`."
             f"{' **Offline run: fake scores.**' if meta['offline'] else ''}",
             f"{meta['samples']} samples per lead. Train: {', '.join(meta['train'])}. Holdout: {', '.join(meta['holdout']) or 'none'}.",
             f"Accept rule: score up by more than {meta['min_delta']}, valid-angle rate down by at most {meta['max_guardrail_drop']:.0%}.",
             f"Mode: {meta.get('mode', 'propose')}.",
             *([f"Production cases from traces: {meta['trace_cases']['n']} "
                f"(bad: {', '.join(meta['trace_cases']['bad']) or 'none'}; good: {', '.join(meta['trace_cases']['good']) or 'none'}; "
                f"only in traces, train set: {', '.join(meta['trace_cases']['only_in_traces']) or 'none'})."]
               if (meta.get('trace_cases') or {}).get('n') else []), "",
             f"**Start:** {fmt(start['all'])}, holdout {fmt(start['holdout'])}  ",
             f"**End:** {fmt(end['all'])}, holdout {fmt(end['holdout'])}", "",
             "| Step | Old | New | Score before → after | Holdout before → after | Decision |",
             "|---|---|---|---|---|---|"]

    def cell(t: str) -> str:
        return t.replace("|", "\\|").replace("\n", "<br>")

    for h in history:
        if not h.get("edit"):
            lines.append(f"| {h['step']} | | | | | {cell(h['decision'])} |")
            continue
        b, af = h["before"], h["after"]
        hold = "-" if b["holdout"] is None else f"{b['holdout']['score']:.3f} → {af['holdout']['score']:.3f}"
        lines.append(f"| {h['step']} | {cell(h['edit']['old_text'])} | {cell(h['edit']['new_text'])} | "
                     f"{b['all']['score']:.3f} → {af['all']['score']:.3f} | {hold} | {cell(h['decision'])} |")
    lines += ["", "Rationale per step:", ""]
    lines += [f"{h['step']}. {h['edit']['diagnosis']} {h['edit']['rationale']}" for h in history if h.get("edit")]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
