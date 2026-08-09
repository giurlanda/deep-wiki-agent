"""Virtual-path helpers shared by the tools that read through a backend.

Paths handed to a tool come from a model, and the tools that consume them —
the document reader, the semantic indexer — all need the same three things:
collapse the path lexically, refuse anything that leaves the directory it is
confined to, and test membership without raising. Resolution is deliberately
lexical: the virtual filesystem has no symlinks, and resolving against the
local filesystem would defeat the point of confining a tool to the backend's
tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["confine", "normalize", "within"]


def normalize(path: str) -> str:
    """Collapse ``.``/``..`` segments into an absolute virtual path.

    Args:
        path: A virtual path, absolute or relative.

    Returns:
        The path with redundant, current and parent segments removed, always
        starting with ``/``. Parent segments at the root are dropped rather
        than escaping it.
    """
    parts: list[str] = []
    for part in path.strip().split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts)


def confine(path: str, root: str) -> str:
    """Resolve a model-supplied path against ``root`` and refuse to leave it.

    Accepts the three spellings a model actually produces for the same file:
    bare (``paper.pdf``), root-relative (``raw/paper.pdf``) and absolute
    (``/raw/paper.pdf``).

    Args:
        path: The path as the model wrote it.
        root: Directory the path is confined to, e.g. ``/raw``.

    Returns:
        An absolute virtual path inside ``root``.

    Raises:
        ValueError: If ``path`` is empty, or lands outside ``root`` — including
            by way of ``..`` segments.
    """
    if not path.strip():
        msg = "path is empty"
        raise ValueError(msg)

    prefix = normalize(root)
    candidates = [normalize(path)]
    if not path.strip().startswith("/"):
        candidates.append(normalize(f"{prefix}/{path}"))

    for candidate in candidates:
        if candidate.startswith(f"{prefix}/"):
            return candidate

    msg = f"{path!r} is outside {prefix}; only paths under {prefix} are allowed"
    raise ValueError(msg)


def within(path: str, roots: Sequence[str]) -> bool:
    """Return whether ``path`` sits inside one of ``roots``.

    The test :func:`confine` performs, without raising and against several
    roots at once — what a bulk operation needs when it is filtering a glob's
    matches rather than validating one path.

    Args:
        path: Absolute virtual path to test.
        roots: Directories that are allowed. A root of ``/`` allows everything.

    Returns:
        ``True`` when the path is one of the roots or sits below one.
    """
    candidate = normalize(path)
    for root in roots:
        prefix = normalize(root)
        if prefix in {"/", candidate} or candidate.startswith(f"{prefix}/"):
            return True
    return False
