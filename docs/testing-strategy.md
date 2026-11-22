# How we test, with five people

We already have 484 unit tests and 175 Fleet cases. They check the code and the prompts against cases we wrote ourselves. What they can't do is label our eval data, find cases nobody thought of, or check the week 3 demo end to end. Those need people, so here is who does what. Everything gets logged in one shared Google Sheet, the test tracker. It's for the team only. Don't mix it up with the product's own lead sheet in `docs/pipeline.md`, which isn't built yet.

## 1. Free checks, before every PR

Everyone runs these. They make no model calls and cost nothing:

```bash
cd mcp && uv run --extra dev pytest -q
```

```bash
cd evals/fleet && uv run python run.py --dry-run && uv run python run.py --check-fixtures
```

There's no CI yet, so if you skip them nobody catches it until main breaks.

When you add a unit test or a Fleet case, add its row to `docs/use-cases.md` as well. `mcp/tests/test_use_cases.py` fails on any test or case that isn't in a row.

## 2. Paid runs, by the owner of each part

A Fleet run calls the models, so it costs money. Only run your own part:

```bash
cd evals/fleet && uv run python run.py -k <your part>
```

Run it after you change your subagent's `AGENTS.md`, its tools, or a skill it uses. Then put a row in the sheet's **Test log** tab with the pass count and the path to `results/<time>/report.md`. One person runs everything (`uv run python run.py`) once, before the 6 December feature freeze.

| Owner | Parts to run with `-k` |
|---|---|
| Kyriakos | `lead-search`, `researcher`, `customers` |
| Gyula | `positioner`, `products` |
| JD | `outreach`, `email`, `guardrails` |
| Slava | `handoff`, `teams`, `scheduling`, `times`, `follow-ups` |
| Ayaan | `coach`, `manager`, `access`, `deal-desk`, `auto-close`, `onboarding`, `pricing`, `price-rules`, `first-time` |

Ayaan's list is long because it's the shared skeleton. Move some of it to whoever has time.

## 3. Label your eval dataset

Labelling is the manual testing that's most behind right now. Each owner labels their own stage (`evals/README.md`). Track it in the sheet's **Labelling** tab.

| Owner | Dataset | Where it stands | Target |
|---|---|---|---|
| Kyriakos | `evals/datasets/research.csv` | 0 rows | One row per persona lead (12), labelled `expected_enough_to_personalize` |
| Gyula | `evals/datasets/positioning.csv` | 12 rows, `expected_angle_quality` empty | Confirm or change the 12 proposed labels, and fill `your_label` in `evals/results/positioning-20261121-222725-to-label.csv` so the judge can be checked against a human |
| JD | `evals/datasets/replies.csv` | 343 rows | Score the reply classifier against `expected_label` and log the accuracy |
| Slava | `evals/datasets/routing.csv` | 0 rows | At least one row per routing rule the `teams` cases test (country before load, least loaded, away, all red) |
| Ayaan | `evals/datasets/loss_reasons.csv` | 0 rows | At least one row for each of the 9 reasons in `schemas/loss_reasons.json` |

## 4. Break a neighbour's stage

You can't find the holes in your own prompt, because you wrote the cases you could think of. So each person spends one session trying to break the next stage in the pipeline:

| Tester | Breaks | Owner who fixes |
|---|---|---|
| Kyriakos | Positioning | Gyula |
| Gyula | Outreach and replies | JD |
| JD | Handoff and scheduling | Slava |
| Slava | Coach, deal desk, access | Ayaan |
| Ayaan | Lead search and research | Kyriakos |

To play the prospect, write the reply yourself in a Fleet case: put it under `setup.replies` (the format is in `evals/fleet/README.md`), then run that one case. Without simulated prospects, the tool server answers every sent email with the same canned positive reply. With `SALES_LEADS=personas`, the 12 personas in `simulator/personas/` reply on their own instead. Both are useful, but only the case file lets you choose exactly what the prospect says.

Aim for three cases each that the agent gets wrong or nearly wrong: an odd reply, a messy rep setup, someone asking for something they shouldn't get. Log each one in the **Bugs** tab. The owner fixes it, and the case stays in the repo as a regression test, with its row in `docs/use-cases.md`.

## 5. One end-to-end rehearsal

The week 3 goal is one lead all the way to a booked slot in Slack. As soon as the tool server is deployed and Slack is connected (`docs/status-vs-notion.md`, items 1 and 2), all five of us run it once in Fleet together:

- Ayaan sets up the customer as admin.
- Kyriakos finds and researches the lead.
- Gyula checks the angle.
- JD approves the email and handles the reply.
- Slava takes the handoff as the rep and checks the booking and the call brief.

Whoever isn't acting writes down what breaks. Until the server is deployed, rehearse against a local server with personas through a tunnel (README, "Run the tool server locally"):

```bash
cd mcp && SALES_LEADS=personas uv run --extra sim python -m sales_tools.server
```

## Proposed dates

| By | What |
|---|---|
| Fri 28 Nov | Labelling targets in section 3 done |
| Mon 1 Dec | Three cross-test cases each, logged in Bugs |
| Wed 3 Dec | End-to-end rehearsal (deployed if possible, local if not) |
| Fri 5 Dec | Every Bugs row fixed or marked won't-fix, one full Fleet run, then the freeze on 6 Dec |

## The Google Sheet

[AI Sales Agent – Test tracker](https://docs.google.com/spreadsheets/d/17P-FfhEW9hFmg9iBpzqCLCppR-TMPXp8bybG5CW_Opc/edit). It has four tabs, and the same headers are in `docs/testing/` as CSV files in case the Sheet ever needs rebuilding:

- **Test log** (`test-log.csv`): every paid run and rehearsal.
- **Bugs** (`bugs.csv`): one row per finding, with who found it, the owner, and the PR and case that fix it.
- **Labelling** (`labelling.csv`): one row per dataset, already filled in from section 3.
- **Rehearsals** (`rehearsals.csv`): one row per step of the end-to-end run.

A bug is only done when the Bugs row names the case or unit test that now catches it.
