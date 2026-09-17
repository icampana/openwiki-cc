---
type: Subsystem Concept
title: The experience layer
description: openwiki/experience/ is an accumulating record of patterns worth turning into skills — why it is structurally exempt from every generation pass, how the three guards enforce that, and the two commands (/openwiki:observe, /openwiki:distill) that write and promote it.
tags: [openwiki-cc, experience-layer, finalizer, skills, wikiskill]
generated: { by: claude-opus-5, at: 2026-09-17T16:26:58Z }
---

# The experience layer

Every other page under `openwiki/` is derived from repository evidence and rewritten when
that evidence changes. `openwiki/experience/` is the one subtree that is not. It accumulates
across sessions: agents and people file patterns they hit, with evidence attached, and a
later pass promotes the recurring ones into proposed skills.

That single difference — accumulated instead of regenerated — is the whole design. Everything
below exists to keep the generation machinery from touching this subtree.

## The lifecycle conflict

The two lifecycles are incompatible, not merely different:

| | the rest of `openwiki/` | `openwiki/experience/` |
|---|---|---|
| Source of truth | repository code | agent execution traces |
| Lifecycle | regenerated from evidence every run | accumulated across runs |
| Invariant | a no-op run leaves every file byte-identical | every run may add history |

The failure mode is concrete. If a page under `experience/` entered the Phase 1 planner's page
list, it would be handed to a Phase 2 worker whose prompt says to research the repository and
write that page. The worker has **no traces to read** — the observations came from sessions,
not from files it can grep — so it writes code documentation into the slot where the
observations were. Step 3b then restamps the result as generated. One update destroys the
section, silently, and the next run has no way to tell that anything was lost.

So the subtree is not protected by convention. It is protected by three structural guards, one
per pass that could reach it.

## Guard 1 — the finalizer's directory exclusion

`scripts/openwiki-finalize.py` declares the seam at module level:

```python
EXCLUDED_DIRS = {"experience"}
```

(`scripts/openwiki-finalize.py:41`). It is matched **by directory name at any depth**, which is
the cheap version: a repository that genuinely wants a documented concept directory called
`experience` has to pick another name.

It is applied in two places, and the second is not redundant.

**In `_iter_dirs` (`scripts/openwiki-finalize.py:193`).** This generator is the filesystem seam
for the whole script. `markdown_files` walks it, and `markdown_files` feeds `pass_frontmatter`,
`pass_links`, `write_state`, and `pass_provenance`. Its child filter is:

```python
if is_real_dir and child.name not in EXCLUDED_DIRS:
    yield from _iter_dirs(child)
```

Because the filter gates *recursion*, excluding `experience` removes the subtree from all four
passes at once. No front matter is backfilled, no broken link is annotated, no body hash is
snapshotted, no `generated:` stamp lands.

**In `render_index` (`scripts/openwiki-finalize.py:298`).** The child-directory loop skips
`EXCLUDED_DIRS` explicitly before deciding whether to link a subdirectory:

```python
if child.is_dir() and not child.is_symlink():
    if child.name in EXCLUDED_DIRS:
        continue
    if _has_real_markdown(child):
        ...
```

This second check is load-bearing because of how `_iter_dirs` is written: **it yields its own
argument before it filters anything.** `_has_real_markdown(child)` calls `_iter_dirs(child)`,
which yields `child` itself — so calling it on `openwiki/experience` would list that directory's
own `decisions.md` and any `candidates/*.md` (`index.md` is in `RESERVED` and filtered out
regardless) and report "yes, real pages here". Without the explicit skip, the
root index would grow a `- [Experience](experience/index.md)` entry, linking a subtree that is
otherwise invisible to the tool. `pass_indexes` (`scripts/openwiki-finalize.py:322`) needs no
third check: its directory list comes from `_iter_dirs(wiki)`, which never yields `experience`
at all, so no `index.md` is ever written inside it.

### Why `RESERVED` is not the seam

This is the distinction most likely to be got wrong. `RESERVED`
(`scripts/openwiki-finalize.py:29`) — `{index.md, log.md, _plan.md, _sidebar.md,
INSTRUCTIONS.md}` — looks like the exclusion mechanism, and it is not. It is matched as
`path.name in RESERVED`: a **filename** filter, checked inside `pass_frontmatter`,
`write_state`, `pass_provenance`, `_has_real_markdown` and `render_index` on individual files.
It never gates directory recursion, so adding a directory name to it would exclude nothing
whatsoever — the walk would still descend, and every non-reserved file underneath would still
be rewritten.

**The rule: a new protected directory goes in `EXCLUDED_DIRS`, never in `RESERVED`.** This is
itself the first filed candidate in the layer
(`openwiki/experience/candidates/reserved-set-filters-filenames-not-directories.md`).

## Guard 2 — the planner is told never to plan it

The finalizer exclusion stops the deterministic passes. It does not stop the model. The Phase 1
planner prompt carries one sentence, alongside the existing exclusion for generated index pages:

> The /openwiki/experience/ subtree is an accumulated experience layer written by
> /openwiki:observe, not documentation derived from repository evidence, so never include a
> page under it in the plan.

It lives in both ports, which carry the same planner text: `commands/wiki.md:207` (Claude Code,
authoritative) and `.agents/skills/openwiki/SKILL.md:223` (Codex, opencode). Editing one without
the other is the standard drift hazard in this repository, and the test below catches it.

## Guard 3 — the gate hook ignores observations

`hooks/openwiki-gate.sh` decides whether a Stop hook should spawn a full wiki run, by asking
whether the working tree is dirty. An uncommitted observation is a dirty tree, so without a
filter, filing one candidate would spawn a frontier-model documentation run — which would find
no source change to document, and whose planner would then be reasoning about a subtree it is
forbidden to plan. The filter (`hooks/openwiki-gate.sh:23-25`) drops two paths:

```sh
dirty=$(git status --short --untracked-files=all \
  | grep -v 'openwiki/\.last-update\.json$' \
  | grep -v 'openwiki/experience/' || true)
```

`.last-update.json` is rewritten by every run; `experience/` is written by `/openwiki:observe`.
Neither is a reason to regenerate documentation. `hooks/test_gate.sh:53-59` asserts both an
untracked candidate and an untracked `decisions.md` produce `skip`.

## The on-disk contract

```
openwiki/experience/index.md          one line per candidate: PROBLEM + ROOT CAUSE + FIX
openwiki/experience/candidates/       one page per candidate, with evidence
openwiki/experience/decisions.md      promoted and rejected, rejections kept in full
```

**`index.md`** is headed `# Experience` and holds bullets sorted by slug:

```markdown
- [<slug>](candidates/<slug>.md): PROBLEM + ROOT CAUSE + FIX in one or two sentences.
```

The index line is the part that actually gets read. It carries all three parts so a reader can
judge relevance without opening the page — the format is taken from WikiSkill directly, because
its stated purpose is the same.

**`candidates/<slug>.md`** carries front matter keys `slug`, `trigger` (what was being attempted
when this surfaced), and `sessions` (an append-only list of run identifiers), then three
headings: `## Evidence`, `## Friction`, `## Would have changed`. The slug is kebab-case and
names **the problem, not the fix**.

**`decisions.md`** is headed `# Decisions` and takes one section per verdict, with the pattern,
the candidate slugs and their session counts, the full proposed skill text, and the verdict with
its reason. The full proposal text is kept **even on a rejection**. This is not archival
sentiment: WikiSkill's first rule is "don't re-propose what was already tried and rejected", and
a verdict with the proposal stripped out cannot recognize the same proposal when it is made
again next month.

Note that the front matter here has no `generated:` or `openwiki_generated` field, and never
acquires one — that is Guard 1 working. These pages are authored, not generated.

## The two commands

**`/openwiki:observe`** (`commands/observe.md`) appends exactly one candidate, in-session, when
someone hits friction worth keeping. Feeding it from the friction a `/openwiki:wiki` run's Phase 2
workers hit — the evidence a worker needed that its `seedPaths` did not provide — is not wired in
this release: the worker prompt has no friction-reporting step, so nothing captures it. Wiring
that in would mean changing the Phase 2 worker prompt, whose contract intentionally ends "Write
only `${job.path}`. Do not create, edit, or delete another wiki page."

Its rules, in order of how often they matter:

- **Evidence or refusal.** Every candidate carries a `file:line`, a command and its observed
  output, or a commit SHA. An entry with no evidence is refused, not filed. A recollection is not
  evidence, and paraphrasing output you did not run is not evidence.
- **Read `decisions.md` first.** A pattern already rejected there is not re-filed.
- **No duplicates.** If an existing candidate covers the pattern, append the session identifier
  to that candidate's `sessions` list instead of creating a second page.
- **Write nowhere else**, propose no skill, and file no working-style observation that would hold
  in any repository — those belong in engram's personal scope, not in this repository's wiki.

**`/openwiki:distill`** (`commands/distill.md`) reads `candidates/`, groups by **root cause**
rather than surface symptom, counts distinct `sessions` entries per group, and proposes a skill
only where the count is two or more. A single-session candidate is left alone: not proposed, not
deleted — a pattern seen once is an anecdote, not noise. Each proposal reports the pattern, the
slugs behind it, the quoted evidence, the skill it proposes, and what would have to be true for
this to be the wrong call. Then a person accepts or rejects, and the verdict is appended to
`decisions.md`. Distill proposes; it never writes a skill file.

## The two constraints that replace a reward signal

The design ports WikiSkill (arXiv:2608.27454), whose accept/reject loop is autonomous because it
has a reward signal: benchmark accuracy per iteration, recorded in a `skill-impact.md`. A pattern
log with no falsifiable signal grows without bound, cannot be ranked, and gives a distillation
pass nothing to prune against.

This port has no score. Two cheaper constraints stand in:

1. **Every entry carries evidence** — `file:line`, a command and its observed output, or a commit.
2. **Promotion requires recurrence** — two or more independent sessions before a proposal.

Say it plainly: **both are weaker than a benchmark.** Evidence proves an entry is about something
real, not that acting on it improves anything; recurrence proves a pattern repeated, not that a
skill would have helped. They were chosen for the one property a benchmark would also have and
that a reward signal is not required for: **a later run can check them.** A missing evidence field
is mechanically detectable, and a `sessions` list of length one is mechanically countable. Because
neither constraint measures outcome, the human approval step in `/openwiki:distill` is not a
temporary caution — it is where the missing signal comes from.

## The tests that hold each invariant

All in `scripts/test_finalize.py`:

- **`TestExcludedDirs`** (line 244) seeds a populated `experience/` tree and asserts, pass by
  pass, that it is invisible: no front matter backfilled, no `index.md` written inside it, no
  `experience` entry in the root index, the authored `experience/index.md` left byte-identical,
  broken links under it not annotated, and no provenance stamp landing on it. Each case calls the
  pass directly, so a regression names the pass that broke.
- **`TestIdempotence.test_a_populated_experience_layer_stays_byte_identical`** (line 657) runs the
  full CLI twice — snapshot then finalize, twice over — and compares every `.md` byte for byte.
  This is the end-to-end version: it would catch an exclusion that holds for one pass but leaks
  through the interaction of several.
- **`TestDocClaims.test_planner_is_told_never_to_plan_the_experience_subtree`** (line 1169)
  asserts the planner sentence is present in **both** `commands/wiki.md` and
  `.agents/skills/openwiki/SKILL.md`, which is the guard against the two ports drifting apart.
- **`TestShippedCopy`** (line 1028) asserts the finalizer shipped under
  `.agents/skills/openwiki/scripts/` matches `scripts/` — so a change to `EXCLUDED_DIRS` that is
  made in only one copy fails the suite.
- **`hooks/test_gate.sh`** (lines 53-59) covers Guard 3 from outside Python.

## Where to extend it

- **Protecting another accumulating directory**: add its name to `EXCLUDED_DIRS`, extend the
  planner sentence in both ports, extend the gate filter, and add a `TestExcludedDirs`-shaped
  case. Do not reach for `RESERVED`.
- **Deliberately out of scope** (and reconsidering any of these is a design change, not a tweak):
  automatic capture via hooks, which produces volume rather than evidence with no signal to prune
  it back; cross-repository capture, which engram's personal scope already covers; and having
  `distill` write the skills it proposes.
- **Open and deferred**: whether `openwiki/experience/` should be committed or gitignored.
  Committing makes observations shared and reviewable, and is what the current setup assumes;
  ignoring them would make the layer per-developer and remove the gate-hook problem entirely. The
  choice changes none of the code above.

See also: [architecture.md](architecture.md) for the run lifecycle these guards sit inside, and
[quickstart.md](quickstart.md) for task routing.
