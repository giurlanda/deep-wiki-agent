"""Conformance validator for OKF v0.1 bundles.

Ordinary package code with a shell entry point, so the same implementation
serves three callers: the ``okf_lint`` tool the manager agent runs, the
:func:`~deep_wiki_agent.tools.lint.run_okf_lint` helper, and a human running

.. code-block:: console

    okf-lint <bundle> [--fix] [--json]

``okf-lint`` is the installed console script; ``python -m
deep_wiki_agent.okf_lint`` runs the same entry point without one on ``PATH``.

It checks:

- presence and validity of the YAML frontmatter;
- the mandatory ``type`` field (the OKF spec's only hard requirement);
- the recommended conventional fields ``title``, ``description``, ``timestamp``;
- ISO 8601 timestamps;
- broken internal markdown links;
- links written as absolute paths instead of relative to their page;
- the same two defects in the path-valued frontmatter fields ``resource`` and
  ``sources``, where the page → source traceability lives;
- orphan pages (no inbound links);
- ``index.md`` files that lag behind their directory's contents;
- ``type`` values not declared in the bundle's ``AGENTS.md``, falling back to a
  sprawl heuristic when that file declares none;
- the greppable ``## [YYYY-MM-DD] type | title`` prefix of ``log.md`` entries;
- the same concept created twice under different paths (duplicate slug or title);
- reserved names used as concept pages.

Deliberately stdlib-only: a bundle must stay verifiable by anyone holding the
directory, without installing this library.

``lint`` walks the bundle through a small ``ls``/``read``/``edit`` interface
(:class:`Backend`) rather than ``Path`` directly, so a caller can hand it
either a local directory (wrapped automatically in :class:`_PathBackend`) or
any object implementing that interface — in particular a deepagents
``BackendProtocol`` instance (state, store, sandbox), via the adapter in
:mod:`deep_wiki_agent.tools.lint`. This module itself never imports
``deepagents``, so the shell entry point stays usable without installing it.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

__all__ = ["lint", "main", "parse_frontmatter"]


class Backend(Protocol):
    """The interface :func:`lint` needs to walk and (optionally) fix a bundle.

    Every path in this interface is a bundle-absolute, POSIX-style string
    (``/concepts/foo.md``), matching how deepagents backends address files at
    the agent's virtual root. Implementations resolve those against whatever
    they actually store the bundle in.
    """

    def list_pages(self) -> list[str]:
        """Return every markdown page's absolute path, excluding :data:`SKIP_DIRS`."""
        ...

    def read(self, path: str) -> str:
        """Return the full text content of the file at ``path``."""
        ...

    def exists(self, path: str) -> bool:
        """Return whether ``path`` refers to an existing, readable file."""
        ...

    def edit(self, path: str, old: str, new: str) -> None:
        """Replace the first occurrence of ``old`` with ``new`` in ``path``."""
        ...


RESERVED = frozenset({"index.md", "log.md", "AGENTS.md"})
"""File names that are structural, not concepts, and so skip concept checks.

``AGENTS.md`` is here for the same reason as ``index.md`` and ``log.md``: it
carries the bundle's local schema, not a concept, so it needs no OKF
frontmatter and nothing is expected to link to it.
"""

SKIP_DIRS = frozenset({"raw", ".git", ".obsidian", "node_modules", "assets"})
"""Directories excluded from validation. ``raw/`` holds the immutable sources,
which are not part of the OKF bundle."""

AGENTS_FILE = "AGENTS.md"
"""Name of the file declaring the bundle's local schema, read for its type list."""

LOG_FILE = "log.md"
"""Name of the append-only chronological history, whose entry prefix is linted."""

LOG_ENTRY_TYPES = frozenset({"ingest", "query", "lint", "refactor"})
"""The entry kinds the log format prescribes."""

REQUIRED = ("type",)
RECOMMENDED = ("title", "description", "timestamp")
PATH_FIELDS = ("resource", "sources")
"""Frontmatter fields holding a path, subject to the same rules as body links."""

LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

_LOG_HEADING_RE = re.compile(r"^##\s+\S")
_LOG_ENTRY_RE = re.compile(r"^##\s+\[\d{4}-\d{2}-\d{2}\]\s+(\S+)\s+\|\s+\S")
_LOG_ENTRY_SHAPE = "## [YYYY-MM-DD] type | title"

_TYPES_HEADING_RE = re.compile(r"^(#{1,6})\s+.*\btypes?\b", re.IGNORECASE)
_BACKTICKED_RE = re.compile(r"`([^`\n]+)`")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|\s*([^|]+?)\s*\|")
_TYPE_GLOSS_RE = re.compile("\\s+[-\u2013\u2014:|]\\s+")
"""Separator between a plainly-written type name and its gloss, as in
``- Entity - people and organizations``. Hyphen, en dash, em dash, colon, pipe."""

_MAX_LISTED_PAGES = 10
"""Cap on page names listed in a single "pages not indexed" warning."""

_MAX_ECHOED_CHARS = 60
"""Cap on how much of an offending source line a finding quotes back."""

_TYPE_SPRAWL_THRESHOLD = 6
"""Above this many distinct ``type`` values, single-use types are worth
flagging as sprawl rather than as legitimate domain modelling. Only consulted
when ``AGENTS.md`` declares no type list of its own — a declared vocabulary
makes the exact check possible, and the heuristic redundant."""

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


_KNOWN_FORMATS = (
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%Y%m%d",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
)
"""Timestamp spellings seen in the wild, tried in order by :func:`_coerce_timestamp`.

Day-first only for the ambiguous separator forms: ``19-07-2026`` is unambiguous
once ``%Y-...`` has already been ruled out by :func:`_is_iso8601`, whereas
guessing month-first would silently invent a different date.
"""


def _coerce_timestamp(value: str) -> str | None:
    """Normalize a non-ISO timestamp to ISO 8601, preserving the date it states.

    A malformed timestamp still carries the page's real last-update date, so it
    is worth parsing rather than discarding. Values with no time component are
    anchored at midnight UTC; naive values are read as UTC, since OKF pages
    carry no timezone of their own.

    Args:
        value: The raw frontmatter value, already known not to be ISO 8601.

    Returns:
        The normalized ``YYYY-MM-DDTHH:MM:SSZ`` string, or ``None`` when no
        known format matches.
    """
    text = value.strip().strip("'\"")
    if not text:
        return None
    for fmt in _KNOWN_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)  # noqa: DTZ007 - naive means UTC here
        except ValueError:
            continue
        return parsed.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


class _PathBackend:
    """Adapts a local directory to the :class:`Backend` interface.

    Built automatically by :func:`lint` when called with a ``Path``, so
    existing local-directory callers (the CLI, ``run_okf_lint``) are unaffected
    by the abstraction.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _real(self, path: str) -> Path:
        return self._root / path.lstrip("/")

    def list_pages(self) -> list[str]:
        return [
            "/" + path.relative_to(self._root).as_posix()
            for path in sorted(self._root.rglob("*.md"))
            if not any(part in SKIP_DIRS for part in path.relative_to(self._root).parts)
        ]

    def read(self, path: str) -> str:
        return self._real(path).read_text(encoding="utf-8", errors="replace")

    def exists(self, path: str) -> bool:
        return self._real(path).exists()

    def edit(self, path: str, old: str, new: str) -> None:
        real = self._real(path)
        text = real.read_text(encoding="utf-8")
        real.write_text(text.replace(old, new, 1), encoding="utf-8")


def _resolve(page: str, target: str) -> str | None:
    """Resolve a markdown link target to a bundle-absolute path.

    Absolute (``/``-prefixed) targets are non-conformant — links must be
    relative to the page holding them — but they are still resolved against the
    bundle root, so that one malformed link is reported once, as an absolute
    link, rather than twice with a spurious "broken link" on top.

    Args:
        page: Absolute path of the page holding the link, for relative targets.
        target: The link target, possibly carrying an ``#anchor``.

    Returns:
        The resolved absolute path, or ``None`` for external links and bare
        anchors, which are outside the linter's remit.
    """
    stripped = target.split("#", maxsplit=1)[0].strip()
    if not stripped or stripped.startswith(("http://", "https://", "mailto:")):
        return None
    base = (
        stripped
        if stripped.startswith("/")
        else posixpath.join(posixpath.dirname(page), stripped)
    )
    return posixpath.normpath(base)


def _relativize(page: str, target: str) -> str:
    """Rewrite a bundle-absolute target as a path relative to ``page``.

    The conversion is mechanical — the page's own location is known — which is
    what makes fixing an absolute link safe: the target it resolves to is
    unchanged, only its spelling is. Any ``#anchor`` is carried over untouched.
    """
    path, sep, anchor = target.strip().partition("#")
    relative = posixpath.relpath(path, posixpath.dirname(page))
    return relative + sep + anchor


def _is_path_like(value: str) -> bool:
    """Return whether a ``resource`` value reads as a path rather than prose.

    ``resource`` is documented as "path or URL", but bundles do use it for a
    provenance note when the source is not a file ("Interview, March 2026").
    Flagging that as a broken link would be noise, so a value carrying spaces
    and no separator is left alone.
    """
    text = value.strip()
    return bool(text) and ("/" in text or " " not in text)


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
    backend: Backend, page: str, rel: str, front: Frontmatter, *, fix: bool
) -> tuple[list[Finding], list[Finding]]:
    """Validate a page's ``timestamp``, rewriting it in place when ``fix``.

    Fixing preserves the date the page states whenever it can be parsed at all
    (see :func:`_coerce_timestamp`); the current time is only a last resort,
    and the report says so, because that case destroys information.

    Returns:
        An ``(errors, fixes)`` pair. A malformed timestamp is an error when
        ``fix`` is off and a fix when it is on — never both.
    """
    stamp = front.get("timestamp")
    if not stamp or _is_iso8601(stamp):
        return [], []
    if not fix:
        return [{"file": rel, "msg": f"timestamp is not ISO 8601: {stamp}"}], []

    normalized = _coerce_timestamp(str(stamp))
    if normalized is not None:
        msg = f"timestamp normalized: {stamp} -> {normalized}"
    else:
        normalized = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = (
            f"timestamp unparseable: {stamp} - replaced with the current time "
            f"-> {normalized} (original date lost)"
        )
    backend.edit(page, f"timestamp: {stamp}", f"timestamp: {normalized}")
    return [], [{"file": rel, "msg": msg}]


def _check_links(
    backend: Backend,
    page: str,
    rel: str,
    text: str,
    inbound: dict[str, int],
    *,
    fix: bool,
) -> tuple[list[Finding], list[Finding]]:
    """Report broken and absolute links, and count inbound links into ``inbound``.

    A link that points where it should but is written from the bundle root is
    still a defect: move the bundle, render it on GitHub or open it in an
    editor, and it stops resolving. It is reported as an absolute link, and its
    target — which does exist — still counts as an inbound link, so the page it
    points to is not also flagged an orphan.

    With ``fix``, an absolute link whose target exists is rewritten relative to
    the page and reported as a fix instead of an error. One whose target does
    not exist is left alone and still reported: rewriting it would only move a
    broken link around, and where it was meant to point is a guess.

    Returns:
        An ``(errors, fixes)`` pair.
    """
    errors: list[Finding] = []
    fixes: list[Finding] = []
    for label, target in LINK_RE.findall(text):
        dest = _resolve(page, target)
        if dest is None:
            continue
        dest_exists = backend.exists(dest)
        if target.lstrip().startswith("/"):
            if fix and dest_exists:
                relative = _relativize(page, target)
                backend.edit(page, f"[{label}]({target})", f"[{label}]({relative})")
                fixes.append(
                    {
                        "file": rel,
                        "msg": f"absolute link made relative: {target} -> {relative}",
                    }
                )
            else:
                errors.append(
                    {
                        "file": rel,
                        "msg": (
                            f"absolute link: [{label}]({target}) - "
                            "write the path relative to this page"
                        ),
                    }
                )
        elif not dest_exists:
            errors.append({"file": rel, "msg": f"broken link: [{label}]({target})"})
        if dest_exists and dest in inbound and dest != page:
            inbound[dest] += 1
    return errors, fixes


def _check_frontmatter_paths(
    backend: Backend,
    page: str,
    rel: str,
    front: Frontmatter,
    inbound: dict[str, int],
    *,
    fix: bool,
) -> tuple[list[Finding], list[Finding]]:
    """Apply the link rules to the path-valued frontmatter fields.

    ``resource`` and ``sources`` are where a page records what it derives from,
    so a broken or absolute path there breaks traceability just as surely as a
    broken body link — and, sitting outside the markdown, it goes unnoticed by
    the body-link check. URLs and prose ``resource`` values are left alone.

    Fixes edit the offending path token itself rather than the whole
    frontmatter line, which is what lets a single ``sources`` entry be
    rewritten without reformatting the inline list around it.

    Returns:
        An ``(errors, fixes)`` pair.
    """
    errors: list[Finding] = []
    fixes: list[Finding] = []
    for field in PATH_FIELDS:
        raw = front.get(field)
        if not raw:
            continue
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            target = value.strip()
            if not _is_path_like(target):
                continue
            dest = _resolve(page, target)
            if dest is None:
                continue
            dest_exists = backend.exists(dest)
            if target.startswith("/"):
                if fix and dest_exists:
                    relative = _relativize(page, target)
                    backend.edit(page, target, relative)
                    fixes.append(
                        {
                            "file": rel,
                            "msg": (
                                f"absolute path in `{field}` made relative: "
                                f"{target} -> {relative}"
                            ),
                        }
                    )
                else:
                    errors.append(
                        {
                            "file": rel,
                            "msg": (
                                f"absolute path in `{field}`: {target} - "
                                "write the path relative to this page"
                            ),
                        }
                    )
            elif not dest_exists:
                errors.append(
                    {"file": rel, "msg": f"broken path in `{field}`: {target}"}
                )
            if dest_exists and dest in inbound and dest != page:
                inbound[dest] += 1
    return errors, fixes


def _check_log_format(rel: str, text: str) -> list[Finding]:
    r"""Verify the fixed prefix of every ``log.md`` entry.

    The ``## [YYYY-MM-DD] type | title`` shape exists so the history stays
    queryable with a single ``grep "^## \["``; an entry that drifts from it is
    invisible to that query even though it reads fine to a human.
    """
    warnings: list[Finding] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not _LOG_HEADING_RE.match(line):
            continue
        match = _LOG_ENTRY_RE.match(line)
        if not match:
            warnings.append(
                {
                    "file": rel,
                    "msg": (
                        f'log entry does not match "{_LOG_ENTRY_SHAPE}": '
                        f"{_truncate(line)}"
                    ),
                }
            )
        elif match.group(1) not in LOG_ENTRY_TYPES:
            warnings.append(
                {
                    "file": rel,
                    "msg": (
                        f"unknown log entry type `{match.group(1)}`: expected "
                        + ", ".join(sorted(LOG_ENTRY_TYPES))
                    ),
                }
            )
    return warnings


def _truncate(text: str) -> str:
    """Shorten a quoted source line so one bad entry cannot dominate a report."""
    return (
        text if len(text) <= _MAX_ECHOED_CHARS else text[: _MAX_ECHOED_CHARS - 1] + "…"
    )


def _check_orphans(pages: list[str], inbound: dict[str, int]) -> list[Finding]:
    """Report concept pages that nothing links to."""
    return [
        {
            "file": page.lstrip("/"),
            "msg": "orphan page: no inbound links",
        }
        for page in pages
        if posixpath.basename(page) not in RESERVED and inbound.get(page, 0) == 0
    ]


def _check_indexes(backend: Backend, pages: list[str]) -> list[Finding]:
    """Report directories whose ``index.md`` is missing or out of date.

    The check is textual on purpose: an index that mentions a page's file name
    anywhere counts as listing it, so hand-written prose indexes pass.
    """
    warnings: list[Finding] = []
    for directory in sorted({posixpath.dirname(page) for page in pages}):
        siblings = [
            page
            for page in pages
            if posixpath.dirname(page) == directory
            and posixpath.basename(page) not in RESERVED
        ]
        if not siblings:
            continue

        index = posixpath.join(directory, "index.md")
        rel = index.lstrip("/")
        if not backend.exists(index):
            warnings.append(
                {"file": rel, "msg": f"index.md missing for {len(siblings)} pages"}
            )
            continue

        text = backend.read(index)
        missing = [
            posixpath.basename(page)
            for page in siblings
            if posixpath.basename(page) not in text
        ]
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


def _section_of(text: str, heading: re.Pattern[str]) -> str | None:
    """Return the body of the first section whose heading matches ``heading``.

    The section runs to the next heading of the same or a higher level, so a
    nested subsection stays part of it.
    """
    lines = text.splitlines()
    for start, line in enumerate(lines):
        match = heading.match(line)
        if not match:
            continue
        level = len(match.group(1))
        body: list[str] = []
        for following in lines[start + 1 :]:
            deeper = re.match(r"^(#{1,6})\s", following)
            if deeper and len(deeper.group(1)) <= level:
                break
            body.append(following)
        return "\n".join(body)
    return None


def _declared_types(backend: Backend) -> set[str] | None:
    """Read the ``type`` vocabulary the bundle declares in its ``AGENTS.md``.

    The prompts ask for the types in use to be listed there, but not for any
    particular markup, so this reads the section whose heading mentions
    "type(s)" and takes the backticked tokens in it — falling back to the lead
    token of each list item or table row when the list is written plainly.

    Returns:
        The declared type names, or ``None`` when there is no ``AGENTS.md``, no
        type section in it, or nothing recognizable in that section. ``None``
        means "no vocabulary declared", which leaves the sprawl heuristic in
        charge rather than flagging every type as undeclared.
    """
    path = "/" + AGENTS_FILE
    if not backend.exists(path):
        return None
    section = _section_of(backend.read(path), _TYPES_HEADING_RE)
    if section is None:
        return None

    names = [token.strip() for token in _BACKTICKED_RE.findall(section)]
    if not names:
        for raw_line in section.splitlines():
            item = _LIST_ITEM_RE.match(raw_line) or _TABLE_ROW_RE.match(raw_line)
            if item:
                # "Entity - people and organizations" declares `Entity`.
                names.append(_TYPE_GLOSS_RE.split(item.group(1).strip())[0])

    declared = {
        name
        for name in names
        # Paths and file names show up in these sections as cross-references,
        # never as type names.
        if name and "/" not in name and not name.endswith(".md")
    }
    return declared or None


def _check_types(
    types: dict[str, list[str]], declared: set[str] | None
) -> list[Finding]:
    """Check the ``type`` values in use against the ones ``AGENTS.md`` declares.

    A declared vocabulary turns the vague sprawl heuristic into an exact check,
    so it takes over entirely when there is one. A value that matches a
    declared type except in case is reported separately: it is a spelling slip,
    not a new type, and saying so points at the actual fix.
    """
    if declared is None:
        return _check_type_sprawl(types)

    by_case = {name.casefold(): name for name in declared}
    warnings: list[Finding] = []
    for name, pages in sorted(types.items()):
        if name in declared:
            continue
        canonical = by_case.get(name.casefold())
        msg = (
            f"type `{name}` is declared in {AGENTS_FILE} as `{canonical}`: "
            "match the declared spelling"
            if canonical
            else (
                f"type not declared in {AGENTS_FILE}: `{name}` "
                f"({len(pages)} page(s)) - reuse a declared type or add it there"
            )
        )
        warnings.append({"file": pages[0], "msg": msg})
    return warnings


def _check_duplicates(pages: list[str], titles: list[tuple[str, str]]) -> list[Finding]:
    """Report the same concept created twice under different paths.

    The file path is the identity of a concept, so two pages sharing a slug in
    different categories — or two paths sharing a title — are one concept with
    two identities: links land on whichever the author happened to remember,
    and updates go to one and not the other.

    Args:
        pages: Every page's absolute path.
        titles: ``(title, page path)`` pairs, one per page declaring a title.
            Matched case-insensitively; the first spelling seen is the one
            reported.
    """
    warnings: list[Finding] = []
    slugs: dict[str, list[str]] = {}
    for page in pages:
        name = posixpath.basename(page)
        if name not in RESERVED:
            slugs.setdefault(name, []).append(page.lstrip("/"))
    for name, group in sorted(slugs.items()):
        if len(group) > 1:
            warnings.append(
                {
                    "file": group[0],
                    "msg": f"duplicate slug `{name}`: also at {_join(group[1:])}",
                }
            )

    by_title: dict[str, tuple[str, list[str]]] = {}
    for title, rel in titles:
        by_title.setdefault(title.casefold(), (title, []))[1].append(rel)
    for _, (display, group) in sorted(by_title.items()):
        if len(group) > 1:
            warnings.append(
                {
                    "file": group[0],
                    "msg": f'duplicate title "{display}": also at {_join(group[1:])}',
                }
            )
    return warnings


def _join(paths: list[str]) -> str:
    """Render a capped, comma-separated list of page paths for a finding."""
    overflow = len(paths) - _MAX_LISTED_PAGES
    return ", ".join(paths[:_MAX_LISTED_PAGES]) + (
        f" (+{overflow})" if overflow > 0 else ""
    )


def lint(root: Path | Backend, *, fix: bool = False) -> LintReport:
    """Validate an OKF bundle.

    Args:
        root: Bundle root directory, or an object implementing :class:`Backend`
            for bundles held somewhere other than the local filesystem (see
            :mod:`deep_wiki_agent.tools.lint` for the deepagents adapter).
        fix: When ``True``, two classes of defect are repaired in place and
            reported as fixes instead of errors: malformed timestamps, which
            keep the date they state whenever it is parseable, and absolute
            links and frontmatter paths whose target exists, which are
            rewritten relative to the page holding them. Nothing else is
            modified.

    Returns:
        An ``(errors, warnings, fixes)`` triple. Each finding is a
        ``{"file": ..., "msg": ...}`` mapping whose ``file`` is a
        bundle-relative path.
    """
    backend: Backend = _PathBackend(root) if isinstance(root, Path) else root
    pages = backend.list_pages()
    if not pages:
        return (
            [{"file": "<bundle>", "msg": "no markdown file found in the bundle"}],
            [],
            [],
        )

    errors: list[Finding] = []
    warnings: list[Finding] = []
    fixes: list[Finding] = []
    inbound: dict[str, int] = dict.fromkeys(pages, 0)
    types: dict[str, list[str]] = {}
    titles: list[tuple[str, str]] = []

    for page in pages:
        rel = page.lstrip("/")
        text = backend.read(page)
        front, _ = parse_frontmatter(text)
        basename = posixpath.basename(page)
        is_reserved = basename in RESERVED

        if front is None:
            if not is_reserved:
                errors.append({"file": rel, "msg": "YAML frontmatter missing"})
        else:
            if not is_reserved:
                field_errors, field_warnings = _check_fields(rel, front)
                errors.extend(field_errors)
                warnings.extend(field_warnings)
            stamp_errors, stamp_fixes = _check_timestamp(
                backend, page, rel, front, fix=fix
            )
            errors.extend(stamp_errors)
            fixes.extend(stamp_fixes)
            path_errors, path_fixes = _check_frontmatter_paths(
                backend, page, rel, front, inbound, fix=fix
            )
            errors.extend(path_errors)
            fixes.extend(path_fixes)
            if front.get("type"):
                types.setdefault(str(front["type"]), []).append(rel)
            title = front.get("title")
            if isinstance(title, str) and title.strip() and not is_reserved:
                titles.append((title.strip(), rel))

        link_errors, link_fixes = _check_links(
            backend, page, rel, text, inbound, fix=fix
        )
        errors.extend(link_errors)
        fixes.extend(link_fixes)

        if basename == LOG_FILE:
            warnings.extend(_check_log_format(rel, text))

    warnings.extend(_check_orphans(pages, inbound))
    warnings.extend(_check_indexes(backend, pages))
    warnings.extend(_check_types(types, _declared_types(backend)))
    warnings.extend(_check_duplicates(pages, titles))
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
        prog="okf-lint",
        description="Validate an OKF v0.1 bundle.",
        epilog=(
            "Also runnable without the console script: "
            "python -m deep_wiki_agent.okf_lint"
        ),
    )
    parser.add_argument("bundle", help="directory of the bundle to validate")
    parser.add_argument(
        "--fix",
        action="store_true",
        help=(
            "repair in place what can be repaired mechanically: malformed "
            "timestamps (preserving their date) and absolute links and "
            "frontmatter paths whose target exists"
        ),
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
