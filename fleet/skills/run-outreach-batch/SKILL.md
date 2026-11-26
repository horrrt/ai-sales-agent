---
name: run-outreach-batch
description: Use when a salesperson asks for a batch or the morning schedule runs. Takes the next leads from the lead sheet through research, positioning and a draft, gets the rep's approval, sends, and sends any follow-ups that are due.
---

<!-- Owner: Ayaan (integration), with Kyriakos, Gyula and JD for their stages. Stages 2-4 of the pipeline diagram. -->

# Run an outreach batch

## Inputs
- Customer ID
- Batch size (default 5)

## Steps, one lead at a time
1. @researcher: next_lead, research_target, research_website_visual (homepage screenshot), check_ready_to_write. Not ready after 2 retries: the lead is on hold; take the next one.
2. @positioner: one angle (save_positioning). No angle: the positioner tags the lead and you take the next one.
3. @outreach: save_draft, then record_approval. The rep sees the whole email and approves, edits or rejects it. Approved: send_email. Rejected: one redraft. While a rep decides, work on the next lead.
4. Repeat until the batch size is reached or next_lead returns no lead.
5. @outreach: due_follow_ups, and send each one through the same approval.

## Output
One message in #sales-desk with prompt_version: researched, on hold, positioned, drafted, approved, edited, rejected, sent, follow-ups sent, and anything waiting for a person.
