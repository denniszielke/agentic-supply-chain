"""Render a Markdown report into a polished HTML + plain-text email.

The Campaign Planning Agent returns GitHub-flavoured Markdown (an executive
summary, a table and a bulleted risk list). This module converts that into:

  * an inline-styled, email-client-friendly **HTML** body, and
  * a **plain-text** alternative (the original Markdown, lightly framed),

so the message renders well in rich clients and degrades gracefully in plain
ones. It has no Azure dependency and is fully unit-testable.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Dict, Optional

import markdown as _markdown


# Accent palette kept consistent with the narrative's Azure-blue / purple style.
_ACCENT = "#0a5bd3"
_ACCENT_DARK = "#0a3f8f"
_INK = "#1b1f2a"
_MUTED = "#5b6470"
_BORDER = "#e3e7ee"
_BG = "#f4f6fa"


@dataclass
class RenderedEmail:
    """The two MIME bodies for a single email."""

    html: str
    text: str


def markdown_to_html(report_markdown: str) -> str:
    """Convert the agent's Markdown into an HTML fragment (tables supported)."""
    return _markdown.markdown(
        report_markdown or "",
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html5",
    )


def _metadata_rows(metadata: Optional[Dict[str, str]]) -> str:
    if not metadata:
        return ""
    cells = "".join(
        f'<span style="display:inline-block;margin:0 14px 4px 0;color:{_MUTED};'
        f'font-size:12px;">{html.escape(str(k))}: '
        f'<strong style="color:{_INK};">{html.escape(str(v))}</strong></span>'
        for k, v in metadata.items()
    )
    return cells


def build_html_document(
    body_html: str,
    *,
    title: str,
    subtitle: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
    footer_note: Optional[str] = None,
) -> str:
    """Wrap an HTML fragment in a full, inline-styled email document."""
    safe_title = html.escape(title)
    safe_subtitle = html.escape(subtitle) if subtitle else ""
    meta_html = _metadata_rows(metadata)
    footer = html.escape(
        footer_note
        or "Generated automatically by the Campaign Planning Agent autopilot."
    )

    # A <style> block (honoured by most rich clients) styles the agent's table
    # and lists; the outer shell uses inline styles for maximum compatibility.
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title}</title>
<style>
  .ap-body {{ margin:0; padding:0; background:{_BG};
    font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif; color:{_INK}; }}
  .ap-content h1,.ap-content h2,.ap-content h3 {{ color:{_ACCENT_DARK};
    margin:22px 0 8px; line-height:1.25; }}
  .ap-content h1 {{ font-size:20px; }} .ap-content h2 {{ font-size:17px; }}
  .ap-content h3 {{ font-size:15px; }}
  .ap-content p,.ap-content li {{ font-size:14px; line-height:1.55; color:{_INK}; }}
  .ap-content table {{ border-collapse:collapse; width:100%; margin:16px 0; font-size:13px; }}
  .ap-content th {{ background:{_ACCENT}; color:#fff; text-align:left;
    padding:9px 11px; font-weight:600; }}
  .ap-content td {{ padding:9px 11px; border-bottom:1px solid {_BORDER};
    vertical-align:top; color:{_INK}; }}
  .ap-content tr:nth-child(even) td {{ background:#fafbfd; }}
  .ap-content code {{ background:#eef1f6; padding:1px 5px; border-radius:4px;
    font-size:12px; }}
</style>
</head>
<body class="ap-body">
  <div style="padding:24px 12px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
      style="max-width:680px;margin:0 auto;background:#ffffff;border:1px solid {_BORDER};
      border-radius:12px;overflow:hidden;">
      <tr>
        <td style="background:linear-gradient(90deg,{_ACCENT_DARK},{_ACCENT});
          padding:22px 26px;">
          <div style="color:#cfe0ff;font-size:12px;letter-spacing:1.5px;
            text-transform:uppercase;">Campaign Planning Agent</div>
          <div style="color:#ffffff;font-size:22px;font-weight:700;margin-top:4px;">
            {safe_title}</div>
          {f'<div style="color:#dce8ff;font-size:13px;margin-top:6px;">{safe_subtitle}</div>' if safe_subtitle else ''}
        </td>
      </tr>
      {f'<tr><td style="padding:14px 26px 0;">{meta_html}</td></tr>' if meta_html else ''}
      <tr>
        <td class="ap-content" style="padding:8px 26px 24px;">
          {body_html}
        </td>
      </tr>
      <tr>
        <td style="padding:16px 26px;background:{_BG};border-top:1px solid {_BORDER};
          color:{_MUTED};font-size:12px;line-height:1.5;">
          {footer}
        </td>
      </tr>
    </table>
  </div>
</body>
</html>"""


def build_plain_text(
    report_markdown: str,
    *,
    title: str,
    subtitle: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
    footer_note: Optional[str] = None,
) -> str:
    """Build the plain-text alternative body."""
    lines = [title]
    if subtitle:
        lines.append(subtitle)
    lines.append("=" * max(len(title), 12))
    if metadata:
        lines.append("")
        lines.extend(f"{k}: {v}" for k, v in metadata.items())
    lines.append("")
    lines.append((report_markdown or "").strip())
    lines.append("")
    lines.append("-" * 40)
    lines.append(
        footer_note
        or "Generated automatically by the Campaign Planning Agent autopilot."
    )
    return "\n".join(lines)


def render_email(
    report_markdown: str,
    *,
    title: str,
    subtitle: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
    footer_note: Optional[str] = None,
) -> RenderedEmail:
    """Render the full HTML + plain-text email from a Markdown report."""
    body_html = markdown_to_html(report_markdown)
    html_doc = build_html_document(
        body_html,
        title=title,
        subtitle=subtitle,
        metadata=metadata,
        footer_note=footer_note,
    )
    text = build_plain_text(
        report_markdown,
        title=title,
        subtitle=subtitle,
        metadata=metadata,
        footer_note=footer_note,
    )
    return RenderedEmail(html=html_doc, text=text)
