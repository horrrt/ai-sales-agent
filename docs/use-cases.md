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
| E15 | An out-of-office comes first, then the real answer. Read the newest and both are handled. Read them one at a time and the real reply still waits. The replied time is the real reply's, not the auto-reply's | `test_out_of_office_then_real_reply_reads_the_newest`, `test_replied_time_is_when_the_reply_arrived`, `test_replies_read_one_at_a_time_keep_the_real_reply_waiting` | `email/16-out-of-office-then-real-reply` |
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

## Team size

The tool is for sales teams of 1 to 10 reps. A rep's traffic light comes from their load (green under 70% of their maximum, yellow up to it, red when full). A rep can also be away: no new leads and no slots.

| ID | Use case | Unit tests | Fleet cases |
|---|---|---|---|
| T1 | A one-person team: the founder gets every lead, and meetings fit around what's already in their calendar | `test_every_team_size_books_without_double_booking` | `teams/01-solo-rep-books` |
| T2 | The only rep is full. The lead waits in the queue, is flagged after a working day, and goes out when the rep has room | `test_solo_rep_takes_leads_until_full_then_the_queue_waits_and_is_flagged` | `teams/02-solo-rep-full-queues`, `teams/07-ten-reps-all-red` |
| T3 | The only rep's week is full, so the slots come from the week after | `test_solo_rep_with_a_full_week_gets_slots_the_week_after` | `scheduling/01-busy-calendar` |
| T4 | Two reps: the lead goes to the rep who covers its country, even if they have more leads | `test_two_reps_country_comes_before_load` | `teams/03-two-reps-country-match` |
| T5 | A rep is away. They get no leads and no slots | `test_three_reps_an_away_rep_gets_no_leads_and_no_slots` | `teams/06-away-rep-skipped` |
| T6 | Five reps in five countries. Slots are inside the rep's working hours and the prospect's business hours, each in their own time zone | `test_five_reps_slots_follow_each_reps_time_zone_and_the_prospects_hours` | `teams/04-five-reps-time-zones` |
| T7 | Ten reps: leads are shared evenly between reps who cover the same country, nobody goes over their maximum, and the rest wait in the queue | `test_ten_reps_share_leads_evenly_and_never_go_over_capacity` | `teams/05-ten-reps-least-loaded`, `teams/07-ten-reps-all-red` |

## Scheduling

Each rep has a calendar (busy blocks and booked meetings), working hours and a time zone. Slots start the working day after next. Booking, moving and cancelling a meeting all ask a person first. Public holidays aren't in the calendars.

| ID | Use case | Unit tests | Fleet cases |
|---|---|---|---|
| S1 | Slots skip busy time and meetings already booked | `test_slots_skip_busy_time_and_booked_meetings` | `scheduling/01-busy-calendar` |
| S2 | Booking refuses a clash, a time that has passed, a time outside working hours, and a second meeting for the same lead | `test_booking_refuses_a_clash_a_past_time_after_hours_and_a_second_meeting` | `scheduling/08-asked-for-a-taken-slot` |
| S3 | The invite includes the people in the thread: the lead, an assistant or colleague who wrote, anyone copied in | `test_meeting_invite_includes_the_people_in_the_thread` | `teams/01-solo-rep-books` |
| S4 | The prospect names a day and time, even weeks out, and that time is booked | `test_reschedule_to_the_day_the_prospect_named_even_weeks_out` | `scheduling/02-prospect-names-a-day` |
| S5 | The prospect asks to move the meeting. It moves, the old slot is free for someone else, and the history is kept | `test_reschedule_moves_the_meeting_and_frees_the_old_slot` | `scheduling/03-prospect-reschedules` |
| S6 | Moving onto a taken slot is refused. After the second move, the rep is asked whether to keep the meeting | `test_reschedule_refuses_a_clash_and_after_two_moves_asks_the_rep` | `scheduling/09-third-reschedule` |
| S7 | A rep is sick. Their meetings are listed from their calendar and move to a colleague who covers the same country | `test_a_sick_reps_meeting_moves_to_a_colleague`, `test_a_reps_calendar_lists_their_meetings_and_busy_time` | `scheduling/07-sick-rep-meeting-moves` |
| S8 | The prospect cancels but wants another time. The slot is freed, no time is picked for them, and a rebooking follow-up is due the next working day | `test_cancellation_frees_the_slot_and_schedules_a_rebooking_follow_up` | `scheduling/04-prospect-cancels-rebook` |
| S9 | The prospect cancels for good. No rebooking, and a person decides whether the lead is lost | `test_cancellation_for_good_waits_for_a_person` | `scheduling/05-prospect-cancels-for-good` |
| S10 | A no-show gets a rebooking follow-up. After a second no-show, a person decides | `test_no_show_schedules_a_rebook_and_a_second_no_show_asks_a_person` | `scheduling/06-no-show` |
| S11 | No rep is ever double-booked, at any team size | `test_every_team_size_books_without_double_booking` | `scheduling/08-asked-for-a-taken-slot`, `scheduling/07-sick-rep-meeting-moves` |
| S12 | Calendars survive a server restart | `test_calendar_survives_a_restart` | `scheduling/03-prospect-reschedules` |

## Follow-ups

`due_follow_ups` lists everything owed, with a kind: email, no_reply, rebook, meeting_outcome, not_booked, queue_waiting, needs_person and quiet_deal. The follow-ups skill runs it every weekday at 09:30 and sends each kind to the agent that owns it.

| ID | Use case | Unit tests | Fleet cases |
|---|---|---|---|
| F1 | Every lead that's still active, whatever happened to it, shows up in the follow-ups: none is left without a next step | `test_every_active_lead_always_has_a_next_step` | `follow-ups/01-daily-mix` |
| F2 | The rebooking email counts as the rebooking follow-up, in the same thread | `test_the_rebooking_email_counts_as_the_follow_up` | `follow-ups/01-daily-mix` |
| F3 | A meeting nobody recorded shows up the working day after. The agent asks the rep and never guesses the outcome | `test_a_meeting_nobody_recorded_shows_up_the_next_working_day` | `follow-ups/02-handoff-works-its-list` |
| F4 | A lead with a rep but no meeting shows up after a working day | `test_a_lead_with_a_rep_but_no_meeting_shows_up` | `follow-ups/02-handoff-works-its-list` |
| F5 | No outreach follow-up while a meeting is booked | `test_no_outreach_follow_up_while_a_meeting_is_booked` | `follow-ups/03-outreach-hands-back` |
| F6 | A reply asking to move the booked meeting goes to @handoff. A reply proposing a first time stays positive | `test_a_request_to_move_the_meeting_goes_to_handoff_and_a_first_proposal_stays_positive` | `follow-ups/06-reply-moves-the-meeting` |
| F7 | A decision only a person can make shows up until it's made, and the agent asks instead of deciding | `test_an_unlabelled_reply_waits_for_a_person_until_it_moves_on` | `follow-ups/05-person-decision-surfaces` |
| F8 | An open deal with no activity for 7 days shows up | `test_a_quiet_deal_shows_up_in_follow_ups` | `deal-desk/06-pipeline-quiet-deals` |
| F9 | Nothing is due: the agent says so and changes nothing | agent only | `follow-ups/04-nothing-due` |

## Who may do what

Each person in the team roster has a role (admin, rep, junior, finance, viewer) and can have their own `allow` and `deny` lists on top, and deny wins. The tools check the person passed as `by` (a rep_id, email or name). A claimed role in a message never counts, and a refusal names who can do it.

| ID | Use case | Unit tests | Fleet cases |
|---|---|---|---|
| R1 | A junior or a viewer can't approve an email. A rep can, and only then does it go out | `test_a_junior_or_viewer_cannot_approve_an_email_and_a_rep_can` | `access/01-junior-cannot-approve-email`, `access/02-rep-approves-email` |
| R2 | Someone who isn't in the team, or who only claims a role ("I'm the CEO", "admin"), is refused | `test_an_unknown_person_or_a_claimed_role_is_refused` | `deal-desk/07-claimed-admin` |
| R3 | One person's allow or deny list changes what their role lets them do, and deny wins even for an admin | `test_per_person_allow_and_deny_override_the_role` | `access/01-junior-cannot-approve-email` |
| R4 | A refusal is logged with who was refused, and says who can do it instead | `test_a_refusal_is_logged_with_who_was_refused_and_who_can` | `access/03-rep-discount-needs-admin` |
| R5 | A discount above the limit needs an admin, even on the rep's own deal | `test_a_discount_above_the_limit_needs_an_admin` | `access/03-rep-discount-needs-admin`, `deal-desk/02-discount-needs-admin` |
| R6 | A rep can't approve, sign or close a colleague's deal | `test_a_rep_cannot_approve_or_close_someone_elses_deal` | `access/04-rep-cannot-close-colleagues-deal`, `manager/06-other-rep-closes-a-deal` |
| R7 | Only finance or an admin records a payment | `test_only_finance_or_an_admin_records_a_payment` | `access/05-finance-records-payment`, `access/06-rep-cannot-record-payment` |
| R8 | Only an admin approves a rule change from the weekly review | `test_only_an_admin_decides_a_rule_change` | `coach/01-proposal-from-rep-edits` |
| R9 | A rep can't move or cancel a colleague's meeting. The owner or an admin can | `test_a_rep_cannot_move_or_cancel_a_colleagues_meeting_but_an_admin_can` | `access/08-rep-cannot-cancel-colleagues-meeting` |
| R10 | A junior runs their own meetings | `test_a_junior_runs_their_own_meetings` | `access/07-junior-moves-own-meeting` |
| R11 | You set your own status. Setting someone else's needs an admin | `test_status_is_your_own_unless_you_are_an_admin` | `scheduling/07-sick-rep-meeting-moves` |
| R12 | People who don't take leads (finance, viewers) never get one | `test_a_finance_person_never_gets_a_lead` | `access/09-viewer-is-read-only` |

## Auto-close

Off unless the brief enables it. A small, standard deal closes without a rep: check_small_deal, then send_offer (in the thread, in business hours), then the payment, then check_deal_done. Anything else goes to a rep or waits for a person.

| ID | Use case | Unit tests | Fleet cases |
|---|---|---|---|
| A1 | A small standard deal: the offer goes out in the thread once, the payment comes in, the deal is won without a rep | `test_auto_close_small_deal_paid_is_won`, `test_auto_close_offer_goes_in_the_thread_once` | `auto-close/01-small-deal-offer-sent`, `auto-close/02-paid-closes-won`, `deal-desk/05-auto-close-small-deal` |
| A2 | Auto-close is switched off: no offer, the reply goes to a rep | `test_auto_close_off_never_sends_an_offer` | `auto-close/13-switched-off` |
| A3 | Over the price cap: a rep, without asking d1 | `test_deal_over_the_price_cap_goes_to_a_rep_without_asking_d1` | `auto-close/03-over-the-cap-to-a-rep` |
| A4 | Custom terms, a security review or a discount request: a rep | `test_auto_close_a_request_for_custom_terms_goes_to_a_rep` | `auto-close/04-custom-terms-to-a-rep` |
| A5 | d1 can't decide: nothing is opened or sent, and a person decides | `test_auto_close_without_a_decision_waits_for_a_person` | `auto-close/05-gate-cannot-decide` |
| A6 | The offer is ready at night: it waits until 08:00 in the prospect's country | `test_auto_close_offer_at_night_waits_for_the_morning` | `auto-close/06-offer-at-night-held` |
| A7 | Not paid: a reminder after 2 working days, at most two, then after 7 working days a rep takes it | `test_auto_close_unpaid_offer_gets_two_reminders_then_goes_to_a_rep` | `auto-close/07-unpaid-reminder`, `auto-close/08-offer-expired-to-a-rep` |
| A8 | The card is declined: the link goes out again the next working day, then the payment goes through | `test_auto_close_failed_payment_sends_the_link_again` | `auto-close/09-payment-failed-retry` |
| A9 | Only part is paid: not won, a person decides | `test_auto_close_partial_payment_waits_for_a_person` | `auto-close/10-partial-payment` |
| A10 | The same payment is reported twice: the second is refused | `test_auto_close_a_second_payment_is_refused` | `auto-close/14-duplicate-payment` |
| A11 | A refund after the win: it comes off won revenue and a person decides what it means | `test_auto_close_refund_after_a_win_comes_off_revenue_and_waits_for_a_person` | `auto-close/12-refund` |
| A12 | After the offer, they ask for a discount: auto-close ends and a rep takes it | agent only | `auto-close/11-reply-asks-for-a-discount` |
| A13 | They unsubscribe after the offer: no reminders | `test_auto_close_no_reminder_after_an_unsubscribe` | `outreach/06-unsubscribe` |
| A14 | No offer or reminder without the gate saying yes | `test_auto_close_cannot_skip_the_gate` | `auto-close/03-over-the-cap-to-a-rep` |
| A15 | Payment amounts are kept and counted in the funnel | `test_payment_amount_is_kept` | `auto-close/02-paid-closes-won` |

## Times

Nothing goes out, falls due or is booked outside working time: weekends, nights and national public holidays in the prospect's country (and, for meetings, where the rep works) don't count. Holidays are national only (not, say, German states). The Fleet runner's `--now` runs any case at another time.

| ID | Use case | Unit tests | Fleet cases |
|---|---|---|---|
| W1 | The holiday tables: Easter, Midsummer, 17 May, 6 December, German Unity Day. Christmas Eve is a working day in Germany | `test_holiday_tables` | `times/07-midsummer-eve-sweden` |
| W2 | An email approved on Christmas Eve waits until Monday 28 December | `test_christmas_eve_send_waits_until_after_christmas` | `times/01-christmas-eve-email-held` |
| W3 | A national holiday holds one country's email, not another's | `test_a_national_holiday_holds_one_country_and_not_another` | `times/02-norway-17-may` |
| W4 | Follow-up spacing skips Christmas | `test_follow_up_spacing_skips_christmas` | `times/04-friday-night-follow-ups` |
| W5 | Meeting slots skip the rep's and the prospect's public holidays. A holiday booking is refused | `test_slots_skip_holidays_where_the_rep_works_and_where_the_prospect_is` | `times/05-prospect-asks-for-christmas-eve`, `times/08-finnish-independence-day` |
| W6 | A yes on Saturday gets slots from Tuesday (Monday is the notice day) | `test_a_reply_on_saturday_gets_slots_from_tuesday` | `times/03-saturday-reply-booked-next-week` |
| W7 | Slots keep their local time across the switch to summer time | `test_slots_keep_local_time_across_the_daylight_saving_change` | `times/06-booking-after-the-clock-change` |
| W8 | A cancellation late on Friday: the rebooking follow-up is due Monday morning | `test_a_rebook_follow_up_after_a_friday_cancellation_is_due_monday` | `scheduling/04-prospect-cancels-rebook` |
| W9 | The same flow at 14 times (from 07:59 to 23:59, weekends, Christmas, New Year's Eve, Easter, the clock change, Midsummer, 17 May, 6 December) in 5 countries: nothing goes out, falls due or is offered at the wrong time | `test_time_sweep_nothing_goes_out_or_is_booked_at_the_wrong_time` | `times/04-friday-night-follow-ups` |
