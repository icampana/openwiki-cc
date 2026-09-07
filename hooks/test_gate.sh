#!/usr/bin/env sh
# Self-check for openwiki-gate.sh. Stubs claude/setsid so nothing real runs.
# Run: sh hooks/test_gate.sh   (exits non-zero on first failed assertion)
set -eu
gate=$(cd "$(dirname "$0")" && pwd)/openwiki-gate.sh
tmp=$(mktemp -d)
trap 'cd /; rm -rf "$tmp"' EXIT

# Stubs on PATH: setsid execs its args; claude records that it was invoked.
mkdir "$tmp/bin"
printf '#!/bin/sh\nexec "$@"\n' > "$tmp/bin/setsid"
printf '#!/bin/sh\ntouch "$SPAWNED"\n' > "$tmp/bin/claude"
chmod +x "$tmp/bin/setsid" "$tmp/bin/claude"
export PATH="$tmp/bin:$PATH"

repo="$tmp/repo"; mkdir "$repo"; cd "$repo"
git init -q && git config user.email t@t && git config user.name t
echo hello > file.txt && git add . && git commit -qm init
H=$(git rev-parse HEAD)
mkdir openwiki

run() {                                   # run() <label> <expect: spawn|skip>
  export SPAWNED="$tmp/spawned"; rm -f "$SPAWNED"
  sh "$gate"
  i=0; while [ ! -f "$SPAWNED" ] && [ $i -lt 20 ]; do sleep 0.05; i=$((i+1)); done
  got=skip; [ -f "$SPAWNED" ] && got=spawn
  if [ "$got" = "$2" ]; then echo "ok   - $1 ($got)"; else
    echo "FAIL - $1: expected $2, got $got"; exit 1; fi
}

# no meta → upstream skips no-op check → spawn
run "no metadata file" spawn

# meta HEAD==head, clean tree → skip
printf '{"gitHead": "%s"}\n' "$H" > openwiki/.last-update.json
run "clean tree, HEAD unchanged" skip

# metadata refreshed in place by a no-op run (upstream #647: updatedAt always
# refreshes) → still skip; only .last-update.json differs
printf '{"gitHead": "%s", "updatedAt": "2026-09-07T12:00:00.000Z", "command": "update", "model": "m", "status": "complete"}\n' "$H" > openwiki/.last-update.json
run "metadata refreshed on no-op" skip

# dirty source file → spawn
echo change >> file.txt
run "dirty source file" spawn
git checkout -q file.txt

# untracked openwiki/ file → spawn (only .last-update.json is excused in the tree)
echo x > openwiki/page.md
run "untracked openwiki/ file in tree" spawn
rm openwiki/page.md

# HEAD moved, source commit → spawn
echo more >> file.txt && git commit -qam second
run "HEAD moved with source change" spawn

# record new head, HEAD-only-openwiki commit → skip
printf '{"gitHead": "%s"}\n' "$(git rev-parse HEAD)" > openwiki/.last-update.json
git add openwiki/.last-update.json && git commit -qm "openwiki: page" \
  && echo p > openwiki/p.md && git add openwiki/p.md && git commit -qm "openwiki only"
run "HEAD moved but only openwiki/ paths" skip

echo "all passed"
