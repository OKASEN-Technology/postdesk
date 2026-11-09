# postdesk

An Instagram manager you run yourself, driven from Telegram, with Claude
writing the posts and you approving every one.

> **Status: early.** The scheduler (queue, approval, publishing) is being
> built first; the planner, maker and editor follow. Watch the
> [milestones](docs/roadmap.md). Nothing here posts anything yet.

You tell it what you post about: a blog feed, a folder of Markdown, or a
link you send it. It proposes a post as a card. Claude writes a brief and
talks it over with you in Telegram. You approve. Claude writes the slide
copy, the caption and the alt text, renders a carousel and a Story in your
theme, and a second, independent Claude pass checks the words, the numbers,
the image credits and the accessibility. You see the preview in Telegram,
tap **Schedule**, and it posts through Instagram's official API at that
time. A day later it tells you how the post did.

Nothing is ever posted without you approving the exact post. There is no
scraping, no auto-liking, no DMs, no follower games.

## What you need

- An **Instagram professional account** (Business or Creator) linked to a
  **Facebook Page**. Instagram's publishing API only works for these.
- A **Meta developer app** (free). The setup page below walks you through it.
- An **Anthropic API key** for Claude. [Get one here](https://platform.claude.com/).
  A typical post costs a few cents; a busy month is a few dollars.
- A **Telegram account**. You will make a bot in two minutes.
- Somewhere to run it: a laptop that stays on, a small VPS, or a hosting
  service. It needs **Docker**. Posting requires the machine to be
  reachable from the internet, or an S3-compatible bucket; the setup
  explains both.

## Install in 15 minutes

```bash
git clone https://github.com/OKASEN-Technology/postdesk
cd postdesk
cp .env.example .env
```

Open `.env` and fill in two lines for now:

```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=123456:ABC...     # from @BotFather: /newbot, then copy the token
```

Start it:

```bash
docker compose up -d
```

Open http://localhost:8080 and set a password. Then in Telegram, open your
new bot and send `/start`. It replies with a code; paste the code into
**Settings → Telegram** in the web page. From now on the bot talks to you.

Then run the checklist:

```bash
docker compose exec app postdesk doctor
```

It tells you what is still missing, in order. The first thing will be
Instagram.

## Connect Instagram

This is the only fiddly part, and it is Meta's, not ours. Follow
[docs/instagram-setup.md](docs/instagram-setup.md); it has a screenshot
per step. In short:

1. Create an app at developers.facebook.com, type **Business**, and add the
   **Instagram** product with **API setup with Facebook login**.
2. In the Graph API Explorer, generate a user token with the permissions
   the page lists, then extend it to a 60-day token.
3. Put `META_APP_ID`, `META_APP_SECRET` and `INSTAGRAM_ACCESS_TOKEN` in
   `.env`, then run:

   ```bash
   docker compose exec app postdesk instagram page-token
   ```

   It prints `INSTAGRAM_PAGE_TOKEN` and `INSTAGRAM_USER_ID`. Put those in
   `.env`; they do not expire, and the 60-day token is no longer needed.
4. `docker compose exec app postdesk instagram whoami` shows your account
   and your daily publishing quota. You are connected.

## Make your first post

Send the bot a link:

```
/new https://yourblog.com/some-article
```

Within a minute it sends you a brief: the angle, the hook, what each slide
says, which numbers it will use, and any picture it found with its credit.
Reply to that message to change anything ("lead with the second point",
"drop the photo"); it answers and updates the brief. When it looks right,
tap **Approve**.

A few minutes later it sends the slides, the caption and the editor's
checks, with a suggested time. Tap **Schedule**, or type
`/schedule tomorrow 18:00`. At that time it posts, and sends you the link.

## Running it every day

Tell it where your posts come from in `postdesk.toml`:

```toml
[sources]
rss = { url = "https://yourblog.com/feed.xml" }
```

Every couple of hours it checks for new items and files a card for each.
Your day then looks like this in Telegram:

| The bot says | You do |
|---|---|
| "New post to plan: …" | nothing yet |
| the brief, with **Approve · Rethink · Hold** | reply with changes, or tap Approve |
| the slides and caption, with **Schedule · Pick a time · Rethink** | tap Schedule |
| "Scheduled for Thu 18:00" | nothing; `/unschedule` if you change your mind |
| the link, once it is live | nothing |
| the numbers, a week later | nothing |
| the morning digest | look at what is waiting on you |

Commands: `/new <text or link>`, `/board`, `/queue`, `/status`,
`/schedule [time]`, `/unschedule`, `/rethink <note>`, `/hold`, `/pause`,
`/resume`. Reply to any card's message to talk to the planner about it;
send a photo in reply to offer it a picture, and say where it is from.

## Make it yours

- **Voice.** Write `voice.md`: how you sound, what you never say. Claude
  reads it when writing and again when checking. There is an example in
  [`examples/voice.md`](examples/voice.md).
- **Look.** Pick a theme in `postdesk.toml`, or copy `renderer/themes/default`
  and change the colours, fonts and logo. `postdesk render --spec
  examples/slide.json --out test.jpg` shows you a slide.
- **Hashtags, timing, how many a day.** All in
  [`examples/postdesk.toml`](examples/postdesk.toml), with comments.
- **What a post looks like.** Templates in `templates/` say which slides a
  post has and what each carries. Start from `article.yaml`.
- **Which model.** Defaults are in `[llm]`. Set a monthly ceiling there;
  when it is reached the agents stop and the bot tells you.

## What it will never do

- Post without your approval of that exact post. An approved post cannot be
  edited; unschedule it first.
- Put a number in a post that is not in your source.
- Use a picture without recording where it came from and crediting it on
  the slide and in the caption.
- Scrape Instagram, message anyone, or touch anything but your own posts.
- Follow instructions found inside an article or a web page. Only you steer
  it.

## Hosting it

It is two small services and a database. [docs/deploy.md](docs/deploy.md)
has recipes for a VPS with Docker Compose, Fly.io, Railway and Render, and
explains the one requirement Instagram imposes: your slide images must be
fetchable from a public URL, which means either your server is reachable
from the internet or you give it an S3-compatible bucket (`STORAGE_S3_*` in
`.env`). `postdesk doctor` checks this for you.

## When something goes wrong

`/status` in Telegram, or `postdesk doctor`, first. Then
[docs/troubleshooting.md](docs/troubleshooting.md): token expired, post
stuck in "publishing", renderer not running, the quota, and what each card
state means.

## Contributing, licence, who made this

Apache-2.0. Themes, sources and image sources are the easiest places to
contribute; see [CONTRIBUTING.md](CONTRIBUTING.md). Built by
[OKASEN Technology](https://github.com/OKASEN-Technology) for our own
account and opened up because the setup was the hard part.
