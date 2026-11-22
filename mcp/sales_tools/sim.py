"""Simulated prospects inside the sandbox tool server. Owner: Gyula (stream 5).

Off by default. Start the server with SALES_LEADS=personas to load the leads from simulator/personas/
(public half only), make research_target return each persona's research, and make get_replies ask the
persona LLM for a reply to the email that was actually sent:

    cd mcp && SALES_LEADS=personas uv run --extra sim python -m sales_tools.server

The persona's private half and the label it intended never reach the agent. The intended label is
ground truth for the reply-classification eval, so it only goes to simulator/out/pipeline-replies.csv.
Without the "sim" extra or an ANTHROPIC_API_KEY, get_replies falls back to the simulator's stub reply.
"""
import copy
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSONA_DIR = ROOT / "simulator" / "personas"
TRUTH_CSV = ROOT / "simulator" / "out" / "pipeline-replies.csv"

ENABLED = os.environ.get("SALES_LEADS") == "personas" and PERSONA_DIR.is_dir()
PERSONAS: dict[str, dict] = (
    {p["lead_id"]: p for p in (json.loads(f.read_text()) for f in sorted(PERSONA_DIR.glob("P-*.json")))} if ENABLED else {}
)


def leads() -> dict[str, dict]:
    """Persona leads as the lead source would hand them over: no research yet, stage new."""
    out = {}
    for lead_id, p in PERSONAS.items():
        lead = copy.deepcopy(p["lead"])
        lead.pop("research", None)
        lead.update(stage="new", status="active", loss_reason=None)  # a candidate; add_lead sets created_at and stage_times
        out[lead_id] = lead
    return out


def research(lead_id: str) -> dict | None:
    p = PERSONAS.get(lead_id)
    return copy.deepcopy(p["lead"]["research"]) if p else None


def reply(lead_id: str, subject: str, body: str) -> dict | None:
    """The persona's reply to this email, or None if it ignores the email (or isn't a persona lead).

    Returns only what an inbox would show: {"body": ...}. The intended label is written to TRUTH_CSV.
    """
    p = PERSONAS.get(lead_id)
    if p is None:
        return None
    sys.path.insert(0, str(ROOT / "simulator"))
    try:
        import simulate  # needs the "sim" extra (anthropic, langsmith)
        r = simulate.reply(p, subject, body)
    except ImportError:
        r = {"body": f"STUB reply from {p['lead']['person']['name']}.", "intended_label": "not_now", "source": "stub"}
    TRUTH_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not TRUTH_CSV.exists()
    with TRUTH_CSV.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["lead_id", "reply_body", "expected_label", "notes"])
        w.writerow([lead_id, r["body"], r["intended_label"], f"simulated ({r['source']}), pipeline run, subject={subject!r}"])
    return None if r["intended_label"] == "no_reply" else {"body": r["body"]}
