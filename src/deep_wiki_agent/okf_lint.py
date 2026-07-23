"""Conformance validator for OKF v0.1 bundles.

Ordinary package code with a shell entry point, so the same implementation
serves three callers: the ``okf_lint`` tool the manager agent runs, the
:func:`~deep_wiki_agent.tools.lint.run_okf_lint` helper, and a human running

.. code-block:: console

    python -m deep_wiki_agent.okf_lint <bundle> [--fix] [--json]

It checks:

- presence and validity of the YAML frontmatter;
- the mandatory ``type`` field (the OKF spec's only hard requirement);
- the recommended conventional fields ``title``, ``description``, ``timestamp``;
- ISO 8601 timestamps;
- broken internal markdown links;
- links written as absolute paths instead of relative to their page;
- orphan pages (no inbound links);
- ``index.md`` files that lag behind their directory's contents;
- reserved names used as concept pages.

Deliberately stdlib-only: a bundle must stay verifiable by anyone holding the
directory, without installing this library.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["lint", "main", "parse_frontmatter"]

RESERVED = frozenset({"index.md", "log.md"})
"""File names that are structural, not concepts, and so skip concept checks."""

SKIP_DIRS = frozenset({"raw", ".git", ".obsidian", "node_modules", "assets"})
"""Directories excluded from validation. ``raw/`` holds the immutable sources,
which are not part of the OKF bundle."""

REQUIRED = ("type",)
RECOMMENDED = ("title", "description", "timestamp")

LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

_MAX_LISTED_PAGES = 10
"""Cap on page names listed in a single "pages not indexed" warning."""

_TYPE_SPRAWL_THRESHOLD = 6
"""Above this many distinct ``type`` values, single-use types are worth
flagging as sprawl rather than as legitimate domain modelling."""

Frontmatter = dict[str, str | list[str]]
Finding = dict[str, str]
LintReport = tuple[list[Finding], list[Finding], list[Finding]]


def parse_frontmatter(text: str) -> tuple[Frontmatter | None, str]:
    """Parse a page's YAML frontmatter without a YAML dependency.

    Handles the subset OKF pages actually use: ``key: value`` lines and inline
    lists (``[a, b]``). Anything more exotic is left as a plain string, which
    is enough for the conformance checks below.

    Args:
        text: Full text of the markdown page.

    Returns:
        A ``(frontmatter, body)`` pair. ``frontmatter`` is ``None`` when the
        page has no frontmatter block at all, in which case ``body`` is the
        original text.
    """
    match = FM_RE.match(text)
    if not match:
        return None, text

    front: Frontmatter = {}
    body = text[match.end() :]
    for raw_line in match.group(1).splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        value = raw_value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            front[key.strip()] = (
                [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
                if inner
                else []
            )
        else:
            front[key.strip()] = value.strip("'\"")
    return front, body


def _is_iso8601(value: object) -> bool:
    """Return whether a frontmatter value parses as an ISO 8601 timestamp."""
    if not isinstance(value, str) or not value:
        return False
    try:
        # Python 3.11+ parses the trailing "Z" natively.
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _collect(root: Path) -> list[Path]:
    """Return every markdown page of the bundle, excluding :data:`SKIP_DIRS`."""
    return [
        path
        for path in sorted(root.rglob("*.md"))
        if not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
    ]


def _resolve(root: Path, page: Path, target: str) -> Path | None:
    """Resolve a markdown link target to a local path.

    Absolute (``/``-prefixed) targets are non-conformant — links must be
    relative to the page holding them — but they are still resolved against the
    bundle root, so that one malformed link is reported once, as an absolute
    link, rather than twice with a spurious "broken link" on top.

    Args:
        root: Bundle root, against which absolute targets resolve.
        page: Page holding the link, for relative targets.
        target: The link target, possibly carrying an ``#anchor``.

    Returns:
        The resolved path, or ``None`` for external links and bare anchors,
        which are outside the linter's remit.
    """
    stripped = target.split("#", maxsplit=1)[0].strip()
    if not stripped or stripped.startswith(("http://", "https://", "mailto:")):
        return None
    if stripped.startswith("/"):
        return (root / stripped.lstrip("/")).resolve()
    return (page.parent / stripped).resolve()


def _check_fields(rel: str, front: Frontmatter) -> tuple[list[Finding], list[Finding]]:
    """Check the frontmatter fields of one page.

    Returns:
        An ``(errors, warnings)`` pair: missing required fields are errors,
        missing conventional fields are warnings.
    """
    errors = [
        {"file": rel, "msg": f"required OKF field missing: `{key}`"}
        for key in REQUIRED
        if not front.get(key)
    ]
    warnings = [
        {"file": rel, "msg": f"recommended field missing: `{key}`"}
        for key in RECOMMENDED
        if not front.get(key)
    ]
    return errors, warnings


def _check_timestamp(
    page: Path, rel: str, text: str, front: Frontmatter, *, fix: bool
) -> tuple[list[Finding], list[Finding]]:
    """Validate a page's ``timestamp``, rewriting it in place when ``fix``.

    Returns:
        An ``(errors, fixes)`` pair. A malformed timestamp is an error when
        ``fix`` is off and a fix when it is on — never both.
    """
    stamp = front.get("timestamp")
    if not stamp or _is_iso8601(stamp):
        return [], []
    if not fix:
        return [{"file": rel, "msg": f"timestamp is not ISO 8601: {stamp}"}], []

    normalized = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    page.write_text(
        text.replace(f"timestamp: {stamp}", f"timestamp: {normalized}", 1),
        encoding="utf-8",
    )
    return [], [{"file": rel, "msg": f"timestamp normalized -> {normalized}"}]


def _check_links(
    root: Path, page: Path, rel: str, text: str, inbound: dict[Path, int]
) -> list[Finding]:
    """Report broken and absolute links, and count inbound links into ``inbound``.

    A link that points where it should but is written from the bundle root is
    still a defect: move the bundle, render it on GitHub or open it in an
    editor, and it stops resolving. It is reported as an absolute link, and its
    target — which does exist — still counts as an inbound link, so the page it
    points to is not also flagged an orphan.
    """
    errors: list[Finding] = []
    for label, target in LINK_RE.findall(text):
        dest = _resolve(root, page, target)
        if dest is None:
            continue
        if target.lstrip().startswith("/"):
            errors.append(
                {
                    "file": rel,
                    "msg": (
                        f"absolute link: [{label}]({target}) - "
                        "write the path relative to this page"
                    ),
                }
            )
        elif not dest.exists():
            errors.append({"file": rel, "msg": f"broken link: [{label}]({target})"})
        if dest.exists() and dest in inbound and dest != page.resolve():
            inbound[dest] += 1
    return errors


def _check_orphans(
    root: Path, pages: list[Path], inbound: dict[Path, int]
) -> list[Finding]:
    """Report concept pages that nothing links to."""
    return [
        {
            "file": page.relative_to(root).as_posix(),
            "msg": "orphan page: no inbound links",
        }
        for page in pages
        if page.name not in RESERVED and inbound.get(page.resolve(), 0) == 0
    ]


def _check_indexes(root: Path, pages: list[Path]) -> list[Finding]:
    """Report directories whose ``index.md`` is missing or out of date.

    The check is textual on purpose: an index that mentions a page's file name
    anywhere counts as listing it, so hand-written prose indexes pass.
    """
    warnings: list[Finding] = []
    for directory in sorted({page.parent for page in pages}):
        siblings = [
            page
            for page in pages
            if page.parent == directory and page.name not in RESERVED
        ]
        if not siblings:
            continue

        index = directory / "index.md"
        rel = index.relative_to(root).as_posix()
        if not index.exists():
            warnings.append(
                {"file": rel, "msg": f"index.md missing for {len(siblings)} pages"}
            )
            continue

        text = index.read_text(encoding="utf-8", errors="replace")
        missing = [page.name for page in siblings if page.name not in text]
        if missing:
            overflow = len(missing) - _MAX_LISTED_PAGES
            warnings.append(
                {
                    "file": rel,
                    "msg": "pages not indexed: "
                    + ", ".join(missing[:_MAX_LISTED_PAGES])
                    + (f" (+{overflow})" if overflow > 0 else ""),
                }
            )
    return warnings


def _check_type_sprawl(types: dict[str, list[str]]) -> list[Finding]:
    """Report a proliferation of one-off ``type`` values.

    A handful of single-use types is normal while a bundle grows; many of them
    alongside many distinct types means the vocabulary was never settled, and
    ``AGENTS.md`` no longer describes the bundle.
    """
    singles = [name for name, pages in types.items() if len(pages) == 1]
    if len(types) <= _TYPE_SPRAWL_THRESHOLD or not singles:
        return []
    return [
        {
            "file": "<bundle>",
            "msg": (
                f"{len(types)} distinct types, {len(singles)} used only once: "
                "consider consolidating them and aligning AGENTS.md"
            ),
        }
    ]


def lint(root: Path, *, fix: bool = False) -> LintReport:
    """Validate an OKF bundle.

    Args:
        root: Bundle root directory.
        fix: When ``True``, malformed timestamps are rewritten in place and
            reported as fixes instead of errors. Nothing else is modified.

    Returns:
        An ``(errors, warnings, fixes)`` triple. Each finding is a
        ``{"file": ..., "msg": ...}`` mapping whose ``file`` is a
        bundle-relative path.
    """
    pages = _collect(root)
    if not pages:
        return (
            [{"file": str(root), "msg": "no markdown file found in the bundle"}],
            [],
            [],
        )

    errors: list[Finding] = []
    warnings: list[Finding] = []
    fixes: list[Finding] = []
    inbound: dict[Path, int] = {page.resolve(): 0 for page in pages}
    types: dict[str, list[str]] = {}

    for page in pages:
        rel = page.relative_to(root).as_posix()
        text = page.read_text(encoding="utf-8", errors="replace")
        front, _ = parse_frontmatter(text)
        is_reserved = page.name in RESERVED

        if front is None:
            if not is_reserved:
                errors.append({"file": rel, "msg": "YAML frontmatter missing"})
        else:
            if not is_reserved:
                field_errors, field_warnings = _check_fields(rel, front)
                errors.extend(field_errors)
                warnings.extend(field_warnings)
            stamp_errors, stamp_fixes = _check_timestamp(
                page, rel, text, front, fix=fix
            )
            errors.extend(stamp_errors)
            fixes.extend(stamp_fixes)
            if front.get("type"):
                types.setdefault(str(front["type"]), []).append(rel)

        errors.extend(_check_links(root, page, rel, text, inbound))

    warnings.extend(_check_orphans(root, pages, inbound))
    warnings.extend(_check_indexes(root, pages))
    warnings.extend(_check_type_sprawl(types))
    return errors, warnings, fixes


def _render(report: LintReport) -> str:
    """Render a lint report as the plain-text output of the shell entry point."""
    errors, warnings, fixes = report
    lines = [f"FIX   {item['file']}: {item['msg']}" for item in fixes]
    lines += [f"ERROR {item['file']}: {item['msg']}" for item in errors]
    lines += [f"WARN  {item['file']}: {item['msg']}" for item in warnings]
    summary = f"{len(errors)} errors, {len(warnings)} warnings"
    if fixes:
        summary += f", {len(fixes)} fixes"
    lines += ["", summary]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the linter from the command line.

    Args:
        argv: Argument list, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` when the bundle is free of errors, ``1`` when it has errors
        (warnings alone do not fail), ``2`` when the bundle path is unusable.
    """
    parser = argparse.ArgumentParser(
        prog="python -m deep_wiki_agent.okf_lint",
        description="Validate an OKF v0.1 bundle.",
    )
    parser.add_argument("bundle", help="directory of the bundle to validate")
    parser.add_argument(
        "--fix", action="store_true", help="normalize malformed timestamps in place"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable output"
    )
    args = parser.parse_args(argv)

    root = Path(args.bundle).expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)  # noqa: T201
        return 2

    errors, warnings, fixes = lint(root, fix=args.fix)
    if args.json:
        output = json.dumps(
            {"errors": errors, "warnings": warnings, "fixes": fixes},
            indent=2,
            ensure_ascii=False,
        )
    else:
        output = _render((errors, warnings, fixes))
    print(output)  # noqa: T201

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
