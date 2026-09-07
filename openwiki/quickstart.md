---
type: Entrypoint
title: openwiki-cc — quickstart
description: Entry point to the openwiki-cc wiki — a native Claude Code, Codex, and opencode port of langchain-ai/openwiki that generates and maintains an openwiki/ documentation wiki for a target repository.
tags: [openwiki-cc, agent-port]
generated: { by: muse-spark, at: 2026-09-07T23:24:26Z }
---

# openwiki-cc — quickstart

**openwiki-cc** is a native **Claude Code**, **OpenAI Codex**, and **opencode** port of
[langchain-ai/openwiki](https://github.com/langchain-ai/openwiki): an agent that generates and
maintains a documentation wiki (an `openwiki/` directory) for *any* repository — the same wiki
you are reading now was produced by running it against this repo.

Upstream OpenWiki ships as a standalone CLI carrying its own harness (DeepAgents/LangGraph, a
shell backend, a SQLite checkpointer, provider adapters, an Ink TUI). Coding agents already
provide all of that plumbing. This repo extracts **only the agent** — the documentation system
prompt, the git-evidence collection, the wiki structure rules, and the idempotence logic — and
re-expresses it natively for each host. There is no application server, no build, no runtime; the
deliverable is the agent definition itself, expressed as prompt files.

## What lives here

| Path | Role |
|---|---|
| [`commands/wiki.md`](../commands/wiki.md) | The Claude Code slash command → `/openwiki:wiki`. Contains the full routing, the six-step lifecycle (Steps 0, 1, 2, 3, 3b, 4), and the verbatim upstream planner + per-page-worker prompts. **This is the canonical agent definition.** |
| [`.agents/skills/openwiki/SKILL.md`](../.agents/skills/openwiki/SKILL.md) | The same agent for the shell-based hosts — **Codex** (`$openwiki`) and **opencode**, which both discover `.agents/skills/`. Same planner/worker prompts and lifecycle; the per-page-worker dispatch section is host-conditional (one subagent per page where the host has a subagent tool, sequential writing under the same worker discipline on Codex). |
| [`.opencode/commands/wiki.md`](../.opencode/commands/wiki.md) | opencode's `/wiki`. Mode routing from `$ARGUMENTS` only — it delegates to the skill rather than restating the prompt. |
| [`.claude-plugin/plugin.json`](../.claude-plugin/plugin.json), [`marketplace.json`](../.claude-plugin/marketplace.json) | Packaging so Claude Code can install the command as a plugin from a marketplace. |
| [`hooks/openwiki-gate.sh`](../hooks/openwiki-gate.sh) | Optional shell gate for auto-running the wiki from a Claude Code `Stop`/`SessionEnd` hook — spawns the frontier model only when source actually changed. |
| [`hooks/test_gate.sh`](../hooks/test_gate.sh) | Self-check for the gate's skip/run decisions. |
| [`upstream.lock.json`](../upstream.lock.json) | The upstream ref this port was ported from, plus a SHA-256 per reproduced file. |
| [`scripts/check-upstream-drift.sh`](../scripts/check-upstream-drift.sh) | Re-hashes those files against the latest upstream release. |
| [`scripts/extract-upstream-prompt.py`](../scripts/extract-upstream-prompt.py) | Pulls the planner/worker prompt text straight out of upstream's `src/agent/repository-prompts.ts` for reproduction in `commands/wiki.md` / `SKILL.md`, so the prompt is never hand-retyped. |
| [`scripts/openwiki-finalize.py`](../scripts/openwiki-finalize.py) | Step 2 `--snapshot` (migrate + body-hash state) and Step 3b finalize (indexes, link validation, `generated` provenance) of a run. Idempotent and never deletes content; tested by `scripts/test_finalize.py`. |
| [`.github/workflows/upstream-drift.yml`](../.github/workflows/upstream-drift.yml) | Runs that check weekly and keeps one issue in sync with the report. |
| [`README.md`](../README.md) | Human-facing install + usage guide. |

The single source of truth for the agent's behavior is `commands/wiki.md`; the Codex `SKILL.md`
tracks it with host-specific adaptations. When they disagree, `commands/wiki.md` is authoritative.

> **This port is tracked against upstream `v0.5.0`, repository output mode.** The planner
> and per-page-worker prompts are extracted programmatically from
> `src/agent/repository-prompts.ts` with
> [`scripts/extract-upstream-prompt.py`](../scripts/extract-upstream-prompt.py) rather than
> retyped, so transcription drift is not possible. See
> [Detecting drift](architecture.md#detecting-upstream-drift) for how future upstream moves are
> tracked and [Fidelity to upstream](../README.md#fidelity-to-upstream) for the per-file detail,
> including what upstream ships that this port deliberately does not (the claims subsystem,
> durable resumable page jobs, mermaid parse-validation, upstream's own installer, personal
> output mode, the chat prompt, `--language`).

## Install & run

**Claude Code (plugin):**
```
/plugin marketplace add SoulKyu/openwiki-cc
/plugin install openwiki@openwiki-cc
```
Then from the root of a target repo: `/openwiki:wiki` (auto-routes), `/openwiki:wiki init`, or
`/openwiki:wiki update`. Plugin commands are always namespaced, so it is `/openwiki:wiki`, never a
bare `/openwiki`. Copying `commands/wiki.md` into `.claude/commands/` instead gives a bare `/wiki`.

**Codex and opencode (skill):** copy `.agents/skills/openwiki/` into `~/.agents/skills/` (or a
repo's `.agents/skills/`) — both hosts read that path — then restart the host. Invoke `$openwiki`
on Codex, or ask either host to "update the openwiki docs". For a real `/wiki` with `init`/`update`
arguments on opencode, also copy `.opencode/commands/wiki.md` into `~/.config/opencode/commands/`.

Full install variants (per-project vs global, both hosts) are in the [README](../README.md).

**Any host (skills CLI):** `npx skills add icampana/openwiki-cc` discovers this same skill and can
install it into opencode, Claude Code, Codex, Cursor, and 70+ more agents from one command. The
Step 3b finalizer ships inside the skill folder, so installs are complete out of the box; details
in [Install — any agent](../README.md#install--any-agent-one-command).

## The three commands

| Invocation | Behavior |
|---|---|
| `/openwiki:wiki` | **Auto-route**: `openwiki/` exists → update, else → init. |
| `/openwiki:wiki init` | Build the wiki from scratch (≤ 8 pages; 1–2 for a small repo). |
| `/openwiki:wiki update` | Surgically refresh only pages affected by changes since the last run. |
| `/openwiki:wiki update <instruction>` | Same, plus an extra instruction appended to the run. |

`init` vs `update` is the core distinction: **init** builds structure from scratch; **update** is
deliberately conservative — it diffs against the last run and edits only what the changes touched,
and it can legitimately be a **no-op** when nothing relevant changed. See
[architecture.md](architecture.md) for how a run actually executes.

## Model tier — do not run this small

Upstream OpenWiki assumes a **frontier coding model** (default `z-ai/glm-5.2`; provider list
includes Claude Opus 4.8 / Sonnet 5 / GPT 5.5). Run the command **and its subagents on Opus 4.8**
(Sonnet 5 minimum) on Claude Code, or Codex's strongest tier with high reasoning effort.
Documentation quality depends directly on the model — a small/fast tier produces a shallow wiki.

## Where to go next

- **[architecture.md](architecture.md)** — how a run executes end-to-end: the two host ports, the
  routing + six-step lifecycle (no-op check → git evidence → snapshot → planner/workers →
  finalize → metadata), idempotence, per-page workers, root-file wiring, and the hook-based auto-run with its shell gate.
- **[Fidelity to upstream](../README.md#fidelity-to-upstream)** — what is verbatim from OpenWiki's
  source vs adapted for these harnesses, and why.

## Changing this repo

- **Behavior of the agent** → edit [`commands/wiki.md`](../commands/wiki.md), then mirror any
  semantic change into [`SKILL.md`](../.agents/skills/openwiki/SKILL.md). There are only these two
  definitions; `.opencode/commands/wiki.md` carries no agent logic, so it does not need mirroring. Keep the reproduced
  system prompt faithful to upstream; mark harness adaptations explicitly (upstream marks them
  `[adapted]`).
- **Packaging / version** → [`plugin.json`](../.claude-plugin/plugin.json) (bump `version`) and
  [`marketplace.json`](../.claude-plugin/marketplace.json).
- **Re-porting a change from upstream** → after updating `commands/wiki.md` and `SKILL.md`, run
  `sh scripts/check-upstream-drift.sh --update` to record the new hashes, and correct
  [Fidelity to upstream](../README.md#fidelity-to-upstream) in the same change. Skipping the
  `--update` leaves the lock asserting an older ref; running it *without* re-porting silences the
  alarm while the port stays stale.
- **Finalizer logic** → [`scripts/openwiki-finalize.py`](../scripts/openwiki-finalize.py). The skill ships an identical twin at [`openwiki/scripts/`](../.agents/skills/openwiki/scripts/) so installed-skill runs get Step 3b; `python3 scripts/test_finalize.py` fails when they drift — update both. [`.github/workflows/tests.yml`](../.github/workflows/tests.yml) runs that guard, plus the gate tests, on every push and pull request.
- **Step 3b script lookup** → the `ls` ladder in [`commands/wiki.md`](../commands/wiki.md) and [`SKILL.md`](../.agents/skills/openwiki/SKILL.md). Add a path there whenever a new install layout appears; the two ladders must stay identical.
- **Auto-run gate** → [`hooks/openwiki-gate.sh`](../hooks/openwiki-gate.sh); run
  `sh hooks/test_gate.sh` after any change to it (it exercises every skip/run branch).
