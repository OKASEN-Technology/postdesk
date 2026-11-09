# Notices

postdesk tells you when something needs you or has happened. The Telegram
bot is the two-way channel (README, Appendix A). Besides it, or instead of
it, three one-way notifiers send the same notices.

## What is sent

| Event | When | Carries |
|---|---|---|
| `filed` | a card lands in Planned (the scout, a link, the board) | its title and source |
| `brief_ready` | the partner's brief is ready | the brief, a link to **Approve** |
| `partner_reply` | the partner answers you in a card's thread | its reply |
| `awaiting_approval` | the editor passed a made post | the summary, the recommended time, a link to **Schedule** |
| `scheduled` | a post is scheduled | the time |
| `live` | it posted | the links |
| `on_hold` | an agent or the publisher held a card | the reason |
| `measured` | a week after it went live | reach, saves, shares, profile visits |
| `spend_ceiling` | the month's spend reached `[llm].monthly_usd` | the spend and the ceiling (once a month for each ceiling) |

Telegram gets every event, as messages with buttons. A move you made in
the chat is not echoed back to it.

## Turning one on

Each notifier needs a table under `[notify]` in `postdesk.toml` and its
secret in `.env`. `events` limits it to some events (default: all);
`enabled = false` turns it off without deleting it.

```toml
[notify.slack]                         # SLACK_WEBHOOK_URL in .env
events = ["brief_ready", "awaiting_approval", "live", "on_hold", "spend_ceiling"]

[notify.webhook]                       # NOTIFY_WEBHOOK_URL in .env

[notify.email]                         # SMTP_* or RESEND_API_KEY, and SMTP_FROM, in .env
to = "you@example.com"
```

- **Slack**: an [incoming webhook](https://api.slack.com/messaging/webhooks)
  URL.
- **Webhook**: a JSON `POST` to a URL of yours:
  `{"event", "title", "text", "card_id", "links", "sent_at"}`. With
  `POSTDESK_SECRET_KEY` set, `X-Postdesk-Signature: sha256=<hex>` is the
  HMAC-SHA256 of the body with that key.
- **Email**: through your SMTP server (`SMTP_HOST`, `SMTP_PORT`,
  `SMTP_USER`, `SMTP_PASSWORD`) or [Resend](https://resend.com)
  (`RESEND_API_KEY`), from `SMTP_FROM`.

**Settings -> Notifiers** shows which are on and has a **Send a test**
button for each.

Notices wait in a table and are sent by the worker; one that fails is
tried again on the next pass, five times at most, and one older than two
days is dropped.

## The links in a notice

A one-way notice cannot have buttons, so the brief carries an **Approve**
link and the finished post a **Schedule for ...** link. Each is signed
with the app's secret, names one card and one action, and expires after
48 hours. Opening it shows what it will do and asks for one more tap; only
that tap acts, so a mail client or a chat app fetching the link for a
preview changes nothing. The move is logged on the card as `link`, and it
goes through the same gate as the board: it fails if the card has moved on.

Whoever holds the link can make that one move, so send notices only to
channels only you read.
