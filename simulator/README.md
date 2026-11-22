# Simulated prospects

Owner: Gyula (stream 5).

LLM personas built from the customer's ICP that receive sandbox outreach and reply the way real prospects would: positive, negative, not now, wrong person, out of office, unsubscribe. They make the whole pipeline testable without real sends, and they're what the "research beats templates" A/B test runs on.

## Files

- `personas/P-*.json`: 12 fictional Nordic companies for the demo customer (`data/customer_brief.demo.json`, Kvitto, a compliance tool). Each file has:
  - `lead`: what the pipeline can know, in the `schemas/lead.schema.json` shape, including `research.facts` with source URLs (fake `.example` domains, `sandbox.test` emails)
  - `private`: what only the prospect knows: real situation, pains, timing, budget, objections. Research shows only part of it, which is the only reason researched outreach can beat a template
  - `design_note`: what the persona is there to test (strong fit, has a competitor, bad timing, outside the ICP, overclaim trap, ...)
- `simulate.py`: `reply(persona, subject, body) -> {body, intended_label, reasoning, source}`. `intended_label` is the ground truth for JD's reply classification, and uses the `outreach.reply_label` values exactly.
- `llm.py`: shared Anthropic client, model choice, optional LangSmith tracing.

Two personas have a fixed outcome that doesn't depend on the email (P-006 wrong person, P-007 out of office). The rest decide from the email.

## Run

```bash
cp .env.example .env   # add ANTHROPIC_API_KEY, optionally LANGSMITH_API_KEY
uv run --project simulator python simulator/simulate.py P-001
uv run --project simulator python evals/positioning_run.py
uv run --project simulator python evals/ab_test.py --samples 3
```

`--offline` (or no API key) runs without model calls. Use it only to check the plumbing: stub replies are labelled `source: stub` and those numbers mean nothing.

Models: the agent side (positioner, drafter) defaults to `claude-opus-5-5`, and personas and the judge to `claude-sonnet-5-5`, so the A/B isn't one model grading its own writing. Override with `POSITIONER_MODEL`, `DRAFTER_MODEL`, `PERSONA_MODEL`, `JUDGE_MODEL`.

`ab_test.py` also writes `simulator/out/replies-<timestamp>.csv` in the `evals/datasets/replies.csv` format, for JD's reply classification eval.

## On real email (OpenMail)

When the tool server sends through OpenMail (main README), `openmail_responder.py` plays the prospect on real email. For every email the agent sends to a test inbox, it picks a kind of reply at random: positive with a proposed time, asking for times or the price, an assistant answering, not now (with a made-up quarter or date), negative (competitor, price, plain no), out of office (made-up return date), wrong person, handover, unsubscribe, a vague question, silence, and a move or cancel once a meeting is booked. It answers in the thread after a random delay. No model calls, so it's free.

```bash
uv run --project simulator python simulator/openmail_responder.py                      # keeps watching
uv run --project simulator python simulator/openmail_responder.py --delay 5:60 --seed 7 --weights handover=4,no_reply=0
```

Each reply, and each deliberate silence, is logged with the label it meant in `simulator/out/openmail-replies.csv`, so you can compare it with what `read_reply` decided. With only the agent's own inbox, the prospect answers from that inbox. A handover then only names the colleague. Add a second test inbox to `OPENMAIL_TEST_RECIPIENTS` and handovers copy that inbox in.

## In the pipeline

The tool server can use the personas as its prospects, so the Fleet agent (or a local run of it) gets real simulated replies instead of the canned positive one:

```bash
cd mcp && SALES_LEADS=personas uv run --extra sim python -m sales_tools.server
```

With that set, the server loads the 12 persona leads instead of `data/sample_leads.json`, uses the demo brief (Kvitto) unless `data/customer_brief.json` exists, `research_target` returns each persona's research, and `get_replies` asks the persona for a reply to the email that was actually sent. A persona that ignores the email has no reply. The agent only ever sees the reply body. The label the persona intended goes to `simulator/out/pipeline-replies.csv` (git-ignored), in the `replies.csv` format, so JD's classifier can be scored against it afterwards.

It's off by default, and the deployed server (Dockerfile) doesn't include it.

## Limitation to state in the pitch

Simulated replies test the pipeline, not whether real people would reply. A positive rate from personas is an early signal that research and positioning work, not a reply-rate forecast.
