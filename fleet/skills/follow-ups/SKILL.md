---
name: follow-ups
description: Every weekday, work through everything due_follow_ups lists, so no lead is left without a next step
---

# Follow-ups

Every follow-up the pipeline owes shows up in `due_follow_ups`, each with a `kind` and a `next`. Work through all of them, then report what you did and what is waiting for a person.

1. @handoff: `due_follow_ups`. It lists every kind, so read the whole list before acting.
2. By kind:
   - `email`: @outreach writes the follow-up (same thread, same cc) and it goes through the rep's approval.
   - `no_reply`: already marked lost by the tool. Report it; nothing to send.
   - `rebook` (a meeting was cancelled or missed): @handoff gets two new times with `find_free_slots` (pass `lead_id`), then @outreach emails them in the thread. @handoff books when the prospect answers.
   - `meeting_outcome`: ask the rep in #sales-desk how the meeting went. Record it with `record_meeting_outcome` only once the rep answers; never guess.
   - `not_booked`: the prospect already said yes, so @handoff books the first free slot (book_meeting asks the rep), as for any positive reply. The invite lets the prospect accept or ask for another time.
   - `queue_waiting`: @handoff posts in #sales-desk that a positive reply is waiting for a rep.
   - `needs_person`: post the reason in #sales-desk and ask; don't decide it yourself.
   - `quiet_deal`: @deal-desk nudges the deal's rep.
   - `offer_unpaid`, `payment_failed`: @deal-desk `send_offer_reminder` (the checkout link again, in the thread).
   - `offer_expired`: @handoff `assign_rep`; the deal goes to a rep, with the offer in the call brief.
3. Report per lead: the kind, what was done, and anything waiting for a person. If `due_follow_ups` is empty, say so in one line.
