"""Shared Anthropic client for the simulator and local evals. Owner: Gyula.

Reads ANTHROPIC_API_KEY (and optional LANGSMITH_API_KEY) from the environment or the repo's .env.
If LANGSMITH_API_KEY is set, every call is traced to the LangSmith project in LANGSMITH_PROJECT.

Models (override with env vars):
- POSITIONER_MODEL / DRAFTER_MODEL: the agent side (default claude-opus-5-5, same as the Fleet agent)
- PERSONA_MODEL: the simulated prospects. Deliberately a different model from the agent side,
  so the A/B result isn't one model rewarding its own writing style.
- JUDGE_MODEL: the LLM-as-judge for positioning quality.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_load_dotenv()

POSITIONER_MODEL = os.environ.get("POSITIONER_MODEL", "claude-opus-5-5")
DRAFTER_MODEL = os.environ.get("DRAFTER_MODEL", "claude-opus-5-5")
PERSONA_MODEL = os.environ.get("PERSONA_MODEL", "claude-sonnet-5-5")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-sonnet-5-5")

T = TypeVar("T", bound=BaseModel)
_client = None


def has_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def client():
    global _client
    if _client is None:
        import anthropic

        c = anthropic.Anthropic()
        if os.environ.get("LANGSMITH_API_KEY"):
            os.environ.setdefault("LANGSMITH_TRACING", "true")
            os.environ.setdefault("LANGSMITH_PROJECT", "sales-agent")
            from langsmith.wrappers import wrap_anthropic

            c = wrap_anthropic(c)
        _client = c
    return _client


class Refused(RuntimeError):
    pass


def structured(model: str, system: str, user: str, schema: type[T], effort: str = "low", max_tokens: int = 4000) -> T:
    """One call, parsed into a pydantic model. Raises Refused if the model declined."""
    response = client().messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
        output_config={"effort": effort},
    )
    if response.stop_reason == "refusal":
        raise Refused(f"{model} declined: {getattr(response, 'stop_details', None)}")
    if response.parsed_output is None:
        raise RuntimeError(f"{model} returned no parsable output (stop_reason={response.stop_reason})")
    return response.parsed_output
