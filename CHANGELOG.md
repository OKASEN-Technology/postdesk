# Changelog

## Unreleased

### M2, the board, the planner and the editor

- **The board.** Each post is a card that moves through Inbox, Planned,
  Brief ready, Approved, Making, In review, Awaiting approval, Scheduled,
  Live (and On hold). Each card has its source's facts, a brief, a thread,
  attachments and a move log. Every move is one function with a
  precondition and an actor (`postdesk.board`).
- **Two gates.** Only the admin (the web session or the API token) moves
  a card into *Approved* or *Scheduled*. Anyone else is refused, and the
  refusal is logged on the card.
- **The scout** files each new item from `[sources]` as a card, at most
  `[scout].max_per_run` (5) a run, every `every_hours` (2) between 06:00
  and 21:00, each item once. `postdesk scout [--days N] [--source NAME]`
  and the board's **Scan now** run it by hand.
- **Sources** `rss` (any RSS or Atom feed: the item's page reduced to its
  article text, the feed's picture credited to the site) and `url` (one
  page). A `[sources]` entry may name its `type`, so two feeds can sit
  side by side.
- **The partner** writes the brief in the card's thread. It covers the
  angle, the guidance for the maker, which pictures to use (including ones
  you attach, with a credit), and its own slide plan when the card has no
  template. It asks you when something is open. It may search the web, but
  only on the source's site and `[partner].allowed_domains`. Every number in
  its brief must be in the source; one that is not is sent back, then
  left in the thread.
- **The editor** reads each made post in a fresh call: the source, the
  brief, every slide as an image and its provenance. Its six checks are
  voice, facts, rights, ai-label, accessibility and links, plus your own
  from `[review.extra_checks]`. Pass goes to *Awaiting approval*. Revise
  goes to **the revision turn** (words only, one slide re-rendered),
  `[review].max_rounds` times at most. Hold, or anything words cannot fix,
  goes On hold with the reason.
- **The job runner** (`postdesk.jobs`) is a table-backed queue worked by a
  thread in `postdesk serve`, or by `postdesk worker` (`--once` for one
  pass) with `serve --no-worker`. One running job per card, using `SKIP
  LOCKED` and a unique index. Transient errors are retried with backoff. A
  refusal, a cut-off reply or the spend ceiling puts the card On hold
  without a retry.
- **The queue and the board stay in step.** A card whose posts are all
  published goes Live with the links. A failure goes On hold, saying what
  is already live. Rejecting every post closes the card. Approving or
  unapproving in the queue screens moves the card as the gate would. A
  post made by hand with no card is filed in *In review* for the editor.
- **API:** `GET/POST /api/cards`, `GET /api/cards/{id}`,
  `POST /api/cards/{id}/move` (`{column, note, when}`),
  `/comments`, `/attachments`, `/events`, `POST /api/cards/{id}/partner |
  make | review`, `GET /api/runs`, `GET /api/sources`,
  `POST /api/sources/{key}/scan`, `POST /api/sources/manual`.
- **Web UI:** the board is the home page. Columns, drag a card to move it,
  and the gate columns ask first. **Post about this** and the sources
  sit beside them. The card page shows the brief with its pictures,
  credits and which are used, the thread with comments and attachments
  (a comment wakes the partner), the drafts (linked to the post editor),
  the editor's verdict and checks, the spend and the move log. Its buttons
  are Approve, Rethink with a note, Schedule with a time picker set to
  the recommended time, Hold, Back to the partner, and Drop. The queue
  moved to `/queue`.
- `[scout]`, `[partner]` and `[review]` in `postdesk.toml`.
  [docs/guardrails.md](docs/guardrails.md) says what each rule is and
  where the code holds it.

### M1, the maker

- `postdesk make article.md`: a Markdown file (front matter for the title,
  date, summary, link, a photo with its credit, named figures, subjects,
  table rows) becomes a rendered carousel and its Story as drafts in the
  queue, with recommended times. `--template`, `--plan` for a one-off
  plan, `--dry-run` to see the brief without calling anything.
- The slide renderer, `@postdesk/renderer`: a Node service (satori and
  resvg, no browser) with eight slide types (`cover`, `caption`,
  `statement`, `stat`, `table`, `label`, `cta`, `story`), `POST /render`
  behind a bearer token, `GET /health`, `GET /themes`, a `render` CLI, and
  a theme format (palette, fonts, wordmark, handle) with a new default
  theme. Its JSON schema is shipped in the Python package too, so a spec is
  checked before it is sent.
- Six post templates as YAML (`article`, `explainer`, `spotlight`, `stat`,
  `list`, `story-only`), each a sequence of typed slides with a brief, the
  fields the model fills and the ones fixed from the facts; a `templates/`
  folder for your own.
- Copy writing through one structured Claude call with the voice file in
  the (cached) system prompt, then the checks: every slot answered, every
  number present in the source, field limits, no em dash, retired phrases,
  the caption lint, alt text per slide; problems go back to the model,
  twice at most.
- Sources: `markdown` (a file or folder) and `manual`, behind the
  `postdesk.sources` entry point for others.
- `postdesk.llm`: one shape for every agent turn (structured output,
  retries on transient errors, refusals and cut-off replies as failed
  turns), model and effort per agent from `[llm]`, every call logged on
  the new `agent_run` table with tokens and cost, and the monthly ceiling
  that stops the agents.
- `PATCH /api/posts/{id}` takes `slides: [{index, fields}]`: the words on
  a made slide are checked against the post's numbers and that slide alone
  is re-rendered. The post editor shows them in a "Words on slide" panel.
- `postdesk doctor` checks the renderer (health and a test render);
  `postdesk render` and `postdesk templates`. Docker Compose gains the
  `renderer` service; CI tests the renderer and renders a slide from the
  image.

### M0, the scheduler

- The queue: file a draft with a recommended time, edit it, approve it with
  a time, unapprove, reject, send a failed post back to draft. An approved
  post is immutable. Times come from `[schedule]` in `postdesk.toml`, in
  the account's timezone.
- Publishing images, carousels and Stories through Instagram's Graph API
  from public URLs, with the row lock that keeps two publisher passes from
  posting the same thing. Per-post and account insights, daily, into the
  `metric` table.
- Storage on local disk (served at `/media/<id>`) or any S3-compatible
  bucket; every stored image has a provenance record.
- The HTTP API (`/api/docs`), one admin login set on the first visit or
  from `.env`, a bearer token for scripts; `/media` public but only for
  images a post references.
- The web UI: login, the queue (to review and scheduled; published and
  rejected), the post editor with the caption lint, alt text per slide and
  the time picker with the publisher-hours warning, a form to queue a post.
- The CLI: `init`, `doctor`, `serve`, `cron`, `instagram whoami` and
  `page-token`, `post` (dry run by default), `queue`, `suggest-times`,
  `db upgrade`.
- Docker Compose with Postgres; the image builds the web UI and runs the
  migrations on start. Docs for connecting Instagram, deploying and
  troubleshooting.

### Earlier

- Repository created: README, licence, contributing and security notes, the vendor-neutral check, a CLI stub.
