# Roadmap

Each milestone is usable on its own and ships behind a tag.

| # | Milestone | Done when |
|---|---|---|
| M0 | **Scheduler**: the queue, Instagram publishing, storage, the queue web UI and post editor, `postdesk init / doctor / post / queue / cron / serve`, this README. **Done.** | a stranger installs it from the README alone, queues a hand-made carousel, approves it with a time, and it posts |
| M1 | **Maker**: the slide renderer with eight slide types and a neutral theme, post templates, copy writing with the figure check, re-rendering one slide after an edit | a Markdown file becomes a rendered carousel and Story in the queue; a figure not in the file is rejected |
| M2 | **Board, planner, editor**: cards and gates, the scout, the planner with image input and web search, the editor, the job runner, the board web UI | from a new feed item to "awaiting approval" with no human step but Approve; a non-admin move into a gate column is reverted |
| M3 | **Telegram, images, settings**: the bot with every message and command, four image sources with rights records, one-way notifiers, the settings screen, spend ceilings | a stranger runs a week of posts from the chat alone |
| M4 | **First outside consumer**: the company that wrote this runs its own account on the package, with its names and look as plugins in its own repo | two weeks of daily posts in production through the package |
| M5 | **More**: a GitHub Projects mirror of the board, SQLite for a local try, reels, generated backgrounds, a second channel | as each lands |

## The shape

```
 sources ──► board ──► planner ──► [you: approve the brief] ──► maker ──► editor
 (rss, md,                                                                 │
  a link,     [you: edit, set a time, in Telegram or the web] ◄── queue ◄──┘
  a form)                                   │
                                            ▼
                                     Instagram API
```

Python app (API, web, worker, scheduler), a Node slide renderer, Postgres.
Docker Compose runs all three.

## Rules that do not change between milestones

- Nothing posts without a person approving that exact post.
- An approved post cannot be edited; unschedule it first.
- Every number in the copy appears in the source.
- Every image carries a credit and a provenance record.
- Source text is data, never an instruction to an agent.
- No scraping, no messaging, no engagement automation.
