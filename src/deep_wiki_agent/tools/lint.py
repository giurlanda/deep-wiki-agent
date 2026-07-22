"""OKF conformance linting, exposed as a tool.

The ``okf-wiki`` skill tells the agent to run ``scripts/okf_lint.py`` before
declaring a write operation complete. A deep agent has no shell, so it cannot
execute that script itself. This module loads the very same script as a Python
module and wraps its ``lint()`` function in a LangChain tool bound to one
bundle, so the instruction the skill gives is actually executable.

Keeping the script as the single implementation (rather than reimplementing it
here) means the skill stays self-contained and usable outside this library,
while the tool can never drift from it.
"""

from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.tools import StructuredTool

from deep_wiki_agent.resources import okf_lint_script

if TYPE_CHECKING:
    from types import ModuleType

    from langchain_core.tools import BaseTool

__all__ = ["OKF_LINT_TOOL_NAME", "create_okf_lint_tool", "run_okf_lint"]

OKF_LINT_TOOL_NAME = "okf_lint"

_MAX_ITEMS_PER_SECTION = 50
"""Cap on findings echoed back to the model, so a broken bundle cannot flood
the context window. The counts in the summary line stay exact."""


@lru_cache(maxsize=1)
def _load_lint_module() -> ModuleType:
    """Import the bundled ``okf_lint.py`` script as a module.

    Returns:
        The imported module, exposing ``lint(root, fix=False)``.

    Raises:
        FileNotFoundError: If the script is missing from the installation.
        ImportError: If the script cannot be loaded as a module.
    """
    script = okf_lint_script()
    module_name = "deep_wiki_agent._vendored_okf_lint"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        msg = f"cannot load the OKF linter from {script}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def run_okf_lint(wiki_path: str | Path, *, fix: bool = False) -> dict[str, Any]:
    """Validate an OKF bundle on the local filesystem.

    Args:
        wiki_path: Directory of the bundle to validate.
        fix: When ``True``, malformed timestamps are normalized in place
            (the script's ``--fix`` behavior). ``False`` reports only.

    Returns:
        A dict with ``errors``, ``warnings`` and ``fixes`` lists, each item a
        ``{"file": ..., "msg": ...}`` mapping.

    Raises:
        NotADirectoryError: If ``wiki_path`` is not an existing directory.
    """
    root = Path(wiki_path).expanduser().resolve()
    if not root.is_dir():
        msg = f"not a directory: {root}"
        raise NotADirectoryError(msg)

    errors, warnings, fixes = _load_lint_module().lint(root, fix=fix)
    return {"errors": errors, "warnings": warnings, "fixes": fixes}


def _format_report(result: dict[str, list[dict[str, str]]]) -> str:
    """Render a lint result as the plain-text report handed to the model."""
    lines: list[str] = []
    for label, key in (("FIX", "fixes"), ("ERROR", "errors"), ("WARN", "warnings")):
        items = result[key]
        lines.extend(
            f"{label} {item['file']}: {item['msg']}"
            for item in items[:_MAX_ITEMS_PER_SECTION]
        )
        if len(items) > _MAX_ITEMS_PER_SECTION:
            lines.append(
                f"{label} ... and {len(items) - _MAX_ITEMS_PER_SECTION} more "
                f"{key} not shown"
            )

    summary = f"{len(result['errors'])} error(s), {len(result['warnings'])} warning(s)"
    if result["fixes"]:
        summary += f", {len(result['fixes'])} fix(es) applied"
    if not result["errors"] and not result["warnings"]:
        summary += " — the bundle is OKF-conformant"
    lines.append("")
    lines.append(summary)
    return "\n".join(lines)


def create_okf_lint_tool(wiki_path: str | Path) -> BaseTool:
    """Build an ``okf_lint`` tool bound to one bundle.

    The bundle path is captured in the closure rather than taken as a tool
    argument, so the model cannot point the linter (and its ``fix`` writes) at
    an arbitrary directory.

    Args:
        wiki_path: Directory of the bundle this tool validates.

    Returns:
        A tool taking a single optional ``fix`` boolean and returning a
        plain-text conformance report.
    """
    root = Path(wiki_path).expanduser().resolve()

    def okf_lint(*, fix: bool = False) -> str:
        """Validate the wiki bundle for OKF v0.1 conformance.

        Checks YAML frontmatter, the mandatory `type` field, recommended
        fields, ISO 8601 timestamps, broken internal links, orphan pages
        (no inbound links), stale or missing `index.md` files, and misuse of
        the reserved names. Run this before declaring any write operation
        complete.

        Args:
            fix: When true, normalize malformed timestamps in place. When
                false (default), report findings without modifying anything.

        Returns:
            A report listing FIX / ERROR / WARN lines and a summary count.
        """
        try:
            result = run_okf_lint(root, fix=fix)
        except (NotADirectoryError, FileNotFoundError, ImportError) as exc:
            return f"ERROR: okf_lint could not run: {exc}"
        return _format_report(result)

    return StructuredTool.from_function(
        func=okf_lint,
        name=OKF_LINT_TOOL_NAME,
        parse_docstring=True,
    )
