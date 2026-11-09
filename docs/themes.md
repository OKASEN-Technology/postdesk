# Themes

A theme is how your slides look: the colours, the fonts, the wordmark in
the corner, the handle and site on the call-to-action slide. The layouts
themselves (where the headline sits, how a table is ruled) are the
renderer's eight slide types and do not change between themes; a theme
dresses them.

The renderer ships one theme, `default`, in
[`renderer/themes/default/`](../renderer/themes/default/). Copy it to make
yours.

## A theme folder

```
themes/
  mine/
    theme.json          # everything below
    wordmark.svg        # your mark, drawn in currentColor
    fonts/
      Serif-Regular.ttf # the files theme.json names (TTF or OTF, not woff2)
      ...
    OFL.txt             # each font's licence, kept next to it
```

`theme.json`:

| Key | What it is |
| --- | --- |
| `name` | What `[theme].name` in `postdesk.toml` points at. Lower case, no spaces. |
| `description` | One line, shown by `GET /themes`. |
| `palette` | Fourteen named colours (below). |
| `roles` | Which font family plays `serif` (headlines), `sans` (body) and `mono` (numbers, labels). |
| `fonts` | One entry per file: `family`, `weight`, `style` (`normal` or `italic`), `file` (relative to the folder). Ship the weights the layouts use: serif 400 and 500 and a 400 italic, sans 400 to 600, mono 400 to 600. |
| `wordmark` | `mark` (an SVG file), `text` (the word next to it), `markWidth` and `markHeight` in pixels at 1080 wide. The SVG is drawn in `currentColor`, so one file works on light and dark slides. |
| `handle`, `site` | Shown on the call-to-action slide and the table's footer. |
| `ctaLine` | The small line above the wordmark on the call-to-action slide. |
| `strings` | The few words the layouts print themselves: `swipe`, `linkInBio`, `newEntry`, `band`, `level`. Translate them here. |

The palette:

| Name | Used for |
| --- | --- |
| `paper` | The light ground (cover, statement, label). |
| `deep`, `deepRaised` | The dark ground (stat, table, cta) and a panel on it. |
| `rule` | Hairlines on the dark ground. |
| `accent`, `accentLight` | The italic headline words, the kicker dot, the swipe arrow. |
| `highlight` | The tag on a Story, the "new" marker in a table. |
| `ink`, `sub` | Text and secondary text on paper. |
| `stone` | Secondary text on the dark ground. |
| `muted`, `mutedLight` | Labels and footers on each ground. |
| `up`, `down` | A table row's change arrows. |

## Trying it

```bash
cd renderer && npm run build
RENDERER_THEMES=../themes npx postdesk-renderer render --spec ../examples/slide.json --out slide.jpg --theme mine
```

Every slide type has a fixture in
[`renderer/test/fixtures/`](../renderer/test/fixtures/); render each
once and look at them together before you settle on the palette. A dark
headline colour on the dark ground is the mistake the renderer cannot
catch for you.

## Using it

Docker Compose mounts `./themes` into the renderer, so a folder at
`themes/mine/` is found on the next `docker compose up -d --build
renderer`. Then in `postdesk.toml`:

```toml
[theme]
name = "mine"
```

`postdesk doctor` checks that the renderer has the theme the config names.
Slides already in the queue keep the look they were made with; a slide you
edit afterwards is re-rendered in the new theme.

## Fonts and licences

Use fonts you may redistribute: anything under the SIL Open Font License
(Google Fonts has hundreds) or fonts you own. Keep each licence file in the
theme folder. The renderer reads TTF and OTF; a `.woff2` from a web
download has to be converted first, or fetch the static TTFs from the
foundry.
