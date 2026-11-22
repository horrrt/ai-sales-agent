---
name: find-leads
description: Use on the weekly schedule or when someone asks for new leads. Searches for candidates that fit the customer's ICP and adds the ones that fit to the lead sheet.
---

<!-- Owner: Kyriakos. Stage 1 of the pipeline diagram. -->

# Find new leads

## Inputs
- Customer ID
- How many candidates to look at (default 20)

## Steps
0. check_brief. If it isn't ready, stop and say what's missing (skill onboard-customer fixes it).
1. @lead-search with the customer ID and the number. It runs find_leads, then add_lead for each candidate (the ICP check and the duplicate check happen inside add_lead).
2. Post one line in #sales-desk: found, added, skipped as not a fit, duplicates, and any that need a person to decide.
