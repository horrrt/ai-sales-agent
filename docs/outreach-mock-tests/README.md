# Outreach Subagent — Fleet Mock-Test Documentation

**Owner:** JD  
**Branch:** `outreach`  
**Test date:** 2026-11-19  
**Agent instructions:** `fleet/subagents/outreach/AGENTS.md`  
**Test type:** Behavioral mock evaluations using synthetic data in LangSmith Fleet.

> **Evidence boundary:** The observed responses below were provided from Fleet mock conversations. They do not verify actual MCP calls, Slack human approvals, database persistence, email delivery, Liquid d1, or subagent delegation. Do not treat synthetic interactions as operational logs.

## Test Register

| Test | Scenario | Result |
|---|---|---|
| T1 | Complete lead → personalized draft → approval boundary | PASS — behavioral mock, with limitations |
| T2 | Incomplete lead → `insufficient_data` | PASS — behavioral mock |
| T3 | Rejected draft → revision → approval again | PASS — behavioral mock, factual-certainty concern |
| T4 | Five synthetic replies → classification | PARTIAL — 0/5 exact output compliance |
| T4-R1 | Same replies after improving Section 5 | PASS — 5/5 expected classification/escalation decisions |
| INT-1 | Actual MCP calls and approval integration | NOT TESTED |

---

## T1 — Complete Researched Lead

**Input:** `TEST-001`, fictional Anna Jensen, Head of Sales at NordPeak SaaS (SE).

Research indicated a fictional announcement of plans to expand the sales team. Required research, positioning and customer-brief information was supplied.

**Expected:**
- Accept complete information.
- Generate a relevant, personalized email.
- Identify unsupported assumptions.
- Stop before human approval and sending.

**Observed:**

The agent accepted the lead and produced:

**Subject:** Reducing sales admin as NordPeak scales

> Hi Anna,
>
> I noticed NordPeak SaaS has announced plans to expand its sales team. When a team is growing, admin work can quickly take time away from selling and follow-up.
>
> Our sales workflow automation software helps reduce repetitive CRM data entry and manual administration, so sales teams can spend more time on active opportunities and less on routine updates.
>
> Would it be worth a short conversation about whether this could support your team as you scale?
>
> Best,  
> [Name]

The agent identified possible unsupported assumptions concerning Anna's responsibilities, NordPeak's CRM bottlenecks, the timing of the expansion and broader product benefits.

No approval or sending was claimed.

**Result: PASS — behavioral mock only.**

**Limitations:**
- Sender identity unresolved.
- Research reference fictional and unverified.
- No MCP calls.
- No actual approval interrupt.
- The test explicitly instructed the agent not to send.

**Improvement candidate:** Require a configured sender identity and verified evidence before actual sending.

---

## T2 — Insufficient Research

**Input:** `TEST-002`, fictional Lars Nielsen at QuietOps.

Missing or insufficient information:
- `research.enough_to_personalize = false`
- `research.facts = []`
- `positioning.angle = ""`
- `positioning.evidence = []`

**Expected:**
- Reject the lead as insufficient.
- Identify missing information.
- Return the case to the Manager Agent.
- Do not draft, send or mark the lead as lost.

**Observed:**

The agent correctly identified all four insufficient fields and reported the lead was not ready.

It recommended returning the case to the Manager Agent for further research or positioning.

No drafting, sending or tool execution was performed.

**Result: PASS — behavioral mock.**

**Limitation:** No actual database status update or Manager re-delegation occurred. The response described the handoff rather than executing it.

**Improvement candidate:** Standardize the Manager handoff using `lead_id`, `status`, `missing_fields`, `reason` and `suggested_next_stage`.

---

## T3 — Rejected Human Approval

**Input:** The fictional reviewer rejected the original NordPeak draft for being too generic and requested a shorter, more specific email.

**Expected:**
- Accept rejection.
- Revise the draft.
- Request human approval again.
- Do not send.

**Observed:**

The agent accepted the feedback and produced:

**Revised subject:** Reducing sales admin as NordPeak’s team grows

> Hi Anna — as NordPeak adds more people, the sales admin load usually grows fast: updates, follow-ups, handoffs, and notes start taking time away from selling.
>
> We help small teams cut that admin so reps can stay focused as they scale. If it’s useful, I can share a quick example.
>
> Best,  
> [Sender]

The agent explicitly stated that human approval was still required and no tools were executed.

**Result: PASS — workflow behavior, with content-quality concerns.**

**Identified weakness:** The revised draft describes NordPeak as adding employees, whereas the research only established an announced expansion. Revision therefore introduced stronger factual implications.

**Improvement candidate:** During revision, preserve the certainty of the original evidence and avoid adding unsupported prospect-specific claims.

**Retest:** Pending.

---

## T4 — Initial Reply Classification

**Test type:** Fleet behavioral mock.

Fleet explicitly reported that it could not delegate to the Outreach Subagent in this conversation. It applied equivalent classification rules itself.

No tools were executed.

| Reply | Expected output | Actual output | Result |
|---|---|---|---|
| A — Meeting request | `positive` | `interested / meeting_request` | FAIL |
| B — Stop contacting | `unsubscribe` | `not_interested / do_not_contact` | FAIL |
| C — Temporary absence | `out_of_office` | `out_of_office / temporary_pause` | FAIL |
| D — Referral | `wrong_person` | `wrong_contact / referral` | FAIL |
| E — Ambiguous interest | Human review | `not_now / warm_maybe` | FAIL |

**Result: PARTIAL PASS.**

The agent mostly understood the meaning of the replies but failed to use the required schema.

**Exact output compliance: 0/5.**

The invented or combined labels would not be accepted by the existing Python `save_reply_label` tool.

### Root-Cause Hypothesis

The original `AGENTS.md` defined reply categories but did not sufficiently enforce:
- Exact output labels.
- Decision priority.
- Separation between ambiguity and postponement.
- Structured human escalation.

---

## T4-R1 — Controlled Classification Improvement

**Date:** 2026-11-19  
**Component:** Outreach Subagent  
**File modified:** `fleet/subagents/outreach/AGENTS.md`  
**Section modified:** Section 5 — Retrieve and classify replies.

### Intervention

The instructions were revised to require:

1. Exactly one of six permitted stored labels: `positive`, `negative`, `not_now`, `wrong_person`, `out_of_office`, `unsubscribe`.
2. No invented synonyms or combined labels.
3. Priority for explicit unsubscribe requests.
4. Clear separation between genuine postponement and ambiguous interest.
5. `review_required` as a workflow escalation decision, not a stored reply label.
6. Human review for ambiguous replies and explicit downstream responsibilities.

### Retest

The same five fictional replies were evaluated.

| Reply | Observed output | Next component | Result |
|---|---|---|---|
| A | `positive` | Handoff Subagent | PASS |
| B | `unsubscribe` | Manager / suppression handling | PASS |
| C | `out_of_office` | Manager Agent | PASS |
| D | `wrong_person` | Manager Agent | PASS |
| E | `review_required` | Human via Manager Agent | PASS |

The agent correctly stated that these were simulated decisions and no tools had executed.

### Before-and-After Results

| Metric | Before | After |
|---|---|---|
| Correct classification/escalation decisions | 0/5 | 5/5 |
| Valid stored labels after revision | — | 4/4 applicable cases |
| Correct ambiguity escalation after revision | No | Yes |

**Interpretation:**

The instruction update improved the observed behavior on the fixed five-example synthetic dataset.

This demonstrates a controlled **evaluation → improvement → retest** process.

It does not prove autonomous learning, generalization to unseen replies or successful MCP tool execution.

### Remaining Limitations

- Small synthetic sample.
- Same examples reused for retesting.
- No verified subagent delegation.
- No actual `save_reply_label` calls.
- No tested unsubscribe suppression.
- No real Fleet approval execution.

### Next Evaluation

Test unseen replies, verify tool persistence and confirm that ambiguous replies are escalated without storing invalid labels.

---

## Learning-Loop Integration — Ayaan

Our Outreach workstream contributes three types of evidence to the shared improvement framework.

### 1. Operational Interaction Logs

For actual executions, follow `schemas/interaction.schema.json`:

- `timestamp`
- `lead_id`
- `stage`
- `channel`
- `message`
- `outcome`
- `loss_reason`
- `trace_id`

Synthetic mock conversations must not be represented as real operational interactions.

### 2. Evaluation Records

For each test, record:
- Test ID and scenario.
- Input data.
- Expected behavior.
- Observed behavior.
- Pass/fail or rubric score.
- Prompt or code version.
- Fleet trace/conversation link when available.
- Technical limitations.

### 3. Improvement Records

Follow:

**Observed failure → Root-cause hypothesis → Change → Identical-input retest → Before/after comparison → Generalization testing**

The T4/T4-R1 experiment is our first concrete example.

### Proposed Outreach Metrics

| Metric | Description |
|---|---|
| Readiness accuracy | Correct readiness decisions / evaluated cases |
| Draft quality | Human rubric: personalization, factual accuracy, tone and CTA |
| Unsupported-claim rate | Drafts with unsupported claims / evaluated drafts |
| Reply classification accuracy | Correct labels / labeled replies |
| Human review accuracy | Ambiguous replies correctly escalated |
| Approval compliance | Unauthorized sending attempts, target zero |
| Unsubscribe compliance | Suppression violations, target zero |

### Responsibilities

**JD:** Outreach evaluation cases, reply labels, evidence from mock tests, stage-specific improvements.

**Ayaan:** Shared tracing, operational logs, evaluation framework, dashboard, cross-stage metrics and improvement-loop coordination.

### Improvement Process

The current system does not automatically learn from repeated execution.

Instead, outcomes must be logged, evaluated and used to propose changes. Proposed changes must then be implemented and tested before an improvement can be claimed.

---

## Open Integration Tasks

- Replace the placeholder MCP server URL: `https://sales-tools.invalid/mcp`.
- Test actual tool execution through Fleet.
- Verify `get_lead`, `save_draft`, `send_email` and `save_reply_label`.
- Verify actual human approval interrupts.
- Test invalid recipients, incomplete data and repeated sends.
- Verify reply classification on unseen examples.
- Establish persistent unsubscribe suppression.
- Confirm Manager-to-Outreach delegation.
- Integrate Outreach logs with Ayaan's improvement framework.
- Add genuine Fleet traces and commit references when available.
- Complete individual Outreach one-pager and GitHub pull request.

**Current maturity:** Behavioral mock tests completed. Controlled instruction improvement demonstrated. Real integration and production reliability remain unverified.
