"""Markdown chunking for the semantic index.

Pure functions over text: nothing here touches a backend, a vector store or an
embedding model, so the chunking of a bundle can be inspected and tested on its
own. Everything a chunk needs to be citable — the page it came from, the header
path above it, whether it is prose or a table — is decided here and travels in
the ``Document``'s metadata.

Three passes, in order:

1. split on markdown headers, so a chunk never straddles two sections and the
   heading hierarchy is recorded rather than thrown away;
2. lift GFM tables out of the prose and index them as atomic chunks, leaving a
   placeholder behind — a table cut in half by a character-count splitter loses
   the header row, and with it any meaning its numbers had;
3. split the remaining prose with a markdown-aware recursive splitter.

Requires the optional ``semantic`` extra
(``pip install "deep-wiki-agent[semantic]"``), which brings
``langchain-text-splitters``. The import happens inside
:func:`chunk_markdown`, so importing this module stays free.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from langchain_core.documents import Document

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DEFAULT_HEADERS",
    "ChunkingConfig",
    "chunk_markdown",
    "extract_markdown_tables",
    "split_table_markdown",
]

DEFAULT_HEADERS: tuple[tuple[str, str], ...] = (
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
    ("####", "h4"),
)
"""Header levels the semantic splitter treats as section boundaries."""

_MISSING_DEPENDENCY_HINT = (
    "semantic chunking requires the optional `semantic` extra: install it with "
    '`pip install "deep-wiki-agent[semantic]"` '
    '(or `uv add "deep-wiki-agent[semantic]"`).'
)

_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")

_MAX_CAPTION_CHARS = 160
"""Longest line accepted as a table's caption. Past this it is prose that
happens to sit above the table, not a title for it."""


@dataclass(frozen=True)
class ChunkingConfig:
    """How a page is cut into chunks.

    Fixed when the tools are built, never exposed to the model: chunk sizes are
    a property of the index, and an agent that could change them per call would
    produce an index whose parts do not compare.

    Attributes:
        headers_to_split_on: ``(marker, metadata key)`` pairs marking a section
            boundary, from the outermost level inwards.
        chunk_size: Target size, in characters, of a prose chunk.
        chunk_overlap: Characters repeated between adjacent prose chunks, so a
            sentence cut in two is still retrievable from either side.
        prepend_header_path: Put the heading hierarchy at the top of each
            chunk's text. Costs a few tokens and buys a chunk that reads as
            self-contained once it is out of its page.
        extract_tables: Index GFM tables as atomic chunks instead of letting
            the character splitter cut them.
        table_max_chars: Size past which a table is split, repeating its header
            and separator rows in every part.
    """

    headers_to_split_on: tuple[tuple[str, str], ...] = DEFAULT_HEADERS
    chunk_size: int = 800
    chunk_overlap: int = 100
    prepend_header_path: bool = True
    extract_tables: bool = True
    table_max_chars: int = 2000


@dataclass
class _Table:
    """One GFM table lifted out of a page's prose."""

    index: int
    header: str
    separator: str
    rows: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    caption: str | None = None

    @property
    def markdown(self) -> str:
        """The table as it appeared in the page."""
        return "\n".join([self.header, self.separator, *self.rows])


def _split_table_row(line: str) -> list[str]:
    """Split one GFM table row into its cells, stripped of the outer pipes."""
    line = line.strip()
    line = line.removeprefix("|")
    line = line.removesuffix("|")
    return [cell.strip() for cell in line.split("|")]


def _caption_above(lines: list[str]) -> str | None:
    """Return the line above a table that reads as its caption, if any.

    Args:
        lines: The prose emitted so far, most recent last.

    Returns:
        The caption text, or ``None`` when the line above is another table row,
        a quote, a list item, too long to be a title, or separated from the
        table by more than one blank line.
    """
    blanks = 0
    for previous in reversed(lines):
        if not previous.strip():
            blanks += 1
            if blanks > 1:
                return None
            continue
        candidate = previous.strip().strip("*_` ")
        if (
            "|" in candidate
            or not candidate
            or len(candidate) > _MAX_CAPTION_CHARS
            or candidate.startswith((">", "-"))
        ):
            return None
        return candidate.lstrip("#").strip().rstrip(":")
    return None


def extract_markdown_tables(text: str) -> tuple[str, list[_Table]]:
    """Lift GFM tables out of the prose, leaving a placeholder behind.

    Tables inside fenced code blocks are left alone: there the markup is
    illustrative content, not data to index separately.

    Args:
        text: The markdown to scan.

    Returns:
        The prose with each table replaced by a one-line placeholder naming it,
        and the tables that were removed, in document order.
    """
    lines = text.split("\n")
    out_lines: list[str] = []
    tables: list[_Table] = []
    in_fence = False
    i = 0

    while i < len(lines):
        line = lines[i]

        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            i += 1
            continue

        starts_table = (
            not in_fence
            and "|" in line
            and i + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[i + 1]) is not None
            and "|" in lines[i + 1]
        )
        if not starts_table:
            out_lines.append(line)
            i += 1
            continue

        j = i + 2
        rows: list[str] = []
        while (
            j < len(lines)
            and lines[j].strip()
            and "|" in lines[j]
            and not _FENCE_RE.match(lines[j])
        ):
            rows.append(lines[j])
            j += 1

        table = _Table(
            index=len(tables) + 1,
            header=line,
            separator=lines[i + 1],
            rows=rows,
            columns=_split_table_row(line),
            caption=_caption_above(out_lines),
        )
        tables.append(table)
        label = table.caption or ", ".join(c for c in table.columns if c)
        out_lines.append(f"[Table {table.index}: {label} — indexed separately]")
        i = j

    return "\n".join(out_lines), tables


def split_table_markdown(table: _Table, max_chars: int) -> list[str]:
    """Split an oversized table, repeating its header in every part.

    Args:
        table: The table to split.
        max_chars: Size past which the table is cut.

    Returns:
        The table's parts, each a valid GFM table. A single-element list when
        the table already fits, or has no data rows to distribute.
    """
    full = table.markdown
    if len(full) <= max_chars or not table.rows:
        return [full]

    head = f"{table.header}\n{table.separator}"
    parts: list[str] = []
    current: list[str] = []
    current_len = len(head)

    for row in table.rows:
        if current and current_len + len(row) + 1 > max_chars:
            parts.append("\n".join([head, *current]))
            current, current_len = [], len(head)
        current.append(row)
        current_len += len(row) + 1
    if current:
        parts.append("\n".join([head, *current]))
    return parts


def _table_documents(
    tables: list[_Table],
    section_metadata: dict[str, Any],
    config: ChunkingConfig,
    counter: int,
) -> tuple[list[Document], int]:
    """Turn one section's tables into atomic chunks.

    Args:
        tables: Tables lifted from the section.
        section_metadata: Header metadata of the section they came from.
        config: Chunking parameters.
        counter: Running table count across the whole page.

    Returns:
        The table chunks and the updated counter.
    """
    documents: list[Document] = []
    for table in tables:
        counter += 1
        parts = split_table_markdown(table, config.table_max_chars)
        columns = [c for c in table.columns if c]
        for part_no, part in enumerate(parts, start=1):
            metadata = dict(section_metadata)
            metadata.update(
                {
                    "content_type": "table",
                    "table_index": counter,
                    "table_caption": table.caption or "",
                    "table_columns": columns,
                    "table_rows": len(table.rows),
                    "table_part": part_no,
                    "table_parts": len(parts),
                }
            )
            caption_line = f"{table.caption}\n" if table.caption else ""
            suffix = f" (part {part_no}/{len(parts)})" if len(parts) > 1 else ""
            documents.append(
                Document(
                    page_content=(
                        f"Table{suffix}: {', '.join(columns)}\n{caption_line}\n{part}"
                    ),
                    metadata=metadata,
                )
            )
    return documents, counter


def _split_sections(text: str, config: ChunkingConfig) -> list[Document]:
    """Cut a page into chunks, carrying the header hierarchy in the metadata.

    Args:
        text: The page as markdown.
        config: Chunking parameters.

    Returns:
        The page's chunks, tables included, before the page-level metadata is
        attached.

    Raises:
        ImportError: If the optional ``semantic`` extra is not installed.
    """
    # Imported here, not at module scope: the extra is opt-in, and importing
    # this module must stay free for users who never build an index.
    try:
        from langchain_text_splitters import (  # noqa: PLC0415
            Language,
            MarkdownHeaderTextSplitter,
            RecursiveCharacterTextSplitter,
        )
    except ImportError as exc:
        raise ImportError(_MISSING_DEPENDENCY_HINT) from exc

    sections = MarkdownHeaderTextSplitter(
        headers_to_split_on=list(config.headers_to_split_on),
        strip_headers=True,  # the heading is put back by `prepend_header_path`
    ).split_text(text)

    prose_splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.MARKDOWN,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )

    chunks: list[Document] = []
    table_counter = 0
    for section in sections:
        body = section.page_content
        tables: list[_Table] = []
        if config.extract_tables:
            body, tables = extract_markdown_tables(body)

        if body.strip():
            chunks.extend(
                prose_splitter.split_documents(
                    [Document(page_content=body, metadata=dict(section.metadata))]
                )
            )

        table_chunks, table_counter = _table_documents(
            tables, dict(section.metadata), config, table_counter
        )
        chunks.extend(table_chunks)
    return chunks


def content_hash(data: bytes) -> str:
    """Return the short digest a file is tracked by in the manifest."""
    return hashlib.sha256(data).hexdigest()[:16]


def chunk_markdown(
    text: str,
    *,
    path: str,
    file_hash: str,
    area: str = "wiki",
    config: ChunkingConfig | None = None,
    modified_at: str | None = None,
    extra_metadata: Mapping[str, Any] | None = None,
) -> list[Document]:
    """Cut one page into chunks carrying everything needed to cite it.

    Args:
        text: The page as markdown — already converted, for a source that was
            not markdown to begin with.
        path: Absolute path of the page on the agent's virtual filesystem, e.g.
            ``/wiki/concepts/operating-margin.md``. This is what a search
            result reports and what an answer cites, so it must be the path the
            agent's own file tools would use.
        file_hash: Digest of the file the text came from, used to tell an
            unchanged page from a rewritten one across ingests.
        area: Which half of the bundle the page belongs to — ``"wiki"`` for a
            wiki page, ``"raw"`` for a source document.
        config: Chunking parameters. Defaults to :class:`ChunkingConfig`.
        modified_at: The file's last-modified timestamp, when the backend knows
            it.
        extra_metadata: Fields merged into every chunk, e.g. a corpus name or
            the caller's tags.

    Returns:
        One ``Document`` per chunk, in document order.

    Raises:
        ImportError: If the optional ``semantic`` extra is not installed.
    """
    config = config or ChunkingConfig()
    chunks = _split_sections(text, config)
    headers = config.headers_to_split_on
    ingested_at = datetime.now(UTC).isoformat()

    documents: list[Document] = []
    for index, chunk in enumerate(chunks):
        hierarchy = [
            str(chunk.metadata[key]) for _, key in headers if chunk.metadata.get(key)
        ]
        header_path = " > ".join(hierarchy)

        content = chunk.page_content.strip()
        if config.prepend_header_path and header_path:
            content = f"{header_path}\n\n{content}"

        metadata: dict[str, Any] = {
            "source": path,
            "relative_path": path.lstrip("/"),
            "file_name": path.rsplit("/", maxsplit=1)[-1],
            "file_hash": file_hash,
            "area": area,
            "modified_at": modified_at or "",
            "ingested_at": ingested_at,
            "section": hierarchy[-1] if hierarchy else "",
            "parent_sections": hierarchy[:-1],
            "header_path": header_path,
            "depth": len(hierarchy),
            "chunk_index": index,
            "chunk_total": len(chunks),
            "content_type": chunk.metadata.get("content_type", "text"),
        }
        for _, key in headers:
            if chunk.metadata.get(key):
                metadata[key] = chunk.metadata[key]
        for key in (
            "table_index",
            "table_caption",
            "table_columns",
            "table_rows",
            "table_part",
            "table_parts",
        ):
            if key in chunk.metadata:
                metadata[key] = chunk.metadata[key]
        if extra_metadata:
            metadata.update(extra_metadata)

        documents.append(Document(page_content=content, metadata=metadata))
    return documents
