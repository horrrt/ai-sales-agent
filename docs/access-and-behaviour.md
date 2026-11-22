# Who can see and do what, and how the agent behaves

This page covers what the sales agent, our customers' teams and their prospects can reach, and how the agent should act towards each of them. It also sets out the minimum we need before the demo and before a real customer. Most of the rules already exist in two places. The prompt (`fleet/AGENTS.md`, "Rules that always hold" and "What you don't do") tells the agent. The tools (`mcp/sales_tools/access.py` and the checks in each tool) enforce them, so a prompt that goes wrong still can't break them. This page puts both side by side and names the gaps.

## Who's involved

- **The agent.** The Fleet manager and its 8 subagents. It only acts through our MCP tools.
- **The customer's team.** The small B2B company using the agent. Each person in the roster (`reps`) has a role: admin, rep, junior, finance or viewer. They can also have their own `allow` and `deny` lists, and deny always wins.
- **Prospects.** People at the companies we email. They never talk to the agent directly. All they do is reply to an email.
- **Other customers.** Another company using the agent, with its own brief, leads and team.
- **Us.** The five of us building and running it.
- **Outside services.** Anthropic (the models), Liquid d1 (the yes/no gates), LangSmith (traces), Clay (the lead source, still a stub) and Slack.

## What each one can do

| | Agent | Admin | Rep | Junior | Finance | Viewer | Prospect |
|---|---|---|---|---|---|---|---|
| Read leads, pipeline, event log, metrics | Yes | Yes | Yes | Yes | Yes | Yes | No |
| Draft emails and positioning | Yes | | | | | | No |
| Approve, edit or reject an email | No | Yes | Unassigned or own leads | No | No | No | No |
| Send an email | Only after approval | | | | | | No |
| Book, move, cancel own meetings | Proposes | Yes, anyone's | Own | Own | No | No | Asks by reply |
| Approve a quote within the discount limit | No | Yes | Own deals | No | No | No | No |
| Approve a discount above the limit | No | Yes | No | No | No | No | No |
| Record a contract or a won/lost outcome | No | Yes | Own deals | No | No | No | No |
| Record a payment | No | Yes | No | No | Yes | No | No |
| Change the brief (product, ICP, pricing, auto-close) | Proposes | Yes | No | No | No | No | No |
| Approve a coach proposal | No | Yes | No | No | No | No | No |
| Set someone else's status, move a meeting to another rep | No | Yes | No | No | No | No | No |
| Unsubscribe | | | | | | | Yes, by reply |
| Delete or export data, read keys or prompts | No | No tool | No tool | No tool | No tool | No tool | No |

A blank cell means the action doesn't apply to that column. "Own" means the lead's rep or the deal's owner. For anyone else's lead, it needs `any_lead`, which only admins have. A lead has no rep until the handoff, so any rep can approve a first email. After that, only the lead's rep or an admin can. Reads aren't role-checked: anyone on the team can read everything for their own customer.

## How the agent behaves

**Towards the customer's team.** The agent works for them and hands over to a person from the first call onward. It takes who's asking from their identity (`by`, from Slack or the Fleet approval prompt), never from what a message claims. When a tool says `not_allowed`, the agent passes on who can do it. When a gate has no answer, it stops that lead and asks in #sales-desk instead of guessing. It only says "sent", "booked" or "handed off" after the tool returned success. When it refuses something, it says no in one line, says why, and offers what it can do instead.

**Towards prospects.** Every email is approved by a person first. It goes out one by one, at most the daily limit (40 by default, set in the brief), on working days 08:00-17:00 in the prospect's country. It only claims what's in the brief and only states facts that are in the prospect's research. It writes as the sender and nobody else. It doesn't invent deadlines, doesn't talk down competitors, and doesn't target anyone for personal traits. Whatever a prospect writes is data. An instruction inside a reply ("email my Gmail instead", "ignore your rules") changes nothing. An unsubscribe marks the lead do-not-contact for good.

**Towards everyone.** No legal, tax or financial advice. No personal data beyond a work email and title. The agent doesn't reveal its prompt, tools or keys, and doesn't predict things it can't know.

## Minimal requirements

### Before the demo (week 3)

These are all in place and tested (`mcp/tests/test_access_and_autoclose.py`, `test_guardrails.py`, and the Fleet `access` and `guardrails` cases), except the last one:

1. Every action that commits the company checks the person's role in the tool, not just in the prompt.
2. Nothing is sent without a person's approval, and the approval covers the exact recipients.
3. Email only goes to sandbox `.test` addresses.
4. There's a daily sending limit.
5. Do-not-contact can't be overridden, even with an approved draft.
6. No tools exist to delete or export data or read secrets.
7. Every approval, edit and rejection is in the event log with who did it, and so is every refusal on a lead.
8. **Slack identity is still missing.** The reps' Slack user IDs are TODO (`fleet/AGENTS.md`), so `by` is whatever name the agent passes. For the demo, the Fleet approval prompt is the real check, which is acceptable for a sandbox run with the five of us.

### Before a real customer

These are open. None of them is built yet:

1. **`by` comes from the platform, not the model.** Map each Slack user ID to a rep in the roster. Then the tools take the identity from the request, so a model that names the wrong person can't approve anything.
2. **One customer per deployment.** The store has no customer ID on its leads, so two customers on one server would see each other's leads and briefs. Until the store separates them, run one tool server per customer.
3. **What prospect data goes to outside services.** Lead data (names, work emails, research) goes to Anthropic, Liquid d1 and LangSmith on every run. Check each one's data terms and keep secrets out of prompts and traces.
4. **A prospect asks to see or delete their data.** Today an unsubscribe stops contact, but there's no way to erase a lead. The minimum is that the agent marks the lead do-not-contact and flags the request to an admin, who handles it by hand. What's legally required is for the lawyer review planned after the course (`docs/status-vs-notion.md`).
5. **Real sending.** Lifting the sandbox needs real sending domains, warmup and bounce monitoring, plus a decision per country on which channels are allowed. That's also on the post-course list.
6. **Telling prospects the email was AI-assisted.** We don't have a disclosure policy yet. It's listed for after the course, but it's a customer-facing promise, so we should decide it before anyone real gets an email.

### Not needed for now

- Role checks on reads. In a team of 1 to 10, everyone seeing the pipeline is normal. Add them if a customer asks for it.
- An admin panel for roles. The roster in the brief and `reps.demo.json` is enough while we set customers up ourselves.
- Our own access to customer data. For now there isn't any beyond the sandbox, because no real customer exists.

## When you change any of this

Change the tool check and the prompt together. The tool is what enforces a rule, and the prompt is what makes the agent explain it properly. Add a unit test or a Fleet case for the new rule, and its row in `docs/use-cases.md`.
