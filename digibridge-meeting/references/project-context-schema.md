# Project Context Schema

## Purpose

`/root/.openclaw/workspace/data/project_context_map.json` stores reusable project and chat collaboration patterns that are not pure identity data.

Use it to remember:

- which chats usually correspond to which projects
- who usually participates in a project's meetings
- rough responsibilities for each person on a project
- recurring attendee patterns by topic
- room preferences by project or chat

## Base shape

```json
{
  "version": 1,
  "projects": [],
  "contexts": [],
  "global": {}
}
```

## `projects[]`

Recommended fields:

- `id`: stable local project id, such as `talkie`
- `name`: human-readable project name
- `aliases`: alternative names seen in chat
- `members[]`:
  - `person_id`: references `contact_map.json -> people[].id`
  - `roles`: rough responsibilities such as `pm`, `dev`, `qa`, `design`, `ops`, `stakeholder`
- `meeting_patterns[]`:
  - `topic_keywords`: words that hint at a recurring meeting type
  - `attendee_ids`: usual attendee set for that topic
  - `notes`: optional explanation
- `room_preferences`: preferred room names for that project
- `notes`: short factual note

## `contexts[]`

Recommended fields:

- `chat_id`: platform chat id
- `project_ids`: projects commonly discussed in that chat
- `default_attendee_ids`: attendee set usually appropriate when the chat requests an internal meeting
- `notes`: short factual note

## `global.meeting_learning`

Suggested fields:

- `reuse_confirmed_patterns`: whether confirmed patterns should be reused automatically
- `require_confirmation_before_new_pattern`: whether a new inferred pattern must be confirmed before storing

## Maintenance rules

- Store only confirmed or strongly evidenced patterns.
- Keep roles rough and practical, not org-chart perfect.
- Update after real scheduling outcomes, especially when attendee sets were explicitly confirmed.
- Prefer project-level patterns for reusable logic and context-level patterns for chat-local defaults.
- Never duplicate identity details here; refer to people by `person_id`.
