# Re-port openwiki-cc to upstream OpenWiki v0.3.3

**Status:** approved design, not yet implemented
**Date:** 2026-08-24
**Tracking issue:** icampana/openwiki-cc#2

## Problem

`openwiki-cc` reproduces a slice of `langchain-ai/openwiki` as prose inside prompt files. It
reproduces version `0.0.4`. Upstream is at `v0.3.3` — thirteen releases and roughly six weeks
ahead.

This is not a patch. Of the 116 substantive lines in the `0.0.4` system prompt, 29 survive
verbatim in `v0.3.3`. Upstream restructured:

| Upstream file | `0.0.4` | `v0.3.3` | Consequence for this port |
|---|---|---|---|
| `src/agent/prompt.ts` | 17,917 B | 9,129 B | Now assembly only. Prompt bodies moved to `src/agent/prompts/`. |
| `src/agent/prompts/code.ts` | absent | 51,171 B | The real system prompt. Not covered by the port at all. |
| `src/agent/utils.ts` | 9,894 B | 14,768 B | Metadata shape changed; git command construction moved out. |
| `src/agent/index.ts` | 28,887 B | 61,637 B | Run lifecycle grew deterministic post-run passes. |

The drift check added in #1 detects this. This spec closes it.

## Scope

**In:** repository output mode, `init` and `update` commands.

**Out, deliberately:**

- `src/agent/prompts/personal.ts` (74,619 B). Upstream selects it via `outputMode: "local-wiki"`,
  the default, and it produces a personal knowledge wiki. That is a different product from
  "document a repository", which is this port's entire framing. Porting it would roughly double
  the work and dilute what the tool is.
- The `chat` prompt. This port replaced upstream's interactive-chat default with auto-routing
  between `init` and `update`, because a plugin slash command is always namespaced and cannot be a
  bare conversational `/openwiki`. That decision stands.
- Upstream's `--language` flag. There is no slash-command equivalent, so `language` is omitted
  from run metadata rather than faked.

Both omissions are recorded in `upstream.lock.json` so the drift check measures the port against
what it actually claims to cover, not against upstream's whole surface.

## How upstream v0.3.3 differs

### Prompt selection

```
createSystemPrompt(command, outputMode, language, openWikiIgnore)
  outputMode === "repository" ? CODE_SYSTEM_PROMPTS[command]
                              : PERSONAL_SYSTEM_PROMPTS[command]
```

`CODE_SYSTEM_PROMPTS` is keyed `{chat, init, update}`. For any command other than `chat`, the
assembled prompt gets `createLinkIntegrityInstructions()` appended.

The two prompts this port needs are asymmetric, and the port should preserve that rather than
normalize it:

- **`init` (12,770 B)** — lean. Sections: Hard constraints, Init workflow, Documentation contract,
  Metadata and links (OKF), Diagrams, IMPORTANT REMINDER.
- **`update` (25,693 B)** — carries the full discipline catalog. Sections: Canonical wiki location,
  Run discipline, Repository mapping discipline, Planning discipline, Index discipline, Existing
  documentation discipline, Root agent instruction files, Security and privacy rules, Documentation
  goals, Coding-agent utility requirements, OKF relationship modeling, Front matter requirements
  (OKF), Section quality rules, Repository decomposition and coverage, Required documentation
  structure, Coverage self-check, Diagram discipline, Mode-specific behavior.

### Template placeholders

The prompt bodies contain placeholders the harness fills. Four matter here:

| Placeholder | Filled from | Port's answer |
|---|---|---|
| `{GIT_HISTORY_HINT}` | whether `.openwikiignore` is active | Step 1 reads the file and selects the variant |
| `{DISCOVERY_INSTRUCTION}` | same | same |
| `{OPENWIKIIGNORE_INSTRUCTIONS}` | same | same |
| `{OUTPUT_LANGUAGE_INSTRUCTIONS}` | `--language` flag | omitted; see Scope |

When `.openwikiignore` is active, upstream tells the agent git history is unavailable and it must
not bypass the restriction. That is a behavioral change, not a formatting one, so the port must
honor it.

### OKF front matter

The largest output change. Every non-reserved Markdown file under `openwiki/` — including the
temporary `_plan.md` — must begin with YAML front matter following the Google Knowledge Catalog
OKF v0.1 schema:

```yaml
---
type: <Type name>                  # REQUIRED
title: <Optional display name>
description: <Optional one to two sentence summary>
resource: <Optional canonical URI>
tags: [<tag>, <tag>]
timestamp: <Optional ISO 8601 datetime>
# Producer-defined extension fields are allowed.
---
```

Only `type` is required, and type values are not centrally registered. Unknown extension fields are
valid OKF and **must survive round trips** — a consumer's custom fields cannot be dropped on
update.

`index.md` and `log.md` are reserved and must not receive concept front matter.

## Design

### The run lifecycle

Step 3b is the only structural addition. Everything else is a content change.

| Step | Change |
|---|---|
| 0 — pre-run no-op check | unchanged |
| 1 — git evidence | **+** read `.openwikiignore`; select the `GIT_HISTORY_HINT` / `DISCOVERY_INSTRUCTION` variants |
| 2 — snapshot | unchanged |
| 3 — system prompt | **replaced** with the v0.3.3 repository-mode text + link-integrity appendix |
| 3b — finalize | **new**: run `scripts/openwiki-finalize.py` |
| 4 — persist metadata | **+** `status` field |

Upstream performs the Step 3b work in harness code: `src/agent/wiki-link-validator.ts` for links,
and the `src/okf/` module for front matter and indexes. The port has no runtime, so it becomes an
explicit command step — the same
adaptation already used for the Step 2 snapshot, which is upstream's in-process SHA-256 expressed
as a shell one-liner.

### Why the deterministic finalizer resolves a real contradiction

Update mode is explicitly surgical: *do not edit pages the changes did not affect*. OKF is
explicitly total: *every page MUST have front matter*. Existing wikis have none. Taken together
these demand that a surgical update rewrite every page.

Upstream resolves it outside the model:

> OpenWiki repairs front matter deterministically after every run, so a page is never rejected for
> missing or invalid front matter. If a page's front matter contains `openwiki_generated: true`,
> that metadata was code-derived as a fallback: replace it with an accurate `type`, `title`, and
> `description` grounded in the page body, then remove [the flag].

So the model stays surgical, code guarantees validity, and the `openwiki_generated: true` flag is a
work queue — a later run upgrades a machine guess into a real description when it is already
editing that page. The port reproduces this exactly.

### Component: `scripts/openwiki-finalize.py`

Python 3, standard library only. No `pip install`, and no `yaml` import — front matter is parsed
by hand, which is tractable because OKF front matter is a flat key/value block. Python 3 is present
on macOS and on `ubuntu-latest`.

The repo's existing convention is POSIX `sh` + `jq` (`openwiki-gate.sh`,
`check-upstream-drift.sh`). This is where that convention stops paying: YAML round-tripping,
relative-link resolution, and heading-anchor slugging in `sh` would mean `awk` and `sed` doing work
they are bad at.

**Contract**

- Input: the `openwiki/` directory. No arguments beyond an optional path.
- **Always exits 0.** A finalizer that can fail a documentation run is worse than one that skips a
  file. Problems are reported on stdout.
- Writes only inside `openwiki/`.

**Pass 1 — front matter.** For every `.md` file except reserved `index.md` and `log.md`:

- Valid front matter present → leave it alone entirely.
- Missing or unparseable → insert a minimal OKF block: `type: Reference` as the fixed fallback,
  `title` from the H1, `description` from the first non-heading paragraph, and
  `openwiki_generated: true`. Upstream's equivalent is `deriveMinimalFrontmatter` in
  `src/okf/frontmatter.ts`, and the flag name comes from its `OPENWIKI_GENERATED_FIELD`.
- Existing front matter with unknown extension fields → preserve every field verbatim.

**Pass 2 — directory indexes.** Generate `index.md` for each directory under `openwiki/`
deterministically, reproducing `synchronizeWikiIndexes` in `src/okf/index-sync.ts`. The prompt
forbids the model from creating or editing these, so the finalizer owns them outright. Observed
output shape at `v0.3.3`: entries are `- [label](href)`, and only the bundle-root index carries
front matter, exactly `---\nokf_version: "0.1"\n---`.

**Pass 3 — link integrity.** Resolve relative Markdown links and heading anchors within `openwiki/`.
A broken link is **left in place** and annotated with an adjacent HTML comment beginning
`openwiki: broken internal link`, carrying the reason. The prompt instructs a later run to repair
the href or restore the target and then delete the comment. Re-running must not duplicate an
existing comment.

### Idempotence is a correctness requirement, not a nicety

Step 2 snapshots `openwiki/` before the agent runs; Step 4 recomputes and compares. Step 3b sits
between them, so any change the finalizer makes lands inside the compared window.

If the finalizer were not idempotent, a genuine no-op run would still produce a hash difference.
`.last-update.json` would then be rewritten on every run, which would advance `gitHead` with no
documentation change and defeat both the in-agent no-op check and `hooks/openwiki-gate.sh`, whose
entire value is spawning a frontier model only on a real change.

So: **running the finalizer twice must change nothing the second time.** This gets a dedicated
test, and it is the single most important property in this spec.

### Metadata

`.last-update.json` gains `status`, matching upstream's `writeLastUpdateMetadata`:

```json
{
  "updatedAt": "<ISO 8601>",
  "command": "init|update",
  "gitHead": "<git rev-parse HEAD>",
  "model": "<model id>",
  "status": "complete"
}
```

`status` is upstream's `UpdateRunStatus`, defined in `src/agent/types.ts` as
`"complete" | "interrupted"`, defaulting to `"complete"`. Upstream persists metadata even on an
interrupted run so partially-generated content stays diffable by the next update. The port writes
`"complete"` on a normal finish; a run that fails mid-way leaves the previous metadata untouched,
which is the conservative equivalent.

`gitHead` stays unconditional. Upstream makes it conditional on `outputMode === "repository"`, and
this port is always repository mode, so the condition is constant here. `language` is omitted.

`hooks/openwiki-gate.sh` reads `gitHead` and must keep working against both the old four-field file
and the new five-field one. Adding a key does not break its `jq` read, but this needs a test rather
than an assumption.

## Files touched

| File | Change |
|---|---|
| `commands/wiki.md` | Step 1 `.openwikiignore`, Step 3 new prompt, Step 3b, Step 4 `status`. Authoritative. |
| `.agents/skills/openwiki/SKILL.md` | Mirror of the above, host-adapted |
| `scripts/openwiki-finalize.py` | New |
| `scripts/test_finalize.py` | New |
| `upstream.lock.json` | `trackedRef` → `v0.3.3`; **add** `src/okf/frontmatter.ts` and `src/okf/index-sync.ts`, whose behavior Step 3b now reproduces; record deliberate omissions |
| `README.md` | Fidelity section: the port is current again |
| `openwiki/` | Architecture page: Step 3b, OKF, the finalizer |

`.opencode/commands/wiki.md` needs no change. It carries only mode routing and delegates to the
skill — the payoff for having built it that way.

## Testing

Test-first, per the repo's workflow.

**Finalizer unit tests** (`scripts/test_finalize.py`), each against a fixture tree:

1. A page with no front matter gets a valid OKF block stamped `openwiki_generated: true`.
2. A page with valid front matter is left byte-identical.
3. A page with unknown extension fields keeps every one of them.
4. `index.md` and `log.md` never receive concept front matter.
5. A broken relative link is annotated and **not** removed.
6. A valid heading anchor is not flagged; a missing one is.
7. **Idempotence:** two consecutive runs produce byte-identical output.
8. A second run does not duplicate an existing broken-link comment.

**Integration:**

9. `sh hooks/test_gate.sh` still passes against a five-field `.last-update.json`.
10. `sh scripts/check-upstream-drift.sh` exits 0 once the lock moves to `v0.3.3`, and issue #2
    closes on the next scheduled run.
11. A real `/openwiki:wiki update` run against this repo produces an OKF-valid wiki, and an
    immediately following run is a no-op with `.last-update.json` untouched.

Test 11 is the one that matters most: it exercises the snapshot/finalizer interaction that tests
1-8 can only approximate.

## Risks

| Risk | Mitigation |
|---|---|
| Non-idempotent finalizer silently breaks the no-op contract and the shell gate | Dedicated test (7) plus integration test (11) |
| Reproducing ~38 KB of prompt text introduces transcription drift | Extract programmatically from `code.ts` rather than retyping; the drift check then guards it |
| OKF front matter breaks existing wikis for current users | Front matter is additive and the finalizer never deletes content; call it out in the README |
| `type` values are unregistered, so the model may invent inconsistent ones | Upstream accepts this by design; the finalizer's fallback uses one conservative default |

## Open question

Whether to regenerate this repo's own `openwiki/` from scratch under the new prompt, or let the
next surgical `update` migrate it.

Recommendation: let `update` migrate it, so the migration path users will actually take is the one
that gets exercised. This is also what upstream does — `src/okf/index-sync.ts` exports an explicit
`migrateWikiToOkf` alongside `synchronizeWikiIndexes`, so migrating an existing non-OKF wiki in
place is a supported path rather than an afterthought. Revisit if the result is poor.

## Correction to earlier work

The README's fidelity section, added in #1, lists upstream subsystems by names taken from
`main` rather than from the `v0.3.3` tag. At `v0.3.3` there is no `wiki-finalizer.ts`, and the
files are `skeleton_critic.ts` and `wiki_qa_subagents.ts` with underscores. The OKF work lives in
`src/okf/`, not `src/agent/`. Implementation must correct that list — a fidelity claim that
misnames upstream files is the specific failure this port exists to avoid.
