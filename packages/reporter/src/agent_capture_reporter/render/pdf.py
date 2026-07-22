"""Render notice HTML to PDF via WeasyPrint.

WeasyPrint pulls native libraries (cairo/pango/gdk-pixbuf), so it is an optional
dependency (the ``pdf`` extra). The HTML and manifest paths work without it;
:func:`render_pdf` raises a clear :class:`RenderError` if it is missing rather
than failing with an opaque ImportError deep in a call stack.
"""

from __future__ import annotations

from typing import cast

from agent_capture_reporter.errors import RenderError


def pdf_available() -> bool:
    """Return whether the WeasyPrint backend can be imported."""
    try:
        import weasyprint  # noqa: F401
    except Exception:
        return False
    return True


def render_pdf(html: str, *, base_url: str | None = None) -> bytes:
    """Render an HTML document string to PDF bytes.

    Args:
        html: A complete HTML document (the output of
            :func:`agent_capture_reporter.render.html.render_html`).
        base_url: Optional base URL for resolving any relative references. The
            v1 template is self-contained (inline CSS), so this is rarely needed.

    Raises:
        RenderError: If WeasyPrint is not installed or PDF generation fails.
    """
    try:
        from weasyprint import HTML
    except Exception as exc:
        raise RenderError(
            "PDF rendering requires WeasyPrint and its native libraries. "
            "Install with: pip install 'agent-capture-reporter[pdf]'"
        ) from exc

    try:
        result = HTML(string=html, base_url=base_url).write_pdf()
    except Exception as exc:
        raise RenderError(f"WeasyPrint failed to render the notice PDF: {exc}") from exc
    if result is None:  # write_pdf() returns None only when a target is given; defensive.
        raise RenderError("WeasyPrint returned no PDF bytes")
    return cast(bytes, result)
