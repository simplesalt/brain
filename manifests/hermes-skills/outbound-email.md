---
name: outbound-email
description: Use when asked to email someone, or to reply to an email, as Dylan or Russell. Composes the message in the Simple Salt voice and sends it as styled HTML with the sender's signature through hermes-msg.
---

# Outbound email

Email goes out through exactly one tool: **`mcp_hermes_msg_send_message`** (the
hermes-msg server's `send_message`). Call that tool directly, by that exact name, with
the JSON arguments shown below. Do not write code, run a terminal command, call
`curl`, or wrap the call in another tool to send email.

It is sent at once: there is no draft or approval step, so send only when the person
asked you to send, and write it as if it will be read.

## Sender

- Only two senders exist: `dylan@simple-salt.com` (default) and `russell@simple-salt.com`.
  Pass the sender as `from_email`. Any other address is refused.
- Send as the person who asked, unless they name the other sender.
- The sender's signature is added for you. Never write a sign-off name, signature,
  links block or "--" yourself; end the body with a short closing line at most.

## Recipient

- If you only have a name, look for their address on the person's gbrain page.
- If no address is on record, ask. Never guess an address.
- Use `cc` (a list of addresses) only when asked.

## Writing

Follow the Simple Salt Marketing & Design Guide:
- Simple, clean, small words, small sentences.
- Voice: insightful, authentic, a little irreverent; punch up at the industry,
  never down at the reader.
- Say what you need from the reader in the first two lines.
- Keep it short: most emails are 3 to 6 short paragraphs or fewer.
- Lowercase "simple salt" when naming us.

## Format

Write `message` in light Markdown. It becomes styled HTML in the Simple Salt fonts and
colors, plus a plain-text copy:
- blank line between paragraphs
- `# heading` (rarely; only for longer notes)
- `- item` or `1. item` lists
- `> quoted text`
- `**bold**`, `*italic*`, `[link text](https://...)`

No raw HTML, images, tables or attachments: they are not supported.

## New email

Call `mcp_hermes_msg_send_message` with:

```json
{
  "verb": "new",
  "channel": "email",
  "from_email": "dylan@simple-salt.com",
  "to": "pat@example.com",
  "subject": "Thursday's call",
  "message": "Hi Pat,\n\nThanks for the time today. ..."
}
```

`verb`, `channel`, `to` and `message` are always required; `subject` is required for a
new email. Optional: `cc` (a list of addresses), `sig_block` (false only if asked for no
signature). Subject: plain, specific, under 8 words, no clickbait.

## Reply

1. Read the thread first: call `mcp_hermes_msg_get_thread` with
   `{"channel": "email", "thread_id": "<gmail thread id>", "from_email": "dylan@simple-salt.com"}`.
2. Call `mcp_hermes_msg_send_message` with
   `{"verb": "reply", "channel": "email", "from_email": "dylan@simple-salt.com", "to": "<last sender's address>", "thread_id": "<gmail thread id>", "message": "..."}`.
   The subject and quoting are handled for you.

## After sending

Tell the person who it went to, from which address, and the subject, with the `url`
from the result. If sending failed, say so with the error; do not retry with a
different sender.
