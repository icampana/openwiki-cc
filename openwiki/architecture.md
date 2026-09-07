---
type: Architecture Overview
title: Architecture — how a run executes
description: The execution contract behind an openwiki-cc run — mode routing, the git-evidence/snapshot/system-prompt/finalize/metadata lifecycle, idempotence, root-agent-file behavior, and upstream drift detection.
tags: [openwiki-cc, agent-port]
generated: { by: muse-spark, at: 2026-09-07T23:24:26Z }
---

# Architecture — how a run executes

openwiki-cc has no runtime of its own. Its "architecture" is the **execution contract** encoded
in the agent definition: what the host model must do, in what order, before and after it writes
documentation. This page describes that contract and how it is packaged for two hosts.

Start at [quickstart.md](quickstart.md) if you haven't; this page is the deep dive.

## Three hosts, two agent definitions

The agent is written twice, not three times. Codex and opencode both discover
`.agents/skills/<name>/SKILL.md`, so one skill file serves both; opencode additionally gets a thin
command for slash arguments, which holds no agent logic.

| | Claude Code — [`commands/wiki.md`](../commands/wiki.md) | opencode — [`SKILL.md`](../.agents/skills/openwiki/SKILL.md) + [`.opencode/commands/wiki.md`](../.opencode/commands/wiki.md) | Codex — [`SKILL.md`](../.agents/skills/openwiki/SKILL.md) |
|---|---|---|---|
| Trigger | `/openwiki:wiki [init\|update] [instruction]` | `/wiki [init\|update] [instruction]`, or ask in natural language | `$openwiki` (or natural-language "update the openwiki docs") |
| Mode input | explicit token, else auto-route | explicit token via `$ARGUMENTS`, else auto-route | phrasing, else `openwiki/` auto-detect (no slash args) |
| Page writing | one **subagent per planned page** (Task tool), each own context window | same — opencode has a subagent tool | orchestrator writes pages one at a time under the same worker discipline (no subagent tool) |
| Filesystem | native Read/Write/Edit/Glob/Grep/Bash on real repo paths | host-native shell + edit tools | same, `apply_patch` for writes |

`commands/wiki.md` is authoritative; when it and `SKILL.md` disagree, the command wins. Both
reproduce OpenWiki `v0.5.0`'s planner + per-page-worker prompts **verbatim from upstream
source** (not from memory), with harness adaptations marked `[adapted]` inline: (a) DeepAgents'
virtual filesystem → the host's native file tools on real paths; (b) upstream's durable
page-job queue → the plan held in orchestrator context plus one host subagent per page
(sequential writing on hosts without a subagent tool); (c) upstream's prepare/finalize
harness → Step 2's `--snapshot` and Step 3b; (d) the claims subsystem is out of scope —
workers write pages directly, with no submission tool, inspection tool, or claims guidance.

That second adaptation is the **only** place the hosts genuinely diverge. Rather than fork the
skill per host, its dispatch paragraph is host-conditional — subagent dispatch where the tool
exists, sequential writing on Codex. The prompt text is identical either way. Keeping one file
avoids a third copy of the prompt drifting out of sync.

## Mode routing

The mode is resolved before any work:

- `init` → build from scratch (assume `openwiki/` has nothing useful).
- `update` → maintenance pass (inspect existing docs, edit surgically).
- **empty** → auto-route: `test -d openwiki && echo update || echo init`.
- A leading `init`/`update` token selects the mode; any remaining text becomes an **additional
  user instruction** appended to the run. With no mode token, auto-route and treat the whole
  argument string as the instruction (upstream OpenWiki's `[message]`).

Auto-route replaces upstream's interactive-chat default — a plugin slash command is always
namespaced and can't be a bare conversational `/openwiki`.

## The run lifecycle (Claude Code, six steps)

`commands/wiki.md` drives the model through a fixed sequence. `SKILL.md` mirrors it for Codex.

**Step 0 — pre-run no-op check** *(update mode only, and only when no extra instruction was
given)*. Mirrors upstream `v0.5.0` `getUpdateNoopStatus` / `shouldCheckUpdateNoop`: skip the
model work when nothing relevant changed. Read `openwiki/.last-update.json`; if it has a
`gitHead`, the run is skipped when **both** hold:
- `git status --short` is empty after ignoring any line for `openwiki/.last-update.json`; **and**
- `HEAD == gitHead`, **or** every path in `gitHead..HEAD` is under `openwiki/`.

With no recorded `gitHead`, this check is skipped and the run proceeds. When the check skips,
the run still refreshes `updatedAt` in `.last-update.json` (upstream #647 — a no-op update
still means OpenWiki ran) so freshness checks reflect the actual last run.

**Step 1 — collect update context** (before any write; all `git --no-pager`, all read-only).
Always `rev-parse HEAD` and `status --short`. On update with a `gitHead`, also
`diff --name-only gitHead..HEAD` plus `log gitHead..HEAD` for orientation — the changed-paths
list is the planner's update window. On init, or update without prior metadata,
`log --max-count=20` instead. History is a discipline, not a prescribed block. A
`.openwikiignore` check selects the git-history/discovery prompt variants and sets the read
boundary for the whole run.

**Step 2 — prepare the wiki** (migrate + provenance snapshot, before any writing):
```bash
python3 <finalizer> --snapshot openwiki
```
Backfills OKF front matter on pages missing it (tagging inferred fields
`openwiki_generated: true` for a later run to upgrade) and writes `.openwiki-run.json` at
the repository root: each concept page's body hash (SHA-256 of the body excluding front
matter) plus its prior `generated` event. The state file lives outside `openwiki/`, is
consumed and deleted by Step 3b, and is overwritten by the next run — a crash between the
steps self-corrects.

**Step 3 — plan, then write pages.** The orchestrator first acts as the **planner**
(upstream `createRepositoryPlannerPrompt`): explore the repo, design the smallest complete
information architecture (hierarchical paths, `relatedPages` for navigation, quickstart
required on init, `pages: []` allowed when an update needs nothing), and hold the plan —
path, title, purpose, seedPaths, relatedPages, instructions per page — in context. Then one
**page worker** per planned page (upstream `createRepositoryPagePrompt`), each briefed with
its plan entry: update workers read the current page first and change only what repository
evidence requires; every worker writes exactly one page and owns nothing else.

Every page MUST begin with valid OKF v0.2 concept front matter (`type` required; `title`,
`description`, `tags` recommended). Workers must not author `generated`, `verified`,
`sources`, `timestamp`, or OpenWiki control fields — OpenWiki owns those. The skeleton-critic
and QA-verifier subagent waves of the old v0.3.3 port are gone: upstream deleted both
subsystems in v0.5.0, and the per-page worker model replaces them. There is no diagram
discipline in the repository prompts. The broken-link repair loop is the port's own
`[adapted]` addition: a worker that finds an `openwiki: broken internal link` comment repairs
the href or restores the target, then deletes the comment.

**Step 3b — finalize (deterministic, no model involved).** Runs
[`scripts/openwiki-finalize.py`](../scripts/openwiki-finalize.py) in finalize mode:
```bash
python3 <the Step 2 path> openwiki --actor <the model you are running as>
```
Regenerates every directory `index.md` (the root carries `okf_version: "0.2"`), annotates
broken internal links with an HTML comment rather than deleting anything, then reconciles
**generated provenance**: pages whose body changed this run are stamped
`generated: { by: <actor>, at: <now> }` and lose any legacy `timestamp`; unchanged pages keep
(or are restored to) their prior stamp. It deletes `.openwiki-run.json`, always exits 0, and
never deletes content. It is idempotent by contract — a run that changes no page bodies leaves
every wiki file byte-identical — otherwise a no-op `update` would churn files and defeat the
gate described below.

**Step 4 — persist metadata.** Write `openwiki/.last-update.json` on **every** completed run,
including no-ops (upstream #647):
```json
{ "updatedAt": "<ISO 8601>", "command": "init|update", "gitHead": "<git rev-parse HEAD>", "model": "<model id>", "status": "complete" }
```
`status` is upstream's `UpdateRunStatus` (`complete` | `interrupted`). This port always writes
`complete`: it cannot persist metadata mid-interrupt, so an interrupted run leaves the previous
file untouched and the next update re-runs on the dirty tree — the conservative equivalent of
`interrupted`.

## Idempotence & state

Two independent mechanisms keep re-runs cheap and honest:

- **Body-hash snapshot (Steps 2/3b)** — each page's SHA-256 decides whether its `generated`
  stamp advances. Unchanged bodies keep (or are restored to) their prior stamp, so a second
  consecutive full run leaves every wiki file byte-identical. Only `.last-update.json`
  refreshes its timestamp on a no-op, by design — and the tree hash the gate cares about
  excludes it.
- **`gitHead` in `.last-update.json`** — the commit the *next* `update` diffs against. This single
  file replaces upstream OpenWiki's SQLite checkpointer; durable crash-resume is intentionally
  dropped as unnecessary on these hosts. The single update window (changed paths since
  `gitHead`) replaces upstream's per-page committed baselines.

## Root agent-file wiring

Upstream v0.5.0 repository prompts carry no security section — upstream enforces this in
harness tooling the port does not have — so the port retains its own `[adapted]` line: a run
does **not** read secrets (`.env`, keys, credentials) and does **not** create or edit agent
instruction files (`AGENTS.md`, `CLAUDE.md`). The headless-permissions allowlist at the bottom
of `commands/wiki.md` deliberately omits `Write`/`Edit` grants for either file — enforcement
doesn't rest solely on the model obeying its own prompt. If a repository's agent instructions
already reference OpenWiki, a run keeps those references accurate but does not edit them unless
explicitly asked. `openwiki/INSTRUCTIONS.md`, when present, is treated the same way: read for
scope and priorities, never rewritten as part of routine init/update runs — and reserved from
concept front matter, index entries, and provenance like `index.md` and `log.md`.

## Auto-run via a Stop/SessionEnd hook

To keep the wiki fresh automatically, a Claude Code hook can invoke the command headlessly
(`claude -p '/openwiki:wiki update' --permission-mode acceptEdits`). The naive version spawns a
full frontier-model run **every turn** — even when nothing changed, it pays the model cost just to
let Step 0 discover there's no work.

[`hooks/openwiki-gate.sh`](../hooks/openwiki-gate.sh) fixes that by reproducing the Step 0 no-op
check in **pure shell** (a few `git` commands, zero tokens) and spawning `claude` **only** when
source actually changed since the recorded `gitHead`. It also carries the `OPENWIKI_HOOK=1` guard
(the headless run fires its own `Stop` hook → the gate exits early to avoid infinite recursion)
and detaches with `setsid` so it never blocks the session. Wired in `.claude/settings.json`:
```json
{ "hooks": { "Stop": [ { "hooks": [ { "type": "command", "command": "sh .claude/hooks/openwiki-gate.sh" } ] } ] } }
```
With the gate, `Stop` (every turn) is cheap enough for continuously-live docs; the frontier model
starts only on a real change. [`hooks/test_gate.sh`](../hooks/test_gate.sh) stubs `claude` and
exercises every skip/run branch — run it after touching the gate.

## Detecting upstream drift

The wiki's own idempotence (above) answers "did *this* repo change?". A second, independent loop
answers "did *upstream* change?" — and it exists because nothing here can answer it otherwise.

This port reproduces a slice of `langchain-ai/openwiki` as **prose inside prompt files**. There is
no import, no lockfile entry, no dependency edge of any kind. Upstream can rewrite the planner
prompt and every check in this repo still passes — that gap is exactly how the port spent six
weeks tracking upstream `0.0.4` while upstream had already moved to `v0.3.3` before the OW-3
re-port closed it, and how the OW-6 re-port closed the next gap to `v0.5.0`.

So the drift is polled instead:

- [`upstream.lock.json`](../upstream.lock.json) pins `trackedRef` (now `v0.5.0`, the ref this port
  was re-ported from) and, per reproduced file, a SHA-256 plus a `why` note naming what in this
  repo depends on it. Tracked today: `src/agent/index.ts`, `src/agent/repository-prompts.ts`,
  `src/agent/utils.ts`, `src/agent/wiki-finalizer.ts`, `src/agent/wiki-link-validator.ts`,
  `src/okf/frontmatter.ts`, `src/okf/generated-provenance.ts`, `src/okf/index-sync.ts` — the
  last five backing `scripts/openwiki-finalize.py`'s snapshot/finalize modes, the first three
  backing the lifecycle and the planner/worker prompts.
- [`scripts/check-upstream-drift.sh`](../scripts/check-upstream-drift.sh) resolves the latest
  upstream release, re-hashes each tracked file at that ref, and diffs against the lock. Exit `0`
  clean, `1` drift, `2` the check itself is broken (missing `jq`, unreachable API, bad `--ref`) —
  a distinction the CI job relies on, since a broken check must not read as "no drift".
- [`.github/workflows/upstream-drift.yml`](../.github/workflows/upstream-drift.yml) runs it weekly
  and keeps a **single** issue in sync with the report, closing it when the port catches up. One
  reused issue rather than a fresh one per run — a weekly job that opens a new issue every time
  trains you to ignore the label. It also runs on pull requests touching the lock or the script,
  so a hand-edited lock fails the PR.

A file recorded as `GONE` would mean it does not exist at `trackedRef` — the lock's way of saying
"upstream has this and the port does not cover it yet". None of the currently tracked files are in
that state; what upstream ships and this port still doesn't cover (the claims subsystem, durable
resumable page jobs, mermaid parse-validation, upstream's own installer, personal output mode,
the chat prompt, `--language`) is recorded instead in
`upstream.lock.json`'s `notCovered` map and in [Fidelity to upstream](../README.md#fidelity-to-upstream).

**Two traps worth keeping.** Never hash a file body captured through `$(...)` — command
substitution strips trailing newlines, so every hash silently shifts and the check reports drift
even against the ref the lock was built from. And an explicit `--ref` is validated before hashing,
because a typo'd ref 404s every file and the report then claims upstream deleted the whole agent.

Note that `--update` only *records* the current upstream hashes. It does not re-port anything;
running it without doing the porting work converts a true alarm into a false all-clear.

## Headless / CI permissions

For non-interactive runs (`claude -p`) without permission prompts, grant a minimal allowlist in
`.claude/settings.json`: read-only git + `find`/`rg`/`date`/`ls`/`python3` (for the Step 2/3b
finalizer lookup and runs), with `Write`/`Edit` scoped to `openwiki/**` only. The exact snippet
lives at the bottom of [`commands/wiki.md`](../commands/wiki.md) — it is the OpenWiki `ShellAllowList` expressed as
Claude Code permissions.
