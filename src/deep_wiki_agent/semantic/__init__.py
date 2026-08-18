"""Optional semantic + keyword search over an OKF bundle.

The bundle's own navigation — indexes, links, `grep` — is what both agents use
by default, and at a few hundred pages it is enough. Past that it degrades:
the category indexes get long, and a page nobody linked from the right place
stops being findable. This subpackage adds the other route in, without
changing what a bundle *is*: the vector index lives outside it, holds nothing
the bundle does not, and can be deleted and rebuilt at any time.

Requires the optional extra::

    pip install "deep-wiki-agent[semantic]"

Typical use is through the agent factories, which build the tools and unlock
the matching prompt section when they are given an embedding model and a
store::

    manager = create_wiki_manager_agent(
        model="anthropic:claude-sonnet-5",
        wiki_path="./my-wiki",
        embeddings=OpenAIEmbeddings(model="text-embedding-3-small"),
        vector_store=store,
    )

:func:`~deep_wiki_agent.semantic.tools.create_semantic_tools` and
:func:`~deep_wiki_agent.semantic.tools.ingest_semantic_index` are the same
machinery without an agent, for building or refreshing an index from a script.
"""

from deep_wiki_agent.semantic.chunking import (
    DEFAULT_HEADERS,
    ChunkingConfig,
    chunk_markdown,
)
from deep_wiki_agent.semantic.index import (
    IngestReport,
    SemanticConfig,
    SemanticIndex,
)
from deep_wiki_agent.semantic.tools import (
    SEMANTIC_INGEST_TOOL_NAME,
    SEMANTIC_SEARCH_TOOL_NAME,
    SemanticTools,
    create_semantic_tools,
    ingest_semantic_index,
)

__all__ = [
    "DEFAULT_HEADERS",
    "SEMANTIC_INGEST_TOOL_NAME",
    "SEMANTIC_SEARCH_TOOL_NAME",
    "ChunkingConfig",
    "IngestReport",
    "SemanticConfig",
    "SemanticIndex",
    "SemanticTools",
    "chunk_markdown",
    "create_semantic_tools",
    "ingest_semantic_index",
]
