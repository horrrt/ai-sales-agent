"""Simulated prospect replies. Owner: Gyula (stream 5).

A persona file (simulator/personas/P-*.json) has two halves:
- lead: what our pipeline can know (person, company, public research facts)
- private: what only the prospect knows (real situation, pains, timing, budget, objections)

The persona LLM sees both halves plus the email, never which A/B arm wrote it, and decides how a
real person in that situation would reply. The persona prompt deliberately says nothing about templates
vs personalisation, so the A/B result isn't written into the instructions. A few personas have a fixed outcome (out of office,
wrong person) that doesn't depend on the email.

    uv run --project simulator python simulator/simulate.py simulator/personas/P-001.json
    uv run --project simulator python simulator/simulate.py simulator/personas/P-001.json --offline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402

PERSONA_DIR = Path(__file__).resolve().parent / "personas"

# Must match outreach.reply_label in schemas/lead.schema.json (JD's classifier labels).
ReplyLabel = Literal["positive", "negative", "not_now", "wrong_person", "out_of_office", "unsubscribe"]
REPLY_LABELS: tuple[str, ...] = ReplyLabel.__args__  # type: ignore[attr-defined]
# Simulator-only outcome: the prospect ignores the email. Never a reply_label; maps to loss reason no_reply.
SimOutcome = Literal["positive", "negative", "not_now", "wrong_person", "out_of_office", "unsubscribe", "no_reply"]


class SimReply(BaseModel):
    reasoning: str = Field(description="One or two sentences, in character: why you react this way to this email")
    label: SimOutcome = Field(description="The kind of reply you send, or no_reply if you would ignore the email")
    body: str = Field(description="The reply email body exactly as you would send it; empty if no_reply")


SYSTEM = """You play one real prospect receiving a cold B2B sales email. You are not an assistant and you are not helpful to the sender by default.

Decide how this person, with this situation, would actually react to this specific email, then write the reply they would send.

How real prospects behave:
- Most cold emails get ignored. If this person would not bother replying, choose no_reply and leave the body empty.
- Reply positively only if the email clearly connects to a problem you really have right now, and the ask is easy.
- Hype, invented facts about you, or claims that are wrong for your situation make you more negative.
- If you already use or are evaluating an alternative, say so and decline.
- If the timing is wrong, say when to come back (label not_now).
- If this isn't your area, point to the right person (label wrong_person).
- If the email is irrelevant or annoying to you, a short no (negative) or a request to stop emailing (unsubscribe).
- "positive" means you agree to a call or explicitly ask for one. Questions without agreeing to talk are "not_now" if timing is the issue, otherwise "negative" unless the interest is clear.

Write in the person's reply style. Keep it as short as a busy person would. Sign with your first name."""

FIXED = {
    "out_of_office": lambda p: (
        f"Thank you for your email. I am on leave until January and have limited access to email. "
        f"For urgent matters please contact info@{p['lead']['company']['domain'].split('.')[0]}.sandbox.test.\n\n"
        f"{p['lead']['person']['name'].split()[0]}"
    ),
    "wrong_person": lambda p: (
        f"Hi, thanks, but this isn't something I handle. Security and compliance sit with our CTO. "
        f"You could try them instead.\n\n{p['lead']['person']['name'].split()[0]}"
    ),
}


def load_persona(path_or_id: str) -> dict:
    p = Path(path_or_id)
    if not p.exists():
        p = PERSONA_DIR / f"{path_or_id}.json"
    return json.loads(p.read_text())


def all_personas() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(PERSONA_DIR.glob("P-*.json"))]


def _persona_prompt(persona: dict, subject: str, body: str) -> str:
    lead, private = persona["lead"], persona["private"]
    me = {
        "name": lead["person"]["name"],
        "title": lead["person"]["title"],
        "company": lead["company"]["name"],
        "company_size": lead["company"]["employees"],
        "country": lead["company"]["country"],
        "what_the_company_does": lead["research"]["summary"],
        "public_facts_about_us": [f["fact"] for f in lead["research"]["facts"]],
        "my_real_situation": private["situation"],
        "my_pains": private["pains"],
        "what_we_use_today": private["current_tools"],
        "timing": private["timing"],
        "budget_and_authority": private["budget"],
        "objections_i_would_have": private["objections"],
        "reply_style": private["reply_style"],
    }
    return (
        f"<who_you_are>\n{json.dumps(me, indent=2, ensure_ascii=False)}\n</who_you_are>\n\n"
        f"<email_you_received>\nSubject: {subject}\n\n{body}\n</email_you_received>\n\n"
        "Reply as this person."
    )


def reply(persona: dict, email_subject: str, email_body: str, offline: bool = False) -> dict:
    """Return the persona's reply and the label it intended (ground truth for JD's reply-classification eval).

    Keys: body, intended_label, reasoning, source ("fixed" | "llm" | "stub"). intended_label is one of
    REPLY_LABELS, or "no_reply" when the prospect ignores the email (no reply exists to classify). Only "llm" and "fixed" replies
    are real results; "stub" is a plumbing smoke test and must never be reported as a reply rate.
    """
    fixed = persona["private"].get("fixed_reply")
    if fixed:
        return {"body": FIXED[fixed](persona), "intended_label": fixed, "reasoning": "fixed outcome", "source": "fixed"}
    if offline or not llm.has_key():
        return {"body": f"STUB reply from {persona['lead']['person']['name']}.", "intended_label": "not_now",
                "reasoning": "offline stub, no model call", "source": "stub"}
    r = llm.structured(llm.PERSONA_MODEL, SYSTEM, _persona_prompt(persona, email_subject, email_body), SimReply, effort="medium")
    return {"body": "" if r.label == "no_reply" else r.body, "intended_label": r.label, "reasoning": r.reasoning, "source": "llm"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("persona", help="persona file or id, e.g. P-001")
    ap.add_argument("--subject", default="Quick question")
    ap.add_argument("--body", default="Hi, we help B2B SaaS companies get audit-ready for SOC 2 in 8 weeks. Open to a 20-minute call?")
    ap.add_argument("--offline", action="store_true", help="no model call (smoke test)")
    a = ap.parse_args()
    print(json.dumps(reply(load_persona(a.persona), a.subject, a.body, offline=a.offline), indent=2, ensure_ascii=False))
