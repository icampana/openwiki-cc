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

## The evidence rule

Every candidate carries evidence a later run can check:

- a code citation naming the **symbol** — function, constant, or class — and quoting
  the line, with a `file:line` as an optional convenience rather than the citation's
  identity. `openwiki/experience/` is never regenerated, so a bare line number decays
  silently the moment the file around it changes; a symbol name and a quoted line
  survive that, or
- a command and the output you actually observed, or
- a commit SHA.

**An entry with no evidence is refused, not filed.** Say what is missing and stop.
A recollection is not evidence. Do not paraphrase output you did not run.

## Steps

1. Create `openwiki/experience/index.md` with the heading `# Experience` and
   `openwiki/experience/decisions.md` with the heading `# Decisions` if they do not
   exist yet — before anything below reads or writes in this directory.
2. Read `openwiki/experience/decisions.md`. If this pattern was already rejected
   there, say so and stop — do not re-file it.
3. Read `openwiki/experience/index.md`. If a candidate already covers this pattern:
   - If that candidate's front matter carries a `decided` key, do not touch it — file
     a new candidate instead (go to step 4).
   - Otherwise, the current session identifier is the invariant: **one entry per
     session, never two from the same sitting**, regardless of what changed (a
     commit, a file edit) in between. If it is already the last entry in that
     candidate's `sessions` list, stop without changing the file. Otherwise append it
     and stop. Do not create a duplicate.
4. Otherwise pick a kebab-case `<slug>` naming the problem, not the fix.
5. Write `openwiki/experience/candidates/<slug>.md`:

```markdown
---
slug: <slug>
trigger: <what was being attempted when this surfaced>
sessions:
  - <the current session identifier — this host's session/conversation ID, never a
    git SHA or an ISO date: both can repeat within the same sitting, which is exactly
    what the one-entry-per-session invariant above forbids>
---

# <one-line problem statement>

## Evidence

<symbol name with quoted line (file:line optional), or the command and its observed
output, or the commit SHA>

## Friction

<what went wrong, and the root cause>

## Would have changed

<what a skill would have done differently>
```

A freshly observed candidate carries no `decided` key. `/openwiki:distill` adds
`decided: accepted|rejected` and `decision_date: <ISO date>` to a candidate's
front matter once it rules on it — never this command, and never before then.

6. Insert one bullet into `openwiki/experience/index.md`, in slug order:

```markdown
- [<slug>](candidates/<slug>.md): PROBLEM + ROOT CAUSE + FIX in one or two sentences.
```

   The index line is the part that gets read. It has to let someone judge
   relevance without opening the page, so it carries all three parts.

## Do not

- Write anywhere outside `openwiki/experience/`.
- Propose or write a skill. That is `/openwiki:distill`, and it needs recurrence
  this command cannot see.
- File a working-style observation that would hold in any repository. Those belong
  in engram's personal scope, not in this repository's wiki.
