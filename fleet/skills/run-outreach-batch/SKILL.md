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
1. @researcher: get targets and research each one
2. @positioner: one positioning angle per researched lead
3. @outreach: draft, then send after approval
4. Log every stage with log_interaction

## Output
One summary message in #sales-desk: leads researched, angles found, emails sent, leads stopped and why.

TODO: fill in details as each stage stops being a stub.
