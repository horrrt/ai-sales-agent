---
name: run-outreach-batch
description: Use when a salesperson asks for a new outreach batch or the morning schedule runs. Finds and researches targets, positions the offer, drafts outreach and sends it after approval.
---

<!-- Owner: Ayaan (integration), with Kyriakos, Gyula and JD for their stages. -->

# Run an outreach batch

## Inputs
- Customer ID (which brief to use)
- Batch size (default 10)

## Steps
1. @researcher with customer_id and batch size. Continue only with leads where enough_to_personalize is true.
2. @positioner per lead. While the A/B test runs (week 2), alternate variant=research and variant=template per lead; afterwards research only.
3. @outreach mode A per positioned lead. Approvals come one by one; draft the next lead while waiting.
4. @outreach mode B for leads due a follow-up today.

## Output
One summary message in #sales-desk with prompt_version: researched, skipped (by reason), positioned (by variant), drafted, sent, waiting approval, follow-ups sent.
