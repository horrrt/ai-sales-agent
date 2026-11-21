"""LLM-as-judge for positioning quality. Owner: Gyula. Dataset: evals/datasets/positioning.csv.

Scale for expected_angle_quality (the human label) and for the judge's overall score:
  good       Specific to this lead, rests on a research fact that shows a real trigger, every offer claim
             is in the brief. You'd be comfortable sending an email built on it.
  weak       Supported and not wrong, but generic (would fit most companies in the ICP) or rests on a weak link.
  bad        Unsupported claim about the lead or the offer, wrong for the lead's situation, or an angle forced
             onto a lead that should have been stopped.
  correct_stop  The positioner stopped the lead (no_fit) and that was right.
  wrong_stop    The positioner stopped a lead that had a usable angle.

The judge sees only what the positioner saw (public lead + brief), never the persona's private state,
so it grades the angle the way a careful salesperson reviewing it would.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

import common
from common import llm

QUALITY = ("good", "weak", "bad", "correct_stop", "wrong_stop")


class Verdict(BaseModel):
    specific: int = Field(ge=1, le=5, description="1 = would fit any company, 5 = only makes sense for this lead")
    supported_by_research: int = Field(ge=1, le=5, description="Every claim about the lead traces to a research fact")
    supported_by_brief: int = Field(ge=1, le=5, description="Every claim about the offer is in the brief, nothing overclaimed")
    trigger_strength: int = Field(ge=1, le=5, description="How directly the cited fact shows a buying trigger from the brief")
    overall: Literal["good", "weak", "bad", "correct_stop", "wrong_stop"]
    critique: str = Field(description="Two sentences max: the main problem, or why it's good")


SYSTEM = """You review positioning angles written by a sales agent before any email is drafted from them. You are strict: an angle that would fit any company in the ICP is "weak" even if it is true, and any claim not backed by the research facts or the customer brief makes it "bad".

If the agent stopped the lead (no_fit), judge whether stopping was right: "correct_stop" if the lead is outside the ICP or no research fact links to a trigger in the brief, otherwise "wrong_stop". For stops, score the four criteria on the stop reason.

Score each criterion 1-5, then give the overall label."""


def judge(lead: dict, brief: dict, positioning: dict) -> dict:
    user = (f"<lead>\n{json.dumps(common.public_lead(lead), indent=2, ensure_ascii=False)}\n</lead>\n\n"
            f"<customer_brief>\n{json.dumps(brief, indent=2, ensure_ascii=False)}\n</customer_brief>\n\n"
            f"<agent_output>\n{json.dumps({k: positioning[k] for k in ('no_fit', 'loss_reason', 'angle', 'evidence', 'confidence')}, indent=2, ensure_ascii=False)}\n</agent_output>")
    return llm.structured(llm.JUDGE_MODEL, SYSTEM, user, Verdict, effort="medium").model_dump()
