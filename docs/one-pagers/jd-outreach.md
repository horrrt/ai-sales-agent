# Outreach Subagent: Human-in-the-Loop Sales Automation

**Author:** JD | **Stream:** 2 — Outreach | **Platform:** LangSmith Fleet

### What this part does

The Outreach Subagent is responsible for turning researched and positioned B2B leads into personalized sales emails, managing human approval before sending, and classifying prospect replies. As part of a multi-agent Sales Assistant, it is coordinated by a Manager Agent while research, positioning and meeting scheduling remain delegated to specialized subagents.

### Inputs and outputs

The subagent receives a `lead_id` and retrieves the associated lead record and customer brief. Required information includes contact and company details, verified research, a positioning angle, product information and the customer's communication style.

It validates input completeness before drafting. Missing or insufficient information results in `status = insufficient_data`, returned to the Manager for further research or positioning. Complete leads receive a personalized email draft for human approval.

After an approved sandbox send, incoming replies are classified into six predefined categories: `positive`, `negative`, `not_now`, `wrong_person`, `out_of_office` and `unsubscribe`. Ambiguous replies are escalated for human review. Classification results are recorded in the shared lead object and returned to the Manager, with positive responses forwarded toward the Handoff Subagent.

### Design decision

The main technical decision is to **combine LLM-based communication with deterministic constraints and human oversight**.

Language models provide flexibility in personalization, contextual interpretation and drafting. However, they are less suitable for independently enforcing strict business rules or authorizing external communication. Therefore, input validation, permitted classification labels and approval-before-send are defined explicitly.

The workflow follows the course concept of **human-in-the-loop agentic automation**: the agent prepares decisions and proposed actions, while a human retains control over consequential external communication.

The intended reply-processing stage also demonstrates **ambient agents**, where incoming messages trigger work without requiring the salesperson to initiate each action manually. Sandbox execution separates experimentation from real customer communication.

### Trade-off

Mandatory human approval reduces autonomy and introduces latency, but limits reputational and operational risks associated with incorrect or unsolicited messages.

Strict readiness checks may reject potentially valuable leads with incomplete research. We prioritize evidence-based personalization over maximizing outreach volume.

Separating Outreach from Research and Handoff improves specialization and responsibility boundaries, but creates dependencies on shared data and Manager orchestration. These integrations remain to be verified.

### A failure and the fix

Four behavioral mock scenarios tested drafting, missing information, rejected approval and reply classification.

The initial classification evaluation revealed that the model understood replies semantically but generated invented or combined labels incompatible with the shared schema. **None of five replies produced the required classification or escalation output (0/5).**

We revised the agent instructions to enforce exact classification labels, prioritize unsubscribe requests and escalate ambiguous responses without storing invalid labels.

Repeating the same five synthetic cases produced **5/5 correct classification or escalation decisions**, including four valid stored-label decisions and one human-review escalation.

This demonstrates an evaluation-driven improvement cycle: **observed failure → instruction revision → controlled retest**. The result is limited to simulated behavior on a small, reused dataset; real tool execution, email approval and end-to-end integration remain unverified.
