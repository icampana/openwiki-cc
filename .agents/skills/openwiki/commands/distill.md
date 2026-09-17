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
   already decided there, rejected or accepted. This is the rule the whole file
   exists to serve.
   - **Rejected** stays out because the full proposal text is kept verbatim on
     rejection, so a later run can recognize the same proposal and not remake it.
   - **Accepted** stays out for a different reason: the skill this candidate
     produced already exists, so proposing it again wastes a review cycle and
     risks a second, duplicate skill for the same pattern.
2. Read every `openwiki/experience/candidates/*.md`.
3. Group candidates that share a root cause, not a surface symptom. Two candidates
   about different files with the same root cause are one pattern.
4. For each group, drop any candidate whose front matter already carries a
   `decided` key (see step 7) — that key means this exact candidate was
   already ruled on, independent of what `decisions.md` says, so it counts
   toward nothing. Then count distinct entries across the remaining members'
   `sessions` list.
   - **Two or more distinct sessions** → propose.
   - **One session** → leave it as a candidate. Do not propose it, and do not
     delete it. A pattern seen once is an anecdote.
   - **Zero remaining members** (every candidate in the group already carries
     `decided`) → nothing to propose; skip the group.
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

   Then stamp every candidate behind this proposal with its own record of the
   verdict, so the candidate file carries its state rather than relying only
   on `decisions.md`: add `decided: accepted` or `decided: rejected` and
   `decision_date: <ISO date>` to each candidate's front matter. Do this only
   for a candidate this step actually decided — never touch a candidate still
   awaiting a verdict.

## Do not

- Write or edit a skill file. Propose only.
- Delete a candidate. Single-occurrence candidates are not noise; they are
  patterns that have happened once.
- Propose a pattern whose candidates carry no evidence. Report the candidate as
  unusable instead, and say which field is missing.
