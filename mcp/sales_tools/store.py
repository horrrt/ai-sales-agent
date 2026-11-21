"""In-memory store for leads, interactions and the customer brief.

Stub for week 1: loads data/sample_leads.json and keeps everything in memory.
Owner: Ayaan. Swap for a real database (e.g. SQLite or Supabase) in week 2.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import sim

DATA = Path(__file__).resolve().parents[2] / "data"

# SALES_LEADS=personas swaps the sample leads for the simulated prospects (see sim.py).
LEADS: dict[str, dict] = sim.leads() if sim.ENABLED else {
    l["lead_id"]: l for l in json.loads((DATA / "sample_leads.json").read_text())}
INTERACTIONS: list[dict] = []

_brief_file = DATA / "customer_brief.json"
if not _brief_file.exists():  # personas are written for the demo customer; otherwise the TODO example
    _brief_file = DATA / ("customer_brief.demo.json" if sim.ENABLED else "customer_brief.example.json")
BRIEF: dict = json.loads(_brief_file.read_text())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()
