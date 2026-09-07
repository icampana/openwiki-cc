# OpenWiki v0.5.0 Re-port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-port openwiki-cc from upstream OpenWiki `v0.3.3` to `v0.5.0`: replace the deleted monolithic init/update prompt with the planner + per-page-worker model, upgrade the finalizer to OKF v0.2 with generated provenance, and follow upstream's no-op/metadata semantics.

**Architecture:** Prompt files carry the new planner/worker prompts (extracted programmatically, claims tooling stripped); the deterministic finalizer gains a `--snapshot` prepare mode (Step 2: migrate + body-hash state) and a finalize mode (Step 3b: index sync + link validation + generated provenance), mirroring upstream's prepare/finalize split.

**Tech Stack:** Markdown prompt files, Python 3 standard library only (finalizer, extractor), POSIX `sh` + `jq` (gate, drift check), unittest (finalizer tests).

**Spec:** `docs/superpowers/specs/2026-09-07-openwiki-report-v050-design.md`

## Global Constraints

- Upstream reference is the tag `v0.5.0` — never `main`. Prompt bodies move; `CODE_SYSTEM_PROMPTS.init`/`.update` no longer exist.
- Python 3 standard library only. No `pip install`, no `import yaml`. (`hashlib`, `json`, `datetime` are stdlib — allowed.)
- `scripts/openwiki-finalize.py` **always exits 0**. It must never fail a documentation run.
- Idempotence: a second consecutive full run leaves every wiki file byte-identical. `.last-update.json` refreshing its timestamp on no-op runs is by design (#647) and is excluded from the bar; `.openwiki-run.json` lives outside the wiki tree.
- Provenance state file `.openwiki-run.json` lives at `wiki.parent` (the repo root when invoked as `openwiki-finalize.py openwiki` from the target repo). Finalize mode deletes it. A missing state file is conservative: treat every page as changed.
- The shipped twin `.agents/skills/openwiki/scripts/openwiki-finalize.py` must stay byte-identical to `scripts/openwiki-finalize.py` (`TestShippedCopy` enforces it). Every finalizer task refreshes the twin.
- `commands/wiki.md` is authoritative; `.agents/skills/openwiki/SKILL.md` mirrors it. `.opencode/commands/wiki.md` must not change.
- Harness adaptations in reproduced prompt text are marked `[adapted]` inline.
- Branch `feature/report-v0.5.0` (already created off `origin/main`). Commits: `type(scope): [OW-6] message`.
- Every `gh` command passes `--repo icampana/openwiki-cc`. The drift check needs `GITHUB_TOKEN=$(gh auth token)` (unauthenticated API calls 403).
- `RESERVED = {"index.md", "log.md", "_plan.md", "_sidebar.md", "INSTRUCTIONS.md"}` after Task 2.
- Provenance stamp rendering is exactly `generated: { by: <by>, at: <at> }` (single-line flow mapping; `at` omitted only when the prior event had none). `at` is UTC ISO 8601 with seconds + `Z`.

---

### Task 1: Upstream prompt extractor for `repository-prompts.ts`

The v0.3.3 extractor points at `code.ts`'s `CODE_SYSTEM_PROMPTS`, which at v0.5.0 holds only `chat`. The new prompts are function-built template literals with `${...}` interpolations (including nested template literals inside interpolations) and `\n`-style escapes that must be decoded to real characters.

**Files:**
- Modify: `scripts/extract-upstream-prompt.py`
- Create: `build/planner-prompt.md` (generated, git-ignored), `build/worker-prompt.md` (generated, git-ignored)

**Interfaces:**
- Consumes: nothing
- Produces: `python3 scripts/extract-upstream-prompt.py --ref <tag> --part <planner|worker>` writes the decoded prompt body to stdout; `--placeholders` lists the distinct top-level `${...}` expressions. Task 8 consumes the `build/` outputs.

- [ ] **Step 1: Confirm the current extractor no longer works**

Run:
```bash
python3 scripts/extract-upstream-prompt.py --ref v0.5.0 --command init
```
Expected: FAIL — exit non-zero (`command not found` at best; the file shape changed anyway). This is the red state.

- [ ] **Step 2: Write the rewritten extractor**

Replace `scripts/extract-upstream-prompt.py` in full with:

```python
#!/usr/bin/env python3
"""Extract a repository-prompt template verbatim from upstream OpenWiki.

The port reproduces upstream prompt text. Retyping it introduces drift, so pull
it from source instead. At v0.5.0 the repository prompts are function-built
template literals in src/agent/repository-prompts.ts. Their bodies carry
${...} interpolation points (including nested template literals inside
interpolations) that the port resolves per run; they are extracted verbatim
and marked, never silently filled.
"""
import argparse
import json
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/langchain-ai/openwiki/{ref}/src/agent/repository-prompts.ts"

PARTS = {
    "planner": "createRepositoryPlannerPrompt",
    "worker": "createRepositoryPagePrompt",
}

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "`": "`", "$": "$", "\\": "\\"}


def fetch(ref: str) -> str:
    with urllib.request.urlopen(RAW.format(ref=ref)) as r:
        return r.read().decode("utf-8")


def extract_template_body(source: str, start: int):
    """Scan a template literal starting just after its opening backtick.

    Understands ${ ... } interpolations, including nested template literals
    inside them, and escaped characters (kept raw here; decoded by unescape).
    Returns (body, end_index).
    """
    body = []
    i = start
    stack = []
    while i < len(source):
        c = source[i]
        if c == "\\":
            body.append(source[i:i + 2])
            i += 2
            continue
        if not stack:
            if c == "`":
                return "".join(body), i
            if c == "$" and source[i + 1:i + 2] == "{":
                stack.append(1)
                body.append("${")
                i += 2
                continue
            body.append(c)
            i += 1
            continue
        if c == "{":
            stack[-1] += 1
        elif c == "}":
            stack[-1] -= 1
            if stack[-1] == 0:
                stack.pop()
        elif c == "`":
            nested, end = extract_template_body(source, i + 1)
            body.append("`" + nested + "`")
            i = end + 1
            continue
        body.append(c)
        i += 1
    raise SystemExit("unterminated template literal")


def extract(source: str, part: str) -> str:
    """Return the raw returned-template body of the named prompt builder."""
    fn_at = source.find(PARTS[part])
    if fn_at == -1:
        raise SystemExit("builder not found: %s" % part)
    ret_at = source.find("return `", fn_at)
    if ret_at == -1:
        raise SystemExit("no returned template literal in %s" % part)
    body, _ = extract_template_body(source, ret_at + len("return `"))
    return body


def unescape(body: str) -> str:
    """Decode TypeScript template-literal escape sequences, left to right.

    Unknown escapes decode to the character itself, matching untagged-template
    semantics. Order-free because scanning is positional, not replacement-based.
    """
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            out.append(_ESCAPES.get(body[i + 1], body[i + 1]))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def interpolations(body: str):
    """Distinct top-level ${...} spans, brace-counted so nested templates survive."""
    found = []
    i = 0
    while i < len(body):
        if body[i] == "$" and body[i + 1:i + 2] == "{":
            depth = 1
            j = i + 2
            while j < len(body) and depth:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                j += 1
            span = body[i:j]
            if span not in found:
                found.append(span)
            i = j
            continue
        i += 1
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="v0.5.0")
    ap.add_argument("--part", required=True, choices=["planner", "worker"])
    ap.add_argument("--placeholders", action="store_true",
                    help="list the ${...} interpolation points instead of the body")
    args = ap.parse_args()

    body = unescape(extract(fetch(args.ref), args.part))
    if args.placeholders:
        print(json.dumps(interpolations(body), indent=2))
        return 0
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Verify sizes, escapes, and interpolation points**

Run:
```bash
mkdir -p build
python3 scripts/extract-upstream-prompt.py --part planner > build/planner-prompt.md
python3 scripts/extract-upstream-prompt.py --part worker   > build/worker-prompt.md
wc -c build/planner-prompt.md build/worker-prompt.md
python3 scripts/extract-upstream-prompt.py --part planner --placeholders
python3 scripts/extract-upstream-prompt.py --part worker --placeholders
grep -c '\\n' build/planner-prompt.md build/worker-prompt.md || true
```

Expected: planner is **2380** bytes, worker is **3003** bytes. Planner placeholders are exactly `${semanticContext}`, `${updateContext}`, and the `${view.wikiGoal ? ... : ""}` ternary. Worker placeholders include `${job.path}`, `${job.title}`, `${job.purpose}`, `${job.mode}`, `${language}`, the three `formatList(...)` spans, the update-mode ternary, `${job.existingClaimCount}`, the `${JSON.stringify(job.claimsRequiringAttention, null, 2)}` span, and the quickstart-map ternary. The final `grep -c` prints `0` twice — every `\n`-style escape decoded to a real character. A different byte count means upstream edited the prompts: stop and investigate rather than proceeding.

- [ ] **Step 4: Commit**

```bash
git add scripts/extract-upstream-prompt.py
git commit -m "feat(prompt): [OW-6] extract planner/worker prompts from repository-prompts.ts"
```

---

### Task 2: Finalizer — OKF v0.2 constants and the `INSTRUCTIONS.md` reservation

Small, independently reviewable: the version string the root index carries, and the concept-file exclusion upstream applies at both refs but the port missed.

**Files:**
- Modify: `scripts/openwiki-finalize.py`
- Modify: `scripts/test_finalize.py`

**Interfaces:**
- Consumes: nothing
- Produces: `RESERVED` containing `INSTRUCTIONS.md`; `ROOT_INDEX_FRONTMATTER` with `okf_version: "0.2"`. Tasks 3–5 consume both.

- [ ] **Step 1: Write the failing tests**

In `scripts/test_finalize.py`, change the existing root-index assertion to `0.2` and add an `INSTRUCTIONS.md` test to `TestFrontmatter`:

```python
    def test_instructions_md_is_never_a_concept(self):
        p = self.write("INSTRUCTIONS.md", "# Instructions\n\nDo not touch.\n")
        self.write("quickstart.md", "# Quickstart\n\nStart.\n")
        finalize.pass_frontmatter(self.wiki)
        finalize.pass_indexes(self.wiki)
        self.assertNotIn("type:", p.read_text(encoding="utf-8"))
        index = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertNotIn("INSTRUCTIONS.md", index)
        self.assertNotIn("Instructions", index)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 scripts/test_finalize.py`
Expected: FAIL — the existing `test_root_index_carries_okf_version_only` (still asserting `"0.1"`, update it to `"0.2"`) and the new test both fail.

- [ ] **Step 3: Write the minimal implementation**

In `scripts/openwiki-finalize.py`, replace the `RESERVED` line and the root front matter:

```python
# Upstream EXCLUDED_FILES at v0.5.0 is {index.md, log.md, INSTRUCTIONS.md}.
# _plan.md stays as legacy defense (this port's earlier versions wrote one);
# _sidebar.md stays (OW-5: Docsify nav partial).
RESERVED = {"index.md", "log.md", "_plan.md", "_sidebar.md", "INSTRUCTIONS.md"}
```

```python
ROOT_INDEX_FRONTMATTER = '---\nokf_version: "0.2"\n---\n\n'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 scripts/test_finalize.py`
Expected: PASS, 51 tests (existing 50 + 1 new; the updated version test now asserts `"0.2"`).

- [ ] **Step 5: Refresh the shipped twin and commit**

```bash
cp scripts/openwiki-finalize.py .agents/skills/openwiki/scripts/openwiki-finalize.py
python3 scripts/test_finalize.py
git add scripts/openwiki-finalize.py scripts/test_finalize.py .agents/skills/openwiki/scripts/openwiki-finalize.py
git commit -m "feat(okf): [OW-6] index as okf 0.2 and reserve INSTRUCTIONS.md"
```

---

### Task 3: Finalizer — `--snapshot` prepare mode (migrate + provenance state)

Ports upstream's `prepareWikiForAuthoring` (`migrate` + `provenance_snapshot`). Runs at the command's Step 2, before any writing. The state file lives at the repo root so it never pollutes the wiki tree.

**Files:**
- Modify: `scripts/openwiki-finalize.py`
- Modify: `scripts/test_finalize.py`

**Interfaces:**
- Consumes: `RESERVED`, `pass_frontmatter`, `markdown_files`, `read_text_or_none`, `split_frontmatter` (all exist)
- Produces: `STATE_FILENAME`, `state_path_for(wiki)`, `write_state(wiki) -> (migrated_count, snapshot_count)`; `--snapshot` CLI flag. Task 5 consumes the state file.

- [ ] **Step 1: Write the failing tests**

Append a `TestSnapshot` class to `scripts/test_finalize.py` (before the `__main__` block):

```python
class TestSnapshot(TempWiki):
    def state_path(self):
        return self.tmp / ".openwiki-run.json"

    def test_snapshot_records_body_hash_excluding_frontmatter(self):
        import hashlib
        self.write("a.md", '---\ntype: Playbook\ntitle: A\n---\n\n# A\n\nBody here.\n')
        finalize.write_state(self.wiki)
        import json
        entries = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["page"], "a.md")
        self.assertEqual(entries[0]["bodyHash"],
                         hashlib.sha256(b"\n# A\n\nBody here.\n").hexdigest())
        self.assertNotIn("generated", entries[0])

    def test_snapshot_records_prior_generated_event(self):
        self.write("a.md", '---\ntype: Playbook\ntitle: A\ngenerated: { by: m, at: 2026-01-01T00:00:00Z }\n---\n\n# A\n\nB.\n')
        finalize.write_state(self.wiki)
        import json
        entries = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(entries[0]["generated"], {"by": "m", "at": "2026-01-01T00:00:00Z"})

    def test_snapshot_migrates_frontmatter_first(self):
        p = self.write("bare.md", "# Bare\n\nProse.\n")
        finalize.write_state(self.wiki)
        self.assertTrue(p.read_text(encoding="utf-8").startswith("---\n"))

    def test_snapshot_overwrites_stale_state(self):
        self.state_path().write_text("STALE", encoding="utf-8")
        self.write("a.md", "# A\n\nB.\n")
        finalize.write_state(self.wiki)
        import json
        entries = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(entries[0]["page"], "a.md")

    def test_snapshot_skips_reserved_pages(self):
        self.write("index.md", "# Idx\n")
        self.write("real.md", "# Real\n\nB.\n")
        finalize.write_state(self.wiki)
        import json
        pages = [e["page"] for e in json.loads(self.state_path().read_text(encoding="utf-8"))]
        self.assertEqual(pages, ["real.md"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 scripts/test_finalize.py -k TestSnapshot`
Expected: FAIL — `write_state` does not exist yet.

- [ ] **Step 3: Write the minimal implementation**

Add `import hashlib` and `import json` to the imports, then add above `main()` (note: `read_generated_event` arrives in Task 4 — this task defines a one-line local parse for the snapshot only; Task 4 refactors it into the shared function. Simpler alternative: implement `read_generated_event` HERE, since these tests need it, and Task 4 consumes it. Doing that now keeps Task 4 purely additive):

```python
STATE_FILENAME = ".openwiki-run.json"


def state_path_for(wiki):
    """Repo-root provenance state: the directory holding the wiki, mirroring
    upstream's `.run.json` beside the wiki rather than inside it."""
    return wiki.resolve().parent / STATE_FILENAME


_GENERATED_RE = re.compile(
    r"^\s*generated:\s*\{\s*by:\s*(?P<by>[^,}]+?)\s*"
    r"(?:,\s*at:\s*(?P<at>[^}]+?)\s*)?\}\s*$"
)


def read_generated_event(text):
    """Parse a valid `generated: { by, at? }` flow mapping from front matter.

    Returns a dict, or None when the page has no block or no valid mapping.
    """
    fields_text, _ = split_frontmatter(text)
    if fields_text is None:
        return None
    for line in fields_text.splitlines():
        m = _GENERATED_RE.match(line)
        if m:
            by = m.group("by").strip().strip("'\"")
            at = (m.group("at") or "").strip().strip("'\"") or None
            if by:
                return {"by": by, "at": at} if at else {"by": by}
    return None


def body_hash(text):
    """SHA-256 of the body excluding front matter, whitespace retained.

    Any body change — including whitespace — advances the hash, so the
    finalize pass stamps exactly the pages this run touched.
    """
    _, body = split_frontmatter(text)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def write_state(wiki):
    """Migrate front matter, then snapshot per-page body hashes + prior
    generated events. Overwrites any stale state file. Returns
    (migrated_count, snapshot_count)."""
    migrated = len(pass_frontmatter(wiki))
    entries = []
    for path in markdown_files(wiki):
        if path.name in RESERVED:
            continue
        text = read_text_or_none(path)
        if text is None:
            continue
        entry = {"page": path.relative_to(wiki).as_posix(),
                 "bodyHash": body_hash(text)}
        event = read_generated_event(text)
        if event:
            entry["generated"] = event
        entries.append(entry)
    entries.sort(key=lambda e: e["page"])
    try:
        with open(state_path_for(wiki), "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
            f.write("\n")
    except OSError as exc:
        print("openwiki-finalize: could not write state (%s)" % exc)
    return migrated, len(entries)
```

And in `main()`, add the flag and dispatch (before the default-mode passes):

```python
    ap.add_argument("--snapshot", action="store_true",
                    help="prepare mode: migrate front matter, then write the provenance state file")
```

```python
    if args.snapshot:
        fm, pages = write_state(wiki)
        print("openwiki-finalize: migrated %d file(s), snapshot %d page(s)" % (fm, pages))
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 scripts/test_finalize.py`
Expected: PASS, 56 tests (51 + 5 new).

- [ ] **Step 5: Refresh the shipped twin and commit**

```bash
cp scripts/openwiki-finalize.py .agents/skills/openwiki/scripts/openwiki-finalize.py
python3 scripts/test_finalize.py
git add scripts/openwiki-finalize.py scripts/test_finalize.py .agents/skills/openwiki/scripts/openwiki-finalize.py
git commit -m "feat(finalizer): [OW-6] add snapshot prepare mode with body-hash state"
```

---

### Task 4: Finalizer — provenance primitives (set / remove / restore / repair)

Pure functions with no I/O, each mirroring one upstream helper (`setGeneratedEvent`, `removeFrontmatterField`, `canonicalizeChangedConcept`, `restoreGeneratedEvent`, `repairOkfFrontmatter` minimal). Task 5 wires them into the pass.

**Files:**
- Modify: `scripts/openwiki-finalize.py`
- Modify: `scripts/test_finalize.py`

**Interfaces:**
- Consumes: `split_frontmatter`, `read_generated_event`, `_GENERATED_RE` (Task 3)
- Produces: `set_generated_event(text, by, at)`, `remove_field(text, key)`, `canonicalize_terminal(text)`, `restore_generated_event(text, prior)`, `repair_frontmatter(text)`. Task 5 consumes all five.

- [ ] **Step 1: Write the failing tests**

Append a `TestProvenancePrimitives` class to `scripts/test_finalize.py`:

```python
class TestProvenancePrimitives(TempWiki):
    def test_set_replaces_existing_event_in_place(self):
        text = '---\ntype: Playbook\ntitle: A\ngenerated: { by: old, at: 2020-01-01T00:00:00Z }\n---\n\n# A\n\nB.\n'
        out = finalize.set_generated_event(text, "m", "2026-09-07T12:00:00Z")
        fields, _ = finalize.split_frontmatter(out)
        self.assertEqual(finalize.parse_fields(fields)["type"], "Playbook")
        self.assertIn("generated: { by: m, at: 2026-09-07T12:00:00Z }", out)
        self.assertNotIn("by: old", out)

    def test_set_appends_when_absent(self):
        text = '---\ntype: Playbook\ntitle: A\n---\n\n# A\n\nB.\n'
        out = finalize.set_generated_event(text, "m", "2026-09-07T12:00:00Z")
        self.assertIn("generated: { by: m, at: 2026-09-07T12:00:00Z }", out)
        self.assertTrue(finalize.split_frontmatter(out)[1].endswith("# A\n\nB.\n"))

    def test_set_without_at_omits_at(self):
        text = '---\ntype: Playbook\n---\n\n# A\n'
        out = finalize.set_generated_event(text, "m", None)
        self.assertIn("generated: { by: m }", out)

    def test_remove_field_drops_legacy_timestamp(self):
        text = '---\ntype: Playbook\ntimestamp: 2024-01-01\ntitle: A\n---\n\n# A\n'
        out = finalize.remove_field(text, "timestamp")
        self.assertNotIn("timestamp", out)
        self.assertIn("title: A", out)

    def test_canonicalize_terminal_endings(self):
        self.assertEqual(finalize.canonicalize_terminal("# A\n\n"), "# A\n")
        self.assertEqual(finalize.canonicalize_terminal("# A"), "# A\n")
        self.assertEqual(finalize.canonicalize_terminal("# A\n\n\n"), "# A\n")
        self.assertEqual(finalize.canonicalize_terminal("# A\r\n\r\n"), "# A\n")

    def test_restore_puts_back_tampered_event(self):
        tampered = '---\ntype: P\ngenerated: { by: impostor, at: 2030-01-01T00:00:00Z }\n---\n\n# A\n'
        out = finalize.restore_generated_event(
            tampered, {"by": "m", "at": "2026-09-07T12:00:00Z"})
        self.assertIn("generated: { by: m, at: 2026-09-07T12:00:00Z }", out)

    def test_restore_removes_stamp_when_previously_unstamped(self):
        stamped = '---\ntype: P\ngenerated: { by: m, at: 2026-09-07T12:00:00Z }\n---\n\n# A\n'
        out = finalize.restore_generated_event(stamped, None)
        self.assertNotIn("generated", out)
        self.assertIn("type: P", out)

    def test_repair_removes_invalid_generated_but_keeps_translation_marker(self):
        text = ('---\ntype: P\ngenerated: someday-maybe\n'
                'openwiki_translation_pending: true\n---\n\n# A\n')
        out = finalize.repair_frontmatter(text)
        self.assertNotIn("generated:", out)
        self.assertIn("openwiki_translation_pending: true", out)

    def test_repair_removes_empty_optional_scalars(self):
        text = '---\ntype: P\ntitle:\n---\n\n# A\n'
        out = finalize.repair_frontmatter(text)
        self.assertNotIn("title:", out)
        self.assertIn("type: P", out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 scripts/test_finalize.py -k TestProvenancePrimitives`
Expected: FAIL — none of the functions exist yet.

- [ ] **Step 3: Write the minimal implementation**

Add after `read_generated_event` in `scripts/openwiki-finalize.py`:

```python
def _is_key_line(line, key):
    """Whether a front-matter line assigns the top-level `key`."""
    stripped = line.strip()
    return (stripped == key or stripped.startswith(key + ":")
            or stripped.startswith(key + " :"))


def _split_fields(text):
    """Return (newline, field_lines, body) for a document known to carry a
    front matter block."""
    fields_text, body = split_frontmatter(text)
    newline = "\r\n" if "\r\n" in fields_text else "\n"
    lines = fields_text.split(newline)
    if lines and lines[-1] == "":
        lines.pop()
    return newline, lines, body


def set_generated_event(text, by, at):
    """Set or replace the `generated: { by, at }` flow mapping.

    Upstream's exact rendering: a single-line flow mapping. Replaced in place,
    appended at the end of the block when absent, so positions are stable.
    """
    line = ("generated: { by: %s }" % by if at is None
            else "generated: { by: %s, at: %s }" % (by, at))
    fields_text, body = split_frontmatter(text)
    if fields_text is None:
        # No block at all (migrate normally guarantees one); create minimal.
        return "---\n%s\n---\n\n%s" % (line, body.lstrip("\r\n"))
    newline, lines, body = _split_fields(text)
    out = [line if _is_key_line(l, "generated") else l for l in lines]
    if not any(_is_key_line(l, "generated") for l in lines):
        out.append(line)
    return "---" + newline + newline.join(out) + newline + "---" + newline + body


def remove_field(text, key):
    """Drop every top-level `key:` line from the front matter block."""
    fields_text, _ = split_frontmatter(text)
    if fields_text is None:
        return text
    newline, lines, body = _split_fields(text)
    out = [l for l in lines if not _is_key_line(l, key)]
    return "---" + newline + newline.join(out) + newline + "---" + newline + body


def canonicalize_terminal(text):
    """Upstream `canonicalizeChangedConcept`: changed pages end in exactly one
    LF. Nothing else is touched."""
    return re.sub(r"[\r\n]*\Z", "", text) + "\n"


def restore_generated_event(text, prior):
    """Unchanged body: put back the pre-run event when the agent removed or
    altered it; remove any stamp when the page was previously unstamped."""
    current = read_generated_event(text)
    if prior is None:
        return text if current is None else remove_field(text, "generated")
    if (current and current.get("by") == prior.get("by")
            and current.get("at") == prior.get("at")):
        return text
    return set_generated_event(text, prior["by"], prior.get("at"))


def repair_frontmatter(text):
    """Minimal OKF repair: drop an unparseable `generated` line and empty
    optional scalars. `openwiki_translation_pending` — a code-managed marker —
    is never touched."""
    fields_text, _ = split_frontmatter(text)
    if fields_text is None:
        return text
    newline, lines, body = _split_fields(text)
    out = []
    for line in lines:
        key = line.split(":", 1)[0].strip() if ":" in line else None
        if key == "generated" and not _GENERATED_RE.match(line):
            continue
        if (key in ("title", "description", "resource")
                and not line.split(":", 1)[1].strip()):
            continue
        out.append(line)
    return "---" + newline + newline.join(out) + newline + "---" + newline + body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 scripts/test_finalize.py`
Expected: PASS, 65 tests (56 + 9 new).

- [ ] **Step 5: Refresh the shipped twin and commit**

```bash
cp scripts/openwiki-finalize.py .agents/skills/openwiki/scripts/openwiki-finalize.py
python3 scripts/test_finalize.py
git add scripts/openwiki-finalize.py scripts/test_finalize.py .agents/skills/openwiki/scripts/openwiki-finalize.py
git commit -m "feat(finalizer): [OW-6] add provenance set/restore/repair primitives"
```

---

### Task 5: Finalizer — provenance pass, CLI modes, and full-run idempotence

Wires Task 4's primitives into `pass_provenance` and reworks `main()` into the two modes. **This task changes default-mode behavior** (front matter migrates at `--snapshot` time now), so the existing CLI-level `TestIdempotence` must be rewritten to run snapshot→finalize sequences.

**Files:**
- Modify: `scripts/openwiki-finalize.py`
- Modify: `scripts/test_finalize.py`

**Interfaces:**
- Consumes: all Task 3–4 functions
- Produces: `pass_provenance(wiki, actor, at, state_path) -> list[str]`, `read_state(path)`, `delete_state(path)`, `utc_now()`; `main()` with `--snapshot` / `--actor` / default modes. Tasks 7 (Step 2/3b wording) and 11 (integration) consume the CLI.

- [ ] **Step 1: Write the failing tests**

Append a `TestProvenancePass` class, and **rewrite** the existing `TestIdempotence.test_two_consecutive_runs_are_byte_identical` and `test_exit_zero_on_unreadable_content` CLI helpers to run snapshot→finalize (default-only runs no longer see a snapshot, so every page is conservatively stamped with a fresh `at` — the old byte-identical assertion would fail by design):

```python
class TestProvenancePass(TempWiki):
    def run_snapshot(self):
        finalize.write_state(self.wiki)

    def test_changed_body_is_stamped_and_legacy_timestamp_removed(self):
        self.write("a.md", '---\ntype: Playbook\ntitle: A\ntimestamp: 2024-01-01\n---\n\n# A\n\nOld.\n')
        self.run_snapshot()
        p = self.wiki / "a.md"
        t = p.read_text(encoding="utf-8")
        p.write_text(t.replace("Old.", "New."), encoding="utf-8")
        changed = finalize.pass_provenance(
            self.wiki, "m", "2026-09-07T12:00:00Z", finalize.state_path_for(self.wiki))
        out = p.read_text(encoding="utf-8")
        self.assertIn(changed[0], str(p))
        self.assertIn("generated: { by: m, at: 2026-09-07T12:00:00Z }", out)
        self.assertNotIn("timestamp", out)
        self.assertTrue(out.endswith("\n") and not out.endswith("\n\n"))

    def test_new_page_absent_from_snapshot_is_stamped(self):
        self.write("a.md", "# A\n\nB.\n")
        self.run_snapshot()
        p = self.write("fresh.md", "# Fresh\n\nNew.\n")
        finalize.pass_provenance(
            self.wiki, "m", "2026-09-07T12:00:00Z", finalize.state_path_for(self.wiki))
        self.assertIn("generated: { by: m, at:", p.read_text(encoding="utf-8"))

    def test_frontmatter_only_change_preserves_prior_stamp(self):
        self.write("a.md", '---\ntype: P\ngenerated: { by: m, at: 2026-01-01T00:00:00Z }\n---\n\n# A\n\nB.\n')
        self.run_snapshot()
        p = self.wiki / "a.md"
        t = p.read_text(encoding="utf-8")
        p.write_text(t.replace("type: P", "type: Playbook"), encoding="utf-8")
        finalize.pass_provenance(
            self.wiki, "m", "2026-09-07T12:00:00Z", finalize.state_path_for(self.wiki))
        out = p.read_text(encoding="utf-8")
        self.assertIn("generated: { by: m, at: 2026-01-01T00:00:00Z }", out)
        self.assertIn("type: Playbook", out)

    def test_missing_snapshot_file_stamps_everything_conservatively(self):
        p = self.write("a.md", "# A\n\nB.\n")
        changed = finalize.pass_provenance(
            self.wiki, "m", "2026-09-07T12:00:00Z", finalize.state_path_for(self.wiki))
        self.assertIn("generated: { by: m, at:", p.read_text(encoding="utf-8"))
        self.assertTrue(changed)

For the rewritten CLI idempotence test, replace the old body with:

```python
class TestIdempotence(TempWiki):
    def snapshot(self):
        return {
            str(p.relative_to(self.wiki)): p.read_text(encoding="utf-8")
            for p in sorted(self.wiki.rglob("*.md"))
        }

    def run_cli(self, *extra):
        script = pathlib.Path(__file__).parent / "openwiki-finalize.py"
        return subprocess.run(
            [sys.executable, str(script)] + list(extra) + [str(self.wiki)],
            capture_output=True, text=True,
        )

    def full_run(self):
        snap = self.run_cli("--snapshot")
        self.assertEqual(snap.returncode, 0, snap.stderr)
        fin = self.run_cli()
        self.assertEqual(fin.returncode, 0, fin.stderr)

    def test_two_consecutive_full_runs_are_byte_identical(self):
        self.write("quickstart.md", "# Quickstart\n\nStart. See [arch](arch/overview.md).\n")
        self.write("arch/overview.md", "# Overview\n\nSee [gone](nope.md) and [qs](../quickstart.md).\n")
        self.full_run()
        after_one = self.snapshot()
        state = self.tmp / ".openwiki-run.json"
        self.assertFalse(state.exists(), "finalize mode must consume the state file")
        self.full_run()
        self.assertEqual(self.snapshot(), after_one)

    def test_exit_zero_on_missing_directory(self):
        script = pathlib.Path(__file__).parent / "openwiki-finalize.py"
        r = subprocess.run(
            [sys.executable, str(script), str(self.tmp / "does-not-exist")],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)

    def test_exit_zero_on_unreadable_content(self):
        self.write("ok.md", "# OK\n\nFine.\n")
        p = self.write("bad.md", "# Bad\n")
        p.write_bytes(b"\xff\xfe not utf-8 \xff")
        r = self.run_cli("--snapshot")
        self.assertEqual(r.returncode, 0)
        r = self.run_cli()
        self.assertEqual(r.returncode, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 scripts/test_finalize.py TestProvenancePass TestIdempotence`
Expected: FAIL — `pass_provenance` does not exist; the rewritten idempotence test fails on default-only semantics.

- [ ] **Step 3: Write the minimal implementation**

Add `import datetime` to the imports, then add above `main()`:

```python
def read_state(state_path):
    """Load the snapshot entries, or None when the file is missing/corrupt.

    A missing state file is conservative: the caller treats every page as
    changed, matching upstream's migration behavior. It self-corrects on the
    next run, which writes a fresh snapshot first.
    """
    try:
        with open(state_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def delete_state(state_path):
    try:
        state_path.unlink()
    except OSError:
        pass


def utc_now():
    """One shared run timestamp, UTC ISO 8601 with seconds, matching the
    `at` shape upstream writes (explicit UTC designator, no microseconds)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pass_provenance(wiki, actor, at, state_path):
    """Stamp generated provenance per the snapshot. Runs LAST, after index
    sync and link validation, exactly like upstream's `generated_provenance`
    operation. Skips reserved files and unreadable pages, never failing."""
    snapshot = read_state(state_path)
    entries = {e["page"]: e for e in snapshot} if snapshot is not None else None
    changed = []
    for path in markdown_files(wiki):
        if path.name in RESERVED:
            continue
        text = read_text_or_none(path)
        if text is None:
            continue
        rel = path.relative_to(wiki).as_posix()
        prior = entries.get(rel) if entries is not None else None
        body_changed = prior is None or prior.get("bodyHash") != body_hash(text)
        if body_changed:
            candidate = canonicalize_terminal(
                remove_field(set_generated_event(text, actor, at), "timestamp"))
        else:
            candidate = restore_generated_event(text, prior.get("generated"))
        updated = repair_frontmatter(candidate)
        if updated != text and write_text_or_skip(path, updated):
            changed.append(str(path))
    return changed
```

Rework `main()`:

```python
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wiki", nargs="?", default="openwiki")
    ap.add_argument("--snapshot", action="store_true",
                    help="prepare mode: migrate front matter, then write the provenance state file")
    ap.add_argument("--actor", default="openwiki-cc",
                    help="producer recorded in `generated` events (the command passes the running model id)")
    args = ap.parse_args()

    wiki = pathlib.Path(args.wiki)
    if not wiki.is_dir():
        print("openwiki-finalize: no such directory: %s (nothing to do)" % wiki)
        return 0

    if args.snapshot:
        fm, pages = write_state(wiki)
        print("openwiki-finalize: migrated %d file(s), snapshot %d page(s)" % (fm, pages))
        return 0

    idx = pass_indexes(wiki)
    lnk = pass_links(wiki)
    prov = pass_provenance(wiki, args.actor, utc_now(), state_path_for(wiki))
    delete_state(state_path_for(wiki))
    print("openwiki-finalize: indexes %d, links %d, provenance %d"
          % (len(idx), len(lnk), len(prov)))
    for path in idx + lnk + prov:
        print("  + %s" % path)
    return 0
```

Note: default mode **no longer runs `pass_frontmatter`** — migration moved to `--snapshot` time, mirroring upstream's prepare-before-authoring. `pass_frontmatter` stays as a library function (snapshot mode + existing unit tests use it).

- [ ] **Step 4: Run the full suite**

Run: `python3 scripts/test_finalize.py`
Expected: PASS. Count: 65 (Task 4) + 4 new pass tests + 1 actor-sanitization test = **70 total**. (The rewritten TestIdempotence keeps its pre-existing extra convergence tests, adapted to full-run sequences — do not delete them.)

- [ ] **Step 5: Refresh the shipped twin and commit**

```bash
cp scripts/openwiki-finalize.py .agents/skills/openwiki/scripts/openwiki-finalize.py
python3 scripts/test_finalize.py
git add scripts/openwiki-finalize.py scripts/test_finalize.py .agents/skills/openwiki/scripts/openwiki-finalize.py
git commit -m "feat(finalizer): [OW-6] stamp okf 0.2 generated provenance on finalize"
```

---

### Task 6: Gate regression test for the metadata refresh

Upstream #647 changed the contract: no-op runs refresh `.last-update.json`. The gate already excuses that file from dirty checks, but this was never asserted directly. One scenario proves it.

**Files:**
- Modify: `hooks/test_gate.sh`

**Interfaces:**
- Consumes: `hooks/openwiki-gate.sh` (unchanged)
- Produces: a passing "metadata refreshed on no-op" scenario. Task 11 consumes the suite.

- [ ] **Step 1: Add the scenario**

In `hooks/test_gate.sh`, after the `clean tree, HEAD unchanged` scenario, insert:

```sh
# metadata refreshed in place by a no-op run (upstream #647: updatedAt always
# refreshes) → still skip; only .last-update.json differs
printf '{"gitHead": "%s", "updatedAt": "2026-09-07T12:00:00.000Z", "command": "update", "model": "m", "status": "complete"}\n' "$H" > openwiki/.last-update.json
run "metadata refreshed on no-op" skip
```

- [ ] **Step 2: Run the gate suite**

Run: `sh hooks/test_gate.sh`
Expected: all 7 scenarios pass (`no metadata file`, `clean tree`, `metadata refreshed on no-op`, `dirty source file`, `untracked openwiki/ file`, `HEAD moved with source change`, `HEAD moved but only openwiki/ paths`).

- [ ] **Step 3: Commit**

```bash
git add hooks/test_gate.sh
git commit -m "test(gate): [OW-6] prove refreshed metadata still skips the hook"
```

---

### Task 7: `commands/wiki.md` — lifecycle rework (Steps 0, 1, 2, 3b, 4)

Everything except Step 3 (Task 8). The old Step 1 evidence block and the Step 2/4 tree-hash one-liners are replaced; the locate-the-script block moves from 3b to 2; Steps 0 and 4 learn the always-refresh semantics. The finalizer locate command itself is unchanged.

**Files:**
- Modify: `commands/wiki.md`

**Interfaces:**
- Consumes: the `--snapshot` / default CLI from Tasks 3–5
- Produces: reworked Steps 0–2, 3b–4, user-prompt line, notes reference. Task 9 mirrors these into SKILL.md.

- [ ] **Step 1: Rework Step 0 (no-op check + timestamp refresh)**

Replace the Step 0 section (lines 33–54) with:

```markdown
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
ts=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
jq --arg t "$ts" '.updatedAt = $t' openwiki/.last-update.json > openwiki/.last-update.json.tmp \
  && mv openwiki/.last-update.json.tmp openwiki/.last-update.json
```
```

- [ ] **Step 2: Slim Step 1 to update context and keep `.openwikiignore`**

Replace the Step 1 section (lines 56–114) with:

```markdown
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
```

Then keep the entire `.openwikiignore` block (`test -f .openwikiignore ...` through the
`{OUTPUT_LANGUAGE_INSTRUCTIONS}` line) **unchanged** — the read boundary applies to
the whole run (planner and workers), `[adapted]` retained from v0.3.3.

- [ ] **Step 3: Replace Step 2's tree hash with `--snapshot`**

Replace the Step 2 section (lines 116–122) with:

```markdown
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
```

- [ ] **Step 4: Rework Step 3b to finalize mode**

Replace the Step 3b section with:

```markdown
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
```

- [ ] **Step 5: Rework Step 4 to always write metadata**

Replace the Step 4 section (lines 467–494) with:

```markdown
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
```

- [ ] **Step 6: Fix the user-prompt line and the notes reference**

In `## The user prompt to act on`, update mode: replace "Update openwiki/.last-update.json
only when OpenWiki content changes." with "openwiki/.last-update.json is rewritten at the
end of every run, including no-ops (upstream #647)."

In `## Notes`, replace "the `v0.3.3` prompt above already forbids writing them" with
"the worker prompt above already forbids writing them" (the AGENTS.md/CLAUDE.md
prohibition moves into Task 8's `[adapted]` security line).

- [ ] **Step 7: Verify structure and commit**

Run:
```bash
grep -n '^## Step' commands/wiki.md
grep -n -- '--snapshot\|--actor\|\.openwiki-run\.json\|updatedAt.*refresh\|tree hash\|sha256sum openwiki' commands/wiki.md || true
```
Expected: Step 0, 1, 2, 3, 3b, 4 headings intact; `--snapshot`/`--actor`/`.openwiki-run.json` present in Steps 2/3b; no tree-hash `find ... sha256sum` one-liner remains (upstream #647's always-refresh makes the Step 2/4 hash comparison vestigial — it is removed, not moved).

```bash
git add commands/wiki.md
git commit -m "feat(command): [OW-6] rework lifecycle for v0.5.0 snapshot/provenance/metadata"
```

---

### Task 8: `commands/wiki.md` — Step 3 becomes planner + per-page workers

The core deliverable. The entire `### Init mode` + `### Update mode` block (today's ~300 lines: init/update system prompts, skeleton-critic and QA-verifier subagent waves, diagram discipline) is replaced by the two extracted prompts with named adaptations. Prompt bodies come from `build/` — **paste from `build/`, never from memory**.

**Files:**
- Modify: `commands/wiki.md`

**Interfaces:**
- Consumes: `build/planner-prompt.md`, `build/worker-prompt.md` (Task 1); lifecycle steps (Task 7)
- Produces: the new Step 3. Task 9 mirrors the content into SKILL.md; Task 11 runs against it.

- [ ] **Step 1: Regenerate the prompt bodies**

Run:
```bash
mkdir -p build
python3 scripts/extract-upstream-prompt.py --part planner > build/planner-prompt.md
python3 scripts/extract-upstream-prompt.py --part worker   > build/worker-prompt.md
wc -c build/planner-prompt.md build/worker-prompt.md
```
Expected: **2380** and **3003** bytes. Any other size → stop: upstream moved, or the extractor regressed.

- [ ] **Step 2: Replace the Step 3 header block**

Replace the `> Reproduced from OpenWiki v0.3.3 ...` attribution (4 lines) with:

```markdown
> Reproduced from OpenWiki `v0.5.0` `src/agent/repository-prompts.ts` —
> `createRepositoryPlannerPrompt` and `createRepositoryPagePrompt`. Extracted with
> `scripts/extract-upstream-prompt.py`, not retyped. Harness adaptations are marked
> `[adapted]`: (a) DeepAgents' virtual filesystem → native Read/Write/Edit/Glob/Grep/Bash
> on real repo paths; (b) upstream's durable page-job queue → the plan held in
> orchestrator context plus one host subagent per page (sequential writing on hosts
> without a subagent tool); (c) upstream's prepare/finalize harness → Step 2's
> `--snapshot` and Step 3b; (d) the claims subsystem is out of scope — workers write
> pages directly, with no submission tool, inspection tool, or claims guidance.
```

- [ ] **Step 3: Write Phase 1 (planning) from the planner prompt**

Replace everything from `### Init mode` through the end of `### Update mode` (just before `## Step 3b`) with two phases. Phase 1 is the decoded `build/planner-prompt.md` body with exactly these substitutions (mark each `[adapted]`):

1. `Your only output action is submit_plan. Do not write documentation and do not delegate work.` →
   `**[adapted]** Your only output for this phase is the plan itself, held in orchestrator context: a structured page list where every page has a path, title, purpose, seedPaths, relatedPages, and instructions. Do not write documentation pages in this phase.`
2. `${semanticContext}` → the additional user instruction from `$ARGUMENTS`, if any (else empty).
3. `${updateContext}` (update mode only) → the changed paths from Step 1, introduced as:
   `Changed paths since <gitHead>:` followed by the `git diff --name-only` list. **No** per-page baselines, **no** claims issues — `[adapted]`: a one-session port has a single update window; claims are out of scope.
4. `${view.wikiGoal ? ... : ""}` → empty. `[adapted]`: no repository-instructions equivalent.

Everything else in the planner body — the information-architecture guidance, the explore-before-planning discipline, `Init MUST include /openwiki/quickstart.md`, `An update with no required page edits and no deletions may submit pages: []`, `relatedPages`, `seedPaths` — is pasted **verbatim**.

Also handle `init` explicitly (upstream #699): when the mode is `init` but `openwiki/` already exists, treat it as fresh generation — existing pages are not preserved; workers rewrite everything.

- [ ] **Step 4: Write Phase 2 (page workers) from the worker prompt**

Phase 2 is the decoded `build/worker-prompt.md` body with exactly these edits (mark each `[adapted]`):

1. Ownership header (`You own exactly ${job.path}.` + Title / Purpose / Mode / Existing / seed / related / instructions lines) — pasted verbatim, with `${...}` filled per page from the Phase 1 plan.
2. `Output language: ${language}` line → dropped. `Write wiki prose and human-readable frontmatter values in ${language}.` → replaced by the sentence that survives it: keep `Keep code identifiers, file paths, commands, URLs, API names, and code blocks unchanged when translation would reduce technical accuracy.` `[adapted]`: no `--language` equivalent.
3. OKF MUST block (`type` / `title` / `description` / `tags`) and `Do not author generated, verified, sources, timestamp, or OpenWiki control fields; OpenWiki owns those. On update preserve unknown producer-defined frontmatter fields unless they are factually wrong.` — pasted **verbatim**.
4. The entire claims apparatus is stripped: the `After writing it, call submit_page ...` paragraph →
   `**[adapted]** After writing the page, you are done: the page file is the deliverable. There is no submission tool.` Remove the `${CLAIMS_SUBSTANCE_GUIDANCE}` and `${CLAIMS_RECONCILIATION_GUIDANCE}` blocks, the `This page currently owns ${job.existingClaimCount} Claim(s). ...` block, the `Every evidence resource MUST be a canonical repository URI ...` sentence, and the `If submission validation fails ...` sentence.
5. `Write only ${job.path}. Do not create, edit, or delete another wiki page.` — verbatim.
6. Update mode `Read the current page first. Preserve accurate unaffected content; change only what current repository evidence requires.` — verbatim.
7. Quickstart special case (`The complete planned page map is: ... task-routing map ...`) — verbatim, with the full planned page map interpolated.
8. Two `[adapted]` additions at the end of the worker prompt, each on its own line:
   - `**[adapted]** If you find an HTML comment starting with "openwiki: broken internal link", repair the href or restore the target page using the reason in the comment, then delete the comment. (Upstream v0.5.0 repository prompts no longer carry this; the port's Step 3b creates the annotations, so the port owns the repair loop.)`
   - `**[adapted]** Do not read secrets (.env, keys, credentials) and do not create or edit agent instruction files (AGENTS.md, CLAUDE.md) during the run. (Retained from the v0.3.3 port; the v0.5.0 repository prompts carry no security section and upstream enforces this in harness tooling the port does not have.)`

Then the dispatch paragraph (orchestrator voice, not prompt text):

```markdown
Dispatch: for every page in the Phase 1 plan, launch one subagent briefed with the Phase 2
worker prompt and that page's plan entry — path, title, purpose, seedPaths, relatedPages,
instructions, and (update mode) whether the page already exists. Launch independent pages
together. On hosts without a subagent tool, write the pages yourself one at a time under
the same worker discipline; the prompt text does not change.
```

- [ ] **Step 5: Verify fidelity mechanically**

Run:
```bash
grep -c 'submit_plan\|submit_page\|inspect_claims\|CLAIMS_\|claimsRequiringAttention' commands/wiki.md
for phrase in "You own exactly" "Write only" "Do not author generated, verified, sources, timestamp" \
  "Read the current page first. Preserve accurate unaffected content" \
  "The complete planned page map is" "Init MUST include /openwiki/quickstart.md" \
  "may submit pages: \[\]" "openwiki: broken internal link"; do
  printf '%s: %s\n' "$phrase" "$(grep -cF "$phrase" commands/wiki.md)"
done
grep -c '\[adapted\]' commands/wiki.md
```
Expected: the first `grep -c` prints `0` — every claims reference was stripped or converted. Every phrase count is non-zero. `[adapted]` count is non-zero and every occurrence sits on an adaptation named in Steps 3–4 above (spot-check by reading them).

Also verify no v0.3.3 prompt residue:
```bash
grep -n 'skeleton-critic\|skeleton critic\|question-finder\|answer-verifier\|Diagram discipline' commands/wiki.md || echo "clean"
```
Expected: `clean` — the deleted upstream subsystems leave no trace.

- [ ] **Step 6: Commit**

```bash
git add commands/wiki.md
git commit -m "feat(prompt): [OW-6] replace init/update prompt with v0.5.0 planner and page workers"
```

---

### Task 9: Mirror into `.agents/skills/openwiki/SKILL.md`

Same content as Tasks 7–8, in the skill's host-adapted vocabulary. The skill already marks its subagent section as host-conditional; the per-page worker model slots into that convention.

**Files:**
- Modify: `.agents/skills/openwiki/SKILL.md`

**Interfaces:**
- Consumes: Tasks 7–8 (`commands/wiki.md`)
- Produces: mirrored lifecycle + planner/worker prompt. Task 11 runs against it on non-Claude hosts.

- [ ] **Step 1: Mirror the lifecycle (Task 7)**

Apply to SKILL.md's Steps 0, 1, 2, 3b, 4 the same changes as Task 7, translated:
- The finalizer locate block keeps the skill's install-layout-proof lookup from PR #4 (it already searches beside the skill file); only the invocations change (`--snapshot` at Step 2, default + `--actor` at Step 3b).
- Step 0's refresh one-liner and Step 4's always-write JSON are host-agnostic shell/JSON — copy them unchanged.
- The user-prompt and notes/headless sections get the same one-line updates.

- [ ] **Step 2: Mirror the planner/worker Step 3 (Task 8)**

Paste the same Phase 1 / Phase 2 blocks. Only the dispatch paragraph differs — host vocabulary:

```markdown
Dispatch: for every page in the Phase 1 plan, launch one subagent briefed with the Phase 2
worker prompt and that page's plan entry. **Opencode/Claude Code only — Codex has no
subagent tool; skip subagent dispatch there and write the pages yourself one at a time
under the same worker discipline.** The prompt text does not change either way.
```

(Replacing the existing "opencode only" subagent caveat, which now covers per-page workers instead of critic/verifier waves.)

- [ ] **Step 3: Rework the context-management section**

SKILL.md's `## Context management — how to survive a large repo` (lines 34–52) was written for the monolithic v0.3.3 agent. Under the worker model each page is written in its own bounded context, so trim it to: plan in the orchestrator, one bounded worker context per page, never load the whole repo into one context. Keep it under 10 lines.

- [ ] **Step 4: Verify the mirror mechanically**

Run:
```bash
for phrase in "You own exactly" "Write only" "Do not author generated, verified, sources, timestamp" \
  "The complete planned page map is" "Init MUST include /openwiki/quickstart.md" \
  "openwiki: broken internal link" "openwiki_generated" "--snapshot" "--actor"; do
  printf 'cmd=%s skill=%s  %s\n' \
    "$(grep -cF "$phrase" commands/wiki.md)" \
    "$(grep -cF "$phrase" .agents/skills/openwiki/SKILL.md)" "$phrase"
done
diff <(grep -o '\${[a-zA-Z_]*}' commands/wiki.md | sort -u) \
     <(grep -o '\${[a-zA-Z_]*}' .agents/skills/openwiki/SKILL.md | sort -u) \
  && echo "placeholder vocab matches"
git diff --stat .opencode/ && test -z "$(git diff --stat .opencode/)" && echo "opencode routing untouched"
```
Expected: every phrase count is non-zero in **both** files; placeholder vocabularies match (the old `{PLACEHOLDER}` tokens are gone with the v0.3.3 prompt — the new files share whatever `${...}` tokens the worker briefs need); `.opencode/` has no diff.

- [ ] **Step 5: Commit**

```bash
git add .agents/skills/openwiki/SKILL.md
git commit -m "feat(skill): [OW-6] mirror v0.5.0 planner/worker port into the skill"
```

---

### Task 10: Move the lock to `v0.5.0` and rewrite the README

The fidelity section still claims v0.3.3, and the "Outstanding" paragraph tracks subagent prompts that upstream has since **deleted** — leaving it would claim missing work that no longer exists.

**Files:**
- Modify: `upstream.lock.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above
- Produces: a passing drift check (Task 11 runs against it)

- [ ] **Step 1: Rewrite the lock's file set**

In `upstream.lock.json`: delete the `src/agent/prompt.ts` and `src/agent/prompts/code.ts` entries (nothing the port reproduces remains in them). Keep `src/agent/index.ts`, `src/agent/utils.ts`, `src/okf/frontmatter.ts`, `src/okf/index-sync.ts` (leave their current v0.3.3 hashes in place — Step 2 refills them). Add these four entries with placeholder hashes:

```json
    "src/agent/repository-prompts.ts": {
      "sha256": "PENDING",
      "bytes": 0,
      "why": "createRepositoryPlannerPrompt and createRepositoryPagePrompt, reproduced in commands/wiki.md Step 3 and SKILL.md"
    },
    "src/agent/wiki-finalizer.ts": {
      "sha256": "PENDING",
      "bytes": 0,
      "why": "prepareWikiForAuthoring / finalizeWikiArtifacts lifecycle, reproduced by the two modes of scripts/openwiki-finalize.py"
    },
    "src/agent/wiki-link-validator.ts": {
      "sha256": "PENDING",
      "bytes": 0,
      "why": "validateWikiInternalLinks, reproduced by pass 3 of scripts/openwiki-finalize.py (tracked late — the v0.3.3 lock omitted it)"
    },
    "src/okf/generated-provenance.ts": {
      "sha256": "PENDING",
      "bytes": 0,
      "why": "body-hash snapshot and generated-event reconciliation, reproduced by the provenance pass of scripts/openwiki-finalize.py"
    }
```

Extend `notCovered` with:

```json
    "src/claims/*, src/okf/claim-sources.ts, src/okf/claims-verification.ts": "grounded claims with machine verification (claims store, evidence resolvers); a prompt-only port cannot provide them, so workers write pages directly",
    "src/generation/*": "durable resumable page jobs and .run.json persistence; the port runs in one session and plans updates from the last recorded gitHead",
    "src/mermaid/*": "mermaid fence parse-validation; Python's standard library cannot parse mermaid, and the repository prompts carry no diagram discipline to lose",
    "src/integrations/*": "upstream's own coding-agent installer; this port is the alternative distribution"
```

- [ ] **Step 2: Record the real hashes**

Run:
```bash
GITHUB_TOKEN=$(gh auth token) sh scripts/check-upstream-drift.sh --update
git diff upstream.lock.json
```
Expected: `trackedRef` and `trackedVersion` become `v0.5.0`, all eight files carry real digests, no `PENDING` remains, and review the diff — every added file must be one Task 8 or Tasks 2–5 reproduces.

- [ ] **Step 3: Verify the check now passes**

Run:
```bash
GITHUB_TOKEN=$(gh auth token) sh scripts/check-upstream-drift.sh; echo "EXIT=$?"
```
Expected: "No drift. The port is current with `v0.5.0`." and `EXIT=0`.

- [ ] **Step 4: Rewrite the README fidelity section**

Replace the `**Tracked against upstream \`v0.3.3\`...**` paragraph with:

```markdown
**Tracked against upstream `v0.5.0`, repository output mode.** The reproduced surface is the
repository planner and per-page worker prompts (`src/agent/repository-prompts.ts`), the run
lifecycle (`src/agent/index.ts`), the prepare/finalize harness (`src/agent/wiki-finalizer.ts`),
link validation (`src/agent/wiki-link-validator.ts`), the git evidence, no-op, and metadata
logic (`src/agent/utils.ts`), and the OKF machinery (`src/okf/frontmatter.ts`,
`src/okf/index-sync.ts`, `src/okf/generated-provenance.ts`). Prompt text is extracted from
upstream with [`scripts/extract-upstream-prompt.py`](scripts/extract-upstream-prompt.py)
rather than retyped, so transcription drift is not possible.
```

Extend the `notCovered` table with:

```markdown
| `src/claims/*`, `src/okf/claim-sources.ts`, `src/okf/claims-verification.ts` | Grounded claims with machine verification. A prompt-only port cannot provide a claims store or evidence resolvers, so workers write pages directly. |
| `src/generation/*` | Durable resumable page jobs. The port runs in one session; update planning uses the last recorded `gitHead` as its only window. |
| `src/mermaid/*` | Mermaid fence parse-validation. Python's standard library cannot parse mermaid, and the v0.5.0 repository prompts carry no diagram discipline to lose. |
| `src/integrations/*` | Upstream's own coding-agent installer. This port *is* the alternative distribution. |
```

Replace the `**Outstanding.** ... are reproduced.` paragraph (the skeleton-critic / QA-verifier tracking note) with:

```markdown
**Removed upstream, removed here.** The `skeleton_critic.ts` and `wiki_qa_subagents.ts`
subsystems that the v0.3.3 port adapted into critic/verifier subagent waves were deleted
upstream in v0.5.0 along with the monolithic prompt. The per-page worker model replaces
them; `src/agent/wiki-link-validator.ts`, previously reproduced but untracked, is now
tracked in [`upstream.lock.json`](upstream.lock.json).
```

- [ ] **Step 5: Update the OKF and layout sections**

Replace `### OKF front matter`'s opening (`From \`v0.3.3\` ... OKF v0.1 ...`) with:

```markdown
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
```

Keep the two `_sidebar.md` upgrade notes that follow (still true). In `## Repository layout`'s `scripts/` listing, update the two changed lines to:

```
  extract-upstream-prompt.py  # pulls planner/worker prompt text from upstream, verbatim
  openwiki-finalize.py     # Step 2 --snapshot (migrate + body-hash state), Step 3b finalize (indexes, links, generated provenance)
```

- [ ] **Step 6: Commit**

```bash
git add upstream.lock.json README.md
git commit -m "docs(reprint): [OW-6] track v0.5.0 and rewrite fidelity section"
```

---

### Task 11: Integration verification and PR

Unit tests cannot exercise the snapshot/provenance/no-op interaction. This task does. The real command run happens here, on this repo, following the re-ported `commands/wiki.md` — the first full crossing of the new planner/worker path.

**Files:**
- No source changes expected. Fix whatever breaks.

**Interfaces:**
- Consumes: everything
- Produces: verified working software, the PR, issue #6 closed

- [ ] **Step 1: Confirm the existing suites still pass**

Run:
```bash
python3 scripts/test_finalize.py
sh hooks/test_gate.sh
GITHUB_TOKEN=$(gh auth token) sh scripts/check-upstream-drift.sh; echo "drift EXIT=$?"
```
Expected: 70 finalizer tests pass, all 7 gate scenarios pass, drift exits 0.

- [ ] **Step 2: Run the real command against this repo**

Run `/openwiki:wiki update` (in Claude Code, from the repo root; on other hosts follow `.agents/skills/openwiki/SKILL.md` update mode).

Expected: the run plans under the new planner discipline, writes/updates pages via worker briefs, runs `--snapshot` at Step 2 and finalize at Step 3b, and finishes with metadata written. Pages gain `generated` stamps, the root index shows `okf_version: "0.2"`, existing prose survives, `.openwiki-run.json` is gone from the repo root, `.last-update.json` keeps its five-field shape.

- [ ] **Step 3: Prove the content no-op contract**

Immediately run `/openwiki:wiki update` again.

Expected: the second run changes no wiki file (`git status --short openwiki/` shows at most `openwiki/.last-update.json` — its `updatedAt` refreshes by design), and its own Step 0 reports the wiki already current on the run after that. If a wiki file changed, provenance is not idempotent against real content — fix that first, because `hooks/openwiki-gate.sh` and every user's CI depend on it.

- [ ] **Step 4: Check the generated wiki for broken links**

Run:
```bash
grep -rn "openwiki: broken internal link" openwiki/ || echo "no broken links"
```
Expected: no broken links. Any marker is a real defect — repair the target or the href per the worker prompt's `[adapted]` repair loop, then re-run Step 3.

- [ ] **Step 5: Verify the wiki documents the new mechanism**

```bash
grep -n "planner\|per-page worker\|Step 3b\|generated.*provenance\|OKF v0.2\|openwiki-run" openwiki/architecture.md openwiki/*.md 2>/dev/null | head -20
```
Expected: the architecture page explains the planner/worker model, what Step 3b does, why stamps advance only on body change, and that OKF v0.2 front matter is now emitted. If the run missed it, add it by hand — this is the page a future maintainer reads before touching the finalizer.

- [ ] **Step 6: Commit the regenerated wiki**

```bash
git add openwiki/
git commit -m "docs(openwiki): [OW-6] migrate wiki to OKF v0.2 under the planner/worker prompt"
```

- [ ] **Step 7: Open the PR**

```bash
git push -u origin feature/report-v0.5.0
gh pr create --repo icampana/openwiki-cc --base main \
  --title "feat: re-port to upstream OpenWiki v0.5.0" \
  --body "Implements docs/superpowers/specs/2026-09-07-openwiki-report-v050-design.md. Closes #6.

Upstream replaced the monolithic init/update prompt with a planner + per-page-worker
model and moved OKF to v0.2 with code-owned generated provenance; the port follows
both. Claims, durable resumability, mermaid parse-validation, and the integrations
installer are deliberately out of scope and recorded in upstream.lock.json."
```

Expected: the `Upstream drift` workflow runs on the PR and **succeeds**. If it fails, the lock and the port disagree — fix before merging.

---

## Notes for the executor

**The prompt text is the deliverable, and it is small but parameterized.** Tasks 1 and 8 handle ~5.4 KB of extracted prose with `${...}` interpolation points. Do not summarize, reflow, or "improve" the extracted bodies. The only permitted edits are the `[adapted]` substitutions named in Task 8 Steps 3–4. `scripts/check-upstream-drift.sh` guards the source by hash; nothing guards your transcription, so paste from `build/`, never from memory.

**The default-mode behavior change is the likeliest surprise.** After Task 5, running the finalizer without `--snapshot` no longer backfills front matter — it stamps conservatively instead. That is faithful to upstream's prepare/finalize split, not a bug. The command file (Tasks 7–9) always runs both modes in order.

**Ignore one stale assertion on sight.** If any surviving test still expects `okf_version: "0.1"`, a 4-field `.last-update.json`, or a `.last-update.json`-untouched no-op, it predates this re-port: update the test, not the code.
