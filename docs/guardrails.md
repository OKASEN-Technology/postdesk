# Guardrails

What postdesk will not do, and where in the code each rule is held. Every
rule here is enforced by code, not only asked of the model in a prompt. A
model that ignores its instructions still cannot get past the check.

## Nothing is published without you approving that exact post

- A post reaches Instagram only from the queue's `approved` state, at its
  time. `approved` is set by `queue.approve`, which the API calls only for
  a signed-in admin (the web session) or the API token. No agent calls it.
- An approved post cannot be edited. Unapprove it first, and approve it
  again after.
- The board has two **gate columns**: *Approved* (the brief is right, make
  it) and *Scheduled* (this post, at this time). Only a person moves a
  card into either. Every column change goes through
  `board/cards.py:set_column`. When the partner, the maker, the editor,
  the worker, the scout or the publisher asks for a gate column, it
  refuses, raises, and writes the refusal on the card's move log (shown in
  red on the card). The tests try each of them against both gates.
- Scheduling a card approves its drafts in the queue at the time you
  picked. Approving all of a card's drafts in the queue screens instead
  counts as the same gate, passed by you. The background sync never moves
  a card into *Scheduled*.

## Every number comes from the source

- The maker's copy may contain a number only if it appears in the source's
  facts (`maker/words.py:known_numbers`). "72 hours" allows `72h`, "78%"
  allows a rounding such as `78.4%`, and `4,855` allows `4855`. Anything
  else goes back to the model, twice at most, and then the make stops.
- The partner's brief, its guidance and its own slide plan are checked the
  same way (`agents/partner:check_brief`). Slide numbers, clock times and
  counts up to ten are left alone. A brief with an invented figure is sent
  back once. If it is still wrong, nothing is saved, the card stays in
  *Planned*, and the problem is written in its thread.
- A word you change on a slide in the web editor, or one the revision turn
  changes, is checked again before that one slide is re-rendered.
- The editor's **facts** check reads every figure against the facts. A
  figure it cannot trace fails: could not verify beats a confident guess.

## Every picture has a record

- Every stored image (an upload, a source's photo, an attachment on a
  card, a rendered slide) has a provenance record: where it came from, its
  credit and licence if known, who added it, and for a slide, which images
  were composited into it.
- A picture from a feed is credited to its site and recorded with no
  licence. The partner decides whether the post uses it; the editor's
  **rights** check reads the record.
- An image you attach to a card is used only if the partner gives it a
  credit your note supports. Without one it is refused, and the card stays
  in *Planned*.
- The revision turn changes words only. A slide's picture, its source line
  and anything about rights need you: a note about them puts the card On
  hold.

## Source text is data, never instructions

- Feed items, web pages, Markdown files, captions and comments reach the
  models inside a `<source>` block, marked as data. The prompts say only
  the admin steers.
- Nothing in postdesk acts on what a model says a source told it to do.
  The models return structured output that is checked field by field: a
  column, a brief, the image keys, a verdict. A model cannot call
  anything else.
- The partner's web search is limited to the source's own site and
  `[partner].allowed_domains`. `web_search = false` turns it off.

## Two independent passes

- The **editor** reads each made post in a fresh call. It never sees the
  maker's conversation, only the source, the approved brief, the thread,
  each slide as an image, and each slide's provenance. It judges and does
  not rewrite.
- It runs six checks: voice, facts, rights, ai-label, accessibility and
  links. Add your own under `[review.extra_checks]`, one sentence each.
  They join the checks it must answer, each exactly once:

  ```toml
  [review.extra_checks]
  prices = "No price is stated for a single item."
  claims = "Nothing is called healthy, healing or good for you."
  ```

- A review that leaves a check out, repeats one, or passes with a failed
  check is sent back once. A second bad review holds the card. It is never
  passed.
- **revise** (words only) goes to the revision turn and back to the
  editor, at most `[review].max_rounds` times (2). After that the card goes
  On hold with "still not right after 2 revisions". **hold**, or a note
  about something words cannot fix, puts the card On hold with the reason.

## When something goes wrong, it stops

- When Claude refuses, a reply is cut off at its token limit, or the
  month's ceiling (`[llm].monthly_usd`) is reached, that agent's turn
  fails. The card goes On hold with the reason, and the turn is not
  retried. Nothing is spent again until you act.
- A network error, the renderer being down, or the API being overloaded
  is retried with backoff, five times at most, and then held the same way.
- One job runs per card at a time. A unique index in the database holds
  this even with two workers.
- Every Claude call is logged with its tokens and cost. Each card shows
  its own spend, and the board shows the month's.

## What it never does

- Scrape Instagram, like, follow, comment or message anyone. It publishes
  your posts through Instagram's official API and reads their insights.
  That is all it does on Instagram.
- Post a reel, or anything not in the queue.
