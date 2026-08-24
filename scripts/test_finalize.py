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
        # Use builtin open() with newline="" for Python 3.9+ compatibility
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
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

    def test_crlf_frontmatter_is_byte_identical(self):
        """A valid CRLF file should not be modified."""
        original = "---\r\ntype: Playbook\r\ntitle: Kept\r\n---\r\n\r\n# Kept\r\n\r\nBody.\r\n"
        p = self.write("kept_crlf.md", original)
        finalize.pass_frontmatter(self.wiki)
        # Use builtin open() with newline="" for Python 3.9+ compatibility
        with open(p, encoding="utf-8", newline="") as f:
            result = f.read()
        self.assertEqual(result, original)

    def test_crlf_file_without_type_gains_type_preserving_crlf(self):
        """A CRLF file missing type should gain it without normalizing line endings."""
        original = "---\r\ntitle: Existing\r\nowner: me\r\n---\r\n\r\n# H\r\n\r\nB.\r\n"
        p = self.write("notype_crlf.md", original)
        finalize.pass_frontmatter(self.wiki)
        # Use builtin open() with newline="" for Python 3.9+ compatibility
        with open(p, encoding="utf-8", newline="") as f:
            out = f.read()
        # Should still use CRLF
        self.assertIn("\r\n", out)
        # Parse and verify fields
        parsed = finalize.parse_fields(finalize.split_frontmatter(out)[0])
        self.assertEqual(parsed["type"], "Reference")
        self.assertEqual(parsed["title"], "Existing")
        self.assertEqual(parsed["owner"], "me")

    def test_lf_behavior_unchanged(self):
        """Verify LF files still work as before."""
        p = self.write("lf_test.md", "# Title\n\nDescription.\n")
        finalize.pass_frontmatter(self.wiki)
        out = p.read_text(encoding="utf-8")
        # Should be LF, not CRLF
        self.assertNotIn("\r\n", out)
        self.assertTrue(out.startswith("---\n"))


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

    def test_orphaned_directory_index_is_not_regenerated_and_not_linked(self):
        """Directory with real page -> index created. Delete page -> index orphaned, parent unlinks it."""
        # Create a page in root and arch
        self.write("quickstart.md", "# Quickstart\n\nStart here.\n")
        self.write("arch/overview.md", "# Overview\n\nText.\n")
        finalize.pass_indexes(self.wiki)
        # Verify parent linked the arch directory
        parent_out = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertIn("- [Arch](arch/index.md)", parent_out)
        # Verify arch/index.md was created
        self.assertTrue((self.wiki / "arch" / "index.md").exists())

        # Delete the real page from arch, leaving only the index
        (self.wiki / "arch" / "overview.md").unlink()

        # Run finalizer again
        finalize.pass_indexes(self.wiki)
        # Parent index should no longer link arch
        parent_out = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertNotIn("arch", parent_out)
        # But root still links quickstart
        self.assertIn("- [Quickstart](quickstart.md)", parent_out)
        # Orphaned index should still exist (not deleted) but not be regenerated
        self.assertTrue((self.wiki / "arch" / "index.md").exists())


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

    def test_idempotence_with_no_trailing_newline(self):
        """FIX 1: File with no trailing newline must remain byte-identical on re-run."""
        p = self.write("a.md", "# A\n\n[gone](missing.md)")  # no trailing \n
        finalize.pass_links(self.wiki)
        first = p.read_text(encoding="utf-8")
        finalize.pass_links(self.wiki)
        second = p.read_text(encoding="utf-8")
        self.assertEqual(second, first, "Second run must be byte-identical")
        # Verify marker is present and appears only once
        self.assertIn(MARKER, first)
        self.assertEqual(first.count(MARKER), 1)

    def test_links_in_frontmatter_are_not_annotated(self):
        """FIX 2: Broken links inside YAML frontmatter should be ignored."""
        p = self.write("a.md",
            "---\n"
            "type: Reference\n"
            "title: Test\n"
            "description: See [broken](nope.md) link\n"
            "---\n\n"
            "# A\n\n[valid](b.md)\n"
        )
        self.write("b.md", "# B\n\nBody.\n")
        finalize.pass_links(self.wiki)
        out = p.read_text(encoding="utf-8")
        # The broken link in frontmatter must NOT get a marker; frontmatter is unchanged
        frontmatter = out.split("---")[1]
        self.assertIn("description: See [broken](nope.md) link", frontmatter)
        self.assertNotIn(MARKER, frontmatter)
        # Overall file has no markers (valid body link, ignored frontmatter)
        self.assertNotIn(MARKER, out)

    def test_links_in_fenced_code_blocks_are_not_annotated(self):
        """FIX 3: Links inside fenced code blocks should not be annotated."""
        p = self.write("a.md",
            "# A\n\n"
            "Example:\n\n"
            "```markdown\n"
            "[broken](missing.md)\n"
            "```\n\n"
            "[valid](b.md)\n"
        )
        self.write("b.md", "# B\n\nBody.\n")
        finalize.pass_links(self.wiki)
        out = p.read_text(encoding="utf-8")
        # The broken link inside the fence should NOT get a marker
        lines = out.split("\n")
        fence_section = "\n".join(lines[3:6])  # The ``` ... ``` part
        self.assertNotIn(MARKER, fence_section)
        # Overall file should have no marker since only the fenced link is broken
        self.assertNotIn(MARKER, out)

    def test_duplicate_headings_are_disambiguated(self):
        """FIX 4: Duplicate heading slugs should be disambiguated like GitHub."""
        self.write("b.md",
            "# B\n\n"
            "## Dup\n\n"
            "First duplicate.\n\n"
            "## Dup\n\n"
            "Second duplicate.\n\n"
            "## Dup\n\n"
            "Third duplicate.\n"
        )
        p = self.write("a.md",
            "# A\n\n"
            "[first](b.md#dup)\n"
            "[second](b.md#dup-1)\n"
            "[third](b.md#dup-2)\n"
        )
        finalize.pass_links(self.wiki)
        out = p.read_text(encoding="utf-8")
        # All three links should be valid (GitHub-style disambiguation)
        self.assertNotIn(MARKER, out)

    def test_tilde_fenced_code_blocks_are_not_annotated(self):
        """FIX 1 (Round 2): Links inside ~~~ fenced code blocks should not be annotated."""
        p = self.write("a.md",
            "# A\n\n"
            "Example:\n\n"
            "~~~markdown\n"
            "[broken](missing.md)\n"
            "~~~\n\n"
            "[valid](b.md)\n"
        )
        self.write("b.md", "# B\n\nBody.\n")
        finalize.pass_links(self.wiki)
        out = p.read_text(encoding="utf-8")
        # The broken link inside the ~~~ fence should NOT get a marker
        lines = out.split("\n")
        fence_section = "\n".join(lines[3:6])  # The ~~~ ... ~~~ part
        self.assertNotIn(MARKER, fence_section)
        self.assertNotIn(MARKER, out)

    def test_tilde_fence_containing_backtick_fence(self):
        """FIX 1 (Round 2): ~~~ fence is not closed by ``` line."""
        p = self.write("a.md",
            "# A\n\n"
            "~~~\n"
            "[broken](missing.md)\n"
            "```\n"
            "not closed\n"
            "~~~\n"
        )
        finalize.pass_links(self.wiki)
        out = p.read_text(encoding="utf-8")
        # Backticks inside ~~~ should not close the fence
        self.assertNotIn(MARKER, out)

    def test_crlf_file_with_broken_link_preserves_crlf(self):
        """FIX 2 (Round 2): CRLF file with broken link stays CRLF throughout."""
        original = "---\r\ntype: Reference\r\ntitle: Test\r\n---\r\n\r\n# A\r\n\r\n[broken](missing.md)\r\n"
        p = self.write("a.md", original)
        # Use open() with newline="" to read as-is (preserve CRLF)
        with open(p, encoding="utf-8", newline="") as f:
            before = f.read()
        self.assertEqual(before, original)

        finalize.pass_links(self.wiki)

        # Verify the file still has CRLF and marker is present
        with open(p, encoding="utf-8", newline="") as f:
            after = f.read()
        self.assertIn("\r\n", after, "File should preserve CRLF line endings")
        self.assertIn(MARKER, after, "File should have marker for broken link")

        # Second run must be byte-identical (idempotent)
        finalize.pass_links(self.wiki)
        with open(p, encoding="utf-8", newline="") as f:
            second = f.read()
        self.assertEqual(second, after, "Second run must be byte-identical")

    def test_crlf_marker_line_has_uniform_endings(self):
        """FIX 3 (Round 3): Marker line in CRLF file must have CRLF, not mixed."""
        original = "# CRLF\r\n\r\n[bad](missing.md)\r\n"
        p = self.write("a.md", original)

        finalize.pass_links(self.wiki)

        with open(p, encoding="utf-8", newline="") as f:
            after = f.read()

        # Verify all lines use CRLF (including marker line)
        lines = after.split("\n")
        for line in lines[:-1]:  # All but last empty element from split
            self.assertTrue(
                line.endswith("\r"),
                f"Line should end with CRLF but ends with: {repr(line[-3:])}"
            )

        # Verify marker is present
        self.assertIn(MARKER, after)

        # Second run must be byte-identical (idempotent)
        finalize.pass_links(self.wiki)
        with open(p, encoding="utf-8", newline="") as f:
            second = f.read()
        self.assertEqual(second, after, "Second run must be byte-identical")

    def test_crlf_no_trailing_newline_with_broken_link(self):
        """Interaction test: CRLF file with no trailing newline + broken link."""
        original = "# CRLF\r\n\r\n[bad](missing.md)"  # No trailing newline
        p = self.write("a.md", original)

        with open(p, encoding="utf-8", newline="") as f:
            before = f.read()
        self.assertEqual(before, original)

        finalize.pass_links(self.wiki)

        with open(p, encoding="utf-8", newline="") as f:
            after = f.read()

        # Should have marker and maintain CRLF in original lines
        self.assertIn(MARKER, after)
        self.assertIn("\r\n", after)

        # Second run must be byte-identical
        finalize.pass_links(self.wiki)
        with open(p, encoding="utf-8", newline="") as f:
            second = f.read()
        self.assertEqual(second, after, "Second run must be byte-identical")


if __name__ == "__main__":
    unittest.main(verbosity=2)
