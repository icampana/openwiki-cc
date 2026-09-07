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
import hashlib
import json
import pathlib
import re
import sys
import urllib.parse

# Upstream EXCLUDED_FILES at v0.5.0 is {index.md, log.md, INSTRUCTIONS.md}.
# _plan.md stays as legacy defense (this port's earlier versions wrote one);
# _sidebar.md stays (OW-5: Docsify nav partial).
RESERVED = {"index.md", "log.md", "_plan.md", "_sidebar.md", "INSTRUCTIONS.md"}
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


def read_text_or_none(path):
    """Read a file with newline="" (see module docstring), or return None
    when it is not decodable text.

    One unreadable file must not cost the whole wiki its finalize pass.
    """
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as exc:
        print("openwiki-finalize: skipping %s (%s)" % (path, exc.__class__.__name__))
        return None


def write_text_or_skip(path, content):
    """Write a file with newline="" (see module docstring), reporting and
    continuing on failure instead of raising.

    One unwritable file (read-only, permissions, missing parent) must not
    cost the whole wiki its finalize pass -- the same guarantee
    read_text_or_none gives reads. Returns True on success, False on skip.
    """
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return True
    except OSError as exc:
        print("openwiki-finalize: skipping write to %s (%s)" % (path, exc.__class__.__name__))
        return False


def _iter_dirs(root):
    """Yield root and every subdirectory beneath it, never descending into a
    symlinked directory.

    A symlinked directory can point anywhere (including outside the wiki),
    so every markdown-finding pass must treat it -- and everything beneath
    it -- as invisible, consistently with pass_indexes never writing an
    index.md into one. Never following a symlink also makes a symlink loop
    structurally impossible to hang on: a loop is made entirely of
    symlinks, and none of them are ever traversed.
    """
    yield root
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return
    for child in children:
        try:
            is_real_dir = child.is_dir() and not child.is_symlink()
        except OSError:
            continue
        if is_real_dir:
            yield from _iter_dirs(child)


def markdown_files(wiki):
    files = []
    for directory in _iter_dirs(wiki):
        try:
            children = directory.iterdir()
        except OSError:
            continue
        files.extend(
            child for child in children
            if child.suffix == ".md" and child.is_file()
        )
    return sorted(files)


def pass_frontmatter(wiki):
    """Backfill OKF front matter. Returns the list of changed paths."""
    changed = []
    for path in markdown_files(wiki):
        if path.name in RESERVED:
            continue
        text = read_text_or_none(path)
        if text is None:
            continue
        updated = ensure_frontmatter(text, path.stem.replace("-", " ").title())
        if updated != text and write_text_or_skip(path, updated):
            changed.append(str(path))
    return changed


ROOT_INDEX_FRONTMATTER = '---\nokf_version: "0.2"\n---\n\n'


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
    """Check if directory has any non-reserved markdown files.

    Never descends into a symlinked subdirectory -- see _iter_dirs -- so a
    directory whose only markdown lives behind a symlink is correctly
    reported as empty, matching pass_indexes' refusal to write through
    that symlink.
    """
    for d in _iter_dirs(directory):
        try:
            children = d.iterdir()
        except OSError:
            continue
        for child in children:
            if child.suffix == ".md" and child.is_file() and child.name not in RESERVED:
                return True
    return False


_HREF_UNSAFE = " ()"


def _encode_href(name):
    """Percent-encode characters that would break a Markdown destination.

    Applies only to hrefs this script authors itself (index entries), never
    to hand-written prose links -- there, truncating an unbalanced paren
    is existing, correct behavior and stays parked. A literal `(` or `)`
    ends a Markdown destination early and a space splits it from an
    optional title, so a self-generated href for `foo(1).md` must encode
    them or the link it authors is broken from the moment it is written.
    """
    return "".join("%%%02X" % ord(c) if c in _HREF_UNSAFE else c for c in name)


def render_index(directory, wiki):
    """Render a directory index. Deterministic: entries are sorted by href."""
    entries = []
    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        if child.is_dir() and not child.is_symlink():
            if _has_real_markdown(child):
                entries.append((
                    "%s/index.md" % _encode_href(child.name),
                    child.name.replace("-", " ").title(),
                ))
        elif child.suffix == ".md" and child.name not in RESERVED:
            entries.append((_encode_href(child.name), index_label(child)))

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
    # A symlinked directory can point outside the wiki (e.g.
    # openwiki/linkdir -> ../outside); writing its index.md would then land
    # outside openwiki/, violating the "writes only inside openwiki/"
    # guarantee. _iter_dirs never descends into one, so it is invisible here
    # exactly as it is to _has_real_markdown and render_index above -- and a
    # symlink loop can never be walked into in the first place.
    directories = sorted(_iter_dirs(wiki), key=str)
    for directory in directories:
        target = directory / "index.md"

        # Check if directory has real (non-reserved) markdown
        if not _has_real_markdown(directory):
            # If index.md exists but directory has no real pages, it's orphaned
            if target.exists():
                orphans.append(str(target))
            continue

        rendered = render_index(directory, wiki)
        existing = read_text_or_none(target) if target.exists() else None

        if existing != rendered and write_text_or_skip(target, rendered):
            changed.append(str(target))

    # Report orphaned indexes
    for orphan in orphans:
        print("openwiki-finalize: orphaned index (no pages remain): %s" % orphan)

    return changed


MARKER_PREFIX = "openwiki: broken internal link"
# LINK_RE does not match nested brackets like [a [b] c](url); such links go unflagged.
# A missed annotation is benign. Handling arbitrary nesting would require a full Markdown
# parser and risks false positives, which corrupt good content.
LINK_RE = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<href>[^)\s]+)\)")
ATX_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.M)


def slugify(heading):
    """GitHub-style anchor slug, collapsing runs of whitespace to one hyphen."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s]+", "-", text).strip("-")


def slugify_uncollapsed(heading):
    """Same, but one hyphen per whitespace character rather than per run.

    Dropping punctuation between words leaves the spaces that surrounded it,
    and GitHub hyphenates each one: "Install - a b" becomes
    "install--a-b", not "install-a-b". Both spellings are accepted so a
    correct anchor is never annotated as broken.
    """
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text).strip("-")


def headings(text):
    """Return set of GitHub-style disambiguated heading slugs.

    When multiple headings slugify identically, they are numbered:
    first is 'dup', second is 'dup-1', third is 'dup-2', etc.
    """
    _, body = split_frontmatter(text)
    slugs = []
    seen = {}
    for m in ATX_RE.finditer(body):
        slug = slugify(m.group(2))
        if slug not in seen:
            seen[slug] = 0
            slugs.append(slug)
        else:
            seen[slug] += 1
            slugs.append(f"{slug}-{seen[slug]}")
        # Accept the uncollapsed spelling of the same heading. Only ever
        # additive, so it can widen what passes but never narrow it.
        alt = slugify_uncollapsed(m.group(2))
        if alt != slug:
            slugs.append(alt)
    return set(slugs)


_MARKER_LINE_RE = re.compile(r"^[ \t]*<!--\s*%s[^\n]*?-->\r?$" % re.escape(MARKER_PREFIX))


def strip_markers_from_body(body):
    """Remove previously written markers from a body, but only outside fences.

    A marker-shaped HTML comment inside a fenced code block is content (e.g. a
    page documenting the marker format itself), not a stale annotation, and
    must survive untouched. Every real marker occupies a full line of its own
    (see pass_links), so removal is line-based: drop lines that match the
    marker pattern and are not currently inside a fence, using the same
    fence-tracking rule as the link-scanning pass below (a fence closes only
    with the character that opened it).
    """
    fence_char = None
    had_markers = False
    kept = []
    for line in body.split("\n"):
        stripped = line.lstrip()
        is_fence_delim = stripped.startswith("```") or stripped.startswith("~~~")

        if fence_char is None and _MARKER_LINE_RE.match(line):
            had_markers = True
            continue

        kept.append(line)

        if is_fence_delim:
            fence_type = "```" if stripped.startswith("```") else "~~~"
            if fence_char is None:
                fence_char = fence_type[0]
            elif fence_type[0] == fence_char:
                fence_char = None

    return "\n".join(kept), had_markers


def _is_internal(href):
    if href.startswith(("http://", "https://", "mailto:", "//", "#", "/")):
        return False
    return True


def pass_links(wiki):
    """Annotate broken relative links and anchors. Returns changed paths."""
    changed = []
    for path in markdown_files(wiki):
        original = read_text_or_none(path)
        if original is None:
            continue
        # Split frontmatter from body so we only process body links, and so
        # marker stripping never has to reason about the front matter block.
        fm_text, original_body = split_frontmatter(original)
        had_frontmatter = fm_text is not None

        body, had_markers = strip_markers_from_body(original_body)

        found_problem = False
        out_lines = []
        fence_char = None  # Track which fence character (` or ~) opened current fence

        # split("\n"), not splitlines(): split is lossless on trailing
        # newlines, so an untouched file round-trips byte-identically.
        body_lines = body.split("\n")
        for line in body_lines:
            # Track fence state: only match fences at line start (possibly after indent)
            stripped = line.lstrip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                # Determine which fence character this line has
                fence_type = "```" if stripped.startswith("```") else "~~~"
                if fence_char is None:
                    # Opening a new fence
                    fence_char = fence_type[0]
                elif fence_type[0] == fence_char:
                    # Closing the current fence (must match opening character)
                    fence_char = None

            out_lines.append(line)
            problems = []

            # Only process links outside of fenced code blocks
            if fence_char is None:
                for match in LINK_RE.finditer(line):
                    href = match.group("href")
                    if not _is_internal(href):
                        continue
                    target_part, _, anchor = href.partition("#")
                    if not target_part:
                        continue
                    # Undo the percent-encoding render_index applies to its own
                    # generated hrefs (e.g. "foo%281%29.md" -> "foo(1).md") so
                    # a self-authored link resolves against the real filename.
                    target = (path.parent / urllib.parse.unquote(target_part)).resolve()
                    if not target.exists():
                        problems.append((href, "target not found"))
                        continue
                    if anchor and target.suffix == ".md":
                        try:
                            with open(target, encoding="utf-8", newline="") as f:
                                target_text = f.read()
                        except (OSError, UnicodeDecodeError):
                            problems.append((href, "heading anchor not found"))
                            continue
                        if slugify(anchor) not in headings(target_text):
                            problems.append((href, "heading anchor not found"))

            indent = line[: len(line) - len(line.lstrip())]
            for href, reason in problems:
                found_problem = True
                marker = "%s<!-- %s: %s - %s -->" % (indent, MARKER_PREFIX, href, reason)
                # Match the line ending of the preceding line (which was just appended)
                if out_lines and out_lines[-1].endswith("\r"):
                    marker += "\r"
                out_lines.append(marker)

        # A file with nothing to say is left completely alone. Normalizing it
        # would count as a change and break the no-op contract.
        if not found_problem and not had_markers:
            continue

        # Reconstruct with proper line ending handling (preserve LF vs CRLF)
        updated_body = "\n".join(out_lines)
        if had_frontmatter:
            # Detect line ending from fm_text to preserve LF vs CRLF
            if fm_text.endswith("\r\n"):
                newline = "\r\n"
            else:
                newline = "\n"
            updated = f"---{newline}{fm_text}---{newline}{updated_body}"
        else:
            updated = updated_body

        if updated != original and write_text_or_skip(path, updated):
            changed.append(str(path))
    return changed


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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wiki", nargs="?", default="openwiki")
    ap.add_argument("--snapshot", action="store_true",
                    help="prepare mode: migrate front matter, then write the provenance state file")
    args = ap.parse_args()

    wiki = pathlib.Path(args.wiki)
    if not wiki.is_dir():
        print("openwiki-finalize: no such directory: %s (nothing to do)" % wiki)
        return 0

    if args.snapshot:
        fm, pages = write_state(wiki)
        print("openwiki-finalize: migrated %d file(s), snapshot %d page(s)" % (fm, pages))
        return 0

    fm = pass_frontmatter(wiki)
    idx = pass_indexes(wiki)
    lnk = pass_links(wiki)
    print("openwiki-finalize: front matter %d, indexes %d, links %d"
          % (len(fm), len(idx), len(lnk)))
    for path in fm + idx + lnk:
        print("  + %s" % path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never fail a documentation run
        print("openwiki-finalize: skipped after error: %r" % exc)
        sys.exit(0)
