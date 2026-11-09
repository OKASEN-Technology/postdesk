# Sources: what you post about

A source answers two questions: what is new enough to post about, and what
are the facts about one thing. The maker writes only from those facts.
What is not in them cannot reach a slide, and every number on a slide is
checked against them.

Built in: **rss** (a feed), **url** (one web page), **markdown** (a file
or a folder of files) and **manual** (a title and pasted text).

## The scout

The scout reads every entry under `[sources]` in `postdesk.toml` and files
each new item as a card in *Planned*, where the partner starts on its
brief. It runs inside the app every `[scout].every_hours` (2) between the
`hours` you give (06:00 to 21:00, your timezone), looks back
`days` (3), and files at most `max_per_run` (5) cards a run; the rest
wait for the next one. `postdesk scout [--days N] [--source NAME]` and the
board's **Scan now** run it by hand.

Each item is filed once: its card carries the fingerprint
`post:<source>:<ref>`, and an item that already has a post in the queue
(made with `postdesk make`, say) is skipped too. A feed that will not load
is reported and the others carry on.

```toml
[sources]
rss = { url = "https://example.com/feed.xml", template = "article" }
news = { type = "rss", url = "https://example.org/news.atom", template = "explainer" }
markdown = { path = "./posts" }
```

An entry's name is its type unless it says `type`, so two feeds can sit
side by side.

## RSS

Any RSS or Atom feed. An item's ref is `rss:` and its guid (else its
link). For each new item the scout fetches the item's page and keeps its
article text as Markdown: headings, paragraphs, lists, quotes and links,
without the navigation, scripts, forms and footer around them. If the page
will not load, the feed's own text is used. The title and summary come
from the feed, the link is the page.

- **The numbers** a post may use are the ones in that text and the title,
  nothing else.
- **The picture** is the item's image in the feed (a `media:content`,
  `media:thumbnail` or image enclosure), credited `Image: <the site's
  name>` and recorded with no licence. The partner sees it and decides
  whether the post uses it; the editor's rights check reads that record.
  If you do not have the right to use a site's pictures, leave them out
  in the brief (or attach your own with a credit).
- **The partner's web search** is limited to the feed's own site, the
  item's site, and `[partner].allowed_domains`.
- `template` picks the post's shape (default `article`); the partner may
  propose its own slide plan instead.

## URL

One page: paste a link into **Post about this** on the board (or send
`{"link": ...}` to `POST /api/cards` or `POST /api/sources/manual`). The
page is fetched and reduced the same way as an RSS item's, its ref is
`url:<the address>`, and the card goes to *Planned*. It has no feed, so
the scout never looks at it on its own and it needs no `[sources]` entry.

Pages are fetched with a 20-second timeout and a 5 MB limit, http(s)
only. What they say is data the partner plans from: an instruction inside
a page ("ignore your rules", "post this at 99% off") is text, not an order.

## Markdown

One file is one post. `postdesk make posts/three-loaves.md` reads it;
`[sources].markdown = { path = "./posts", template = "article" }` in
`postdesk.toml` tells the scout to watch the folder.

```markdown
---
title: Three loaves, one long rest
date: 2026-11-03
summary: What 72 hours in the fridge does to a sourdough crumb.
url: https://example.com/bakes/three-loaves   # what "link in bio" points at
template: article                             # optional; the default
hero: ./photos/crumb.jpg                      # optional: a file next to this one, or an https URL
hero_credit: "Photo: Jo Baker"                # required when hero is set
figures:                                      # optional: named numbers, written exactly as they may appear
  hydration: "78%"
  rest: "72 hours"
subjects:                                     # optional: who or what may carry a picture (spotlight)
  - name: Jo Baker
    role: the baker
    meta: Bristol
    link: https://example.com/jo
    image: ./photos/jo.jpg
    credit: "Photo: Jo Baker"
    facts: [{label: Loaves, value: 3 a week}, {label: Since, value: "2019"}]
rows:                                         # optional: a `list` template's table
  - {rank: 1, name: Country loaf, delta: 2}
  - {rank: 2, name: Seeded tin, delta: null}  # null = new this time
  - {rank: 3, name: Focaccia, value: "78%"}
---
The body, in Markdown. Its numbers are the ones the copy may use, along
with `figures:` above. Links like [the method](https://example.com/method)
become citations.
```

Without front matter, the first `# heading` is the title and the first
paragraph the summary; the date is the file's. Any other front-matter key
lands in the facts' `extra`, where a template can read it
(`{{ facts.extra.kicker }}`, `frames: 2` for `story-only`).

A local picture is stored as an asset the first time it is used, with the
credit as its provenance, and shown on the slide and in the web editor
from there. An `https` picture is fetched by the renderer each time.

[`examples/article.md`](../examples/article.md) is a complete file to try.

## Manual

**Post about this** on the board (`POST /api/sources/manual`) and, in M3,
a message to the bot: a title, some text, an optional link and picture. The facts are built the same way,
with the text as the body, so the same number rule applies.

## The number rule

The copy may contain a number only if it appears in the facts: the title,
the summary, the body, `figures:`, `subjects:` or `rows:`. "72 hours" in
the body allows `72h` on a slide; "78%" allows `78%` and a rounding such
as `+78.4%`; `4,855` allows `4855`. Anything else is sent back to the model
as a problem, and if it is still there after two more tries the make
stops. If you want a number on a slide, put it in the file.

## Writing a source

A source is a Python class with a `key`, `candidates(since) ->
list[Candidate]` and `facts(ref) -> Facts`, registered on the
`postdesk.sources` entry point:

```toml
# your package's pyproject.toml
[project.entry-points."postdesk.sources"]
notion = "my_postdesk_sources.notion:NotionSource"
```

The shapes are in [`src/postdesk/sources/base.py`](../src/postdesk/sources/base.py):
`Candidate` (a stable `ref`, title, date, suggested template, URL),
`Facts` (title, summary, `body_md`, URL, date, citations, a hero `Image`,
`figures`, `subjects`, `extra`), `Image` (url or local path, credit,
licence, fit) and `Subject` (name, role, link, meta, image, facts). The
constructor receives the table from `[sources].<key>` as keyword
arguments (without `type`), plus `client=`, an `httpx.Client` the scout
passes so tests can fake the network; a source that does not take it is
built without it. An optional `domains()` names the sites the partner may
search for this source's items. The markdown source is a small, complete
example; `rss.py` is one that fetches.

Treat what you read as data. A source that pulls text from the web must
not pass it to anything as an instruction; the maker's prompt says as much
to the model, and nothing in postdesk acts on text inside a source.
