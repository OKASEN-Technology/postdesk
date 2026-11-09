#!/usr/bin/env bash
# Fails when the tree mentions anything from scripts/deny-list.txt.
#
# postdesk is for anyone with an Instagram account. The company that wrote
# it keeps its own names, voice, data and look in its own repository, as
# plugins. This check keeps them out of here: it runs in CI and as a
# pre-commit hook, and the list grows whenever a review finds another.
#
# Patterns are extended regexes, case-insensitive, one per line; lines
# starting with # are comments. Word boundaries (\b) keep short tokens
# from matching inside ordinary words.
set -euo pipefail

cd "$(dirname "$0")/.."
list=scripts/deny-list.txt
allow=scripts/allow-list.txt
patterns=$(grep -v '^\s*#' "$list" | grep -v '^\s*$' || true)
allowed=$(grep -v '^\s*#' "$allow" | grep -v '^\s*$' || true)
[ -n "$patterns" ] || { echo "check-neutral: empty deny list"; exit 0; }

# Everything tracked or about to be, except the lists themselves and binaries.
files=$(git ls-files --cached --others --exclude-standard \
  | grep -v -e "^$list\$" -e "^$allow\$" -e '\.\(png\|jpg\|jpeg\|webp\|gif\|woff2\?\|ttf\|otf\|mp4\|pdf\)$' || true)

# Drop the allowed lines (the organisation's handle, the README's credit)
# before matching, so a hit is always something that should not be there.
strip_allowed() {
  if [ -n "$allowed" ]; then grep -vE -f <(echo "$allowed") || true; else cat; fi
}

status=0
while IFS= read -r pat; do
  # grep exits 1 on no match and xargs turns that into 123; neither is an error here.
  hits=$( (echo "$files" | xargs -r grep -nIiE -- "$pat" 2>/dev/null || true) | strip_allowed)
  if [ -n "$hits" ]; then
    echo "check-neutral: '$pat' found:"
    echo "$hits" | sed 's/^/  /'
    status=1
  fi
done <<< "$patterns"

[ $status -eq 0 ] && echo "check-neutral: clean"
exit $status
