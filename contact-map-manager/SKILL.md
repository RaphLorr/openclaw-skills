---
name: contact-map-manager
description: Maintain and query the local structured relationship data at `/root/.openclaw/workspace/data/contact_map.json` and `/root/.openclaw/workspace/data/project_context_map.json` for cross-session identity resolution, email mapping, chat-to-person linking, and person-to-project relationships. Use when adding, correcting, reviewing, or normalizing people records, WeChat nicknames and sender ids, email addresses, company affiliation, meeting preferences, project membership, role ownership, chat-to-project mapping, or maintenance rules for the local relationship knowledge base.
---

# Contact Map Manager

Use `/root/.openclaw/workspace/data/contact_map.json` as the structured source of truth for reusable people data.
Use `/root/.openclaw/workspace/data/project_context_map.json` as the structured source of truth for reusable person-to-project and chat-to-project relationships.

## Core model

- Keep stable identity in `contact_map.json -> people`.
- Keep chat-specific defaults and policies in `contact_map.json -> contexts` when they are primarily about identity resolution or invite defaults.
- Keep project membership, rough responsibilities, recurring attendee sets, and chat-to-project patterns in `project_context_map.json`.
- Keep parsing and safety defaults in each file's `global` section.
- Treat `MEMORY.md` and daily memory as commentary, not the primary structured directory.

## Edit rules

- Add only stable aliases to `people[].profile.aliases`, such as known nicknames, handles, sender ids spoken as names.
- Never store speaker-relative pronouns in aliases, including `我`, `我自己`, `本人`, `self`, or similar variants.
- Resolve those pronouns at runtime to the current speaker instead of binding them to a fixed person.
- Prefer one person record per real person; merge aliases into the same record instead of creating duplicates.
- Keep `email.primary` as the most reliable scheduling address; place secondary addresses in `email.others`.
- Treat `wecom.name` as the preferred formal Chinese name for DigiBridge internal people, especially when TAPD member nicknames need to be filled.
- Mark company affiliation explicitly in `tags` or `notes` when known, for example `digibridge`.
- Keep unknown values as empty strings or empty arrays rather than guessing.

## WeChat discovery queue (contact_map_pending.json)

`/root/.openclaw/workspace/data/contact_map_pending.json` holds group members
whose WeChat sender id is not yet in `contact_map.json`. The wechat plugin appends
to it automatically and DMs Raphael the unresolved entries; it never edits
`contact_map.json` itself. Resolving an entry is your job.

Each entry looks like:
`{ "wxid": "afu7466", "displayName": "Sean", "groupId": "...@chatroom", "matchedPerson": null, "matchConfidence": "none" }`

When Raphael tells you who a pending wxid is, ALL THREE of these must happen —
missing any one of them silently defeats the purpose:

1. Add the `wxid` to that person's `people[].wechat.sender_ids`.
   Without this the bot still does not recognise the person, no matter how
   complete the rest of the record is. This is the single most-missed step.
2. Add the entry's `displayName` to `people[].wechat.display_names`.
   Use the nickname from the pending entry, NEVER the wxid itself.
3. Remove that entry from `contact_map_pending.json`.
   A resolved person left in the queue keeps showing up as unresolved.

Then create or update the person record per the rules below (formal name,
`wecom.userid`, `email.primary`, tags, notes — whatever Raphael supplied).
Create a new person only when no existing record fits; prefer attaching the wxid
to the person who is already there.

`matchedPerson`/`matchConfidence` are hints computed at discovery time and may be
stale or wrong. Never act on them alone — only map what Raphael has confirmed.

When Raphael says to ignore someone, just remove the entry from pending and leave
`contact_map.json` untouched.

After resolving, verify: the wxid appears in exactly one person's `sender_ids`,
`display_names` contains no wxids, and the entry is gone from pending.

## Update and query workflow

1. Read `/root/.openclaw/workspace/data/contact_map.json` and `/root/.openclaw/workspace/data/project_context_map.json` before editing or answering relationship questions.
2. Find whether the person already exists by sender id, display name, alias, email, `wecom.userid`, or `wecom.name`.
3. If the person is still unclear and may be a DigiBridge employee, use `list_employees` as a fallback enrichment source.
4. Update the existing person when it is clearly the same person.
5. Create a new person only when no existing record fits.
6. Update `contact_map.json -> contexts` only for chat-specific defaults such as default attendees or auto-include rules tied to identity resolution.
7. Update `project_context_map.json` for project membership, rough roles, recurring attendee sets, and chat-to-project mappings.
8. When asked relationship questions, answer from both files together: who a person is, which project they are tied to, what role they roughly play, and which chats commonly map to that project.
9. Record meaningful changes in the current daily memory file.

## Resolution rules to preserve

- `contact_map.json -> people` is global and reusable across chats.
- `contact_map.json -> contexts` is local to a chat and may override defaults without changing the person record.
- `project_context_map.json` stores reusable project relationships and should be used to answer questions like who usually joins which project meetings or who roughly owns a topic.
- Direct mentions like `黄皇Raph` or `raphael2002` may map through stable aliases.
- First-person phrases must map to the current speaker, not to `owner_id`.

## Data quality checks

After edits, verify:

- each `people[].id` is unique
- no duplicate sender id appears on multiple people unless intentionally shared
- no pronoun aliases exist in `people[].profile.aliases`
- `contact_map.json -> contexts[].defaults.attendee_ids` only reference valid `people[].id` values
- `project_context_map.json -> projects[].members[].person_id` only reference valid `people[].id` values
- JSON stays valid and formatted consistently

## References

- Read `/root/.openclaw/workspace/skills/contact-map-manager/references/schema-and-rules.md` when you need the schema, field conventions, and conflict-resolution rules.
- Read `/root/.openclaw/workspace/skills/digibridge-meeting/references/project-context-schema.md` when you need the project relationship schema.
