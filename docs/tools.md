---
title: "Tools"
description: "All 34 telethon-plus tools: what each does, its REST route and MCP name, and every parameter with its type, default and limits."
nav_order: 4
---

# Tools

Every capability is a tool. The same tool runs from REST (its route below) and from MCP (its name), with the same parameters, and answers the same JSON. REST takes the parameters as the JSON body for `POST`, `PATCH` and `DELETE`, and as query parameters for `GET`; path parameters such as `{id}` fill `message_id`. Over MCP they are the tool's arguments. Errors are listed on the [HTTP API](http-api.md#errors) and [MCP](mcp.md#tool-results-and-errors) pages.

Parameters are checked before anything reaches Telegram: an unknown field, a missing required one or a value outside its limits answers `400` (`invalid_argument` over MCP) with what was wrong.

## Chat references

`chat`, `from_chat`, `to_chat` and `user` take any of these:

| Form | Example |
| --- | --- |
| Username | `@psyb0t` |
| Phone number | `+1234567890` |
| t.me link | `https://t.me/psyb0t` |
| Numeric ID | `123456789` |
| Supergroup or channel ID | `-1001234567890` |
| Your own Saved Messages | `me` |

A numeric ID resolves from the [entity cache](throttling.md#entity-cache) first, which keeps the access hash Telegram needs with every ID, then through Telethon, and finally by a search of the account's dialogs. So the ID of any chat the account is in works, also after a restart, and the bare IDs this API returns can be passed back as they are. A user or bot the account has no dialog with resolves by ID only after a lookup by `@username` or link.

## Account and lookups

Who the account is, and turning chat references into profiles.

### get_me

`GET /api/me`

Return the authorized account profile.

No parameters.

### get_entity

`GET /api/entities`

Resolve a chat reference (username, phone, link, or ID) to a profile.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | Username, phone, t.me link, or numeric ID as string. |

### bulk_resolve

`POST /api/entities/bulk`

Resolve many chat references in one call, honoring the resolve-username rate limit. Returns per-handle success/error and populates the entity cache.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chats` | list of string | required | at least 1 item, at most 500 items | Chat references to resolve. |

### get_dialogs

`GET /api/dialogs`

List your dialogs (chats, groups, channels).

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `limit` | int | `20` | 1 to 200 | How many to return. |
| `archived` | boolean | `false` |  | List the archived dialogs instead of the main list. |
| `search` | string | none | not empty, at most 128 characters | Optional case-insensitive substring filter on title/username. |

## Reading messages

History, single messages and attachments. Reads charge the `read` bucket, one slot per 100 messages or per file part.

### get_messages

`GET /api/messages`

Read recent messages from a chat (newest first).

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `limit` | int | `20` | 1 to 200 | How many to return. |
| `offset_id` | int | `0` | at least 0 | Start below this message ID, to page back through history. `0` starts at the newest. |
| `search` | string | none |  | Only return matches for this text. |

### get_message

`GET /api/messages/{id}`

Fetch a single message by ID from a chat.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `message_id` | int | required | at least 1 | The message ID in that chat. |

### download_media

`GET /api/messages/{id}/media`

Download the media attachment of a message. Returns the file as base64-encoded bytes. Reject files larger than max_bytes.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `message_id` | int | required | at least 1 | The message ID in that chat. |
| `max_bytes` | int | `52428800` | 1 to 2147483648 | Refuse files larger than this many bytes. |

Over REST the answer is the raw file, with its type in `Content-Type` (or `application/octet-stream` when the sender's type is not a plain `type/subtype`), the file name in `Content-Disposition` and Telethon's media type in `X-Media-Type`. Over MCP the file comes back base64 encoded. A file larger than `max_bytes` is refused before it is downloaded, and a message whose media has no file, such as a location or a poll, answers `400`.

## Sending and changing messages

Every tool here changes something, so it answers `403` under `TELETHON_READ_ONLY` and only echoes under `TELETHON_DRY_RUN`.

### send_message

`POST /api/messages`

Send a text message to a chat.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | Target chat (username/ID/phone). |
| `text` | string | required | not empty, at most 4096 characters | Message text. |
| `parse_mode` | string | none |  | One of: md, markdown, html, or null for plain text. |
| `reply_to` | int | none |  | Message ID to reply to. |
| `silent` | boolean | `false` |  | Send without notification. |
| `link_preview` | boolean | `true` |  | Allow link previews. |
| `schedule` | datetime | none |  | Optional UTC datetime to schedule the send. ISO 8601 string; must be in the future. |

### send_file

`POST /api/messages (with file_url)`

Download a file from an HTTP(S) URL and send it to a chat. Use force_document=true to send as a generic file instead of letting Telegram pick a media type.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `file_url` | string | required |  | HTTPS URL of the file to download and forward to Telegram. |
| `text` | string | none | at most 4096 characters | Optional caption/message text to attach to the file. |
| `parse_mode` | string | none |  | `md`, `markdown`, `html`, or unset for plain text. |
| `silent` | boolean | `false` |  | Send without a notification. |
| `force_document` | boolean | `false` |  | Send as a generic file instead of letting Telegram pick a media type. |
| `max_bytes` | int | `52428800` | 1 to 2147483648 | Refuse files larger than this many bytes. |

The server downloads the file into `TELETHON_DOWNLOAD_DIR` and deletes it after the send, whatever happens. A URL whose extension or `Content-Type` says PNG or JPEG goes out as a photo; anything else goes out as a document named after the last part of the URL. A video goes out as a playable video with its width, height and duration read from the file, and an audio file carries its duration, title and artist. With `force_document=true` a video goes out as a plain file. A download that fails, returns an empty file, or redirects to anything but `http(s)`, answers `400`. The upload charges the `send` bucket one slot per part Telethon uploads. Anyone with the auth key can make the server fetch any URL it can reach; see [Security](security.md#what-it-stores-and-where-it-connects).

### forward_messages

`POST /api/messages/forward`

Forward one or more messages between chats.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `from_chat` | string | required |  | Chat to forward from. |
| `to_chat` | string | required |  | Chat to forward to. |
| `message_ids` | list of int | required | at least 1 item, at most 100 items | Message IDs in the chat. |
| `silent` | boolean | `false` |  | Send without a notification. |

### edit_message

`PATCH /api/messages/{id}`

Edit a message you sent.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `message_id` | int | required |  | The message ID in that chat. |
| `text` | string | required | not empty, at most 4096 characters | Message text. |
| `parse_mode` | string | none |  | `md`, `markdown`, `html`, or unset for plain text. |
| `link_preview` | boolean | `true` |  | Show a preview for links in the text. |

### delete_messages

`DELETE /api/messages`

Delete messages by ID.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `message_ids` | list of int | required | at least 1 item, at most 100 items | Message IDs in the chat. |
| `revoke` | boolean | `true` |  | Delete for everyone, not just you. |

### mark_read

`POST /api/messages/read`

Mark messages in a chat as read.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `max_id` | int | `0` | at least 0 | Mark up to this message ID. 0 = all. |

### pin_message

`POST /api/messages/{id}/pin`

Pin a message in a chat.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `message_id` | int | required | at least 1 | The message ID in that chat. |
| `silent` | boolean | `true` |  | Pin without notification. |
| `pm_oneside` | boolean | `false` |  | In DMs, pin only for yourself. |

### unpin_message

`POST /api/messages/{id}/unpin`

Unpin a previously pinned message.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `message_id` | int | required | at least 1 | The message ID in that chat. |

### set_reaction

`POST /api/messages/{id}/reactions`

React to a message with an emoji.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `message_id` | int | required | at least 1 | The message ID in that chat. |
| `emoji` | string | required | not empty, at most 16 characters | The reaction emoji. |
| `big` | boolean | `false` |  | Animate as 'big' reaction. |

### remove_reaction

`DELETE /api/messages/{id}/reactions`

Remove your reaction from a message.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `message_id` | int | required | at least 1 | The message ID in that chat. |

## Chats

Members, joining, leaving and creating. Joins charge the `join` bucket (5 an hour) and creations the `create` bucket.

### get_participants

`GET /api/participants`

List members of a group or channel.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `limit` | int | `100` | 1 to 1000 | How many to return. |
| `search` | string | none |  | Only return matches for this text. |

### create_group

`POST /api/chats`

Create a new supergroup or broadcast channel.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `title` | string | required | not empty, at most 128 characters | Title. |
| `megagroup` | boolean | `true` |  | True = supergroup, False = broadcast channel. |

### delete_chat

`DELETE /api/chats`

Delete a supergroup or channel you own.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |

Only the chat's creator can delete it.

### join_chat

`POST /api/chats/join`

Join a public channel or supergroup.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |

### leave_chat

`POST /api/chats/leave`

Leave a channel, supergroup or basic group.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |

### join_via_invite

`POST /api/chats/invite`

Join a private chat via t.me/+hash invite link.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `invite` | string | required |  | Private invite hash (the part after t.me/+) or the full link. |

### get_linked_chat

`GET /api/chats/{chat}/linked`

Resolve a channel's linked discussion group (if any). Returns linked_chat_id and the linked entity profile.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |

## Moderation

These need a supergroup or channel where the account is an admin with the matching right. A basic group or a user as `chat` is refused with a 400, and `promote_user` and `demote_user` refuse anything but a user as `user`.

### ban_user

`POST /api/chats/{chat}/admin/ban`

Ban a user from a supergroup/channel. until_seconds=0 = permanent.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `user` | string | required |  | The user, in any [reference form](#chat-references). |
| `until_seconds` | int | `0` | at least 0 | Ban length in seconds. `0` bans forever; Telegram also treats under 30 seconds or over 366 days as forever. |

### unban_user

`POST /api/chats/{chat}/admin/unban`

Lift a ban on a user in a supergroup/channel.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `user` | string | required |  | The user, in any [reference form](#chat-references). |

### kick_user

`POST /api/chats/{chat}/admin/kick`

Kick a user from a chat (ban, then unban at once, so they can rejoin through an invite).

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `user` | string | required |  | The user, in any [reference form](#chat-references). |

It sends two requests, a ban and then an unban. If the unban fails, the user stays banned until you call `unban_user`.

### promote_user

`POST /api/chats/{chat}/admin/promote`

Grant admin rights to a user in a supergroup/channel. Each boolean toggles a specific permission. Pass title for the custom rank.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `user` | string | required |  | The user, in any [reference form](#chat-references). |
| `title` | string | none | at most 16 characters | Admin custom title. |
| `change_info` | boolean | `false` |  | Grant this admin right. |
| `post_messages` | boolean | `false` |  | Grant this admin right. |
| `edit_messages` | boolean | `false` |  | Grant this admin right. |
| `delete_messages` | boolean | `false` |  | Grant this admin right. |
| `ban_users` | boolean | `false` |  | Grant this admin right. |
| `invite_users` | boolean | `false` |  | Grant this admin right. |
| `pin_messages` | boolean | `false` |  | Grant this admin right. |
| `add_admins` | boolean | `false` |  | Grant this admin right. |
| `anonymous` | boolean | `false` |  | Grant this admin right. |
| `manage_call` | boolean | `false` |  | Grant this admin right. |

### demote_user

`POST /api/chats/{chat}/admin/demote`

Strip admin rights from a user.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `user` | string | required |  | The user, in any [reference form](#chat-references). |

## Polls

Polls and quizzes. Option indices are 0-based.

### create_poll

`POST /api/polls`

Create a poll in a chat. Set quiz=true with correct_option for quiz-style polls.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `question` | string | required | not empty, at most 255 characters | Poll question. |
| `options` | list of string | required | each not empty and at most 100 characters, at least 2 items, at most 10 items | Answer options, in order. |
| `multiple_choice` | boolean | `false` |  | Allow more than one answer. Not allowed for a quiz. |
| `quiz` | boolean | `false` |  | Make it a quiz with one correct answer. |
| `correct_option` | int | none | at least 0 | 0-based index of the correct answer. Required for a quiz, refused otherwise. |
| `solution` | string | none | at most 200 characters | Explanation shown after answering a quiz. Quiz only. |

### vote_poll

`POST /api/polls/{id}/vote`

Vote on a poll by 0-based option index/indices.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `message_id` | int | required | at least 1 | The message ID in that chat. |
| `options` | list of int | required | at least 1 item, at most 10 items | 0-based indices of the answers to vote for; more than one only on a multiple-choice poll. |

### get_poll_results

`GET /api/polls/{id}/results`

Fetch current results of a poll message.

| Parameter | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `chat` | string | required |  | The chat, in any [reference form](#chat-references). |
| `message_id` | int | required | at least 1 | The message ID in that chat. |

## Status

The live flood-protection state, also described in [Observability](observability.md).

### throttle_status

`GET /api/throttle/status`

Return current throttling state: bucket usage, adaptive multiplier, recent flood events, cache stats, safety flags.

No parameters.

### account_health

`GET /api/account/health`

Account-level health: are we connected, what's our flood-risk tier based on recent FLOOD_WAITs and the adaptive multiplier.

No parameters.
