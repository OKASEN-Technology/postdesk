# Security

postdesk holds three things worth protecting: your Anthropic key, your
Instagram Page token (which can post as you), and your Telegram bot token
(which can speak as the manager). They live only in `.env` and the
environment, never in the database, the config file, logs or messages.

## Reporting

Use GitHub's private vulnerability reporting on this repository (Security
tab → Report a vulnerability). Please do not open a public issue for a
vulnerability. We aim to answer within three working days.

## What we consider a vulnerability

- Any path by which a post reaches Instagram without a person's approval of
  that exact post.
- Any path by which an agent, a web page, an article or a Telegram message
  from an unlinked chat can move a card through a human gate.
- A token or key appearing in a log, a message, a card or an asset.
- The public media endpoint serving anything not referenced by a post.

## Scope notes

The Telegram bot answers only its one linked chat. The web UI has one admin
login. The renderer takes a bearer token shared with the app. The app never
fetches a URL that an agent produced on its own; it fetches sources you
configured and pages the partner was pointed at with an allow-list.
