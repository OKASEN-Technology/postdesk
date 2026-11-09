"""
Telegram, the daily interface (README "Running it every day"). The bot is
how the operator works with postdesk day to day: the brief arrives as a
message with Approve, Rethink and Hold; the made post arrives as its slides
with Schedule; a reply to any card's message is a comment on that card.

- `api`: the Bot API over httpx, nothing else.
- `pairing`: `/start` gives a code; the web UI's settings (or `postdesk
  telegram link <code>`) pairs that chat. Only the paired chat may act;
  every other chat is told the bot is private.
- `render`: Markdown to Telegram's HTML, the 4,096-character split, and
  each message the bot sends.
- `commands`: one inbound update -> a board action, through the same moves
  and gates as the web UI (`actor = telegram:<chat id>`).
- `bot`: the loop inside `postdesk serve`: long polling (no public URL
  needed) or a webhook (`PUBLIC_BASE_URL` on https), the notices, the
  daily digest.
"""
