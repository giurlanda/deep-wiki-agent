"""OKF conformance linting, exposed as a tool.

The manager agent is told to validate the bundle before declaring a write
complete, but a deep agent has no shell to run a validator with. This module
wraps :func:`deep_wiki_agent.okf_lint.lint` in a LangChain tool bound to one
bundle, so that instruction is actually executable.

The validator itself is ordinary package code, imported normally — the tool is
a thin adapter over it, not a second implementation. This module is the bridge
between it and deepagents: :class:`_BackendAdapter` implements
``okf_lint``'s :class:`~deep_wiki_agent.okf_lint.Backend` interface over a
deepagents ``BackendProtocol``, so the same validator that reads a local
directory can also walk a bundle held in state, a store, or a sandbox.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.tools import StructuredTool

from deep_wiki_agent.okf_lint import SKIP_DIRS, lint

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol
    from langchain_core.tools import BaseTool

__all__ = ["OKF_LINT_TOOL_NAME", "create_okf_lint_tool", "run_okf_lint"]

OKF_LINT_TOOL_NAME = "okf_lint"

_MAX_ITEMS_PER_SECTION = 50
"""Cap on findings echoed back to the model, so a broken bundle cannot flood
the context window. The counts in the summary line stay exact."""

_FULL_READ_LIMIT = 1_000_000
"""Line limit passed to ``backend.read``, high enough that no OKF page (a
markdown concept page, not a data file) is ever truncated."""


class _BackendAdapter:
    """Adapts a deepagents ``BackendProtocol`` to the interface ``lint`` walks.

    Every method either returns the value ``lint`` needs directly or raises
    ``RuntimeError`` on a backend error, so callers (``run_okf_lint``,
    ``create_okf_lint_tool``) only need to catch one exception type regardless
    of which backend they were pointed at.
    """

    def __init__(self, backend: BackendProtocol) -> None:
        self._backend = backend

    def list_pages(self) -> list[str]:
        result = self._backend.glob("**/*.md")
        if result.error:
            msg = f"okf_lint: glob failed: {result.error}"
            raise RuntimeError(msg)
        pages = []
        for match in result.matches or []:
            path = match["path"]
            vpath = path if path.startswith("/") else f"/{path}"
            if any(part in SKIP_DIRS for part in vpath.strip("/").split("/")):
                continue
            pages.append(vpath)
        return sorted(pages)

    def read(self, path: str) -> str:
        result = self._backend.read(path, limit=_FULL_READ_LIMIT)
        if result.error or result.file_data is None:
            msg = f"okf_lint: could not read {path}: {result.error}"
            raise RuntimeError(msg)
        return result.file_data["content"]

    def exists(self, path: str) -> bool:
        return self._backend.read(path, limit=1).error is None

    def edit(self, path: str, old: str, new: str) -> None:
        result = self._backend.edit(path, old, new)
        if result.error:
            msg = f"okf_lint: could not fix {path}: {result.error}"
            raise RuntimeError(msg)


def run_okf_lint(
    wiki_path: str | Path | None = None,
    *,
    backend: BackendProtocol | None = None,
    fix: bool = False,
) -> dict[str, Any]:
    """Validate an OKF bundle.

    Args:
        wiki_path: Directory of the bundle to validate, on the local
            filesystem. Mutually exclusive with ``backend``.
        backend: A pre-built deepagents backend (state, store, sandbox, or
            filesystem) holding the bundle, validated in place through its
            ``glob``/``read``/``edit`` methods. Mutually exclusive with
            ``wiki_path``.
        fix: When ``True``, malformed timestamps are normalized in place
            (preserving the date they state whenever it is parseable) and
            absolute links and frontmatter paths whose target exists are
            rewritten relative to their page. ``False`` reports only.

    Returns:
        A dict with ``errors``, ``warnings`` and ``fixes`` lists, each item a
        ``{"file": ..., "msg": ...}`` mapping.

    Raises:
        ValueError: If neither or both of ``wiki_path`` and ``backend`` are given.
        NotADirectoryError: If ``wiki_path`` is not an existing directory.
        RuntimeError: If a backend operation the linter needs fails.
    """
    if (wiki_path is None) == (backend is None):
        msg = "provide exactly one of `wiki_path` or `backend`"
        raise ValueError(msg)

    target: Path | _BackendAdapter
    if wiki_path is not None:
        target = Path(wiki_path).expanduser().resolve()
        if not target.is_dir():
            msg = f"not a directory: {target}"
            raise NotADirectoryError(msg)
    else:
        assert backend is not None  # noqa: S101 - guaranteed by the check above
        target = _BackendAdapter(backend)

    errors, warnings, fixes = lint(target, fix=fix)
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


def create_okf_lint_tool(
    wiki_path: str | Path | None = None,
    *,
    backend: BackendProtocol | None = None,
) -> BaseTool:
    """Build an ``okf_lint`` tool bound to one bundle.

    The bundle — a local directory or a pre-built backend — is captured in the
    closure rather than taken as a tool argument, so the model cannot point the
    linter (and its ``fix`` writes) at an arbitrary location.

    Args:
        wiki_path: Directory of the bundle this tool validates, on the local
            filesystem. Mutually exclusive with ``backend``.
        backend: A pre-built deepagents backend (state, store, sandbox, or
            filesystem) holding the bundle this tool validates. Mutually
            exclusive with ``wiki_path``.

    Returns:
        A tool taking a single optional ``fix`` boolean and returning a
        plain-text conformance report.

    Raises:
        ValueError: If neither or both of ``wiki_path`` and ``backend`` are given.
    """
    if (wiki_path is None) == (backend is None):
        msg = "provide exactly one of `wiki_path` or `backend`"
        raise ValueError(msg)

    target: Path | BackendProtocol
    if wiki_path is not None:
        target = Path(wiki_path).expanduser().resolve()
    else:
        assert backend is not None  # noqa: S101 - guaranteed by the check above
        target = backend

    def okf_lint(*, fix: bool = False) -> str:
        """Validate the wiki bundle for OKF v0.1 conformance.

        Checks YAML frontmatter, the mandatory `type` field, recommended
        fields, ISO 8601 timestamps, broken and absolute links in the body and
        in the `resource`/`sources` frontmatter fields, orphan pages (no
        inbound links), stale or missing `index.md` files, `type` values not
        declared in `AGENTS.md`, the `log.md` entry format, duplicate slugs and
        titles, and misuse of the reserved names. Run this before declaring any
        write operation complete.

        Args:
            fix: When true, repair in place what is mechanical: malformed
                timestamps keep the date they state when it is parseable, and
                absolute links and frontmatter paths whose target exists are
                rewritten relative to their page. When false (default), report
                findings without modifying anything.

        Returns:
            A report listing FIX / ERROR / WARN lines and a summary count.
        """
        try:
            if isinstance(target, Path):
                result = run_okf_lint(target, fix=fix)
            else:
                result = run_okf_lint(backend=target, fix=fix)
        except (NotADirectoryError, FileNotFoundError, RuntimeError) as exc:
            return f"ERROR: okf_lint could not run: {exc}"
        return _format_report(result)

    return StructuredTool.from_function(
        func=okf_lint,
        name=OKF_LINT_TOOL_NAME,
        parse_docstring=True,
    )
