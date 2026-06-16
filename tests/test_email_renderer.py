import unittest

from src.campaign_autopilot.email_renderer import (
    build_html_document,
    markdown_to_html,
    render_email,
)

SAMPLE_MD = """\
# Weekly Briefing

## Executive summary

Competitive pressure is high in dairy.

| Category | Action |
|---|---|
| dairy | promote butter |

## Key risks

- perishability
- cost-floor proximity
"""


class MarkdownToHtmlTests(unittest.TestCase):
    def test_table_is_rendered(self):
        html = markdown_to_html(SAMPLE_MD)
        self.assertIn("<table>", html)
        self.assertIn("<th>Category</th>", html)
        self.assertIn("<td>promote butter</td>", html)

    def test_lists_and_headings(self):
        html = markdown_to_html(SAMPLE_MD)
        self.assertIn("<h1>Weekly Briefing</h1>", html)
        self.assertIn("<li>perishability</li>", html)

    def test_empty_input_is_safe(self):
        self.assertEqual(markdown_to_html(""), "")


class HtmlDocumentTests(unittest.TestCase):
    def test_title_is_escaped(self):
        doc = build_html_document("<p>body</p>", title="A & B <x>")
        self.assertIn("A &amp; B &lt;x&gt;", doc)
        # The raw, unescaped title must not appear.
        self.assertNotIn("A & B <x>", doc)

    def test_metadata_is_rendered(self):
        doc = build_html_document(
            "<p>body</p>", title="T", metadata={"Schedule": "0 6 * * 1"}
        )
        self.assertIn("Schedule", doc)
        self.assertIn("0 6 * * 1", doc)


class RenderEmailTests(unittest.TestCase):
    def test_returns_html_and_text(self):
        rendered = render_email(
            SAMPLE_MD,
            title="Weekly Competitor & Margin Briefing",
            subtitle="Automated analysis",
            metadata={"Model": "gpt-4.1-mini"},
        )
        # HTML body
        self.assertIn("<!DOCTYPE html>", rendered.html)
        self.assertIn("Weekly Competitor &amp; Margin Briefing", rendered.html)
        self.assertIn("<table>", rendered.html)
        self.assertIn("gpt-4.1-mini", rendered.html)
        # Plain-text alternative carries the original markdown and the title.
        self.assertIn("Weekly Competitor & Margin Briefing", rendered.text)
        self.assertIn("| Category | Action |", rendered.text)
        self.assertIn("Model: gpt-4.1-mini", rendered.text)


if __name__ == "__main__":
    unittest.main()
