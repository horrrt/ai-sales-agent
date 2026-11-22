"""Store for the sandbox tool server: the lead sheet, the event log, reps, approvals and the wait queue.

Owner: Ayaan. Still in memory, with an optional JSON snapshot so a local server keeps its state across
restarts: set SALES_STATE=path/to/state.json. The snapshot is written after every tool call (see
server.py). On Cloud Run the filesystem is wiped on restart, so a deployed server still needs a real
database (docs/fleet-prompts-v2.md §2).

- SOURCE: candidate leads the lead search can find (the personas with SALES_LEADS=personas, otherwise
  data/sample_leads.json). Not the record: a candidate only becomes a lead through add_lead.
- LEADS: the lead sheet, keyed by lead_id. add_lead skips duplicates by email and domain.
- INTERACTIONS: the event log, append-only.
- REPS: the sales team and their traffic lights (data/reps.json, else data/reps.demo.json).
- APPROVALS: one per draft, written by record_approval; send_email checks it.
- OUTBOX: every email sent to a sandbox inbox, with the prospect's reply once there is one.
- QUEUE: positive replies waiting for a rep when every rep is red.
- PROPOSALS: rule changes the weekly review proposes, for a person to approve.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import sim

DATA = Path(__file__).resolve().parents[2] / "data"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(*names: str):
    for name in names:
        p = DATA / name
        if p.exists():
            return json.loads(p.read_text())
    raise FileNotFoundError(f"none of {names} in {DATA}")


# SALES_LEADS=personas: the simulated prospects are the lead source (see sim.py).
SOURCE: dict[str, dict] = sim.leads() if sim.ENABLED else {l["lead_id"]: l for l in _load_json("sample_leads.json")}

_brief_file = DATA / "customer_brief.json"
if not _brief_file.exists():  # personas are written for the demo customer; otherwise the TODO example
    _brief_file = DATA / ("customer_brief.demo.json" if sim.ENABLED else "customer_brief.example.json")
BRIEF: dict = json.loads(_brief_file.read_text())

LEADS: dict[str, dict] = {}
INTERACTIONS: list[dict] = []
REPS: dict[str, dict] = _load_json("reps.json", "reps.demo.json")
APPROVALS: dict[str, dict] = {}
OUTBOX: list[dict] = []
QUEUE: list[dict] = []
PROPOSALS: list[dict] = []

STATE_FILE = os.environ.get("SALES_STATE")
_STATE = {"leads": LEADS, "interactions": INTERACTIONS, "reps": REPS, "approvals": APPROVALS,
          "outbox": OUTBOX, "queue": QUEUE, "proposals": PROPOSALS}


def load() -> None:
    """Load the snapshot into the live objects (in place, so every module keeps its reference)."""
    if not STATE_FILE or not Path(STATE_FILE).exists():
        return
    saved = json.loads(Path(STATE_FILE).read_text())
    for key, obj in _STATE.items():
        if key in saved:
            obj.clear()
            (obj.update if isinstance(obj, dict) else obj.extend)(saved[key])
    # Test fixtures (evals/fleet) can also set the lead source and change the brief. Neither is saved back.
    if "source" in saved:
        SOURCE.clear()
        SOURCE.update(saved["source"])
    for k, v in (saved.get("brief") or {}).items():
        BRIEF[k] = {**BRIEF[k], **v} if isinstance(v, dict) and isinstance(BRIEF.get(k), dict) else v


def save() -> None:
    """Write the snapshot atomically (temp file, then rename), so a crash never leaves half a file."""
    if not STATE_FILE:
        return
    path = Path(STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(_STATE, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def set_stage(lead: dict, stage: str) -> None:
    """Move a lead to a stage and remember when it first got there."""
    lead["stage"] = stage
    lead.setdefault("stage_times", {}).setdefault(stage, now())
    lead["updated_at"] = now()


def log_event(lead_id: str, stage: str, outcome: str, message: str = "", channel: str = "internal",
              loss_reason: str | None = None, **extra) -> dict:
    """Append one row to the event log. Tools call this for facts; agents call log_interaction for decisions."""
    entry = {"timestamp": now(), "lead_id": lead_id, "stage": stage, "channel": channel, "message": message,
             "outcome": outcome, "loss_reason": loss_reason, "prompt_version": extra.pop("prompt_version", None),
             "variant": extra.pop("variant", None), "trace_id": None, "by": extra.pop("by", "tool"), **extra}
    INTERACTIONS.append(entry)
    if lead_id in LEADS:
        LEADS[lead_id]["updated_at"] = entry["timestamp"]
    return entry


load()
