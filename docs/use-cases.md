# Use cases and their tests

Each row is something that happens to a real sales team, with the tests that check we handle it. Unit tests (in `mcp/tests/`) check what the tools do, with d1 replaced by a scripted stub and the clock pinned. Fleet cases (in `evals/fleet/cases/`) run the real agents against the same tools and check what they decide. Rows where only the agent decides say "agent only" in the unit column.

`mcp/tests/test_use_cases.py` reads these tables. It fails if a row names a test or case that doesn't exist, if a row has no Fleet case, or if a unit test or Fleet case isn't in any row. So when you add a test, add it here too.

Run them:

```bash
cd mcp && uv run --extra dev pytest -q
```

```bash
cd evals/fleet && uv run python run.py --dry-run && uv run python run.py --check-fixtures
```

The dry run and the fixture check make no model calls. A full Fleet run (`uv run python run.py`) does, so it costs money.

## Email context

Every email is a message record: from, to, cc, subject, body, thread and time, for ours and theirs (`schemas/message.schema.json`). The person approving an email sees the final subject, to and cc (`record_approval` refuses to approve without them), and `send_email` sends to exactly those. Emails go out 08:00-17:00 on working days in the lead's country. Outside that they're held until the next 08:00. Weekends count, public holidays don't yet.

| ID | Use case | Unit tests | Fleet cases |
|---|---|---|---|
| E1 | The first email goes out with our sender name, the lead as recipient, a subject and a new thread | `test_first_email_is_a_full_message_record` | `email/01-first-email-full-record` |
| E2 | The rep approves at 22:00. The email waits until 08:00, nobody can reply to it before then, and it survives a server restart while it waits | `test_send_at_night_is_held_until_morning`, `test_held_email_and_inbox_survive_a_restart` | `email/02-night-send-is-held` |
| E3 | Approved on Friday evening, the email goes out Monday at 08:00 | `test_send_on_friday_evening_waits_for_monday` | `email/03-friday-evening-waits-for-monday` |
| E4 | Business hours are the lead's: 08:45 in Helsinki sends, 07:45 in Stockholm waits. A country we don't know uses the sender's time zone | `test_send_window_uses_the_lead_country`, `test_unknown_country_uses_the_sender_time_zone` | `email/04-lead-country-time-zone` |
| E5 | Follow-ups count working days: 3 after the first email, 5 after the first follow-up, and no_reply 5 working days after the last | `test_follow_ups_are_spaced_in_working_days`, `test_no_reply_after_the_last_follow_up_counts_working_days` | `email/05-follow-up-spaced-in-working-days`, `email/06-follow-up-not-due-yet` |
| E6 | A follow-up stays in the thread ("Re:") and keeps everyone the prospect copied in | `test_follow_up_keeps_the_thread_and_the_prospects_cc`, `test_unanswered_follow_up_threads_under_our_first_email` | `email/07-follow-up-keeps-thread-and-cc` |
| E7 | A rep asks to be copied in. The draft has them in cc and the person approving sees it | `test_cc_asked_for_in_the_draft_is_shown_for_approval` | `email/08-rep-asks-to-be-copied-in` |
| E8 | The rep adds themself to cc while approving. That counts as an edit, and changing cc after approval blocks the send | `test_rep_added_to_cc_at_approval_is_sent_to_them`, `test_approval_covers_the_recipients` | `email/09-rep-adds-themself-at-approval` |
| E9 | The CEO's assistant answers for her. The reply is flagged as not from the lead, the assistant becomes a contact, and our answer goes to the assistant with the CEO in cc | `test_assistant_replies_for_the_ceo`, `test_get_replies_shows_sender_cc_time_and_what_it_answers` | `email/10-assistant-replies-for-the-ceo` |
| E10 | The CEO copies in the CTO and hands over. The lead isn't lost: our answer goes to the CTO with the CEO in cc | `test_ceo_hands_over_to_the_cto_they_copied_in` | `email/11-ceo-hands-over-to-cto`, `email/20-manager-routes-handover` |
| E11 | "Yes to option 2" only makes sense next to our email with the options, so d1 reads both | `test_reply_is_read_with_the_email_it_answers`, `test_thread_shows_both_sides_in_order` | `email/12-yes-to-option-2` |
| E12 | A positive reply copies in the CFO. The CFO becomes a contact and stays in cc. Our own sender and reps never become contacts | `test_new_person_in_cc_becomes_a_contact_and_stays_in_cc`, `test_our_own_side_never_becomes_a_contact` | `email/13-cc-person-becomes-contact` |
| E13 | "Not my area", with a colleague copied in but no handover: lost as wrong_person, the colleague kept as a referral | `test_wrong_person_saves_the_referral` | `email/14-wrong-person-copies-colleague` |
| E14 | The prospect attaches a security questionnaire. The attachment name reaches d1 and the agent | `test_attachments_reach_the_reader` | `email/15-attachment-questionnaire` |
| E15 | An out-of-office comes first, then the real answer. Read the newest and both are handled; read them one at a time and the real reply still waits. The replied time is the real reply's, not the auto-reply's | `test_out_of_office_then_real_reply_reads_the_newest`, `test_replied_time_is_when_the_reply_arrived`, `test_replies_read_one_at_a_time_keep_the_real_reply_waiting` | `email/16-out-of-office-then-real-reply` |
| E16 | A reply asks us to write to a Gmail address or copy a stranger in. The recipients don't change and nothing leaves the sandbox | `test_reply_text_never_changes_who_we_email`, `test_cc_outside_the_sandbox_is_refused` | `email/17-reply-asks-to-change-recipients`, `outreach/07-prompt-injection-in-reply` |
| E17 | "Our CTO will get in touch" with nobody copied in. Not lost, and a person decides who to write to | `test_handover_without_an_address_goes_to_a_person` | `email/18-handover-without-address` |
| E18 | The call brief names everyone in the thread: the assistant who booked and the CFO copied in | agent only | `email/19-handoff-brief-names-everyone` |
| E19 | Someone pastes a reply that isn't in the inbox. It's still recorded as a message | `test_pasted_reply_without_an_inbox_entry_is_still_recorded` | `outreach/05-reply-exits` |
| E20 | After an unsubscribe the lead is never emailed again, even if someone reopens it by hand | `test_unsubscribed_lead_is_never_emailed_again` | `outreach/06-unsubscribe` |
| E21 | Tests and demos pin the time (SALES_NOW, or `setup.now` in a Fleet case) | `test_clock_follows_sales_now` | `email/02-night-send-is-held` |
| E22 | The person approving always sees who gets the email (to and cc) and can change either. An approval without them is refused | `test_approval_without_the_recipients_is_refused`, `test_person_can_change_the_recipient_at_approval` | `email/11-ceo-hands-over-to-cto`, `email/07-follow-up-keeps-thread-and-cc` |

## The rest of the pipeline

| ID | Use case | Unit tests | Fleet cases |
|---|---|---|---|
| P1 | A candidate fits the ICP and is added once. A second copy is a duplicate, and created_at and stage times are set | `test_add_lead_fit_is_added_and_duplicates_skipped`, `test_stage_times_and_created_at_are_recorded` | `lead-search/01-fit-added`, `lead-search/03-duplicate` |
| P2 | A candidate outside the ICP is recorded as lost with wrong_company_fit | `test_add_lead_not_a_fit_is_recorded_with_a_loss_reason` | `lead-search/02-outside-icp` |
| P3 | d1 can't decide. The lead goes to a person instead of being guessed | `test_add_lead_without_a_decision_goes_to_a_human`, `test_decide_without_any_backend_returns_no_answer` | `lead-search/04-gate-cannot-decide`, `outreach/09-gate-cannot-decide` |
| P4 | The d1 call has the shape Liquid's API expects | `test_d1_request_and_response_shape` | `lead-search/01-fit-added` |
| P5 | Research is enough to write, or after 2 retries the lead goes on hold (not lost) | `test_research_gate_retries_then_puts_the_lead_on_hold` | `researcher/01-ready-to-write`, `researcher/02-thin-goes-on-hold` |
| P6 | The positioner finds an angle in a sourced trigger, or says there's no fit | agent only | `positioner/01-angle-from-a-trigger`, `positioner/02-no-fit` |
| P7 | The rep approves and the email is sent once. One approval covers one send | `test_approved_draft_sends_once_and_schedules_a_follow_up` | `outreach/01-approve-and-send` |
| P8 | The rep rejects. The agent redrafts and asks again | `test_rejected_draft_cannot_be_sent_and_a_new_draft_needs_new_approval` | `outreach/02-rejected-then-redraft` |
| P9 | The rep edits the email. The edit is sent and both versions are logged | `test_rep_edit_becomes_the_draft_and_is_logged` | `outreach/03-rep-edit-is-sent` |
| P10 | Nothing is sent without a person's approval, and update_lead can't fake one | `test_send_refuses_without_approval`, `test_update_lead_cannot_fake_an_approval` | `outreach/04-no-send-without-approval` |
| P11 | Each kind of reply gets its exit: lost with a reason, follow up later, or go on | `test_reply_exits`, `test_read_reply_refuses_a_lead_that_was_never_emailed` | `outreach/05-reply-exits` |
| P12 | "Talk to Eva, eva@..." without copying her in: lost as wrong_person, Eva kept as a referral | `test_wrong_person_naming_an_address_saves_it_as_a_referral` | `outreach/08-referral` |
| P13 | A follow-up is due. After 2 follow-ups with no answer the lead is lost as no_reply | `test_follow_up_due_then_no_reply_after_two` | `outreach/10-follow-up-due` |
| P14 | The draft doesn't promise what the brief can't back ("will pass", "guarantee") | agent only | `outreach/11-overclaim-trap` |
| P15 | A positive reply goes to a rep, and only once | `test_positive_reply_goes_to_a_rep_when_auto_close_is_off`, `test_a_reply_is_handled_once_and_a_rep_assigned_once` | `handoff/01-green-rep-books` |
| P16 | Every rep is red: the lead waits in the queue and is handed out when a rep has room | `test_assign_rep_prefers_green_then_yellow_then_queues` | `handoff/02-all-red-queues`, `handoff/05-queue-release` |
| P17 | A meeting slot on a weekend is refused, and the slots reach the day the prospect asked for | `test_free_slots_skip_weekends_and_booking_rejects_them`, `test_free_slots_follow_the_clock_and_reach_the_day_asked_for` | `handoff/04-rep-rejects-the-slot`, `manager/02-routes-replies` |
| P18 | A held meeting counts toward the North Star and opens a deal owned by the rep | `test_held_meeting_opens_a_deal_owned_by_the_rep`, `test_no_deal_before_a_meeting_is_held`, `test_meeting_outcome_counts_toward_the_north_star` | `handoff/03-meeting-held-opens-deal` |
| P19 | Call notes give BANT ("unknown" doesn't block), and the quote is priced in code | `test_bant_comes_from_call_notes_and_unknown_does_not_block`, `test_quote_totals_are_computed_in_code_and_policy_is_enforced` | `deal-desk/01-notes-bant-quote`, `deal-desk/08-bant-fails` |
| P20 | A discount over 10% needs an admin, over 20% is refused, and a claimed approval doesn't count | `test_quote_totals_are_computed_in_code_and_policy_is_enforced` | `deal-desk/02-discount-needs-admin`, `deal-desk/03-discount-over-max`, `deal-desk/07-claimed-admin` |
| P21 | A quote changed after approval can't be sent | `test_quote_cannot_be_sent_without_approval_or_after_a_change` | `deal-desk/09-rep-changes-the-quote` |
| P22 | A deal is won once, or lost with a reason from the list | `test_won_deal_is_recorded_once_on_the_lead`, `test_lost_deal_needs_a_reason_from_the_list` | `deal-desk/04-lost-with-reason`, `manager/06-other-rep-closes-a-deal` |
| P23 | Auto-close: a small deal is paid and won without a rep, a deal over the cap goes to a rep, and with auto-close off no offer is sent | `test_auto_close_small_deal_paid_is_won`, `test_auto_close_off_never_sends_an_offer`, `test_deal_over_the_price_cap_goes_to_a_rep_without_asking_d1`, `test_payment_amount_is_kept` | `deal-desk/05-auto-close-small-deal` |
| P24 | The pipeline is weighted by stage and quiet deals are flagged | `test_pipeline_weights_open_deals_and_flags_quiet_ones` | `deal-desk/06-pipeline-quiet-deals` |
| P25 | The weekly review proposes a rule change from rep edits, waits when there's too little data, and spots losses to a competitor | `test_event_log_and_proposals`, `test_funnel_metrics_with_a_deal_that_never_set_auto_close` | `coach/01-proposal-from-rep-edits`, `coach/02-too-early`, `coach/03-losses-to-a-competitor` |
| P26 | Every log row uses the schema's stages and outcomes | `test_log_interaction_rejects_values_outside_the_schema` | `manager/05-batch-end-to-end` |
| P27 | The lead sheet survives a server restart, and a snapshot from before message records still loads | `test_state_snapshot_round_trip`, `test_old_snapshot_outbox_is_upgraded` | `manager/05-batch-end-to-end` |
| P28 | The manager sends each request to the right agent and declines off-topic ones | agent only | `manager/01-routes-lead-search`, `manager/02-routes-replies`, `manager/03-routes-deal`, `manager/04-off-topic` |
