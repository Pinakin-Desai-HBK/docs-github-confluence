import unittest
from lxml import etree

# Adjust the import to match your repo/module layout.
# If markdown_to_confluence is in sync_to_confluence.py at repo root:
from sync_to_confluence import markdown_to_confluence


class TestMarkdownToConfluenceXhtml(unittest.TestCase):
    def test_gfm_and_br_normalizes_to_well_formed_xhtml(self):
        md = (
            "# Title\n\n"
            "~~strike~~\n\n"
            "- [ ] todo item\n"
            "- [x] done item\n\n"
            "| Col1 | Col2 |\n"
            "| ---- | ---- |\n"
            "| a    | Line 1<br>Line 2 |\n"
        )

        out = markdown_to_confluence(md)

        # Ensure we don't leave raw <br> (XML-invalid) around.
        self.assertIn("<br/>", out)
        self.assertNotIn("<br>", out)

        # Ensure strikethrough rendered (bleach allowlist should include 'del').
        self.assertIn("<del>", out)

        # Ensure a table made it through.
        self.assertIn("<table", out)
        self.assertIn("<tr", out)

        # Critical: output must be well-formed XML when wrapped.
        etree.fromstring(f"<div>{out}</div>".encode("utf-8"))

    def test_raw_xml_is_escaped(self):
        md = "Example: <xml><a>1</a></xml>\n"
        out = markdown_to_confluence(md)

        # Must not emit real <xml> tags into Confluence storage XHTML.
        self.assertNotIn("<xml", out.lower())
        self.assertIn("&lt;xml", out.lower())

        etree.fromstring(f"<div>{out}</div>".encode("utf-8"))


if __name__ == "__main__":
    unittest.main()