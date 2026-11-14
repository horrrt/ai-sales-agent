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
