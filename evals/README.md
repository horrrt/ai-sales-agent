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
- `datasets/positioning.csv`: one row per persona lead. `expected_angle_quality` is the best outcome a careful salesperson could reach for that lead (`good`, `weak` or `correct_stop`). It's empty on purpose: the notes column has Claude-proposed labels that Gyula still has to confirm or change before they count as human labels.

Run with `uv run --project simulator python evals/<script>.py`. Results go to `evals/results/`.
