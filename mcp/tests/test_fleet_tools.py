"""Who may call what in fleet/: the manager routes, the subagents act. Reads the JSON files only, no model calls.

A Fleet run on 26 Nov showed the manager doing research, sending and the weekly review itself because it had
every stage tool, so the researcher's screenshot step and the coach's rules never ran. These tests keep the
stage tools with the subagents that own them.
"""
import json
import re
from pathlib import Path

FLEET = Path(__file__).resolve().parents[2] / "fleet"
TOOLS_SRC = Path(__file__).resolve().parents[1] / "sales_tools" / "tools"

# What the manager needs to know who is asking, answer questions and route (fleet/AGENTS.md, "Pipeline").
MANAGER_TOOLS = {"get_person", "get_customer_brief", "find_lead", "get_lead", "log_interaction", "get_funnel_metrics",
                 "get_pipeline", "get_rep_calendar", "get_rep_capacity", "get_setup_status", "check_brief",
                 "get_wait_queue", "due_follow_ups", "decide_proposal", "update_team_member", "update_brief",
                 "slack_send_channel_message", "slack_reply_to_message", "slack_read_thread_messages"}


def tools(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    gated = {k.split("::")[1] for k, v in (cfg.get("interrupt_config") or {}).items() if v}
    return {"names": {t["name"] for t in cfg["tools"]}, "gated": gated}


def subagents() -> dict[str, dict]:
    return {p.parent.name: tools(p) for p in (FLEET / "subagents").glob("*/tools.json")}


def server_tools() -> set[str]:
    return set(re.findall(r"@mcp\.tool\(\)\s*\ndef (\w+)", "".join(p.read_text(encoding="utf-8") for p in TOOLS_SRC.glob("*.py"))))


def test_the_manager_only_has_routing_and_read_tools():
    manager = tools(FLEET / "tools.json")["names"]
    assert manager <= MANAGER_TOOLS, f"stage tools on the manager: {sorted(manager - MANAGER_TOOLS)}"
    stage = {"research_target", "research_website_visual", "save_positioning", "save_draft", "record_approval", "send_email",
             "read_reply", "get_replies", "book_meeting", "add_lead", "find_leads", "save_proposal", "draft_quote", "record_payment"}
    assert not manager & stage


def test_every_server_tool_is_reachable_by_some_agent():
    owners = tools(FLEET / "tools.json")["names"].union(*(s["names"] for s in subagents().values()))
    assert not server_tools() - owners, f"no agent can call: {sorted(server_tools() - owners)}"


def test_tools_that_commit_the_company_ask_a_person_wherever_they_are():
    must_ask = {"record_approval", "book_meeting", "reschedule_meeting", "cancel_meeting", "record_meeting_outcome",
                "approve_quote", "record_contract", "record_deal_outcome", "record_payment", "update_brief",
                "add_team_member", "update_team_member", "decide_proposal"}
    for name, cfg in {"manager": tools(FLEET / "tools.json"), **subagents()}.items():
        missing = (cfg["names"] & must_ask) - cfg["gated"]
        assert not missing, f"{name} can call {sorted(missing)} without asking a person"
