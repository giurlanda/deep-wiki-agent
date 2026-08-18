"""Tests for the semantic index: what it reads, what it skips, what it deletes.

Two properties carry the weight here. The index reads through the backend, so
the same bundle held in a directory and in a store must index identically. And
a second ingest has to be cheap and *correct*: unchanged files skipped,
rewritten pages updated rather than duplicated, and the chunks of a page that
shrank or disappeared actually removed — an index that only ever grows answers
questions with pages that no longer exist.
"""

from __future__ import annotations

import json

import pytest

from deep_wiki_agent.semantic.index import SemanticConfig, SemanticIndex
from test_document_tool import PDF_TEXT, minimal_pdf

CONCEPT_MD = """\
---
type: Concept
title: Operating margin
---

# Operating margin

Operating income divided by net revenue.

## Thresholds

| Year | Margin |
|---|---|
| 2024 | 12% |
| 2025 | 14% |
"""

ENTITY_MD = """\
---
type: Entity
title: Acme SpA
---

# Acme SpA

A manufacturer of industrial pumps.
"""

INDEX_MD = "# Wiki\n\n- [concepts](concepts/index.md)\n"


@pytest.fixture
def bundle(tmp_path):
    """A bundle in the layout the prompts describe: `wiki/` beside `raw/`."""
    root = tmp_path / "bundle"
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "raw").mkdir()
    (root / "wiki" / "index.md").write_text(INDEX_MD, encoding="utf-8")
    (root / "wiki" / "concepts" / "operating-margin.md").write_text(
        CONCEPT_MD, encoding="utf-8"
    )
    (root / "wiki" / "entities" / "acme-spa.md").write_text(ENTITY_MD, encoding="utf-8")
    (root / "raw" / "note.txt").write_text(
        "Raw note about margins.\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def config():
    """Index configuration with the timestamp fast path off.

    A test rewrites a file within the same second as it wrote it, so the
    size-and-mtime shortcut would report "unchanged" for a real edit. The
    digest never lies, so the slow path is the one under test here; the fast
    path has a test of its own.
    """
    return SemanticConfig(trust_timestamps=False)


@pytest.fixture
def index(embeddings, vector_store, bundle, config):
    """An index over the on-disk bundle."""
    from deepagents.backends import FilesystemBackend

    backend = FilesystemBackend(root_dir=bundle, virtual_mode=True)
    return SemanticIndex(embeddings, vector_store, backend, config=config)


def stored_ids(vector_store):
    """Return the ids currently held by the in-memory store."""
    return set(vector_store.store)


class TestWhatGetsIndexed:
    def test_it_indexes_the_pages_and_the_sources(self, index):
        report = index.ingest()

        assert report.files == 4
        assert set(report.matched_files) == {
            "/raw/note.txt",
            "/wiki/concepts/operating-margin.md",
            "/wiki/entities/acme-spa.md",
            "/wiki/index.md",
        }
        assert report.errors == []

    def test_tables_are_counted_as_their_own_chunks(self, index):
        report = index.ingest()

        assert report.table_chunks == 1

    def test_the_area_records_which_half_of_the_bundle_a_chunk_came_from(
        self, index, vector_store
    ):
        index.ingest()

        areas = {
            document.metadata["source"]: document.metadata["area"]
            for document in vector_store.store.values()
            for document in [_as_document(document)]
        }

        assert areas["/raw/note.txt"] == "raw"
        assert areas["/wiki/index.md"] == "wiki"

    def test_a_path_outside_the_ingest_roots_is_refused(self, index, bundle):
        (bundle / "secret.md").write_text("# Secret\n\nNot for the index.\n", "utf-8")

        report = index.ingest(["/secret.md"])

        assert report.files == 0
        assert report.matched_files == []

    def test_an_unsupported_format_is_left_out(self, index, bundle):
        (bundle / "raw" / "archive.zip").write_bytes(b"PK\x03\x04not really")

        report = index.ingest()

        assert "/raw/archive.zip" not in report.matched_files
        assert report.errors == []

    def test_a_single_file_can_be_indexed_on_its_own(self, index):
        report = index.ingest(["/wiki/entities/acme-spa.md"])

        assert report.matched_files == ["/wiki/entities/acme-spa.md"]

    def test_a_glob_selects_a_subtree(self, index):
        report = index.ingest(["/wiki/concepts/**/*.md"])

        assert report.matched_files == ["/wiki/concepts/operating-margin.md"]

    def test_the_manifest_never_indexes_itself(self, index):
        index.ingest()

        report = index.ingest(only_modified=False)

        assert all("semantic-manifest" not in path for path in report.matched_files)


class TestIncrementalIngest:
    def test_a_second_run_reindexes_nothing(self, index):
        first = index.ingest()

        second = index.ingest()

        assert second.files == 0
        assert second.chunks == 0
        assert second.skipped == first.files

    def test_only_modified_false_forces_a_rebuild(self, index):
        index.ingest()

        report = index.ingest(only_modified=False)

        assert report.files == 4
        assert report.skipped == 0

    def test_an_edited_page_is_the_only_one_reindexed(self, index, bundle):
        index.ingest()
        (bundle / "wiki" / "entities" / "acme-spa.md").write_text(
            ENTITY_MD.replace("industrial pumps", "industrial valves"), encoding="utf-8"
        )

        report = index.ingest()

        assert report.matched_files == ["/wiki/entities/acme-spa.md"]
        assert report.skipped == 3

    def test_reindexing_updates_chunks_instead_of_duplicating_them(
        self, index, bundle, vector_store
    ):
        index.ingest()
        before = stored_ids(vector_store)
        (bundle / "wiki" / "entities" / "acme-spa.md").write_text(
            ENTITY_MD.replace("industrial pumps", "industrial valves"), encoding="utf-8"
        )

        index.ingest()

        assert stored_ids(vector_store) == before

    def test_a_page_that_shrank_loses_its_orphan_chunks(
        self, index, bundle, vector_store
    ):
        page = bundle / "wiki" / "concepts" / "operating-margin.md"
        index.ingest()
        before = len(stored_ids(vector_store))
        page.write_text("# Operating margin\n\nOne line now.\n", encoding="utf-8")

        report = index.ingest()

        assert report.deleted > 0
        assert len(stored_ids(vector_store)) < before

    def test_a_deleted_page_is_removed_from_the_index(
        self, index, bundle, vector_store
    ):
        index.ingest()
        page = bundle / "wiki" / "entities" / "acme-spa.md"
        remaining = {
            chunk_id
            for chunk_id, document in vector_store.store.items()
            if _as_document(document).metadata["source"] != "/wiki/entities/acme-spa.md"
        }
        page.unlink()

        report = index.ingest()

        assert report.deleted > 0
        assert stored_ids(vector_store) == remaining

    def test_indexing_one_directory_does_not_prune_another(
        self, index, bundle, vector_store
    ):
        index.ingest()
        (bundle / "wiki" / "entities" / "acme-spa.md").unlink()

        report = index.ingest(["/wiki/concepts"])

        assert report.deleted == 0
        assert any(
            _as_document(document).metadata["source"] == "/wiki/entities/acme-spa.md"
            for document in vector_store.store.values()
        )

    def test_a_truncated_run_prunes_nothing(self, embeddings, vector_store, bundle):
        from deepagents.backends import FilesystemBackend

        backend = FilesystemBackend(root_dir=bundle, virtual_mode=True)
        index = SemanticIndex(
            embeddings,
            vector_store,
            backend,
            config=SemanticConfig(trust_timestamps=False, max_files_per_call=2),
        )
        index.ingest()

        report = index.ingest(only_modified=False)

        assert report.truncated
        assert report.deleted == 0

    def test_the_timestamp_fast_path_skips_unchanged_files(
        self, embeddings, vector_store, bundle
    ):
        from deepagents.backends import FilesystemBackend

        backend = FilesystemBackend(root_dir=bundle, virtual_mode=True)
        index = SemanticIndex(
            embeddings, vector_store, backend, config=SemanticConfig()
        )
        first = index.ingest()

        second = index.ingest()

        assert second.skipped == first.files


class TestTheManifest:
    def test_it_is_written_where_the_linter_does_not_look(self, index, bundle):
        index.ingest()

        manifest = bundle / ".okf" / "semantic-manifest.json"

        assert manifest.is_file()
        assert manifest.suffix != ".md"

    def test_it_records_the_chunk_ids_of_every_file(self, index, bundle):
        report = index.ingest()

        payload = json.loads(
            (bundle / ".okf" / "semantic-manifest.json").read_text(encoding="utf-8")
        )

        assert set(payload["files"]) == set(report.matched_files)
        assert all(entry["ids"] for entry in payload["files"].values())

    def test_a_corrupt_manifest_costs_a_rebuild_not_an_error(self, index, bundle):
        index.ingest()
        (bundle / ".okf" / "semantic-manifest.json").write_text("{ not json", "utf-8")

        report = index.ingest()

        assert report.files == 4
        assert report.errors == []


class TestBackendsOtherThanADirectory:
    def test_it_indexes_a_bundle_held_in_a_store(
        self, embeddings, vector_store, store_backend, config
    ):
        store_backend.write("/wiki/concepts/margin.md", CONCEPT_MD)
        index = SemanticIndex(embeddings, vector_store, store_backend, config=config)

        report = index.ingest()

        assert "/wiki/concepts/margin.md" in report.matched_files
        assert "/raw/source.txt" in report.matched_files

    def test_a_pdf_is_converted_before_it_is_chunked(
        self, embeddings, vector_store, store_backend, config
    ):
        pytest.importorskip("markitdown")
        store_backend.upload_files([("/raw/paper.pdf", minimal_pdf(PDF_TEXT))])
        index = SemanticIndex(embeddings, vector_store, store_backend, config=config)

        index.ingest()

        texts = [
            _as_document(document).page_content
            for document in vector_store.store.values()
            if _as_document(document).metadata["source"] == "/raw/paper.pdf"
        ]
        assert any(PDF_TEXT in text for text in texts)

    def test_one_unreadable_source_does_not_abort_the_run(
        self, embeddings, vector_store, store_backend, config, monkeypatch
    ):
        import deep_wiki_agent.semantic.index as index_module

        store_backend.write("/wiki/concepts/margin.md", CONCEPT_MD)
        store_backend.upload_files([("/raw/broken.pdf", b"%PDF-1.4 truncated")])

        def refuse(_backend, path):
            if path.endswith(".pdf"):
                msg = f"could not read {path}: corrupt"
                raise RuntimeError(msg)
            raise AssertionError(path)

        monkeypatch.setattr(index_module, "_download", refuse)
        index = SemanticIndex(embeddings, vector_store, store_backend, config=config)

        report = index.ingest()

        assert "/wiki/concepts/margin.md" in report.matched_files
        assert any("broken.pdf" in error for error in report.errors)


class TestSearch:
    def test_it_returns_the_page_a_passage_came_from(self, index):
        index.ingest()

        results = index.search("operating margin", area="any")

        assert results
        assert all(result["file"].startswith("/") for result in results)
        assert all("metadata" in result for result in results)

    def test_the_area_filter_separates_pages_from_sources(self, index):
        index.ingest()

        assert all(
            result["area"] == "raw" for result in index.search("margins", area="raw")
        )
        assert all(
            result["area"] == "wiki" for result in index.search("margins", area="wiki")
        )

    def test_the_content_type_filter_isolates_tables(self, index):
        index.ingest()

        results = index.search("margin by year", area="any", content_type="table")

        assert results
        assert all(result["content_type"] == "table" for result in results)

    def test_the_path_filter_narrows_to_a_directory(self, index):
        index.ingest()

        results = index.search("margin", area="any", path_contains="/entities/")

        assert all("/entities/" in result["file"] for result in results)

    def test_k_caps_the_number_of_hits(self, index):
        index.ingest()

        assert len(index.search("margin", k=2, area="any")) <= 2

    def test_an_empty_index_answers_nothing(self, index):
        assert index.search("margin", area="any") == []

    def test_a_hybrid_store_is_queried_with_text_not_a_vector(
        self, embeddings, bundle, config
    ):
        """BM25 only sees the query if the store is handed the query itself."""
        from deepagents.backends import FilesystemBackend

        seen: dict[str, object] = {}

        class HybridStore:
            retrieval_mode = "hybrid"

            def add_documents(self, documents, ids=None):  # noqa: ARG002
                return ids or []

            def similarity_search_with_score(self, query, **kwargs):  # noqa: ARG002
                seen["query"] = query
                return []

            def similarity_search_with_score_by_vector(self, _vector, **_kwargs):
                msg = "a hybrid store must not be searched by vector"
                raise AssertionError(msg)

        backend = FilesystemBackend(root_dir=bundle, virtual_mode=True)
        index = SemanticIndex(embeddings, HybridStore(), backend, config=config)

        index.search("operating margin", area="any")

        assert seen["query"] == "operating margin"


def _as_document(entry):
    """Return the `Document` an `InMemoryVectorStore` entry holds.

    The store keeps its own record shape; this is the one place that knows it,
    so the assertions above read as assertions rather than as plumbing.
    """
    from langchain_core.documents import Document

    if isinstance(entry, Document):
        return entry
    return Document(page_content=entry["text"], metadata=entry["metadata"])
