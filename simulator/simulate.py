"""Simulated prospect replies. Owner: Gyula.

STUB: returns a canned reply. Replace with an LLM call that plays the persona.
"""
import json
import sys
from pathlib import Path


def reply(persona: dict, email_subject: str, email_body: str) -> dict:
    """Return the persona's reply and the label it intended (ground truth for evals)."""
    return {
        "body": f"STUB reply from {persona['name']}: thanks, tell me more.",
        "intended_label": persona.get("intended_reply", "positive"),
    }


if __name__ == "__main__":
    persona = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps(reply(persona, "Test subject", "Test body"), indent=2))
