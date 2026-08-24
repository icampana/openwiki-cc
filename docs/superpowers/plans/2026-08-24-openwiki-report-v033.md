# OpenWiki v0.3.3 Re-port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `openwiki-cc` from reproducing upstream OpenWiki `0.0.4` to reproducing `v0.3.3` repository output mode, including OKF front matter, deterministic indexes, and link integrity.

**Architecture:** The port has no runtime — it is prompt files plus small scripts. The v0.3.3 system prompts are extracted programmatically from upstream's `code.ts` rather than retyped. Behavior upstream moved into harness code (`src/okf/`, `wiki-link-validator.ts`) becomes one deterministic post-run script, `scripts/openwiki-finalize.py`, invoked as a new Step 3b in `commands/wiki.md`.

**Tech Stack:** POSIX `sh` + `jq` (existing scripts), Python 3 standard library only (new finalizer), Markdown prompt files, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-24-openwiki-report-v033-design.md`

## Global Constraints

- Upstream reference is the tag `v0.3.3` — never `main`. Files differ between them; `wiki-finalizer.ts` exists only on `main`.
- Python 3 standard library only. No `pip install`, no `import yaml`.
- `scripts/openwiki-finalize.py` **always exits 0**. It must never fail a documentation run.
- The finalizer writes only inside the wiki directory it is given.
- The finalizer must be **idempotent**: a second consecutive run produces byte-identical output.
- Reserved OKF filenames, never given concept front matter: `index.md`, `log.md`.
- The generated-metadata flag is exactly `openwiki_generated` (upstream `OPENWIKI_GENERATED_FIELD`).
- Fallback front matter `type` value is exactly `Reference`.
- Broken-link comments start with exactly `openwiki: broken internal link`.
- `.last-update.json` `status` values are `complete` | `interrupted`, default `complete`.
- `commands/wiki.md` is authoritative; `SKILL.md` mirrors it. `.opencode/commands/wiki.md` must not change.
- Harness adaptations in reproduced prompt text are marked `[adapted]` inline, matching existing convention.

---

### Task 1: Upstream prompt extractor

Retyping 39 KB of prompt text would introduce silent transcription drift — the exact failure the drift check exists to catch. Extract it instead, with a committed script so the next re-port is repeatable.

**Files:**
- Create: `scripts/extract-upstream-prompt.py`
- Create: `build/prompt-init.md` (generated, git-ignored)
- Create: `build/prompt-update.md` (generated, git-ignored)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing
- Produces: `scripts/extract-upstream-prompt.py --ref <tag> --command <init|update>` writes the unescaped prompt body to stdout. Task 6 consumes its output.

- [ ] **Step 1: Write the extractor**

```python
#!/usr/bin/env python3
"""Extract a CODE_SYSTEM_PROMPTS template verbatim from upstream OpenWiki.

The port reproduces upstream prompt text. Retyping it introduces drift, so pull
it from source instead. Upstream stores the prompts as TypeScript template
literals, so backticks and dollar signs arrive escaped and must be unescaped.
"""
import argparse
import json
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/langchain-ai/openwiki/{ref}/src/agent/prompts/code.ts"


def fetch(ref: str) -> str:
    with urllib.request.urlopen(RAW.format(ref=ref)) as r:
        return r.read().decode("utf-8")


def extract(source: str, command: str) -> str:
    """Return the raw template literal body for CODE_SYSTEM_PROMPTS[command]."""
    anchor = source.index("CODE_SYSTEM_PROMPTS")
    needle = "\n  %s: `" % command
    at = source.find(needle, anchor)
    if at == -1:
        raise SystemExit("command not found in CODE_SYSTEM_PROMPTS: %s" % command)
    start = at + len(needle)
    i = start
    while i < len(source):
        if source[i] == "\\":
            i += 2
            continue
        if source[i] == "`":
            break
        i += 1
    else:
        raise SystemExit("unterminated template literal for %s" % command)
    return source[start:i]


def unescape(body: str) -> str:
    """Undo TypeScript template-literal escaping. Order matters: backslash last."""
    return body.replace("\\`", "`").replace("\\$", "$").replace("\\\\", "\\")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="v0.3.3")
    ap.add_argument("--command", required=True, choices=["chat", "init", "update"])
    ap.add_argument("--placeholders", action="store_true",
                    help="list the {PLACEHOLDER} tokens instead of the body")
    args = ap.parse_args()

    body = unescape(extract(fetch(args.ref), args.command))
    if args.placeholders:
        import re
        found = sorted(set(re.findall(r"\{[A-Z_]+\}", body)))
        print(json.dumps(found, indent=2))
        return 0
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it extracts the expected sizes**

Run:
```bash
mkdir -p build
python3 scripts/extract-upstream-prompt.py --command init   > build/prompt-init.md
python3 scripts/extract-upstream-prompt.py --command update > build/prompt-update.md
wc -c build/prompt-init.md build/prompt-update.md
```

Expected: `prompt-init.md` is **12,754** bytes and `prompt-update.md` is **26,849** bytes.
These are measured, not estimated. A different number means upstream retagged `v0.3.3` or the
unescaping changed — stop and investigate rather than proceeding.

- [ ] **Step 3: Verify placeholders and unescaping**

Run:
```bash
python3 scripts/extract-upstream-prompt.py --command update --placeholders
grep -c '\\`' build/prompt-update.md
```

Expected: the placeholder list is exactly
`["{DISCOVERY_INSTRUCTION}", "{GIT_HISTORY_HINT}", "{OPENWIKIIGNORE_INSTRUCTIONS}", "{OUTPUT_LANGUAGE_INSTRUCTIONS}"]`.
The `grep -c` prints `0` — no escaped backticks survive.

- [ ] **Step 4: Ignore the generated artifacts**

Append to `.gitignore`:
```
# Generated by scripts/extract-upstream-prompt.py; not the deliverable.
build/
```

- [ ] **Step 5: Commit**

```bash
git add scripts/extract-upstream-prompt.py .gitignore
git commit -m "feat(reprint): [OW-3] extract upstream prompts programmatically"
```

---

### Task 2: Finalizer pass 1 — OKF front matter

**Files:**
- Create: `scripts/openwiki-finalize.py`
- Create: `scripts/test_finalize.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `RESERVED: set[str]`, `GENERATED_FIELD: str`, `FALLBACK_TYPE: str`
  - `split_frontmatter(text: str) -> tuple[str | None, str]` — returns `(fields_text, body)`; `fields_text` is `None` when no block is present
  - `parse_fields(fields_text: str) -> dict[str, str]` — top-level keys only
  - `derive_title(body: str) -> str | None`
  - `derive_description(body: str) -> str | None`
  - `ensure_frontmatter(text: str, fallback_title: str) -> str`
  - `pass_frontmatter(wiki: pathlib.Path) -> list[str]` — returns changed file paths

- [ ] **Step 1: Write the failing tests**

```python
#!/usr/bin/env python3
"""Tests for openwiki-finalize.py. Run: python3 scripts/test_finalize.py"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import importlib.util

# The script name has a hyphen, so it cannot be imported normally.
_SCRIPT = pathlib.Path(__file__).parent / "openwiki-finalize.py"
_spec = importlib.util.spec_from_file_location("finalize", _SCRIPT)
finalize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(finalize)


class TempWiki(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.wiki = self.tmp / "openwiki"
        self.wiki.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, rel, text):
        p = self.wiki / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


class TestFrontmatter(TempWiki):
    def test_missing_frontmatter_is_backfilled_and_flagged(self):
        p = self.write("quickstart.md", "# My Repo\n\nA thing that does stuff.\n")
        finalize.pass_frontmatter(self.wiki)
        out = p.read_text(encoding="utf-8")
        self.assertTrue(out.startswith("---\n"))
        fields, body = finalize.split_frontmatter(out)
        parsed = finalize.parse_fields(fields)
        self.assertEqual(parsed["type"], "Reference")
        self.assertEqual(parsed["title"], "My Repo")
        self.assertEqual(parsed["description"], "A thing that does stuff.")
        self.assertEqual(parsed["openwiki_generated"], "true")
        self.assertIn("# My Repo", body)

    def test_valid_frontmatter_is_left_byte_identical(self):
        original = '---\ntype: Playbook\ntitle: Kept\n---\n\n# Kept\n\nBody.\n'
        p = self.write("kept.md", original)
        finalize.pass_frontmatter(self.wiki)
        self.assertEqual(p.read_text(encoding="utf-8"), original)

    def test_unknown_extension_fields_survive(self):
        original = (
            "---\ntype: Metric\nowner_team: platform\n"
            "custom_nested:\n  a: 1\n---\n\n# M\n\nBody.\n"
        )
        p = self.write("metric.md", original)
        finalize.pass_frontmatter(self.wiki)
        out = p.read_text(encoding="utf-8")
        self.assertIn("owner_team: platform", out)
        self.assertIn("custom_nested:", out)
        self.assertIn("  a: 1", out)

    def test_frontmatter_without_type_gains_type_and_keeps_fields(self):
        p = self.write("notype.md", "---\ntitle: Existing\nowner: me\n---\n\n# H\n\nB.\n")
        finalize.pass_frontmatter(self.wiki)
        out = p.read_text(encoding="utf-8")
        parsed = finalize.parse_fields(finalize.split_frontmatter(out)[0])
        self.assertEqual(parsed["type"], "Reference")
        self.assertEqual(parsed["title"], "Existing")
        self.assertEqual(parsed["owner"], "me")

    def test_reserved_files_are_never_given_concept_frontmatter(self):
        i = self.write("index.md", "# Index\n\n- [a](a.md)\n")
        l = self.write("log.md", "# Log\n\nentry\n")
        finalize.pass_frontmatter(self.wiki)
        self.assertNotIn("type:", i.read_text(encoding="utf-8"))
        self.assertNotIn("type:", l.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 scripts/test_finalize.py`
Expected: FAIL — `openwiki-finalize.py` does not exist, so the loader raises `FileNotFoundError`.

- [ ] **Step 3: Write the minimal implementation**

```python
#!/usr/bin/env python3
"""Deterministic post-run pass over a generated OpenWiki directory.

Upstream OpenWiki does this work in harness code: src/okf/frontmatter.ts
(front matter), src/okf/index-sync.ts (directory indexes), and
src/agent/wiki-link-validator.ts (link integrity). This port has no runtime, so
the same work runs here as an explicit command step.

Two rules govern everything below:

1. Never fail. A finalizer that can break a documentation run is worse than one
   that skips a file, so this always exits 0.
2. Be idempotent. This runs inside the Step 2/Step 4 snapshot window, so any
   change on a no-op run would rewrite .last-update.json every time and defeat
   both the in-agent no-op check and hooks/openwiki-gate.sh.
"""
import argparse
import pathlib
import sys

RESERVED = {"index.md", "log.md"}
GENERATED_FIELD = "openwiki_generated"
FALLBACK_TYPE = "Reference"


def split_frontmatter(text):
    """Split leading YAML front matter. Returns (fields_text, body).

    fields_text is None when there is no well-formed block, in which case body
    is the untouched input.
    """
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 3)
    if end == -1:
        return None, text
    return text[4:end + 1], text[end + 5:]


def parse_fields(fields_text):
    """Parse top-level scalar keys. Nested and comment lines are left opaque.

    This is deliberately not a YAML parser: OKF front matter is a flat block,
    and anything we do not understand must survive untouched rather than be
    reserialized.
    """
    out = {}
    for line in (fields_text or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip()
    return out


def derive_title(body):
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
    return None


FENCE = "`" * 3  # written this way so the literal cannot close a Markdown fence


def derive_description(body):
    """First prose line: not a heading, list, fence, or table row."""
    in_fence = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(FENCE):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        if stripped[0] in "#-*>|":
            continue
        return stripped
    return None


def _escape(value):
    """Quote a scalar when YAML would otherwise misread it."""
    if value and (value[0] in "[{&*!|>%@`\"'" or ": " in value or value.endswith(":")):
        return '"%s"' % value.replace('"', '\\"')
    return value


def ensure_frontmatter(text, fallback_title):
    """Return text guaranteed to carry an OKF block with a `type`.

    Existing blocks are preserved verbatim; only a missing `type` is injected.
    Rewriting a valid block would violate OKF's round-trip requirement for
    producer-defined fields.
    """
    fields_text, body = split_frontmatter(text)

    if fields_text is not None:
        if parse_fields(fields_text).get("type"):
            return text
        injected = "type: %s\n%s: true\n" % (FALLBACK_TYPE, GENERATED_FIELD)
        return "---\n" + injected + fields_text + "---\n" + body

    title = derive_title(body) or fallback_title
    description = derive_description(body)
    lines = ["---", "type: %s" % FALLBACK_TYPE, "title: %s" % _escape(title)]
    if description:
        lines.append("description: %s" % _escape(description))
    lines += ["%s: true" % GENERATED_FIELD, "---", ""]
    return "\n".join(lines) + "\n" + body.lstrip("\n")


def markdown_files(wiki):
    return sorted(p for p in wiki.rglob("*.md") if p.is_file())


def pass_frontmatter(wiki):
    """Backfill OKF front matter. Returns the list of changed paths."""
    changed = []
    for path in markdown_files(wiki):
        if path.name in RESERVED:
            continue
        text = path.read_text(encoding="utf-8")
        updated = ensure_frontmatter(text, path.stem.replace("-", " ").title())
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed.append(str(path))
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wiki", nargs="?", default="openwiki")
    args = ap.parse_args()

    wiki = pathlib.Path(args.wiki)
    if not wiki.is_dir():
        print("openwiki-finalize: no such directory: %s (nothing to do)" % wiki)
        return 0

    changed = pass_frontmatter(wiki)
    print("openwiki-finalize: front matter written to %d file(s)" % len(changed))
    for path in changed:
        print("  + %s" % path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never fail a documentation run
        print("openwiki-finalize: skipped after error: %r" % exc)
        sys.exit(0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 scripts/test_finalize.py`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
chmod +x scripts/openwiki-finalize.py
git add scripts/openwiki-finalize.py scripts/test_finalize.py
git commit -m "feat(okf): [OW-3] backfill OKF front matter deterministically"
```

---

### Task 3: Finalizer pass 2 — deterministic directory indexes

Upstream's `synchronizeWikiIndexes` owns `index.md` outright; the prompt forbids the model from touching these files.

**Files:**
- Modify: `scripts/openwiki-finalize.py`
- Modify: `scripts/test_finalize.py`

**Interfaces:**
- Consumes: `RESERVED`, `split_frontmatter`, `derive_title`, `markdown_files` from Task 2
- Produces:
  - `index_label(path: pathlib.Path) -> str`
  - `render_index(directory: pathlib.Path, wiki: pathlib.Path) -> str`
  - `pass_indexes(wiki: pathlib.Path) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test_finalize.py` before the `__main__` block:

```python
class TestIndexes(TempWiki):
    def test_root_index_carries_okf_version_only(self):
        self.write("quickstart.md", "# Quickstart\n\nStart here.\n")
        finalize.pass_indexes(self.wiki)
        out = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertTrue(out.startswith('---\nokf_version: "0.1"\n---\n'))
        self.assertIn("- [Quickstart](quickstart.md)", out)

    def test_subdirectory_index_has_no_frontmatter(self):
        self.write("arch/overview.md", "# Overview\n\nText.\n")
        finalize.pass_indexes(self.wiki)
        out = (self.wiki / "arch" / "index.md").read_text(encoding="utf-8")
        self.assertFalse(out.startswith("---"))
        self.assertIn("- [Overview](overview.md)", out)

    def test_root_index_links_subdirectories(self):
        self.write("arch/overview.md", "# Overview\n\nText.\n")
        finalize.pass_indexes(self.wiki)
        out = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertIn("- [Arch](arch/index.md)", out)

    def test_label_falls_back_to_filename_when_no_h1(self):
        self.write("no-heading.md", "Just prose, no heading.\n")
        finalize.pass_indexes(self.wiki)
        out = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertIn("- [No Heading](no-heading.md)", out)

    def test_entries_are_sorted_for_stability(self):
        self.write("b.md", "# Bee\n\nx\n")
        self.write("a.md", "# Ay\n\nx\n")
        finalize.pass_indexes(self.wiki)
        out = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertLess(out.index("(a.md)"), out.index("(b.md)"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 scripts/test_finalize.py -k TestIndexes`
Expected: FAIL with `AttributeError: module 'finalize' has no attribute 'pass_indexes'`.

- [ ] **Step 3: Write the minimal implementation**

Add to `scripts/openwiki-finalize.py` above `main`:

```python
ROOT_INDEX_FRONTMATTER = '---\nokf_version: "0.1"\n---\n\n'


def index_label(path):
    """Human label for an index entry: the page's H1, else its filename."""
    try:
        _, body = split_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return path.stem.replace("-", " ").title()
    return derive_title(body) or path.stem.replace("-", " ").title()


def _escape_label(label):
    return label.replace("[", "\\[").replace("]", "\\]")


def render_index(directory, wiki):
    """Render a directory index. Deterministic: entries are sorted by href."""
    entries = []
    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        if child.is_dir():
            if any(child.rglob("*.md")):
                entries.append((
                    "%s/index.md" % child.name,
                    child.name.replace("-", " ").title(),
                ))
        elif child.suffix == ".md" and child.name not in RESERVED:
            entries.append((child.name, index_label(child)))

    is_root = directory.resolve() == wiki.resolve()
    title = "OpenWiki" if is_root else directory.name.replace("-", " ").title()
    lines = ["# %s" % title, ""]
    lines += ["- [%s](%s)" % (_escape_label(label), href)
              for href, label in sorted(entries)]
    body = "\n".join(lines) + "\n"
    return (ROOT_INDEX_FRONTMATTER + body) if is_root else body


def pass_indexes(wiki):
    """Generate index.md for the wiki root and every directory holding pages."""
    changed = []
    directories = [wiki] + [d for d in sorted(wiki.rglob("*")) if d.is_dir()]
    for directory in directories:
        if not any(directory.rglob("*.md")):
            continue
        target = directory / "index.md"
        rendered = render_index(directory, wiki)
        existing = target.read_text(encoding="utf-8") if target.exists() else None
        if existing != rendered:
            target.write_text(rendered, encoding="utf-8")
            changed.append(str(target))
    return changed
```

Then extend `main`, replacing its `changed = pass_frontmatter(wiki)` block with:

```python
    fm = pass_frontmatter(wiki)
    idx = pass_indexes(wiki)
    print("openwiki-finalize: front matter %d, indexes %d" % (len(fm), len(idx)))
    for path in fm + idx:
        print("  + %s" % path)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 scripts/test_finalize.py`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add scripts/openwiki-finalize.py scripts/test_finalize.py
git commit -m "feat(okf): [OW-3] generate directory indexes deterministically"
```

---

### Task 4: Finalizer pass 3 — link integrity

Broken links are **annotated, never removed**, so a run always completes and a later run self-corrects. Idempotence comes from stripping all existing markers before re-adding, which also means a link someone fixed loses its marker automatically.

**Files:**
- Modify: `scripts/openwiki-finalize.py`
- Modify: `scripts/test_finalize.py`

**Interfaces:**
- Consumes: `split_frontmatter`, `markdown_files` from Task 2
- Produces:
  - `slugify(heading: str) -> str`
  - `headings(text: str) -> set[str]`
  - `strip_markers(text: str) -> str`
  - `pass_links(wiki: pathlib.Path) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test_finalize.py` before the `__main__` block:

```python
MARKER = "openwiki: broken internal link"


class TestLinks(TempWiki):
    def test_broken_link_is_annotated_and_kept(self):
        p = self.write("a.md", "# A\n\nSee [gone](missing.md).\n")
        finalize.pass_links(self.wiki)
        out = p.read_text(encoding="utf-8")
        self.assertIn("[gone](missing.md)", out)   # link preserved
        self.assertIn(MARKER, out)
        self.assertIn("missing.md", out.split(MARKER)[1])

    def test_valid_link_is_not_annotated(self):
        self.write("b.md", "# B\n\nBody.\n")
        p = self.write("a.md", "# A\n\nSee [b](b.md).\n")
        finalize.pass_links(self.wiki)
        self.assertNotIn(MARKER, p.read_text(encoding="utf-8"))

    def test_valid_anchor_passes_and_missing_anchor_fails(self):
        self.write("b.md", "# B\n\n## Real Section\n\nBody.\n")
        ok = self.write("ok.md", "# OK\n\n[x](b.md#real-section)\n")
        bad = self.write("bad.md", "# Bad\n\n[x](b.md#nope)\n")
        finalize.pass_links(self.wiki)
        self.assertNotIn(MARKER, ok.read_text(encoding="utf-8"))
        self.assertIn(MARKER, bad.read_text(encoding="utf-8"))

    def test_external_and_absolute_links_are_ignored(self):
        p = self.write("a.md", "# A\n\n[x](https://example.com) [y](/abs/path)\n")
        finalize.pass_links(self.wiki)
        self.assertNotIn(MARKER, p.read_text(encoding="utf-8"))

    def test_link_outside_wiki_that_exists_is_valid(self):
        (self.tmp / "README.md").write_text("# Readme\n", encoding="utf-8")
        p = self.write("a.md", "# A\n\n[readme](../README.md)\n")
        finalize.pass_links(self.wiki)
        self.assertNotIn(MARKER, p.read_text(encoding="utf-8"))

    def test_second_run_does_not_duplicate_marker(self):
        p = self.write("a.md", "# A\n\n[gone](missing.md)\n")
        finalize.pass_links(self.wiki)
        first = p.read_text(encoding="utf-8")
        finalize.pass_links(self.wiki)
        self.assertEqual(p.read_text(encoding="utf-8"), first)
        self.assertEqual(first.count(MARKER), 1)

    def test_marker_disappears_once_target_exists(self):
        p = self.write("a.md", "# A\n\n[b](b.md)\n")
        finalize.pass_links(self.wiki)
        self.assertIn(MARKER, p.read_text(encoding="utf-8"))
        self.write("b.md", "# B\n\nBody.\n")
        finalize.pass_links(self.wiki)
        self.assertNotIn(MARKER, p.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 scripts/test_finalize.py -k TestLinks`
Expected: FAIL with `AttributeError: module 'finalize' has no attribute 'pass_links'`.

- [ ] **Step 3: Write the minimal implementation**

Add `import re` to the imports, then add above `main`:

```python
MARKER_PREFIX = "openwiki: broken internal link"
LINK_RE = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<href>[^)\s]+)\)")
# No DOTALL: markers are single-line, and spanning lines could eat real content.
MARKER_RE = re.compile(r"[ \t]*<!--\s*%s[^\n]*?-->\n?" % re.escape(MARKER_PREFIX))
ATX_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.M)


def slugify(heading):
    """GitHub-style anchor slug."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s]+", "-", text).strip("-")


def headings(text):
    _, body = split_frontmatter(text)
    return {slugify(m.group(2)) for m in ATX_RE.finditer(body)}


def strip_markers(text):
    """Remove previously written markers so re-running cannot stack them."""
    return MARKER_RE.sub("", text)


def _is_internal(href):
    if href.startswith(("http://", "https://", "mailto:", "//", "#", "/")):
        return False
    return True


def pass_links(wiki):
    """Annotate broken relative links and anchors. Returns changed paths."""
    changed = []
    for path in markdown_files(wiki):
        original = path.read_text(encoding="utf-8")
        text = strip_markers(original)
        had_markers = text != original
        found_problem = False
        out_lines = []
        # split("\n"), not splitlines(): split is lossless on trailing
        # newlines, so an untouched file round-trips byte-identically.
        for line in text.split("\n"):
            out_lines.append(line)
            problems = []
            for match in LINK_RE.finditer(line):
                href = match.group("href")
                if not _is_internal(href):
                    continue
                target_part, _, anchor = href.partition("#")
                if not target_part:
                    continue
                target = (path.parent / target_part).resolve()
                if not target.exists():
                    problems.append((href, "target not found"))
                    continue
                if anchor and target.suffix == ".md":
                    if slugify(anchor) not in headings(
                        target.read_text(encoding="utf-8")
                    ):
                        problems.append((href, "heading anchor not found"))
            indent = line[: len(line) - len(line.lstrip())]
            for href, reason in problems:
                found_problem = True
                out_lines.append(
                    "%s<!-- %s: %s - %s -->" % (indent, MARKER_PREFIX, href, reason)
                )
        # A file with nothing to say is left completely alone. Normalizing it
        # would count as a change and break the no-op contract.
        if not found_problem and not had_markers:
            continue
        updated = "\n".join(out_lines)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(str(path))
    return changed
```

Then extend `main` to run the third pass, replacing the print block with:

```python
    fm = pass_frontmatter(wiki)
    idx = pass_indexes(wiki)
    lnk = pass_links(wiki)
    print("openwiki-finalize: front matter %d, indexes %d, links %d"
          % (len(fm), len(idx), len(lnk)))
    for path in fm + idx + lnk:
        print("  + %s" % path)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 scripts/test_finalize.py`
Expected: PASS, 17 tests.

- [ ] **Step 5: Commit**

```bash
git add scripts/openwiki-finalize.py scripts/test_finalize.py
git commit -m "feat(okf): [OW-3] annotate broken internal links without removing them"
```

---

### Task 5: End-to-end idempotence

The single most important property in the spec. All three passes now run together, and pass 2 writes files that pass 3 then reads — an ordering that can oscillate if the passes disagree.

**Files:**
- Modify: `scripts/test_finalize.py`

**Interfaces:**
- Consumes: `main` and all three passes
- Produces: nothing consumed downstream

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_finalize.py` before the `__main__` block:

```python
class TestIdempotence(TempWiki):
    def snapshot(self):
        return {
            str(p.relative_to(self.wiki)): p.read_text(encoding="utf-8")
            for p in sorted(self.wiki.rglob("*.md"))
        }

    def run_cli(self):
        script = pathlib.Path(__file__).parent / "openwiki-finalize.py"
        return subprocess.run(
            [sys.executable, str(script), str(self.wiki)],
            capture_output=True, text=True,
        )

    def test_two_consecutive_runs_are_byte_identical(self):
        self.write("quickstart.md", "# Quickstart\n\nStart. See [arch](arch/overview.md).\n")
        self.write("arch/overview.md", "# Overview\n\nSee [gone](nope.md) and [qs](../quickstart.md).\n")
        self.write("kept.md", '---\ntype: Playbook\nowner: me\n---\n\n# Kept\n\nBody.\n')

        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        after_one = self.snapshot()

        second = self.run_cli()
        self.assertEqual(second.returncode, 0, second.stderr)
        after_two = self.snapshot()

        self.assertEqual(after_one, after_two)

    def test_exit_zero_on_missing_directory(self):
        script = pathlib.Path(__file__).parent / "openwiki-finalize.py"
        r = subprocess.run(
            [sys.executable, str(script), str(self.tmp / "does-not-exist")],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0)

    def test_exit_zero_on_unreadable_content(self):
        p = self.write("bad.md", "# Bad\n")
        p.write_bytes(b"\xff\xfe not utf-8 \xff")
        r = self.run_cli()
        self.assertEqual(r.returncode, 0)
```

- [ ] **Step 2: Run tests to verify they fail or expose oscillation**

Run: `python3 scripts/test_finalize.py -k TestIdempotence -v`
Expected: `test_exit_zero_on_unreadable_content` FAILS — `pass_frontmatter` calls `read_text` with no error handling, so the exception escapes to the top-level guard and the run reports a skip rather than continuing. The other two may pass; if `test_two_consecutive_runs_are_byte_identical` fails, the passes disagree and that is the real bug to fix.

- [ ] **Step 3: Make each pass skip unreadable files instead of aborting**

In `scripts/openwiki-finalize.py`, add this helper above `pass_frontmatter`:

```python
def read_text_or_none(path):
    """Read a file, or return None when it is not decodable text.

    One unreadable file must not cost the whole wiki its finalize pass.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print("openwiki-finalize: skipping %s (%s)" % (path, exc.__class__.__name__))
        return None
```

Then in `pass_frontmatter` replace `text = path.read_text(encoding="utf-8")` with:

```python
        text = read_text_or_none(path)
        if text is None:
            continue
```

And in `pass_links` replace `original = path.read_text(encoding="utf-8")` with:

```python
        original = read_text_or_none(path)
        if original is None:
            continue
```

- [ ] **Step 4: Run the full suite**

Run: `python3 scripts/test_finalize.py`
Expected: PASS, 20 tests.

- [ ] **Step 5: Commit**

```bash
git add scripts/openwiki-finalize.py scripts/test_finalize.py
git commit -m "test(okf): [OW-3] prove finalizer idempotence and never-fail contract"
```

---

### Task 6: Wire the v0.3.3 prompt and Step 3b into `commands/wiki.md`

This is the authoritative agent definition. `SKILL.md` mirrors it in Task 7.

**Files:**
- Modify: `commands/wiki.md`

**Interfaces:**
- Consumes: `scripts/extract-upstream-prompt.py` (Task 1), `scripts/openwiki-finalize.py` (Tasks 2-5)
- Produces: the prompt text and step structure Task 7 mirrors

- [ ] **Step 1: Regenerate the prompt bodies**

Run:
```bash
mkdir -p build
python3 scripts/extract-upstream-prompt.py --command init   > build/prompt-init.md
python3 scripts/extract-upstream-prompt.py --command update > build/prompt-update.md
```

- [ ] **Step 2: Replace Step 1's git-evidence section**

Keep every existing `git --no-pager` command unchanged. Add `.openwikiignore` handling at the end of Step 1:

```markdown
Then check for an ignore file, which changes two prompt instructions and the git posture:

```bash
test -f .openwikiignore && echo active || echo absent
```

- **absent** → use these variants in the Step 3 prompt:
  - `{GIT_HISTORY_HINT}` → `Read git history when it helps establish repository context or explain why code exists. `
  - `{DISCOVERY_INSTRUCTION}` → `- Do not call glob with **/* from the root. Use targeted discovery by directory and extension. Prefer shell commands like rg --files with excludes for .git, node_modules, dist, build, cache directories, and existing generated wiki output.`
- **active** → read it, treat every pattern as off-limits for reading, and use:
  - `{GIT_HISTORY_HINT}` → `Git history is unavailable while .openwikiignore is active; rely on allowed source files and tests without bypassing the restriction. `
  - `{DISCOVERY_INSTRUCTION}` → `- Do not call glob with **/* from the root. Use targeted ls, glob, and grep by directory and extension, skipping .git, node_modules, dist, build, cache directories, and existing generated wiki output.`

`{OUTPUT_LANGUAGE_INSTRUCTIONS}` is always empty: this port has no `--language` flag.
```

- [ ] **Step 3: Replace the Step 3 system prompt**

Replace everything between the `## Step 3 — System prompt (act as this agent)` heading and the `## Step 4` heading. Use the contents of `build/prompt-init.md` for init mode and `build/prompt-update.md` for update mode, under two subheadings. Keep the existing `>` attribution block, updated to:

```markdown
> Reproduced from OpenWiki `v0.3.3` `src/agent/prompts/code.ts` — `CODE_SYSTEM_PROMPTS.init`
> and `CODE_SYSTEM_PROMPTS.update`, repository output mode. Extracted with
> `scripts/extract-upstream-prompt.py`, not retyped. Harness adaptations are marked
> `[adapted]`: (a) DeepAgents' virtual filesystem → native Read/Write/Edit/Glob/Grep/Bash on real
> repo paths; (b) DeepAgents' task tool → Claude Code subagents; (c) upstream's post-run OKF and
> link-validation harness → Step 3b, `scripts/openwiki-finalize.py`.
```

Then append the link-integrity block, reproducing upstream `createLinkIntegrityInstructions()`:

```markdown
Link integrity:
- Prefer relative Markdown links to existing wiki pages and stable heading anchors. Do not invent destinations that are not written in the same run.
- **[adapted]** Step 3b validates relative internal links and heading anchors after the run. Broken links are left in place and marked with an HTML comment starting with "openwiki: broken internal link", so the run completes and a later update can self-correct. If you find such a comment, repair the href or restore the target page using the reason in the comment, then delete the comment.
```

- [ ] **Step 4: Adapt upstream's CLI-specific sentences**

Search the pasted prompt text for sentences that describe upstream's CLI rather than this harness, and mark each `[adapted]`. At `v0.3.3` the known one is the final line of the update prompt:

```
- The CLI will record successful run metadata in /openwiki/.last-update.json after you finish.
```

Rewrite as:

```
- **[adapted]** Step 4 of this command records successful run metadata in openwiki/.last-update.json after you finish.
```

Run this to confirm none were missed:
```bash
grep -n "The CLI\|openwiki --\|npx openwiki" commands/wiki.md
```
Expected: every hit is inside an `[adapted]` line.

- [ ] **Step 5: Insert Step 3b**

Add between Step 3 and Step 4:

```markdown
## Step 3b — Finalize the wiki (deterministic, run AFTER the wiki work)

Upstream does this in harness code (`src/okf/frontmatter.ts`, `src/okf/index-sync.ts`,
`src/agent/wiki-link-validator.ts`). Here it is one script:

```bash
python3 scripts/openwiki-finalize.py openwiki
```

It backfills OKF front matter on any page missing it (tagging its guesses
`openwiki_generated: true` for a later run to upgrade), regenerates every directory `index.md`,
and annotates broken internal links. It always exits 0 and never deletes content.

Run it BEFORE Step 4 — its writes must land inside the snapshot window, or the hash comparison
will not see them. It is idempotent, so a genuine no-op run leaves every file byte-identical and
Step 4 correctly writes nothing.

If the plugin is installed from a marketplace, the script lives in the plugin directory rather
than the target repo; invoke it by its absolute path.
```

- [ ] **Step 6: Add `status` to Step 4**

In the Step 4 JSON block, change the metadata shape to:

```json
{
  "updatedAt": "<current UTC time, ISO 8601>",
  "command": "init|update",
  "gitHead": "<output of git rev-parse HEAD, omit if not a git repo>",
  "model": "<the model you are running as>",
  "status": "complete"
}
```

And add below it:

```markdown
`status` is upstream's `UpdateRunStatus` (`complete` | `interrupted`), from
`src/agent/types.ts`. Write `complete` on a normal finish. If the run is interrupted, leave the
previous metadata untouched so the next update still diffs from the last known-good state.
```

- [ ] **Step 7: Add the finalizer to the headless allowlist**

In the `## Notes — headless / CI permissions` snippet, add to the `allow` array:

```json
      "Bash(python3 scripts/openwiki-finalize.py:*)",
```

- [ ] **Step 8: Verify the file is coherent**

Run:
```bash
grep -c '^## Step' commands/wiki.md
grep -n 'openwiki-finalize\|{GIT_HISTORY_HINT}\|{DISCOVERY_INSTRUCTION}\|"status"' commands/wiki.md
```
Expected: 6 step headings (0, 1, 2, 3, 3b, 4). Every placeholder token appears with both its resolved variants, and `openwiki-finalize` appears in Step 3b and the allowlist.

- [ ] **Step 9: Commit**

```bash
git add commands/wiki.md
git commit -m "feat(reprint): [OW-3] port Claude Code command to upstream v0.3.3"
```

---

### Task 7: Mirror into `SKILL.md`

**Files:**
- Modify: `.agents/skills/openwiki/SKILL.md`

**Interfaces:**
- Consumes: the finished `commands/wiki.md` from Task 6
- Produces: nothing consumed downstream

- [ ] **Step 1: Apply the same four changes**

Mirror Task 6's Step 1 (`.openwikiignore`), Step 3 (new prompt), Step 5 (Step 3b), and Step 6 (`status`) into `SKILL.md`, preserving its host-adapted vocabulary:

- Tool names stay generic: "your host's write/edit tool (`apply_patch` on Codex)", not Read/Write/Edit.
- The subagent section stays marked **opencode only — Codex has no subagent tool; skip this whole section there**.
- Adaptation marker (c) reads: upstream's post-run OKF and link-validation harness → Step 3b, `scripts/openwiki-finalize.py`.

- [ ] **Step 2: Note the script dependency**

Add to the Step 3b section in `SKILL.md`:

```markdown
This step needs `scripts/openwiki-finalize.py`, which ships beside the skill. When the skill is
installed globally at `~/.agents/skills/openwiki/`, copy the script there too and call it by
absolute path. If the script is genuinely unavailable, say so in your final message and skip the
step — do not hand-write front matter or indexes, which would be non-deterministic and would
break the no-op contract.
```

- [ ] **Step 3: Verify both definitions still agree**

Run:
```bash
for phrase in "You are OpenWiki, an expert technical writer" \
              "openwiki: broken internal link" \
              "openwiki_generated" \
              '"status": "complete"'; do
  printf 'cmd=%s skill=%s  %s\n' \
    "$(grep -cF "$phrase" commands/wiki.md)" \
    "$(grep -cF "$phrase" .agents/skills/openwiki/SKILL.md)" "$phrase"
done
python3 -c "
import re,pathlib
for f in ['commands/wiki.md','.agents/skills/openwiki/SKILL.md']:
    t=pathlib.Path(f).read_text()
    print(f, sorted(set(re.findall(r'\{[A-Z_]+\}', t))))
"
```
Expected: every phrase count is non-zero in both files, and both list the same placeholder tokens.

- [ ] **Step 4: Confirm the opencode command needs no change**

Run: `git diff --stat .opencode/`
Expected: empty. It carries only mode routing.

- [ ] **Step 5: Commit**

```bash
git add .agents/skills/openwiki/SKILL.md
git commit -m "feat(reprint): [OW-3] mirror v0.3.3 port into the shell-host skill"
```

---

### Task 8: Move the lock to `v0.3.3` and correct the README

The README's fidelity section, merged in #1, names upstream files as they appear on `main` rather than at the `v0.3.3` tag. A fidelity claim that misnames upstream files is the exact failure this port exists to prevent.

**Files:**
- Modify: `upstream.lock.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above
- Produces: a passing drift check

- [ ] **Step 1: Add the newly reproduced files to the lock**

Add these two entries to `upstream.lock.json`'s `files` object, leaving their hashes as placeholders that Step 2 fills:

```json
    "src/okf/frontmatter.ts": {
      "sha256": "PENDING",
      "bytes": 0,
      "why": "OKF front matter validation and deriveMinimalFrontmatter, reproduced by pass 1 of scripts/openwiki-finalize.py"
    },
    "src/okf/index-sync.ts": {
      "sha256": "PENDING",
      "bytes": 0,
      "why": "synchronizeWikiIndexes, reproduced by pass 2 of scripts/openwiki-finalize.py"
    }
```

Also update the `why` for `src/agent/prompts/code.ts` to:
`"CODE_SYSTEM_PROMPTS.init and .update, reproduced verbatim in commands/wiki.md Step 3 and SKILL.md"`

Add a top-level key recording what the port deliberately does not cover:

```json
  "notCovered": {
    "src/agent/prompts/personal.ts": "personal-wiki output mode (outputMode: local-wiki); a different product from repository documentation",
    "CODE_SYSTEM_PROMPTS.chat": "interactive chat; this port auto-routes between init and update instead",
    "--language flag": "no slash-command equivalent, so language is omitted from run metadata"
  },
```

- [ ] **Step 2: Record the real hashes**

Run:
```bash
sh scripts/check-upstream-drift.sh --update
git diff upstream.lock.json
```
Expected: `trackedRef` and `trackedVersion` become `v0.3.3`, and every `sha256` is a real digest with no `PENDING` left.

- [ ] **Step 3: Verify the check now passes**

Run:
```bash
sh scripts/check-upstream-drift.sh; echo "EXIT=$?"
```
Expected: "No drift. The port is current with `v0.3.3`." and `EXIT=0`.

- [ ] **Step 4: Rewrite the README fidelity section**

Replace the `## Fidelity to upstream` opening — the paragraph beginning "**Tracked against upstream `0.0.4`. Upstream is at `v0.3.3` — this port is behind.**" and its table — with:

```markdown
**Tracked against upstream `v0.3.3`, repository output mode.** The reproduced surface is the
`init` and `update` system prompts (`src/agent/prompts/code.ts` → `CODE_SYSTEM_PROMPTS`), the
prompt assembly and link-integrity appendix (`src/agent/prompt.ts`), and the git evidence,
no-op, snapshot and metadata logic (`src/agent/utils.ts`). Prompt text is extracted from upstream
with [`scripts/extract-upstream-prompt.py`](scripts/extract-upstream-prompt.py) rather than
retyped, so transcription drift is not possible.

**Deliberately not covered**, and recorded as such in
[`upstream.lock.json`](upstream.lock.json):

| Upstream surface | Why not |
|---|---|
| `src/agent/prompts/personal.ts` | The personal-wiki output mode (`outputMode: "local-wiki"`). A different product from documenting a repository. |
| `CODE_SYSTEM_PROMPTS.chat` | Interactive chat. This port auto-routes between `init` and `update`; a plugin slash command is always namespaced. |
| `--language` | No slash-command equivalent, so `language` is omitted from run metadata rather than faked. |
| `translation-middleware.ts`, `skills.ts`, `crash-guard.ts`, `vertex-surface.ts`, `openai-chatgpt-oauth.ts` | Harness plumbing the host already provides. |
```

- [ ] **Step 5: Document OKF for existing users**

Add after that table:

```markdown
### OKF front matter

From `v0.3.3`, every generated page carries YAML front matter following the Google Knowledge
Catalog OKF v0.1 schema — `type` is required, `title` and `description` are recommended, and
producer-defined extension fields are valid and preserved across runs. `index.md` and `log.md` are
reserved and never receive it.

If you already have an `openwiki/` from an earlier version, no migration step is needed. Front
matter is additive, and [`scripts/openwiki-finalize.py`](scripts/openwiki-finalize.py) backfills
it on the next run, tagging anything it inferred with `openwiki_generated: true` so a later run
can replace the guess with a real description. Nothing is deleted.
```

- [ ] **Step 6: Update the repository layout block**

Add to the layout listing in `## Repository layout`:

```
scripts/
  check-upstream-drift.sh  # re-hashes the upstream files this port reproduces
  extract-upstream-prompt.py  # pulls prompt text from upstream, verbatim
  openwiki-finalize.py     # Step 3b: OKF front matter, indexes, link integrity
  test_finalize.py         # finalizer test suite
```

- [ ] **Step 7: Commit**

```bash
git add upstream.lock.json README.md
git commit -m "docs(reprint): [OW-3] track v0.3.3 and correct upstream file names"
```

---

### Task 9: Integration verification

Unit tests cannot exercise the snapshot/finalizer/gate interaction. This task does.

**Files:**
- No source changes expected. Fix whatever breaks.

**Interfaces:**
- Consumes: everything
- Produces: verified working software

- [ ] **Step 1: Confirm the existing suites still pass**

Run:
```bash
python3 scripts/test_finalize.py
sh hooks/test_gate.sh
sh scripts/check-upstream-drift.sh; echo "drift EXIT=$?"
```
Expected: 20 finalizer tests pass, all five gate branches pass, drift exits 0.

- [ ] **Step 2: Prove the gate tolerates the five-field metadata file**

The gate reads `gitHead` with `jq`. Adding `status` should be harmless, but verify rather than assume:

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("openwiki/.last-update.json")
d = json.loads(p.read_text())
d["status"] = "complete"
p.write_text(json.dumps(d, indent=2) + "\n")
print(d)
PY
sh hooks/test_gate.sh
git checkout openwiki/.last-update.json
```
Expected: all gate branches still pass.

- [ ] **Step 3: Run the real command against this repo**

In Claude Code, from the repo root: `/openwiki:wiki update`

Expected: pages gain OKF front matter, `openwiki/index.md` appears with
`okf_version: "0.1"`, existing prose is preserved, and `.last-update.json` gains `status`.

- [ ] **Step 4: Prove the no-op contract survives**

Immediately run `/openwiki:wiki update` again.

Expected: the run reports the wiki is already current, and `git status --short openwiki/` is
empty — `.last-update.json` was NOT rewritten. If it was, the finalizer is not idempotent against
real content; fix that before proceeding, because `hooks/openwiki-gate.sh` depends on it.

- [ ] **Step 5: Check the generated wiki for broken links**

Run:
```bash
grep -rn "openwiki: broken internal link" openwiki/ || echo "no broken links"
```
Expected: no broken links. Any marker is a real defect in the generated wiki — repair the target or the href, then re-run Step 4.

- [ ] **Step 6: Verify the wiki documents the new mechanism**

The spec requires the architecture page explain Step 3b. The `update` run should have done this on
its own, since the source changed — but confirm rather than assume:

```bash
grep -n "Step 3b\|openwiki-finalize\|OKF" openwiki/architecture.md
```

Expected: the page explains what Step 3b does, why the finalizer must be idempotent, and that OKF
front matter is now emitted. If the run missed it, add it by hand — this is the page a future
maintainer reads before touching the finalizer.

- [ ] **Step 7: Commit the regenerated wiki**

```bash
git add openwiki/
git commit -m "docs(openwiki): [OW-3] migrate wiki to OKF under the v0.3.3 prompt"
```

- [ ] **Step 8: Open the PR**

```bash
git push -u origin feature/report-v0.3.3
gh pr create --base main \
  --title "feat: re-port to upstream OpenWiki v0.3.3" \
  --body "Implements docs/superpowers/specs/2026-08-24-openwiki-report-v033-design.md. Closes #2.

The drift check should now PASS on this PR — the first time it has."
```

Expected: the `Upstream drift` workflow runs on the PR and **succeeds**. It has failed by design on every run so far; a pass is the signal that the re-port is complete. If it fails, the lock and the port disagree.

---

## Notes for the executor

**The prompt text is the deliverable, and it is large.** Tasks 6 and 7 paste roughly 39 KB of extracted prose. Do not summarize, reflow, or "improve" it. The only permitted edits are the `[adapted]` markers named in Task 6 Steps 3 and 4. `scripts/check-upstream-drift.sh` guards the source; nothing guards your transcription, so paste from `build/`, never from memory.

**If a test in Task 5 fails on real content but passed on fixtures,** the likely cause is pass ordering: pass 2 writes `index.md` files that pass 3 then link-checks. Fix by making pass 2's output stable, not by reordering — pass 3 must see the final indexes.

**Ignore `openwiki/_plan.md`.** Upstream requires front matter on it, but the prompt also deletes it before the run ends, so the finalizer never sees it. Do not add special handling.
