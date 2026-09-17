# An experience layer for openwiki: capturing patterns worth a skill

**Status:** approved design, not yet implemented
**Date:** 2026-09-17
**Branch:** `feature/OW-8`
**Prior art:** WikiSkill (arXiv:2608.27454v1, Google Research, 2026-08-27)
**Touches:** `scripts/openwiki-finalize.py`, `commands/wiki.md`,
`.agents/skills/openwiki/SKILL.md`, `hooks/openwiki-gate.sh`

## Problem

WikiSkill co-evolves agent skills with a persistent wiki, and its ablations report
that the persistent layer, not the skill proposer, is what makes skill evolution
work. The structure it uses is small:

```
wiki/index.md          one line per pattern
wiki/log.md            chronological evolution log
wiki/skill-impact.md   what was tried, and the outcome
wiki/patterns/         one page per pattern, with evidence
```

openwiki-cc has a wiki already, so the structure looks free. It is not. The two
wikis have opposite lifecycles:

| | openwiki `openwiki/` | WikiSkill `wiki/` |
|---|---|---|
| Source of truth | repository code | agent execution traces |
| Lifecycle | regenerated from evidence every run | accumulated across runs |
| Invariant | a no-op run leaves every file byte-identical | every run adds history |

A pattern page inside the planned page set is handed to a Phase 2 worker whose
prompt says to research the repository and write that page. The worker has no
traces to read, so it replaces accumulated observations with code documentation,
and Step 3b restamps the result. One update destroys the section.

## What does not transfer

WikiSkill's accept/reject loop works because it has a reward signal: benchmark
accuracy per iteration, recorded in `skill-impact.md`. openwiki has no score. A
pattern log with no falsifiable signal grows without bound, cannot be ranked, and
gives a distillation pass nothing to prune against.

This design therefore replaces the reward signal with two cheaper constraints:

1. **Every entry carries evidence** — a `file:line`, a command and its observed
   output, or a commit. An entry without evidence is refused, not filed.
2. **Promotion requires recurrence** — a candidate becomes a skill proposal only
   after it appears in two or more independent sessions.

Neither is as strong as a benchmark score. Both are checkable by a later run,
which is the property that matters.

## Scope

**In:** a reserved `openwiki/experience/` subtree; a directory exclusion in the
finalizer; one sentence in the planner prompt; a gate-hook exclusion; two new
skills, `openwiki:observe` (capture) and `openwiki:distill` (promote).

**Out, deliberately:**

- **Automatic capture via hooks.** Capture is an explicit skill invocation. A hook
  that files observations on every session end produces volume, not evidence, and
  there is no reward signal here to prune it back.
- **Cross-repository capture.** `openwiki/experience/` belongs to its repository.
  Working-style observations that hold across repositories are already covered by
  engram's personal scope (`~/.claude/CLAUDE.md` §2.5) and stay there.
- **Writing the skills themselves.** `openwiki:distill` proposes; a person
  approves and writes. The paper's proposer is autonomous because it can measure
  the result. This one cannot.
- **Modifying the Phase 2 worker prompt.** The worker contract ends with `Write
  only ${job.path}. Do not create, edit, or delete another wiki page.` That
  sentence stays intact. Workers report friction in their final message, which is
  already permitted; the orchestrator files it.

## Design

### 1. The reserved subtree

```
openwiki/experience/index.md          one line per candidate: PROBLEM + ROOT CAUSE + FIX
openwiki/experience/candidates/       one page per candidate, with evidence
openwiki/experience/decisions.md      promoted and rejected, rejections kept in full
```

`decisions.md` keeps the full text of rejected proposals. WikiSkill's first rule
is "don't propose what was already tried and rejected," and that rule only works
if the rejected body survives.

The index line format is taken from the paper directly, because its stated purpose
matches this one: the entry has to let a reader judge relevance without opening the
page.

### 2. Finalizer exclusion

Every pass in `scripts/openwiki-finalize.py` reaches the filesystem through
`_iter_dirs` (line 183): `markdown_files` (line 208) feeds `pass_frontmatter`,
`pass_links`, `write_state`, and `pass_provenance`; `_has_real_markdown` (line 253)
and `pass_indexes` (line 310) walk it directly.

Add a module-level `EXCLUDED_DIRS = {"experience"}` and apply it in two places:

- `_iter_dirs`, in the child filter, so the whole subtree is invisible to all four
  passes at once.
- `render_index` (line 288), in the child-directory loop, so the root index does
  not link `experience/index.md`.

`RESERVED` is not the seam. It is matched as `path.name in RESERVED` (line 225),
which filters filenames, not directories.

The finalizer never writes inside the subtree, so `openwiki/experience/index.md`
is authored by `openwiki:observe`, not generated.

### 3. Planner prompt

One sentence, in the Phase 1 planner prompt, alongside the existing exclusion for
generated index pages. It must be added in both ports, which carry the same text:

- `commands/wiki.md` (Claude Code, authoritative)
- `.agents/skills/openwiki/SKILL.md` (Codex, opencode)

### 4. Gate-hook exclusion

`hooks/openwiki-gate.sh:20` computes `dirty` by removing only
`openwiki/.last-update.json`. An uncommitted observation therefore reads as a
source change and spawns a full wiki run on the next Stop hook. Extend the filter
to drop `openwiki/experience/` as well.

### 5. `openwiki:observe`

Appends one candidate. Invoked two ways, with the same entry format:

- **In a session**, by a person who saw something worth keeping.
- **After Step 3b of a wiki run**, by the orchestrator, from the friction the
  Phase 2 workers reported in their final messages: the evidence a worker needed
  that its `seedPaths` did not provide.

Required fields per candidate. A missing evidence field is a refusal:

| Field | Content |
|---|---|
| `trigger` | What was being attempted when this surfaced |
| `evidence` | `file:line`, a command and its observed output, or a commit SHA |
| `friction` | What went wrong, and the root cause |
| `would-have-changed` | What a skill would have done differently |
| `sessions` | Append-only list of run identifiers where this recurred |

### 6. `openwiki:distill`

Reads `candidates/`, groups by recurrence, and proposes a skill only where
`sessions` holds two or more entries. Every proposal is written to `decisions.md`
with its verdict. Single-occurrence candidates stay candidates; they are not
deleted, and they are not proposed.

Before proposing, it reads `decisions.md` first, and does not re-propose anything
already rejected there.

## Invariants and how each is verified

The existing guarantee is that a run changing no page bodies leaves every wiki file
byte-identical. `scripts/test_finalize.py` holds that contract in
`TestIdempotence` (line 562). Each invariant below gets a test that fails before
the change:

| Invariant | Test |
|---|---|
| A populated `experience/` subtree leaves finalizer output byte-identical | Extend `TestIdempotence` |
| No `index.md` is written inside `experience/` | New case in `TestIndexes` |
| The root index does not link `experience/` | New case in `TestIndexes` |
| No front matter is backfilled on an `experience/` page | New case in `TestFrontmatter` |
| No provenance stamp lands on an `experience/` page | New case in `TestProvenancePass` |
| Links inside `experience/` are not annotated | New case in `TestLinks` |
| The gate does not spawn a run for an observation-only diff | `hooks/test_gate.sh` |

`TestShippedCopy` (line 963) already asserts that the shipped finalizer matches
`scripts/`, so the copy in `.agents/skills/openwiki/scripts/` must be updated in
the same change.

## Version

`.claude-plugin/plugin.json` is at `0.5.1`. This adds two skills and changes the
finalizer's file selection, so it ships as `0.6.0`.

## Open question, deferred

Whether `openwiki/experience/` should be committed or ignored. Committing makes
observations shared and reviewable, and is assumed here. Ignoring them would make
the layer per-developer and would remove the gate-hook problem entirely. The
decision does not change any of the code above, so it can be made when the first
real observation exists.
