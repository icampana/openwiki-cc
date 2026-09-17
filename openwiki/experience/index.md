# Experience

- [reserved-set-filters-filenames-not-directories](candidates/reserved-set-filters-filenames-not-directories.md): `RESERVED` in `scripts/openwiki-finalize.py` looks like the exclusion seam but is matched as `path.name in RESERVED`, a filename filter that never gates directory recursion; the real seam is `_iter_dirs`, gated by `EXCLUDED_DIRS`. Fix: add new directories to `EXCLUDED_DIRS`, not `RESERVED`.
