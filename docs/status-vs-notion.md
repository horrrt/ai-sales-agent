# Where we are against the Notion plan

As of 22 November 2026, on `main` (PRs #2 to #8 merged). Compared with the Notion pages "Sales Assistant Agent (Applied AI)" and "AI Sales Agent: Business Case".

The whole pipeline from the Notion page runs end to end in the sandbox: setup, finding leads, research, positioning, approved outreach, replies, routing to a rep, booking, the call brief, follow-ups, closing deals and small-deal auto-close, plus the weekly review. Every step is checked by 484 unit tests and 175 Fleet cases run against the real models. The gap is everything that touches the outside world. The lead source and research are stubs, the tool server isn't deployed, and Slack isn't connected, so the week 3 goal ("one Clay lead all the way to a booked slot in Slack") isn't met yet. Four of the five one-pagers and the pitch deck haven't been started.

## What the customer gives

| Notion | Status | Where |
|---|---|---|
| Sales proposal (pricing, deal) | Done | Brief `pricing`: plans, minimum terms, per-user minimums, setup fees, custom plans, discount limits, and price rules per prospect (size, country, industry, volume, term) |
| ICP: the company | Done | Brief `icp`: countries, size range, industries from a shared list, buying triggers. Exclusions are passed to the ICP gate |
| ICP: the person inside the company | Done | Brief `icp.titles` |
| Product or service info (or the website) | Done, sandbox only | `import_website` reads sites stored for the run. There is no live fetching yet |
| 3-5 won deals, an exclusion list, tone and claims, target countries (business case §12) | Done | Brief `won_deals`, `exclusions`, `tone`, `icp.countries` |

## What it does

| Notion | Status | Where |
|---|---|---|
| Helps the user describe the product and add their website | Done | @onboarding, `import_website`, `update_brief` (asks an admin) |
| Helps with pricing | Done | `review_pricing`: first-year value per plan, discount rules, whether auto-close can ever fire |
| Helps discover the ICP | Done | `suggest_icp_from_deals` (won and lost by country, size, industry, title), or options when there's no history |
| A first-time customer sets it all up and starts | Done | `get_setup_status` checklist, team setup tools, the lead search waits until it's ready |
| Collects targets from Clay | **Stub** | `get_targets_from_clay` and `find_leads` read sample leads or the simulated personas. No Clay API |
| Finds the decision maker | Partly | The ICP gate checks the title. Nothing searches for people inside a company |
| Research: LinkedIn, website, blogs (Instagram dropped) | **Stub** | `research_target` returns fixture or persona research. No real web or LinkedIn research. The VLM step is built: `research_website_visual` reads a homepage screenshot (committed fixtures for the sandbox leads, real pages captured locally, or live with `SALES_SCREENSHOTS=live`) with a vision model and adds up to three sourced facts |
| Custom positioning per person | Done | Positioner on Sonnet, evidence must come from research facts, positioning eval and LLM judge in `evals/` |
| Automates the first outreach, a few back-and-forths | Done, sandbox | Approve-before-send, threads, cc, business hours and public holidays per country, follow-ups in working days, a daily cap, handovers and assistants, replies read next to the email they answer |
| Takes over from the first call with notes on person, company and positioning | Done | `save_call_brief`, with everyone in the thread |
| Multiple people use it through Slack | **Not connected** | Fleet's Slack tools are in the tool list, but the `#sales-desk` channel ID and the reps' Slack user IDs are still TODO |
| Traffic light per rep, routing by workload | Done | Green, yellow, red and away. Country before load. Teams of 1 to 10 tested. A wait queue when everyone is full |
| Calendar slot from availability | Done, in the store | Rep calendars with working hours and time zones. Booking, rescheduling, cancelling, no double-booking. **Not Google Calendar**: invites aren't sent |
| Stretch: closes deals under EUR 2k alone, books meetings for bigger ones | Done, sandbox | Auto-close on a self-serve plan under the cap. Offers in the thread, reminders, expiry to a rep, failed and partial payments, refunds. The checkout is a sandbox link and payments are recorded by a person, with no payment provider in test mode |

## Learning loop ("how we apply the things learned in class")

| Notion | Status | Where |
|---|---|---|
| Success metrics per stage | Done | `get_funnel_metrics`: by stage, losses by reason, the North Star (meetings held), won revenue, closed automatically, time from reply to hand-off |
| Agent assigns the reason for each loss | Done | One reason from `schemas/loss_reasons.json`, checked in code |
| History of all interactions | Done | Event log (`get_event_log`), including who approved, edited or rejected what |
| Improves the process (self-improving) | Partly | The weekly review (@coach) proposes one rule change at a time, and an admin approves it. **No before/after example** of the loop improving something yet |
| LangSmith tracing on every stage | Partly | Fleet traces in LangSmith. The local runner and the event log don't link to traces: `trace_id` is always empty |
| Funnel dashboard | **Not built** | The numbers are there (`get_funnel_metrics`), but there's no dashboard |
| Eval framework, each owner labels their stage | Partly | `evals/datasets/` for research, positioning, replies, routing and loss reasons. Only the positioning eval has run (`evals/results/`) |
| Research vs template A/B on simulated prospects | Done once | Research 4 positive out of 30 against template 3 out of 30. The simulator counts interested-but-asking replies as negative, so the numbers understate both arms |

## Week 1 agreements

| Notion | Status |
|---|---|
| Lead object | Done: `schemas/lead.schema.json` (with deal, contacts, meetings, payments) |
| Interaction log entry | Done: `schemas/interaction.schema.json` |
| One LangGraph / LangSmith setup, one node per stage | Changed: Fleet agents (a manager and 8 subagents) on one MCP tool server, plus a local runner on LangChain deepagents for testing without Fleet credits |
| A test set of 50 real Clay leads | **Not done**: 12 simulated personas and fixture leads instead |

## Weekly plan

| Week | Goal | Status |
|---|---|---|
| 1 (12-18 Nov) | Runs end to end with stubs | Done |
| 2 (19-25 Nov) | Real versions, measured | The logic is real and measured. Research and the lead source are still stubs |
| 3 (26 Nov-2 Dec) | One Clay lead all the way to a booked slot in Slack, through the sandbox | **Not yet**: needs Clay, the deployed tool server and Slack |
| 4 (3-9 Dec) | Show it working and improving. Feature freeze 6 Dec | Auto-close (the stretch goal) is already built. Still needed: the before/after loop example |

## MVP scope (business case §12)

| Item | Status |
|---|---|
| Must: Clay import | Stub |
| Must: research agent (website, blog, LinkedIn via Clay) | Stub |
| Must: positioning per target | Done |
| Must: drafting with approve-before-send | Done |
| Must: sandbox sending | Done |
| Must: simulated prospects that reply | Done |
| Must: reply classification | Done |
| Must: call brief | Done |
| Must: LangSmith tracing with stage metrics and loss reasons | Metrics and loss reasons done. Tracing not linked |
| Should: Slack traffic light and calendar slots | Logic done. Slack not connected, no Google Calendar |
| Should: automated follow-ups | Done |
| Should: per-country channel rules | Partly: business hours and holidays per country. **No rules for which channel is allowed in which country** (email vs LinkedIn vs phone) |
| Stretch: closing small deals | Done, sandbox |

## Not in the Notion plan, but built

- Roles and permissions checked in the tools (admin, rep, junior, finance, viewer, with per-person allow and deny)
- Guardrails: no legal or tax advice, no personal data beyond work contact details, no false claims or fake urgency, no discriminatory targeting, instructions hidden in data are ignored
- 10 test customer types (5 products, 10 ICPs, 4 pricing models) and a 200-brief matrix test
- An upload-ready Fleet zip: `scripts/package_fleet.py` checks `fleet/` and sets the deployed tool server's URL
- `docs/use-cases.md`: every use case mapped to its tests

## Still missing, in order of what blocks the week 3 demo

1. **Deploy the tool server.** The `Dockerfile` is ready (Cloud Run). The deployed server keeps state in a JSON file, which a Cloud Run restart wipes, so a demo that runs for several days needs a database. Then register it in Fleet (Integrations > + Custom MCP, named `sales_tools`, no auth), build with `python3 scripts/package_fleet.py --mcp-url https://<server>/mcp` and import the zip. Without the registered custom MCP the imported agent sees none of our tools. Tested on 22 November through a cloudflared tunnel: "Sales Assistant v2" in Fleet ran `get_setup_status` and added two leads through @lead-search. The server needs `ANTHROPIC_API_KEY` (or the d1 key) in its environment, or every ICP check comes back as needs a person.
2. **Connect Slack:** the `#sales-desk` channel ID, the reps' Slack user IDs, and the agent name in `config.json` (the TODO list at the end of `fleet/AGENTS.md`).
3. **Clay import and the 50-lead test set** from the beachhead (Danish B2B SaaS).
4. **Real research:** the website and blog, and LinkedIn data through Clay.
5. **Link the event log to LangSmith traces**, and a funnel dashboard on top of `get_funnel_metrics`.
6. **One before/after example of the loop**: a proposal from @coach, approved, and the metric moving.
7. **One-pagers:** only JD's is written. Kyriakos, Gyula, Slava and Ayaan's are still the template.
8. **Pitch deck:** problem, solution, value proposition, moat.
9. Nice to have for the demo: Google Calendar invites, a payment provider in test mode, per-country channel rules.

After the course, per the business case: a lawyer's yes or no per channel per country, real sending domains with warmup and bounce monitoring, a disclosure policy for AI-assisted outreach, CRM sync, and Hunter as a second data source.
