---
description: Generate/maintain the openwiki/ documentation wiki (init | update)
argument-hint: "[init|update] [extra instruction]"
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Task
---

# /openwiki:wiki — documentation wiki agent

Invoked as `/openwiki:wiki` (auto-route), `/openwiki:wiki init`, or `/openwiki:wiki update`.

Native Claude Code port of OpenWiki (`langchain-ai/openwiki`). You are the agent; this
repository is the target. Follow the routing, run the git evidence collection, then act on
the system prompt below.

## Routing — resolve the mode from `$ARGUMENTS`

- `init` → **init mode**.
- `update` → **update mode**.
- empty → **auto-route**: if `openwiki/` exists → update, else → init. Run:
  `test -d openwiki && echo update || echo init`
- Anything else (e.g. `update Please document the API routes first`) → the first token, if it
  is `init`/`update`, selects the mode; the remaining text is an **additional user instruction**
  appended to the run. If no mode token is present, auto-route and treat all of `$ARGUMENTS` as
  the additional instruction (equivalent to OpenWiki's `[message]`).

## Model tier

OpenWiki's default model is `z-ai/glm-5.2` (OpenRouter), with fallbacks `openai/gpt-5.4-mini`
and `anthropic/claude-sonnet-5`; its provider list includes Claude Opus 4.8 / Sonnet 5 / GPT 5.5
— a **frontier coding model** tier. Run this command **and its subagents on Opus 4.8** (Sonnet 5
minimum) for comparable documentation quality. Do not run it on a small/fast model.

## Step 0 — Pre-run no-op check (update mode with no additional instruction only)

Mirrors OpenWiki `v0.5.0` `getUpdateNoopStatus` / `shouldCheckUpdateNoop`: skip the
model work when nothing relevant changed. Applies **only** in update mode **and only
when `$ARGUMENTS` carried no additional instruction**. If an instruction was given,
skip this step and proceed to Step 1.

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
ls "$CLAUDE_PLUGIN_ROOT/scripts/openwiki-finalize.py" .claude/skills/openwiki/scripts/openwiki-finalize.py .agents/skills/openwiki/scripts/openwiki-finalize.py "$HOME/.claude/skills/openwiki/scripts/openwiki-finalize.py" "$HOME/.agents/skills/openwiki/scripts/openwiki-finalize.py" scripts/openwiki-finalize.py 2>/dev/null | head -1
```

`ls` sorts its operands, so with several layouts present the winner is whichever path sorts
first, not the order listed. Every copy is byte-identical (CI enforces it), so any hit is correct.
Remember the path it prints; Step 3b uses the same one.

If the `ls` prints nothing, the script is genuinely unavailable: say so in your final message
and skip both this step and Step 3b. Do not hand-write front matter, indexes, or
provenance — that is non-deterministic and would break idempotence.

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
> `[adapted]`: (a) DeepAgents' virtual filesystem → native Read/Write/Edit/Glob/Grep/Bash
> on real repo paths; (b) upstream's durable page-job queue → the plan held in
> orchestrator context plus one host subagent per page (sequential writing on hosts
> without a subagent tool); (c) upstream's prepare/finalize harness → Step 2's
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
worker prompt and that page's plan entry — path, title, purpose, seedPaths, relatedPages,
instructions, and (update mode) whether the page already exists. Launch independent pages
together. On hosts without a subagent tool, write the pages yourself one at a time under
the same worker discipline; the prompt text does not change.

## Step 3b — Finalize the wiki (deterministic, run AFTER the wiki work)

Upstream does this in harness code (`src/agent/wiki-finalizer.ts`,
`src/okf/generated-provenance.ts`, `src/okf/frontmatter.ts`, `src/okf/index-sync.ts`,
`src/agent/wiki-link-validator.ts`). Here it is one script, in finalize mode:

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

## Step 4 — Persist metadata (run AFTER the wiki work)

Write `openwiki/.last-update.json` with exactly these fields (shape from OpenWiki
`writeLastUpdateMetadata`; upstream #647: the timestamp always refreshes so freshness
checks reflect the actual last run — a no-op update still means OpenWiki ran):

```json
{
  "updatedAt": "<current UTC time, ISO 8601, e.g. 2026-07-05T12:34:56.000Z>",
  "command": "init|update",
  "gitHead": "<output of git rev-parse HEAD, omit if not a git repo>",
  "model": "<the model you are running as, e.g. claude-opus-4-8>",
  "status": "complete"
}
```

Get `updatedAt` and `gitHead` from Bash (`date -u +%Y-%m-%dT%H:%M:%S.000Z`, `git rev-parse HEAD`)
rather than guessing. Write the file on **every** completed run, including no-ops.

`status` is upstream's `UpdateRunStatus` (`complete` | `interrupted`), from
`src/agent/types.ts`. This port always writes `complete`: it cannot reliably persist
metadata mid-interrupt, so an interrupted run leaves the previous file untouched, and
the next update re-runs on the dirty `openwiki/` tree — the conservative equivalent of
upstream's `interrupted` status.

---

## The user prompt to act on

**init:**
> Initialize OpenWiki documentation for this repository.
>
> Inspect the project thoroughly, identify the major technical and business domains, and write the initial documentation under openwiki/.
>
> Start with openwiki/quickstart.md as the entrypoint. Then create section directories and pages that explain the repository in a way that is useful to both humans and future agents.
>
> Git context: *(the Step 1 block)*

**update:**
> Update the existing OpenWiki documentation for this repository.
>
> Inspect openwiki/, identify recent source changes, and refresh only the documentation pages directly affected by those changes. Use the git evidence below when available. Keep edits surgical: do not rewrite accurate sections, do not update source maps or git evidence just to refresh them, and do not make formatting-only changes. If the wiki is already current, do not edit files. openwiki/.last-update.json is rewritten at the
end of every run, including no-ops (upstream #647).
>
> Last update metadata: *(contents of .last-update.json, or "No previous OpenWiki update metadata was found.")*
>
> Git change summary: *(the Step 1 block)*

If `$ARGUMENTS` carried an additional instruction, append it as:
> Additional user instruction: *(that text)*

---

## Notes — headless / CI permissions (`claude -p`)

To run non-interactively without blocking (OpenWiki's `ShellAllowList` equivalent; the Claude
Code harness enforces permissions, so nothing to re-code), grant a minimal, non-permissive
allowlist. In `.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(git --no-pager status:*)",
      "Bash(git --no-pager rev-parse:*)",
      "Bash(git --no-pager log:*)",
      "Bash(git --no-pager diff:*)",
      "Bash(git --no-pager show:*)",
      "Bash(git --no-pager blame:*)",
      "Bash(python3:*)",
      "Bash(ls:*)",
      "Bash(find:*)",
      "Bash(sha256sum:*)",
      "Bash(rg:*)",
      "Bash(date:*)",
      "Edit(openwiki/**)",
      "Write(openwiki/**)"
    ]
  }
}
```

`Bash(python3:*)` is broader than the path-pinned entry it replaces, and the trade-off is
deliberate: under a plugin or skill install the finalizer sits at an absolute, version-dependent
path that no static prefix can match, so pinning the path blocks Step 3b rather than permitting
it. Be aware of what you are granting — `python3` can write anywhere, so this entry is wider than
the `Edit`/`Write` scoping below it. If that matters more to you than Step 3b, drop both
`Bash(python3:*)` and `Bash(ls:*)`; the run then reports the finalizer as unavailable and skips
the step instead of failing. `Bash(ls:*)` on its own only covers the lookup.

`AGENTS.md` / `CLAUDE.md` are deliberately absent from this allowlist: the worker prompt above
already forbids writing them during normal runs, and granting the permission anyway would leave
enforcement resting solely on the model obeying its own prompt, with no technical backstop. Do
not add them back.

Read/Glob/Grep over the repo are already read-only; the system prompt forbids reading `.env`
and secrets. Git commands here are all read-only. Keep Write/Edit scoped to `openwiki/**` only.
