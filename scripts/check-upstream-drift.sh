#!/bin/sh
# Detects drift between this port and the upstream OpenWiki source it reproduces.
#
# This repo re-expresses a specific slice of langchain-ai/openwiki (the system
# prompt, the git-evidence commands, and the idempotence logic) as prompt files.
# Nothing here imports upstream, so a normal dependency bump can never surface a
# change. This script is the substitute: it hashes the upstream files the port
# claims to reproduce and compares them against upstream.lock.json.
#
# Usage:
#   sh scripts/check-upstream-drift.sh            # report drift, exit 1 if any
#   sh scripts/check-upstream-drift.sh --update   # accept current upstream as tracked
#   sh scripts/check-upstream-drift.sh --ref v0.3.3   # pin the ref instead of latest
#
# Set GITHUB_TOKEN to raise the GitHub API rate limit (required in CI).
set -eu

LOCK=${LOCK:-upstream.lock.json}
MODE=check
REF=

while [ $# -gt 0 ]; do
  case "$1" in
    --update) MODE=update ;;
    --ref) REF=${2:?--ref needs a value}; shift ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

for bin in jq; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing required tool: $bin" >&2; exit 2; }
done

# sha256sum is absent on macOS; shasum ships everywhere Perl does.
if command -v sha256sum >/dev/null 2>&1; then
  sha256() { sha256sum "$1" | cut -d' ' -f1; }
else
  sha256() { shasum -a 256 "$1" | cut -d' ' -f1; }
fi

[ -f "$LOCK" ] || { echo "lock file not found: $LOCK" >&2; exit 2; }

REPO=$(jq -r '.upstream' "$LOCK")
TRACKED_REF=$(jq -r '.trackedRef' "$LOCK")

# Never route file bodies through $(...) — command substitution strips trailing
# newlines, which silently changes every hash and reports permanent false drift.
fetch() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" "$@"
  else
    curl -fsSL "$@"
  fi
}

if [ -z "$REF" ]; then
  REF=$(fetch "https://api.github.com/repos/$REPO/releases/latest" | jq -r '.tag_name')
  [ -n "$REF" ] && [ "$REF" != "null" ] || { echo "could not resolve latest release of $REPO" >&2; exit 2; }
else
  # Without this, a typo'd ref 404s every file and the report claims upstream
  # deleted the entire agent.
  fetch -o /dev/null "https://api.github.com/repos/$REPO/commits/$REF" 2>/dev/null ||
    { echo "ref not found in $REPO: $REF" >&2; exit 2; }
fi

# Hash every tracked file at $REF. A file that 404s is drift, not an error:
# upstream moving or deleting it is exactly what this script exists to catch.
DRIFT=0
REPORT=$(mktemp)
NEWFILES=$(mktemp)
BODY=$(mktemp)
trap 'rm -f "$REPORT" "$NEWFILES" "$BODY"' EXIT
echo '{}' > "$NEWFILES"

for path in $(jq -r '.files | keys[]' "$LOCK"); do
  want=$(jq -r --arg p "$path" '.files[$p].sha256' "$LOCK")
  note=$(jq -r --arg p "$path" '.files[$p].why // ""' "$LOCK")
  if fetch -o "$BODY" "https://raw.githubusercontent.com/$REPO/$REF/$path" 2>/dev/null; then
    got=$(sha256 "$BODY")
    size=$(wc -c < "$BODY" | tr -d ' ')
  else
    got=GONE
    size=0
  fi
  jq --arg p "$path" --arg s "$got" --argjson n "$size" --arg w "$note" \
    '.[$p] = {sha256: $s, bytes: $n, why: $w}' "$NEWFILES" > "$NEWFILES.tmp" && mv "$NEWFILES.tmp" "$NEWFILES"

  if [ "$got" = "$want" ]; then
    printf -- '- ✅ `%s` — unchanged\n' "$path" >> "$REPORT"
  elif [ "$got" = GONE ]; then
    DRIFT=1
    printf -- '- ❌ `%s` — **no longer exists at `%s`** (moved or deleted upstream)\n  - port relies on it for: %s\n' "$path" "$REF" "$note" >> "$REPORT"
  else
    DRIFT=1
    printf -- '- ⚠️ `%s` — **changed** (%s bytes at `%s`)\n  - port relies on it for: %s\n' "$path" "$size" "$REF" "$note" >> "$REPORT"
  fi
done

if [ "$MODE" = update ]; then
  jq --arg ref "$REF" --argjson files "$(cat "$NEWFILES")" \
    '.trackedRef = $ref | .trackedVersion = ($ref | ltrimstr("v")) | .files = $files' \
    "$LOCK" > "$LOCK.tmp" && mv "$LOCK.tmp" "$LOCK"
  echo "updated $LOCK: now tracking $REPO@$REF"
  echo "Review the diff and re-port any changed surface before committing."
  exit 0
fi

echo "## Upstream drift report"
echo
echo "- upstream: \`$REPO\`"
echo "- tracked by this port: \`$TRACKED_REF\`"
echo "- latest upstream: \`$REF\`"
echo
cat "$REPORT"
echo

if [ "$DRIFT" -eq 0 ] && [ "$TRACKED_REF" = "$REF" ]; then
  echo "No drift. The port is current with \`$REF\`."
  exit 0
fi

echo "**Action required.** Re-port the changed surface, then run:"
echo
echo '```sh'
echo "sh scripts/check-upstream-drift.sh --update"
echo '```'
echo
echo "Update the \"Fidelity to upstream\" section of README.md in the same change."
exit 1
