# AI Sales Agent

An AI sales agent that finds, researches and contacts prospects for small B2B companies, and hands interested leads to the right salesperson with a booked meeting and a call brief.
Stretch goal: it closes small deals on its own and only books meetings for the bigger ones.

Group project for 38113 Applied AI for Entrepreneurs (DTU, autumn 2026). Built on LangSmith Fleet.

## Team

| Stream | Owner | Fleet subagent | Tool module | One-pager |
|---|---|---|---|---|
| 1. Targets & research | Kyriakos | `fleet/subagents/researcher` | `mcp/sales_tools/tools/research.py` | `docs/one-pagers/kyriakos-research.md` |
| 1. Positioning + 5. Simulated prospects & pitch | Gyula | `fleet/subagents/positioner` | `mcp/sales_tools/tools/positioning.py`, `simulator/` | `docs/one-pagers/gyula-positioning.md` |
| 2. Outreach | JD | `fleet/subagents/outreach` | `mcp/sales_tools/tools/outreach.py` | `docs/one-pagers/jd-outreach.md` |
| 3. Handoff (Slack + calendar) | Slava | `fleet/subagents/handoff` | `mcp/sales_tools/tools/handoff.py` | `docs/one-pagers/slava-handoff.md` |
| 4. Learning loop + shared skeleton | Ayaan | `fleet/subagents/coach`, `fleet/AGENTS.md` | `mcp/sales_tools/tools/shared.py`, `schemas/`, `evals/` | `docs/one-pagers/ayaan-learning-loop.md` |

## Repo layout

```
fleet/        The agent, in Fleet's export layout. Zip it and import it into Fleet.
  AGENTS.md     Manager agent (system prompt on every run)
  config.json   Name, description, model, schedules
  tools.json    Tools and approval settings (interrupt_config: true = Ask)
  subagents/    One per stream: AGENTS.md + tools.json
  skills/       Repeated procedures: SKILL.md
mcp/          Our custom tools as an MCP server. Every tool is a stub for now.
schemas/      Lead object, interaction log, loss reasons. Every stage reads and writes these.
simulator/    Simulated prospects that reply to sandbox outreach.
evals/        Eval datasets per stage (uploaded to LangSmith).
data/         Sample leads and the customer brief example. No real personal data.
scripts/      Build the Fleet ZIP, check a Fleet export against the repo.
docs/         Architecture, Fleet notes and one-pagers.
```

See `docs/architecture.md` for how the pieces connect and the Fleet facts that shape this layout.

**Where we are:** [`docs/status-vs-notion.md`](docs/status-vs-notion.md) compares what's built with the Notion plan and lists what's still missing, in order of what blocks the week 3 demo. [`docs/use-cases.md`](docs/use-cases.md) maps every use case to its tests.

## Week 1 goal: the pipeline runs end to end with stubs

Every tool in `mcp/` returns fake data in the right shape, so each stage can be built without waiting for the others. Replace your own stubs in week 2.

## Run the tool server locally

```bash
cd mcp
uv sync
uv run python -m sales_tools.server
```

It serves MCP over streamable HTTP at `http://localhost:8000/mcp`. Fleet needs a public URL: use a tunnel (e.g. `cloudflared tunnel --url http://localhost:8000`) while testing, then deploy once and keep the URL fixed.

Deploy once to Cloud Run, then point every `tools.json` at it (one PR):

```bash
scripts/deploy_mcp.sh            # prints https://sales-tools-....run.app/mcp
scripts/set_mcp_url.sh <that url>
```

The deployed server runs on the demo customer (`data/customer_brief.demo.json`) with an in-memory store, so data resets when the instance restarts.

## Import the agent into Fleet

```bash
python3 scripts/package_fleet.py --mcp-url https://<your tool server>/mcp
```

It checks `fleet/` and writes `dist/sales-assistant-fleet.zip` pointing at your tool server (`scripts/build_fleet_zip.sh` builds one with the placeholder URL). Then in Fleet: **+ > Import** and pick the zip. Add schedules by hand. After changing anything in Fleet, export the agent and run `scripts/check_fleet_export.sh path/to/export.zip` before copying the changes back into `fleet/`.

## How we work

- One branch per stream (`research`, `positioning`, `outreach`, `handoff`, `learning-loop`). Merge to `main` by pull request.
- Edit your own subagent folder, tool module and one-pager freely.
- `fleet/AGENTS.md`, `fleet/tools.json`, `schemas/` and `mcp/sales_tools/store.py` are shared. Change them by PR, and Ayaan reviews.
- Sandbox only. No real emails, no own LinkedIn scraping, no real personal data in the repo.
- Never commit `.env` or API keys.
