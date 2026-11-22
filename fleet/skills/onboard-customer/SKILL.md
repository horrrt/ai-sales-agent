---
name: onboard-customer
description: Use when a new customer sets up the agent, or wants to change what it sells, to whom or at what price. Covers product info (from text or their website), pricing and auto-close, and the ICP (company and person).
---

# Onboard a customer

1. @onboarding: check_brief, then go through what's missing with the customer, one section at a time:
   product and offer (import_website if they give a site), pricing (review_pricing before saving), ICP
   (suggest_icp_from_deals if they have results, otherwise their best customers), sender and tone.
2. Every save goes through update_brief, which asks the customer's admin.
3. When check_brief says ready: post the five-line summary, and say the weekly lead search can start.
   Don't run find-leads in the same turn unless they ask.
4. While check_brief isn't ready, don't run find-leads or an outreach batch: say what's missing instead.
