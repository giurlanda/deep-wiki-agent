"""Backend assembly for wiki agents.

Both factories need the agent to see two distinct trees on a single virtual
filesystem:

- the **OKF bundle** itself, at the root (``/``), so that the paths the agent
  reads and writes are exactly the bundle-relative paths the OKF format
  prescribes (``/concepts/foo.md``, ``/index.md``, ``/log.md``);
- the **skills**, at a mount point (``/skills/`` by default), so that
  ``deepagents``' ``SkillsMiddleware`` can discover ``okf-wiki`` and the agent
  can ``read_file`` its instructions on demand.

:func:`build_wiki_backend` wires those two together with a
:class:`~deepagents.backends.CompositeBackend`, whose prefix routing strips the
mount point before delegating — so the skills backend is rooted at the skills
directory and the wiki backend never sees the skill files at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission

from deep_wiki_agent.resources import bundled_skills_dir

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol

__all__ = [
    "DEFAULT_SKILLS_MOUNT",
    "RAW_DIR",
    "build_wiki_backend",
    "normalize_mount",
    "read_only_permissions",
    "write_protect_permissions",
]

DEFAULT_SKILLS_MOUNT = "/skills"
"""Mount point of the skills tree on the agent's virtual filesystem."""

RAW_DIR = "/raw"
"""Bundle directory holding the immutable source documents.

Per the OKF wiki convention, ``raw/`` is *not* part of the bundle: it holds the
sources the wiki is derived from and must never be modified by the agent.
"""


def normalize_mount(mount: str) -> str:
    """Normalize a mount point to the ``/name/`` form required by routing.

    ``CompositeBackend`` matches routes by string prefix, so a route must start
    and end with ``/`` for ``/skills/okf-wiki/SKILL.md`` to resolve while a
    sibling directory such as ``/skills-archive/`` does not.

    Args:
        mount: Mount point, with or without surrounding slashes
            (``skills``, ``/skills``, ``/skills/`` are all accepted).

    Returns:
        The mount point in ``/name/`` form.

    Raises:
        ValueError: If ``mount`` is empty or resolves to the root ``/``, which
            would shadow the whole bundle.
    """
    stripped = mount.strip().strip("/")
    if not stripped:
        msg = f"mount point must not be empty or the root path, got {mount!r}"
        raise ValueError(msg)
    return f"/{stripped}/"


def build_wiki_backend(
    wiki_path: str | Path,
    *,
    skills_mount: str = DEFAULT_SKILLS_MOUNT,
    skills_dir: str | Path | None = None,
    virtual_mode: bool = True,
    max_file_size_mb: int = 10,
) -> CompositeBackend:
    """Build the composite backend shared by the wiki agents.

    Args:
        wiki_path: Local directory of the OKF bundle. Mounted at the virtual
            root, so the agent addresses pages by their bundle-relative path.
        skills_mount: Where the skills tree is mounted. The bundled skill is
            then readable at ``<skills_mount>/okf-wiki/SKILL.md``.
        skills_dir: Directory *containing* skill directories. Defaults to the
            skills shipped inside this package. Pass your own to extend or
            replace the instructions the agent follows.
        virtual_mode: When ``True`` (default), each ``FilesystemBackend`` is
            confined to its root directory, so the agent cannot escape the
            bundle via ``../`` or ``~/``. Only turn this off if you understand
            that it grants the model access to the rest of the disk.
        max_file_size_mb: Per-file read limit, passed to both backends.

    Returns:
        A :class:`~deepagents.backends.CompositeBackend` routing
        ``<skills_mount>`` to the skills tree and everything else to the wiki.

    Raises:
        ValueError: If ``skills_mount`` is empty or the root path.
        FileNotFoundError: If ``wiki_path`` or the skills directory does not
            exist. Callers that want the bundle created on demand should do so
            before calling (``create_wiki_manager_agent`` does this for you via
            ``create_if_missing``).
    """
    route = normalize_mount(skills_mount)

    wiki_root = Path(wiki_path).expanduser().resolve()
    if not wiki_root.is_dir():
        msg = f"wiki_path is not an existing directory: {wiki_root}"
        raise FileNotFoundError(msg)

    skills_root = (
        Path(skills_dir).expanduser().resolve()
        if skills_dir is not None
        else bundled_skills_dir()
    )
    if not skills_root.is_dir():
        msg = f"skills directory is not an existing directory: {skills_root}"
        raise FileNotFoundError(msg)

    return CompositeBackend(
        default=FilesystemBackend(
            root_dir=wiki_root,
            virtual_mode=virtual_mode,
            max_file_size_mb=max_file_size_mb,
        ),
        routes={
            route: FilesystemBackend(
                root_dir=skills_root,
                virtual_mode=virtual_mode,
                max_file_size_mb=max_file_size_mb,
            )
        },
    )


def write_protect_permissions(
    paths: list[str],
) -> list[FilesystemPermission]:
    """Deny writes under the given path prefixes.

    Args:
        paths: Directory paths to protect, e.g. ``["/raw", "/skills"]``.
            Both the directory itself and everything below it are protected.

    Returns:
        A one-rule permission list suitable for ``create_deep_agent``.
    """
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
    """Return the local directory a backend's default route points at, if any.

    Used to decide whether the OKF linter tool — which walks a real directory —
    can be attached to an agent. Returns ``None`` for backends that are not
    filesystem-backed (state, store, sandbox), where linting must instead go
    through the skill's script run by the caller.

    Args:
        backend: The backend to inspect, or ``None``.

    Returns:
        The local bundle root, or ``None`` when it is not a local directory.
    """
    if isinstance(backend, CompositeBackend):
        backend = backend.default  # type: ignore[assignment]
    # ``FilesystemBackend`` stores its resolved root as ``cwd``; other
    # backends have no local root at all.
    root = getattr(backend, "cwd", None)
    if root is None:
        return None
    path = Path(root)
    return path if path.is_dir() else None
