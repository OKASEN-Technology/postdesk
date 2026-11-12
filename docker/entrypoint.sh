#!/bin/sh
# Bring the schema up to date before `postdesk serve` or `postdesk cron`,
# then run the command. Any other command (`postdesk version`, `doctor`,
# `instagram whoami`, a shell) runs straight away: it either needs no
# database or reports on it itself.
#
# The wait is capped so a wrong DATABASE_URL fails loudly instead of
# looping forever. POSTDESK_SKIP_MIGRATE=1 skips the step entirely.
set -e

case "$1 $2" in
  "postdesk serve"|"postdesk cron") migrate=1 ;;
  *) migrate= ;;
esac

if [ -n "$migrate" ] && [ "${POSTDESK_SKIP_MIGRATE:-}" != "1" ]; then
  tries=0
  until postdesk db upgrade; do
    tries=$((tries + 1))
    if [ "$tries" -ge "${POSTDESK_DB_WAIT_TRIES:-30}" ]; then
      echo "the database did not answer after $tries tries; check DATABASE_URL" >&2
      exit 1
    fi
    echo "waiting for the database… ($tries)"
    sleep 2
  done
fi

if [ "$1 $2" = "postdesk serve" ] && [ "${POSTDESK_BUNDLED_RENDERER:-0}" = "1" ]; then
  exec python /supervise.py "$@"
fi

exec "$@"
