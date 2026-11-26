"""LangSmith traces: tool runs, trace_id in the event log, outcome feedback. No network: LangSmith is faked.

    cd mcp && uv run --extra dev pytest -q tests/test_tracing.py
"""
import contextlib
import itertools

import pytest

from sales_tools import decide as decide_mod
from sales_tools import store, tracing
from sales_tools.tools import deals, handoff, onboarding, outreach, positioning, research, shared  # noqa: F401  (registers every tool)


class FakeRun:
    def __init__(self, trace_id):
        self.trace_id, self.ended = trace_id, None

    def end(self, outputs=None, error=None):
        self.ended = {"outputs": outputs, "error": error}


class FakeLangSmith:
    """Stands in for both the run API (_open_run) and the client (create_feedback)."""

    def __init__(self):
        self.runs, self.feedback, self._ids = [], [], (f"trace-{i}" for i in itertools.count(1))

    @contextlib.contextmanager
    def open_run(self, name, inputs, parent, metadata):
        run = FakeRun(next(self._ids))
        self.runs.append({"name": name, "inputs": inputs, "parent": parent, "metadata": metadata, "run": run})
        yield run

    def create_feedback(self, **kw):
        self.feedback.append(kw)


@pytest.fixture
def ls(monkeypatch):
    fake = FakeLangSmith()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(tracing, "_open_run", fake.open_run)
    monkeypatch.setattr(tracing, "_client", lambda: fake)
    monkeypatch.setattr(tracing, "_submit", lambda fn: fn())  # post feedback right away, not on a thread
    monkeypatch.setattr(tracing, "_incoming_headers", lambda: None)
    return fake


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    saved = {k: dict(v) for k, v in (("leads", store.LEADS),)}
    store.LEADS.clear()
    store.INTERACTIONS.clear()
    store.CLOCK["now"] = "2026-11-24T10:00:00+01:00"
    store.LEADS["L-1"] = {"lead_id": "L-1", "customer_id": store.BRIEF.get("customer_id"), "stage": "sent", "status": "active",
                          "person": {"name": "Ingrid"}, "company": {"name": "Nordvik"}}
    yield
    store.LEADS.clear()
    store.LEADS.update(saved["leads"])
    store.INTERACTIONS.clear()
    store.CLOCK.clear()
    decide_mod.BACKEND = None


def test_tracing_off_changes_nothing(monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    opened = []
    monkeypatch.setattr(tracing, "_open_run", lambda *a: opened.append(a))
    tool = tracing.traced("log_interaction", shared.log_interaction)
    assert tool(lead_id="L-1", stage="research", outcome="success", message="ok")["ok"] is True
    assert opened == []
    assert store.INTERACTIONS[-1]["trace_id"] is None


def test_each_tool_call_is_a_run_and_its_rows_carry_the_trace_id(ls):
    tool = tracing.traced("log_interaction", shared.log_interaction)
    tool(lead_id="L-1", stage="research", outcome="success", message="two facts", prompt_version="v2")
    tracing.traced("tag_loss_reason", shared.tag_loss_reason)(lead_id="L-1", reason="price", evidence="too expensive")
    assert [r["name"] for r in ls.runs] == ["log_interaction", "tag_loss_reason"]
    run = ls.runs[0]
    assert run["inputs"]["lead_id"] == "L-1"
    assert run["metadata"]["thread_id"] == "lead-L-1"  # a lead's calls group as one thread in LangSmith
    assert run["run"].ended["outputs"] == {"output": {"ok": True}}
    assert [r["trace_id"] for r in store.INTERACTIONS] == ["trace-1", "trace-2"]
    assert tracing.current_trace_id() is None  # reset after the call


def test_incoming_trace_headers_make_the_tool_a_child_of_the_agent_trace(ls, monkeypatch):
    headers = {"langsmith-trace": "20261124T090000000000Zabc.20261124T090000000001Zdef", "baggage": "x=1"}
    monkeypatch.setattr(tracing, "_incoming_headers", lambda: headers)
    tracing.traced("log_interaction", shared.log_interaction)(lead_id="L-1", stage="research", outcome="success")
    assert ls.runs[0]["parent"] == headers
    assert "thread_id" not in ls.runs[0]["metadata"]  # the agent's trace already groups the calls


def test_outcomes_become_feedback_on_every_trace_that_worked_on_the_lead(ls):
    log = tracing.traced("log_interaction", shared.log_interaction)
    log(lead_id="L-1", stage="research", outcome="success", message="researched")
    log(lead_id="L-1", stage="positioning", outcome="success", message="angle saved")
    assert ls.feedback == []  # research and positioning are no outcome
    tracing.traced("tag_loss_reason", shared.tag_loss_reason)(lead_id="L-1", reason="has_competitor", evidence="uses Vanta")
    assert {f["trace_id"] for f in ls.feedback} == {"trace-1", "trace-2", "trace-3"}
    assert {(f["key"], f["value"], f["score"]) for f in ls.feedback} == {("lost", "has_competitor", 0.0)}
    assert all(f["run_id"] == f["trace_id"] and f["source_info"]["lead_id"] == "L-1" for f in ls.feedback)


def test_feedback_only_goes_to_traces_of_the_same_lead(ls):
    store.LEADS["L-2"] = {**store.LEADS["L-1"], "lead_id": "L-2"}
    log = tracing.traced("log_interaction", shared.log_interaction)
    log(lead_id="L-2", stage="research", outcome="success")
    log(lead_id="L-1", stage="approval", outcome="success", message="approved")
    assert {f["trace_id"] for f in ls.feedback} == {"trace-2"}


@pytest.mark.parametrize("row, expected", [
    ({"stage": "approval", "outcome": "success", "message": "approved"}, [("email_approval", 1.0, "approved")]),
    ({"stage": "approval", "outcome": "success", "message": "approved with edits"}, [("email_approval", 0.5, "edited")]),
    ({"stage": "approval", "outcome": "failure", "message": "rejected: too long"}, [("email_approval", 0.0, "rejected")]),
    ({"stage": "approval", "outcome": "success", "message": "quote approved by U1"}, []),
    ({"stage": "reply", "outcome": "success", "message": "positive reply", "decision": {"answer": "positive"}},
     [("reply", 1.0, "positive")]),
    ({"stage": "reply", "outcome": "failure", "message": "negative: we use Vanta", "loss_reason": "has_competitor",
      "decision": {"answer": "negative"}}, [("reply", 0.0, "negative"), ("lost", 0.0, "has_competitor")]),
    ({"stage": "reply", "outcome": "skipped", "message": "out_of_office: follow up on 2026-12-01"}, [("reply", None, "out_of_office")]),
    ({"stage": "reply", "outcome": "success", "message": "labelled not_now"}, [("reply", 0.25, "not_now")]),
    ({"stage": "handoff", "outcome": "success", "message": "meeting booked with rep-1 at 2026-11-25T09:00"}, [("meeting_booked", 1.0, None)]),
    ({"stage": "handoff", "outcome": "success", "message": "meeting held"}, [("meeting_held", 1.0, None)]),
    ({"stage": "handoff", "outcome": "failure", "message": "meeting no_show"}, [("meeting_held", 0.0, "no_show")]),
    ({"stage": "handoff", "outcome": "success", "message": "assigned to rep-1 (Mette Holm)"}, []),
    ({"stage": "deal", "outcome": "success", "message": "won: EUR 1,188.00."}, [("deal_won", 1.0, None)]),
    ({"stage": "research", "outcome": "success", "message": "two facts"}, []),
])
def test_feedback_for_maps_outcome_rows(row, expected):
    assert [(f["key"], f.get("score"), f.get("value")) for f in tracing.feedback_for(row)] == expected


def test_a_langsmith_failure_never_breaks_a_tool(ls, monkeypatch):
    def broken(*a):
        raise ConnectionError("LangSmith unreachable")

    monkeypatch.setattr(tracing, "_open_run", broken)
    out = tracing.traced("log_interaction", shared.log_interaction)(lead_id="L-1", stage="research", outcome="success")
    assert out["ok"] is True
    assert store.INTERACTIONS[-1]["trace_id"] is None


def test_a_tool_error_is_recorded_on_its_run_and_still_raised(ls):
    def boom(**kw):
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        tracing.traced("boom", boom)(lead_id="L-1")
    assert "ValueError: bad input" in ls.runs[0]["run"].ended["error"]


def test_every_registered_tool_is_traced(ls):
    from sales_tools.app import mcp

    class Server:  # a stand-in with the same tool manager, so the real server's tools stay untouched
        class _tool_manager:
            @staticmethod
            def list_tools():
                return tools

    from types import SimpleNamespace

    tools = [SimpleNamespace(name=t.name, fn=t.fn) for t in mcp._tool_manager.list_tools()]
    tracing.trace_each_tool(Server)
    names = {t.name for t in tools}
    assert {"add_lead", "send_email", "read_reply", "book_meeting", "log_interaction"} <= names
    decide_mod.BACKEND = lambda q, e, o: {"answer": True, "confidence": 1, "reason": "stub", "backend": "stub"}
    store.SOURCE["L-9"] = {"lead_id": "L-9", "customer_id": store.BRIEF.get("customer_id"),
                           "person": {"name": "Ola", "email": "ola@arctic.sandbox.test"},
                           "company": {"name": "Arctic", "domain": "arctic.example", "country": "NO", "employees": 30,
                                       "industry": "B2B SaaS"}}
    try:
        add_lead = next(t for t in tools if t.name == "add_lead").fn
        assert add_lead(lead_id="L-9")["status"] == "added"
    finally:
        store.SOURCE.pop("L-9", None)
    assert ls.runs[-1]["name"] == "add_lead"
    assert store.INTERACTIONS[-1]["trace_id"] == ls.runs[-1]["run"].trace_id
