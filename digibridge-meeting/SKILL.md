---
name: digibridge-meeting
description: >-
  Arrange DigiBridge internal meetings: resolve attendees, send one native weekly
  repeat invitation for recurring meetings, and use managed rolling reservations
  only when a physical meeting room is needed. Use for meeting invitations, weekly
  schedules, room booking, reservation status and cancellation requests. Read this
  skill before sending invitations or reserving rooms; never split a recurring
  invitation into separate emails per occurrence.
---

# DigiBridge Meeting

Resolve the meeting intent and identities first, then choose the workflow below.
Read [meeting-playbook.md](references/meeting-playbook.md) for repeat fields,
managed plan execution, partial outcomes, retries and cancellation.

## Sources and identity

- Read `/root/.openclaw/workspace/data/contact_map.json` for confirmed names,
  sender IDs, aliases and emails, then `project_context_map.json` for relevant
  chat/project patterns. Read only the relevant people and context.
- “我 / 我自己 / self” means the current message sender, never a global alias or
  the application's sending mailbox. Never put a response's `sender_email` in
  the attendee list unless it independently matches an explicitly requested person.
- Prefer confirmed company email addresses. Directory tools can help identify a
  person, but `list_employees` does not supply email addresses; resolve the email
  from the contact map or ask. Never construct emails from name patterns.
- Resolve a room's numeric ID with room tools and the booker's actual WeCom
  userid with confirmed identity data; an email or display name is not a userid.
- Ask once if recipients, an external address, the physical room, or the meeting
  period is ambiguous. Reuse explicit authorization and confirmed patterns;
  do not reconfirm each automatic reservation in an already-authorized series.

## Workflow selection

| User intent | Tool / behavior |
|---|---|
| Single calendar invitation, no physical room | `send_schedule_email`, omit `repeat` |
| Weekly recurring invitation, no physical room | `send_schedule_email` with native `repeat`; one invitation for the whole series, no managed plan or worker |
| Invitation plus physical room, single or weekly | `create_managed_meeting`; sends the invitation once and manages room booking separately |
| Standalone single room booking without invitations | `book_meeting_room` after checking availability; preserve `booking_id` |
| Single Tencent Meeting invitation | `send_meeting_email` with confirmed `meeting_admin_userid` |

The public repeat model currently supports weekly recurrence. Never simulate
unsupported monthly/yearly rules or recurring Tencent Meeting invites with a
batch of single invitations. Explain the current tool limitation and ask about
an acceptable supported alternative or client-side creation.

## Timing and rooms

- Default to Beijing Time / `Asia/Shanghai` unless the user specifies otherwise.
  Resolve relative dates against the current runtime date. Use executable date
  arithmetic to calculate timestamps, weekdays and repeat end boundaries;
  never compute Unix timestamps mentally or reuse old transcript timestamps.
- Confirm first occurrence, duration and inclusive repeat end date/count. Do not
  silently choose “a few months” or an unbounded series when the end is unclear.
- A required physical room comes from the user's request or confirmed in-office
  context, not merely from the fact that coworkers are attending.
- `location` is only text. It never reserves a room. Check candidate rooms before
  choosing one; use the managed workflow for invitations requiring a room.
- Never additionally call `send_schedule_email` or `book_meeting_room` for a
  managed series. That duplicates invites/reservations. Never keep a manual
  month-long loop or rely on chat memory for later room bookings.
- If the managed worker is unavailable, report the failure. Do not switch to
  separate invitation emails or claim future rooms will be booked automatically.

## Results, changes and durable context

- Treat invitation and room outcomes independently. Describe a native repeat
  invitation as sent only when confirmed; list which room dates are booked,
  pending the booking window, conflicting, failed or unknown. `complete=false`
  must not become “all meetings and rooms arranged”.
- Query a managed plan for status; retain its `plan_id` and use the original
  message ID as `request_id` for creation retries. Do not recreate the plan or
  resend the invitation to fix room failures.
- Cancellation of the plan stops future booking work and attempts known room
  cancellations. It does not cancel the already-sent calendar invitation with
  current tooling. Disclose exactly which parts were cancelled.
- Preserve useful confirmed project/attendee/room preferences in
  `project_context_map.json`; this is not a reservation ledger. See
  [project-context-schema.md](references/project-context-schema.md) when updating
  that structure. Actual booking states belong to the meeting service's store.
- Credentials belong to server runtime configuration. Do not request, read into
  chat, copy into the skill, or print API keys, application secrets or tokens.
