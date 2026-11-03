# Meeting playbook

## Resolve people and intent

Read the relevant people in `contact_map.json` and the current chat/project's
confirmed patterns in `project_context_map.json`. Use the current speaker for
first-person requests. Do not use the application's `sender_email` as a person's
mailbox. Missing/ambiguous names, booker userids, or emails require clarification,
not guessing. Only split required/optional attendees when requested.

Use `list_employees` as identity fallback, not as a source of email addresses.
Use `list_meeting_rooms` for actual room IDs, names, capacity and approval needs,
then `query_room_availability` for candidate times within the allowed window.
Do not send invitations while relevant recipient/period/room details are unresolved.

## One native recurring invitation

`send_schedule_email` takes `repeat` for a native weekly series. The server sends
one compose request with `schedule.reminders` repeat fields; it does not send an
invitation for each occurrence. If no physical room is requested, use this directly.
No plan, booker userid, reservation worker or room booking is needed.

For “every Monday 10–11 until the end of December”, resolve the first Monday
and the last included date in the intended timezone with executable arithmetic.
Pass the first occurrence's `start_time` / `end_time`, and:

```json
{
  "repeat": {
    "repeat_type": 1,
    "repeat_interval": 1,
    "repeat_day_of_week": [1],
    "timezone": 8,
    "repeat_until": "REPLACE_WITH_CALCULATED_UNIX_SECONDS"
  }
}
```

The placeholder above must become an integer, not a string. `repeat_type=1`
means weekly; weekdays are 1=Monday through 7=Sunday. `repeat_interval=2` means
every other week. The first event must fall on an included weekday. Set
`repeat_until` to the inclusive final local day's end, not its 00:00 or a duration
of months. Check the converted dates before sending. Weekly recurring invitations
must have a clear end; do not ask the user to calculate timestamps.

The repeat API takes an integer UTC-hour offset, not an IANA timezone. Do not
silently treat a DST-crossing local-time series as a fixed-offset series. The
managed workflow rejects DST-crossing periods and fractional-hour offsets; explain
unsupported cases rather than splitting them into many invitations.

`send_meeting_email` is for a single Tencent Meeting with a confirmed administrator.
Its exposed tool does not currently provide weekly repeat. If recurring Tencent
Meeting links are explicitly requested, explain the limitation; don't promise
that a normal recurring calendar invitation creates recurring video meetings.

## Invitations that require a physical room

Call `create_managed_meeting` with `plan` containing:

- `request_id`: stable original user message ID (internal parameter; reuse on retry).
- Resolved `to_emails`, `subject`, `content`, room name in `location`, and the
  first occurrence's calculated `start_time` / `end_time`.
- `until_date` in `YYYY-MM-DD`, inclusive, and `timezone`, normally `Asia/Shanghai`.
- `meetingroom_id`, confirmed `booker_userid`, and confirmed organizer `notify_email`.

The first local occurrence determines the weekday/time for a weekly plan. It
derives the native repeat rule itself; don't pass another `repeat` rule that
could disagree with room dates. For one meeting, use the first local date as
`until_date`. The current managed plan supports weekly frequency, at most 52
occurrences over one year, same-day room bookings 08:00–23:00, and 30-minute
boundaries. Do not round a user's requested times without telling them.

The service sends one invitation. Only rooms are processed per occurrence:

1. Within the operator-configured advance window, check availability and book.
2. Outside the window, persist `pending_window` with `eligible_at`. It is not a
   failure or confirmed booking. The background worker reserves it when eligible.
3. Conflicts, definite API failures and unknown write outcomes stay distinguishable.
   The organizer receives ordinary status email alerts; these are not new
   calendar invitations. A future booking is never guaranteed before it succeeds.

Never send another invitation or independently book the same series's rooms.
The default configured window is 30 days, but WeCom's actual room policy wins;
don't claim the tool bypasses booking limits. A healthy worker is required before
creation. If it fails readiness checks, stop and report, without falling back to
an untracked long-range series of separate calls.

For a room-only single request, use `book_meeting_room`, preserve `booking_id`,
and do not send mail. A recurring room-only managed plan is not exposed by the
current tool: explain that limitation rather than inventing attendees to satisfy
the managed invitation schema.

## Status and recovery

Use `get_managed_meeting(plan_id)`; use `list_managed_meetings` to find a lost ID.

- The single series-level `invite.state` must be `sent` to claim the recurring
  invitation succeeded. `failed`, `not_sent`, `unknown` or `expired` are not success.
- Check every occurrence's `room` and `booking_id`. Report booked dates separately
  from `pending_window`, `conflict`, `retry`, `failed`, `unknown` and `expired`.
- Only `complete=true` means the invitation and all requested room dates succeeded.
  At initial creation, a multi-month plan will normally be partial because later
  rooms are not yet bookable. State this explicitly.
- To retry a definite room failure/conflict after the user asks, use
  `retry_managed_room_booking`. It does not resend the calendar invitation.
- `retry_managed_invitation` is only for an explicit retry of a definite rejection
  or an unattempted send. Already-sent and unknown invitations cannot be replayed.
  The worker itself never sends invitations, and pauses further bookings while
  the series invitation is not confirmed sent.
- With an ambiguous network result, query the plan and actual provider records
  first. Don't assume a timeout means nothing was created or sent.
- Legacy per-occurrence invitation plans are marked `requires_review`; don't
  recreate or automatically convert them. Audit old invitations and bookings
  first to avoid notifying recipients again.

Example truthful result:

> 周期邀请已发送：每周一 10:00–11:00，至确认的结束日期。会议室已订好列出的日期；
> 其余日期尚未开放预约，已登记滚动补订。发生冲突会通知组织者。

Only say “已登记滚动补订” if the managed plan was actually saved and its worker is
healthy. If alerts fail, status remains queryable; do not promise guaranteed email
delivery. Normal calendar-only invitations should simply report the recurrence,
not claim any room reservations.

## Cancellation and edits

For a managed plan, use `cancel_managed_meeting` only on the user's explicit
cancellation request. It stops future work before attempting known room booking
cancellations. Inspect any unknown outcomes and `cancel_error`; keep IDs for
reconciliation, and don't say all rooms were cancelled if some remain uncertain.

For a standalone room, use `cancel_room_booking` with the saved `booking_id`.
Current tools do not expose reliable cancellation/update of a sent calendar
series: the mail API requires a known `schedule_id`, which the send response
does not reliably return. Explain that the invitation remains on calendars and
the organizer must handle it in the calendar client. Do not equate cancelling
rooms with cancelling the meeting invitation, and do not send a new series to
"update" an existing one without explicit user authorization.
