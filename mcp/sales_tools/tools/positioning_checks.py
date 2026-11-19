"""Positioning checks with no dependencies, so the MCP tool and the local evals use the same rules. Owner: Gyula."""


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _is_placeholder(s: str) -> bool:
    return not s.strip() or s.strip().upper().startswith(("TODO", "STUB"))


def check_positioning(lead: dict, angle: str, evidence: list[str], confidence: float) -> list[str]:
    """Return a list of problems; empty means the angle can be saved."""
    problems = []
    if not isinstance(angle, str) or _is_placeholder(angle):
        problems.append("angle is empty or a placeholder")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        problems.append("confidence must be a number between 0 and 1")
    facts = [_norm(f.get("fact", "")) for f in (lead.get("research") or {}).get("facts", []) if isinstance(f, dict)]
    if not facts:
        problems.append("lead has no research.facts to position on; run research first")
    evidence = [e for e in (evidence or []) if isinstance(e, str) and e.strip()]
    if not evidence:
        problems.append("evidence needs at least one fact from research.facts")
    for e in evidence:
        ne = _norm(e)
        if facts and not any(ne in f or f in ne for f in facts):
            problems.append(f"evidence not found in research.facts: {e!r}")
    return problems
