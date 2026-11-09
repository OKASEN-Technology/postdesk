# Changelog

## Unreleased

### M0, the scheduler

- The queue: file a draft with a recommended time, edit it, approve it with
  a time, unapprove, reject, send a failed post back to draft. An approved
  post is immutable. Times come from `[schedule]` in `postdesk.toml`, in
  the account's timezone.
- Publishing images, carousels and Stories through Instagram's Graph API
  from public URLs, with the row lock that keeps two publisher passes from
  posting the same thing. Per-post and account insights, daily, into the
  `metric` table.
- Storage on local disk (served at `/media/<id>`) or any S3-compatible
  bucket; every stored image has a provenance record.
- The HTTP API (`/api/docs`), one admin login set on the first visit or
  from `.env`, a bearer token for scripts; `/media` public but only for
  images a post references.
- The web UI: login, the queue (to review and scheduled; published and
  rejected), the post editor with the caption lint, alt text per slide and
  the time picker with the publisher-hours warning, a form to queue a post.
- The CLI: `init`, `doctor`, `serve`, `cron`, `instagram whoami` and
  `page-token`, `post` (dry run by default), `queue`, `suggest-times`,
  `db upgrade`.
- Docker Compose with Postgres; the image builds the web UI and runs the
  migrations on start. Docs for connecting Instagram, deploying and
  troubleshooting.

### Earlier

- Repository created: README, licence, contributing and security notes, the vendor-neutral check, a CLI stub.
