# @postdesk/renderer

The slide renderer: a small Node service that turns a slide spec (JSON) into a 1080×1350 feed slide or a 1080×1920 Story as a JPEG. Layout is [satori](https://github.com/vercel/satori) (flexbox to SVG), rasterising is [resvg](https://github.com/RazrFalcon/resvg). No browser, no React, no network: fonts and the wordmark come from the theme folder.

The app talks to it over HTTP; you can also run it on its own.

## Run it

Inside Docker Compose it is the `renderer` service and the app reaches it as `http://renderer:3100`. Locally:

```bash
cd renderer && npm install && npm run build
RENDERER_TOKEN=change-me npx postdesk-renderer serve --port 3100
```

Or render one slide to a file without a server:

```bash
npx postdesk-renderer render --spec ../examples/slide.json --out slide.jpg
```

`npx postdesk-renderer themes` lists the themes it can see.

## HTTP

| Route | Auth | Does |
| --- | --- | --- |
| `GET /health` | none | `{ok, version, themes}` |
| `GET /themes` | bearer | the themes with their palette and fonts |
| `GET /schema` | bearer | the slide schema (`schema/slide.json`) |
| `POST /render` | bearer | body: one slide spec; `?theme=name`, `?svg=1` for the SVG instead; returns `image/jpeg` |

Auth is `Authorization: Bearer $RENDERER_TOKEN`. With the variable unset the service is open, which is fine on localhost and nowhere else. A bad spec is a 400 with `{detail}` naming the field.

## Slide types

`cover`, `caption`, `statement`, `stat`, `table`, `label`, `cta` (feed, 1080×1350) and `story` (1080×1920, with the safe zones Instagram keeps clear at the top and bottom). The TypeScript types in `src/slides.tsx` are the reference; `schema/slide.json` is the same thing as JSON Schema, and the Python side ships a copy so a spec is checked before it is sent. The fixtures in `test/fixtures/` show one of each.

## Themes

A theme is a folder with a `theme.json` (palette, fonts, wordmark, handle, site) next to its font files and wordmark SVG. The service looks in `themes/` here and in every folder on `RENDERER_THEMES` (colon-separated). See `docs/themes.md` at the repository root for the fields and how to make your own.

## Develop

```bash
npm test          # vitest: every slide type renders, the schema rejects bad specs, the server guards its routes
npm run typecheck
npm run dev       # serve from source with tsx
```
