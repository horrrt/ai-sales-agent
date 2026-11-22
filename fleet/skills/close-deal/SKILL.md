---
name: close-deal
description: Use after a first meeting is held, when a rep shares call notes, asks for a quote, or reports a contract or an outcome, and when someone asks how the deal pipeline is going. The rep runs the deal; the agent records, scores BANT, drafts quotes and flags deals that went quiet.
---

<!-- Owner: Slava (close or escalate), payments Ayaan. Stage 6 of the pipeline. -->

# Close a deal

## Steps
1. Meeting held: the rep confirms it with record_meeting_outcome (via @handoff). That opens the deal at qualification.
2. @deal-desk: record_call_notes with what the rep says, then qualify_deal (BANT). Tell the rep any factor that's a clear no.
3. Rep asks for a quote: @deal-desk draft_quote, approve_quote (a person approves; an admin above the discount limit), send_quote.
4. Contract and outcome: @deal-desk record_contract, then record_deal_outcome won (amount) or lost (one loss reason). Both ask a person.
5. Weekly, or when asked: @deal-desk get_pipeline. Post deals by stage, the weighted pipeline and deals with no activity in 7 days, by lead_id and company.
