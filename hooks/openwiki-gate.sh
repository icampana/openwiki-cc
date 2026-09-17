#!/usr/bin/env sh
# openwiki gate — spawn the wiki refresh only when source actually changed.
#
# Reproduces the wiki's Step 0 no-op check (getUpdateNoopStatus) in pure shell,
# so a Stop/SessionEnd hook that fires every turn costs a few git commands
# instead of a full frontier-model run that spins up only to find nothing to do.
#
# Install: copy to .claude/hooks/openwiki-gate.sh, then in .claude/settings.json:
#   { "hooks": { "Stop": [ { "hooks": [
#     { "type": "command", "command": "sh .claude/hooks/openwiki-gate.sh" } ] } ] } }
set -eu

[ -n "${OPENWIKI_HOOK:-}" ] && exit 0                   # child run: never re-trigger
git rev-parse --git-dir >/dev/null 2>&1 || exit 0       # not a git repo → nothing to do

meta=openwiki/.last-update.json
head=$(git rev-parse HEAD 2>/dev/null) || exit 0
# ponytail: sed-parse gitHead out of the flat JSON; jq if the shape ever nests.
last=$([ -f "$meta" ] && sed -n 's/.*"gitHead":[[:space:]]*"\([^"]*\)".*/\1/p' "$meta" | head -1 || true)
# .last-update.json is rewritten by every run; openwiki/experience/ is the
# accumulated experience layer, written by /openwiki:observe rather than derived
# from source. Neither is a reason to regenerate documentation.
dirty=$(git status --short --untracked-files=all \
  | grep -v 'openwiki/\.last-update\.json$' \
  | grep -v 'openwiki/experience/' || true)

# No recorded gitHead → upstream skips the no-op check → let claude decide.
if [ -n "$last" ] && [ -z "$dirty" ]; then
  [ "$head" = "$last" ] && exit 0                        # HEAD unchanged + clean tree
  # HEAD moved but tree clean: skip when every changed path is under openwiki/.
  [ -z "$(git diff --name-only "$last..$head" | grep -v '^openwiki/' || true)" ] && exit 0
fi

OPENWIKI_HOOK=1 setsid claude -p '/openwiki:wiki update' --permission-mode acceptEdits >/dev/null 2>&1 &
