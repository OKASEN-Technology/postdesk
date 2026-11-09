# themes

Your own slide themes go here, one folder each (`themes/mine/theme.json`,
its fonts and wordmark). Docker Compose mounts this folder into the
renderer; set `[theme].name = "mine"` in `postdesk.toml` to use one.

Start by copying `renderer/themes/default/`. [docs/themes.md](../docs/themes.md)
explains every key.
