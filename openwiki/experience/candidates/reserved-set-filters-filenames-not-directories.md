---
slug: reserved-set-filters-filenames-not-directories
trigger: implementing Task 1 of the experience-layer plan (excluding openwiki/experience/ from the finalizer)
sessions:
  - 8ac89a2
---

# A filename-matching exclusion set looks like the seam for excluding a directory, but is not

## Evidence

`RESERVED`, a module-level constant in `scripts/openwiki-finalize.py`: `RESERVED =
{"index.md", "log.md", "_plan.md", "_sidebar.md", "INSTRUCTIONS.md"}`. It is checked
as `path.name in RESERVED` in five places — the function `pass_frontmatter` (`if
path.name in RESERVED:`), the function `_has_real_markdown` (`... and child.name not
in RESERVED:`), the function `render_index` (`elif child.suffix == ".md" and
child.name not in RESERVED:`), the function `write_state` (`if path.name in
RESERVED:`), and the function `pass_provenance` (`if path.name in RESERVED:`) — a
check against a file's basename, run on paths already yielded by
`markdown_files`/`_iter_dirs`. `RESERVED` never gates which directories get walked in
the first place; the actual directory gate is the function `_iter_dirs` (`if
is_real_dir and child.name not in EXCLUDED_DIRS:`), which recurses into every real
(non-symlinked) child directory whose name is not in `EXCLUDED_DIRS`, another
module-level constant (`EXCLUDED_DIRS = {"experience"}`). All four finalizer passes
(`pass_frontmatter`, `pass_indexes`, `pass_links`, `pass_provenance`) reach the
filesystem through `_iter_dirs`, so `EXCLUDED_DIRS` is the single seam that makes a
whole subtree invisible.

## Friction

Before reading the code, `RESERVED` looked like the natural place to add
`"experience"` to keep the finalizer out of `openwiki/experience/` — it already reads
like an exclusion list. Adding a directory name to a set matched with `path.name in
RESERVED` would have done nothing: that check only ever sees file basenames handed to
it by callers that already descended into the directory. The real fix required finding
where directory recursion itself is decided, which meant tracing `markdown_files` back
to `_iter_dirs` rather than trusting the name of the set that was already in scope.

## Would have changed

A skill that says "when excluding a path from a recursive filesystem pass, find the
function that walks directories (the `os.walk`/`iterdir` recursion), not the nearest
set literal with 'reserved' or 'excluded' in its name" would have pointed straight at
`_iter_dirs` instead of `RESERVED`, skipping a wrong-seam detour entirely.
