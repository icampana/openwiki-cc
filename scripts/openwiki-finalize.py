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
    is the untouched input. Handles both LF and CRLF line endings.
    """
    # Detect opening delimiter and line ending style
    if text.startswith("---\n"):
        newline = "\n"
        open_len = 4
    elif text.startswith("---\r\n"):
        newline = "\r\n"
        open_len = 5
    else:
        return None, text

    # Find closing delimiter using the same line ending
    closing_start = newline + "---" + newline
    end = text.find(closing_start, open_len)
    if end == -1:
        return None, text

    # Return fields (from after opening to before closing including newline before ---)
    # and body (from after closing)
    return text[open_len:end + len(newline)], text[end + len(closing_start):]


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
    producer-defined fields. Line endings (LF/CRLF) are preserved.
    """
    fields_text, body = split_frontmatter(text)

    if fields_text is not None:
        if parse_fields(fields_text).get("type"):
            return text

        # Determine line ending from fields_text (which includes trailing newline)
        if fields_text.endswith("\r\n"):
            newline = "\r\n"
        else:
            newline = "\n"

        injected = "type: %s%s%s: true%s" % (FALLBACK_TYPE, newline, GENERATED_FIELD, newline)
        return "---" + newline + injected + fields_text + "---" + newline + body

    title = derive_title(body) or fallback_title
    description = derive_description(body)

    # Determine line ending from body
    if "\r\n" in body:
        newline = "\r\n"
    else:
        newline = "\n"

    lines = ["---", "type: %s" % FALLBACK_TYPE, "title: %s" % _escape(title)]
    if description:
        lines.append("description: %s" % _escape(description))
    lines += ["%s: true" % GENERATED_FIELD, "---", ""]
    return newline.join(lines) + newline + body.lstrip("\r\n")


def markdown_files(wiki):
    return sorted(p for p in wiki.rglob("*.md") if p.is_file())


def pass_frontmatter(wiki):
    """Backfill OKF front matter. Returns the list of changed paths."""
    changed = []
    for path in markdown_files(wiki):
        if path.name in RESERVED:
            continue
        # Use builtin open() with newline="" for Python 3.9+ compatibility
        # (pathlib.Path.read_text() didn't support newline= until Python 3.13)
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        updated = ensure_frontmatter(text, path.stem.replace("-", " ").title())
        if updated != text:
            # Use builtin open() with newline="" for Python 3.9+ compatibility
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(updated)
            changed.append(str(path))
    return changed


ROOT_INDEX_FRONTMATTER = '---\nokf_version: "0.1"\n---\n\n'


def index_label(path):
    """Human label for an index entry: the page's H1, else its filename."""
    try:
        _, body = split_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return path.stem.replace("-", " ").title()
    return derive_title(body) or path.stem.replace("-", " ").title()


def _escape_label(label):
    return label.replace("[", "\\[").replace("]", "\\]")


def _has_real_markdown(directory):
    """Check if directory has any non-reserved markdown files."""
    for md_file in directory.rglob("*.md"):
        if md_file.name not in RESERVED:
            return True
    return False


def render_index(directory, wiki):
    """Render a directory index. Deterministic: entries are sorted by href."""
    entries = []
    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        if child.is_dir():
            if _has_real_markdown(child):
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
    orphans = []
    directories = [wiki] + [d for d in sorted(wiki.rglob("*")) if d.is_dir()]
    for directory in directories:
        target = directory / "index.md"

        # Check if directory has real (non-reserved) markdown
        if not _has_real_markdown(directory):
            # If index.md exists but directory has no real pages, it's orphaned
            if target.exists():
                orphans.append(str(target))
            continue

        rendered = render_index(directory, wiki)
        # Use builtin open() with newline="" for Python 3.9+ compatibility
        existing = None
        if target.exists():
            with open(target, encoding="utf-8", newline="") as f:
                existing = f.read()

        if existing != rendered:
            with open(target, "w", encoding="utf-8", newline="") as f:
                f.write(rendered)
            changed.append(str(target))

    # Report orphaned indexes
    for orphan in orphans:
        print("openwiki-finalize: orphaned index (no pages remain): %s" % orphan)

    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wiki", nargs="?", default="openwiki")
    args = ap.parse_args()

    wiki = pathlib.Path(args.wiki)
    if not wiki.is_dir():
        print("openwiki-finalize: no such directory: %s (nothing to do)" % wiki)
        return 0

    fm = pass_frontmatter(wiki)
    idx = pass_indexes(wiki)
    print("openwiki-finalize: front matter %d, indexes %d" % (len(fm), len(idx)))
    for path in fm + idx:
        print("  + %s" % path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never fail a documentation run
        print("openwiki-finalize: skipped after error: %r" % exc)
        sys.exit(0)
