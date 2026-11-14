"""In-memory store for leads, interactions and the customer brief.

Stub for week 1: loads data/sample_leads.json and keeps everything in memory.
Owner: Ayaan. Swap for a real database (e.g. SQLite or Supabase) in week 2.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"

LEADS: dict[str, dict] = {l["lead_id"]: l for l in json.loads((DATA / "sample_leads.json").read_text())}
INTERACTIONS: list[dict] = []

_brief_file = DATA / "customer_brief.json"
BRIEF: dict = json.loads((_brief_file if _brief_file.exists() else DATA / "customer_brief.example.json").read_text())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()
