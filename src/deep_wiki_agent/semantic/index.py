"""The semantic index over a bundle: ingestion, incremental updates, search.

Everything here reads the bundle through the deepagents backend — never off
the local filesystem — so the index covers exactly the tree the agent's own
file tools see, whether that tree is a directory, agent state, a store or a
sandbox.

Two halves:

- **Ingestion.** Files are resolved with the backend's ``glob``, confined to
  the configured roots, converted to markdown when they are not markdown to
  begin with (the same `markitdown` path
  :mod:`deep_wiki_agent.tools.documents` uses), chunked, and written to the
  vector store under deterministic ids. A manifest — a small JSON file kept in
  the bundle, outside anything the linter walks — records each file's digest
  and the ids its chunks were stored under, which is what makes a second
  ingest skip unchanged files and delete the chunks of files that shrank or
  disappeared.
- **Search.** Dense retrieval, or dense + BM25 when the store is configured
  for hybrid retrieval. The filters a caller can apply — area, content type,
  path, section — run server-side when a filter builder is supplied, and
  client-side over a widened candidate set otherwise.

The index is derived data: it can be deleted and rebuilt from the bundle at
any time, and nothing in the bundle depends on it. That is deliberate — an OKF
bundle stays a directory of markdown files, with no database to carry around.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from deep_wiki_agent._paths import normalize, within
from deep_wiki_agent.backends import RAW_DIR
from deep_wiki_agent.semantic.chunking import (
    ChunkingConfig,
    chunk_markdown,
    content_hash,
)
from deep_wiki_agent.tools.documents import _download, _to_markdown

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from deepagents.backends.protocol import BackendProtocol
    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings
    from langchain_core.vectorstores import VectorStore

__all__ = [
    "DEFAULT_DOCUMENT_EXTENSIONS",
    "DEFAULT_TEXT_EXTENSIONS",
    "IngestReport",
    "SemanticConfig",
    "SemanticIndex",
]

_ID_NAMESPACE = uuid.UUID("6f6b1b8e-3c6a-4a3f-9c1a-2f9f0c3a7b11")
"""Namespace for the chunk ids. Fixed forever: changing it would orphan every
chunk already in a store rather than update it."""

_FULL_READ_LIMIT = 1_000_000
"""Line limit for ``backend.read``, high enough that no page is truncated."""

_GLOB_MAGIC = ("*", "?", "[")

_FILES_IN_SUMMARY = 5
"""How many filenames an ingest report names before saying "and N more"."""

DEFAULT_TEXT_EXTENSIONS: tuple[str, ...] = (".md", ".markdown", ".txt")
"""Read as text through the backend, with no conversion step."""

DEFAULT_DOCUMENT_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".xlsx",
    ".xls",
    ".csv",
    ".html",
    ".htm",
    ".epub",
    ".json",
    ".xml",
    ".rtf",
    ".msg",
)
"""Converted to markdown with ``markitdown`` before chunking, exactly as
``read_document`` does. Anything not listed here is left out of the index
rather than fed to a converter that would make something up about it."""


@dataclass(frozen=True)
class SemanticConfig:
    """Everything about the index that the model does not get to choose.

    Attributes:
        chunking: How a page is cut into chunks.
        ingest_roots: Directories ingestion is confined to. A pattern that
            resolves outside them is dropped — the patterns can come from the
            model, the roots cannot.
        text_extensions: Suffixes read as text through the backend.
        document_extensions: Suffixes converted with ``markitdown`` first.
            Requires the ``documents`` extra, which ``semantic`` pulls in.
        max_files_per_call: Ceiling on the files one ingest touches. When it
            trips, stale-chunk pruning is skipped for that call: a partial scan
            cannot tell a deleted file from one it never reached.
        batch_size: Documents per ``add_documents`` call.
        deterministic_ids: Derive each chunk's id from its page, section and
            position, so re-ingesting a file updates its chunks instead of
            duplicating them. Turn it off only for a store that assigns ids
            itself, and accept that incremental ingest degrades with it.
        trust_timestamps: Skip downloading a file whose size and modification
            time both match the manifest. Saves re-reading large sources; turn
            it off when a backend's timestamps are too coarse to trust.
        over_fetch: Multiplier on ``k`` when filters are applied client-side,
            so filtering does not empty the result set.
        snippet_chars: Longest snippet per hit in the text handed to the model.
            The full chunk always travels in the tool's artifact.
        corpus_name: Label written into every chunk's metadata.
        filter_builder: Converts the active filters into the store's own filter
            object, for server-side filtering. Left ``None``, filters are
            applied client-side.
        manifest_path: Where the ingest manifest is kept, on the bundle's
            virtual filesystem. The default sits under a dot-directory, so the
            linter (which walks ``**/*.md``) never sees it.
    """

    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    ingest_roots: tuple[str, ...] = ("/wiki", RAW_DIR)
    text_extensions: tuple[str, ...] = DEFAULT_TEXT_EXTENSIONS
    document_extensions: tuple[str, ...] = DEFAULT_DOCUMENT_EXTENSIONS
    max_files_per_call: int = 500
    batch_size: int = 64
    deterministic_ids: bool = True
    trust_timestamps: bool = True
    over_fetch: int = 4
    snippet_chars: int = 500
    corpus_name: str | None = None
    filter_builder: Callable[[dict[str, Any]], Any] | None = None
    manifest_path: str = "/.okf/semantic-manifest.json"


@dataclass
class IngestReport:
    """What one ingest did.

    Attributes:
        files: Files whose chunks were written.
        skipped: Files left alone because they had not changed.
        chunks: Chunks written.
        table_chunks: How many of those chunks are tables.
        deleted: Chunks removed from the store — superseded by a re-ingest, or
            belonging to a file that is gone.
        matched_files: Paths of the files that were ingested.
        errors: Per-file failures, as ``"<path>: <reason>"``. One unreadable
            source does not abort the run.
        truncated: Whether ``max_files_per_call`` cut the file list short.
    """

    files: int = 0
    skipped: int = 0
    chunks: int = 0
    table_chunks: int = 0
    deleted: int = 0
    matched_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    truncated: bool = False

    def summary(self) -> str:
        """Render the report as the one-paragraph text a model reads."""
        if not self.files and not self.deleted and not self.skipped:
            return (
                "Nothing was indexed: no file matched, or every match fell "
                "outside the directories ingestion is allowed to read."
            )
        shown = self.matched_files[:_FILES_IN_SUMMARY]
        head = ", ".join(path.rsplit("/", maxsplit=1)[-1] for path in shown)
        more = (
            f" and {self.files - _FILES_IN_SUMMARY} more"
            if self.files > _FILES_IN_SUMMARY
            else ""
        )
        parts = [
            (
                f"Indexed {self.chunks} chunk(s) from {self.files} file(s) "
                f"({self.table_chunks} of them tables)."
            )
        ]
        if self.matched_files:
            parts.append(f"Files: {head}{more}.")
        if self.skipped:
            parts.append(f"{self.skipped} file(s) were already up to date.")
        if self.deleted:
            parts.append(f"{self.deleted} outdated chunk(s) were removed.")
        if self.truncated:
            parts.append(
                "The per-call file limit was reached, so some files were not "
                "indexed and nothing was pruned; run the ingest again."
            )
        if self.errors:
            shown = "; ".join(self.errors[:5])
            parts.append(f"{len(self.errors)} file(s) failed: {shown}")
        return " ".join(parts)


@dataclass(frozen=True)
class _Source:
    """A file the index is about to look at."""

    path: str
    size: int | None = None
    modified_at: str | None = None

    @property
    def suffix(self) -> str:
        """The file's lowercased extension, ``""`` when it has none."""
        name = self.path.rsplit("/", maxsplit=1)[-1]
        return f".{name.rsplit('.', maxsplit=1)[-1].lower()}" if "." in name else ""


class SemanticIndex:
    """Ingestion and search over one bundle and one vector store.

    The bundle (through its backend), the embedding model, the store and the
    configuration are all fixed at construction. What a caller — or a model
    through a tool — chooses per call is only which files to ingest and what to
    search for.
    """

    def __init__(
        self,
        embeddings: Embeddings,
        vector_store: VectorStore,
        backend: BackendProtocol,
        *,
        search_k: int = 5,
        config: SemanticConfig | None = None,
    ) -> None:
        """Bind an index to a bundle and a store.

        Args:
            embeddings: Embedding model. Used directly only for a dense-only
                store, where embedding the query here avoids a round trip.
            vector_store: Where the chunks live. Hybrid dense + BM25 stores are
                detected and driven through their text query path, so the
                keyword half is not silently dropped.
            backend: Backend holding the bundle. Every read goes through it.
            search_k: Default number of results a search returns.
            config: Index configuration. Defaults to :class:`SemanticConfig`.
        """
        self._embeddings = embeddings
        self._store = vector_store
        self._backend = backend
        self._search_k = search_k
        self._config = config or SemanticConfig()

    # ------------------------------------------------------------ resolving --
    def _glob(self, pattern: str, path: str | None = None) -> list[_Source]:
        """Run one glob through the backend, returning what it matched."""
        result = self._backend.glob(pattern, path)
        if result.error and not result.matches:
            return []
        return [
            _Source(
                path=normalize(info["path"]),
                size=info.get("size"),
                modified_at=info.get("modified_at"),
            )
            for info in result.matches or []
            if not info.get("is_dir")
        ]

    def _expand(self, pattern: str) -> list[_Source]:
        """Expand one caller-supplied pattern into concrete files.

        Directories, single paths and globs all end up going through the
        backend's ``glob``, so every match arrives with the size and timestamp
        the incremental ingest needs.

        Args:
            pattern: A directory, a file path, or a glob, absolute or relative
                to the bundle root.

        Returns:
            The files it matched, unfiltered.
        """
        target = normalize(pattern)
        if any(magic in target for magic in _GLOB_MAGIC):
            return self._glob(target.lstrip("/"))

        if self._backend.ls(target).error is None:
            extensions = (
                *self._config.text_extensions,
                *self._config.document_extensions,
            )
            return [
                source
                for extension in extensions
                for source in self._glob(f"**/*{extension}", target)
            ]

        parent, _, name = target.rpartition("/")
        return [
            source
            for source in self._glob(name, parent or "/")
            if source.path == target
        ]

    def _resolve(self, patterns: Sequence[str]) -> list[_Source]:
        """Expand every pattern, then keep only what may be indexed.

        Args:
            patterns: Directories, paths or globs.

        Returns:
            The matching files, deduplicated and in a stable order: inside the
            configured roots, of a supported type, and never the manifest.
        """
        supported = {
            *self._config.text_extensions,
            *self._config.document_extensions,
        }
        manifest = normalize(self._config.manifest_path)
        found: dict[str, _Source] = {}
        for pattern in patterns:
            for source in self._expand(pattern):
                if source.path == manifest or source.path in found:
                    continue
                if not within(source.path, self._config.ingest_roots):
                    continue
                if source.suffix not in supported:
                    continue
                found[source.path] = source
        return [found[path] for path in sorted(found)]

    @staticmethod
    def _scan_prefixes(patterns: Sequence[str]) -> list[str]:
        """Return the directories a set of patterns actually looked at.

        A manifest entry is only a candidate for pruning if it sits under one
        of these: ingesting one directory must not delete the chunks of
        another.

        Args:
            patterns: The patterns of the ingest that just ran.

        Returns:
            One prefix per pattern — the part before its first glob character.
        """
        prefixes: list[str] = []
        for pattern in patterns:
            segments: list[str] = []
            for segment in normalize(pattern).strip("/").split("/"):
                if any(magic in segment for magic in _GLOB_MAGIC):
                    break
                segments.append(segment)
            prefixes.append("/" + "/".join(segments))
        return prefixes

    # ------------------------------------------------------------- manifest --
    def _read_manifest(self) -> dict[str, dict[str, Any]]:
        """Return the recorded state of every indexed file.

        A missing, unreadable or malformed manifest is not an error: it means
        the next ingest is a full one, which is always correct, only slower.
        """
        try:
            responses = self._backend.download_files([self._config.manifest_path])
        except (NotImplementedError, OSError):
            return {}
        if not responses or responses[0].error or responses[0].content is None:
            return {}
        try:
            payload = json.loads(responses[0].content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        files = payload.get("files")
        return files if isinstance(files, dict) else {}

    def _write_manifest(self, entries: dict[str, dict[str, Any]]) -> str | None:
        """Persist the manifest through the backend.

        Args:
            entries: The new per-file state.

        Returns:
            An error message when the manifest could not be written, ``None``
            on success. A failure only costs the *next* ingest its incremental
            fast path, so it is reported rather than raised.
        """
        payload = json.dumps(
            {
                "version": 1,
                "updated_at": datetime.now(UTC).isoformat(),
                "files": entries,
            },
            indent=2,
            sort_keys=True,
        )
        try:
            responses = self._backend.upload_files(
                [(self._config.manifest_path, payload.encode("utf-8"))]
            )
        except (NotImplementedError, OSError) as exc:
            return f"manifest not written: {exc}"
        if responses and responses[0].error:
            return f"manifest not written: {responses[0].error}"
        return None

    # -------------------------------------------------------------- loading --
    def _load(self, source: _Source) -> tuple[str, str]:
        """Return a file's markdown and the digest identifying its content.

        Text files are read through the backend and hashed as they are;
        everything else is pulled down as bytes, hashed, and converted with
        ``markitdown`` — the same two-step
        :func:`deep_wiki_agent.tools.documents.read_document` performs, reused
        rather than reimplemented.

        Args:
            source: The file to load.

        Returns:
            A ``(markdown, digest)`` pair.

        Raises:
            RuntimeError: If the backend cannot produce the file's content.
            ImportError: If a conversion is needed and the optional extra that
                provides ``markitdown`` is not installed.
        """
        if source.suffix in self._config.text_extensions:
            result = self._backend.read(source.path, limit=_FULL_READ_LIMIT)
            if result.error or result.file_data is None:
                msg = f"could not read {source.path}: {result.error}"
                raise RuntimeError(msg)
            text = result.file_data["content"]
            return text, content_hash(text.encode("utf-8"))

        data = _download(self._backend, source.path)
        return _to_markdown(data, source.path), content_hash(data)

    def _chunk_id(self, document: Document) -> str:
        """Derive a chunk's stable id from where it sits in the bundle."""
        metadata = document.metadata
        key = (
            f"{metadata['source']}::{metadata['header_path']}::"
            f"{metadata['chunk_index']}"
        )
        return str(uuid.uuid5(_ID_NAMESPACE, key))

    def _unchanged(self, previous: dict[str, Any], source: _Source) -> bool:
        """Return whether a file can be skipped without reading it."""
        return bool(
            self._config.trust_timestamps
            and source.size is not None
            and source.modified_at
            and previous.get("size") == source.size
            and previous.get("modified_at") == source.modified_at
        )

    # ------------------------------------------------------------- ingestion --
    def ingest(
        self,
        patterns: Sequence[str] | None = None,
        *,
        tags: Sequence[str] | None = None,
        only_modified: bool = True,
    ) -> IngestReport:
        """Index the bundle, or the part of it the patterns name.

        Args:
            patterns: Directories, file paths or globs to index. Defaults to
                the configured ``ingest_roots``, i.e. the whole bundle.
                Anything resolving outside those roots is dropped.
            tags: Labels written into every chunk's metadata, to filter
                searches by later.
            only_modified: Skip files whose content has not changed since the
                last ingest. Pass ``False`` to force a full rebuild — after a
                change to the chunking parameters, for instance, which the
                manifest has no way to notice.

        Returns:
            What the run did, as an :class:`IngestReport`.
        """
        targets = list(patterns) if patterns else list(self._config.ingest_roots)
        sources = self._resolve(targets)

        report = IngestReport(truncated=len(sources) > self._config.max_files_per_call)
        sources = sources[: self._config.max_files_per_call]

        recorded = self._read_manifest()
        entries: dict[str, dict[str, Any]] = {}
        documents: list[Document] = []
        ids: list[str] = []
        stale: list[str] = []

        for source in sources:
            previous = recorded.get(source.path)
            if only_modified and previous and self._unchanged(previous, source):
                report.skipped += 1
                entries[source.path] = previous
                continue

            try:
                text, digest = self._load(source)
            except (RuntimeError, ImportError, ValueError) as exc:
                report.errors.append(f"{source.path}: {exc}")
                if previous:
                    entries[source.path] = previous
                continue

            if only_modified and previous and previous.get("hash") == digest:
                report.skipped += 1
                entries[source.path] = {
                    **previous,
                    "size": source.size,
                    "modified_at": source.modified_at,
                }
                continue

            chunks = self._chunk(source, text, digest, tags)
            chunk_ids = [self._chunk_id(chunk) for chunk in chunks]
            documents.extend(chunks)
            ids.extend(chunk_ids)

            if previous:
                fresh = set(chunk_ids)
                stale.extend(i for i in previous.get("ids", []) if i not in fresh)

            report.files += 1
            report.chunks += len(chunks)
            report.table_chunks += sum(
                1 for chunk in chunks if chunk.metadata["content_type"] == "table"
            )
            report.matched_files.append(source.path)
            entries[source.path] = {
                "hash": digest,
                "size": source.size,
                "modified_at": source.modified_at,
                "ids": chunk_ids,
                "ingested_at": datetime.now(UTC).isoformat(),
            }

        stale.extend(
            self._prune(recorded, entries, targets, truncated=report.truncated)
        )
        self._write(documents, ids)
        report.deleted = self._delete(stale, report)

        error = self._write_manifest(entries)
        if error:
            report.errors.append(error)
        return report

    def _chunk(
        self,
        source: _Source,
        text: str,
        digest: str,
        tags: Sequence[str] | None,
    ) -> list[Document]:
        """Cut one loaded file into chunks, tagged with where it came from."""
        extra: dict[str, Any] = {}
        if self._config.corpus_name:
            extra["corpus"] = self._config.corpus_name
        if tags:
            extra["tags"] = list(tags)
        return chunk_markdown(
            text,
            path=source.path,
            file_hash=digest,
            area="raw" if within(source.path, [RAW_DIR]) else "wiki",
            config=self._config.chunking,
            modified_at=source.modified_at,
            extra_metadata=extra or None,
        )

    def _prune(
        self,
        recorded: dict[str, dict[str, Any]],
        entries: dict[str, dict[str, Any]],
        targets: Sequence[str],
        *,
        truncated: bool,
    ) -> list[str]:
        """Collect the chunk ids of files that were indexed and are now gone.

        Args:
            recorded: The manifest as it was before this run.
            entries: The manifest being built, mutated here to carry forward
                every file this run did not look at.
            targets: The patterns this run was given.
            truncated: Whether the file list was cut short. When it was, the
                run cannot tell a deleted file from one it never reached, so
                nothing is pruned.

        Returns:
            The ids to delete from the store.
        """
        prefixes = self._scan_prefixes(targets)
        stale: list[str] = []
        for path, entry in recorded.items():
            if path in entries:
                continue
            if truncated or not within(path, prefixes):
                entries[path] = entry
                continue
            stale.extend(entry.get("ids", []))
        return stale

    def _write(self, documents: list[Document], ids: list[str]) -> None:
        """Add the new chunks to the store, in batches."""
        size = self._config.batch_size
        keep_ids = self._config.deterministic_ids
        for start in range(0, len(documents), size):
            batch = documents[start : start + size]
            batch_ids = ids[start : start + size] if keep_ids else None
            self._store.add_documents(batch, ids=batch_ids)

    def _delete(self, stale: list[str], report: IngestReport) -> int:
        """Remove superseded chunks, tolerating a store that cannot.

        Args:
            stale: Ids to remove.
            report: Report to note a refusal on.

        Returns:
            How many chunks were removed.
        """
        if not stale:
            return 0
        try:
            self._store.delete(ids=stale)
        except (NotImplementedError, TypeError) as exc:
            report.errors.append(
                f"{len(stale)} outdated chunk(s) could not be deleted: {exc}"
            )
            return 0
        return len(stale)

    # ---------------------------------------------------------------- search --
    def search(
        self,
        query: str,
        *,
        k: int | None = None,
        area: str = "any",
        content_type: str = "any",
        path_contains: str | None = None,
        section_contains: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find the passages closest to a query.

        Args:
            query: What to look for, in natural language. Both halves of the
                search see it: the dense one embeds it, and the lexical one —
                when the store is hybrid — matches its terms.
            k: How many results to return. Defaults to the index's ``search_k``.
            area: ``"wiki"`` for the wiki's own pages, ``"raw"`` for the source
                documents, ``"any"`` for both.
            content_type: ``"table"``, ``"text"``, or ``"any"``.
            path_contains: Restrict to files whose path contains this substring.
            section_contains: Restrict to sections whose heading path contains
                this substring.

        Returns:
            One dict per hit — rank, score, text, file, section, content type
            and the full chunk metadata — ordered best first.
        """
        limit = k or self._search_k
        filters = {
            "area": area,
            "content_type": content_type,
            "path_contains": path_contains,
            "section_contains": section_contains,
        }
        active = {
            key: value for key, value in filters.items() if value and value != "any"
        }

        kwargs: dict[str, Any] = {"k": limit}
        if active and self._config.filter_builder is not None:
            kwargs["filter"] = self._config.filter_builder(active)
        elif active:
            kwargs["k"] = limit * self._config.over_fetch

        pairs = self._retrieve(query, kwargs)

        results: list[dict[str, Any]] = []
        for document, score in pairs:
            metadata = document.metadata
            if (
                active
                and self._config.filter_builder is None
                and not _matches(metadata, filters)
            ):
                continue
            results.append(
                {
                    "rank": len(results) + 1,
                    "score": float(score) if score is not None else None,
                    "text": document.page_content,
                    "file": metadata.get("source") or metadata.get("relative_path"),
                    "area": metadata.get("area", ""),
                    "section": metadata.get("section", ""),
                    "parent_sections": metadata.get("parent_sections", []),
                    "header_path": metadata.get("header_path", ""),
                    "content_type": metadata.get("content_type", "text"),
                    "chunk_index": metadata.get("chunk_index"),
                    "metadata": metadata,
                }
            )
            if len(results) >= limit:
                break
        return results

    def _retrieve(
        self, query: str, kwargs: dict[str, Any]
    ) -> list[tuple[Document, float]]:
        """Query the store, keeping the lexical half of a hybrid store alive.

        A hybrid or sparse store searched by vector would quietly answer with
        its dense half alone, disabling BM25 without saying so. Only a
        dense-only store is queried by vector; everything else is handed the
        query as text.
        """
        mode = getattr(self._store, "retrieval_mode", None)
        dense_only = (
            mode is None or str(getattr(mode, "value", mode)).lower() == "dense"
        )
        by_vector = hasattr(self._store, "similarity_search_with_score_by_vector")

        if dense_only and by_vector:
            vector = self._embeddings.embed_query(query)
            return list(
                self._store.similarity_search_with_score_by_vector(vector, **kwargs)
            )
        return list(self._store.similarity_search_with_score(query, **kwargs))


def _matches(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Return whether one chunk's metadata satisfies the active filters."""
    area = filters.get("area")
    if area and area != "any" and metadata.get("area") != area:
        return False

    content_type = filters.get("content_type")
    if (
        content_type
        and content_type != "any"
        and metadata.get("content_type") != content_type
    ):
        return False

    path_contains = filters.get("path_contains")
    haystack = str(metadata.get("source") or metadata.get("relative_path") or "")
    if path_contains and path_contains.lower() not in haystack.lower():
        return False

    section_contains = filters.get("section_contains")
    return not (
        section_contains
        and section_contains.lower() not in str(metadata.get("header_path", "")).lower()
    )
