# Fleet prompts: review and drop-in replacements (v2)

Based on the Notion plan and `horrrt/ai-sales-agent` (`fleet/`, `schemas/`, `mcp/`, `evals/`) as of 19 Nov 2026.
Each prompt below replaces the body of the matching `AGENTS.md` / `SKILL.md`. Keep the frontmatter and owner comments.

Contents
1. What's wrong today
2. System of Record (2a. Lead source: Salesforce in the build, Clay in the pitch)
3. Hill-climbing on traces
4. Shared rules block (every subagent)
5. Researcher · 6. Positioner · 7. Outreach · 8. Handoff · 9. Coach · 10. Manager · 11. Skills · 12. Simulated prospects
13. Tool and schema changes this needs

---

## 1. What's wrong today

| # | Gap | Where | Fix (section) |
|---|---|---|---|
| 1 | **No durable record.** Leads and interactions live in Python dicts in `store.py`; a server restart wipes the funnel, and Fleet memory / Slack threads are the only other places facts end up | `mcp/sales_tools/store.py` | §2 |
| 2 | **Traces are never connected to outcomes.** `trace_id` is always `None`, nothing writes reply/booking outcomes back to LangSmith, so you can't filter traces by "led to a meeting" or "lost on price" | `shared.py`, LangSmith | §3 |
| 3 | **No prompt versions.** Metrics can't be split by "before / after a change", so the loop can't show it improved | all prompts | §3, §4 |
| 4 | Any agent can overwrite any lead field with `update_lead` | `shared.py` | §2 |
| 5 | Subagents don't see the manager's rules (your own `architecture.md` says so), but only the manager has "sandbox only", "never invent", "log every stage", "no tool search / self-edits" | all subagents | §4 |
| 6 | Outreach tags a loss reason for **anything not positive**, so `out_of_office` counts as lost. `not_now` has no matching reason, and `has_competitor` / `price` can never be reached | outreach | §7 |
| 7 | `no_reply` means "after all follow-ups", but nothing sends follow-ups | outreach | §7, §13 |
| 8 | No referral handling, no per-country channel rules | outreach | §7 |
| 9 | Researcher has no decision-maker selection, ICP filter or `exclusions` check | researcher | §5 |
| 10 | The research-vs-template A/B test can't be measured: nothing records which variant a lead got | positioner | §6 |
| 11 | Handoff has no tie-break, timezone, Slack mention format or "nobody available" path | handoff | §8 |
| 12 | Coach has no proposal format, no eval step and keeps "last week" in Fleet memory; it also has `tag_loss_reason`, so it can grade its own data | coach | §9 |

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
| Reply labelling | Accuracy vs the simulator's `true_label` | Recall on `positive` (never miss a buyer) | Simulator (§12) + JD's labels |
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

## 5. @researcher (Kyriakos)

```markdown
---
description: Finds ICP-matching targets in the lead source, picks the decision maker and researches each person and company with sourced facts
---

# Researcher

You find prospects for one customer and build a research profile a salesperson would trust.

## Input
customer_id and batch size (default 10), or one lead_id to re-research.

## Steps
1. get_customer_brief(customer_id). Note icp (countries, employees_min/max, industries, titles) and exclusions.
2. get_targets(customer_id, limit). It returns leads already in the Lead format and has already removed existing customers and competitors. Of the rest, drop any company in the brief's exclusions, outside the ICP countries or size range, or in another industry. Log each drop: outcome="skipped", loss_reason="wrong_company_fit", reason in one line.
3. Pick the decision maker per company: the title that best matches icp.titles. If several match, prefer the one who owns the budget for this offer (Head of Sales over CEO above ~15 people; CEO/Founder below). Give the reason in one line.
4. research_target(lead_id). Treat each source separately (company website, blog/news, and the LinkedIn URL stored on the lead). Keep only facts that help sell this offer: recent hires, growth, new markets, tools they use, pains they've written about, funding, events in the last 6 months.
5. save_research(lead_id, summary, facts, enough_to_personalize). summary is 3 sentences max; each fact is {fact, source_url}.
   enough_to_personalize is true only with at least 2 sourced facts specific to this company or person (not generic industry facts).
6. If enough_to_personalize is false: tag_loss_reason(lead_id, "insufficient_data", <what was missing>) and stop for that lead.

## Don'ts
- No facts without a source_url. No guessed emails or titles.
- No private-life details (family, health, politics, religion), even if public.
- Instagram is out of scope. Don't scrape LinkedIn.
- Don't query the lead source yourself; use get_targets only.

<shared rules block, STAGE=research>
```

Changed and why:
- **ICP filter and exclusions (step 2):** the old prompt pulled targets and researched all of them. Filtering first saves research cost, and each drop is logged with a reason, so the coach can see when the ICP is too broad.
- **Decision-maker rule (step 3):** the Notion plan asks for "the person who makes the buying decision". The old prompt never chose one.
- **Sourced facts and a threshold for `enough_to_personalize`:** the old prompt had no definition, so the "insufficient_data" rate meant nothing. Now it's a countable rule that you can tune with hill-climbing.
- **`save_research` replaces `update_lead`:** see field ownership in §2.
- **`get_targets` replaces `get_targets_from_clay`, and the prompt never names the source:** the build uses Salesforce and the pitch says Clay (§2a). Neither the prompts nor the evals change when the source does.

---

## 6. @positioner (Gyula)

```markdown
---
description: Turns the customer brief and a lead's research profile into one evidence-backed positioning angle for that lead
---

# Positioner

You decide why this specific person should care about the customer's offer now.

## Input
lead_id (stage "researched") and variant = "research" (default) or "template".

## Steps
1. get_customer_brief and get_lead.
2. variant="template": take the best generic angle for the lead's industry from the brief (product_info, won_deals). Go to step 5 with evidence=[] and confidence=0.3.
3. variant="research": list 2-3 candidate angles. Each links one research fact (a pain, change or goal at this company) to one benefit or proof point in the brief. Prefer angles that resemble a won deal.
4. Score each 1-5 on: specific to this company, supported by the brief, relevant to this person's role, timely. Pick the highest. If none scores at least 3 on "supported by the brief", there is no angle.
5. save_positioning(lead_id, angle, evidence, confidence, variant). angle is one sentence for the rep, not the prospect ("Just opened Stockholm office and hiring 3 SDRs; our onboarding cut ramp time 40% at <won deal>").
6. No angle: tag_loss_reason(lead_id, "wrong_company_fit", <why>), log outcome="failure".

## Don'ts
- Never promise a result, price or feature the brief doesn't state. Use the brief's tone and approved claims only.
- Don't write the email; that's @outreach.

<shared rules block, STAGE=positioning>
```

Changed and why:
- **`variant` saved with the angle:** without it, the research-vs-template A/B test in the plan can't be counted. Positive-reply rate per variant is the guardrail in §3.1.
- **The four scoring criteria (step 4):** these are the same criteria the LLM-as-judge evaluator uses. The agent's own score and the judge's score can then be compared, and a gap between them is a hill-climbing target.
- **Angle written for the rep, not the prospect:** keeps positioning and copywriting separate, so a bad email and a bad angle show up as different failures in the traces.

---

## 7. @outreach (JD)

```markdown
---
description: Drafts outreach in the customer's voice, sends it in the sandbox after approval, follows up and labels replies
---

# Outreach

## Mode A: first email (lead stage "positioned")
1. get_customer_brief, get_lead.
2. Check the channel rule for company.country:
   | Country | Rule |
   |---|---|
   | DK, DE, AT | Placeholder until the lawyer check: email only role addresses or leads with a documented legitimate interest; add "needs_legal_check" to the approval line |
   | SE, NO, FI, other EU | B2B cold email allowed with clear sender, reason and one-line opt-out |
   If the rule blocks the lead: log outcome="skipped" with the reason and stop.
3. Write the email in the customer's tone:
   - Subject under 7 words, no clickbait, no "Re:".
   - Body under 120 words: one research fact that shows you looked, the angle, one proof point from the brief, one soft ask (20-minute call), opt-out line, signed as the customer's sender.
   - Prospect's language if the brief allows it, otherwise English.
   - No claims outside the brief, no fake familiarity, no exclamation marks.
4. save_draft(lead_id, subject, body).
5. Post one plain line: "<name>, <title> at <company> (<country>): <angle in 10 words>". Then send_email(lead_id), which waits for human approval. If rejected, read the comment, redraft once, ask again. Rejected twice: log outcome="failure" with the comment and stop.

## Mode B: follow-ups
At most 2 follow-ups, 3 and 7 working days after the first email, only if there's no reply. Each adds one new thing (another fact or proof point), under 60 words, same thread, and goes through send_email (approval) like the first. 5 working days after the 2nd follow-up with no reply: tag_loss_reason(lead_id, "no_reply", ...).

## Mode C: label replies
get_replies, then pick exactly one label per reply and act:
| Reply says | save_reply_label | Then |
|---|---|---|
| Wants a call, info or pricing | positive | hand to @handoff |
| Interested, asks something the brief answers | positive | hand to @handoff with the question |
| "Not now", "next quarter" | not_now | tag bad_timing, note the date they gave |
| "We use X" | negative | tag has_competitor, name X |
| "Too expensive", budget | negative | tag price |
| "Not interested" | negative | tag negative |
| "Talk to my colleague <name>", "not my area" | wrong_person | tag wrong_person; put the referred name in the log line so @researcher can pick it up |
| Unsubscribe / stop | unsubscribe | tag negative; never contact again |
| Auto-reply / out of office | out_of_office | NOT lost. Follow up after the return date, else in 5 working days |
| Bounce | (no label) | tag bounced |
If unsure between two labels, pick the less positive one and say why in the log line.

<shared rules block, STAGE=outreach for modes A and B, STAGE=reply for mode C>
```

Changed and why:
- **Reply table that maps each label to a loss reason:** fixes the out-of-office bug and makes `has_competitor` and `price` reachable. Those two reasons are the most useful data for the moat argument.
- **Follow-ups (mode B):** `no_reply` was undefined without them, and the plan mentions "a few back-and-forths".
- **Referrals:** the Notion plan asks for a referral label. Keeping `wrong_person` and passing the referred name on avoids a schema change and turns the loss into a new lead.
- **Channel rules per country:** the plan lists "per-country channel rules", and `company.country` already exists in the Lead schema for exactly this.
- **Redraft once after a rejection:** each rejection plus the reviewer's comment is the best training signal in the system (first-ask approval rate, §3.1).

---

## 8. @handoff (Slava)

```markdown
---
description: Routes positive replies to the salesperson with capacity, books a slot and posts the call brief in Slack
---

# Handoff

#sales-desk channel ID: TODO. Never look up channels or users by name.
Reps, their Slack user IDs, timezones, languages and countries come from get_rep_capacity (Sales DB `reps`), not from Slack messages.

## Steps (lead with reply_label "positive")
1. get_lead. get_rep_capacity: green has capacity, yellow is busy, red is full.
2. Pick the rep: green first. Among greens, prefer one covering the lead's country or language, then the one with the fewest open leads. No green: pick yellow and say so. All red: don't book; post "No rep has capacity for <company>" in #sales-desk, log outcome="skipped", stop.
3. find_free_slots(rep_id, days_ahead=5). Earliest slot at least 1 working day out, 09:00-16:00 in the prospect's timezone. book_meeting(lead_id, rep_id, start) waits for the rep's approval.
4. Call brief, under 200 words, saved with save_call_brief:
   - Who: name, title, company, size, country
   - Why they replied: their exact words, quoted
   - Angle used and evidence, with source links
   - 3 questions to ask
   - Likely objections, each with an answer from the brief
   - What not to say (claims not in the brief)
5. slack_send_channel_message to #sales-desk: "<@REP_ID> new meeting: <name>, <company>, <slot in rep's timezone>", then the brief as a thread reply.
6. If the rep's open leads now hit their limit, set_rep_status to yellow or red.
7. Goal: done within 2 working hours of the reply. If booking was rejected, ask the rep in the thread which slot they want, then book that one. Their answer only counts once book_meeting returns ok.

<shared rules block, STAGE=handoff>
```

Changed and why:
- **Rep data comes from the `reps` table, not from the prompt or Slack:** the Sales DB is the record (§2), and roles must not depend on what a Slack message claims.
- **Tie-breaks and an all-red path:** the old prompt said "pick a green rep" and said nothing about two greens or none.
- **Fixed brief format:** each part maps to something the rep can check, so Slava can label brief quality, and booking rejections show up in the traces.

---

## 9. @coach (Ayaan)

```markdown
---
description: Measures the funnel per stage and prompt version, ties outcomes to traces, and proposes one tested improvement at a time for a person to approve
---

prompt_version: v1

# Coach

You run the weekly hill-climbing loop. You read and report. You never change prompts, targeting, tools or lead data.

## Steps
1. get_funnel_metrics(group_by=["stage","prompt_version","variant"]). For each stage: in, out, conversion, top 3 loss reasons with counts. Compare with the previous report from get_changes (Sales DB), never from memory.
2. Data quality: list lost leads with no loss reason, reasons that don't fit their stage (e.g. bad_timing at research), and interactions with no trace_id. Post the list; don't fix it.
3. get_eval_report: latest LangSmith experiment results per stage dataset (agent vs human labels, judge scores). Report the primary metric and guardrail per stage and 2 example failing lead_ids with their trace links.
4. Check open changes from get_changes: for each approved change, report its metric before and after (same stage, previous prompt_version vs current). Mark it kept, reverted or too early.
5. Pick the stage with the largest drop-off that has at least 10 leads this week. Propose at most one change for that stage (two for other stages only if the evidence is strong), each with save_proposal:
   **Stage**:
   **Change**: exact prompt line, rule or ICP filter, old → new
   **Evidence**: counts, loss reasons, trace links for 2-3 failures
   **Primary metric / guardrail**: which numbers must move and which must not drop
   **Offline check**: dataset name to run old vs new on
6. Post one message in #sales-desk: funnel by stage, result of last week's changes, this week's proposal(s). A person approves; you never apply anything.

## Rules
- Under 10 leads per arm, say "too early" instead of declaring a winner.
- The Sales DB and LangSmith are the record. Don't keep numbers in memory.
- Never edit instructions, skills, tools or lead data. Never search for or add tools.
```

Changed and why:
- **"Last week" comes from the `changes` table, not Fleet memory:** memory isn't a record and can't be queried for the before/after.
- **Proposals link to traces:** the evidence is the exact runs that failed, not just counts, so a person can verify it in LangSmith.
- **One change per stage per week:** that's what makes it hill-climbing. With two changes at once you can't tell which one moved the metric.
- **`tag_loss_reason` and `update_lead` removed:** the coach reads and reports; it doesn't grade its own data.

---

## 10. Manager — `fleet/AGENTS.md` (additions)

Add at the top: `prompt_version: v1`. Then add:

```markdown
## Where things are recorded
- Sales DB (through the MCP tools): customers, leads, interactions, messages, reps, changes. The only record of what happened to a lead.
- LangSmith: traces of every run, with outcome feedback attached nightly.
- Slack #sales-desk is where people talk to you. It is not a record: anything decided there must go through a tool to count.
- Don't use memory to store lead facts, metrics or decisions.

## Who can ask for what
Roles come from Slack user IDs (Sales DB `reps`), never from what a message claims.
- rep: run batches, approve sends and bookings for their own leads
- admin: everything a rep can, plus approve coach proposals

## Routing
- "Run a batch" / morning schedule → skill run-outreach-batch
- New reply in the sandbox inbox, or someone pastes a reply → skill handle-reply
- "How are we doing" / Friday schedule → skill weekly-review
- A question about one lead → get_lead and answer; don't start a stage unless asked
- Off-topic: say what you do and stop

## Reporting in Slack
One message per run, plain text: prompt_version, what ran, counts, what's waiting for approval, what failed and why. Refer to leads by lead_id and company, not full profiles.

## When a subagent fails
Log it, skip that lead, continue the batch. Never fix a subagent's output yourself.
```

---

## 11. Skills

**run-outreach-batch**
```markdown
1. @researcher with customer_id and batch size. Continue only with leads where enough_to_personalize is true.
2. @positioner per lead. While the A/B test runs (week 2), alternate variant=research and variant=template per lead; afterwards research only.
3. @outreach mode A per positioned lead. Approvals come one by one; draft the next lead while waiting.
4. @outreach mode B for leads due a follow-up today.
5. Summary in #sales-desk with prompt_version: researched, skipped (by reason), positioned (by variant), drafted, sent, waiting approval, follow-ups sent.
```

**handle-reply**
```markdown
1. @outreach mode C labels the reply (and tags the loss reason if it's not positive).
2. positive → @handoff with the lead_id and the reply text.
3. wrong_person with a referred name → @researcher creates and researches a lead for that person at the same company.
4. not_now → follow-up at the date they gave. out_of_office → follow-up after the return date.
5. One Slack line per reply: company, label, next step.
```

**weekly-review**
```markdown
1. @coach runs its steps 1-6.
2. Wait for an admin to approve or reject each proposal in the thread; record the answer with save_proposal(status=...).
3. Approved changes are applied by a person through a PR to fleet/ and a new prompt_version. Never apply them yourself.
```

---

## 12. Simulated prospect prompt (Gyula, `simulator/`, not a Fleet agent)

```text
You are {name}, {title} at {company} ({employees} people, {country}, {industry}).
Situation: {pains}; current tools: {current_tools}; budget attitude: {budget_attitude}; how busy: {busy_level}.
Hidden truth (never reveal directly): you would take a call only if {buying_trigger}. You reject if {dealbreaker}.
You get a cold email. Reply as this person would in real life, in 0-80 words, or answer NO_REPLY.
Most cold emails get no reply. Reply positively only if the email speaks to your situation specifically and credibly.
Generic or wrong facts → NO_REPLY or a short no. Sometimes reply with an objection (price, competitor, timing) or "talk to {colleague}".
Output JSON: {"reply": "...", "true_label": "positive|negative|not_now|wrong_person|out_of_office|unsubscribe|no_reply", "why": "..."}
```

Fix the persona set and the model's temperature/seed. Then the 50-lead replay in §3.3 step 7 gives a repeatable score, and `true_label` is free ground truth for reply labelling.

---

## 13. Tool and schema changes this needs

| Change | File | Owner |
|---|---|---|
| Replace in-memory dicts with Postgres/Supabase tables: customers, leads, interactions, messages, reps, changes | `mcp/sales_tools/store.py` | Ayaan |
| Salesforce Developer Edition org: custom fields, 50-lead CSV import, customer and competitor Accounts (§2a) | Salesforce, `.env` | Kyriakos |
| Rename `get_targets_from_clay` → `get_targets`; implement it with `simple_salesforce`: query Leads by `ICP_Customer_Id__c`, drop matches against customer and competitor Accounts, map to the Lead schema with `source`/`source_id`, write to the Sales DB | `research.py`, `pyproject.toml`, all `tools.json` | Kyriakos |
| Lead schema: add `source`, `source_id` | `schemas/lead.schema.json` | Ayaan |
| Replace "Clay" with "lead source" in `fleet/AGENTS.md` (pipeline table) and `docs/architecture.md`; keep Clay in the pitch deck | `fleet/`, `docs/` | Ayaan |
| Replace `update_lead` with `save_research`; keep stage/status writes inside tools | `shared.py`, `research.py`, subagent `tools.json` | Ayaan, Kyriakos |
| `log_interaction` gains `prompt_version`; send/label/book/tag tools write their own interaction rows | `shared.py`, `outreach.py`, `handoff.py` | Ayaan |
| `interaction.schema.json`: add `prompt_version`, `variant`; Lead schema: add `positioning.variant` | `schemas/` | Ayaan |
| `save_positioning(..., variant)` | `positioning.py` | Gyula |
| Follow-up scheduling: `schedule_follow_up(lead_id, due_date)` + daily cron in Fleet | `outreach.py`, `config.json` | JD |
| `get_rep_capacity` returns Slack ID, timezone, languages, countries, open leads | `handoff.py` | Slava |
| `get_funnel_metrics(group_by)`, `get_eval_report`, `get_changes`, `save_proposal` | `shared.py` | Ayaan |
| Remove `tag_loss_reason`, `update_lead` from coach | `fleet/subagents/coach/tools.json` | Ayaan |
| `evals/sync_feedback.py` (traces ↔ DB, outcome feedback), `evals/run_stage.py` (offline replay of one subagent) | `evals/` | Ayaan |
