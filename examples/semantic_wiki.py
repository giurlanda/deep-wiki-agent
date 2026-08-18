"""Index an OKF bundle for semantic + keyword search, then query it.

Run ``examples/build_wiki.py`` first to have a bundle, start a local Qdrant,
then:

    docker run -p 6333:6333 qdrant/qdrant
    uv sync --extra semantic --extra examples
    uv add langchain-qdrant qdrant-client fastembed
    export ANTHROPIC_API_KEY=... OPENAI_API_KEY=...
    uv run python examples/semantic_wiki.py

Two things this shows that the README cannot:

- ingestion runs as a plain function, before any agent exists, and reports what
  it did — a second run reports that everything was already up to date;
- the store is in hybrid mode, so the dense half and BM25 both see the query.
  Qdrant is one choice among many: any LangChain `VectorStore` works, and the
  library pins none.
"""

from __future__ import annotations

from pathlib import Path

from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from deep_wiki_agent import create_deep_wiki_agent, ingest_semantic_index

MODEL = "anthropic:claude-sonnet-5"
WIKI = Path(__file__).parent / "contracts-wiki"
COLLECTION = "contracts-wiki"
QDRANT_URL = "http://localhost:6333"

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"

QUESTION = "What confidentiality obligations are set out in the Acme contract?"


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
    if not WIKI.is_dir():
        msg = f"{WIKI} does not exist — run examples/build_wiki.py first"
        raise SystemExit(msg)

    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    store = build_store(embeddings)

    # Deterministic ingestion: no model decides whether this runs.
    report = ingest_semantic_index(embeddings, store, wiki_path=WIKI)
    print("=== ingest\n")
    print(report.summary())

    print("\n=== ingest again (nothing should change)\n")
    print(ingest_semantic_index(embeddings, store, wiki_path=WIKI).summary())

    reader = create_deep_wiki_agent(
        model=MODEL,
        wiki_path=WIKI,
        embeddings=embeddings,
        vector_store=store,
        search_k=8,
    )

    result = reader.invoke(
        {"messages": [{"role": "user", "content": QUESTION}]},
        config={"configurable": {"thread_id": "semantic-1"}},
    )
    print(f"\n=== {QUESTION}\n")
    print(result["messages"][-1].content)


if __name__ == "__main__":
    main()
