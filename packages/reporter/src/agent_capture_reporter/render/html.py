"""Render a view-model to HTML via Jinja2.

The HTML is the single template source: the PDF is this same HTML run through
WeasyPrint (see :mod:`agent_capture_reporter.render.pdf`). The Jinja environment
autoescapes, so redacted/fingerprinted values from spans are HTML-safe.

:func:`render` is the generic entry point (template name + context); each
renderer keeps a thin wrapper — :func:`render_html` for the ECOA notice — that
binds its template and context variable.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from agent_capture_reporter.ecoa.model import AdverseActionModel


@lru_cache(maxsize=1)
def _environment() -> Environment:
    """Build (once) the Jinja environment loading templates from the package."""
    return Environment(
        loader=PackageLoader("agent_capture_reporter.render", "templates"),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render(template_name: str, context: dict[str, Any]) -> str:
    """Render a named template from the package's templates/ dir with ``context``."""
    return _environment().get_template(template_name).render(**context)


def render_html(model: AdverseActionModel) -> str:
    """Render the ECOA notice view-model to a complete HTML document string."""
    return render("adverse_action.html.j2", {"notice": model})
