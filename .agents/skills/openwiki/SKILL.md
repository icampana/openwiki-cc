---
name: openwiki
description: Generate or maintain an openwiki/ documentation wiki for this repository. Use when asked to initialize, build, update, or refresh the repo's OpenWiki docs. Auto-detects init (no openwiki/ yet) vs update (openwiki/ exists). Port of langchain-ai/openwiki for shell-based agent hosts.
license: MIT
---

# openwiki — documentation wiki agent

Port of OpenWiki (`langchain-ai/openwiki`) for shell-based agent hosts — **Codex** and
**opencode** both load this file. You are the agent; this repository is the target. Resolve the
mode, collect git evidence, then act on the system prompt below.

Where the two hosts differ, this file says so inline. The only real difference is subagents:
opencode has a Task tool, Codex does not. Everything else — the system prompt, the git commands,
the `.last-update.json` shape, the idempotence logic — is identical across hosts, and identical
to the Claude Code port in `commands/wiki.md`, which stays authoritative when they disagree.

## Mode resolution

- The user explicitly asks to **initialize / build from scratch** → **init mode**.
- The user explicitly asks to **update / refresh** → **update mode**.
- Otherwise **auto-detect**: run `test -d openwiki && echo update || echo init`. If `openwiki/`
  exists → update, else → init.
- Any extra instruction the user gives (e.g. "document the API routes first") is an additional
  instruction appended to the run.

## Model tier

OpenWiki assumes a frontier coding model (its default is `z-ai/glm-5.2`; its provider list
includes GPT 5.5, Claude Opus 4.8, Sonnet 5). Run this on your host's strongest model with high
reasoning effort — on Codex, something like `gpt-5.5`; on opencode, a frontier model set for the
`build` agent. Do not run it on a small/fast tier — documentation quality depends on it.

## Context management — plan centrally, write in bounded contexts

Each Phase 2 page worker runs in its own bounded context with only its brief (path,
title, purpose, seedPaths, relatedPages, instructions) plus the repository evidence it
inspects — never the whole repo in one context. The orchestrator holds only the Phase 1
plan and the Git change summary. Explore with targeted `grep`/`rg` and short reads;
document incrementally so progress survives compaction.

## Step 0 — Pre-run no-op check (update mode with no additional instruction only)

Mirrors OpenWiki `v0.5.0` `getUpdateNoopStatus` / `shouldCheckUpdateNoop`: skip the
model work when nothing relevant changed. Applies **only** in update mode **and only
when the user gave no additional instruction**. If an instruction was given, skip this step and
proceed to Step 1.

Read `openwiki/.last-update.json`. If it has no `gitHead`, skip this check → go to Step 1.
Otherwise run:

```bash
git --no-pager rev-parse HEAD
git --no-pager status --short --untracked-files=all
git --no-pager diff --name-only <gitHead>..HEAD   # only if HEAD != gitHead
```

Skip the model work when **all** hold:
- `status --short` is empty after removing any line whose path is `openwiki/.last-update.json`;
- HEAD == `gitHead`, **or** every path in `<gitHead>..HEAD` is under `openwiki/`.

If skipped: refresh the run timestamp so freshness checks reflect the actual last run
(upstream #647 — a no-op update still means OpenWiki ran), report "wiki already current —
no repository changes since `<gitHead>`", and stop. Refresh with:

```bash
python3 - <<'PY'
import datetime, json
p = "openwiki/.last-update.json"
d = json.load(open(p))
d["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
with open(p, "w", encoding="utf-8") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
PY
```
(`python3` is used instead of `jq` so the refresh works under the headless allowlist, which grants `Bash(python3:*)` but no `jq`.)

## Step 1 — Collect update context (run BEFORE any write)

Read `openwiki/.last-update.json` if it exists: `gitHead`, `updatedAt`, `status`.

Run these exact commands (all git invocations use `--no-pager`; git is read-only here):

```bash
git --no-pager rev-parse HEAD
git --no-pager status --short --untracked-files=all
git --no-pager diff --name-only <gitHead>..HEAD   # update with a gitHead only
```

On **update** with a `gitHead`, also run `git --no-pager log <gitHead>..HEAD
--max-count=50 --oneline --name-status` for orientation. The changed-paths list is the
planner's update window: only pages whose systems intersect those paths (plus
navigation and cross-page consistency) need work. On **init**, or update without prior
metadata, run `git --no-pager log --max-count=20 --name-status --oneline` for
orientation instead. History is a discipline, not a prescribed block: read it when it
helps establish context; skip it when it does not.

If this is not a git repository, degrade gracefully: use filesystem timestamps, source
inspection, and existing docs to infer what changed.

Keep the assembled output as the **Git change summary** referenced by the planner in
Step 3.

Then check for an ignore file, which changes three prompt instructions and the git posture:

```bash
test -f .openwikiignore && echo active || echo absent
```

- **absent** → use these variants in the Step 3 prompt:
  - `{GIT_HISTORY_HINT}` → `Read git history when it helps establish repository context or explain why code exists. `
  - `{DISCOVERY_INSTRUCTION}` → `- Do not call glob with **/* from the root. Use targeted discovery by directory and extension. Prefer shell commands like rg --files with excludes for .git, node_modules, dist, build, cache directories, and existing generated wiki output.`
  - `{OPENWIKIIGNORE_INSTRUCTIONS}` → empty (upstream `formatOpenWikiIgnoreInstructions` returns just a newline when there is no ignore file).
- **active** → read it, treat every pattern as off-limits for reading, and use:
  - `{GIT_HISTORY_HINT}` → `Git history is unavailable while .openwikiignore is active; rely on allowed source files and tests without bypassing the restriction. `
  - `{DISCOVERY_INSTRUCTION}` → `- Do not call glob with **/* from the root. Use targeted ls, glob, and grep by directory and extension, skipping .git, node_modules, dist, build, cache directories, and existing generated wiki output.`
  - `{OPENWIKIIGNORE_INSTRUCTIONS}` → from upstream `formatOpenWikiIgnoreInstructions`, with the active patterns listed one per line, each indented two spaces and JSON-quoted:
    ```
    .openwikiignore discipline:
    - This repository has .openwikiignore rules. Treat matching paths as out of scope.
    - Filesystem tools enforce these rules; if a tool reports an excluded path, do not retry through shell execute.
    - For repository discovery use ls, read_file, glob, and grep; these keep exclusions enforced. Shell execute is limited to a few maintenance commands while .openwikiignore is active, so do not use it to read files or reconstruct git history.
    - Do not document excluded paths or infer details about their contents.
    - Active patterns:
      "<pattern>"
      "<pattern>"
    ```

`{OUTPUT_LANGUAGE_INSTRUCTIONS}` is always empty: this port has no `--language` flag.

## Step 2 — Prepare the wiki (migrate + provenance snapshot, run BEFORE the wiki work)

First locate the finalizer. It ships with every install layout, but not always at the same
path, and the working directory is the *target* repository rather than the install:

```bash
for c in "${CLAUDE_PLUGIN_ROOT:-}/scripts/openwiki-finalize.py" \
         scripts/openwiki-finalize.py \
         .claude/skills/openwiki/scripts/openwiki-finalize.py \
         .agents/skills/openwiki/scripts/openwiki-finalize.py \
         "$HOME/.claude/skills/openwiki/scripts/openwiki-finalize.py" \
         "$HOME/.agents/skills/openwiki/scripts/openwiki-finalize.py"; do
  [ -f "$c" ] && python3 "$c" --help 2>/dev/null | grep -q -- --snapshot && { echo "$c"; break; }
done
```

The loop probes each candidate for the `--snapshot` flag this step needs, then stops at the
first one that has it. That matters because a machine can hold several installs at different
versions — a hand-copied `~/.agents` skill next to the plugin, say — and only a probe tells
them apart. Do not substitute `ls`: it sorts its operands, the `eza` alias many users install
does not, so the winner would vary by shell. Order matters: a plugin install wins when
`CLAUDE_PLUGIN_ROOT` is set, since that is the copy the host manages; otherwise a repository's
own `scripts/` is preferred over a hand-installed global skill, which is the copy most likely to
have gone stale. Remember the path it prints; Step 3b uses the same one.

If the loop prints nothing, no usable finalizer is installed — either none is present, or every
copy predates provenance. Say so in your final message, name the paths you probed, and skip
both this step and Step 3b. Do not hand-write front matter, indexes, or provenance — that is
non-deterministic and would break idempotence. Do not fall back to a copy that rejects
`--snapshot`: collapsing Steps 2 and 3b into one call drops provenance stamping silently.

Run prepare mode:

```bash
python3 <the path from the previous command> --snapshot openwiki
```

It backfills OKF front matter on pages missing it (tagging its guesses
`openwiki_generated: true` for a later run to upgrade) and writes
`.openwiki-run.json` at the repository root: each concept page's body hash plus its
prior `generated` event. The state file lives outside `openwiki/`, is consumed and
deleted by Step 3b, and is overwritten by the next run — a crash between the steps is
self-correcting.

## Step 3 — System prompt (act as this agent)

> Reproduced from OpenWiki `v0.5.0` `src/agent/repository-prompts.ts` —
> `createRepositoryPlannerPrompt` and `createRepositoryPagePrompt`. Extracted with
> `scripts/extract-upstream-prompt.py`, not retyped. Harness adaptations are marked
> `[adapted]`: (a) DeepAgents' virtual filesystem → your host's shell and file-editing tools (`ls`,
> `rg`/`grep`, reading files, and whatever your host writes with — `apply_patch` on Codex, the
> edit/write tools on opencode) on real repo paths; (b) upstream's durable page-job queue → the plan held in
> orchestrator context plus one host subagent per page (opencode's Task tool; sequential writing on Codex,
> which has no subagent tool); (c) upstream's prepare/finalize harness → Step 2's
> `--snapshot` and Step 3b; (d) the claims subsystem is out of scope — workers write
> pages directly, with no submission tool, inspection tool, or claims guidance.

### Phase 1 — Planning

You are planning an OpenWiki code wiki for this repository.

**[adapted]** Your only output for this phase is the plan itself, held in orchestrator context: a structured page list where every page has a path, title, purpose, seedPaths, relatedPages, and instructions. Do not write documentation pages in this phase.

Design the smallest complete repository-specific information architecture that
helps a coding agent understand and safely change the system. Organize around
owned systems, runtime domains, and cross-system workflows rather than mirroring
the source tree. Use hierarchical paths for meaningful groups such as
/openwiki/architecture/, /openwiki/concepts/, /openwiki/workflows/,
/openwiki/operations/, /openwiki/integrations/, and /openwiki/testing/ when the
repository has enough coverage to warrant them. Do not emit a flat dump of
unrelated top-level pages. Include /openwiki/quickstart.md for init.

Explore before submitting the plan. First map manifests, major directories,
entrypoints, and public surfaces. Then trace representative end-to-end control
and data flows across callers, state/persistence, failure handling, configuration,
operations, and integrations. Finally inspect focused tests and neighboring
implementations to verify boundaries, invariants, and non-obvious connections.
Do not stop at directory names or one representative file. Explore only until
the major systems, behaviors, and relationships are supported by repository
evidence; avoid exhaustive file-by-file inventory. Page paths are final once
submitted.

Populate relatedPages with the most useful conceptual and workflow neighbors so
the resulting wiki is navigable across system boundaries. The quickstart must
route readers through the hierarchy; generated index pages will provide folder
navigation and must not be included in the plan.

Init MUST include /openwiki/quickstart.md. Update MUST NOT delete quickstart. If
an update adds, deletes, moves, or materially regroups documentation pages,
include /openwiki/quickstart.md in the plan so its task-routing map is refreshed.
An update with no required page edits and no deletions may submit pages: [].

For every page provide a concise purpose and useful seedPaths. seedPaths are
starting points, not research boundaries. Copy only relevant global constraints
from the user/connector context into that page's instructions array; do not copy
unrelated context into every job.

**[adapted]** `${semanticContext}` is the additional user instruction from `$ARGUMENTS`, if any (else empty).

**[adapted]** (update mode only) `${updateContext}` is the changed paths from Step 1, introduced as `Changed paths since <gitHead>:` followed by the `git diff --name-only` list. No per-page baselines, no claims issues: a one-session port has a single update window, and claims are out of scope.

**[adapted]** The `${view.wikiGoal ? ... : ""}` interpolation resolves to empty: there is no repository-instructions equivalent.

Mode: use the mode resolved in Routing above. **[adapted]** (upstream #699) When the mode is `init` but `openwiki/` already exists, treat it as fresh generation — existing pages are not preserved; workers rewrite everything.

### Phase 2 — Page workers

For each planned page, brief one worker by filling the `${...}` slots below from that page's Phase 1 plan entry (path, title, purpose, seedPaths, relatedPages, instructions, and whether the page already exists):

You own exactly ${job.path}.

Title: ${job.title}
Purpose: ${job.purpose}
Mode: ${job.mode}
Existing page: ${job.existing ? "yes" : "no"}
Seed source paths:
${formatList(job.seedPaths)}
Related pages:
${formatList(job.relatedPages)}
Page-specific global instructions:
${formatList(job.instructions)}

${job.mode === "update" ? "Read the current page first. Preserve accurate unaffected content; change only what current repository evidence requires.
" : ""}
Keep code identifiers, file paths, commands, URLs, API names, and code blocks unchanged when translation would reduce technical accuracy.

**[adapted]** No `--language` equivalent: the `Output language: ${language}` line and the `Write wiki prose and human-readable frontmatter values in ${language}.` instruction are dropped; the sentence above is kept verbatim.

The page MUST begin with valid OKF concept frontmatter:
---
type: <short descriptive concept type>
title: <human-readable page title>
description: <one or two sentence retrieval-oriented summary>
tags: [<stable English tag>, ...]
---
Do not author generated, verified, sources, timestamp, or OpenWiki control fields; OpenWiki owns those. On update preserve unknown producer-defined frontmatter fields unless they are factually wrong.

Research deeply enough to explain the important responsibilities, entrypoints,
mechanisms/control flow, relationships, state/lifecycle, invariants/failures,
extension points, configuration/operations, and focused tests that actually
matter for this topic. Follow evidence beyond seed paths through callers,
callees, state owners, integration boundaries, and representative tests when
required. Do not turn the page into a source-file inventory.

Write only ${job.path}. Do not create, edit, or delete another wiki page.

**[adapted]** After writing the page, you are done: the page file is the deliverable. There is no submission tool.

${
  job.path === "/openwiki/quickstart.md"
    ? `The complete planned page map is:
${JSON.stringify(
        allPages.map(({ path, title, purpose }) => ({ path, title, purpose })),
        null,
        2,
      )}
Use it to produce a compact task-routing map and link to the major domains.`
    : ""
}

**[adapted]** If you find an HTML comment starting with "openwiki: broken internal link", repair the href or restore the target page using the reason in the comment, then delete the comment. (Upstream v0.5.0 repository prompts no longer carry this; the port's Step 3b creates the annotations, so the port owns the repair loop.)
**[adapted]** Do not read secrets (.env, keys, credentials) and do not create or edit agent instruction files (AGENTS.md, CLAUDE.md) during the run. (Retained from the v0.3.3 port; the v0.5.0 repository prompts carry no security section and upstream enforces this in harness tooling the port does not have.)

Dispatch: for every page in the Phase 1 plan, launch one subagent briefed with the Phase 2
worker prompt and that page's plan entry. **Opencode/Claude Code only — Codex has no
subagent tool; skip subagent dispatch there and write the pages yourself one at a time
under the same worker discipline.** The prompt text does not change either way.

## Step 3b — Finalize the wiki (deterministic, run AFTER the wiki work)

Upstream does this in harness code (`src/agent/wiki-finalizer.ts`,
`src/okf/generated-provenance.ts`, `src/okf/frontmatter.ts`, `src/okf/index-sync.ts`,
`src/agent/wiki-link-validator.ts`). Here it is one script, in finalize mode — use the
Step 2 path:

```bash
python3 <the Step 2 path> openwiki --actor <the model you are running as>
```

It regenerates every directory `index.md` (the root carries `okf_version: "0.2"`),
annotates broken internal links, then reconciles generated provenance: pages whose
body changed this run are stamped `generated: { by: <actor>, at: <now> }` and lose any
legacy `timestamp`; unchanged pages keep (or are restored to) their prior stamp. It
deletes `.openwiki-run.json`, always exits 0, and never deletes content.

Run it AFTER the wiki work. It is idempotent: a run that changes no page bodies leaves
every wiki file byte-identical (only `.last-update.json` refreshes in Step 4, by
design — see below).

If the Step 2 lookup printed nothing, the script is genuinely unavailable: say so in your final
message and skip this step. Do not hand-write front matter, indexes, or
provenance — that is non-deterministic and would break idempotence.

## Step 4 — Persist metadata (run AFTER the wiki work)

Write `openwiki/.last-update.json` with exactly these fields (shape from OpenWiki
`writeLastUpdateMetadata`; upstream #647: the timestamp always refreshes so freshness
checks reflect the actual last run — a no-op update still means OpenWiki ran):

```json
{
  "updatedAt": "<current UTC time, ISO 8601, e.g. 2026-07-05T12:34:56.000Z>",
  "command": "init|update",
  "gitHead": "<output of git rev-parse HEAD, omit if not a git repo>",
  "model": "<the model you are running as>",
  "status": "complete"
}
```

Get `updatedAt` and `gitHead` from the shell (`date -u +%Y-%m-%dT%H:%M:%S.000Z`,
`git rev-parse HEAD`) rather than guessing. Write the file on **every** completed run,
including no-ops.

`status` is upstream's `UpdateRunStatus` (`complete` | `interrupted`), from
`src/agent/types.ts`. This port always writes `complete`: it cannot reliably persist
metadata mid-interrupt, so an interrupted run leaves the previous file untouched, and
the next update re-runs on the dirty `openwiki/` tree — the conservative equivalent of
upstream's `interrupted` status.

## The user prompt to act on

**init:**
> Initialize OpenWiki documentation for this repository. Inspect the project thoroughly, identify the major technical and business domains, and write the initial documentation under openwiki/. Start with openwiki/quickstart.md as the entrypoint, then create section directories and pages that explain the repository in a way that is useful to both humans and future agents.
> Git context: *(the Step 1 block)*

**update:**
> Update the existing OpenWiki documentation for this repository. Inspect openwiki/, identify recent source changes, and refresh only the documentation pages directly affected by those changes. Use the git evidence below when available. Keep edits surgical: do not rewrite accurate sections, do not update source maps or git evidence just to refresh them, and do not make formatting-only changes. If the wiki is already current, do not edit files. openwiki/.last-update.json is rewritten at the end of every run, including no-ops (upstream #647).
> Last update metadata: *(contents of .last-update.json, or "No previous OpenWiki update metadata was found.")*
> Git change summary: *(the Step 1 block)*

Append any extra user instruction as `Additional user instruction: <text>`.

## Headless / CI

To run non-interactively, give Codex a workspace-write sandbox and non-interactive approvals so
it can run the read-only git commands and write under openwiki/, e.g.:

```bash
codex exec --sandbox workspace-write --ask-for-approval never "run the openwiki skill: update the docs"
```

The skill only needs: read-only git (`status`/`log`/`diff`/`rev-parse`/`show`/`blame`),
`find`/`sha256sum`/`rg` for discovery and the snapshot, and writes limited to `openwiki/` —
`AGENTS.md` / `CLAUDE.md` are deliberately excluded, since the worker prompt above already forbids
writing them during normal runs.
