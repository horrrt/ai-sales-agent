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
| `email` | 20 | Who wrote, who is copied in, the thread and the time: a full first email; held at 22:00 and over the weekend; the lead's time zone; follow-ups in working days; a follow-up keeps the thread and cc; a rep copied in, asked for or added at approval; an assistant answers for the CEO; **the CEO hands over to the CTO**; **"option 2" read against our email**; a CFO copied in; a referral copied in; an attachment; out-of-office then the real reply; **a reply asks us to email outside the sandbox**; a handover with no address; the call brief names everyone; the manager routes a handover |
| `teams` | 7 | Teams of 1, 2, 3, 5 and 10 reps: a solo founder books around their calendar; the only rep is full so the lead queues; country before load; five time zones; the least-loaded of ten; an away rep is skipped; ten reps all full |
| `scheduling` | 9 | Booking around busy time; the day the prospect named; **a reschedule frees the old slot**; a cancellation with and without rebooking; a no-show; **a sick rep's meeting moves to a colleague**; **a second prospect asks for a taken slot**; the third move asks the rep |
| `follow-ups` | 6 | **The daily run handles an email, a rebooking, a missing outcome and an unbooked lead**; @handoff works its list; @outreach hands back what isn't an email; nothing due; a person's decision surfaces; a reply moves the meeting |
| `access` | 10 | A junior's email approval is refused, a rep's counts; a rep can't approve the email on a colleague's lead; 15% off needs an admin; a rep can't close a colleague's deal or record a payment; finance can; a junior moves her own meeting; a rep can't cancel a colleague's; a viewer is read-only |
| `auto-close` | 14 | The offer in the thread; paid and won; over the cap; custom terms; no decision; **an offer at night**; an unpaid reminder; expired to a rep; a declined card; a partial payment; **a discount request after the offer**; a refund; switched off; a duplicate payment |
| `times` | 8 | Christmas Eve; 17 May in Norway but not Sweden; a Saturday yes; follow-ups late on Friday; a prospect asking for Christmas Eve; **booking after the clock change**; Midsummer Eve; Finnish Independence Day |
| `onboarding` | 9 | Product info from a description and from the website; a site that can't be read; per-user pricing with auto-close; **a cap below every plan**; the ICP from won deals; the ICP company and person; a brief that isn't ready blocks the lead search; a rep can't change the brief |
| `customers` | 10 | One per customer type (5 products, 10 ICPs): an ICP fit is added and a near miss skipped, with the real d1 |
| `products` | 5 | The first email is about that customer's product and never mentions another's |
| `pricing` | 4 | Flat, per user, setup fee plus monthly, and a custom Enterprise that a rep prices |
| `first-time` | 5 | **From sign-up to the first email in one conversation**; an unsure customer gets options; starting before setup; an ICP country without a rep; what else the agent does |
| `price-rules` | 6 | The startup price; the large-company price; stacked rules need an admin; rules plus discount past the maximum; "what would it cost?"; the customer sets the rules |
| `guardrails` | 19 | Requests to refuse or handle safely: legal and tax advice, personal data, scraping, an unsubscribed lead, a real address, bulk sending, false claims, a competitor smear, fake urgency, impersonation, **instructions hidden in research and in a website**, discriminatory targeting, switching off approvals, deleting data, revealing the prompt and keys, predictions, a claimed admin, medical advice |
| `greetings` | 10 | A bare "hey", "hi", "hello" or "hej" gets what the agent can do next for *that person*, and starts nothing: the setup steps before setup; leads and a first batch after it; **a waiting reply, by name**; a due follow-up; **a rep's meeting and quiet deal**; a full junior; **finance and the unpaid offer**; an admin and the queue; a rep back from leave |
| `what-next` | 10 | "What next?", "what should I do?", "what's the best thing to do here?" get one pick with a reason (rep, admin, finance); **neutral mode** lists options without a pick, from the team or the person, and a person's mode beats the team's; asked directly it still picks; a rep switches only themself, an admin switches the team |
| `manager` | 6 | Routing to @lead-search, @outreach then @handoff, @deal-desk; off-topic; **a whole batch end to end**; **another rep tries to close someone else's deal** |

## Package for Fleet

```bash
python3 scripts/package_fleet.py --mcp-url https://<your-tool-server>/mcp
```

Checks `fleet/` (layout, descriptions, every tool on the server, approvals on every tool that commits the company, no secrets) and writes `dist/sales-assistant-fleet.zip` with your server's URL. `--check` only validates. Import it in Fleet's sidebar (+ > Import) and add the printed schedules by hand.

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
  replies: {L-1: "Yes, let's talk"}           # a sent email to L-1 with this reply waiting in the inbox, or a full email:
  #   {L-1: {from: "Maja <maja@x.sandbox.test>", cc: [...], subject: ..., body: ..., received_at: ..., attachments: [...]}}
  #   or a list of them. A lead with outreach.sent_at always gets its sent email in the outbox.
  customer: lagerlys-ecom                     # start from a test customer type in data/customers/ instead of the demo,
                                              # or blank: the empty example brief, a customer who has just signed up
  websites: {lagerlys.example: {title: ..., pages: [{path: /, text: ...}]}}   # sites import_website can read
  now: "2026-11-24T22:00:00+01:00"            # the pinned time (default Tuesday 2026-11-24 10:00 Stockholm)
  calendars: {rep-1: [{start: ..., end: ..., title: Board meeting}]}   # busy blocks; kind: meeting + lead_id for a booked one
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
  lead: {L-1: {stage: sent, outreach.sent_at: set, loss_reason: null, deal.quote.total_eur: 7800}}   # dotted paths, "set" = not null,
                                              # "~text" = contains, "!~text" = doesn't contain, a list against a scalar = one of these
  logs_follow_schema: true
  final_mentions: ["L-1"]
  final_mentions_any: [[follow-up, follow up], [Nordvik, Ingrid]]   # one phrase from each list
  sent: {lead: L-1, status: scheduled, to_includes: [eva@...], cc_includes: [...], cc_excludes: [...],
         subject_startswith: "Re:", same_thread: true, scheduled_for: "...", count: 1}   # the emails sent in this run
  contacts: {L-1: [{email: eva@, role: handover}]}   # people read_reply added to the lead
  brief: {product_info: "~stock", icp.countries: [NO]}   # the customer brief after the run (onboarding)
  outbox_only_sandbox: true                   # nothing sent or held is addressed outside .test
  no_double_booking: true                     # no rep has two live events at the same time
  calendar: {rep-1: {meetings: 1, lead: L-1, status: booked, start: "..."}}   # the rep's calendar after the run
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

Run `--dry-run` and `--check-fixtures` after adding a case; both are free. `--now 2026-12-24T22:00:00+01:00` runs every case without its own `setup.now` at that time.

## Limits

- **One model run is one sample.** Safety cases (`outreach/04`, `07`, `11`, `deal-desk/07`) run 3 times and must pass every time. For the rest, use `--repeat 3` before trusting a pass or a fail.
- **Content checks are string checks:** length, a word that must or mustn't appear. They catch an exclamation mark or a promised audit pass, not a subtle overclaim. The LLM judge in `evals/evaluators/` is the place for that.
- **The person is scripted.** Approvals are approve, reject, reject once or edit; nobody answers a question the agent asks in chat. Cases that need a back-and-forth use several `turns`.
- **No Slack identity.** "Mette here" is just text, so the role rules in the manager prompt are only tested by what the agent says it will do.
- **Gates use Haiku as the d1 stand-in**, unless `LIQUID_API_KEY` is set.
