---
name: weekly-review
description: Use on the Friday schedule or when someone asks how outreach is going. Summarises the funnel per stage, reports last week's changes and proposes one tested improvement for a person to approve.
---

<!-- Owner: Ayaan. Loop design: docs/fleet-prompts-v2.md §3 -->

# Weekly review

## Steps
1. @coach runs its steps 1-6 (funnel per stage and prompt_version, data quality, eval results, before/after of approved changes, proposal).
2. Wait for an admin to approve or reject each proposal in the thread; record the answer with save_proposal(status=...).
3. Approved changes are applied by a person through a PR to fleet/ and a new prompt_version. Never apply them yourself.
