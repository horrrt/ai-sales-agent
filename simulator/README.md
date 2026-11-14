# Simulated prospects

Owner: Gyula (stream 5).

LLM personas built from the customer's ICP that receive sandbox outreach and reply the way real prospects would: interested, objection, not now, wrong person, unsubscribe. They make the whole pipeline testable without real sends, and they're what the "research beats templates" A/B test runs on.

- `personas/`: one JSON file per persona (see `personas/example.json`)
- `simulate.py`: given a persona and an email, returns a reply and the label the persona intended (the ground truth for JD's reply classification eval)

Limitation to state in the pitch: simulated replies test the pipeline, not whether real people would reply.
