---
name: handle-reply
description: Use when a prospect replies to outreach. Labels the reply, hands positive replies to a salesperson with a booked slot and a call brief, and tags a loss reason for the rest.
---

<!-- Owner: Slava, with JD for labelling. -->

# Handle a reply

## Steps
1. @outreach mode C labels the reply (and tags the loss reason if it's not positive).
2. positive → @handoff with the lead_id and the reply text.
3. wrong_person with a referred name → @researcher creates and researches a lead for that person at the same company.
4. not_now → follow-up at the date they gave. out_of_office → follow-up after the return date.
5. One Slack line per reply: company, label, next step.
