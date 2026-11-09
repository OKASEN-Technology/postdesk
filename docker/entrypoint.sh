#!/bin/sh
# Bring the schema up to date, then run whatever the command is
# (`postdesk serve` by default; `postdesk cron` for a hosted cron job).
set -e
if [ "${POSTDESK_SKIP_MIGRATE:-}" != "1" ]; then
  until postdesk db upgrade; do
    echo "waiting for the database…"
    sleep 2
  done
fi
exec "$@"
