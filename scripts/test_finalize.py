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


if __name__ == "__main__":
    unittest.main(verbosity=2)
