"""A/B test: researched positioning vs a merge-field template, on simulated prospects. Owner: Gyula.

This is the course OMTM from the business case: positive reply rate from simulated prospects,
deep research against templates.

    uv run --project simulator python evals/ab_test.py --samples 3
    uv run --project simulator python evals/ab_test.py --offline      # plumbing check only, no model calls

Arms
- research: the positioner (fleet/subagents/positioner/AGENTS.md) picks an angle from the lead's research,
  then a drafter writes the email from that angle. If the positioner stops a lead, no email is sent.
- template: the customer's own best merge-field template (name, company, industry, size, country),
  written to be a fair, decent cold email, not a strawman.

Fairness
- The persona never sees which arm an email came from.
- Personas run on a different model (PERSONA_MODEL) from the positioner and drafter, so the result isn't
  one model rewarding its own style.
- Fixed-outcome personas (out of office, wrong person) are left out of the comparison.
- Each persona gets --samples emails per arm; counts are reported with n, not only as percentages.

The drafter here is a local stand-in for the outreach subagent (JD) so the A/B can run before the
pipeline is integrated. Swap it for JD's drafting once that exists, and rerun.

Headline metric: positive replies per lead attempt. A lead the research arm stops (no fit, or an angle
save_positioning would reject) counts as an attempt with no positive reply, so stopping weak fits can't
inflate the research arm. Positive per email sent is shown as a secondary number.

Outputs: evals/results/ab-<timestamp>.jsonl (every email and reply, written as each one completes) and
simulator/out/replies-<timestamp>.csv in the replies.csv format (lead_id, reply_body, expected_label, notes)
for JD's reply-classification eval.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import llm, simulate  # noqa: E402

COUNTRY = {"SE": "Sweden", "FI": "Finland", "NO": "Norway", "DK": "Denmark"}
SENDER = "Mette\nKvitto"

TEMPLATE_SUBJECT = "{company} and security reviews"
TEMPLATE_BODY = """Hi {first_name},

Most {industry} companies of {company}'s size in {country} hit the same wall when they start selling to bigger customers: security questionnaires and requests for SOC 2 or ISO 27001.

Kvitto gets teams like yours audit-ready in about 8 weeks. It connects to your cloud and tools, collects the evidence automatically, and a Danish compliance lead helps you answer questionnaires for the first three months. Fjordline Analytics (14 people, Oslo) reached SOC 2 Type I in 10 weeks.

Would a 20-minute call next week be useful?

Best,
{sender}"""


class Email(BaseModel):
    subject: str = Field(description="Short subject line, no clickbait")
    body: str = Field(description="The email body, signed by the sender")


DRAFTER_SYSTEM = """You write first-touch B2B cold emails for the customer described in the brief, from the positioning angle you are given.

- Open with the angle: the lead's situation, in one sentence, from the evidence. Then the part of the offer that answers it. Then one easy ask.
- Follow the brief's tone rules exactly, including the word limit.
- Only state facts about the lead that are in the evidence, and only claims about the offer that are in the brief.
- Sign off exactly as given in the sender line."""


def template_email(lead: dict) -> dict:
    f = {"first_name": lead["person"]["name"].split()[0], "company": lead["company"]["name"],
         "industry": lead["company"]["industry"], "country": COUNTRY.get(lead["company"]["country"], lead["company"]["country"]),
         "sender": SENDER}
    return {"subject": TEMPLATE_SUBJECT.format(**f), "body": TEMPLATE_BODY.format(**f)}


def research_email(lead: dict, brief: dict, pos: dict) -> dict:
    user = (f"<customer_brief>\n{json.dumps(brief, indent=2, ensure_ascii=False)}\n</customer_brief>\n\n"
            f"<lead>\n{json.dumps({'person': lead['person'], 'company': lead['company']}, ensure_ascii=False)}\n</lead>\n\n"
            f"<positioning>\n{json.dumps({k: pos[k] for k in ('angle', 'evidence')}, ensure_ascii=False)}\n</positioning>\n\n"
            f"Sender line:\n{SENDER}")
    return llm.structured(llm.DRAFTER_MODEL, DRAFTER_SYSTEM, user, Email, effort="low").model_dump()


def _call(fn, *args, **kw):
    """Run one model step; return (result, error string). Errors are recorded, not fatal."""
    try:
        return fn(*args, **kw), None
    except Exception as e:  # noqa: BLE001  (refusals, truncation, API errors all end up in the row)
        return None, f"{type(e).__name__}: {e}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=3, help="emails per persona per arm")
    ap.add_argument("--leads", nargs="*", help="limit to these lead ids")
    ap.add_argument("--offline", action="store_true", help="no model calls: checks the plumbing, results are meaningless")
    a = ap.parse_args()
    offline = a.offline or not llm.has_key()
    if offline:
        print("OFFLINE: no model calls. Replies are stubs, so the numbers below are NOT results.\n")

    brief = common.load_brief()
    personas = [p for p in simulate.all_personas() if not a.leads or p["lead_id"] in a.leads]
    stamp = common.stamp() + ("-OFFLINE" if offline else "")
    out = common.RESULTS / f"ab-{stamp}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    replies_out = common.ROOT / "simulator" / "out" / f"replies-{stamp}.csv"
    replies_out.parent.mkdir(parents=True, exist_ok=True)
    models = {"positioner": llm.POSITIONER_MODEL, "drafter": llm.DRAFTER_MODEL, "persona": llm.PERSONA_MODEL}

    rows: list[dict] = []
    with out.open("w") as jf, replies_out.open("w", newline="") as cf:
        cw = csv.writer(cf)
        cw.writerow(["lead_id", "reply_body", "expected_label", "notes"])

        def emit(row: dict) -> None:
            row["models"] = models
            rows.append(row)
            jf.write(json.dumps(row, ensure_ascii=False) + "\n")
            jf.flush()
            r = row.get("reply")
            if r and r["intended_label"] != "no_reply":
                cw.writerow([row["lead_id"], r["body"], r["intended_label"],
                             f"simulated ({r['source']}), arm={row['arm']}, sample={row['sample']}"])
                cf.flush()

        for p in personas:
            lead = p["lead"]
            fixed = bool(p["private"].get("fixed_reply"))
            pos, pos_err = (None, None) if offline else _call(common.run_positioner, lead, brief)
            for i in range(1 if fixed else a.samples):  # fixed outcomes don't depend on the email
                for arm in ("research", "template"):
                    row = {"lead_id": lead["lead_id"], "arm": arm, "sample": i, "fixed": fixed, "sent": False,
                           "not_sent_reason": None, "error": None, "positioning": pos if arm == "research" else None,
                           "email": None, "reply": None}
                    if arm == "template":
                        email = template_email(lead)
                    elif offline:
                        email = {"subject": "STUB", "body": "STUB research email"}
                    elif pos_err:
                        row["error"] = f"positioner: {pos_err}"
                        emit(row)
                        continue
                    elif pos["no_fit"]:
                        row["not_sent_reason"] = f"positioner stopped the lead ({pos['loss_reason']})"
                        emit(row)
                        continue
                    elif pos["validation_problems"]:
                        # save_positioning would reject this angle, so outreach would never draft it.
                        row["not_sent_reason"] = "save_positioning would reject: " + "; ".join(pos["validation_problems"])
                        emit(row)
                        continue
                    else:
                        email, err = _call(research_email, lead, brief, pos)
                        if err:
                            row["error"] = f"drafter: {err}"
                            emit(row)
                            continue
                    row["email"] = email
                    r, err = _call(simulate.reply, p, email["subject"], email["body"], offline=offline)
                    if err:
                        row["error"] = f"persona: {err}"
                    else:
                        row["sent"], row["reply"] = True, r
                    emit(row)
            print(f"{lead['lead_id']} done")

    report(rows)
    print(f"\nwrote {out.relative_to(common.ROOT)} and {replies_out.relative_to(common.ROOT)}")
    if offline:
        print("OFFLINE run: do not report these numbers.")


def report(rows: list[dict]) -> None:
    errored = [r for r in rows if r["error"]]
    compared = [r for r in rows if not r["fixed"] and not r["error"]]
    print("\nPer lead: positive replies / attempts (s = stopped before sending)")
    by_lead: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for r in compared:
        c = by_lead[r["lead_id"]][r["arm"]]
        c["attempts"] += 1
        c["stopped"] += not r["sent"]
        if r["sent"]:
            c[r["reply"]["intended_label"]] += 1
    for lid, arms in sorted(by_lead.items()):
        cells = []
        for arm in ("research", "template"):
            c = arms[arm]
            cells.append(f"{arm} {c['positive']}/{c['attempts']}" + (f" ({c['stopped']}s)" if c["stopped"] else ""))
        print(f"  {lid}: " + "   ".join(cells))

    print("\nTotals (fixed-outcome personas and errored rows left out)")
    print("Headline: positive replies per lead attempt. Stopping a lead counts as no positive reply.")
    for arm in ("research", "template"):
        rs = [r for r in compared if r["arm"] == arm]
        sent = [r for r in rs if r["sent"]]
        labels = Counter(r["reply"]["intended_label"] for r in sent)
        pos = labels["positive"]
        per_attempt = f"{pos / len(rs):.0%}" if rs else "n/a"
        per_sent = f"{pos / len(sent):.0%}" if sent else "n/a"
        print(f"  {arm:9} positive {pos} of {len(rs)} attempts ({per_attempt}); of {len(sent)} sent ({per_sent}); "
              f"stopped {len(rs) - len(sent)}")
        print(f"            outcomes: {dict(labels)}")
    print("\nNegative + unsubscribe are the brand-damage cost of each arm; stopped leads cost nothing.")
    if errored:
        print(f"\n{len(errored)} rows errored and are not counted above, e.g. {errored[0]['error'][:160]}")


if __name__ == "__main__":
    main()
