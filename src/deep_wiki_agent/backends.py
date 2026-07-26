"""Backend assembly and permission sets for the wiki agents.

The agent sees exactly one tree: the OKF bundle, at the virtual root (``/``).
That way the paths it reads and writes are the bundle-relative paths the OKF
format prescribes — ``/concepts/foo.md`` in a tool call and
``/concepts/foo.md`` in a markdown link are the same string, with nothing to
translate and nothing for the model to get wrong.

The permission helpers make the format's immutability rules structural:
``FilesystemMiddleware`` applies them before a file tool runs, so they hold
regardless of what the model was told, or talked into.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol

__all__ = [
    "RAW_DIR",
    "read_only_permissions",
    "write_protect_permissions",
]

RAW_DIR = "/raw"
"""Bundle directory holding the immutable source documents.

Per the OKF wiki convention, ``raw/`` is *not* part of the bundle: it holds the
sources the wiki is derived from and must never be modified by the agent.
"""


def write_protect_permissions(
    paths: list[str],
) -> list[FilesystemPermission]:
    """Deny writes under the given path prefixes.

    Args:
        paths: Directory paths to protect, e.g. ``["/raw"]``. Both the
            directory itself and everything below it are protected.

    Returns:
        A one-rule permission list suitable for ``create_deep_agent``, or an
        empty list when ``paths`` is empty — a rule with no patterns would
        restrict nothing while still looking like a restriction.
    """
    if not paths:
        return []
    patterns: list[str] = []
    for path in paths:
        prefix = "/" + path.strip().strip("/")
        patterns.extend([prefix, f"{prefix}/**"])
    return [
        FilesystemPermission(operations=["write"], paths=patterns, mode="deny"),
    ]


def read_only_permissions() -> list[FilesystemPermission]:
    """Deny every write on the whole virtual filesystem.

    Enforced by ``FilesystemMiddleware`` at the tool boundary, so it holds even
    if the model is talked into ignoring its system prompt.

    Returns:
        A one-rule permission list denying ``write`` everywhere.
    """
    return [
        FilesystemPermission(operations=["write"], paths=["/", "/**"], mode="deny"),
    ]


def resolve_local_wiki_path(backend: BackendProtocol | None) -> Path | None:
    """Return the local directory a backend is rooted at, if any.

    Used to decide whether the OKF linter tool — which walks a real directory —
    can be attached to an agent. Returns ``None`` for backends that are not
    filesystem-backed (state, store, sandbox), where linting must instead be
    run out of band by the caller.

    Args:
        backend: The backend to inspect, or ``None``.

    Returns:
        The local bundle root, or ``None`` when it is not a local directory.
    """
    # An explicit type check rather than duck typing on ``cwd``: the attribute
    # is an implementation detail of ``FilesystemBackend``, and probing for it
    # would silently treat any future backend that happens to expose a ``cwd``
    # as local. Only this class is contractually rooted at a real directory.
    if not isinstance(backend, FilesystemBackend):
        return None
    path = Path(backend.cwd)
    return path if path.is_dir() else None
