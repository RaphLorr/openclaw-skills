# Schema And Rules

## Purpose

This file documents how `/root/.openclaw/workspace/data/contact_map.json` should evolve as a durable contact directory for scheduling and messaging workflows.

## File layout

```json
{
  "version": 1,
  "owner_id": "raphael",
  "people": [],
  "contexts": [],
  "global": {}
}
```

## `people[]`

Use one record per real person.

Recommended fields:

- `id`: stable local identifier, lowercase kebab-case or simple slug
- `profile.name`: preferred display name
- `profile.aliases`: stable nicknames, handles, sender ids used as names
- `wechat.sender_ids`: platform sender ids such as `raphael2002`
- `wechat.display_names`: observed WeChat names
- `email.primary`: best default email for scheduling
- `email.others`: secondary addresses
- `wecom.userid`: enterprise WeCom userid when known
- `wecom.name`: enterprise display name when known
- `meeting.default_invite`: whether this person should usually receive invites
- `meeting.default_required`: whether this person is usually required
- `tags`: useful labels such as `owner`, `digibridge`, `external`
- `notes`: short factual notes only

## `contexts[]`

Use for per-chat defaults, never for identity truth.

Recommended fields:

- `id`: chat id such as `wechat:dm:raphael2002`
- `channel`: provider family
- `chat_type`: `direct` or `group`
- `label`: human-readable label
- `defaults.attendee_ids`: default attendees for scheduling in that chat
- `defaults.required_attendee_ids`: attendees that should start as required
- `defaults.optional_attendee_ids`: attendees that should start as optional
- `meeting.preferred_room_names`: room preferences for that chat
- `meeting.default_subject`: default meeting title seed
- `meeting.auto_include_requester`: whether to include the current speaker automatically
- `policy.*`: chat-local safety flags

## `global`

Use for behavior defaults shared across contexts.

Examples:

- `resolution.prefer_local_mapping`
- `resolution.fallback_to_wecom_lookup`
- `resolution.auto_learn_confirmed_aliases`
- `meeting.default_duration_minutes`
- `safety.require_confirmation_before_send`

## What not to store in aliases

Never store pronouns or role words that depend on who is speaking.

Do not add values like:

- `我`
- `我自己`
- `本人`
- `自己`
- `self`
- `me`

These must be resolved at runtime to the current speaker.

## Merge policy

When new information arrives:

- merge into an existing person if sender id matches
- else merge if primary email matches
- else merge if `wecom.userid` matches
- else merge if `wecom.name` strongly matches an existing alias or surrounding context
- else merge if a strong alias match and surrounding context agree
- else create a new person record

When merging:

- preserve the existing `id`
- append newly confirmed aliases rather than replacing old ones
- populate `wecom.userid` and `wecom.name` when confidently known
- do not overwrite a non-empty primary email with a weaker candidate
- move superseded emails into `email.others` if still useful

## WeCom enrichment guideline

Use `list_employees` when a chat mentions someone who may be a DigiBridge employee but the local contact map is incomplete.
This is a guideline, not an always-on bulk sync.

Suggested flow:

1. Try local matching first with sender id, display name, alias, email, `wecom.userid`, and `wecom.name`.
2. If still unclear and the person is likely internal, inspect `list_employees`.
3. Merge only when identity is strong enough, such as matching `wecom.userid`, matching known email identity, or a strong Chinese-name match supported by context.
4. Write back useful employee fields:
   - `wecom.userid`
   - `wecom.name`
   - stable aliases from `english_name` or `alias` when useful
5. For DigiBridge employees, add `digibridge` and `employee` tags when confirmed.
6. If more than one employee could match, ask instead of forcing a merge.

For TAPD-related tasks, prefer `wecom.name` as the Chinese member nickname source whenever available.

## Company affiliation

When the user says a person is a DigiBridge employee, add `digibridge` to `tags`.
Use `external` for non-employees when useful for invite confirmation rules.

## Daily memory logging

Log meaningful updates in `memory/YYYY-MM-DD.md`, especially:

- newly confirmed email mappings
- merged duplicate identities
- policy decisions such as pronoun handling
- newly established group defaults
