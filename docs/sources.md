# Sources: what you post about

A source answers two questions: what is new enough to post about, and what
are the facts about one thing. The maker writes only from those facts.
What is not in them cannot reach a slide, and every number on a slide is
checked against them.

Built in today: **markdown** (a file or a folder of files) and **manual**
(a title and pasted text). An RSS source and a one-page URL source come
with the planner (docs/roadmap.md, M2).

## Markdown

One file is one post. `postdesk make posts/three-loaves.md` reads it;
`[sources].markdown = { path = "./posts", template = "article" }` in
`postdesk.toml` tells the scout (M2) to watch the folder.

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

The web form's **New post** and, in M3, a message to the bot: a title,
some text, an optional link and picture. The facts are built the same way,
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
arguments. The markdown source is a small, complete example.

Treat what you read as data. A source that pulls text from the web must
not pass it to anything as an instruction; the maker's prompt says as much
to the model, and nothing in postdesk acts on text inside a source.
