"""Semantic ingestion and search, exposed as tools.

Two tools come out of one factory, because they are two halves of the same
index: :data:`SEMANTIC_INGEST_TOOL_NAME` writes the bundle into a vector store,
:data:`SEMANTIC_SEARCH_TOOL_NAME` queries it. The manager agent gets both — it
is the agent that changes the bundle, so it is the one that has to keep the
index current. The reader gets only the search: it is read-only over the
bundle, and an index it could rewrite would not be read-only in any useful
sense.

The embedding model, the vector store and the bundle are captured in the
closure, never taken as tool arguments — the model chooses what to index and
what to look for, not where to read from or where to write to. Reads go
through the deepagents backend, so the index covers exactly the tree the
agent's own file tools see.

Both operations are also plain Python: :func:`ingest_semantic_index` is the
same code path without an agent in it, for a deterministic ingestion job.

Requires the optional ``semantic`` extra::

    pip install "deep-wiki-agent[semantic]"
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from deep_wiki_agent.semantic.index import IngestReport, SemanticConfig, SemanticIndex
from deep_wiki_agent.tools.documents import _resolve_backend

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from deepagents.backends.protocol import BackendProtocol
    from langchain_core.embeddings import Embeddings
    from langchain_core.tools import BaseTool
    from langchain_core.vectorstores import VectorStore

__all__ = [
    "SEMANTIC_INGEST_TOOL_NAME",
    "SEMANTIC_SEARCH_TOOL_NAME",
    "SemanticTools",
    "create_semantic_tools",
    "ingest_semantic_index",
]

SEMANTIC_INGEST_TOOL_NAME = "semantic_ingest"
SEMANTIC_SEARCH_TOOL_NAME = "semantic_search"

_MAX_K = 25
_K_DESCRIPTION = "How many passages to return."


def _search_args_schema(default_k: int) -> type[BaseModel]:
    """Return the search schema with ``k`` defaulted to this index's setting.

    The schema is what the model sees, so a ``search_k`` that only reached the
    Python function would be silently overridden the moment the model omitted
    ``k`` — which is most of the time.

    Args:
        default_k: Number of passages to return when the model does not say.

    Returns:
        A subclass of :class:`SemanticSearchArgs` with that default in place.
    """
    return create_model(
        "SemanticSearchArgs",
        __base__=SemanticSearchArgs,
        k=(int, Field(default=default_k, ge=1, le=_MAX_K, description=_K_DESCRIPTION)),
    )


class SemanticIngestArgs(BaseModel):
    """Arguments of the ingestion tool."""

    patterns: list[str] | None = Field(
        default=None,
        description=(
            "Files, directories or glob patterns to index, as bundle paths "
            "(e.g. `/wiki/concepts`, `/raw/report.pdf`, `/wiki/**/*.md`). "
            "Leave it out to index the whole bundle, which is the usual case "
            "after an ingest. Paths outside the wiki and its sources are ignored."
        ),
    )
    tags: list[str] | None = Field(
        default=None,
        description=(
            "Optional labels stored with every chunk this call indexes, so "
            "later searches can be narrowed to them."
        ),
    )
    only_modified: bool = Field(
        default=True,
        description=(
            "Index only what changed since the last run. Keep it true for the "
            "routine update after writing pages; set it to false to rebuild "
            "the index from scratch."
        ),
    )


class SemanticSearchArgs(BaseModel):
    """Arguments of the search tool."""

    query: str = Field(
        ...,
        description=(
            "What you are looking for, in natural language, including the "
            "technical terms you expect on the page: the search matches "
            "meaning and wording at once."
        ),
    )
    k: int = Field(default=5, ge=1, le=_MAX_K, description=_K_DESCRIPTION)
    area: Literal["any", "wiki", "raw"] = Field(
        default="wiki",
        description=(
            "Where to look. `wiki` searches the wiki's own pages and is the "
            "right default; `raw` searches the source documents, for a detail "
            "the pages did not capture; `any` searches both."
        ),
    )
    content_type: Literal["any", "text", "table"] = Field(
        default="any",
        description=(
            "Filter by chunk kind. Use `table` when you are after tabulated "
            "figures, limits or parameters, `text` for prose."
        ),
    )
    path_contains: str | None = Field(
        default=None,
        description="Restrict the search to files whose path contains this substring.",
    )
    section_contains: str | None = Field(
        default=None,
        description=(
            "Restrict the search to sections whose heading — or a parent "
            "heading — contains this substring."
        ),
    )


@dataclass
class SemanticTools:
    """The tools of one index, plus the functions underneath them.

    Attributes:
        ingest_tool: The ``semantic_ingest`` tool.
        search_tool: The ``semantic_search`` tool.
        index: The :class:`~deep_wiki_agent.semantic.index.SemanticIndex` both
            tools drive, for callers who want it directly.
    """

    ingest_tool: BaseTool
    search_tool: BaseTool
    index: SemanticIndex

    @property
    def ingest(self) -> Callable[..., IngestReport]:
        """The ingestion function, without the tool wrapper."""
        return self.index.ingest

    @property
    def search(self) -> Callable[..., list[dict[str, Any]]]:
        """The search function, without the tool wrapper."""
        return self.index.search

    def as_list(self) -> list[BaseTool]:
        """Both tools, in the order an agent should be given them."""
        return [self.ingest_tool, self.search_tool]


def create_semantic_tools(
    embeddings: Embeddings,
    vector_store: VectorStore,
    *,
    wiki_path: str | Path | None = None,
    backend: BackendProtocol | None = None,
    search_k: int = 5,
    config: SemanticConfig | None = None,
    virtual_mode: bool = True,
) -> SemanticTools:
    """Build the ingestion and search tools for one bundle and one store.

    Pass the result's tools to a factory, or let
    :func:`~deep_wiki_agent.factory.create_wiki_manager_agent` build them for
    you by handing it ``embeddings`` and ``vector_store`` directly::

        from deep_wiki_agent import create_semantic_tools

        semantic = create_semantic_tools(
            OpenAIEmbeddings(model="text-embedding-3-small"),
            QdrantVectorStore(...),
            wiki_path="./my-wiki",
        )
        semantic.ingest()          # deterministic, no agent involved

    Args:
        embeddings: Embedding model for the chunks and the queries.
        vector_store: Store the chunks are written to and searched in. A store
            configured for hybrid retrieval keeps its keyword half: the query
            reaches it as text rather than as a vector.
        wiki_path: Directory of the bundle, on the local filesystem. Mutually
            exclusive with ``backend``.
        backend: A pre-built deepagents backend (state, store, sandbox or
            filesystem) holding the bundle. Mutually exclusive with
            ``wiki_path``. Must implement ``download_files`` if the bundle
            holds sources that need converting.
        search_k: Default number of passages a search returns.
        config: Index configuration — chunking, the directories ingestion may
            read, batch sizes, the manifest location. Defaults to
            :class:`~deep_wiki_agent.semantic.index.SemanticConfig`.
        virtual_mode: Confine the assembled filesystem backend to
            ``wiki_path``. Ignored when ``backend`` is given.

    Returns:
        The two tools and the index they drive.

    Raises:
        ValueError: If neither or both of ``wiki_path`` and ``backend`` are
            given, or if ``search_k`` is not positive.
        NotADirectoryError: If ``wiki_path`` is not an existing directory.
    """
    if not 1 <= search_k <= _MAX_K:
        msg = f"search_k must be positive and at most {_MAX_K}, got {search_k}"
        raise ValueError(msg)

    # Resolved once, at build time: a bad bundle should fail where the agent is
    # assembled, not on the model's first tool call.
    target = _resolve_backend(wiki_path, backend, virtual_mode=virtual_mode)
    index = SemanticIndex(
        embeddings, vector_store, target, search_k=search_k, config=config
    )

    def semantic_ingest(
        patterns: list[str] | None = None,
        tags: list[str] | None = None,
        *,
        only_modified: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        """Index the bundle so it can be searched by meaning."""
        report = index.ingest(patterns, tags=tags, only_modified=only_modified)
        return report.summary(), asdict(report)

    def semantic_search(
        query: str,
        k: int = search_k,
        area: str = "wiki",
        content_type: str = "any",
        path_contains: str | None = None,
        section_contains: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Search the index and render the hits for the model."""
        results = index.search(
            query,
            k=k,
            area=area,
            content_type=content_type,
            path_contains=path_contains,
            section_contains=section_contains,
        )
        if not results:
            return (
                "No passage matched. Try different wording, widen `area`, or "
                "drop the filters — and remember the index may not cover "
                "everything the bundle holds.",
                [],
            )
        return _render(results, config.snippet_chars if config else 500), results

    ingest_tool = StructuredTool.from_function(
        func=semantic_ingest,
        name=SEMANTIC_INGEST_TOOL_NAME,
        description=(
            "Index the wiki's pages and source documents into the semantic "
            "search index, so `semantic_search` can find them. Run it after "
            "writing or rewriting pages, and after new sources arrive. "
            "Re-indexing a file updates it rather than duplicating it, and "
            "unchanged files are skipped, so running this is cheap."
        ),
        args_schema=SemanticIngestArgs,
        response_format="content_and_artifact",
    )

    search_tool = StructuredTool.from_function(
        func=semantic_search,
        name=SEMANTIC_SEARCH_TOOL_NAME,
        description=(
            "Search the wiki by meaning as well as by wording, and get back "
            "the closest passages with the page and section they came from. "
            "Use it to find the pages worth opening when the indexes and the "
            "link graph do not obviously lead to them. It returns excerpts, "
            "not pages: open what it finds and read it before relying on it."
        ),
        args_schema=_search_args_schema(search_k),
        response_format="content_and_artifact",
    )

    return SemanticTools(ingest_tool=ingest_tool, search_tool=search_tool, index=index)


def _render(results: list[dict[str, Any]], snippet_chars: int) -> str:
    """Render search hits as the text the model reads.

    The full chunks travel in the tool's artifact; what the model sees is
    trimmed, because a handful of long chunks is how a context window is lost.

    Args:
        results: Hits, best first.
        snippet_chars: Longest excerpt per hit.

    Returns:
        One block per hit, separated by a rule.
    """
    blocks: list[str] = []
    for result in results:
        body = result["text"]
        if len(body) > snippet_chars:
            body = body[:snippet_chars].rstrip() + " […]"
        score = f"{result['score']:.4f}" if result["score"] is not None else "n/a"
        blocks.append(
            f"[{result['rank']}] file: {result['file']} | section: "
            f"{result['header_path'] or '(page root)'} | type: "
            f"{result['content_type']} | score: {score}\n{body}"
        )
    return "\n\n---\n\n".join(blocks)


def ingest_semantic_index(
    embeddings: Embeddings,
    vector_store: VectorStore,
    *,
    wiki_path: str | Path | None = None,
    backend: BackendProtocol | None = None,
    patterns: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    only_modified: bool = True,
    config: SemanticConfig | None = None,
    virtual_mode: bool = True,
) -> IngestReport:
    """Index a bundle without an agent in the loop.

    The same code path the ``semantic_ingest`` tool runs, exposed for a
    deterministic job — a cron entry, a post-commit hook, the rebuild step of a
    deployment — where nothing should depend on a model deciding to call a tool.

    Args:
        embeddings: Embedding model for the chunks.
        vector_store: Store the chunks are written to.
        wiki_path: Directory of the bundle, on the local filesystem. Mutually
            exclusive with ``backend``.
        backend: A pre-built deepagents backend holding the bundle. Mutually
            exclusive with ``wiki_path``.
        patterns: Files, directories or globs to index. Defaults to the whole
            bundle.
        tags: Labels stored with every chunk this call indexes.
        only_modified: Skip files that have not changed since the last run.
        config: Index configuration.
        virtual_mode: Confine the assembled filesystem backend to ``wiki_path``.

    Returns:
        What the run did, as an
        :class:`~deep_wiki_agent.semantic.index.IngestReport`.

    Raises:
        ValueError: If neither or both of ``wiki_path`` and ``backend`` are given.
        NotADirectoryError: If ``wiki_path`` is not an existing directory.
    """
    target = _resolve_backend(wiki_path, backend, virtual_mode=virtual_mode)
    index = SemanticIndex(embeddings, vector_store, target, config=config)
    return index.ingest(patterns, tags=tags, only_modified=only_modified)
