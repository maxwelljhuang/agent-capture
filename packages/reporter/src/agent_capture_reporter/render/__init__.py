"""Rendering: a resolved view-model → HTML (Jinja2) → PDF (WeasyPrint)."""

from agent_capture_reporter.render.html import render, render_html
from agent_capture_reporter.render.pdf import pdf_available, render_pdf

__all__ = ["pdf_available", "render", "render_html", "render_pdf"]
