# Re-port openwiki-cc to upstream OpenWiki v0.5.0

**Status:** approved design, not yet implemented
**Date:** 2026-09-07
**Tracking issue:** icampana/openwiki-cc#6
**Supersedes:** the v0.3.3 port's prompt architecture (not its scripts — those evolve)

## Problem

The scheduled drift check detected that `langchain-ai/openwiki` moved from `v0.3.3`
to `v0.5.0` (releases v0.4.0–v0.5.0, 2026-08-25 → 2026-09-01). Every file the port
tracks changed, and the change is architectural, not editorial:

| Upstream file | v0.3.3 | v0.5.0 | What happened |
|---|---|---|---|
| `src/agent/prompts/code.ts` | 51,171 B | 10,066 B | Now holds only the `chat` prompt. `CODE_SYSTEM_PROMPTS.init`/`.update` — the port's core artifact — **no longer exist**. |
| `src/agent/repository-prompts.ts` | absent | 9,463 B | The replacement: a planner prompt plus a per-page worker prompt. |
| `src/agent/utils.ts` | 14,768 B | 29,960 B | No-op and metadata semantics changed (see below). |
| `src/okf/frontmatter.ts` | 12,416 B | 26,282 B | OKF v0.2: code-owned `generated` provenance, deterministic repair. |
| `src/agent/index.ts` | 61,637 B | 68,741 B | Run lifecycle rewritten around resumable page jobs. |
| `src/agent/prompt.ts` | 9,129 B | 9,735 B | `createSystemPrompt` now **throws** for repository init/update: "Repository generation does not use shared agent prompts." |

`createSystemPrompt` throwing is the headline: there is no v0.5.0 prompt text to
re-extract into the port's existing Step 3. The generation model itself changed.

## Scope

**In:** repository output mode, `init` and `update`, under the new planner + worker
model; the deterministic finalizer upgraded to OKF v0.2; metadata and no-op
semantics following upstream.

**Out, deliberately** (each recorded in `upstream.lock.json` `notCovered`):

- **The claims subsystem** — `src/claims/` (brains, evidence resolvers, store),
  `src/okf/claim-sources.ts`, `src/okf/claims-verification.ts`, the claims guidance
  in worker prompts, and the `submit_page`/`inspect_claims` tools. Claims require a
  persistent store and machine verification; a prompt-only port cannot provide
  either. Workers write pages directly. Pretending otherwise would be a fidelity lie.
- **Durable resumability** — `src/generation/` (page jobs, run state, `.run.json`
  persistence, page update windows). The port runs in one session. Update planning
  uses the only window that exists: the last recorded `gitHead`.
- **Mermaid parse-validation** — upstream parses every fence in a DOM shim and
  converts failures to ```` ```text ```` with an `openwiki: mermaid parse failed`
  comment. Python's standard library cannot parse mermaid, and a fence-balance
  check would not be validation. Omitted. No prompt text is lost: upstream v0.5.0
  repository prompts carry no diagram discipline (verified —
  `createDiagramInstructions` is attached only outside the repository pipeline).
- **`src/integrations/`** — upstream's own coding-agent installer. This port *is*
  the alternative distribution.
- **`CODE_SYSTEM_PROMPTS.chat`, `personal.ts`, `--language`** — as in the v0.3.3
  port.

## How upstream v0.5.0 differs

### Generation model

One monolithic agent with a 26 KB discipline catalog became:

1. **Planner** (`createRepositoryPlannerPrompt`) — explores the repository, designs
   the page architecture (`/openwiki/quickstart.md` required on init; hierarchical
   paths; `relatedPages` for navigation; `pages: []` allowed when an update needs
   nothing), and submits the plan via a `submit_plan` tool.
2. **Page workers** (`createRepositoryPagePrompt`) — one fresh worker per planned
   page, briefed with path, title, purpose, seed paths, related pages, and
   mode-specific instructions. Each writes exactly one page and submits sparse
   claim decisions via `submit_page`. Update workers read the existing page first
   and preserve unaffected content.

The skeleton critic and QA-verifier subsystems (`skeleton_critic.ts`,
`wiki_qa_subagents.ts`) were **deleted**. The v0.3.3 port's init-mode critic and
verifier waves must go with them.

### Finalizer lifecycle

`src/agent/wiki-finalizer.ts` (new at a tag) splits the work:

- **`prepareWikiForAuthoring`** (before authoring): `migrate` (backfill/normalize
  OKF front matter) + `provenance_snapshot` (per-page body hash + prior
  `generated` event).
- **`finalizeWikiArtifacts`** (after authoring), in order: `mermaid` → `index_sync`
  → `link_validation` → `claims_sources` (optional) → `generated_provenance`.

### OKF v0.2 and generated provenance

`src/okf/generated-provenance.ts` defines the contract:

- Snapshot: for each concept page, SHA-256 of the **body excluding front matter**
  (whitespace retained), plus the prior valid `generated: { by, at? }` event.
- Finalize, per page: body changed or new → stamp `generated: { by: <actor>, at: <now> }`
  (single-line flow mapping), remove the superseded legacy `timestamp` field,
  canonicalize terminal line endings to exactly one LF, then run
  `repairOkfFrontmatter`. Body unchanged → restore the prior event if the agent
  removed or altered it; remove any stamp if the page was previously unstamped.
- Best-effort: an unreadable page is skipped, never fatal.
- Actor: upstream stamps `openwiki/<version>`; page-specific overrides name other
  producers. Root `index.md` front matter becomes `okf_version: "0.2"`.
- `openwiki_translation_pending` is a tolerated, code-managed marker.
- `openwiki_generated: true` backfill (the work-queue flag) is unchanged from
  v0.3.3.
- `INSTRUCTIONS.md` joins `index.md`/`log.md` as excluded from concept treatment —
  a v0.3.3 port gap this re-port closes.

### Metadata and no-op semantics

`src/agent/utils.ts`:

- `.last-update.json` shape: `{updatedAt, command, gitHead, model, status,
  language?}`. The port omits `language` (no `--language` equivalent), as before.
- **No-op runs now refresh `updatedAt`** (#647): "a no-op update still means
  OpenWiki ran", so freshness checks reflect reality. This deliberately breaks the
  v0.3.3 port's "a no-op writes nothing" contract.
- `status: "interrupted"` is written by upstream's process-interrupt handling so
  the next update re-runs instead of skipping. The port cannot reliably write
  metadata mid-interrupt; it keeps the conservative equivalent (leave previous
  metadata untouched — a partial run leaves a dirty `openwiki/` tree, which the
  no-op check already treats as meaningful).
- The no-op check treats `openwiki/`-only committed changes and
  `.last-update.json` working-tree changes as non-meaningful. `hooks/openwiki-gate.sh`
  already implements both exclusions and survives the metadata-refresh change
  unchanged (verified by reading it; a test proves it).

### Init semantics

#699: `init` on an existing wiki regenerates it from scratch. The port's
auto-routing (init when `openwiki/` is absent, update when it exists) is unchanged,
but an explicit init on an existing wiki now means fresh generation.

## Design

### The run lifecycle

| Step | Change |
|---|---|
| 0 — no-op check | **+** refresh `updatedAt` on the skip path (shell one-liner, no model) |
| 1 — git evidence | **slimmed**: metadata read + `git diff --name-only <gitHead>..HEAD` (update context for the planner); history remains a discipline, not a prescribed block; the `.openwikiignore` check is **retained** `[adapted]` — the read boundary applies to the whole run (planner and workers), with the same variant selection as v0.3.3 |
| 2 — snapshot | **+** `python3 scripts/openwiki-finalize.py --snapshot openwiki` = migrate + provenance snapshot, state at repo-root `.openwiki-run.json` |
| 3 — system prompt | **replaced**: planner phase + per-page worker phase (below) |
| 3b — finalize | **reworked**: index sync (okf 0.2) + link validation + generated provenance; consumes and deletes `.openwiki-run.json` |
| 4 — persist metadata | **+** refresh timestamp even when content unchanged; `status` semantics documented |

### Step 3 — planner + per-page workers

**Planning phase.** The main agent acts as the planner, using the planner prompt
extracted verbatim from `createRepositoryPlannerPrompt`. Adaptations, each marked
`[adapted]`:

- `submit_plan` → the plan is held in orchestrator context as a structured page
  list (path, title, purpose, seedPaths, relatedPages, instructions). No plan file
  is written; `_plan.md` is dropped entirely (upstream deleted the concept).
- Update context: upstream's `formatPageUpdateWindows` (per-page committed
  baselines) collapses to the single window a one-session port has — changed paths
  since the recorded `gitHead`, from Step 1.
- Claims context (`formatIssues`) is omitted (claims out of scope).
- `wikiGoal` (upstream `readRepositoryWikiInstructions`) is omitted unless the
  repository provides an equivalent; empty by default.

**Worker phase.** For each planned page, dispatch one subagent (Claude Code Task
tool / opencode subagent) briefed with the worker prompt extracted verbatim from
`createRepositoryPagePrompt`, with the page's plan entry interpolated.
Adaptations:

- Claims tooling stripped: no `submit_page`, no `inspect_claims`, no claims
  guidance blocks, no evidence-URI rules, no claim counts. The worker writes the
  page and finishes.
- OKF v0.2 front matter rules kept verbatim: the MUST block
  (`type`/`title`/`description`/`tags`) and "Do not author `generated`, `verified`,
  `sources`, `timestamp`, or OpenWiki control fields; OpenWiki owns those. On
  update preserve unknown producer-defined frontmatter fields unless they are
  factually wrong."
- "Write only this page. Do not create, edit, or delete another wiki page" kept
  verbatim — it is what makes per-page workers safe.
- Update mode: "Read the current page first. Preserve accurate unaffected content;
  change only what current repository evidence requires."
- Quickstart special case kept: the quickstart worker receives the complete
  planned page map and produces a task-routing map.
- Hosts without a subagent tool (Codex): the orchestrator writes pages one at a
  time under the same worker discipline. The prompt text is identical; only the
  execution unit differs.

**Removed from the port:** skeleton-critic and QA-verifier subagent waves
(upstream deleted both subsystems), and the diagram-discipline section (upstream
repository prompts no longer carry it).

**Kept as an explicit `[adapted]` addition:** the broken-link repair instruction.
Upstream v0.5.0 repository prompts no longer mention the
`openwiki: broken internal link` annotations, but the port's finalizer creates
them, so the port owns the repair loop: a later run that finds a comment repairs
the href or restores the target, then deletes the comment.

### Component: `scripts/openwiki-finalize.py` v0.5.0

Python 3, standard library only, **always exits 0**, idempotent. Two modes:

- **`--snapshot <wiki>`** (Step 2): runs the migrate pass (today's pass 1,
  unchanged behavior: backfill `type: Reference` + `title`/`description` +
  `openwiki_generated: true`; preserve valid blocks and unknown fields verbatim),
  then writes `.openwiki-run.json` at the **repo root** (mirroring upstream's
  `.run.json`): a sorted JSON array of `{page, bodyHash, generated?}` where
  `bodyHash` is SHA-256 of the body excluding front matter. The file lives outside
  `openwiki/` so it never pollutes the Step 2/Step 4 tree hash. `--snapshot`
  deletes any stale `.openwiki-run.json` before writing.
- **default `<wiki>`** (Step 3b): index sync → link validation → generated
  provenance, then deletes `.openwiki-run.json`.

Pass details:

- **Index sync**: as today, plus root front matter `okf_version: "0.2"` and
  `RESERVED` gains `INSTRUCTIONS.md`. `_plan.md` stays in `RESERVED` as legacy
  defense (wikis made by this port's earlier versions may contain leftovers);
  `_sidebar.md` stays (OW-5).
- **Link validation**: unchanged (annotate, never remove, strip-then-readd for
  idempotence).
- **Generated provenance** (new, runs last): per concept page, compare
  `bodyHash` against the snapshot (missing snapshot file → treat every page as
  changed: conservative, matches upstream migration, self-corrects next run).
  Changed/new → set `generated: { by: <actor>, at: <now> }` as a single-line flow
  mapping, remove legacy `timestamp`, canonicalize terminal line endings to one
  LF, then repair front matter (invalid optional fields removed; invalid
  `generated` mapping removed; `openwiki_translation_pending` tolerated).
  Unchanged → restore the prior event if tampered; remove stamps from previously
  unstamped pages. One shared `at` (UTC ISO 8601) per run. Actor comes from a new
  `--actor <id>` flag; the command passes the running model id; default
  `openwiki-cc`. Unreadable pages are skipped, never fatal.

**Idempotence** remains the correctness bar: a second consecutive run produces
byte-identical wiki files. Stamps advance only when bodies change; metadata
(`updatedAt` refresh) is excluded from the bar by design — the tree hash already
excludes `.last-update.json`, and `.openwiki-run.json` lives outside the wiki.

### Metadata and the gate

- Step 4 JSON: `{updatedAt, command, gitHead, model, status}` with
  `status: "complete"`; interrupted runs leave previous metadata untouched
  (conservative equivalent of upstream's `interrupted` status — documented as an
  adaptation).
- Step 0's skip path refreshes `updatedAt` in place (jq/sh one-liner) so freshness
  reflects the actual last run, matching #647.
- `hooks/openwiki-gate.sh` is unchanged; a test proves it still skips when only
  `.last-update.json` changed.

### Extractor

`scripts/extract-upstream-prompt.py` is rewritten: `--part planner|worker` pulls
the corresponding template literal bodies verbatim from
`src/agent/repository-prompts.ts`, marking `${...}` interpolation points by name.
The lock hashes the source file, so any upstream prompt edit trips the drift
check; the extractor keeps transcription honest in between.

### Lock and README

`upstream.lock.json` at `v0.5.0`:

- **Keep**: `src/agent/index.ts`, `src/agent/utils.ts`, `src/okf/frontmatter.ts`,
  `src/okf/index-sync.ts`.
- **Add**: `src/agent/repository-prompts.ts`, `src/agent/wiki-finalizer.ts`,
  `src/agent/wiki-link-validator.ts`, `src/okf/generated-provenance.ts`.
- **Drop**: `src/agent/prompt.ts`, `src/agent/prompts/code.ts` — nothing the port
  reproduces remains in them.
- `notCovered` gains: claims subsystem, resumable generation, mermaid validation,
  integrations installer.

README: fidelity section rewritten for v0.5.0 (naming files as they exist at the
tag — the v0.3.3 spec's correction discipline); OKF section updated to v0.2 with
the `generated` field explained for existing users (additive; nothing deleted;
first run after upgrade stamps changed pages).

## Files touched

| File | Change |
|---|---|
| `commands/wiki.md` | Steps 0–4 reworked per the lifecycle table; Step 3 replaced by planner + workers; critic/QA/diagram sections removed; link-repair note kept `[adapted]`. Authoritative. |
| `.agents/skills/openwiki/SKILL.md` | Mirror, host-adapted vocabulary preserved |
| `scripts/openwiki-finalize.py` | `--snapshot` mode, provenance pass, okf 0.2, `RESERVED` + `INSTRUCTIONS.md`, `--actor` |
| `scripts/test_finalize.py` | New tests per the list below |
| `scripts/extract-upstream-prompt.py` | Rewritten for `repository-prompts.ts` |
| `upstream.lock.json` | v0.5.0, file set above, `notCovered` extended |
| `README.md` | Fidelity + OKF v0.2 + layout |
| `hooks/openwiki-gate.sh` | Expected unchanged; regression-tested |
| `openwiki/` | Migrated by the real update run at integration |

`.opencode/commands/wiki.md` must not change (routing only).

## Testing

**Finalizer unit tests** (`scripts/test_finalize.py`), extending the existing 20:

1. Existing migrate tests still pass (behavior unchanged, moved to `--snapshot`).
2. Root index carries `okf_version: "0.2"`.
3. `INSTRUCTIONS.md` never receives front matter, index entries, or provenance.
4. `--snapshot` writes `.openwiki-run.json` with body hashes excluding front
   matter, and deletes a stale one first.
5. Provenance stamps a changed body with `generated: { by, at }` and removes a
   legacy `timestamp`.
6. A new page (absent from snapshot) is stamped.
7. A front-matter-only change preserves the prior stamp.
8. A tampered stamp on an unchanged body is restored; a stamp on a previously
   unstamped unchanged page is removed.
9. A changed page's terminal line endings are canonicalized to one LF; an
   unchanged page's bytes are untouched.
10. Invalid `generated` mapping is removed by repair; `openwiki_translation_pending`
    survives.
11. Missing snapshot file → all pages stamped (conservative), run exits 0.
12. Default mode deletes `.openwiki-run.json`.
13. **Idempotence:** two consecutive full runs (snapshot + finalize) leave wiki
    files byte-identical.
14. Never-fail: unreadable content and missing directories still exit 0.

**Gate:** no-op scenario with a refreshed `.last-update.json` still skips (only
`.last-update.json` dirty → exit 0).

**Integration:**

15. `sh scripts/check-upstream-drift.sh` exits 0 after `--update` to v0.5.0.
16. A real `/openwiki:wiki update` against this repo migrates the wiki to OKF v0.2:
    pages gain `generated` stamps, root index shows `okf_version: "0.2"`, existing
    prose survives, `.last-update.json` keeps its shape.
17. An immediately following update is a content no-op: no wiki file changes;
    `updatedAt` refreshes; the gate skips.
18. The PR's `Upstream drift` workflow passes, and issue #6 closes.

## Risks

| Risk | Mitigation |
|---|---|
| Prompt assembly via interpolations invites transcription drift | Extractor marks interpolation points; lock hashes the source file |
| Provenance breaks idempotence (stamps advancing on no-op runs) | Dedicated test 13; stamps advance only on body-hash change by construction |
| Crash between Step 2 and 3b leaves `.openwiki-run.json` | Next run's `--snapshot` deletes it first; worst case is one conservative re-stamp |
| Subagent dispatch varies across hosts | Worker prompt is host-agnostic; dispatch is the only `[adapted]` variable; Codex fallback specified |
| Claims omission weakens the fidelity claim | Recorded in `notCovered` with reasoning; README states it plainly |
| This repo's own wiki migrates poorly | Front matter is additive; migrate preserves valid blocks; test 16 exercises the real path |

## Open question

Regenerate this repo's `openwiki/` from scratch under the new init, or let `update`
migrate it? Same answer as the v0.3.3 re-port: let `update` migrate — it is the path
every existing user takes, so it is the path that must work. Revisit if the result
is poor.
