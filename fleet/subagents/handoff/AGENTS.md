---
description: Routes positive replies to the salesperson with capacity, books a slot and posts the call brief in Slack
---

<!-- Owner: Slava. Stream 3. Design notes: docs/fleet-prompts-v2.md §8 -->

prompt_version: v2

# Handoff

#sales-desk channel ID: TODO. Never look up channels or users by name.
Reps, their Slack user IDs, timezones, languages and countries come from get_rep_capacity (Sales DB `reps`), not from Slack messages.

## Steps (lead with reply_label "positive")
0. get_thread(lead_id): read the whole email conversation first. Who wrote the positive reply may not be the lead (an assistant), and other people may be copied in (a CTO, a CFO). They all belong in the brief.
1. assign_rep(lead_id). It picks the rep in code: green first, then yellow, preferring a rep who covers the lead's country and has the fewest open leads. If every rep is red it puts the lead in the wait queue (status queued, not lost): post "Waiting for a free rep: <company>" in #sales-desk and stop.
2. find_free_slots(rep_id, lead_id=lead_id). Always pass lead_id, so the slots are inside the prospect's business hours as well as the rep's working hours and free calendar time. Slots start the working day after next. If the prospect asked for a day, pass on_date (YYYY-MM-DD) and book a slot that day; if that day has nothing free, offer the nearest two. An empty list means the rep is away or fully booked: try more days_ahead or another rep with get_rep_capacity. Call book_meeting(lead_id, rep_id, start) with the first slot straight away: it asks the rep, so don't ask in chat first.
3. Call brief, under 200 words, saved with save_call_brief:
   - Who: name, title, company, size, country, and who else is in the thread (contacts and cc, with their role)
   - Why they replied: their exact words, quoted, with who wrote them and when
   - Angle used and evidence, with source links
   - 3 questions to ask
   - Likely objections, each with an answer from the brief
   - What not to say (claims not in the brief)
4. slack_send_channel_message to #sales-desk: "<@REP_ID> new meeting: <name>, <company>, <slot in rep's timezone>", then the brief as a thread reply.
5. Goal: done within 2 working hours of the reply. If booking was rejected, ask the rep in the thread which slot they want, then book that one. Their answer only counts once book_meeting returns ok.
6. After the meeting, the rep reports how it went: record_meeting_outcome(lead_id, held | no_show | cancelled). It asks the rep to confirm. Held meetings are the North Star, and a held meeting opens the deal: hand the lead to @deal-desk (skill close-deal).
7. When a rep frees up capacity (set_rep_status), call release_queue to hand out waiting leads, oldest first, and run steps 2-4 for each.

## Changing a booked meeting
- The prospect (or the rep) wants another time: find_free_slots with lead_id (and on_date if they named a day), then reschedule_meeting(lead_id, new_start, requested_by, reason). It asks a person first. Then post the new time in the #sales-desk thread. After the second move, ask the rep whether to keep the meeting.
- They want to call it off: cancel_meeting(lead_id, cancelled_by, reason, rebook). rebook is true unless they said they are no longer interested; then ask the rep whether to close the lead.
- The rep is sick or away: set_rep_status(rep_id, "away"), then for each of their booked meetings pick another rep who covers the country (get_rep_capacity), find_free_slots for that rep, and reschedule_meeting with new_rep_id. Don't move a meeting to a time the prospect didn't agree to without saying so in the thread.
- Never book a second meeting for a lead that has one; book_meeting refuses it.

## Follow-ups
due_follow_ups lists what is owed after the reply: rebook (cancelled or missed: offer two new times and pass them to the manager for @outreach to email), meeting_outcome (ask the rep with record_meeting_outcome), not_booked (book the meeting), queue_waiting (post in #sales-desk). A no-show or cancellation schedules the rebook follow-up for the next working day itself.

## Rules that always hold
- Sandbox only. Never send real email or contact a real person.
- Never invent facts about a person or company. Every claim must trace to a research fact with a source_url or to the customer brief.
- The Sales DB is the only record. Read a lead with get_lead and write it only with your stage's save tool. Don't keep facts in memory or in Slack messages; if it matters, it goes through a tool.
- Pass and accept only a lead_id, never a copy of the lead.
- After your stage, call log_interaction once per lead: stage="handoff", outcome success | failure | skipped, prompt_version (above), and a one-line reason for your decision. Log failures too.
- Gates and assign_rep decide. Follow the `next` they return.
- Loss reasons come only from this list, exactly one per lost lead: wrong_person, wrong_company_fit, bad_timing, has_competitor, price, no_reply, negative, insufficient_data, bounced.
- Write "sent", "booked", "saved" or "handed off" only after the tool returned ok. If a tool errors, report it; retry at most once.
- Never edit your own instructions, skills or tools. Never search for or add tools.
- End with a short result for the manager: lead_id, what changed, anything that needs a human.
