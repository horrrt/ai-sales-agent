# Fleet prompts v2: System of Record, hill-climbing and prompt changes

Based on the Notion plan and `horrrt/ai-sales-agent` (`fleet/`, `schemas/`, `mcp/`, `evals/`) as of 19 Nov 2026.
Written by Ayaan in PR #1. The prompts themselves live in `fleet/` (section 5 says which parts are applied); this doc keeps the reasoning. Section 6 lists the tool and schema changes still needed.

Contents
1. What's wrong today
2. System of Record (2a. Lead source: Salesforce in the build, Clay in the pitch)
3. Hill-climbing on traces
4. Shared rules block (every subagent)
5. Where each prompt lives, and what's applied
6. Tool and schema changes this needs

---

## 1. What's wrong today

| # | Gap | Where | Fix (section) |
|---|---|---|---|
| 1 | **No durable record.** Leads and interactions live in Python dicts in `store.py`; a server restart wipes the funnel, and Fleet memory / Slack threads are the only other places facts end up | `mcp/sales_tools/store.py` | §2 |
| 2 | **Traces are never connected to outcomes.** `trace_id` is always `None`, nothing writes reply/booking outcomes back to LangSmith, so you can't filter traces by "led to a meeting" or "lost on price" | `shared.py`, LangSmith | §3 |
| 3 | **No prompt versions.** Metrics can't be split by "before / after a change", so the loop can't show it improved | all prompts | §3, §4 |
| 4 | Any agent can overwrite any lead field with `update_lead` | `shared.py` | §2 |
| 5 | Subagents don't see the manager's rules (your own `architecture.md` says so), but only the manager has "sandbox only", "never invent", "log every stage", "no tool search / self-edits" | all subagents | §4 |
| 6 | Outreach tags a loss reason for **anything not positive**, so `out_of_office` counts as lost. `not_now` has no matching reason, and `has_competitor` / `price` can never be reached | outreach | §5 |
| 7 | `no_reply` means "after all follow-ups", but nothing sends follow-ups | outreach | §5, §6 |
| 8 | No referral handling, no per-country channel rules | outreach | §5 |
| 9 | Researcher has no decision-maker selection, ICP filter or `exclusions` check | researcher | §5 |
| 10 | The research-vs-template A/B test can't be measured: nothing records which variant a lead got | positioner | §5 |
| 11 | Handoff has no tie-break, timezone, Slack mention format or "nobody available" path | handoff | §5 |
| 12 | Coach has no proposal format, no eval step and keeps "last week" in Fleet memory; it also has `tag_loss_reason`, so it can grade its own data | coach | §5 |

---

## 2. System of Record

**Principle: every fact has exactly one home. Agents read and write facts only through MCP tools. Slack, Fleet memory and the LLM's context are never the record.**

| Data | System of Record | Who writes | Notes |
|---|---|---|---|
| Customer brief (offer, ICP, product info, tone, exclusions) | **Sales DB** `customers` | Humans only (seed file / admin) | Read-only to agents via `get_customer_brief` |
| Leads (person, company, research, positioning, outreach, handoff, stage, status, loss_reason) | **Sales DB** `leads` | Only the owning stage's tool (table below) | The lead source (Salesforce in the build, Clay in the pitch) is a *source*, not the record: `get_targets` copies into `leads` (§2a) |
| Interactions (business events per lead) | **Sales DB** `interactions`, append-only | Tools log facts automatically; agents log decisions with `log_interaction` | Never updated or deleted |
| Emails and replies | Sandbox mailbox, mirrored to **Sales DB** `messages` | `send_email`, `get_replies` | Holds the exact text that was sent and approved |
| Meetings | **Calendar** owns the slot; `leads.handoff` stores the event ID | `book_meeting` | Calendar wins if they disagree |
| Rep capacity (traffic light) | **Sales DB** `reps` | `set_rep_status`, reps themselves | Slack user ID is the rep's key |
| Proposed / approved changes and their results | **Sales DB** `changes` | Coach proposes (`save_proposal`), humans approve | Replaces Fleet memory for the coach |
| What the agent did (prompts, tool calls, tokens, latency) | **LangSmith** project `sales-agent` | Fleet, automatically | Joined to the DB by `lead_id` + `prompt_version` (§3) |
| Human labels, outcome feedback, eval results | **LangSmith** (feedback on runs, datasets, experiments) | Owners, sync script | Datasets seeded from `evals/datasets/*.csv` |
| Agent instructions and tools | **Git** `fleet/`, each release tagged `prompt-vN` | PR only | Fleet is a deployment of git, never edited in place |
| Slack `#sales-desk` | **Interface, not a record** | | Anything decided in Slack (approval, rep's slot choice) is written back through a tool |

**Field ownership in `leads`** (enforced by replacing the generic `update_lead` with one save tool per stage):

| Fields | Written only by | Tool |
|---|---|---|
| person, company, source, source_id | researcher | `get_targets`, `save_research` |
| research.* | researcher | `save_research` |
| positioning.* (incl. variant) | positioner | `save_positioning` |
| outreach.* | outreach | `save_draft`, `send_email`, `save_reply_label` |
| handoff.* | handoff | `book_meeting`, `save_call_brief` |
| stage | the tool of each stage, never the agent | (set inside the tools) |
| status, loss_reason | `tag_loss_reason` only | |

**Storage choice:** Postgres (Supabase free tier) behind the deployed MCP server, so all five of you and the coach's dashboard read the same data. SQLite only works if the MCP server has a persistent disk and nobody else needs to query it.

**Facts by code, judgements by the agent.** `send_email`, `save_reply_label`, `book_meeting` and `tag_loss_reason` write their own interaction rows, so the log doesn't depend on the LLM remembering. `log_interaction` is for the agent's *decision and reason* ("skipped: competitor in exclusions").

### 2a. Lead source: Salesforce in the build, Clay in the pitch

For the submission the agent pulls targets from a **Salesforce Developer Edition org** (free) instead of Clay. Pitch line: *"In production targets come from Clay. For the build we used Salesforce because it was faster to integrate. The source is one adapter, so switching back is one tool."*

**How it's wired: an adapter tool in our MCP server, not Fleet's Salesforce connector.**

| | Adapter tool `get_targets` (recommended) | Fleet's native Salesforce connector |
|---|---|---|
| Code | ~50 lines in `research.py` (e.g. `simple_salesforce`) | None |
| Output | Already in Lead-schema shape, written to the Sales DB by code | Raw Salesforce records; the LLM has to copy fields into the DB |
| Determinism | Same query, same leads every run (needed for the replay in §3.3) | The agent writes its own queries, so runs can differ |
| Switching to Clay later | Replace the function body; prompts, schema and evals stay the same | Rewrite the researcher prompt and tools |
| Writes to Salesforce | Not needed | Not possible (read-only) |

The native connector is the fallback if the adapter isn't ready by week 3. Then copying fields into the DB becomes an LLM step that you also have to trace and evaluate.

**Setting up the org (one person, about half a day):**
1. Sign up for a Salesforce Developer Edition org. Create an integration user or connected app for the MCP server. Keep the credentials (`SF_USERNAME`, `SF_PASSWORD`, `SF_TOKEN`, or an OAuth client ID and secret) in `.env`, never in git.
2. Add custom fields on Lead: `LinkedIn_URL__c` and `ICP_Customer_Id__c` (which customer the lead belongs to). Standard fields cover the rest.
3. Import the 50-lead test set from a CSV with the Data Import Wizard, with `LeadSource = "Clay export"`. The leads can still come from a Clay export or the sample data; only the integration changes. Use sandbox email addresses so nothing real can be emailed.
4. Add existing customers and competitors as Accounts with `Type = Customer` or `Competitor`. `get_targets` drops leads whose company matches one, so exclusions come from CRM data instead of a hand-written list.

**Field mapping (inside `get_targets`):**

| Salesforce Lead | Lead schema |
|---|---|
| Id | `source_id`, with `source = "salesforce"` |
| FirstName + LastName, Title, Email, LinkedIn_URL__c | `person.name`, `.title`, `.email`, `.linkedin_url` |
| Company, Website, Country, NumberOfEmployees, Industry | `company.name`, `.domain`, `.country` (ISO-2), `.employees`, `.industry` |

**What Clay did that Salesforce doesn't, and how the build covers it:**
- **Finding new companies:** done once, by hand, when you import the 50 leads. That's fine for a fixed test set.
- **Enrichment (LinkedIn data, tech stack, headcount growth):** `research_target` uses the company website, blog and news instead. LinkedIn data is limited to the URL stored on the lead; don't scrape LinkedIn.

---

## 3. Hill-climbing on traces

Hill-climbing = change **one** thing, measure it against a fixed baseline, keep it only if the score goes up without hurting anything else, repeat. Traces are what make each step concrete.

### 3.1 Signals per stage

| Stage | Primary metric (climb this) | Guardrail (must not drop) | Where the label comes from |
|---|---|---|---|
| Research | Sourced-fact precision (fact true and on the source page) | % leads with enough_to_personalize | Kyriakos labels traces in a LangSmith annotation queue |
| Positioning | Judge score (specific, supported, relevant, timely; 1-5) agreeing with Gyula's labels | Positive-reply rate per variant | LLM-as-judge evaluator + Gyula's labels |
| Outreach drafts | **First-ask approval rate** (every rejection in Fleet's approval inbox is a free label) | Positive-reply rate | Approvals/rejections + reviewer comment, logged by `send_email` |
| Reply labelling | Accuracy vs the simulator's `true_label` | Recall on `positive` (never miss a buyer) | Simulator (`simulator/`) + JD's labels |
| Handoff | Routing accuracy vs Slava's labels | Time reply→booked < 2 working hours | `routing.csv` + timestamps in `interactions` |
| **North star** | **Meetings booked per 100 leads contacted** on the fixed test set | Cost per lead (tokens) from LangSmith | End-to-end replay (3.3) |

### 3.2 Connecting traces to outcomes

1. **Version tag on every run.** Each `AGENTS.md` starts with `prompt_version: vN` (the git tag). Agents pass it to `log_interaction`, and each skill posts it in the Slack summary. That's how metrics split into before/after.
2. **Join key = `lead_id`.** Every tool call in a Fleet trace has `lead_id` in its inputs. A nightly script `evals/sync_feedback.py`:
   - lists runs in LangSmith project `sales-agent` since the last sync,
   - finds the root run for each `lead_id` + stage, writes its ID into `interactions.trace_id`,
   - reads outcomes from the Sales DB and attaches them as **feedback on that root run**: `reply_label`, `meeting_booked` (0/1), `loss_reason`, `approved_first_time` (0/1), `prompt_version`.
3. Now in LangSmith you can filter "positioning runs, prompt v3, loss_reason=wrong_company_fit" and open the exact reasoning that produced them.

### 3.3 The loop (weekly, owned by Ayaan, run by the coach + script)

1. **Collect.** Fleet traces + outcome feedback (3.2). Human approvals/rejections already sit in the traces.
2. **Find the worst stage.** The coach ranks stages by drop-off and loss reason. Ayaan filters traces with bad feedback for that stage.
3. **Add failures to the dataset.** Push the failing traces into the stage's LangSmith dataset (annotation queue → owner labels the expected output). The dataset only grows; nothing is removed.
4. **Propose one change.** One prompt line, one rule or one ICP filter. Old → new, written in the `changes` table.
5. **Offline eval.** Run the stage prompt old vs new on the **whole** stage dataset (not just the new failures) with `evaluate()` and the evaluators in `evals/evaluators/`. Fleet subagents aren't called directly here: `evals/run_stage.py` replays the subagent's `AGENTS.md` as the system prompt with that stage's tools stubbed from the dataset row.
6. **Accept rule.** Keep the change only if the primary metric goes up and no guardrail drops more than 2 points. Otherwise revert and record why.
7. **End-to-end check.** Replay the 50-lead test set through the whole Fleet agent against the simulated prospects (fixed personas, fixed seed) and compare the north star with the previous version.
8. **Ship.** PR to `fleet/`, tag `prompt-vN+1`, re-import into Fleet, update the `changes` row with the result. This row is the before/after you show at the exam.

Two-week budget: one full loop per week from week 2. Expect 2-3 accepted changes by 6 Dec; one clear before/after is enough for the pitch.

---

## 4. Shared rules block (paste into every subagent)

```markdown
prompt_version: v1

## Rules that always hold
- Sandbox only. Never send real email or contact a real person.
- Never invent facts about a person or company. Every claim must trace to a research fact with a source_url or to the customer brief.
- The Sales DB is the only record. Read a lead with get_lead and write it only with your stage's save tool. Don't keep facts in memory or in Slack messages; if it matters, it goes through a tool.
- Pass and accept only a lead_id, never a copy of the lead.
- After your stage, call log_interaction once per lead: stage="<STAGE>", outcome success | failure | skipped, prompt_version (above), and a one-line reason for your decision. Log failures too.
- Loss reasons come only from this list, exactly one per lost lead: wrong_person, wrong_company_fit, bad_timing, has_competitor, price, no_reply, negative, insufficient_data, bounced.
- Write "sent", "booked", "saved" or "handed off" only after the tool returned ok. If a tool errors, report it; retry at most once.
- Never edit your own instructions, skills or tools. Never search for or add tools.
- End with a short result for the manager: lead_id, what changed, anything that needs a human.
```

Why: subagents don't inherit the manager's prompt, so without this block each subagent can break the rules the manager promises. `prompt_version` is what lets every trace and DB row be split by version (§3.2).

---

## 5. Where each prompt lives, and what's applied

The prompt text lives only in `fleet/`, so this doc can't drift from what Fleet runs.

| Prompt | File | State |
|---|---|---|
| Manager | `fleet/AGENTS.md` | Applied: where things are recorded, roles, routing, Slack reporting, subagent failures, `prompt_version` |
| Researcher | `fleet/subagents/researcher/AGENTS.md` | Applied, adapted to today's tools: `get_targets_from_clay` instead of `get_targets`, and `research_target` saves the profile itself (no `save_research` yet) |
| Positioner | `fleet/subagents/positioner/AGENTS.md` | Gyula's prompt (the one the positioning evals run on), plus `prompt_version` and `variant` |
| Outreach | `fleet/subagents/outreach/AGENTS.md` | JD's prompt from PR #2, plus `prompt_version` and schema-valid logging. Not applied from PR #1: follow-up mode B, the country channel rules and the loss-reason table for replies. Those need a team decision, because PR #2 deliberately doesn't tag most replies as lost |
| Handoff | `fleet/subagents/handoff/AGENTS.md` | Applied: rep choice and tie-breaks, a slot at least one working day out, call brief format, the all-red path |
| Coach | `fleet/subagents/coach/AGENTS.md` | Rules and `prompt_version` only. The full weekly loop needs `get_changes`, `get_eval_report` and `save_proposal` |
| Skills | `fleet/skills/` | Not applied yet. PR #1's skill steps refer to outreach modes A/B/C and to creating leads for referrals, which don't exist on main |
| Simulated prospects | `simulator/simulate.py` | Built (Gyula): 12 personas with a private half, plugged into `get_replies` with `SALES_LEADS=personas` |

---

## 6. Tool and schema changes this needs

| Change | File | Owner |
|---|---|---|
| Replace in-memory dicts with Postgres/Supabase tables: customers, leads, interactions, messages, reps, changes | `mcp/sales_tools/store.py` | Ayaan |
| Salesforce Developer Edition org: custom fields, 50-lead CSV import, customer and competitor Accounts (§2a) | Salesforce, `.env` | Kyriakos |
| Rename `get_targets_from_clay` → `get_targets`; implement it with `simple_salesforce`: query Leads by `ICP_Customer_Id__c`, drop matches against customer and competitor Accounts, map to the Lead schema with `source`/`source_id`, write to the Sales DB | `research.py`, `pyproject.toml`, all `tools.json` | Kyriakos |
| ~~Lead schema: add `source`, `source_id`~~ done | `schemas/lead.schema.json` | Ayaan |
| Replace "Clay" with "lead source" in `fleet/AGENTS.md` (pipeline table) and `docs/architecture.md`; keep Clay in the pitch deck | `fleet/`, `docs/` | Ayaan |
| Replace `update_lead` with `save_research`; keep stage/status writes inside tools | `shared.py`, `research.py`, subagent `tools.json` | Ayaan, Kyriakos |
| ~~`log_interaction` gains `prompt_version`~~ done, and it now rejects stage and outcome values outside the schema. Still to do: send/label/book/tag tools write their own interaction rows | `shared.py`, `outreach.py`, `handoff.py` | Ayaan |
| ~~`interaction.schema.json`: add `prompt_version`, `variant`; Lead schema: add `positioning.variant`~~ done | `schemas/` | Ayaan |
| ~~`save_positioning(..., variant)`~~ done | `positioning.py` | Gyula |
| Follow-up scheduling: `schedule_follow_up(lead_id, due_date)` + daily cron in Fleet | `outreach.py`, `config.json` | JD |
| `get_rep_capacity` returns Slack ID, timezone, languages, countries, open leads | `handoff.py` | Slava |
| `get_funnel_metrics(group_by)`, `get_eval_report`, `get_changes`, `save_proposal` | `shared.py` | Ayaan |
| ~~Remove `tag_loss_reason`, `update_lead` from coach~~ done | `fleet/subagents/coach/tools.json` | Ayaan |
| `evals/sync_feedback.py` (traces ↔ DB, outcome feedback), `evals/run_stage.py` (offline replay of one subagent) | `evals/` | Ayaan |
