# web

The web UI: a Vite + React + Tailwind app the Python package serves at `/`.
M0 has the login, the queue, the post editor and a "new post" form; the
board, card and settings screens come with their milestones.

```bash
cd web
npm ci
npm run build        # writes src/postdesk/static/, which `postdesk serve` picks up
npm run dev          # a dev server on :5173 that proxies /api to postdesk serve on :8080
```

The Docker image builds it; `src/postdesk/static/` is not committed.
