# postdesk

An Instagram manager you run yourself, driven from Telegram, with Claude
writing the posts and you approving every one.

> **Status: the scheduler works.** You can queue a hand-made carousel or
> Story, approve it with a time in the web page, and it posts through
> Instagram's official API at that time. The planner, the maker (slides,
> captions, alt text written by Claude), the editor pass and the Telegram
> bot are the next [milestones](docs/roadmap.md). Until they land, the
> sections below marked *coming* describe where this is going, not what
> it does today.

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
- Somewhere to run it: a laptop that stays on, a small VPS, or a hosting
  service. It needs **Docker**. Posting requires the machine to be
  reachable from the internet, or an S3-compatible bucket; the setup
  explains both.
- *Coming:* an **Anthropic API key** for Claude, once the writer lands
  ([get one here](https://platform.claude.com/); a typical post costs a
  few cents), and a **Telegram account** for the daily chat.

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

## Running it every day

The publisher runs inside the app every 15 minutes within the hours you
set (`publisher_hours` in `postdesk.toml`, 06:00 to 21:00 by default), and
once a day collects how each recent post did. If you would rather run it
from your own cron, `docker compose exec app postdesk cron` is one pass.

*Coming:* the daily loop in Telegram. You will tell it where your posts
come from (`[sources]` in `postdesk.toml`), it will file a card for each
new item, write a brief and talk it over with you, make the slides, and
send you the preview with a **Schedule** button. The web page stays for
editing a caption by hand and for the settings.

## Make it yours

- **Timing, how many a day, hashtag limits.** `[schedule]` and `[voice]`
  in [`examples/postdesk.toml`](examples/postdesk.toml), with comments.
  Every time is in your timezone.
- **The queue by hand.** `postdesk queue`, `postdesk suggest-times`
  (recommended times for drafts that have none), and the HTTP API at
  `/api/docs`.
- *Coming:* your voice file ([`examples/voice.md`](examples/voice.md)),
  your theme, post templates, model choice and a monthly spend ceiling.

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

It is one small service and a database (the slide renderer joins in M1).
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
