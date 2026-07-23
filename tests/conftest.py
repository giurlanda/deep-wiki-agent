"""Shared fixtures: a minimal but OKF-conformant bundle on disk."""

from __future__ import annotations

from pathlib import Path

import pytest

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
def model() -> str:
    """A model identifier that is never actually called.

    Every test here inspects graph construction, never runs inference, so no
    provider credentials are needed. deepagents resolves the string lazily.
    """
    return "anthropic:claude-sonnet-5"
