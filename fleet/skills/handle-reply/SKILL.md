---
name: handle-reply
description: Use when prospects reply to outreach. Reads each reply, applies its exit (stop, follow up, or lost with a reason), and takes positive replies to auto-close or to a free rep with a booked slot and a call brief.
---

<!-- Owner: Slava, with JD for reading replies. Stage 5 of the pipeline diagram. -->

# Handle replies

## Steps
1. @outreach: get_replies, then read_reply for each. Other replies are handled by read_reply itself (follow up later, or lost with a reason).
2. Positive, and read_reply says next is check_small_deal (auto-close is on): @deal-desk. If it closes, done. If it hands back, go to step 3.
3. Positive otherwise: @handoff. assign_rep picks a green rep, then yellow. All red: the lead waits in the queue; say so in #sales-desk.
4. @handoff books a slot (the rep approves it), saves the call brief and tells the rep in Slack.
5. One Slack line per reply: company, label, what happens next.
