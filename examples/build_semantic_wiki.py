"""Bootstrap an OKF wiki, ingest a source document, then index it for
semantic + keyword search and query it.

This combines ``examples/build_document_wiki.py`` (manager agent bootstraps the bundle
and ingests ``raw/``) with ``examples/semantic_wiki.py`` (deterministic
semantic indexing over a hybrid Qdrant store, then a read-only agent that
searches it).

Run:
    docker run -p 6333:6333 qdrant/qdrant
    uv sync --extra semantic --extra examples
    uv add langchain-qdrant qdrant-client fastembed
    export OPENAI_API_KEY=...
    uv run python examples/build_semanti_wiki.py

Expects ``examples/contracts-wiki/raw/`` to already contain the source
documents to ingest.
"""

from __future__ import annotations

from pathlib import Path
import shutil

from debug_middleware import DebugMiddleware
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from deep_wiki_agent import (
    create_deep_wiki_agent,
    create_wiki_manager_agent,
    ingest_semantic_index,
)
from deep_wiki_agent.tools.documents import create_read_document_tool

MODEL = "anthropic:claude-sonnet-5"
WIKI = Path(__file__).parent / "contracts-wiki"
SOURCE = Path(__file__).parent / "assets"

COLLECTION = "semantic-wiki"
QDRANT_URL = "http://localhost:6333"

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"

QUESTION = (
    "What are the six core capabilities of Deep Agents, and what four "
    "layers make up its execution environment?"
)


def build_store(embeddings: OpenAIEmbeddings) -> QdrantVectorStore:
    """Create the collection if needed and return a hybrid store over it."""
    client = QdrantClient(url=QDRANT_URL)
    sparse = FastEmbedSparse(model_name="Qdrant/bm25")

    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config={
                DENSE_VECTOR: qmodels.VectorParams(
                    size=len(embeddings.embed_query("probe")),
                    distance=qmodels.Distance.COSINE,
                )
            },
            # Modifier.IDF is mandatory for BM25: Qdrant computes the IDF over
            # the corpus itself, and without it the sparse half scores nothing.
            sparse_vectors_config={
                SPARSE_VECTOR: qmodels.SparseVectorParams(modifier=qmodels.Modifier.IDF)
            },
        )

    return QdrantVectorStore(
        client=client,
        collection_name=COLLECTION,
        embedding=embeddings,
        vector_name=DENSE_VECTOR,
        sparse_embedding=sparse,
        sparse_vector_name=SPARSE_VECTOR,
        retrieval_mode=RetrievalMode.HYBRID,
    )


def main() -> None:
    (WIKI / "raw").mkdir(parents=True, exist_ok=True)
    
    for source_file in SOURCE.iterdir():
        if source_file.is_file():
            shutil.copy2(source_file, WIKI / "raw" / source_file.name)

    manager = create_wiki_manager_agent(
        model=MODEL, wiki_path=WIKI, middleware=[DebugMiddleware()],
        tools=[create_read_document_tool(wiki_path=WIKI)]
    )

    result = manager.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Bootstrap this wiki for technical documentation "
                        "(default categories, unsupervised "
                        "ingest), then ingest all file in raw/ . Run the "
                        "linter when you are done."
                    ),
                }
            ]
        },
        config={"configurable": {"thread_id": "build-1"}},
    )

    print(result["messages"][-1].content)
    print(f"\nBundle written to {WIKI}")

    # local embedding model hosted on LMStudio
    embeddings = OpenAIEmbeddings(
        model="text-embedding-embeddinggemma-300m",
        base_url="http://127.0.0.1:1234/v1",
        api_key="no-key",
        check_embedding_ctx_length=False,
    )
    store = build_store(embeddings)

    # Deterministic ingestion: no model decides whether this runs.
    print("\n=== semantic ingest\nStart ingestion...")
    report = ingest_semantic_index(embeddings, store, wiki_path=WIKI)
    print(report.summary())

    print("\n=== semantic ingest again (nothing should change)\n")
    print(ingest_semantic_index(embeddings, store, wiki_path=WIKI).summary())

    reader = create_deep_wiki_agent(
        model=MODEL,
        wiki_path=WIKI,
        middleware=[DebugMiddleware()],
        embeddings=embeddings,
        vector_store=store,
        search_k=8,
    )

    answer = reader.invoke(
        {"messages": [{"role": "user", "content": QUESTION}]},
        config={"configurable": {"thread_id": "semantic-1"}},
    )
    print(f"\n=== {QUESTION}\n")
    print(answer["messages"][-1].content)


if __name__ == "__main__":
    main()
