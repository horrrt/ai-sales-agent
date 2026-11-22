"""Yes/no and multiple-choice decisions: the green "d1" boxes in the pipeline diagram.

Every gate in the pipeline asks one question about one piece of evidence and gets back a fixed
answer, never free text. Backends, in order:

1. Liquid d1 (LIQUID_API_KEY set). A decision model that answers in one forward pass and
   generates no text: https://docs.liquid.ai/lfm/models/d1. Model d1.2 (plain d1 is deprecated).
2. Claude (ANTHROPIC_API_KEY set), a small model with a strict structured answer. A stand-in until
   the team has a Liquid key, and a baseline to compare d1 against.
3. None: returns answer=None, and the calling tool sends the lead to a human instead of guessing.

Each result records which backend answered, so d1 and Claude can be compared on the same gates.
Tests replace BACKEND with a stub.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable

LIQUID_URL = os.environ.get("LIQUID_DECISIONS_URL", "https://api.liquid.ai/decisions/v1/systemone")
D1_MODEL = os.environ.get("D1_MODEL", "d1.2")
CLAUDE_MODEL = os.environ.get("DECIDE_CLAUDE_MODEL", "claude-haiku-5-5")


def _evidence_text(evidence: Any) -> str:
    return evidence if isinstance(evidence, str) else json.dumps(evidence, ensure_ascii=False, default=str)


def _d1(question: str, evidence: Any, options: dict[str, str] | None) -> dict:
    q = ({"type": "choice", "instructions": question, "criteria": options} if options
         else {"type": "noul", "instructions": question})
    body = json.dumps({"model": D1_MODEL, "state": _evidence_text(evidence), "questions": {"q": q}}).encode()
    req = urllib.request.Request(LIQUID_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {os.environ['LIQUID_API_KEY']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        a = json.loads(r.read())["answers"]["q"]
    if options:
        return {"answer": a["choice"], "confidence": a.get("confidence"), "reason": "", "backend": f"liquid:{D1_MODEL}"}
    p = float(a["noul"])  # probability of yes
    return {"answer": p >= 0.5, "confidence": max(p, 1 - p), "reason": "", "backend": f"liquid:{D1_MODEL}"}


def _claude(question: str, evidence: Any, options: dict[str, str] | None) -> dict:
    import anthropic

    if options:
        schema = {"type": "object", "additionalProperties": False, "required": ["answer", "reason"],
                  "properties": {"answer": {"type": "string", "enum": list(options)}, "reason": {"type": "string"}}}
        ask = "Pick exactly one option:\n" + "\n".join(f"- {k}: {v}" for k, v in options.items())
    else:
        schema = {"type": "object", "additionalProperties": False, "required": ["answer", "reason"],
                  "properties": {"answer": {"type": "boolean"}, "reason": {"type": "string"}}}
        ask = "Answer true (yes) or false (no)."
    msg = anthropic.Anthropic().messages.create(
        model=CLAUDE_MODEL, max_tokens=2000,
        system="You answer one decision question about the evidence given. Use only the evidence. "
               "Give a one-sentence reason.",
        messages=[{"role": "user", "content": f"<evidence>\n{_evidence_text(evidence)}\n</evidence>\n\n"
                                              f"Question: {question}\n{ask}"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    )
    if msg.stop_reason == "refusal":
        return {"answer": None, "confidence": None, "reason": "model declined", "backend": f"claude:{CLAUDE_MODEL}"}
    text = "".join(b.text for b in msg.content if b.type == "text")
    out = json.loads(text)
    return {"answer": out["answer"], "confidence": None, "reason": out["reason"], "backend": f"claude:{CLAUDE_MODEL}"}


def _none(question: str, evidence: Any, options: dict[str, str] | None) -> dict:
    return {"answer": None, "confidence": None, "reason": "no decision backend configured", "backend": "none"}


def _pick_backend() -> Callable[[str, Any, dict | None], dict]:
    if os.environ.get("LIQUID_API_KEY"):
        return _d1
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return _claude
    return _none


BACKEND: Callable[[str, Any, dict | None], dict] | None = None  # tests set this


def decide(question: str, evidence: Any, options: dict[str, str] | None = None) -> dict:
    """Ask one yes/no question (options=None) or one multiple-choice question about the evidence.

    Returns {"answer": bool | option key | None, "confidence", "reason", "backend"}. answer is None when
    no backend could decide; callers must then route the lead to a human, never guess.
    """
    fn = BACKEND or _pick_backend()
    try:
        return fn(question, evidence, options)
    except Exception as e:  # noqa: BLE001  a failed decision goes to a human, it never blocks the pipeline
        return {"answer": None, "confidence": None, "reason": f"{type(e).__name__}: {e}"[:300], "backend": "error"}
