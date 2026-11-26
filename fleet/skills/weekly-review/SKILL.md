---
name: weekly-review
description: Use on the Friday schedule or when someone asks how outreach is going. Reviews what happened this week, including rep edits and replies, and proposes one rule change for a person to approve.
---

<!-- Owner: Ayaan. The weekly learning loop of the pipeline diagram. Loop design: docs/fleet-prompts-v2.md §3 -->

# Weekly review

## Steps
1. @coach runs its steps: funnel, event log (rep edits, replies, skipped and on-hold leads), last changes, and at most one proposal saved with save_proposal.
2. Post the summary and the proposal in #sales-desk. Before an admin decides, a person runs the offline check on it (evals/traces_to_cases.py, then evals/hill_climb.py --edit-old/--edit-new --trace-cases) and adds the before/after score to the thread. An admin approves or rejects it; record the answer with decide_proposal (it asks the admin to confirm).
3. Approved changes become a new SKILL.md or prompt line in a PR to fleet/, with a new prompt_version. Never apply them yourself.
4. If a rep freed up capacity this week, @handoff: release_queue.
