"""LangSmith traces for the tool server: every tool call is a run, every event-log row knows its trace,
and outcomes come back to the traces as feedback. Owner: Ayaan (stream 4, docs/fleet-prompts-v2.md §3.2).

On when LANGSMITH_TRACING=true and LANGSMITH_API_KEY are set (LANGSMITH_PROJECT picks the project,
default "sales-agent"). Off, every function here is a no-op and the tools behave exactly as before.

1. Runs. trace_each_tool() wraps every tool. A call becomes a "tool" run with its inputs, output and errors.
   If the request carries LangSmith's distributed-tracing headers (langsmith-trace, baggage), the run is a
   child of the agent's trace, so Fleet's reasoning and our tool calls are one trace. Without them, each call
   is its own trace, with metadata thread_id=lead-<lead_id> so LangSmith's Threads view shows a lead's whole
   journey across calls.
2. trace_id. store.log_event writes current_trace_id() into every event-log row, so the event log (the
   System of Record) and LangSmith join on trace_id as well as lead_id.
3. Feedback. store.log_event hands every new row to on_event(). Rows that record an outcome (email approved,
   edited or rejected; reply label; meeting booked or held; lost with a reason; deal won) become feedback on
   every trace that worked on that lead, so a trace from the research or positioning step later shows
   whether it led to a meeting. That is what the hill-climbing loop filters on.

Tracing never breaks a tool: any LangSmith error is printed to stderr and the tool carries on.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterator

PROJECT = os.environ.get("LANGSMITH_PROJECT", "sales-agent")
MAX_TRACES_PER_LEAD = 25  # feedback goes to at most this many of a lead's most recent traces

_CURRENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("sales_tools_trace_id", default=None)
_CLIENT: Any = None
_POOL: ThreadPoolExecutor | None = None


def enabled() -> bool:
    """LangSmith is configured. Read on every call, so tests and a running server can switch it."""
    on = os.environ.get("LANGSMITH_TRACING", os.environ.get("LANGCHAIN_TRACING_V2", "")).lower() in ("1", "true", "yes")
    return on and bool(os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"))


def current_trace_id() -> str | None:
    """The trace the running tool call belongs to, or None outside a traced call."""
    return _CURRENT.get()


# ---- runs -------------------------------------------------------------------------------------------------

def _incoming_headers() -> dict | None:
    """LangSmith trace headers on the HTTP request that called this tool, if the caller sent any."""
    try:
        from .app import mcp

        request = mcp.get_context().request_context.request
        headers = {k.lower(): v for k, v in (request.headers.items() if request is not None else [])}
    except Exception:  # noqa: BLE001  no request (a test, stdio): no parent
        return None
    if "langsmith-trace" not in headers:
        return None
    return {k: headers[k] for k in ("langsmith-trace", "baggage") if k in headers}


@contextlib.contextmanager
def _open_run(name: str, inputs: dict, parent: dict | None, metadata: dict) -> Iterator[Any]:
    """Open a LangSmith tool run; yields an object with .trace_id and .end(outputs=..., error=...).
    Tests replace this function with a fake."""
    import langsmith as ls

    with ls.trace(name, "tool", inputs=inputs, project_name=PROJECT, parent=parent, metadata=metadata,
                  tags=["sales-tools"]) as run:
        yield run


def _metadata(tool: str, kwargs: dict, parent: dict | None) -> dict:
    md = {"tool": tool, "server": "sales-tools"}
    for k in ("lead_id", "by", "rep_id"):
        if kwargs.get(k):
            md[k] = kwargs[k]
    if kwargs.get("lead_id") and parent is None:
        md["thread_id"] = f"lead-{kwargs['lead_id']}"  # groups a lead's calls in LangSmith's Threads view
    return md


def traced(name: str, fn: Callable) -> Callable:
    """fn, run inside a LangSmith tool run when tracing is on."""

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        if not enabled():
            return fn(*args, **kwargs)
        parent = _incoming_headers()
        try:
            cm = _open_run(name, dict(kwargs), parent, _metadata(name, kwargs, parent))
            run = cm.__enter__()
        except Exception as e:  # noqa: BLE001  LangSmith down or misconfigured: run the tool untraced
            print(f"[tracing] could not open a run for {name}: {e}", file=sys.stderr)
            return fn(*args, **kwargs)
        token = _CURRENT.set(str(run.trace_id))
        try:
            out = fn(*args, **kwargs)
        except BaseException as e:
            _CURRENT.reset(token)
            _close(cm, run, error=e)
            raise
        _CURRENT.reset(token)
        _close(cm, run, outputs={"output": out})
        return out

    return wrapped


def _close(cm, run, outputs: dict | None = None, error: BaseException | None = None) -> None:
    try:
        if error is not None:
            run.end(error=f"{type(error).__name__}: {error}")
        else:
            run.end(outputs=outputs)
        cm.__exit__(None, None, None)
    except Exception as e:  # noqa: BLE001
        print(f"[tracing] could not close run: {e}", file=sys.stderr)


def trace_each_tool(server) -> None:
    """Wrap every registered tool of a FastMCP server in traced(). Call once, after the tools are imported."""
    for tool in server._tool_manager.list_tools():
        tool.fn = traced(tool.name, tool.fn)


# ---- feedback ---------------------------------------------------------------------------------------------

_REPLY_LABELS = {"positive": 1.0, "meeting_change": 1.0, "handover": 0.75, "not_now": 0.25, "out_of_office": None,
                 "negative": 0.0, "wrong_person": 0.0, "unsubscribe": 0.0}


def feedback_for(entry: dict) -> list[dict]:
    """The feedback an event-log row stands for: [{key, score, value, comment}], or [] for rows that are no outcome.

    Keys: email_approval (1 approved, 0.5 edited, 0 rejected), reply (score by label, value the label),
    meeting_booked, meeting_held (1 held, 0 no-show or cancelled), lost (value the loss reason), deal_won.
    """
    stage, outcome, msg = entry.get("stage"), entry.get("outcome"), (entry.get("message") or "")
    out: list[dict] = []
    if stage == "approval" and not msg.startswith("quote"):
        if outcome == "failure":
            out.append({"key": "email_approval", "score": 0.0, "value": "rejected", "comment": msg[:300]})
        elif msg.startswith("approved with edits"):
            out.append({"key": "email_approval", "score": 0.5, "value": "edited"})
        elif msg.startswith("approved"):
            out.append({"key": "email_approval", "score": 1.0, "value": "approved"})
    elif stage == "reply":
        label = _reply_label(entry)
        if label:
            out.append({"key": "reply", "score": _REPLY_LABELS.get(label), "value": label})
    elif stage == "handoff":
        if msg.startswith("meeting booked"):
            out.append({"key": "meeting_booked", "score": 1.0})
        elif msg == "meeting held":
            out.append({"key": "meeting_held", "score": 1.0})
        elif msg.startswith("meeting ") and outcome == "failure":
            out.append({"key": "meeting_held", "score": 0.0, "value": msg[len("meeting "):].split(" ")[0]})
    elif stage == "deal" and outcome == "success" and (msg.startswith("won:") or msg.startswith("paid: closed")):
        out.append({"key": "deal_won", "score": 1.0, "comment": msg[:300]})
    if entry.get("loss_reason"):
        out.append({"key": "lost", "score": 0.0, "value": entry["loss_reason"], "comment": msg[:300]})
    return out


def _reply_label(entry: dict) -> str | None:
    """The reply label behind a reply row: the gate's answer if it was saved, else read from the message."""
    d = entry.get("decision") or {}
    if isinstance(d, dict) and isinstance(d.get("answer"), str):
        return d["answer"]
    msg = (entry.get("message") or "").lower()
    if msg.startswith("labelled "):
        return msg.split()[1]
    for label, words in (("positive", "positive reply"), ("meeting_change", "change the meeting"),
                         ("handover", "handover"), ("not_now", "not_now"), ("out_of_office", "out_of_office"),
                         ("unsubscribe", "unsubscribe"), ("wrong_person", "wrong_person"), ("negative", "negative")):
        if words in msg:
            return label
    return None


def lead_traces(lead_id: str, history: list[dict]) -> list[str]:
    """The traces that worked on a lead, most recent last, without repeats."""
    seen: dict[str, None] = {}
    for row in history:
        if row.get("lead_id") == lead_id and row.get("trace_id"):
            seen.pop(row["trace_id"], None)
            seen[row["trace_id"]] = None
    return list(seen)[-MAX_TRACES_PER_LEAD:]


def _client():
    global _CLIENT
    if _CLIENT is None:
        from langsmith import Client

        _CLIENT = Client()
    return _CLIENT


def _submit(fn: Callable[[], Any]) -> None:
    """Run fn off the tool's thread, so a slow LangSmith never slows a tool down."""
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ls-feedback")
    _POOL.submit(fn)


def on_event(entry: dict, history: list[dict]) -> None:
    """Called by store.log_event for every new row: post its outcome as feedback on the lead's traces."""
    if not enabled():
        return
    items = feedback_for(entry)
    traces = lead_traces(entry.get("lead_id"), history)
    if not items or not traces:
        return

    def post():
        for trace_id in traces:
            for f in items:
                try:
                    _client().create_feedback(run_id=trace_id, trace_id=trace_id, key=f["key"], score=f.get("score"),
                                              value=f.get("value"), comment=f.get("comment"),
                                              source_info={"lead_id": entry.get("lead_id"), "stage": entry.get("stage"),
                                                           "event_at": entry.get("timestamp")})
                except Exception as e:  # noqa: BLE001
                    print(f"[tracing] feedback {f['key']} on {trace_id} failed: {e}", file=sys.stderr)

    _submit(post)
