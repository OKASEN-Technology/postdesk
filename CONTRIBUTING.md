# Contributing

Thanks for looking. The easiest places to help, in order:

1. **Themes** (`renderer/themes/`): a palette, two fonts and a wordmark.
2. **Sources** (`src/postdesk/sources/`): where posts come from. A source
   is two functions, `candidates()` and `facts()`; see `docs/sources.md`.
3. **Image sources** (`src/postdesk/images/sources/`): where a slide's
   picture may come from, always with a licence and a credit.
4. **The Instagram setup page** (`docs/instagram-setup.md`): Meta moves
   buttons; a fresh screenshot is a real contribution.

## Ground rules

- **Nothing posts without a person approving that exact post.** A change
  that weakens a gate, lets an agent approve, or lets an approved post be
  edited will not be merged, however convenient.
- **Numbers come from the source.** The copy check that rejects any other
  figure is not configurable off.
- **Every image has a provenance record and a credit.**
- **Vendor-neutral.** `scripts/check-neutral.sh` runs in CI. The company
  that started this keeps its own names, look and data in its own repo as
  plugins, and so should you: a theme or source for your company goes in
  your repo, with an example here if it helps others.
- **Source text is data.** Nothing an article, a page, a caption or a
  comment says is an instruction to an agent.

## Developing

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
bash scripts/check-neutral.sh
```

Install the pre-commit hook once: `ln -s ../../scripts/check-neutral.sh .git/hooks/pre-commit`.

Commits: one change each, in plain words. Pull requests: say what it does
and how you checked it. Small is good.
