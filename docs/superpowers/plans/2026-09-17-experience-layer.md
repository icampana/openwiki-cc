# Experience Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give openwiki a reserved `openwiki/experience/` subtree that accumulates
skill-worthy patterns across runs, without weakening the guarantee that a no-op wiki
run leaves every generated file byte-identical.

**Architecture:** One name-based directory exclusion in the finalizer makes the
subtree invisible to all four passes, because every pass reaches the filesystem
through `_iter_dirs`. The planner prompt gains one sentence so no page under the
subtree is ever planned. The Stop-hook gate gains one filter so an observation does
not spawn a wiki run. Two new commands write and distill the subtree; the Phase 2
worker prompt is not touched.

**Tech Stack:** Python 3 standard library (no dependencies), POSIX `sh`, Markdown
prompt files. Tests are `unittest` in `scripts/test_finalize.py` and assertions in
`hooks/test_gate.sh`.

**Spec:** `docs/superpowers/specs/2026-09-17-experience-layer-design.md`

## Global Constraints

- The finalizer must never fail: it always exits 0. See its module docstring.
- The finalizer must be idempotent: a run that changes no page bodies leaves every
  wiki file byte-identical.
- The finalizer writes only inside `openwiki/`.
- Python 3.9 compatible. Standard library only. No f-strings with `=`, no `match`.
- `scripts/openwiki-finalize.py` and
  `.agents/skills/openwiki/scripts/openwiki-finalize.py` must stay byte-identical.
  `TestShippedCopy` enforces this.
- Prompt text changes land in **both** ports, which carry the same text:
  `commands/wiki.md` (authoritative) and `.agents/skills/openwiki/SKILL.md`.
- Run the full suite with `python3 scripts/test_finalize.py` and
  `sh hooks/test_gate.sh`.
- Plugin version ends this branch at `0.6.0` in `.claude-plugin/plugin.json`.
- Branch is `feature/OW-8`. Commit subjects use `<type>(<scope>): [OW-8] <subject>`.

---

### Task 1: Finalizer directory exclusion

Makes `openwiki/experience/` invisible to every finalizer pass. All four passes
reach the filesystem through `_iter_dirs`, so one filter there covers frontmatter,
links, state, and provenance. `render_index` lists child directories directly and
needs its own check.

**Files:**
- Modify: `scripts/openwiki-finalize.py` (add `EXCLUDED_DIRS`; edit `_iter_dirs` at
  line 183 and `render_index` at line 288)
- Modify: `.agents/skills/openwiki/scripts/openwiki-finalize.py` (byte-identical copy)
- Test: `scripts/test_finalize.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `finalize.EXCLUDED_DIRS`, a `set` of directory names (strings) that
  every markdown-finding pass skips. Task 6 relies on `"experience"` being in it.

- [ ] **Step 1: Write the failing tests**

Add this class to `scripts/test_finalize.py`, immediately after `class
TestIndexes(TempWiki):` and its methods end (before `class TestLinks`):

```python
class TestExcludedDirs(TempWiki):
    """openwiki/experience/ accumulates across runs instead of being regenerated,
    so every finalizer pass must treat it as invisible."""

    def seed(self):
        self.write("quickstart.md", "# Quickstart\n\nStart here.\n")
        self.write("experience/index.md", "# Experience\n\n- [a](candidates/a.md): P + C + F\n")
        self.write("experience/decisions.md", "# Decisions\n\nNothing yet.\n")
        return self.write(
            "experience/candidates/a.md",
            "# Seed paths missed the gate hook\n\nSee [b](missing.md).\n",
        )

    def test_no_frontmatter_is_backfilled_under_an_excluded_dir(self):
        p = self.seed()
        original = p.read_text(encoding="utf-8")
        finalize.pass_frontmatter(self.wiki)
        self.assertEqual(p.read_text(encoding="utf-8"), original)

    def test_no_index_is_written_inside_an_excluded_dir(self):
        self.seed()
        finalize.pass_indexes(self.wiki)
        target = self.wiki / "experience" / "candidates" / "index.md"
        self.assertFalse(target.exists(), "wrote an index into the experience layer")

    def test_root_index_does_not_link_an_excluded_dir(self):
        self.seed()
        finalize.pass_indexes(self.wiki)
        root = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertIn("quickstart.md", root)
        self.assertNotIn("experience", root)

    def test_authored_experience_index_is_left_byte_identical(self):
        self.seed()
        p = self.wiki / "experience" / "index.md"
        original = p.read_text(encoding="utf-8")
        finalize.pass_indexes(self.wiki)
        self.assertEqual(p.read_text(encoding="utf-8"), original)

    def test_broken_links_under_an_excluded_dir_are_not_annotated(self):
        p = self.seed()
        finalize.pass_links(self.wiki)
        self.assertNotIn("openwiki: broken internal link", p.read_text(encoding="utf-8"))

    def test_no_provenance_stamp_lands_under_an_excluded_dir(self):
        p = self.seed()
        state = finalize.state_path_for(self.wiki)
        finalize.write_state(self.wiki)
        finalize.pass_provenance(self.wiki, "test-actor", "2026-09-17T00:00:00.000Z", state)
        self.assertNotIn("generated:", p.read_text(encoding="utf-8"))
```

Then add this method inside the existing `class TestIdempotence(TempWiki):`,
after `test_two_consecutive_full_runs_are_byte_identical`:

```python
    def test_a_populated_experience_layer_stays_byte_identical(self):
        self.write("quickstart.md", "# Quickstart\n\nStart. See [arch](arch/overview.md).\n")
        self.write("arch/overview.md", "# Overview\n\nBody.\n")
        self.write("experience/index.md", "# Experience\n\n- [a](candidates/a.md): P + C + F\n")
        self.write("experience/decisions.md", "# Decisions\n\nNothing yet.\n")
        self.write("experience/candidates/a.md", "# A\n\nSee [gone](nope.md).\n")

        self.full_run()
        after_one = self.snapshot()
        self.full_run()
        self.assertEqual(self.snapshot(), after_one)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 scripts/test_finalize.py TestExcludedDirs -v`
Expected: FAIL. `test_no_index_is_written_inside_an_excluded_dir` and
`test_root_index_does_not_link_an_excluded_dir` fail on assertions;
`test_no_frontmatter_is_backfilled_under_an_excluded_dir` and
`test_no_provenance_stamp_lands_under_an_excluded_dir` fail because the passes
rewrite the page.

- [ ] **Step 3: Add the exclusion constant**

In `scripts/openwiki-finalize.py`, directly below the `RESERVED` / `GENERATED_FIELD`
/ `FALLBACK_TYPE` block near line 29, add:

```python
# Directory names whose contents are never OpenWiki pages. The experience layer
# accumulates across runs from agent sessions instead of being regenerated from
# repository evidence, so every markdown-finding pass has to treat it as
# invisible: otherwise pass_frontmatter backfills it, pass_provenance stamps it,
# and pass_indexes both overwrites its authored index and links it from the root.
# Matched by name at any depth, which is the cheap version -- a repository that
# genuinely wants a documented concept directory called "experience" has to pick
# another name.
EXCLUDED_DIRS = {"experience"}
```

- [ ] **Step 4: Filter in `_iter_dirs`**

In `_iter_dirs` (line 183), replace the recursion guard:

```python
        if is_real_dir:
            yield from _iter_dirs(child)
```

with:

```python
        if is_real_dir and child.name not in EXCLUDED_DIRS:
            yield from _iter_dirs(child)
```

- [ ] **Step 5: Filter in `render_index`**

In `render_index` (line 288), replace the child-directory branch:

```python
        if child.is_dir() and not child.is_symlink():
            if _has_real_markdown(child):
```

with:

```python
        if child.is_dir() and not child.is_symlink():
            if child.name in EXCLUDED_DIRS:
                continue
            if _has_real_markdown(child):
```

This second check is required, not redundant: `_has_real_markdown(child)` calls
`_iter_dirs(child)`, which always yields its own argument before filtering, so an
excluded directory holding `decisions.md` would otherwise report as having pages
and get linked from the root index.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 scripts/test_finalize.py -v`
Expected: PASS, whole suite, including `TestIdempotence` and `TestShippedCopy`
failing only until Step 7.

- [ ] **Step 7: Sync the shipped copy**

```bash
cp scripts/openwiki-finalize.py .agents/skills/openwiki/scripts/openwiki-finalize.py
python3 scripts/test_finalize.py TestShippedCopy -v
```
Expected: PASS.

- [ ] **Step 8: Mutation-test the guard**

Prove the tests can fail. Temporarily change `EXCLUDED_DIRS = {"experience"}` to
`EXCLUDED_DIRS = set()`, run `python3 scripts/test_finalize.py TestExcludedDirs -v`,
confirm failures, then restore the line and re-run to confirm PASS. A guard whose
test still passes when the guard is removed is not a guard.

- [ ] **Step 9: Commit**

```bash
git add scripts/openwiki-finalize.py scripts/test_finalize.py \
        .agents/skills/openwiki/scripts/openwiki-finalize.py
git commit -m "feat(finalize): [OW-8] exclude the experience subtree from every pass"
```

---

### Task 2: Gate-hook exclusion

`hooks/openwiki-gate.sh:20` strips only `openwiki/.last-update.json` from its dirty
check, so an uncommitted observation reads as a source change and spawns a full
frontier-model wiki run on the next Stop hook.

**Files:**
- Modify: `hooks/openwiki-gate.sh:20`
- Test: `hooks/test_gate.sh`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: nothing later tasks call. Behavior only.

- [ ] **Step 1: Write the failing assertions**

In `hooks/test_gate.sh`, after the block ending `rm openwiki/page.md`, insert:

```sh
# untracked observation → skip (the experience layer is not repository evidence)
mkdir -p openwiki/experience/candidates
echo x > openwiki/experience/candidates/a.md
run "untracked experience candidate" skip
echo y > openwiki/experience/decisions.md
run "untracked experience decisions" skip
rm -rf openwiki/experience
```

- [ ] **Step 2: Run it to verify it fails**

Run: `sh hooks/test_gate.sh`
Expected: FAIL with `FAIL - untracked experience candidate: expected skip, got spawn`.

- [ ] **Step 3: Add the filter**

In `hooks/openwiki-gate.sh`, replace line 20:

```sh
dirty=$(git status --short --untracked-files=all | grep -v 'openwiki/\.last-update\.json$' || true)
```

with:

```sh
# .last-update.json is rewritten by every run; openwiki/experience/ is the
# accumulated experience layer, written by /openwiki:observe rather than derived
# from source. Neither is a reason to regenerate documentation.
dirty=$(git status --short --untracked-files=all \
  | grep -v 'openwiki/\.last-update\.json$' \
  | grep -v 'openwiki/experience/' || true)
```

The `HEAD`-moved branch below needs no change: its filter is already
`grep -v '^openwiki/'`, which covers the subtree.

- [ ] **Step 4: Run it to verify it passes**

Run: `sh hooks/test_gate.sh`
Expected: `all passed`.

- [ ] **Step 5: Mutation-test the filter**

Remove the `grep -v 'openwiki/experience/'` line, run `sh hooks/test_gate.sh`,
confirm it fails on the new assertion, restore it, confirm `all passed`.

- [ ] **Step 6: Commit**

```bash
git add hooks/openwiki-gate.sh hooks/test_gate.sh
git commit -m "fix(gate): [OW-8] stop observations from spawning a wiki run"
```

---

### Task 3: Planner prompt exclusion

Without this sentence, Phase 1 can plan a page under `experience/`, and the Phase 2
worker then rewrites accumulated observations as code documentation.

**Files:**
- Modify: `commands/wiki.md:206-207`
- Modify: `.agents/skills/openwiki/SKILL.md` (the same planner paragraph)
- Test: `scripts/test_finalize.py` (`TestDocClaims`)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing callable. The literal sentence below is asserted by the test,
  so later edits must keep it verbatim.

- [ ] **Step 1: Write the failing test**

Add to `class TestDocClaims(unittest.TestCase):` in `scripts/test_finalize.py`:

```python
    def test_planner_is_told_never_to_plan_the_experience_subtree(self):
        """A planned experience page gets rewritten by a Phase 2 worker, which
        has no traces to read and replaces observations with code docs."""
        for doc in DOC_PAIR:
            with self.subTest(doc=doc):
                text = (pathlib.Path(__file__).parent.parent / doc).read_text()
                self.assertIn(
                    "never include a page under it in the plan", text
                )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 scripts/test_finalize.py TestDocClaims -v`
Expected: FAIL on both subtests.

- [ ] **Step 3: Add the sentence to `commands/wiki.md`**

At line 206-207 the paragraph currently ends:

```
route readers through the hierarchy; generated index pages will provide folder
navigation and must not be included in the plan.
```

Append, in the same paragraph:

```
The /openwiki/experience/ subtree is an accumulated experience layer written by
/openwiki:observe, not documentation derived from repository evidence, so never
include a page under it in the plan.
```

- [ ] **Step 4: Add the same sentence to the shell-host port**

Make the identical edit to the matching planner paragraph in
`.agents/skills/openwiki/SKILL.md`. Locate it with:

```bash
grep -n "must not be included in the plan" .agents/skills/openwiki/SKILL.md
```

- [ ] **Step 5: Run it to verify it passes**

Run: `python3 scripts/test_finalize.py TestDocClaims -v`
Expected: PASS, both subtests.

- [ ] **Step 6: Commit**

```bash
git add commands/wiki.md .agents/skills/openwiki/SKILL.md scripts/test_finalize.py
git commit -m "docs(prompt): [OW-8] tell the planner never to plan the experience layer"
```

---

### Task 4: `/openwiki:observe`

Appends one evidence-bearing candidate to the subtree. Refuses an entry with no
evidence, which is the only defense against an unfalsifiable pile — there is no
reward signal here to prune one back.

**Files:**
- Create: `commands/observe.md`
- Modify: `.agents/skills/openwiki/SKILL.md` (add an `observe` mode to the routing)
- Modify: `README.md` (the command list at line 13-14)

**Interfaces:**
- Consumes: `EXCLUDED_DIRS` from Task 1 — the paths this writes are invisible to
  the finalizer only because `"experience"` is in that set.
- Produces: the on-disk contract Task 5 reads:
  - `openwiki/experience/index.md` — one bullet per candidate,
    `- [<slug>](candidates/<slug>.md): PROBLEM + ROOT CAUSE + FIX`
  - `openwiki/experience/candidates/<slug>.md` — YAML front matter with keys
    `slug`, `trigger`, `sessions` (a YAML list of strings), then Markdown sections
    `## Evidence`, `## Friction`, `## Would have changed`
  - `openwiki/experience/decisions.md` — read-only for this command

- [ ] **Step 1: Write the command file**

Create `commands/observe.md`:

````markdown
---
description: Record a pattern worth a skill into openwiki/experience/
argument-hint: "[what you observed]"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# /openwiki:observe — capture a skill-worthy pattern

Appends exactly one candidate to `openwiki/experience/`. The subtree is excluded
from every finalizer pass and is never planned, so nothing here is regenerated:
what you write is what stays.

## When this runs

- **In a session**, when you or the user hit friction worth keeping.
- **After Step 3b of a `/openwiki:wiki` run**, from the friction the Phase 2
  workers reported in their final messages — the evidence a worker needed that its
  `seedPaths` did not provide. The worker prompt is unchanged: workers report,
  this command files.

## The evidence rule

Every candidate carries evidence a later run can check:

- a `file:line` reference, or
- a command and the output you actually observed, or
- a commit SHA.

**An entry with no evidence is refused, not filed.** Say what is missing and stop.
A recollection is not evidence. Do not paraphrase output you did not run.

## Steps

1. Read `openwiki/experience/decisions.md` if it exists. If this pattern was
   already rejected there, say so and stop — do not re-file it.
2. Read `openwiki/experience/index.md` if it exists. If a candidate already covers
   this pattern, append the current session identifier to that candidate's
   `sessions` list and stop. Do not create a duplicate.
3. Otherwise pick a kebab-case `<slug>` naming the problem, not the fix.
4. Write `openwiki/experience/candidates/<slug>.md`:

```markdown
---
slug: <slug>
trigger: <what was being attempted when this surfaced>
sessions:
  - <session identifier, e.g. the git SHA at the time, or an ISO date>
---

# <one-line problem statement>

## Evidence

<file:line, or the command and its observed output, or the commit SHA>

## Friction

<what went wrong, and the root cause>

## Would have changed

<what a skill would have done differently>
```

5. Append one bullet to `openwiki/experience/index.md`, keeping bullets sorted by
   slug:

```markdown
- [<slug>](candidates/<slug>.md): PROBLEM + ROOT CAUSE + FIX in one or two sentences.
```

   The index line is the part that gets read. It has to let someone judge
   relevance without opening the page, so it carries all three parts.

6. Create `openwiki/experience/index.md` with the heading `# Experience` and
   `openwiki/experience/decisions.md` with the heading `# Decisions` if they do not
   exist yet.

## Do not

- Write anywhere outside `openwiki/experience/`.
- Propose or write a skill. That is `/openwiki:distill`, and it needs recurrence
  this command cannot see.
- File a working-style observation that would hold in any repository. Those belong
  in engram's personal scope, not in this repository's wiki.
````

- [ ] **Step 2: Verify the front matter parses and the command is discoverable**

```bash
python3 -c "import pathlib,sys; t=pathlib.Path('commands/observe.md').read_text(); sys.exit(0 if t.startswith('---\n') and '\n---\n' in t else 1)" && echo ok
grep -c "allowed-tools" commands/observe.md
```
Expected: `ok` and `1`.

- [ ] **Step 3: Add the `observe` mode to the shell-host skill**

In `.agents/skills/openwiki/SKILL.md`, under `## Mode resolution`, add a bullet:

```markdown
- The user asks to **record / observe / capture** a pattern worth a skill →
  **observe mode**: follow `commands/observe.md` in this repository, which is
  authoritative for that mode.
```

- [ ] **Step 4: Update the README command list**

In `README.md` near line 13, under the Claude Code bullet, add:

```markdown
- **Claude Code** — `commands/observe.md` → `/openwiki:observe`, records one
  evidence-bearing pattern into `openwiki/experience/`.
```

- [ ] **Step 5: Drive the command end to end**

Run `/openwiki:observe` in this repository against a real observation from this
branch — for example, that `RESERVED` looked like the exclusion seam but is matched
as `path.name in RESERVED`, so the actual seam is `_iter_dirs`. Then confirm the
finalizer ignores what it wrote:

```bash
python3 scripts/openwiki-finalize.py --snapshot openwiki
python3 scripts/openwiki-finalize.py openwiki --actor manual-check
git status --short openwiki/
```
Expected: the `openwiki/experience/` files appear as untracked or modified exactly
as written, with no front matter backfilled, no `generated:` stamp, and no
`openwiki/experience/**/index.md` created by the finalizer.

- [ ] **Step 6: Confirm the gate stays quiet**

```bash
sh hooks/test_gate.sh
```
Expected: `all passed`. The observation written in Step 5 is covered by Task 2's
filter.

- [ ] **Step 7: Commit**

```bash
git add commands/observe.md .agents/skills/openwiki/SKILL.md README.md openwiki/experience
git commit -m "feat(observe): [OW-8] add /openwiki:observe for capturing patterns"
```

---

### Task 5: `/openwiki:distill`

Groups candidates by recurrence and proposes a skill only where a pattern appears
in two or more independent sessions. Records every verdict, so a rejected proposal
never comes back.

**Files:**
- Create: `commands/distill.md`
- Modify: `.agents/skills/openwiki/SKILL.md` (add a `distill` mode)
- Modify: `README.md`

**Interfaces:**
- Consumes: the on-disk contract from Task 4 — `candidates/<slug>.md` front matter
  keys `slug`, `trigger`, `sessions`; the `## Evidence`, `## Friction`,
  `## Would have changed` sections; and `index.md`'s bullet format.
- Produces: `openwiki/experience/decisions.md` entries, which Task 4 Step 1 reads
  to refuse re-filing a rejected pattern.

- [ ] **Step 1: Write the command file**

Create `commands/distill.md`:

````markdown
---
description: Propose skills from recurring patterns in openwiki/experience/
argument-hint: "[optional: a slug to focus on]"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# /openwiki:distill — propose skills from accumulated patterns

Reads `openwiki/experience/candidates/`, groups by recurrence, and proposes skills.
It proposes; a person approves and writes. WikiSkill's proposer is autonomous
because it can measure the result against a benchmark score. This one has no score,
so recurrence and evidence stand in for it, and a human makes the call.

## Steps

1. **Read `openwiki/experience/decisions.md` first.** Never re-propose anything
   already rejected there. This is the rule the whole file exists to serve.
2. Read every `openwiki/experience/candidates/*.md`.
3. Group candidates that share a root cause, not a surface symptom. Two candidates
   about different files with the same root cause are one pattern.
4. For each group, count distinct entries across every member's `sessions` list.
   - **Two or more distinct sessions** → propose.
   - **One session** → leave it as a candidate. Do not propose it, and do not
     delete it. A pattern seen once is an anecdote.
5. For each proposal, report:
   - the pattern, as PROBLEM + ROOT CAUSE + FIX;
   - the candidate slugs behind it and their session counts;
   - every piece of evidence, quoted from the candidate pages;
   - the skill you propose: its name, when it would trigger, and what it would do;
   - what would have to be true for this to be the wrong call.
6. Ask the user to accept or reject each proposal. Do not write the skill.
7. Append the verdict to `openwiki/experience/decisions.md`:

```markdown
## <slug-or-group-name> — <accepted|rejected> — <ISO date>

**Pattern:** <PROBLEM + ROOT CAUSE + FIX>
**Candidates:** <slugs>, <n> sessions
**Proposal:**

<the full proposed skill text, kept verbatim even on rejection, so a later
run can recognize it and not re-propose it>

**Verdict:** <accepted|rejected> — <the reason, in the user's terms>
```

   Keep the full proposal text on a rejection. A verdict with the proposal
   stripped out cannot stop the same proposal being made again next month.

## Do not

- Write or edit a skill file. Propose only.
- Delete a candidate. Single-occurrence candidates are not noise; they are
  patterns that have happened once.
- Propose a pattern whose candidates carry no evidence. Report the candidate as
  unusable instead, and say which field is missing.
````

- [ ] **Step 2: Verify the front matter parses**

```bash
python3 -c "import pathlib,sys; t=pathlib.Path('commands/distill.md').read_text(); sys.exit(0 if t.startswith('---\n') and '\n---\n' in t else 1)" && echo ok
```
Expected: `ok`.

- [ ] **Step 3: Add the `distill` mode to the shell-host skill**

In `.agents/skills/openwiki/SKILL.md`, under `## Mode resolution`, add:

```markdown
- The user asks to **distill / promote** patterns into skills → **distill mode**:
  follow `commands/distill.md` in this repository, which is authoritative for that
  mode.
```

- [ ] **Step 4: Update the README command list**

In `README.md`, beside the `/openwiki:observe` bullet from Task 4, add:

```markdown
- **Claude Code** — `commands/distill.md` → `/openwiki:distill`, proposes skills
  from patterns that recurred in two or more sessions.
```

- [ ] **Step 5: Drive the command end to end**

Run `/openwiki:distill` against the candidate written in Task 4 Step 5.
Expected: it reports the candidate as a single-occurrence pattern, proposes
nothing, and writes nothing to `decisions.md`. That is the correct result, and it
exercises the recurrence gate in the direction most likely to be wrong.

- [ ] **Step 6: Commit**

```bash
git add commands/distill.md .agents/skills/openwiki/SKILL.md README.md
git commit -m "feat(distill): [OW-8] add /openwiki:distill for promoting patterns"
```

---

### Task 6: Ship it

Version, documentation, and the doc audit the house rules require whenever a wiki
page changed.

**Files:**
- Modify: `.claude-plugin/plugin.json` (version `0.5.1` → `0.6.0`)
- Modify: `openwiki/architecture.md`, `openwiki/quickstart.md` (via the wiki skill)
- Modify: `README.md` if the audit finds a false claim

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Run the whole suite**

```bash
python3 scripts/test_finalize.py
sh hooks/test_gate.sh
```
Expected: both pass. Record the observed counts, not "should pass".

- [ ] **Step 2: Bump the version**

In `.claude-plugin/plugin.json`, change `"version": "0.5.1"` to `"version": "0.6.0"`.
Two new commands and a change to the finalizer's file selection are a minor bump.
A shipped change is not done while the version people install by still points at
the old one.

- [ ] **Step 3: Update the wiki**

Run `/openwiki:wiki update` so `openwiki/architecture.md` and
`openwiki/quickstart.md` describe the experience layer, the finalizer exclusion,
and the two new commands.

- [ ] **Step 4: Confirm the update was idempotent for the experience layer**

```bash
git status --short openwiki/experience/
```
Expected: no modifications to `openwiki/experience/` from the wiki run. If the run
rewrote anything there, Task 3 did not land and the branch is not shippable.

- [ ] **Step 5: Audit the docs**

Dispatch one `assetlink:doc-accuracy-auditor` with a clean context against the
branch diff. Read-only, findings only. Verify every finding yourself before acting
on it — prefer running the finalizer or the gate over re-reading the prose.

- [ ] **Step 6: Run the premortem**

Invoke `/premortem` on the branch. Fix anything it finds, then re-run it.

- [ ] **Step 7: Stage, do not commit**

```bash
git add -A
git status --short
```

Propose this commit message and stop:

```
feat(experience): [OW-8] add an accumulating experience layer to the wiki
```
