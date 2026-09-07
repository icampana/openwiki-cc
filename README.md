# openwiki-cc

A native **Claude Code**, **OpenAI Codex**, and **opencode** port of
[OpenWiki](https://github.com/langchain-ai/openwiki) — an agent that generates and maintains a
documentation wiki (`openwiki/`) for any repository.

OpenWiki ships as a standalone CLI on its own harness (DeepAgents/LangGraph, a local shell
backend, a SQLite checkpointer, provider adapters, an Ink TUI). Coding agents already provide all
of that plumbing. This repo extracts **the agent itself** — the documentation system prompt, the
git-evidence collection, the wiki structure, and the idempotence logic — and re-expresses it
natively for each host:

- **Claude Code** — a slash-command plugin: `commands/wiki.md` → `/openwiki:wiki`.
- **Codex** — a skill: `.agents/skills/openwiki/SKILL.md` → `$openwiki`.
- **opencode** — the *same* skill file, which opencode also discovers, plus a thin
  `.opencode/commands/wiki.md` that adds `init`/`update` slash arguments → `/wiki`.
- **anything else** — the same skill via [skills](https://github.com/vercel-labs/skills):
  `npx skills add icampana/openwiki-cc`.

The system prompt and the exact git commands are reproduced **verbatim from OpenWiki's real
source**, not from memory.

## What it does

Point it at a repository and it writes human- and agent-friendly Markdown documentation under
`openwiki/`, with `quickstart.md` as the entrypoint and thematic section pages (architecture,
workflows, operations, …). It grounds every claim in source files, existing docs, and git
history — and on later runs it updates only what actually changed.

## Install — any agent, one command

This repo doubles as a [skills](https://github.com/vercel-labs/skills) source: the CLI discovers
`.agents/skills/openwiki/SKILL.md` on its own and can install it into **opencode**, **Claude Code**,
**Codex**, **Cursor**, and 70+ more agents:

```bash
npx skills add icampana/openwiki-cc                          # interactive: pick agents + scope

# scripted examples
npx skills add icampana/openwiki-cc -g -a opencode           # global, opencode only
npx skills add icampana/openwiki-cc -g -a opencode claude-code codex   # several at once
```

The skill always materializes under `.agents/skills/` — project scope or global
(`~/.agents/skills/`) — and other agents are symlinked to that one copy. Restart hosts afterward.
Later: `npx skills update openwiki` pulls new versions; `npx skills list` shows what is installed
where.

**The install is complete out of the box.** The deterministic Step 3b finalizer
(`openwiki-finalize.py` — OKF front matter backfill, index generation, broken-link repair) ships
inside the skill folder, so every install path gets full behavior with no extra steps. Installed
before the script shipped? `npx skills update openwiki` refreshes your copy.

Invocation is unchanged from the host sections below: `$openwiki`, or ask for "init / update the
openwiki docs". On opencode you can additionally install the `/wiki` command for real slash
arguments.

## Install — Claude Code

### Via the plugin marketplace (recommended)

Inside Claude Code:

```
/plugin marketplace add icampana/openwiki-cc
/plugin install openwiki@openwiki-cc
```

Then `/openwiki:wiki` is available in every project. `/plugin marketplace update openwiki-cc`
pulls new versions.

> Plugin commands are always namespaced `plugin:command`, so the command is `/openwiki:wiki`
> (never a bare `/openwiki`). Install manually instead if you want a bare `/wiki`.

> Pick **one** install path. The marketplace plugin and `npx skills add` both work on Claude Code,
> but together they expose the same prompt twice — `/openwiki:wiki` and an `openwiki` skill — and
> the two can be at different versions.

### Manual (no marketplace)

Copy the single command file into the repo you want to document, or globally. Under
`.claude/commands/` the name is bare — the file becomes `/wiki`:

```bash
# per-project → /wiki
mkdir -p your-repo/.claude/commands
cp commands/wiki.md your-repo/.claude/commands/

# or global → /wiki everywhere
cp commands/wiki.md ~/.claude/commands/
```

The command looks for the Step 3b finalizer next to a known install root, so copy it too —
otherwise the run completes but skips front matter, indexes, and link checks:

```bash
mkdir -p ~/.claude/skills/openwiki/scripts
cp scripts/openwiki-finalize.py ~/.claude/skills/openwiki/scripts/
```

## Install — Codex and opencode

Both hosts read skills from `~/.agents/skills/`, so one copy serves both:

```bash
# global → available in every repo, on both hosts
mkdir -p ~/.agents/skills
cp -r .agents/skills/openwiki ~/.agents/skills/
```

Restart the host afterward. To scope it to a single repo instead, copy the same folder to
`your-repo/.agents/skills/`.

**Codex.** Invoke it with `$openwiki` (or via the `/skills` menu); Codex may also trigger it
implicitly when you ask to "initialize / update the openwiki docs".

> Codex skills don't take slash arguments, so the mode comes from your phrasing (or the
> `openwiki/` auto-detect) rather than an `init`/`update` token. Codex custom prompts
> (`~/.codex/prompts/`) are deprecated, so this ships as a skill.

**opencode.** The skill alone works — ask to "update the openwiki docs" and opencode loads it via
the native `skill` tool. For a real `/wiki` that takes `init` and `update` as arguments, also
install the command:

```bash
# global → /wiki in every repo
mkdir -p ~/.config/opencode/commands
cp .opencode/commands/wiki.md ~/.config/opencode/commands/

# or per-repo
mkdir -p your-repo/.opencode/commands
cp .opencode/commands/wiki.md your-repo/.opencode/commands/
```

The command holds no agent logic — it resolves the mode from `$ARGUMENTS` and hands off to the
skill, so there is no third copy of the system prompt to keep in sync.

> opencode has a **Task tool**, so it runs the parallel read-only subagents that Codex cannot.
> The skill marks that section opencode-only; everything else is identical on both hosts.

## Usage

**Claude Code** — from the root of the target repository. Installed as a plugin the command is
`/openwiki:wiki`; copied under `.claude/commands/` it is `/wiki`. Both take the same arguments:

| Command | Behavior |
|---|---|
| `/openwiki:wiki` | **Auto-route**: if `openwiki/` exists → update, else → init. |
| `/openwiki:wiki init` | Build the wiki from scratch. |
| `/openwiki:wiki update` | Surgically refresh only the pages affected by recent changes. |
| `/openwiki:wiki update <instruction>` | Same, plus an extra instruction (e.g. `document the API routes first`). |

> **Note** — auto-route replaces upstream OpenWiki's interactive-chat default (a slash command
> can't be a bare `/openwiki` anyway; plugin commands are always namespaced). `init` and `update`
> remain explicit.

**opencode** — with the command installed, `/wiki`, `/wiki init`, `/wiki update`, and
`/wiki update <instruction>` behave exactly like the Claude Code table above. Without it, invoke
the skill by asking to "update the openwiki docs".

**Codex** — invoke `$openwiki` (or ask to "update the openwiki docs"). It auto-detects init
(no `openwiki/`) vs update (`openwiki/` exists); say "initialize" or "update" to force a mode,
and add any extra instruction in the same request.

## How it works

**Git evidence (before any write).** The command reads `openwiki/.last-update.json`, then runs
the exact upstream commands (all `git --no-pager`, all read-only):

- always: `git status --short`, `git rev-parse HEAD`, `git diff --name-status HEAD`
- init / no prior metadata: `git log --max-count=20 --name-status --oneline`
- update with a recorded head: `git log <gitHead>..HEAD --name-status --oneline`
- update with only a timestamp: `git log --since <updatedAt> --name-status --oneline`

If the target is not a git repo, it degrades gracefully to timestamps and source inspection.

**Parallel exploration.** For large repos, the agent fans out read-only subagents (Task tool),
each in its own context window, each with a narrow brief (existing docs, runtime architecture,
data/storage, API surface, integrations, tests, business workflows). Subagents only inspect and
summarize; the main thread synthesizes and does **all** writes. This is the substitute for
OpenWiki's context compaction — the main thread never loads the whole repo.

**Wiki structure.** `openwiki/quickstart.md` is the required entrypoint (overview + links to
every section). Section directories are created one per major domain, ≤ 8 pages on init, no stub
pages. Small repos get quickstart plus 1–2 pages.

**Wiring — this changed in `v0.3.3`.** Earlier upstream (and earlier versions of this port) added
a short `## OpenWiki` reference section to top-level `AGENTS.md` / `CLAUDE.md`, creating
`AGENTS.md` if neither existed. Upstream reversed that: the `update` prompt now states "Do not
create or update repository `/AGENTS.md` or `/CLAUDE.md` files during normal code wiki runs" —
everything stays under `/openwiki`, and an existing `AGENTS.md` / `CLAUDE.md` is left untouched.
If you're upgrading from an earlier version, do not expect your `AGENTS.md` to keep getting
updated; it won't. Upstream also adds `/openwiki/INSTRUCTIONS.md`, a user-authored brief the
agent reads for scope and priorities but never writes to during a normal run.

**Idempotence.** The agent snapshots `openwiki/` content (excluding `.last-update.json`) with a
SHA-256 hash before and after the run:

```bash
find openwiki -type f -not -name .last-update.json -print0 | sort -z | xargs -0 sha256sum | sha256sum
```

If nothing changed → **no-op** (the wiki is already current). If it changed → write
`openwiki/.last-update.json`:

```json
{ "updatedAt": "<ISO>", "command": "init|update", "gitHead": "<HEAD>", "model": "<model>" }
```

`gitHead` is what the next `update` run diffs against — this file replaces OpenWiki's SQLite
checkpointer (durable crash-resume is intentionally dropped).

## Model tier

OpenWiki assumes a frontier coding model (default `z-ai/glm-5.2`, fallbacks
`openai/gpt-5.4-mini` / `anthropic/claude-sonnet-5`, provider list including Claude Opus 4.8 /
Sonnet 5 / GPT 5.5). **Run this command and its subagents on Opus 4.8** (Sonnet 5 minimum) for
comparable documentation quality.

## Headless / CI (`claude -p`)

To run non-interactively without permission prompts, grant a minimal allowlist in
`.claude/settings.json` (read-only git + snapshot tooling, writes scoped to `openwiki/` only —
`AGENTS.md` / `CLAUDE.md` are deliberately absent from the allowlist, matching the `v0.3.3`
prompt's own "do not write these" instruction). The full snippet is documented inside
[`commands/wiki.md`](commands/wiki.md).

## Auto-run as a hook (keep the wiki fresh)

Claude Code hooks can invoke the command headlessly so the wiki refreshes itself after you work.
The naive hook spawns a full `claude -p` run every turn — even when nothing changed, it still
spins up a frontier model just for the in-agent no-op to discover there's no work. The token cost
is paid *before* the check runs.

Use [`hooks/openwiki-gate.sh`](hooks/openwiki-gate.sh) instead: it reproduces the wiki's Step 0
no-op check ([`getUpdateNoopStatus`](commands/wiki.md)) in pure shell — a few `git` commands, zero
tokens — and spawns `claude` **only** when source actually changed since the last run. `Stop` can
then fire every turn for near-zero cost; the frontier model starts only on a real change.

Copy the script under `.claude/hooks/` and wire it in `.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh .claude/hooks/openwiki-gate.sh"
          }
        ]
      }
    ]
  }
}
```

- **Shell-level gate.** The gate skips the spawn when HEAD is unchanged and the working tree is
  clean (ignoring `openwiki/.last-update.json`), or when every commit since the recorded `gitHead`
  touches only `openwiki/`. Same condition as the in-agent no-op, minus the token cost.
- **`OPENWIKI_HOOK` guard (built in).** The headless run fires its own `Stop` hook; the gate exports
  `OPENWIKI_HOOK=1` and exits early when it sees it. Without it → infinite recursion.
- **Backgrounded + `setsid`.** The gate detaches the run so it doesn't block your session and
  survives a closing session — safe for both `Stop` and `SessionEnd`.
- Edit the script's `/openwiki:wiki update` to bare `/wiki` if you installed manually under
  `.claude/commands/`.
- **`Stop` vs `SessionEnd`.** With the gate, `Stop` (every turn) is cheap and gives continuously-live
  docs; `SessionEnd` (once per session) still works if you'd rather batch. Either way you only pay
  the frontier model on a real change.
- Self-check: `sh hooks/test_gate.sh` (stubs `claude`; exercises every skip/run branch).

## Fidelity to upstream

**Tracked against upstream `v0.5.0`, repository output mode.** The reproduced surface is the
repository planner and per-page worker prompts (`src/agent/repository-prompts.ts`), the run
lifecycle (`src/agent/index.ts`), the prepare/finalize harness (`src/agent/wiki-finalizer.ts`),
link validation (`src/agent/wiki-link-validator.ts`), the git evidence, no-op, and metadata
logic (`src/agent/utils.ts`), and the OKF machinery (`src/okf/frontmatter.ts`,
`src/okf/index-sync.ts`, `src/okf/generated-provenance.ts`). Prompt text is extracted from
upstream with [`scripts/extract-upstream-prompt.py`](scripts/extract-upstream-prompt.py)
rather than retyped, so transcription drift is not possible.

**Deliberately not covered**, and recorded as such in
[`upstream.lock.json`](upstream.lock.json):

| Upstream surface | Why not |
|---|---|
| `src/agent/prompts/personal.ts` | The personal-wiki output mode (`outputMode: "local-wiki"`). A different product from documenting a repository. |
| `CODE_SYSTEM_PROMPTS.chat` | Interactive chat. This port auto-routes between `init` and `update`; a plugin slash command is always namespaced. |
| `--language` | No slash-command equivalent, so `language` is omitted from run metadata rather than faked. |
| `translation-middleware.ts`, `skills.ts`, `crash-guard.ts`, `vertex-surface.ts`, `openai-chatgpt-oauth.ts` | Harness plumbing the host already provides. |
| `src/claims/*`, `src/okf/claim-sources.ts`, `src/okf/claims-verification.ts` | Grounded claims with machine verification. A prompt-only port cannot provide a claims store or evidence resolvers, so workers write pages directly. |
| `src/generation/*` | Durable resumable page jobs. The port runs in one session; update planning uses the last recorded `gitHead` as its only window. |
| `src/mermaid/*` | Mermaid fence parse-validation. Python's standard library cannot parse mermaid, and the v0.5.0 repository prompts carry no diagram discipline to lose. |
| `src/integrations/*` | Upstream's own coding-agent installer. This port *is* the alternative distribution. |

**Removed upstream, removed here.** The `skeleton_critic.ts` and `wiki_qa_subagents.ts`
subsystems that the v0.3.3 port adapted into critic/verifier subagent waves were deleted
upstream in v0.5.0 along with the monolithic prompt. The per-page worker model replaces
them; `src/agent/wiki-link-validator.ts`, previously reproduced but untracked, is now
tracked in [`upstream.lock.json`](upstream.lock.json).

### OKF front matter

From `v0.5.0`, every generated page carries YAML front matter following the Google Knowledge
Catalog OKF v0.2 schema — `type` is required, `title` and `description` are recommended, and
producer-defined extension fields are valid and preserved across runs. `index.md`, `log.md`,
`INSTRUCTIONS.md`, `_plan.md`, and `_sidebar.md` are reserved and never receive it.

After every run the finalizer stamps each page whose **body** changed with
`generated: { by: <model>, at: <timestamp> }` and removes the superseded legacy `timestamp`
field; pages whose body did not change keep their prior stamp. The stamp is code-owned:
workers must not author or edit it, and the finalizer restores it if a run tampers with it.

If you already have an `openwiki/` from an earlier version, no migration step is needed. Front
matter is additive, and [`scripts/openwiki-finalize.py`](scripts/openwiki-finalize.py) backfills
it on the next run (prepare mode, Step 2 of the command), tagging anything it inferred with
`openwiki_generated: true` so a later run can replace the guess with a real description.
Nothing is deleted.

Two notes if you are upgrading a wiki that already exists:

- If your `openwiki/` holds a `_sidebar.md`, the first run after upgrading rewrites the root
  `index.md` once, because the sidebar drops out of the generated listing. That single rewrite is
  the corrected output, not churn: the run is not a no-op, so `.last-update.json` is rewritten
  too. Runs after it are no-ops again.
- If an earlier version already injected front matter into your `_sidebar.md`, delete that block by
  hand once. The finalizer never removes content, so it stops adding the block but cannot clean up
  a sidebar that already carries one.
- Exempt `index.md` from any orphan or wiki-link check you run in CI. Generated indexes are
  deliberately absent from `_sidebar.md`, so a checker that treats "not reachable from the
  sidebar" as an orphan reports every one of them.

### Detecting drift

Nothing here imports upstream, so no dependency bump can ever reveal that upstream moved. The port
polls instead. [`upstream.lock.json`](upstream.lock.json) records a SHA-256 of each upstream file
the port reproduces, at the ref it was ported from, and
[`scripts/check-upstream-drift.sh`](scripts/check-upstream-drift.sh) re-hashes them against the
latest upstream release:

```sh
sh scripts/check-upstream-drift.sh          # report drift; exit 1 if any
sh scripts/check-upstream-drift.sh --ref 0.0.4   # check against a specific ref
sh scripts/check-upstream-drift.sh --update      # accept current upstream as tracked
```

Run `--update` only *after* re-porting the changed surface — it records the new hashes and silences
the alarm. Requires `jq` and `curl`; set `GITHUB_TOKEN` to raise the API rate limit.

[`.github/workflows/upstream-drift.yml`](.github/workflows/upstream-drift.yml) runs the check every
Monday and keeps a single issue in sync with the report, closing it when the port catches up. It
also runs on pull requests that touch the lock or the script, so a hand-edited lock fails the PR.

**Taken verbatim:** the full system prompt (`src/agent/prompt.ts`), the `init`/`update` prompt
bodies (`src/agent/prompts/code.ts`), the git commands (`src/agent/utils.ts`), the
`.last-update.json` shape, and the snapshot / no-op logic (`src/agent/index.ts`).

**Adapted (and why):** DeepAgents virtual-filesystem tools and paths → native Read/Write/Edit/
Glob/Grep/Bash on real repo paths; the DeepAgents "task tool" → Claude Code subagents; the
in-process SHA-256 snapshot → a shell one-liner; provider/model plumbing → a model-tier
recommendation.

**Dropped as acceptable loss:** provider adapters, credential storage, the Ink TUI, OpenRouter
fallback, and LangSmith tracing (Claude Code transcripts cover debugging).

## Repository layout

```
.claude-plugin/
  plugin.json        # Claude Code plugin manifest
  marketplace.json   # single-plugin marketplace (source: ./)
commands/
  wiki.md            # Claude Code slash command (system prompt + git + idempotence)
.agents/skills/
  openwiki/SKILL.md  # skill for Codex AND opencode (same agent, shell tool vocabulary)
.opencode/commands/
  wiki.md            # opencode /wiki — mode routing only; delegates to the skill
hooks/
  openwiki-gate.sh   # shell gate for hook-driven auto-run
  test_gate.sh       # self-check for the gate
scripts/
  check-upstream-drift.sh  # re-hashes the upstream files this port reproduces
  extract-upstream-prompt.py  # pulls planner/worker prompt text from upstream, verbatim
  openwiki-finalize.py     # Step 2 --snapshot (migrate + body-hash state), Step 3b finalize (indexes, links, generated provenance)
  test_finalize.py         # finalizer test suite
upstream.lock.json   # the upstream ref + per-file SHA-256 the port is ported from
.github/workflows/
  upstream-drift.yml # weekly drift check; keeps one issue in sync
README.md
```

The repo is both the Claude Code plugin and its marketplace, so
`/plugin marketplace add icampana/openwiki-cc` exposes it directly. The skill under
`.agents/skills/` is copied to `~/.agents/skills/` (or a repo's `.agents/skills/`), where **both**
Codex and opencode find it.

There are two agent definitions, not three. `commands/wiki.md` is authoritative for Claude Code;
`SKILL.md` carries the same verbatim OpenWiki system prompt, git commands, `.last-update.json`
shape and idempotence logic for the shell-based hosts. The opencode command adds slash-argument
routing and nothing else, so it never drifts from the skill.

The hosts differ in exactly one place — subagents:

| | Exploration strategy |
|---|---|
| Claude Code | native file tools + parallel read-only subagents (Task tool) |
| opencode | same, via its own Task tool — the skill's subagent section is marked opencode-only |
| Codex | no subagent tool; relies on native context compaction plus read discipline |

## License

The upstream prompt and command semantics originate from
[langchain-ai/openwiki](https://github.com/langchain-ai/openwiki). This repository is a fork of
[SoulKyu/openwiki-cc](https://github.com/SoulKyu/openwiki-cc), which did the original Claude Code
and Codex port.
