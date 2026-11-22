"""Shared helpers for the local positioning evals and the A/B test. Owner: Gyula.

The positioner's system prompt is read from fleet/subagents/positioner/AGENTS.md, so the evals measure
the same prompt Fleet runs. The local runner can't call MCP tools, so the lead and the customer brief are
given inline and the model returns what it would have saved.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulator"))
import llm  # noqa: E402
import simulate  # noqa: E402

RESULTS = ROOT / "evals" / "results"

_spec = importlib.util.spec_from_file_location("positioning_checks", ROOT / "mcp/sales_tools/tools/positioning_checks.py")
positioning_checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(positioning_checks)


def load_brief() -> dict:
    for name in ("customer_brief.json", "customer_brief.demo.json"):
        p = ROOT / "data" / name
        if p.exists():
            return json.loads(p.read_text())
    raise FileNotFoundError("no customer brief in data/")


POSITIONER_FILE = ROOT / "fleet/subagents/positioner/AGENTS.md"


def positioner_prompt(raw: str | None = None) -> str:
    """The system prompt Fleet runs: AGENTS.md without frontmatter and maintainer comments.
    Pass raw to strip a candidate version of the file instead (evals/hill_climb.py)."""
    text = POSITIONER_FILE.read_text() if raw is None else raw
    text = re.sub(r"\A---.*?---\s*", "", text, flags=re.S)  # frontmatter
    return re.sub(r"<!--.*?-->\s*", "", text, flags=re.S).strip()  # maintainer comments


class PositioningOut(BaseModel):
    no_fit: bool = Field(description="True if you would stop this lead instead of positioning it")
    loss_reason: Optional[Literal["wrong_company_fit", "insufficient_data"]] = Field(description="Set only when no_fit is true")
    angle: str = Field(description="The angle you would save with save_positioning, or the one-sentence stop reason")
    evidence: list[str] = Field(description="Exact fact strings from research.facts the angle rests on")
    confidence: float = Field(description="0 to 1")


HARNESS_NOTE = (
    "Local eval harness: tools are not available in this run. The lead (as get_lead returns it) and the "
    "customer brief (as get_customer_brief returns it) are below. Follow your steps and return exactly what "
    "you would pass to save_positioning, or no_fit with the loss reason you would pass to tag_loss_reason."
)


def public_lead(lead: dict) -> dict:
    """The lead as the pipeline sees it. Persona files keep private state outside this object."""
    return {k: v for k, v in lead.items() if k not in ("positioning", "outreach", "handoff")}


def run_positioner(lead: dict, brief: dict, system: str | None = None) -> dict:
    user = (f"{HARNESS_NOTE}\n\n<lead>\n{json.dumps(public_lead(lead), indent=2, ensure_ascii=False)}\n</lead>\n\n"
            f"<customer_brief>\n{json.dumps(brief, indent=2, ensure_ascii=False)}\n</customer_brief>")
    out = llm.structured(llm.POSITIONER_MODEL, system or positioner_prompt(), user, PositioningOut, effort="medium")
    result = out.model_dump()
    result["validation_problems"] = [] if out.no_fit else positioning_checks.check_positioning(lead, out.angle, out.evidence, out.confidence)
    return result


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
