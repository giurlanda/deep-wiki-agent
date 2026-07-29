"""Source-document loading, exposed as a tool.

The library ships no loaders by default — which formats a wiki ingests is
domain-specific, and a mandatory PDF stack would be dead weight for a bundle
built from plain text. In practice, though, nearly every bundle's ``raw/``
directory holds PDFs, and every user ends up writing the same adapter. This
module is that adapter, behind the optional ``documents`` extra:

```bash
pip install "deep-wiki-agent[documents]"
```

Conversion is delegated to `markitdown`_, so one tool covers PDF, docx, pptx,
xlsx, html, epub and the rest of its format list rather than PDF alone.

Bytes are pulled through the backend's ``download_files``, never off the local
filesystem, so the tool reads the same tree the agent's file tools do —
including a bundle held in state, a store, or a sandbox. Reads are confined to
``/raw``: the agent may summarize sources, but the tool cannot be aimed at the
wiki pages themselves, nor at anything outside the bundle.

.. _markitdown: https://github.com/microsoft/markitdown
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

from deepagents.backends import FilesystemBackend
from langchain_core.tools import StructuredTool

from deep_wiki_agent.backends import RAW_DIR

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol
    from langchain_core.tools import BaseTool

__all__ = [
    "READ_DOCUMENT_TOOL_NAME",
    "create_read_document_tool",
    "read_document",
]

READ_DOCUMENT_TOOL_NAME = "read_document"

DEFAULT_MAX_CHARS = 200_000
"""Cap on the markdown handed back to the model, so one oversized source
cannot consume the whole context window. Truncation is announced in the
returned text, so the model knows it is looking at a prefix."""

_MISSING_DEPENDENCY_HINT = (
    "reading source documents requires the optional `documents` extra: "
    'install it with `pip install "deep-wiki-agent[documents]"` '
    '(or `uv add "deep-wiki-agent[documents]"`).'
)


def _normalize(path: str) -> str:
    """Collapse ``.``/``..`` segments into an absolute virtual path.

    Purely lexical: the virtual filesystem has no symlinks to resolve, and
    resolving against the local filesystem would defeat the point of confining
    the tool to the backend's tree.

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


def _confine(path: str, root: str) -> str:
    """Resolve a model-supplied path against ``root`` and refuse to leave it.

    Accepts the three spellings a model actually produces for the same file:
    bare (``paper.pdf``), root-relative (``raw/paper.pdf``) and absolute
    (``/raw/paper.pdf``).

    Args:
        path: The path as the model wrote it.
        root: Directory the read is confined to, e.g. ``/raw``.

    Returns:
        An absolute virtual path inside ``root``.

    Raises:
        ValueError: If ``path`` is empty, or lands outside ``root`` — including
            by way of ``..`` segments.
    """
    if not path.strip():
        msg = "path is empty"
        raise ValueError(msg)

    prefix = _normalize(root)
    candidates = [_normalize(path)]
    if not path.strip().startswith("/"):
        candidates.append(_normalize(f"{prefix}/{path}"))

    for candidate in candidates:
        if candidate.startswith(f"{prefix}/"):
            return candidate

    msg = f"{path!r} is outside {prefix}; only documents under {prefix} can be read"
    raise ValueError(msg)


def _resolve_backend(
    wiki_path: str | Path | None,
    backend: BackendProtocol | None,
    *,
    virtual_mode: bool = True,
) -> BackendProtocol:
    """Return the backend the document bytes are pulled from.

    Args:
        wiki_path: Local bundle directory, or ``None`` when ``backend`` is given.
        backend: Caller-supplied backend, used verbatim when present.
        virtual_mode: Confine the assembled filesystem backend to the bundle.

    Returns:
        Either the caller's backend or a ``FilesystemBackend`` rooted at the
        bundle.

    Raises:
        ValueError: If neither or both of ``wiki_path`` and ``backend`` are given.
        NotADirectoryError: If ``wiki_path`` is not an existing directory.
    """
    if (wiki_path is None) == (backend is None):
        msg = "provide exactly one of `wiki_path` or `backend`"
        raise ValueError(msg)

    if backend is not None:
        return backend

    assert wiki_path is not None  # noqa: S101 - guaranteed by the check above
    root = Path(wiki_path).expanduser().resolve()
    if not root.is_dir():
        msg = f"not a directory: {root}"
        raise NotADirectoryError(msg)
    return FilesystemBackend(root_dir=root, virtual_mode=virtual_mode)


def _download(backend: BackendProtocol, path: str) -> bytes:
    """Fetch one file's raw bytes through the backend.

    ``download_files`` rather than ``read``: the latter returns decoded text,
    which a PDF or a docx does not survive.

    Args:
        backend: Backend holding the bundle.
        path: Absolute virtual path of the file.

    Returns:
        The file's bytes.

    Raises:
        RuntimeError: If the backend reports an error, returns nothing, or does
            not implement ``download_files``.
    """
    try:
        responses = backend.download_files([path])
    except NotImplementedError as exc:
        msg = (
            f"backend {type(backend).__name__} cannot return raw bytes "
            f"(`download_files` is not implemented), so {path} cannot be converted"
        )
        raise RuntimeError(msg) from exc

    if not responses:
        msg = f"backend returned no response for {path}"
        raise RuntimeError(msg)

    response = responses[0]
    if response.error or response.content is None:
        msg = f"could not read {path}: {response.error or 'no content returned'}"
        raise RuntimeError(msg)
    return response.content


def _to_markdown(data: bytes, path: str) -> str:
    """Convert a source document's bytes to markdown via ``markitdown``.

    Args:
        data: The document's raw bytes.
        path: Absolute virtual path, used only as a format hint — markitdown
            sniffs the content too, so an unhelpful extension is not fatal.

    Returns:
        The document rendered as markdown.

    Raises:
        ImportError: If the optional ``documents`` extra is not installed.
        RuntimeError: If no converter handles the format, or conversion fails.
    """
    # Imported here, not at module scope: the extra is opt-in, and importing
    # this module must stay free for users who never touch the tool.
    try:
        from markitdown import (  # noqa: PLC0415
            MarkItDown,
            MarkItDownException,
            StreamInfo,
        )
    except ImportError as exc:
        raise ImportError(_MISSING_DEPENDENCY_HINT) from exc

    name = Path(path).name
    stream_info = StreamInfo(extension=Path(path).suffix.lower() or None, filename=name)
    try:
        result = MarkItDown().convert_stream(io.BytesIO(data), stream_info=stream_info)
    except MarkItDownException as exc:
        msg = f"could not convert {path}: {exc}"
        raise RuntimeError(msg) from exc
    return result.markdown


def read_document(
    path: str,
    *,
    wiki_path: str | Path | None = None,
    backend: BackendProtocol | None = None,
    root: str = RAW_DIR,
) -> str:
    """Read a source document from the bundle and return it as markdown.

    Args:
        path: Path of the document, relative to ``root`` or absolute within the
            bundle (``paper.pdf``, ``raw/paper.pdf`` and ``/raw/paper.pdf`` all
            name the same file).
        wiki_path: Directory of the bundle holding the document, on the local
            filesystem. Mutually exclusive with ``backend``.
        backend: A pre-built deepagents backend (state, store, sandbox, or
            filesystem) holding the bundle. Mutually exclusive with
            ``wiki_path``.
        root: Directory reads are confined to. Defaults to ``/raw``, the OKF
            wiki's immutable source directory.

    Returns:
        The document converted to markdown, in full — unlike the tool built by
        :func:`create_read_document_tool`, this function never truncates.

    Raises:
        ValueError: If neither or both of ``wiki_path`` and ``backend`` are
            given, or if ``path`` falls outside ``root``.
        NotADirectoryError: If ``wiki_path`` is not an existing directory.
        ImportError: If the optional ``documents`` extra is not installed.
        RuntimeError: If the document cannot be read or converted.
    """
    resolved = _confine(path, root)
    effective_backend = _resolve_backend(wiki_path, backend)
    return _to_markdown(_download(effective_backend, resolved), resolved)


def create_read_document_tool(
    wiki_path: str | Path | None = None,
    *,
    backend: BackendProtocol | None = None,
    root: str = RAW_DIR,
    max_chars: int = DEFAULT_MAX_CHARS,
    virtual_mode: bool = True,
) -> BaseTool:
    """Build a ``read_document`` tool bound to one bundle.

    Pass the result to a factory's ``tools``::

        from deep_wiki_agent import (
            create_read_document_tool,
            create_wiki_manager_agent,
        )

        manager = create_wiki_manager_agent(
            model="anthropic:claude-sonnet-5",
            wiki_path="./my-wiki",
            tools=[create_read_document_tool("./my-wiki")],
        )

    The bundle is captured in the closure and reads are confined to ``root``,
    so the only thing the model chooses is which source document to open.

    Args:
        wiki_path: Directory of the bundle this tool reads from, on the local
            filesystem. Mutually exclusive with ``backend``.
        backend: A pre-built deepagents backend (state, store, sandbox, or
            filesystem) holding the bundle. Mutually exclusive with
            ``wiki_path``. Must implement ``download_files``, since converting
            a binary format needs the bytes rather than decoded text.
        root: Directory reads are confined to. Defaults to ``/raw``, the OKF
            wiki's immutable source directory — the agent already reads the
            bundle's own markdown pages with its built-in file tools.
        max_chars: Truncate the returned markdown beyond this many characters,
            appending a note saying so. Guards the context window against a
            single oversized source.
        virtual_mode: Confine the assembled filesystem backend to
            ``wiki_path``. Ignored when ``backend`` is given.

    Returns:
        A tool taking a single ``path`` string and returning the document as
        markdown, or an ``ERROR:`` line explaining why it could not.

    Raises:
        ValueError: If neither or both of ``wiki_path`` and ``backend`` are
            given, or if ``max_chars`` is not positive.
        NotADirectoryError: If ``wiki_path`` is not an existing directory.
    """
    if max_chars <= 0:
        msg = f"max_chars must be positive, got {max_chars}"
        raise ValueError(msg)

    # Resolved once, at build time: a bad bundle should fail where the agent is
    # assembled, not on the model's first tool call.
    target = _resolve_backend(wiki_path, backend, virtual_mode=virtual_mode)
    prefix = _normalize(root)

    def read_document(path: str) -> str:
        """Read a source document from the wiki's source directory as markdown.

        Handles PDF, Word, PowerPoint, Excel, HTML, EPUB and other formats,
        converting them to markdown you can quote and summarize. Use it to
        ingest a source before writing wiki pages about it; the wiki's own
        markdown pages are read with the ordinary file tools instead.

        Args:
            path: Path of the document to read, relative to the source
                directory (e.g. `paper.pdf`) or absolute within the bundle
                (e.g. `/raw/paper.pdf`). List the directory first if unsure.

        Returns:
            The document as markdown, truncated with a note if it is very
            long, or a line starting with `ERROR:` explaining the failure.
        """
        try:
            resolved = _confine(path, prefix)
            markdown = _to_markdown(_download(target, resolved), resolved)
        except (ValueError, ImportError, RuntimeError) as exc:
            return f"ERROR: read_document could not read {path!r}: {exc}"

        if len(markdown) > max_chars:
            omitted = len(markdown) - max_chars
            return (
                f"{markdown[:max_chars]}\n\n"
                f"[truncated: {omitted} more character(s) of {resolved} not shown]"
            )
        return markdown

    return StructuredTool.from_function(
        func=read_document,
        name=READ_DOCUMENT_TOOL_NAME,
        parse_docstring=True,
    )
