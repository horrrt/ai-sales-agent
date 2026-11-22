"""A fake prospect on real email: answers the agent's OpenMail emails with every kind of reply. Owner: Gyula.

The agent sends real email to our own OpenMail test inboxes (mcp/sales_tools/openmail.py). This script watches
those inboxes and, for each email from the agent, picks a kind of reply at random, waits a random delay, and
answers in the same thread with invented details: a meeting time, a date they're back, a colleague's name, a
quarter to come back in. Some emails get no answer at all. So one run exercises many paths of read_reply,
follow-ups and the handoff without anyone writing replies by hand.

    uv run --project simulator python simulator/openmail_responder.py               # keeps watching
    uv run --project simulator python simulator/openmail_responder.py --once        # one pass, then exit
    uv run --project simulator python simulator/openmail_responder.py --delay 5:60 --seed 7 --weights positive=5,no_reply=0

Reads OPENMAIL_API_KEY, OPENMAIL_INBOX_ID, OPENMAIL_ADDRESS and OPENMAIL_TEST_RECIPIENTS from the
environment or the repo's .env. Prospect inboxes are the test recipients; with only the agent's own inbox
(OPENMAIL_TEST_RECIPIENTS empty), the "prospect" answers from that inbox, in the thread. Replies that
would copy a colleague in only do so when there is a second test inbox to copy; otherwise the colleague is
named in the text, which tests the agent's "handover without an address" path.

Ground truth: every reply (and every deliberate silence) is logged with the label it intends, in
simulator/out/openmail-replies.csv, so the agent's labels can be scored afterwards. State (what was seen,
what is planned) is in simulator/out/openmail-responder.json, so a restart neither forgets a planned reply
nor answers twice.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
from sales_tools import openmail  # noqa: E402

OUT = ROOT / "simulator" / "out"
STATE = OUT / "openmail-responder.json"
TRUTH = OUT / "openmail-replies.csv"

# kind -> the reply label it intends (outreach.REPLY_OPTIONS), and how often it's picked by default
KINDS = {
    "positive_time": ("positive", 3), "positive_ask_times": ("positive", 2), "positive_price": ("positive", 1),
    "positive_assistant": ("positive", 1), "not_now": ("not_now", 2), "negative_competitor": ("negative", 1),
    "negative_price": ("negative", 1), "negative_plain": ("negative", 1), "out_of_office": ("out_of_office", 1),
    "wrong_person": ("wrong_person", 1), "handover": ("handover", 1), "unsubscribe": ("unsubscribe", 1),
    "vague": ("unclear", 1), "no_reply": ("no_reply", 1),
}
MEETING_WORDS = ("booked", "invite", "calendar", "confirm", "see you", "meeting on")

FIRST = ["Anna", "Erik", "Maja", "Lars", "Sofia", "Mikko", "Ingrid", "Jonas", "Aino", "Henrik", "Freja", "Oskar"]
LAST = ["Berg", "Lund", "Virtanen", "Holm", "Nieminen", "Dahl", "Strand", "Korhonen", "Lindqvist", "Hansen"]
ROLES = ["our CTO", "our Head of Security", "the compliance lead", "our COO", "the IT manager", "my colleague in finance"]
COMPETITORS = ["Vanta", "Drata", "a spreadsheet our auditor gave us", "Secureframe", "an in-house tool"]
HELLO = ["Hi", "Hello", "Hej", "Moi", "Hi there", "", "Hey"]
BYE = ["Best", "Thanks", "Cheers", "Br", "Kind regards", "/", "Mvh"]


# --------------------------------------------------------------------------- made-up details

def _workday(rng: random.Random, start: date, lo: int, hi: int) -> date:
    d = start + timedelta(days=rng.randint(lo, hi))
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _slot(rng: random.Random, today: date) -> str:
    d = _workday(rng, today, 1, 12)
    h, m = rng.choice(range(8, 17)), rng.choice((0, 0, 15, 30, 45))
    return rng.choice([f"{d:%A} {d.day} {d:%b} at {h:02d}:{m:02d}", f"{d.day}/{d.month} kl. {h}", f"{d:%A} around {h}",
                       f"{d.isoformat()} {h:02d}:{m:02d} CET", f"next {d:%A} {'morning' if h < 12 else 'afternoon'}"])


def _later(rng: random.Random, today: date) -> str:
    d = today + timedelta(days=rng.randint(30, 200))
    q = (d.month - 1) // 3 + 1
    return rng.choice([f"in Q{q} {d.year}", f"after {d:%B}", f"mid-{d:%B}", "after the new year",
                       f"after our audit on {d.day} {d:%B}", "once the budget for next year is set", f"around {d.isoformat()}"])


def _name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def compose(kind: str, rng: random.Random, today: date, colleague: str | None) -> tuple[str, list[str]]:
    """(body, cc) for one kind of reply. colleague: a second test inbox to copy in, if there is one."""
    me, other = _name(rng), _name(rng)
    hi = rng.choice(HELLO)
    hi = f"{hi},\n\n" if hi else ""
    sign = f"\n\n{rng.choice(BYE)}\n{me.split()[0] if rng.random() < 0.6 else me}"
    cc: list[str] = []
    text = {
        "positive_time": f"Sounds relevant. Could we do {_slot(rng, today)}? Otherwise {_slot(rng, today)} works too.",
        "positive_ask_times": rng.choice(["Happy to have a quick call. Send me a few times next week?",
                                          "Interesting timing actually. What times do you have?", "ok lets talk. when?"]),
        "positive_price": f"Could be useful for us. Roughly what does it cost for a team of {rng.choice((5, 12, 30, 80))}? "
                          f"If it's reasonable I'd like a demo {_slot(rng, today)}.",
        "positive_assistant": f"I'm {other.split()[0]}, assistant to {me.split()[0]}. They'd like to meet. "
                              f"{_slot(rng, today)} is free in their calendar.",
        "not_now": f"Not the right moment, we're in the middle of something else. Can you come back {_later(rng, today)}?",
        "negative_competitor": f"Thanks, but we already use {rng.choice(COMPETITORS)} and are happy with it.",
        "negative_price": "We looked at tools like this last year and there's no budget for it. Not for us, sorry.",
        "negative_plain": rng.choice(["No thanks.", "Not interested.", "We'll pass, thanks."]),
        "out_of_office": f"I'm out of the office until {_workday(rng, today, 3, 20):%A %d %B}, with limited access to email. "
                         f"For urgent matters contact {other}.",
        "wrong_person": f"I'm not the right person for this. You'd want to talk to {other}, {rng.choice(ROLES)}.",
        "handover": None,
        "unsubscribe": rng.choice(["Please remove me from your list.", "Stop emailing me.", "unsubscribe"]),
        "vague": rng.choice(["What is this about exactly?", "Who gave you my address?", "Maybe. Send more info.", "?"]),
    }[kind] if kind != "handover" else None
    if kind == "handover":
        if colleague:
            cc = [colleague]
            text = f"Thanks. {other.split()[0]} (in cc) is {rng.choice(ROLES)} and will take it from here."
        else:
            text = f"{other} handles this, {rng.choice(ROLES)}. I'll let them take it from here."
    if kind == "out_of_office":
        return f"Automatic reply: {text}", cc
    return f"{hi}{text}{sign}", cc


def meeting_change(rng: random.Random, today: date) -> str:
    return rng.choice([f"Something came up, can we move it to {_slot(rng, today)}?",
                       "I have to cancel our meeting, sorry. I'll get back to you.",
                       f"Could we push it by a week? {_slot(rng, today)} maybe."])


# --------------------------------------------------------------------------- state

def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen": [], "plans": [], "our_bodies": [], "booked_threads": []}


def _save(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE)


def _h(text: str) -> str:
    return hashlib.sha256(openmail.clean_body(text).encode()).hexdigest()[:16]


def _log(plan: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    new = not TRUTH.exists()
    with TRUTH.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["planned_at", "reply_at", "prospect", "thread_id", "answers_subject", "kind", "expected_label",
                        "delay_s", "cc", "reply_body"])
        w.writerow([plan["planned_at"], plan.get("sent_at", ""), plan["prospect"], plan["thread_id"], plan["subject"],
                    plan["kind"], plan["label"], plan["delay_s"], ";".join(plan.get("cc") or []), plan.get("body", "")])


# --------------------------------------------------------------------------- the loop

def prospect_inboxes() -> dict[str, str]:
    """address -> inbox id of each test inbox the prospects answer from (just the agent's own, if that's all)."""
    agent, agent_id = openmail.address(), os.environ["OPENMAIL_INBOX_ID"]
    wanted = openmail.test_recipients()
    if wanted == {agent}:
        return {agent: agent_id}
    try:
        found = {i["address"].lower(): i["id"] for i in openmail.list_inboxes() if i["address"].lower() in wanted}
    except openmail.OpenMailError as e:
        sys.exit(f"can't list the test inboxes ({e}); an inbox-scoped key only sees its own inbox")
    missing = wanted - set(found)
    if missing:
        print(f"not OpenMail inboxes on this account, skipped: {', '.join(sorted(missing))}", file=sys.stderr)
    return found


def pick(rng: random.Random, weights: dict[str, float]) -> str:
    kinds = [k for k in KINDS if weights.get(k, 0) > 0]
    return rng.choices(kinds, [weights[k] for k in kinds])[0]


def plan_new(state: dict, inboxes: dict[str, str], rng: random.Random, weights: dict, delay: tuple[int, int],
             since: str) -> list[dict]:
    """Plan a reply (or a silence) for every new email from the agent."""
    agent, planned = openmail.address(), []
    seen, ours, start = set(state["seen"]), set(state["our_bodies"]), datetime.fromisoformat(since)
    for addr, inbox_id in inboxes.items():
        for x in sorted(openmail.inbound(limit=50, inbox_id=inbox_id), key=lambda x: x.get("createdAt") or ""):
            if x["id"] in seen or not x.get("createdAt") or datetime.fromisoformat(x["createdAt"]) < start:
                continue
            seen.add(x["id"])
            body = x.get("bodyText") or ""
            if (x.get("fromAddr") or "").lower() != agent or _h(body) in ours:
                continue  # not from the agent, or one of our own replies coming back
            booked = x["threadId"] in state["booked_threads"]
            if any(w in body.lower() for w in MEETING_WORDS):
                state["booked_threads"].append(x["threadId"])
            kind = "meeting_change" if booked and rng.random() < 0.4 else pick(rng, weights)
            label = "meeting_change" if kind == "meeting_change" else KINDS[kind][0]
            d = rng.randint(3, 20) if kind == "out_of_office" else rng.randint(*delay)  # auto-replies come at once
            p = {"id": x["id"], "prospect": addr, "inbox_id": inbox_id, "thread_id": x["threadId"],
                 "subject": x.get("subject") or "", "kind": kind, "label": label, "delay_s": d,
                 "planned_at": datetime.now(timezone.utc).isoformat(),
                 "due": (datetime.now(timezone.utc) + timedelta(seconds=d)).isoformat()}
            planned.append(p)
            print(f"planned {kind} ({label}) for {x.get('subject')!r} in {d}s")
            if kind == "no_reply":
                _log(p)
            else:
                state["plans"].append(p)
    state["seen"] = sorted(seen)
    return planned


def send_due(state: dict, inboxes: dict[str, str], rng: random.Random, dry_run: bool) -> list[dict]:
    now, today, sent = datetime.now(timezone.utc), date.today(), []
    others = [a for a in inboxes if a != openmail.address()]
    for p in list(state["plans"]):
        if datetime.fromisoformat(p["due"]) > now:
            continue
        colleague = next((a for a in others if a != p["prospect"]), None)
        if p["kind"] == "meeting_change":
            body, cc = meeting_change(rng, today), []
        else:
            body, cc = compose(p["kind"], rng, today, colleague)
        p.update(body=body, cc=cc)
        if not dry_run:
            try:
                openmail.send(openmail.address(), "", body, cc=cc, thread_id=p["thread_id"], inbox_id=p["inbox_id"],
                              idempotency_key=f"sim-{p['id']}")
            except openmail.OpenMailError as e:
                print(f"not sent yet ({e}), retrying next pass", file=sys.stderr)
                continue
        state["our_bodies"].append(_h(body))
        p["sent_at"] = now.isoformat()
        state["plans"].remove(p)
        _log(p)
        sent.append(p)
        print(f"{'would send' if dry_run else 'sent'} {p['kind']} to the agent: {body[:70]!r}")
    return sent


def parse_weights(s: str | None) -> dict[str, float]:
    w = {k: float(v[1]) for k, v in KINDS.items()}
    for part in (s or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() not in KINDS:
                sys.exit(f"unknown kind {k!r}; kinds: {', '.join(KINDS)}")
            w[k.strip()] = float(v)
    return w


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true", help="one pass (plan new, send what's due), then exit")
    ap.add_argument("--interval", type=int, default=20, help="seconds between passes (default 20)")
    ap.add_argument("--delay", default="30:600", help="reply delay range in seconds, min:max (default 30:600)")
    ap.add_argument("--weights", help="kind=weight,... (0 turns a kind off). Kinds: " + ", ".join(KINDS))
    ap.add_argument("--seed", type=int, help="same seed, same choices")
    ap.add_argument("--since", help="ignore agent emails before this ISO time (default: when the state file was started)")
    ap.add_argument("--dry-run", action="store_true", help="plan and print, send nothing")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT / "simulator"))
    import llm  # noqa: F401  (loads the repo's .env; here, not at import, so tests never see the real key)
    if not openmail.enabled() or not openmail.address():
        sys.exit("set OPENMAIL_API_KEY, OPENMAIL_INBOX_ID and OPENMAIL_ADDRESS (repo .env)")
    lo, hi = (int(x) for x in args.delay.split(":"))
    rng, weights = random.Random(args.seed), parse_weights(args.weights)
    state = _load()
    state.setdefault("started", datetime.now(timezone.utc).isoformat())
    since = args.since or state["started"]
    inboxes = prospect_inboxes()
    print(f"answering as {len(inboxes)} prospect inbox(es); agent emails since {since}")
    while True:
        try:
            plan_new(state, inboxes, rng, weights, (lo, hi), since)
            send_due(state, inboxes, rng, args.dry_run)
        except openmail.OpenMailError as e:
            print(f"OpenMail: {e}", file=sys.stderr)
        _save(state)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
