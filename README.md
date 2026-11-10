# postdesk

An Instagram manager you run yourself, driven from Telegram, with Claude
writing the posts and you approving every one.

> **Status: everything below works.** The scheduler, the maker, the board
> with its planner and editor, the Telegram bot, the image sources, the
> settings screen and the spend ceiling. What is next is on the
> [roadmap](docs/roadmap.md).

You tell it what you post about: a blog feed, a folder of Markdown, or a
link you send it. It proposes a post as a card. Claude writes a brief and
talks it over with you in Telegram. You approve. Claude writes the slide
copy, the caption and the alt text, renders a carousel and a Story in your
theme, and a second, independent Claude pass checks the words, the numbers,
the image credits and the accessibility. You see the preview in Telegram,
tap **Schedule**, and it posts through Instagram's official API at that
time. A week later it tells you how the post did.

Nothing is ever posted without you approving the exact post. There is no
scraping, no auto-liking, no DMs, no follower games.

## What you need

- An **Instagram professional account** (Business or Creator) linked to a
  **Facebook Page**. Instagram's publishing API only works for these.
- A **Meta developer app** (free). The setup page below walks you through it.
- Somewhere to run it: a laptop that stays on, a small VPS, or a hosting
  service. It needs **Docker**. Posting requires the machine to be
  reachable from the internet, or an S3-compatible bucket; the setup
  explains both.
- An **Anthropic API key** for Claude, to have posts written
  ([get one here](https://platform.claude.com/); a typical post costs a
  few cents). Queueing hand-made posts needs none.
- A **Telegram account**, for the daily chat. Optional: everything also
  works from the web page.

## Install in 15 minutes

```bash
git clone https://github.com/OKASEN-Technology/postdesk
cd postdesk
cp .env.example .env
cp examples/postdesk.toml postdesk.toml
```

Open `postdesk.toml` and set your timezone and handle under `[account]`;
the rest can wait. `.env` can stay as it is for now. Start it:

```bash
docker compose up -d
```

Open http://localhost:8080 and set a password. Then run the checklist:

```bash
docker compose exec app postdesk doctor
```

It tells you what is still missing, in order. The first thing will be
Instagram.

## Connect Instagram

This is the only fiddly part, and it is Meta's, not ours. Follow
[docs/instagram-setup.md](docs/instagram-setup.md). In short:

1. Create an app at developers.facebook.com, type **Business**, and add the
   **Instagram** product with **API setup with Facebook login**.
2. In the Graph API Explorer, generate a user token with the five
   permissions the page lists, ticking both your Page and your Instagram
   account in the popup.
3. Put `META_APP_ID`, `META_APP_SECRET` and `INSTAGRAM_ACCESS_TOKEN` in
   `.env`, then run:

   ```bash
   docker compose exec app postdesk instagram page-token
   ```

   It prints `INSTAGRAM_PAGE_TOKEN` and `INSTAGRAM_USER_ID`. Put those in
   `.env` and `docker compose up -d` again; they do not expire, and the
   token from step 2 is no longer needed.
4. `docker compose exec app postdesk instagram whoami` shows your account
   and your daily publishing quota. You are connected.

The last thing `doctor` checks is that Instagram can fetch your images:
your machine has to be reachable at `PUBLIC_BASE_URL`, or you give it a
bucket. [docs/deploy.md](docs/deploy.md) has both.

## Make your first post

The web page does everything the bot does: the **Board** at
http://localhost:8080 is where cards are planned, approved and scheduled
([below](#have-claude-make-the-post)), and the **Queue** holds every post. This section is the smallest start: a post you made
yourself.

Make the slides yourself for now (1080×1350 PNG or JPEG for the feed,
1080×1920 for a Story), write the caption in a text file with the alt
text after a `---` line:

```
Three loaves, one weekend.

The recipe is at the link in bio.

#homebaking #sourdough

---
ALT TEXT
1: A sourdough loaf on a board, scored in a leaf pattern.
2: The crumb, cut open.
```

Queue it:

```bash
docker compose exec app postdesk queue slides/01.png slides/02.png --caption caption.txt --title "Weekend loaves"
```

(With Docker, put the files somewhere the container can see them, or use
the web page's **New post** form, which does the same thing.) It checks
the images and the caption against Instagram's limits, stores them, and
files a draft with a recommended time: the next free evening slot from
`[schedule]` in `postdesk.toml`, in your timezone.

Open the queue at http://localhost:8080. Check the slides, fix the alt
text, keep or change the time, and tap **Approve & schedule**. At that time
the publisher posts it and the queue shows the link. A post you have
approved cannot be edited; unapprove it first.

To post something right now, by hand, there is also:

```bash
docker compose exec app postdesk post slides/01.png slides/02.png --caption caption.txt            # a dry run
docker compose exec app postdesk post slides/01.png slides/02.png --caption caption.txt --publish  # posts it
```

## Have Claude make the post

**From the board.** Add a feed to `[sources]` in `postdesk.toml`
(`rss = { url = "https://example.com/feed.xml" }`), or type a title and
some text (or just a link) into **Post about this** on the board. The
scout files each new item as a card in *Planned*, at most five a run,
every two hours between 06:00 and 21:00 (`[scout]`), and the partner
writes a brief in the card's thread: the angle, the slides, which
pictures to use, and any question it has for you. Answer in the thread,
attach a photo with its credit if you have a better one, and press
**Approve** when the brief is right. The maker makes the post, the editor
reads it in a fresh context and either passes it to *Awaiting approval*,
sends it back for a revision of the words (twice at most, `[review]`), or
holds it with the reason. Open the drafts, change anything by hand, and
press **Schedule** with the recommended time or your own. Only you can
move a card into *Approved* or *Scheduled*. `postdesk scout` runs the
scout by hand; `postdesk worker` runs the agents in their own process if
you start the app with `serve --no-worker`.

**From a file.** Write what you want to post about as a Markdown file
with a little front matter: the title, the date, a summary, the link, an
optional photo with its credit, and any numbers the slides may use.
[`examples/article.md`](examples/article.md) is a complete one. Then:

```bash
docker compose exec app postdesk make examples/article.md
```

It plans the post from a template (`article` by default: a cover, three
key points, a number if the piece has one, a call to action, and a
Story), has Claude write every slide's words, the caption and the alt text
in the voice of your [`voice.md`](examples/voice.md), checks the result
(every number must appear in your file, no em dashes, none of your retired
phrases, your hashtag limit) and sends problems back to the model, renders
each slide in your theme, and files the carousel and its Story as drafts
with recommended times. Open the queue, read it, change a line on a slide
if you like (only that slide is re-rendered), and approve.

`--template explainer | spotlight | stat | list | story-only` changes the
shape ([docs/templates.md](docs/templates.md)); `--dry-run` shows the plan
and the brief without calling anything. A run costs a few cents and is
logged; `[llm].monthly_usd` in `postdesk.toml` is the ceiling.

## Running it every day

The publisher runs inside the app every 15 minutes within the hours you
set (`publisher_hours` in `postdesk.toml`, 06:00 to 21:00 by default), and
once a day collects how each recent post did. If you would rather run it
from your own cron, `docker compose exec app postdesk cron` is one pass.

The agents run inside the app too: one job at a time per card, retried
when the network or the API hiccups, and a card goes *On hold* with the
reason when Claude refuses or a reply is cut off. The board shows what
each card cost. When the month's spend reaches `[llm].monthly_usd`, the
agents stop: their jobs wait, nothing is held, every channel is told once,
and they start again when you raise the ceiling in **Settings** or the
month turns. Scheduled posts still go out. `/pause` in Telegram (or
**Pause** in Settings) stops the scout and the agents by hand.

## Run your day from Telegram

Make a bot with @BotFather, put its token in `.env`, and pair your chat
([Appendix A](#appendix-a-run-your-day-from-telegram) has every step). From
then on the bot sends you each brief with **Approve**, **Rethink** and
**Hold**, each finished post with its slides, caption, alt text and the
editor's checks and a **Schedule** button, a note when a post goes live
and a week later how it did, and a digest every morning. Reply to any of
its messages to talk to the partner about that card. Only your paired
chat can do anything; anyone else who finds the bot is told it is private.

The web page stays for editing by hand, for the settings, and for
everything the chat does.

Not on Telegram? `[notify]` in `postdesk.toml` sends the same notices,
one way, to Slack, to a URL of yours (signed), or by email; the brief and
the finished post carry a link that asks once more and then approves or
schedules ([docs/notify.md](docs/notify.md)).

## Make it yours

- **Timing, how many a day, hashtag limits.** `[schedule]` and `[voice]`
  in [`examples/postdesk.toml`](examples/postdesk.toml), with comments.
  Every time is in your timezone.
- **The queue by hand.** `postdesk queue`, `postdesk suggest-times`
  (recommended times for drafts that have none), and the HTTP API at
  `/api/docs`.
- **Your voice.** Copy [`examples/voice.md`](examples/voice.md) next to
  `postdesk.toml` and rewrite it; the maker reads the whole file before it
  writes. `[voice]` sets the hashtags it may use and the phrases it may not.
- **Your look.** A theme is a folder of colours, fonts and a wordmark;
  copy the default and point `[theme]` at yours ([docs/themes.md](docs/themes.md)).
- **The shape of a post.** Six templates are built in; add a YAML file to
  `templates/` for your own ([docs/templates.md](docs/templates.md)).
- **Where posts come from.** A feed, a link, a Markdown file or folder, or
  text you paste ([docs/sources.md](docs/sources.md)). `[partner]` lists
  the extra sites the partner may search to check a fact.
- **What the editor checks.** Six checks are built in; add your own, one
  sentence each, under `[review.extra_checks]`
  ([docs/guardrails.md](docs/guardrails.md)).
- **Model and spend.** `[llm]` picks the model and effort per agent and
  sets a monthly ceiling, after which the agents stop.
- **Pictures.** `[images].sources` says where a slide's picture may come
  from: the source's own page, Wikimedia Commons (open licences only), the
  subject's own Instagram account through the API, or a photo you attach.
  Each is stored with its credit, licence and where it was found; a rights
  holder's yes goes on file with `postdesk permission add` or in Settings
  ([docs/images.md](docs/images.md)).
- **From the browser.** **Settings** changes the account, the schedule, the
  voice, the sources, the image sources, the models and the ceiling without
  editing the file; each change is kept in the database over
  `postdesk.toml` and can be taken back.

## What it will never do

- Post without your approval of that exact post. An approved post cannot be
  edited; unapprove it first.
- Put a number in a post that is not in your source.
- Use a picture without recording where it came from and crediting it on
  the slide and in the caption.
- Scrape Instagram, message anyone, or touch anything but your own posts.
- Follow instructions found inside an article or a web page. Only you steer
  it.

## Hosting it

It is two small services (the app and the slide renderer) and a database.
[docs/deploy.md](docs/deploy.md) has recipes for a VPS with Docker
Compose, Fly.io, Railway and Render, and explains the one requirement
Instagram imposes: your slide images must be fetchable from a public URL,
which means either your server is reachable from the internet or you give
it an S3-compatible bucket (`STORAGE_S3_*` in `.env`). `postdesk doctor`
checks this for you.

## When something goes wrong

`postdesk doctor` first. Then
[docs/troubleshooting.md](docs/troubleshooting.md): token expired, post
stuck in "publishing", Instagram cannot fetch an image, the quota, and what
each state means.

## Contributing, licence, who made this

Apache-2.0. See [CONTRIBUTING.md](CONTRIBUTING.md). Built by
[OKASEN Technology](https://github.com/OKASEN-Technology) for our own
account and opened up because the setup was the hard part.

## Appendix A: run your day from Telegram

**1. Make the bot.** In Telegram, open a chat with @BotFather, send
`/newbot`, and give it a name and a username (it must end in `bot`).
BotFather answers with a token like `123456789:AA...`. Put it in `.env`:

```
TELEGRAM_BOT_TOKEN=123456789:AA...
```

and restart (`docker compose up -d`). `postdesk doctor` now says the bot
answers and that no chat is paired.

**2. Pair your chat.** Send `/start` to your bot. It answers with a
six-letter code, good for 30 minutes. Paste it in **Settings -> Telegram**,
or:

```bash
docker compose exec app postdesk telegram link ABC123
```

The bot says hello and lists what it does. From now on it talks to that
chat only. `postdesk telegram test` sends a test message,
`postdesk telegram status` says which bot, which chat and how updates
arrive, and `postdesk telegram unlink` unpairs it.

No public address is needed: the bot asks Telegram for new messages (long
polling), so it works from a laptop. If `PUBLIC_BASE_URL` is a public
`https` address, it uses a webhook instead; `[telegram].mode` can force
either ([docs/deploy.md](docs/deploy.md)).

**3. A day.**

- *Morning.* The digest, at `[telegram].digest_at` (08:00): what needs
  you, what is scheduled this week, and the month's spend.
- *A new post.* The scout files new items from your feeds by itself. To
  add one, send `/new` and a link, or `/new` and a few lines of text.
- *A brief.* The bot sends it with its picture and the picture's licence.
  Tap **Approve**; or **Rethink** and reply with what to change; or reply
  to the message with a question, and the partner answers there. To offer
  a picture, send a photo in reply to the card's message with where it is
  from in the caption (`Photo: Jo Baker, mine to use`).
- *A finished post.* The slides, the caption, the alt text, the editor's
  checks and a recommended time. Tap **Schedule for ...**, or
  **Pick a time** and reply `tomorrow 18:00` or `fri 7:30`. **Rethink**
  sends it back to the maker with your note.
- *Later.* "Live" with the link when it posts; the numbers a week on.
  `/unschedule` pulls a scheduled post back; `/hold` and `/drop` put a card
  aside or take it off the board.

Any time: `/board` (every open card), `/queue` (what is scheduled),
`/status` (the account, the renderer, the month's spend), `/pause` and
`/resume`, `/help`. A command that is not a reply acts on the one card it
fits (the only brief waiting, say); if there are several, the bot asks you
to reply to the right card's message.

### Connections in Settings

The Connections section reports whether each service is configured and offers
an explicit connection test. Configured does not mean tested. The storage test
uploads a small test image; the renderer test creates a test slide. Instagram,
Telegram, storage and renderer settings continue to be managed by the server
administrator. Telegram must also be paired to a chat.

To save OpenAI or Claude keys in the UI, set a stable `POSTDESK_CREDENTIALS_KEY`
in the server's secret manager. Generate a Fernet key with
`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`.
Back it up securely outside the database and use the same key on every web and
worker instance. UI keys are verified before saving, encrypted in the database,
and override environment keys. Losing or changing the encryption key makes saved
credentials unreadable. Never put that key in the database or commit it to Git.
Key verification confirms provider authentication, not model access or billing.
