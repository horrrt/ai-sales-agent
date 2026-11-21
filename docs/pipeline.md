# Pipeline

How the agent runs a lead from search to a closed deal or a booked meeting, box by box from the team's pipeline diagram. Purple boxes are LLM agents (Fleet subagents), green boxes are yes/no checks (tools that call d1), orange boxes are people.

| Diagram box | Built as | File |
|---|---|---|
| **1. Find leads** | | |
| Lead search agent (weekly, by ICP) | @lead-search, skill `find-leads` | `fleet/subagents/lead-search/` |
| d1: fits our ICP? | `add_lead` | `mcp/sales_tools/tools/research.py` |
| Skipped (not a fit) | lead saved with status lost, reason `wrong_company_fit` | same |
| Add to lead sheet, skips duplicates | `add_lead` checks email and domain | same |
| **2. Research** | | |
| Manager picks next lead (daily) | `next_lead`, skill `run-outreach-batch` | same |
| Research the company | @researcher, `research_target` (saves the profile on the lead) | `fleet/subagents/researcher/` |
| d1: enough to write? (yes/no per required fact) | `check_ready_to_write`: decision maker, trigger, specific fact | `research.py` |
| Missing info: retry, max 2 / On hold | attempts counted in `research.attempts`, then status `on_hold` (not lost) | same |
| **3. Write** | | |
| Find the angle | @positioner, `save_positioning` | `fleet/subagents/positioner/` |
| Write the email | @outreach, `save_draft` | `fleet/subagents/outreach/` |
| **4. Human check** | | |
| Rep approves, edits or rejects | `record_approval` (Fleet asks a person and shows the whole email). `send_email` refuses any draft without an approval for that exact text | `mcp/sales_tools/tools/outreach.py` |
| Rejected: redraft | the approval note goes back to @outreach | same |
| **5. Reply and hand-off** | | |
| Send, d1 reads the reply | `send_email`, `get_replies`, `read_reply` (one label, then its exit) | same |
| Other replies: stop or follow up | not_now and out_of_office: follow-up date, not lost. negative: lost with has_competitor, price or negative. wrong_person: lost. unsubscribe: lost, never contacted again | same |
| d1: small, standard deal? | `check_small_deal` (only if the brief's `auto_close.enabled`) | `mcp/sales_tools/tools/close.py` |
| Auto-close agent: offer + checkout link | @closer, `send_offer` (sandbox link) | `fleet/subagents/closer/` |
| d1: deal done? | `check_deal_done` on the payment status (`record_payment`, by a person for now) | `close.py` |
| Closed automatically, rep gets a summary | status won, Slack message from @closer | |
| Hand to a free rep (green first, then yellow) | `assign_rep`, then `find_free_slots`, `book_meeting`, `save_call_brief` | `mcp/sales_tools/tools/handoff.py` |
| Wait queue if all reps red | status `queued` (not lost), `release_queue` when a rep has room | same |
| **Weekly learning loop** | | |
| What happened: edits, replies | every gate and tool writes the event log; rep edits keep the original and the edited email | `mcp/sales_tools/store.py` |
| Event log | `get_event_log` (a Google Sheet export is not built yet) | `shared.py` |
| Weekly review finds patterns | @coach, skill `weekly-review` | `fleet/subagents/coach/` |
| Agent rules: new SKILL.md | `save_proposal`, then a person approves with `decide_proposal`; the change goes in as a PR with a new `prompt_version` | `shared.py` |

Additions the diagram doesn't show but the Notion plan needs: a booked slot and a call brief for every hand-off, one loss reason on every lost lead, sandbox-only sending, follow-ups (2, then `no_reply`), and a person approving every rule change.

## The d1 checks

`mcp/sales_tools/decide.py` asks each yes/no or multiple-choice question:

1. **Liquid d1** if `LIQUID_API_KEY` is set (model `d1.2`; plain `d1` is deprecated). A key comes from console.liquid.ai; new organisations get $2 of free credit, and d1.2 costs $0.02 per million input tokens.
2. **Claude Haiku** if `ANTHROPIC_API_KEY` is set, with a strict yes/no or choice answer. A stand-in until the team has a Liquid key, and a baseline to compare d1 against.
3. **Nobody**: the gate returns no answer and the lead goes to a person.

Every gate records which backend answered and how confident it was (d1 only), in the event log under `decision`.

## Not built yet

- Screenshots of the company site for the ICP and research checks (the diagram's VLM step). The checks use the lead's fields and research facts.
- A real lead source (Clay, Salesforce or web search) and real research. Both are stubs, or the simulated prospects with `SALES_LEADS=personas`.
- Google Sheets for the lead sheet and event log. State is in memory, with an optional local JSON snapshot (`SALES_STATE=path`). A deployed server still needs a database.
- A payment provider in test mode. `record_payment` is set by a person.
- Slack approval inside Slack itself. The approval is Fleet's, on `record_approval`; once Slack is connected to Fleet, approvals can be answered there.

## Run it

```bash
cd mcp && uv run --extra dev pytest -q                                  # unit tests, no model calls
SALES_LEADS=personas SALES_STATE=../data/state.json uv run --extra sim python -m sales_tools.server
```
