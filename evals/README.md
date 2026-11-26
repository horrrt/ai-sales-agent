# Evals

Owner of the framework: Ayaan (stream 4). Each owner labels their own stage's data.

| Dataset | Labelled by | What it checks |
|---|---|---|
| `datasets/research.csv` | Kyriakos | Profile has enough correct, sourced facts to personalize |
| `datasets/positioning.csv` | Gyula | Angle fits the person and is backed by evidence (LLM-as-judge + human labels) |
| `datasets/replies.csv` | JD | Reply label matches the simulated prospect's intended label |
| `datasets/routing.csv` | Slava | Lead went to the right rep given the traffic lights |
| `datasets/loss_reasons.csv` | Ayaan | Agent's loss reason matches a human label |

Upload each CSV as a LangSmith dataset with the same name (prefix `sales-agent-`). Rerun the whole set on every version, not only the cases that failed last time.

`evaluators/`: custom evaluator code or LLM-as-judge prompts, one file per dataset.

## Positioning (Gyula)

- `positioning_run.py`: runs the positioner prompt (`fleet/subagents/positioner/AGENTS.md`, loaded as-is) on every persona's lead, applies the same checks as `save_positioning`, and grades each angle with `evaluators/positioning_judge.py`. It also writes a `-to-label.csv` for hand-labelling the actual angles, so the judge can be checked against a human.
- `ab_test.py`: researched positioning against a merge-field template, on simulated prospects. Reports positive replies with n per arm.
- `hill_climb.py`: hill-climbs the positioner prompt (steps 4 to 6 of `docs/fleet-prompts-v2.md` §3.3). A proposer model reads the lowest-scored angles and suggests one edit to `AGENTS.md`. The edited prompt runs on every persona lead, and the edit is kept only if the judge score rises by more than 0.02 and the share of angles passing `save_positioning`'s checks drops by no more than 2 points. A third of the leads are held out from the proposer, and their score is logged so you can see when an edit only fits the training leads. Each run writes a changes table (old → new, before → after, decision) to `results/hill-climb-<time>.md`. `--offline` checks the plumbing without model calls, and `--apply` writes an accepted prompt back into `fleet/` for you to review and PR.
- `traces_to_cases.py` (Ayaan): learning from production. It reads the tool runs and outcome feedback the tool server wrote to LangSmith, and writes one case per lead to `datasets/trace_cases.jsonl`: the lead as the positioner saw it (research and homepage facts), the angle it saved, the email the rep approved or changed, the prompt version, and what happened, judged `bad`, `good` or `open` for the positioning stage. `--to-langsmith <dataset>` also adds new cases to a LangSmith dataset. No model calls. Run it from `mcp/` with `uv run --env-file ../.env python ../evals/traces_to_cases.py`.
- `hill_climb.py --trace-cases datasets/trace_cases.jsonl`: climbs on the personas plus those cases. Leads only seen in production join the train set, and the holdout stays the same personas, so production cases never leak into it. The proposer sees each train lead's real outcome next to the reviewer's grade, including failures the reviewer scored well.
- `hill_climb.py --edit-old @old.txt --edit-new @new.txt`: measures one given edit (the weekly review's proposal) with the same accept rule instead of proposing one, so the admin sees the before/after before approving it.
- `datasets/positioning.csv`: one row per persona lead. `expected_angle_quality` is the best outcome a careful salesperson could reach for that lead (`good`, `weak` or `correct_stop`). It's empty on purpose: the notes column has Claude-proposed labels that Gyula still has to confirm or change before they count as human labels.

Run with `uv run --project simulator python evals/<script>.py`. Results go to `evals/results/`.

To turn an A/B run into funnel numbers, `python3 scripts/ab_to_sheet.py | pbcopy` prints one row per persona and arm. Paste it at `Personas!A5` in the [funnel simulator sheet](https://docs.google.com/spreadsheets/d/1kI-c29_DIgmAz7pDn9VUYFD9S6pYn-5HLIt_igPIi7I/edit) (ask Gyula for access). The sheet simulates a month of outreach 1,000 times from those counts, with Beta uncertainty on the rates, and reports how likely it is that research really beats the template.
