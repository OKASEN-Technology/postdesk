# Troubleshooting

Start with `postdesk doctor` (with Docker: `docker compose exec app
postdesk doctor`). It checks, in order: the Claude key, `postdesk.toml`,
the database, the migrations, the Instagram token, that your public
storage really is public, the month's spend, and the Telegram bot. Fix the
first `FAIL` it prints and run it again.

## Token

**`Graph GET /me/accounts failed (400): Invalid OAuth access token`** or
**`(#190) ... Session has expired`**. The token is dead: the user token
from the Graph API Explorer lasts an hour (60 days once extended), and any
token dies when the Facebook password changes or the app is removed from
the account. Repeat steps 2 and 3 of docs/instagram-setup.md to get a
fresh Page token; those do not expire.

**`The token sees no Facebook Page with a linked Instagram professional
account`**. In the Facebook popup when generating the token, both the Page
and the Instagram account must be ticked. Generate it again and tick both.
Also check the account is linked to that Page (Instagram app → Settings →
Accounts Center).

**`The token reaches several Instagram accounts`**. Set
`INSTAGRAM_USER_ID` to the one you mean; the ids are in the message.

## Publishing

**A post is `failed` with `Media not fetchable` / `(#9004)`**. Instagram
could not download a slide from the URL the queue gave it. Almost always
`PUBLIC_BASE_URL` points somewhere Instagram cannot reach (localhost, a
LAN address, a box behind NAT, a firewall). `postdesk doctor` fetches a
test image the way Instagram does and says what it got. Fix the URL or
switch to a bucket (docs/deploy.md), open the post and **Back to draft**,
then approve it again.

**A post is `failed` with `... is not reachable by Instagram`**. The same
thing, caught before Instagram was asked: `PUBLIC_BASE_URL` is a localhost
address.

**A post sits in `publishing`**. The publisher marks a post `publishing`
before talking to Instagram, so a crash mid-way leaves it there rather
than posting it twice on the next pass. If it has been more than an hour:
open the account on Instagram and look. If the post is live, open it in
the queue and **Reject** it (the record stays; the post is up). If it is
not live, **Back to draft** and approve again. Do not send it back to
draft without looking; that is how a post goes out twice.

**`The publisher only posts between 06:00 and 21:00`** (the editor's
warning). `[schedule].publisher_hours` in postdesk.toml. A post approved
for a time outside the window goes out at the next pass inside it. Widen
the window, or `postdesk cron --force` for a one-off.

**`publishing quota is used up`**. Instagram allows 100 published posts
per rolling 24 hours. `postdesk instagram whoami` shows the count.

**A post was approved but nothing happened at its time**. Is the loop
running? `docker compose logs app` shows a `scheduler:` line every 15
minutes. If you run `serve --no-scheduler`, your own cron has to run
`postdesk cron`. Is the time inside `publisher_hours`? Is the clock right?
Times are stored in UTC and shown in `[account].timezone`.

## The queue

**`post is approved; this needs draft`** when editing. By design: an
approved post is immutable, so what was approved is exactly what goes out.
Unapprove it, edit, approve again.

**`asset ... is image/png; Instagram takes JPEG only`**. Only when a post
was created through the API with a PNG asset. `postdesk queue`, the web
form and `POST /api/assets` convert to JPEG; use those.

**`carousel takes 2–20 images`**, **`a story is one image`**, **`1080×1920
is outside the feed's 4:5 to 1.91:1 range`**. Instagram's limits. A Story
is 9:16; a feed post is between 4:5 (portrait) and 1.91:1 (landscape); a
carousel's slides must all be the same size.

**The recommended time keeps landing on the same day**. One feed post and
one Story a day by default (`max_feed_per_day`, `max_story_per_day`); a
rejected or failed post frees its day. `postdesk suggest-times
--overwrite` recomputes every draft.

## The web page

**It asks me to set a password, but I set one**. The password lives in the
database (hashed) unless `POSTDESK_ADMIN_PASSWORD` is set in `.env`. A new
database (a wiped volume) means a new password. To reset a forgotten one,
set `POSTDESK_ADMIN_PASSWORD` in `.env` and restart; it takes precedence.

**Signed out after every restart**. Set `POSTDESK_SECRET_KEY` in `.env` to
a long random string; without it one is generated and stored in the
database, so a wiped database also wipes sessions.

**Thumbnails are broken in the queue**. `/media/<id>` is served from
`PUBLIC_BASE_URL`; if that is a public address and you are opening the
page on a private one, the browser cannot reach it. Open the page at
`PUBLIC_BASE_URL`, or set it to what you use.

**"The web UI has not been built into this install"**. You are running
from a checkout without building it: `cd web && npm ci && npm run build`,
then restart. The Docker image has it built in.

## What each state means

| State | Meaning | What you can do |
|---|---|---|
| to review (`draft`) | filed, with a recommended time | edit, approve with a time, reject |
| scheduled (`approved`) | a person approved it; it goes out at its time | unapprove (to edit), reject |
| `publishing` | the publisher has claimed it and is talking to Instagram | wait; see above if it sticks |
| `published` | live; the permalink is in the post | nothing |
| `failed` | Instagram said no; the error is in the post | back to draft, reject |
| `rejected` | a person dropped it | nothing |

## Telegram

**The bot does not answer.** `postdesk telegram status` says which bot the
token belongs to and how updates arrive. If it says *webhook*, Telegram
must reach `PUBLIC_BASE_URL/api/telegram/webhook` over https; behind NAT
use `[telegram].mode = "polling"`. Two copies of postdesk with the same
token take each other's updates: run one.

**"This bot is private."** The message came from a chat that is not the
paired one. `postdesk telegram status` shows which chat is paired;
`postdesk telegram unlink`, then `/start` from the right chat and pair it.

**"that code is not one the bot gave out in the last 30 minutes"**. Send
`/start` again for a fresh code.

**"2 cards are at Brief ready; reply to the card's message to say
which"**. More than one card fits the command. Reply to the message about
the card you mean (`/board` lists them).

## The agents stopped

**"The agents have stopped: this month's runs cost ..."** The spend
reached `[llm].monthly_usd`. Raise it in Settings (it applies at once) or
wait for the month to turn; the queued jobs then run. Scheduled posts go
out either way.

**"paused by ..."** Someone sent `/pause` or pressed Pause. `/resume`, or
Resume in Settings.

## Pictures

**"wikimedia found nothing usable"**. Nothing under an open licence
matched; try another query in the card's thread, or attach a photo.

**"... is not a business or creator account Instagram lets us read"**.
Business Discovery reads only professional accounts. Ask the person for a
photo and attach it with their credit.

