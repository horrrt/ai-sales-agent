# Outreach Subagent — Fleet Mock-Test Documentation

**Owner:** JD  
**Branch:** `outreach`  
**Started:** 2026-11-19  
**Component:** `fleet/subagents/outreach/AGENTS.md`

## Testing scope

These are **behavioral mock evaluations in LangSmith Fleet using synthetic leads**. They do not demonstrate actual Python MCP executions, approval interrupts, Slack delivery, or Liquid d1 integration. Record only observed results; never invent trace IDs.

| ID | Scenario | Status | Result |
| --- | --- | --- | --- |
| T1 | Complete fictional lead → draft → approval boundary | Completed | PASS — behavioral mock; limitations below |
| T2 | Incomplete research → `insufficient_data` | Pending | Not tested |
| T3 | Rejected approval → no send, revision | Pending | Not tested |
| T4 | Synthetic replies → classification and escalation | Pending | Not tested |
| INT-1 | Custom MCP and approval interrupt integration | Blocked by placeholder server URL | Not tested |

## T1 — Complete researched lead

**Date:** 2026-11-19  
**Test type:** Fleet behavioral mock  
**Lead:** `TEST-001` — fictional Anna Jensen, Head of Sales, NordPeak SaaS (SE)

### Test input

Synthetic contact/company details, `research.enough_to_personalize = true`, a fictional sales-team-expansion announcement, a positioning angle, and a fictional customer brief for sales-workflow automation. The reference `https://example.com/fictional-news` was **not a verified research source**.

### Expected

1. Accept complete data for the mock scenario.
2. Generate a relevant and concise personalized email based on supplied context.
3. Identify unsupported assumptions.
4. Stop at the human-approval boundary without claiming a send or tool success.

### Observed Fleet output

The agent judged the lead ready and identified supplied mandatory fields.

**Subject:** Reducing sales admin as NordPeak scales

**Body:**

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

**Assumptions identified by Fleet:**
- Anna personally owns the sales administration problem.
- NordPeak has CRM data-entry bottlenecks.
- Sales expansion is already underway beyond the fictional announcement.
- The product improves outcomes beyond reducing manual administration.

**Stop condition:** No approval or sending was claimed; the draft was described as awaiting human review in a real workflow.

### Result

**PASS — behavioral mock only.** The draft was relevant and the approval boundary was respected *in the prompted scenario*. This does **not** verify Python input validation, real tool calls or approval enforcement.

### Limitations / improvement candidates

- Sender remains `[Name]`; real sending should require a configured sender identity.
- Source evidence was fictional and not verified.
- The prompt explicitly instructed the agent not to send; this does not independently test Fleet's approval interrupt.
- Actual MCP tool execution and Liquid d1 were not tested.

**Retest:** Pending, after a concrete change.  
**Fleet conversation / trace URL:** Not provided.  
**Prompt revision / commit SHA:** Not recorded.

## T2 — Insufficient research (pending)

**Input:** `TEST-002`; `research.enough_to_personalize = false`; empty `research.facts`, `positioning.angle` and `positioning.evidence`.

**Expected:** Return `insufficient_data` and exact missing fields to Manager; do not draft, send or mark lead lost. Manager decides re-delegation.

**Observed:** Pending.

## T3 — Rejected approval (pending)

**Expected:** No send, no claim of sent status, request revision or human escalation. Actual approval interrupt requires MCP connectivity.

**Observed:** Pending.

## T4 — Reply classification (pending)

**Input:** Synthetic positive, negative, out-of-office, wrong-person and ambiguous replies.

**Expected:** Correct allowed labels; positive to Manager for handoff; ambiguous to human review. No fabricated tool results.

**Observed:** Pending.

## Evidence for Ayaan's improvement loop

Separate three types of records:

1. **Operational interaction log:** `timestamp`, `lead_id`, `stage`, `channel`, `message`, `outcome`, `loss_reason`, `trace_id`, per `schemas/interaction.schema.json`. Do not claim mock outputs are actual system interactions.
2. **Evaluation record:** Test ID, synthetic input, expected result, observed Fleet output, pass/fail, model/prompt version, and trace URL if available.
3. **Improvement record:** Failure → root-cause hypothesis → exact instruction/tool change → retest on **the same examples** → measured before/after comparison → limitations. Running a workflow repeatedly does not automatically improve it.

**Proposed Outreach metrics:** Readiness classification accuracy, unsupported-claim rate, human draft acceptance/edit rate, reply classification accuracy by class, and unauthorized-send count (target zero). No measured aggregate improvement is claimed yet.

## Integration dependencies

- `fleet/subagents/outreach/tools.json` currently references `https://sales-tools.invalid/mcp`.
- MCP execution, real approval interrupts and Slack sending remain unverified.
- Ayaan owns aggregate trace/evaluation infrastructure; JD contributes Outreach evaluation cases, outcome labels and before/after evidence.
