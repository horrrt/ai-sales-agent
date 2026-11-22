# Fleet tests

Agent-level tests for the Fleet agent: one folder of cases per part of the pipeline. Each case runs the real prompts from `fleet/` (the manager with its subagents and skills, or one subagent on its own) against this repo's tool server, answers the approvals the way a person would, and checks the tool calls and the saved state afterwards.

They don't use Fleet or its credits. Models are called through the Anthropic API with `ANTHROPIC_API_KEY` from the repo's `.env`. The tool unit tests in `mcp/tests/` are separate and make no model calls.

## Run

```bash
uv run --project evals/fleet python evals/fleet/run.py --dry-run          # check every case, no model calls
uv run --project evals/fleet python evals/fleet/run.py --check-fixtures   # load every case into the tool server, no model calls
uv run --project evals/fleet python evals/fleet/run.py -k outreach        # run one part (model calls)
uv run --project evals/fleet python evals/fleet/run.py                    # run everything (model calls)
```

By default the manager, positioner, outreach and deal-desk run on Sonnet 5.5, and the rest on Haiku 5.5 (`--model-map ''` for Haiku everywhere). Each run writes `results/<time>/report.md`, `results.json`, and per case a `.jsonl` trace of every tool call and approval plus the final `.state.json`.

## What's covered

| Part | Cases | What they check |
|---|---|---|
| `lead-search` | 4 | A fit goes into the lead sheet; a non-fit is skipped with `wrong_company_fit`; a duplicate isn't added; no decision backend means nobody is added |
| `researcher` | 2 | Sourced facts pass "enough to write?"; thin research is retried, then on hold (not lost) |
| `positioner` | 2 | The angle rests on a research fact; a consumer business gets no forced angle |
| `outreach` | 11 | Approve and send (email length, a research fact, tone); rejection logged and redrafted to the rep's note; the rep's edit is what's sent; nothing sent without approval even when told to skip it; reply exits; unsubscribe; **injected instructions in a reply**; referral names the colleague; **no decision backend**; a due follow-up; **an overclaim the brief forbids** |
| `handoff` | 5 | Green rep, weekday slot, call brief; all reps red means the queue; a held meeting opens the deal; **the rep rejects the first slot**; **the queue is released when a rep frees up** |
| `deal-desk` | 9 | Notes, BANT, quote approved and sent; discount above the limit flagged; above the maximum nothing goes out; lost with a reason; auto-close; quiet deals; **someone claims to be the admin**; **BANT fails, no quote**; **two turns: the rep changes the quote** |
| `coach` | 3 | Rep edits lead to one proposal; too few leads, no proposal; **losses to a competitor are cited as evidence** |
| `manager` | 6 | Routing to @lead-search, @outreach then @handoff, @deal-desk; off-topic; **a whole batch end to end**; **another rep tries to close someone else's deal** |

## Write a case

One YAML file in `cases/<part>/`. Everything under `setup` is optional; every case starts from `data/customer_brief.demo.json` (Kvitto) and `data/reps.demo.json`.

```yaml
title: One line saying what this checks
agent: outreach               # manager, or a folder name in fleet/subagents/
setup:
  brief: {auto_close: {enabled: true}}        # merged into the demo brief
  reps: {rep-1: {status: red, ...}}           # replaces the demo reps
  source: [{lead_id: L-1, person: ..., company: ..., research: {...}}]   # candidates for find_leads; research is what research_target finds
  leads: [{lead_id: L-1, stage: positioned, person: ..., company: ..., research: ..., positioning: ...}]
  replies: {L-1: "Yes, let's talk"}           # a sent email to L-1 with this reply waiting in the inbox
  interactions: [...]                         # event log rows, e.g. for the coach
turns:
  - What the user types
approvals:                                    # how a person answers (default: approve)
  record_approval: approve                    # or reject, {action: reject, message: ...}, {action: reject_first, ...},
                                              # {action: edit, args: {subject: ...}}
  book_meeting: {reject_weekend: true}
expect:
  called: [save_draft, send_email]
  not_called: [book_meeting]
  called_with: {draft_quote: {plan: annual, discount_pct: 15}}     # "~text" means contains
  asked_person: [record_approval]             # Fleet asked a person before this tool ran
  not_asked_person: [approve_quote]
  delegated_to: [outreach, handoff]           # manager cases
  lead: {L-1: {stage: sent, outreach.sent_at: set, loss_reason: null, deal.quote.total_eur: 7800}}   # dotted paths, "set" = not null
  logs_follow_schema: true
  final_mentions: ["L-1"]
  queue_length: 1
  proposals: 1
  max_tool_calls: 1
  times_called: {send_email: 1}
  final_must_not_contain: ["Ledgerfox"]
  email: {lead: L-1, max_words: 120, mentions_any: [hospital, pilot], must_not_contain: ["!", guarantee]}   # the lead's latest draft
  events: [{stage: approval, outcome: failure, by: agent}]   # rows in the saved event log; "~text" means contains
repeat: 3          # run the case 3 times (or --repeat N for every case)
pass_at: 3         # how many runs must pass (default: all)
```

`setup.no_decisions: true` starts the tool server without `LIQUID_API_KEY` or `ANTHROPIC_API_KEY`, so every gate returns no answer. That tests that the agent sends the lead to a person instead of guessing.

Run `--dry-run` and `--check-fixtures` after adding a case; both are free.

## Limits

- **One model run is one sample.** Safety cases (`outreach/04`, `07`, `11`, `deal-desk/07`) run 3 times and must pass every time. For the rest, use `--repeat 3` before trusting a pass or a fail.
- **Content checks are string checks:** length, a word that must or mustn't appear. They catch an exclamation mark or a promised audit pass, not a subtle overclaim. The LLM judge in `evals/evaluators/` is the place for that.
- **The person is scripted.** Approvals are approve, reject, reject once or edit; nobody answers a question the agent asks in chat. Cases that need a back-and-forth use several `turns`.
- **No Slack identity.** "Mette here" is just text, so the role rules in the manager prompt are only tested by what the agent says it will do.
- **Gates use Haiku as the d1 stand-in**, unless `LIQUID_API_KEY` is set.
