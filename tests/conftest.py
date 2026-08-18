"""Shared fixtures: a minimal but OKF-conformant bundle on disk."""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.backends import StoreBackend
from langgraph.store.memory import InMemoryStore

INDEX_MD = """\
# Test wiki

- [concepts](concepts/index.md)
"""

CONCEPTS_INDEX_MD = """\
# Concepts

- [margine-operativo](margine-operativo.md)
"""

CONCEPT_MD = """\
---
type: Concept
title: Margine operativo
description: Rapporto tra risultato operativo e ricavi.
tags: [bilancio]
timestamp: 2026-07-19T10:30:00Z
---

# Margine operativo

Definito come risultato operativo diviso ricavi netti.
"""

LOG_MD = """\
# Log

## [2026-07-19] ingest | Seed
- Creato: concepts/margine-operativo.md
"""


@pytest.fixture
def wiki_path(tmp_path: Path) -> Path:
    """Create a small, conformant OKF bundle and return its root."""
    root = tmp_path / "wiki"
    (root / "concepts").mkdir(parents=True)
    (root / "raw").mkdir()
    (root / "index.md").write_text(INDEX_MD, encoding="utf-8")
    (root / "log.md").write_text(LOG_MD, encoding="utf-8")
    (root / "concepts" / "index.md").write_text(CONCEPTS_INDEX_MD, encoding="utf-8")
    (root / "concepts" / "margine-operativo.md").write_text(
        CONCEPT_MD, encoding="utf-8"
    )
    (root / "raw" / "source.txt").write_text("fonte originale\n", encoding="utf-8")
    return root


@pytest.fixture
def store_backend() -> StoreBackend:
    """A `StoreBackend`, pre-populated with the same conformant bundle as `wiki_path`.

    Backed by a plain `InMemoryStore` rather than a LangGraph runtime, so it
    can be exercised directly in a unit test — the same shape a real deep
    agent would hand the linter, without needing a compiled graph.
    """
    backend = StoreBackend(store=InMemoryStore(), namespace=lambda _rt: ("wiki",))
    pages = {
        "/index.md": INDEX_MD,
        "/log.md": LOG_MD,
        "/concepts/index.md": CONCEPTS_INDEX_MD,
        "/concepts/margine-operativo.md": CONCEPT_MD,
        "/raw/source.txt": "fonte originale\n",
    }
    for path, content in pages.items():
        backend.write(path, content)
    return backend


@pytest.fixture
def embeddings():
    """A deterministic stand-in for a real embedding model.

    Hashes text into a fixed-width vector, so the same page always embeds to
    the same point and no test needs a provider, a network, or a GPU. Nothing
    here asserts on semantic proximity — that is the model's job, not the
    index's.
    """
    from langchain_core.embeddings import DeterministicFakeEmbedding

    return DeterministicFakeEmbedding(size=32)


@pytest.fixture
def vector_store(embeddings):
    """An in-memory vector store, which supports ids and deletion.

    Both matter here: the incremental ingest identifies chunks by id and
    removes the ones a rewritten or deleted page left behind.
    """
    from langchain_core.vectorstores import InMemoryVectorStore

    return InMemoryVectorStore(embeddings)


@pytest.fixture
def model() -> str:
    """A model identifier that is never actually called.

    Every test here inspects graph construction, never runs inference, so no
    provider credentials are needed. deepagents resolves the string lazily.
    """
    return "anthropic:claude-sonnet-5"
