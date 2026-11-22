"""Agent-level tests for the Fleet agent, one folder of cases per part of the pipeline.

Each case (cases/<part>/*.yaml) says which agent to run (the manager, or one subagent on its own), the
state the tool server starts with, what the user types, how a person answers each approval, and what
must be true afterwards. The agent is built from fleet/ exactly as Fleet imports it (AGENTS.md,
subagents, skills, tools.json approvals) and runs on LangChain deepagents against this repo's own MCP
tool server. No Fleet LCU is used; models are called through the Anthropic API.

    uv run --project evals/fleet python evals/fleet/run.py --dry-run          # check every case, no model calls
    uv run --project evals/fleet python evals/fleet/run.py -k outreach          # run one part
    uv run --project evals/fleet python evals/fleet/run.py --model-map ''       # Haiku for every agent

Results: evals/fleet/results/<time>/report.md, results.json and a .jsonl trace per case.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import socket
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SERVER_TOOLS = sorted(set(re.findall(r"@mcp\.tool\(\)\s*\ndef (\w+)",
                                     "".join(p.read_text() for p in (REPO / "mcp/sales_tools/tools").glob("*.py")))))
DEFAULT_MODEL = "anthropic:claude-haiku-5-5"
# Judgement-heavy agents on Sonnet, tool-following ones on Haiku (see the PR #3 runs: on Haiku the manager
# often skips its subagents).
DEFAULT_MODEL_MAP = "manager=anthropic:claude-sonnet-5-5,positioner=anthropic:claude-sonnet-5-5," \
                    "outreach=anthropic:claude-sonnet-5-5,deal-desk=anthropic:claude-sonnet-5-5," \
                    "onboarding=anthropic:claude-sonnet-5-5"
EXPECT_KEYS = {"called", "not_called", "called_with", "asked_person", "not_asked_person", "lead", "delegated_to",
               "logs_follow_schema", "final_mentions", "final_must_not_contain", "queue_length", "proposals",
               "max_tool_calls", "email", "events", "times_called", "sent", "contacts", "outbox_only_sandbox", "no_double_booking", "calendar", "brief", "plain_answer"}
# Every case runs at a pinned time (setup.now overrides it): Tuesday 10:00 in Stockholm, inside business hours.
DEFAULT_NOW = "2026-11-24T10:00:00+01:00"
SETUP_KEYS = {"brief", "reps", "source", "leads", "replies", "interactions", "queue", "proposals", "no_decisions", "now", "calendars", "customer", "websites"}
AGENTS = {"manager"} | {p.name for p in (REPO / "fleet" / "subagents").iterdir() if p.is_dir()}


# --------------------------------------------------------------------------- the agent

def gated(cfg: dict) -> dict[str, bool]:
    return {k.split("::")[1]: True for k, v in (cfg.get("interrupt_config") or {}).items() if v and "::" in k}


def our_tools(cfg: dict) -> list[str]:
    return [t["name"] for t in cfg.get("tools", []) if "tools.langchain.com" not in t.get("mcp_server_url", "")]


def slack_reply(events: list):
    """Stand-in for Fleet's slack_reply_to_message: records the reply instead of posting it. The manager's
    answer to a Slack message is what it posts with this, so the checks read it from here."""
    from langchain_core.tools import tool

    @tool
    def slack_reply_to_message(channel_id: str, message_ts: str, message: str) -> dict:
        """Reply in the Slack thread of the message that triggered you."""
        events.append({"event": "slack_reply", "text": message})
        return {"message_ts": "1795339999.000001", "channel": channel_id, "thread_ts": message_ts,
                "message": "Successfully replied to message in thread"}
    return slack_reply_to_message


def final_answer(events: list) -> str:
    """What the person reads: the last Slack reply, or the manager's last message when it didn't post one."""
    replies = [e["text"] for e in events if e["event"] == "slack_reply"]
    if replies:
        return replies[-1]
    final = [e["text"] for e in events if e["event"] == "ai_message" and e["agent"] == "manager"]
    return final[-1] if final else ""


async def build(agent_name: str, url: str, model: str, model_map: dict, events: list):
    from deepagents import SubAgent, create_deep_agent
    from fleet_deepagents_export.parsers import parse_frontmatter
    from fleet_deepagents_export.skills import StaticSkillsLoader
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langgraph.checkpoint.memory import InMemorySaver

    fleet = REPO / "fleet"
    client = MultiServerMCPClient({"sales": {"transport": "streamable_http", "url": url}})
    server = {t.name: t for t in await client.get_tools(server_name="sales")}

    def pick(names, who):
        missing = [n for n in names if n not in server]
        if missing:
            events.append({"event": "missing_tools", "agent": who, "names": missing})
        return [server[n] for n in names if n in server]

    if agent_name != "manager":  # one subagent on its own, with its own prompt, tools and approvals
        d = fleet / "subagents" / agent_name
        _, prompt = parse_frontmatter((d / "AGENTS.md").read_text())
        cfg = json.loads((d / "tools.json").read_text())
        return create_deep_agent(model=model_map.get(agent_name, model), tools=pick(our_tools(cfg), agent_name),
                                 system_prompt=prompt, interrupt_on=gated(cfg), checkpointer=InMemorySaver())
    subagents = []
    for d in sorted((fleet / "subagents").iterdir()):
        if not (d / "AGENTS.md").exists():
            continue
        meta, prompt = parse_frontmatter((d / "AGENTS.md").read_text())
        cfg = json.loads((d / "tools.json").read_text())
        spec = SubAgent(name=d.name, description=meta.get("description", ""), system_prompt=prompt,
                        tools=pick(our_tools(cfg), d.name), interrupt_on=gated(cfg))
        spec["model"] = model_map.get(d.name, model)
        subagents.append(spec)
    root = json.loads((fleet / "tools.json").read_text())
    loader = StaticSkillsLoader([(fleet / "skills", "/skills")])
    return create_deep_agent(model=model_map.get("manager", model), tools=pick(our_tools(root), "manager") + [slack_reply(events)],
                             system_prompt=(fleet / "AGENTS.md").read_text(), subagents=subagents,
                             skills=loader.skill_paths, middleware=[loader], interrupt_on=gated(root),
                             checkpointer=InMemorySaver())


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def answer(policy: dict, tool: str, args: dict) -> dict:
    """How the person in the case answers an approval: approve, reject (with a note), or edit some arguments."""
    rule = policy.get(tool, policy.get("default", "approve"))
    if isinstance(rule, str):
        rule = {"action": rule}
    if rule.get("reject_weekend"):
        try:
            if datetime.fromisoformat(args.get("start", "")).weekday() >= 5:
                return {"type": "reject", "message": rule.get("message", "That's a weekend. Pick a weekday slot.")}
        except ValueError:
            pass
        return {"type": "approve"}
    if rule.get("action") == "reject":
        return {"type": "reject", "message": rule.get("message", "Rejected.")}
    if rule.get("action") == "edit":
        return {"type": "edit", "edited_action": {"name": tool, "args": {**args, **rule.get("args", {})}}}
    if rule.get("action") == "reject_first":  # reject once, then approve
        if not rule.get("_done"):
            rule["_done"] = True
            policy[tool] = rule
            return {"type": "reject", "message": rule.get("message", "Rejected.")}
        return {"type": "approve"}
    return {"type": "approve"}


DEFAULT_SENDER = "rep-1"  # a turn with no `from` comes from this person; `from: null` is a scheduled run (no sender)


def slack_message(text: str, slack_user_id: str | None) -> str:
    """A turn exactly as Fleet hands a Slack message to the agent (seen in a real Slack run, 22 Nov 2026): the
    channel, the reply target, then the request with the sender's Slack user ID in front. The one place that
    knows Fleet's format. No sender (a schedule): the text alone."""
    if not slack_user_id:
        return text
    return ("**Slack Thread**\n- **Channel ID:** `C0SALESDESK`\n\n"
            "**Reply Target (use this as `message_ts` in `slack_reply_to_message`):**\n- `1795339902.450959`\n\n"
            "This is the user's request that triggered this agent:\n"
            f"<user_request>\n@{slack_user_id}: {text}\n</user_request>")


def turn_messages(case: dict) -> list[str]:
    """Each turn's text with its sender. People don't say who they are: a turn names its sender with `from`."""
    setup = case.get("setup") or {}
    reps = setup.get("reps") or json.loads((REPO / "data/reps.demo.json").read_text())
    out = []
    for t in case["turns"]:
        sender = DEFAULT_SENDER if isinstance(t, str) else t.get("from", DEFAULT_SENDER)
        text = t if isinstance(t, str) else t["text"]
        slack_id = (reps.get(sender) or {}).get("slack_user_id") or sender if sender else None  # or a raw Slack ID
        if case.get("agent") != "manager":  # a subagent gets the task from the manager, with who asked in Slack
            out.append(f"{text}\n\n(Asked in Slack by <@{slack_id}>.)" if slack_id else text)
        else:
            out.append(slack_message(text, slack_id))
    return out


async def run_turns(agent, turns: list[str], approvals: dict, events: list):
    from langgraph.types import Command

    approvals = json.loads(json.dumps(approvals))  # reject_first keeps state per case
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 150}
    seen_calls, seen_msgs = set(), set()  # a message streams again after an interrupt resumes: count it once
    for turn in turns:
        events.append({"event": "user", "text": turn})
        payload = {"messages": [{"role": "user", "content": turn}]}
        while True:
            interrupts = []
            async for ns, update in agent.astream(payload, config, stream_mode="updates", subgraphs=True):
                who = "manager" if not ns else ns[-1].split(":")[0]
                for node, val in (update or {}).items():
                    if node == "__interrupt__":
                        interrupts.extend(val)
                        continue
                    for m in (val.get("messages", []) if isinstance(val, dict) else []):
                        um = getattr(m, "usage_metadata", None)
                        if m.type == "ai" and um and not (m.id and m.id in seen_msgs):
                            seen_msgs.add(m.id)
                            d = um.get("input_token_details") or {}
                            events.append({"event": "usage", "input": um.get("input_tokens", 0),
                                           "output": um.get("output_tokens", 0), "cache_read": d.get("cache_read", 0) or 0})
                        for tc in getattr(m, "tool_calls", None) or []:
                            if tc.get("id") and tc["id"] in seen_calls:
                                continue
                            seen_calls.add(tc.get("id"))
                            events.append({"event": "tool_call", "agent": who, "tool": tc["name"], "args": tc["args"]})
                        if m.type == "tool":
                            events.append({"event": "tool_result", "agent": who, "tool": m.name, "result": text_of(m.content)[:1500]})
                        elif m.type == "ai" and not getattr(m, "tool_calls", None) and text_of(m.content):
                            events.append({"event": "ai_message", "agent": who, "text": text_of(m.content)})
            if not interrupts:
                break
            interrupts = list({it.id: it for it in interrupts}.values())
            resume = {}
            for it in interrupts:
                decisions = []
                for r in (it.value or {}).get("action_requests", []):
                    dec = answer(approvals, r["name"], r["args"])
                    events.append({"event": "asked_person", "tool": r["name"], "args": r["args"], "decision": dec["type"]})
                    decisions.append(dec)
                resume[it.id] = {"decisions": decisions}
            payload = Command(resume=resume)


# --------------------------------------------------------------------------- state and server

def _utc(iso: str) -> str:
    t = datetime.fromisoformat(str(iso))
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


def _replies(value) -> list[dict]:
    """setup.replies[lead]: a body, one email {from, cc, subject, body, received_at, attachments}, or a list of them."""
    items = value if isinstance(value, list) else [value]
    return [{"body": v} if isinstance(v, str) else dict(v) for v in items]


def initial_state(setup: dict) -> dict:
    """The case's setup as a store snapshot. Leads get defaults. A lead with outreach.sent_at gets our email in
    the outbox; setup.replies become the prospect's emails in the inbox, answering it."""
    now = _utc(setup.get("now") or DEFAULT_NOW)
    # every case starts from the demo customer, or from one of the test customer types in data/customers/
    # customer: blank starts from the empty example brief (a customer who has just signed up)
    base = (REPO / "data/customer_brief.example.json" if setup.get("customer") == "blank" else
            REPO / "data/customers" / f"{setup['customer']}.json" if setup.get("customer") else REPO / "data/customer_brief.demo.json")
    brief = {k: v for k, v in json.loads(base.read_text()).items() if not k.startswith("_")}
    if setup.get("customer") == "blank":
        brief["customer_id"] = "C-new"
    for k, v in (setup.get("brief") or {}).items():
        brief[k] = {**brief[k], **v} if isinstance(v, dict) and isinstance(brief.get(k), dict) else v
    sender = brief.get("sender") or {"name": "Kvitto", "email": "outreach@sandbox.test"}
    leads = {}
    for lead in setup.get("leads", []):
        lead = {"customer_id": brief["customer_id"], "stage": "new", "status": "active", "loss_reason": None,
                "created_at": now, "updated_at": now, **lead}
        lead.setdefault("stage_times", {lead["stage"]: now})
        leads[lead["lead_id"]] = lead
    outbox, inbox = [], []
    for lid in set(setup.get("replies") or {}) | {l for l, v in leads.items() if (v.get("outreach") or {}).get("sent_at")}:
        lead = leads[lid]
        out = lead.setdefault("outreach", {})
        out.setdefault("draft_subject", "Earlier email")
        out.setdefault("draft_body", "(sent)")
        out["sent_at"] = _utc(out.get("sent_at") or now)
        out.setdefault("thread_id", f"T-{lid}")
        out.setdefault("thread_subject", out["draft_subject"])
        out.setdefault("sent_count", 1)
        ours = {"message_id": f"{lid}-out-1", "thread_id": out["thread_id"], "lead_id": lid, "direction": "out",
                "from": f"{sender['name']} <{sender['email']}>", "to": [lead["person"]["email"]],
                "cc": list(out.get("draft_cc") or []), "subject": out["draft_subject"], "body": out["draft_body"],
                "in_reply_to": None, "attachments": [], "status": "sent", "scheduled_for": None,
                "sent_at": out["sent_at"], "asked": True, "seeded": True}
        outbox.append(ours)
        for n, r in enumerate(_replies((setup.get("replies") or {}).get(lid, [])), 1):
            frm = r.get("from") or lead["person"]["email"]
            inbox.append({"message_id": f"{lid}-in-{n}", "thread_id": out["thread_id"], "lead_id": lid, "direction": "in",
                          "from": frm, "to": r.get("to") or [sender["email"]], "cc": list(r.get("cc") or []),
                          "subject": r.get("subject") or f"Re: {out['draft_subject']}", "body": r["body"],
                          "received_at": _utc(r.get("received_at") or now), "in_reply_to": ours["message_id"],
                          "attachments": list(r.get("attachments") or []), "handled": False, "seeded": True,
                          "from_lead": frm.split("<")[-1].strip(" >").lower() == lead["person"]["email"].lower()})
    state = {"leads": leads, "outbox": outbox, "inbox": inbox, "interactions": setup.get("interactions", []),
             "queue": setup.get("queue", []), "proposals": setup.get("proposals", []), "approvals": {},
             "clock": {"now": now}}
    if "reps" in setup:
        state["reps"] = setup["reps"]
    # calendars: {rep_id: [{start, end, title?, kind?}]}; busy blocks unless kind says meeting
    state["calendars"] = {rid: [{"event_id": e.get("event_id") or f"B-{rid}-{i}", "rep_id": rid, "kind": e.get("kind", "busy"),
                                 "status": e.get("status", "booked"), "title": e.get("title", "Busy"), "lead_id": e.get("lead_id"),
                                 "start": _utc(e["start"]), "end": _utc(e["end"]), "attendees": e.get("attendees", []),
                                 "history": []}
                                for i, e in enumerate(events, 1)]
                          for rid, events in (setup.get("calendars") or {}).items()}
    if "source" in setup:
        state["source"] = {c["lead_id"]: {"customer_id": brief["customer_id"], **c} for c in setup["source"]}
    state["brief"] = brief
    state["websites"] = setup.get("websites") or {}
    return state


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def tool_server(state: dict, logdir: Path, name: str, no_decisions: bool = False):
    """Start this repo's tool server with SALES_STATE pointing at the case's snapshot. It saves after every tool call."""
    port, state_file = free_port(), logdir / f"{name}.state.json"
    state_file.write_text(json.dumps(state, default=str))
    log = open(logdir / f"{name}.server.log", "w")
    env = {**os.environ, "SALES_STATE": str(state_file), "PORT": str(port), "HOST": "127.0.0.1"}
    if no_decisions:  # no d1 and no Claude: every gate returns no answer and must send the lead to a person
        for k in ("LIQUID_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(k, None)
        env["DOTENV_DISABLED"] = "1"
    proc = await asyncio.create_subprocess_exec("uv", "run", "--quiet", "--project", str(REPO / "mcp"), "python", "-m",
                                                "sales_tools.server", cwd=str(REPO / "mcp"), env=env, stdout=log, stderr=log)
    try:
        for _ in range(240):
            await asyncio.sleep(0.25)
            text = (logdir / f"{name}.server.log").read_text()
            if "Uvicorn running" in text:
                break
            if proc.returncode is not None or "Traceback" in text:
                raise RuntimeError(f"tool server failed, see {name}.server.log")
        else:
            raise RuntimeError("tool server did not start in 60s")
        yield f"http://127.0.0.1:{port}/mcp", state_file
    finally:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        await proc.wait()
        log.close()


# --------------------------------------------------------------------------- checks

def dig(obj, path: str):
    for part in path.split("."):
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def matches(args: dict, want: dict) -> bool:
    for k, v in want.items():
        got = args.get(k)
        if isinstance(v, str) and v.startswith("~"):  # ~text: contains, case-insensitive
            if v[1:].lower() not in str(got).lower():
                return False
        elif got != v:
            return False
    return True


def check(expect: dict, events: list, state: dict, enums: dict) -> list[dict]:
    calls = [e for e in events if e["event"] == "tool_call"]
    called = [c["tool"] for c in calls]
    asked = [e["tool"] for e in events if e["event"] == "asked_person"]
    out = []

    def add(name, ok, detail=""):
        out.append({"check": name, "ok": bool(ok), "detail": "" if ok else detail})

    for t in expect.get("called", []):
        add(f"called {t}", t in called, f"tools called: {sorted(set(called))}")
    for t in expect.get("not_called", []):
        add(f"did not call {t}", t not in called, f"called {called.count(t)}x")
    for t, want in (expect.get("called_with") or {}).items():
        args = [c["args"] for c in calls if c["tool"] == t]
        add(f"called {t} with {want}", any(matches(a, want) for a in args), f"got {args[:3]}")
    for t in expect.get("asked_person", []):
        add(f"asked a person before {t}", t in asked, f"asked for: {asked}")
    for t in expect.get("not_asked_person", []):
        add(f"never asked a person about {t}", t not in asked)
    targets = [(lid, state.get("leads", {}).get(lid, {}), fields) for lid, fields in (expect.get("lead") or {}).items()]
    if expect.get("brief"):  # the customer brief after the run (onboarding), same rules as lead
        targets.append(("brief", state.get("brief") or {}, expect["brief"]))
    for lid, lead, fields in targets:
        for path, want in fields.items():
            got = dig(lead, path)
            # "set": not null. "!x": anything but x. A list against a scalar: one of these values. A list against a list: equal.
            ok = ((got is not None) if want == "set"
                  else (got != want[1:]) if isinstance(want, str) and want.startswith("!")
                  else (want[1:].lower() in json.dumps(got, ensure_ascii=False).lower() if got is not None else False)
                  if isinstance(want, str) and want.startswith("~")
                  else (got in want) if isinstance(want, list) and not isinstance(got, list)
                  else got == want)
            add(f"{lid}.{path} == {want}", ok, f"got {got!r}")
    for name in expect.get("delegated_to", []):
        add(f"manager delegated to @{name}",
            any(c["tool"] == "task" and c["args"].get("subagent_type") == name for c in calls),
            f"delegated to {[c['args'].get('subagent_type') for c in calls if c['tool'] == 'task']}")
    if expect.get("logs_follow_schema") and enums:
        bad = [c["args"] for c in calls if c["tool"] == "log_interaction"
               and (c["args"].get("stage") not in enums["stage"] or c["args"].get("outcome") not in enums["outcome"])]
        add("log_interaction calls use schema values", not bad, str(bad[:2]))
    answer = final_answer(events)
    for phrase in expect.get("final_mentions", []):
        add(f"final message mentions {phrase!r}", phrase.lower() in answer.lower())
    for phrase in expect.get("final_must_not_contain", []):
        add(f"final message doesn't contain {phrase!r}", phrase.lower() not in answer.lower(), "it does")
    if expect.get("plain_answer"):
        text = final_answer(events)
        ids = set(state.get("leads", {})) | {(state.get("brief") or {}).get("customer_id") or ""}
        found = [i for i in ids if i and re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", text)]
        found += re.findall(r"\b(?:rep|fin|view|adm)-\d+\b", text)
        found += [t for t in SERVER_TOOLS if re.search(rf"\b{t}\b", text)]
        add("the answer is in plain words (no IDs or tool names)", not found, f"contains {sorted(set(found))}")
    for t, n in (expect.get("times_called") or {}).items():
        add(f"{t} called {n}x", called.count(t) == n, f"called {called.count(t)}x")
    if expect.get("email"):
        e = expect["email"]
        out_ = (state.get("leads", {}).get(e["lead"], {}).get("outreach") or {})
        subject, body = out_.get("draft_subject") or "", out_.get("draft_body") or ""
        text, words = f"{subject}\n{body}".lower(), len(body.split())
        add("an email was drafted", bool(body), "no draft on the lead")
        if body:
            if "max_words" in e:
                add(f"email body at most {e['max_words']} words", words <= e["max_words"], f"{words} words")
            if e.get("mentions_any"):
                add(f"email mentions one of {e['mentions_any']}", any(m.lower() in text for m in e["mentions_any"]), body[:200])
            for bad in e.get("must_not_contain", []):
                add(f"email doesn't contain {bad!r}", bad.lower() not in text, body[:200])
    if expect.get("sent"):  # the emails the agent sent (or held) during the run, from the saved outbox
        e = expect["sent"]
        new = [m for m in state.get("outbox", []) if m["lead_id"] == e["lead"] and not m.get("seeded")]
        if "count" in e:
            add(f"{e['count']} email(s) to {e['lead']}", len(new) == e["count"], f"got {len(new)}")
        add(f"an email to {e['lead']} went into the outbox", bool(new), "nothing in the outbox")
        if new:
            m, low = new[-1], lambda xs: [a.lower() for a in xs]
            if "status" in e:
                add(f"email status {e['status']}", m["status"] == e["status"], f"got {m['status']}")
            for a in e.get("to_includes", []):
                add(f"to includes {a}", any(a.lower() in x for x in low(m["to"])), f"to {m['to']}")
            for a in e.get("cc_includes", []):
                add(f"cc includes {a}", any(a.lower() in x for x in low(m["cc"])), f"cc {m['cc']}")
            for a in e.get("cc_excludes", []):
                add(f"cc doesn't include {a}", not any(a.lower() in x for x in low(m["cc"] + m["to"][1:])), f"cc {m['cc']}")
            if "subject_startswith" in e:
                add(f"subject starts with {e['subject_startswith']!r}", m["subject"].lower().startswith(e["subject_startswith"].lower()),
                    f"subject {m['subject']!r}")
            if e.get("same_thread"):
                first = next((x for x in state.get("outbox", []) if x["lead_id"] == e["lead"]), m)
                add("same thread as the first email", m["thread_id"] == first["thread_id"] and m.get("in_reply_to"),
                    f"thread {m['thread_id']}, in_reply_to {m.get('in_reply_to')}")
            if "scheduled_for" in e:
                add(f"held until {e['scheduled_for']}", m.get("scheduled_for") == _utc(e["scheduled_for"]), f"got {m.get('scheduled_for')}")
    if expect.get("no_double_booking"):  # no rep has two live events at the same time
        clashes = []
        for rid, evs in (state.get("calendars") or {}).items():
            live = sorted((e for e in evs if e.get("status") != "cancelled"), key=lambda e: e["start"])
            clashes += [f"{rid}: {a['event_id']} and {b['event_id']}" for a, b in zip(live, live[1:])
                        if datetime.fromisoformat(b["start"]) < datetime.fromisoformat(a["end"])]
        add("no rep is double-booked", not clashes, "; ".join(clashes))
    for rid, want in (expect.get("calendar") or {}).items():  # {rep-1: {meetings: 1, lead: L-1, status: booked, start: ...}}
        evs = [e for e in (state.get("calendars") or {}).get(rid, []) if e.get("kind") == "meeting"
               and (not want.get("lead") or e.get("lead_id") == want["lead"])]
        if "meetings" in want:
            add(f"{rid} has {want['meetings']} meeting(s)", len([e for e in evs if e.get("status") != "cancelled"]) == want["meetings"],
                f"got {[(e['event_id'], e['status']) for e in evs]}")
        if evs and "status" in want:
            add(f"{rid} meeting status {want['status']}", evs[-1]["status"] == want["status"], f"got {evs[-1]['status']}")
        if evs and "start" in want:
            add(f"{rid} meeting at {want['start']}", evs[-1]["start"] == _utc(want["start"]), f"got {evs[-1]['start']}")
        if not evs and ("status" in want or "start" in want):
            add(f"{rid} has a meeting", False, "none")
    if expect.get("outbox_only_sandbox"):  # nothing we sent or held is addressed outside the .test sandbox
        outside = [a for m in state.get("outbox", []) for a in m["to"] + m["cc"]
                   if not a.split("<")[-1].strip(" >").lower().endswith(".test")]
        add("every recipient is a sandbox address", not outside, f"outside the sandbox: {outside}")
    for lid, wants in (expect.get("contacts") or {}).items():
        have = (state.get("leads", {}).get(lid) or {}).get("contacts") or []
        for w in wants:
            add(f"{lid} contact {w}", any(all(str(v).lower() in str(c.get(k, "")).lower() for k, v in w.items()) for c in have),
                f"contacts {[(c['email'], c['role']) for c in have]}")
    for want in expect.get("events", []):  # rows in the saved event log, e.g. {stage: approval, outcome: failure, by: agent}
        rows = state.get("interactions", [])
        add(f"event log has {want}", any(all(str(r.get(k)) == str(v) if not str(v).startswith("~") else str(v)[1:].lower() in str(r.get(k)).lower()
                                             for k, v in want.items()) for r in rows), f"{len(rows)} rows")
    if "queue_length" in expect:
        add(f"wait queue has {expect['queue_length']}", len(state.get("queue", [])) == expect["queue_length"],
            f"got {len(state.get('queue', []))}")
    if "proposals" in expect:
        add(f"{expect['proposals']} proposal(s) saved", len(state.get("proposals", [])) == expect["proposals"],
            f"got {len(state.get('proposals', []))}")
    if "max_tool_calls" in expect:
        add(f"at most {expect['max_tool_calls']} tool calls", len(calls) <= expect["max_tool_calls"], f"{len(calls)} calls")
    return out


# --------------------------------------------------------------------------- run

def load_cases(k: str) -> list[tuple[str, Path, dict]]:
    cases = []
    for p in sorted((HERE / "cases").glob("*/*.yaml")):
        name = f"{p.parent.name}/{p.stem}"
        if k in name:
            cases.append((name, p, yaml.safe_load(p.read_text())))
    return cases


def validate(name: str, case: dict, server_tools: set[str]) -> list[str]:
    """Static checks a case must pass before it is worth a model call."""
    problems = []
    if set(case.get("setup", {})) - SETUP_KEYS:
        problems.append(f"unknown setup keys {sorted(set(case['setup']) - SETUP_KEYS)}")
    for lid, v in (case.get("setup", {}).get("replies") or {}).items():
        for r in (v if isinstance(v, list) else [v]):
            if isinstance(r, dict) and (set(r) - {"from", "to", "cc", "subject", "body", "received_at", "attachments"} or "body" not in r):
                problems.append(f"reply for {lid} needs a body and only from/to/cc/subject/body/received_at/attachments")
    sent = (case.get("expect") or {}).get("sent")
    if sent and (set(sent) - {"lead", "status", "to_includes", "cc_includes", "cc_excludes", "subject_startswith",
                              "same_thread", "scheduled_for", "count"} or "lead" not in sent):
        problems.append("expect.sent needs a lead and only status/to_includes/cc_includes/cc_excludes/subject_startswith/same_thread/scheduled_for/count")
    if (case.get("expect") or {}).get("email") and "lead" not in case["expect"]["email"]:
        problems.append("expect.email needs a lead")
    for key in ("title", "agent", "turns", "expect"):
        if key not in case:
            problems.append(f"missing {key}")
    reps = (case.get("setup") or {}).get("reps") or json.loads((REPO / "data/reps.demo.json").read_text())
    for t in case.get("turns") or []:
        if isinstance(t, dict):
            if set(t) - {"from", "text"} or not isinstance(t.get("text"), str):
                problems.append("a turn is a string or {from, text}")
            elif t.get("from") is not None and not (reps.get(t["from"]) or {}).get("slack_user_id") \
                    and not str(t["from"]).startswith("U"):
                problems.append(f"turn from {t['from']!r}: not a rep with a slack_user_id, nor a Slack ID (U...)")
    if case.get("agent") not in AGENTS:
        problems.append(f"agent {case.get('agent')!r} is not manager or a subagent in fleet/subagents/")
    unknown = set(case.get("expect", {})) - EXPECT_KEYS
    if unknown:
        problems.append(f"unknown expect keys {sorted(unknown)}")
    named = set(case.get("approvals", {})) - {"default"}
    for key in ("called", "not_called", "asked_person", "not_asked_person"):
        named |= set(case.get("expect", {}).get(key, []))
    named |= set((case.get("expect", {}).get("called_with") or {})) | set((case.get("expect", {}).get("times_called") or {}))
    named -= {"task", "read_file", "write_todos"}
    if server_tools and named - server_tools:
        problems.append(f"tools not on the server: {sorted(named - server_tools)}")
    try:
        state = initial_state(case.get("setup", {}))
        json.dumps(state, default=str)
    except Exception as e:  # noqa: BLE001
        problems.append(f"setup does not load: {type(e).__name__}: {e}")
    if case.get("agent", "manager") != "manager":
        tools = {t["name"] for t in json.loads((REPO / "fleet/subagents" / case["agent"] / "tools.json").read_text())["tools"]}
        need = set(case.get("expect", {}).get("called", [])) - {"task", "read_file", "write_todos"}
        if need - tools:
            problems.append(f"@{case['agent']} doesn't have {sorted(need - tools)} in its tools.json")
    return problems


async def check_fixture(name: str, case: dict, logdir: Path, sem: asyncio.Semaphore) -> list[str]:
    """Start the tool server with the case's state and read it back over MCP. No model calls."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    setup, problems = case.get("setup", {}), []
    async with sem:
        try:
            async with tool_server(initial_state(setup), logdir, name.replace("/", "__")) as (url, _):
                client = MultiServerMCPClient({"sales": {"transport": "streamable_http", "url": url}})
                tools = {t.name: t for t in await client.get_tools(server_name="sales")}

                async def call(tool, args):
                    raw = await tools[tool].ainvoke(args)
                    blocks = raw if isinstance(raw, list) else [raw]
                    def parse(text):
                        try:
                            return json.loads(text)
                        except ValueError:  # a plain string, e.g. one slot from find_free_slots
                            return text
                    return [parse(b["text"]) if isinstance(b, dict) and "text" in b
                            else parse(b) if isinstance(b, str) else b for b in blocks]
                for lead in setup.get("leads", []):
                    got = (await call("get_lead", {"lead_id": lead["lead_id"]}))[0]
                    if got.get("lead_id") != lead["lead_id"]:
                        problems.append(f"lead {lead['lead_id']} not loaded")
                replies = await call("get_replies", {}) if setup.get("replies") else []
                want = sum(len(_replies(v)) for v in (setup.get("replies") or {}).values())
                if len(replies) != want:
                    problems.append(f"{len(replies)} replies in the inbox, expected {want}")
                for r in replies:
                    if not r.get("answers"):
                        problems.append(f"reply {r.get('message_id')} isn't linked to the email it answers")
                for rid, evs in (setup.get("calendars") or {}).items():
                    slots = await call("find_free_slots", {"rep_id": rid, "days_ahead": 10})
                    if any(any(_utc(e["start"]) <= s < _utc(e["end"]) for e in evs) for s in slots):
                        problems.append(f"find_free_slots for {rid} offers a time inside a seeded busy block")
                if "reps" in setup:
                    reps = {r["rep_id"]: r["status"] for r in await call("get_rep_capacity", {})}
                    if reps != {k: v["status"] for k, v in setup["reps"].items()}:
                        problems.append(f"reps loaded as {reps}")
                if "source" in setup:
                    found = {c["lead_id"] for c in await call("find_leads", {"customer_id": initial_state(setup)["brief"]["customer_id"], "limit": 50})}
                    expected = {c["lead_id"] for c in setup["source"]}
                    known = {(l.get("person") or {}).get("email") for l in setup.get("leads", [])}
                    expected -= {c["lead_id"] for c in setup["source"] if (c.get("person") or {}).get("email") in known}
                    if found != expected:
                        problems.append(f"find_leads returned {sorted(found)}, expected {sorted(expected)}")
                want_brief = initial_state(setup)["brief"]
                brief = (await call("get_customer_brief", {"customer_id": want_brief["customer_id"]}))[0]
                if brief.get("customer_id") != want_brief["customer_id"] or brief.get("company_name") != want_brief.get("company_name"):
                    problems.append(f"the brief didn't load (got {brief.get('customer_id')})")
                for k, v in (setup.get("brief") or {}).items():
                    if isinstance(v, dict) and any(brief.get(k, {}).get(kk) != vv for kk, vv in v.items()):
                        problems.append(f"brief override {k} didn't apply")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{type(e).__name__}: {e}")
    return problems


async def run_case(name: str, path: Path, case: dict, args, enums: dict, logdir: Path, sem: asyncio.Semaphore) -> dict:
    """Run a case `repeat` times (case field or --repeat). It passes when at least `pass_at` runs pass (default: all)."""
    n = max(args.repeat, case.get("repeat", 1))
    runs = await asyncio.gather(*(run_once(f"{name}#{i}" if n > 1 else name, case, args, enums, logdir, sem) for i in range(n)))
    if n == 1:
        return runs[0]
    need = case.get("pass_at", n)
    first = next((r for r in runs if not r["passed"]), runs[0])
    return {**first, "case": name, "passed": sum(r["passed"] for r in runs) >= need,
            "pass_rate": f"{sum(r['passed'] for r in runs)}/{n}", "runs": runs}


async def run_once(name: str, case: dict, args, enums: dict, logdir: Path, sem: asyncio.Semaphore) -> dict:
    events, started, safe = [], time.time(), name.replace("/", "__").replace("#", "_run")
    async with sem:
        try:
            async with tool_server(initial_state(case.get("setup", {})), logdir, safe,
                                   no_decisions=case.get("setup", {}).get("no_decisions", False)) as (url, state_file):
                agent = await build(case["agent"], url, args.model, args.model_map, events)
                await run_turns(agent, turn_messages(case), case.get("approvals", {}), events)
            state = json.loads(state_file.read_text())
            expect = dict(case["expect"])
            if case["agent"] == "manager":  # people read the manager's answers: plain words unless the case says otherwise
                expect.setdefault("plain_answer", True)
            checks, error = check(expect, events, state, enums), None
        except Exception as e:  # noqa: BLE001  one failing case doesn't stop the others
            checks, error = [], f"{type(e).__name__}: {e}"
    (logdir / f"{safe}.jsonl").write_text("".join(json.dumps(e, default=str) + "\n" for e in events))
    usage = [e for e in events if e["event"] == "usage"]
    final = [e["text"] for e in events if e["event"] == "ai_message" and e["agent"] == "manager"]
    return {"case": name, "title": case["title"], "agent": case["agent"], "error": error,
            "passed": not error and all(c["ok"] for c in checks), "checks": checks,
            "tools": [e["tool"] for e in events if e["event"] == "tool_call"],
            "asked_person": [e["tool"] for e in events if e["event"] == "asked_person"],
            "final_message": final_answer(events)[:800],
            "tokens": {k: sum(u[k] for u in usage) for k in ("input", "cache_read", "output")},
            "seconds": round(time.time() - started)}


def report(results: list, args) -> str:
    lines = ["# Fleet test results", "", f"Model `{args.model}`, overrides `{args.model_map}`",
             f"Passed {sum(r['passed'] for r in results)}/{len(results)}", ""]
    by_part: dict[str, list] = {}
    for r in results:
        by_part.setdefault(r["case"].split("/")[0], []).append(r)
    lines += ["| Part | Passed |", "|---|---|"] + [f"| {p} | {sum(r['passed'] for r in rs)}/{len(rs)} |" for p, rs in by_part.items()] + [""]
    for r in results:
        lines.append(f"## {'✅' if r['passed'] else '❌'} {r['case']}: {r['title']}" + (f" ({r['pass_rate']} runs passed)" if "pass_rate" in r else ""))
        if r["error"]:
            lines.append(f"- Error: {r['error']}")
        lines += [f"- {'✅' if c['ok'] else '❌'} {c['check']}" + (f" ({c['detail']})" if c["detail"] else "") for c in r["checks"]]
        t = r["tokens"]
        lines += [f"- Tools: {', '.join(r['tools']) or 'none'}", f"- Asked a person: {', '.join(r['asked_person']) or 'no'}",
                  f"- Tokens: {t['input']:,} in ({t['cache_read']:,} cached), {t['output']:,} out; {r['seconds']}s", "",
                  "> " + r["final_message"].replace("\n", "\n> "), ""]
    return "\n".join(lines)


def schema_enums() -> dict:
    props = json.loads((REPO / "schemas/interaction.schema.json").read_text())["properties"]
    return {"stage": set(props["stage"]["enum"]), "outcome": set(props["outcome"]["enum"])}


def server_tool_names() -> set[str]:
    import re
    return set(re.findall(r"@mcp\.tool\(\)\s*\ndef (\w+)", "".join(p.read_text() for p in (REPO / "mcp/sales_tools/tools").glob("*.py"))))


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", default="", help="only cases whose part/name contains this")
    ap.add_argument("--dry-run", action="store_true", help="validate every case without starting a model")
    ap.add_argument("--check-fixtures", action="store_true", help="start the tool server for each case and read its data back; no model calls")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--model-map", default=DEFAULT_MODEL_MAP, help="per-agent models; '' for --model everywhere")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--repeat", type=int, default=1, help="run every case this many times (cases can also set repeat)")
    ap.add_argument("--now", default=None, help="pinned time for cases without setup.now, e.g. 2026-12-24T22:00:00+01:00")
    args = ap.parse_args()
    if args.now:
        global DEFAULT_NOW
        DEFAULT_NOW = args.now
    cases = load_cases(args.k)
    if not cases:
        sys.exit("No cases matched")
    tools = server_tool_names()
    problems = {name: validate(name, case, tools) for name, _, case in cases}
    for name, probs in problems.items():
        print(f"{'OK ' if not probs else 'BAD'}  {name}" + (f": {'; '.join(probs)}" if probs else ""))
    if args.check_fixtures and not any(problems.values()):
        logdir = HERE / "results" / ("fixtures-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        logdir.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(args.parallel)
        found = await asyncio.gather(*(check_fixture(n, c, logdir, sem) for n, _, c in cases))
        print()
        for (n, _, _), probs in zip(cases, found):
            print(f"{'OK ' if not probs else 'BAD'}  fixture {n}" + (f": {'; '.join(probs)}" if probs else ""))
        print(f"\n{sum(not p for p in found)}/{len(cases)} fixtures load (no model calls)")
        sys.exit(1 if any(found) else 0)
    if any(problems.values()) or args.dry_run:
        print(f"\n{sum(not p for p in problems.values())}/{len(cases)} cases valid" + (" (dry run, no model calls)" if args.dry_run else ""))
        sys.exit(1 if any(problems.values()) else 0)
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set (env or the repo's .env)")
    args.model_map = dict(kv.split("=", 1) for kv in args.model_map.split(",") if kv)
    logdir = HERE / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    logdir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.parallel)
    print(f"\nRunning {len(cases)} case(s)...")
    results = await asyncio.gather(*(run_case(n, p, c, args, schema_enums(), logdir, sem) for n, p, c in cases))
    (logdir / "results.json").write_text(json.dumps(results, indent=1, default=str))
    (logdir / "report.md").write_text(report(results, args))
    for r in results:
        failed = [c["check"] for c in r["checks"] if not c["ok"]]
        rate = f" [{r['pass_rate']}]" if "pass_rate" in r else ""
        print(f"{'ERROR' if r['error'] else 'PASS ' if r['passed'] else 'FAIL '} {r['case']:<40}{rate} {r['error'] or '; '.join(failed)}")
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} passed. Report: {logdir / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
