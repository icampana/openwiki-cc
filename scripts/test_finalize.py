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
