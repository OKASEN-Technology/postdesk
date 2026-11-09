# Post templates

A template is what a post looks like before any words are written: the
sequence of slides, what each one is for, which words the model writes and
which are set from the source. `postdesk make article.md --template
explainer` runs one; the ones built in live in
[`src/postdesk/templates/`](../src/postdesk/templates/) and
`postdesk templates` lists what it can see.

| Template | Sequence | Use it for |
| --- | --- | --- |
| `article` (default) | cover, three statements, a number if there is one, call to action, plus a Story | a piece with a few points |
| `explainer` | cover, what happened, why it matters, what to watch, call to action, plus a Story | news |
| `spotlight` | a label slide with the subject's picture, their story, a number, a facts panel, call to action, plus a Story | one person, product or place; needs `subjects:` in the front matter |
| `stat` | one stat slide, plus a Story | one number |
| `list` | cover, a table, call to action, plus a Story | a ranking; needs `rows:` in the front matter |
| `story-only` | one to three Story frames | something that does not need a feed post |
| a file of your own | whatever it says | `postdesk make article.md --plan plan.yaml` |

## Slide types

A template may only use the renderer's eight: `cover`, `caption` (a photo
with a sentence over it), `statement`, `stat`, `table`, `label` (a picture
with a wall label, or a facts panel), `cta`, and `story` (9:16). Each has
its fields; the fixtures in
[`renderer/test/fixtures/`](../renderer/test/fixtures/) show one of each
filled in, and [`renderer/schema/slide.json`](../renderer/schema/slide.json)
is the exact shape. A new layout is a change to the renderer and the
theme, not a prompt.

## Writing one

Put a YAML file in `templates/` at the repository root (Docker Compose
mounts that folder) and it is found by its file name, ahead of a built-in
with the same name. The `article` template, shortened:

```yaml
name: article
description: A cover, three key points, a number if the piece has one, a call to action, and a Story.
kind: carousel                      # carousel | image | story
kicker: "{{ facts.extra.kicker or 'New post' }}"

slides:
  - type: cover
    if: facts.hero                    # only when the source has a picture
    brief: "The hook: a headline of 6 words or fewer, and one line of promise under it."
    fields: [headline, sub]           # the model writes these
    fixed:                            # these are set, never written
      kicker: "{{ kicker }}"
      kicker_sub: "{{ month }}"
      image: "{{ facts.hero }}"
      link: "{{ account.site }}"

  - type: statement
    brief: "Key point {{ n }} of the piece."
    fields: [kicker, headline, headline_em, body, source]
    repeat: 3

  - type: stat
    optional: true                    # dropped when the model leaves `figure` empty
    brief: "One headline number from the piece, verbatim. Leave figure empty if there is none."
    fields: [figure, figure_sub, kicker, note, source]

  - type: cta
    brief: "One action: read the full piece via the link in bio."
    fields: [headline, headline_em, body]
    fixed: { kicker: "{{ kicker }}" }

story:
  brief: "A 9:16 companion: the headline in 3 to 8 words, one line on what is behind the link."
  fields: [headline, line]
```

Per slide:

- `brief`: what the model is told to write. Be specific about length and
  what the slide is for; the field rules (a headline is 3 to 9 words, a
  body at most 200 characters, and so on) are added for you.
- `fields`: the words the model fills. The first is the slide's main one.
- `fixed`: values set from the source. Anything the model must not touch
  goes here: images, table rows, a facts panel, links.
- `optional: true`: the slide is dropped when the model leaves its first
  field empty. The `article` template's stat slide works this way.
- `if` and `unless`: include the slide only when the expression is (or is
  not) truthy, which is how a cover differs with and without a photo.
- `repeat`: the same slide several times; `{{ n }}` is the count.
- `requires` at the top: a list of `{path, message}` the facts must have,
  so a `spotlight` without a subject fails with a sentence, not a blank
  slide.

Expressions in `{{ }}` are deliberately small: a dotted path into the
facts (`facts.title`, `facts.hero`, `facts.extra.rows`,
`facts.subjects.0.name`), the account (`account.handle`, `account.site`),
`month` and `date` from the source's date, `kicker` from the top of the
template, `n` inside a repeat, a quoted literal, and `or` to fall back. A
value that is one expression keeps its type, so `image: "{{ facts.hero }}"`
is the whole image; text around an expression is interpolated.

## What the checker does with the result

Whatever the template asks for, the copy goes through the same checks
before anything is rendered: every slot answered, every number on a slide
present in the source (`figures:` in the front matter, the text, the
data), each field within its limit, no em dash, none of your retired
phrases, the caption within Instagram's limits and your hashtag ceiling,
alt text for every slide. The problems go back to the model, twice at
most, then `postdesk make` stops and says what was wrong.

A post made from a template keeps each slide's spec. Change the words on
one in the web editor and only that slide is re-rendered, through the same
checks.
