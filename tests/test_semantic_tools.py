"""Tests for the two tools the semantic factory builds.

What matters at this boundary is what the model can and cannot do: it chooses
what to index and what to look for, never which bundle or which store. And
what it gets back has to be readable as text — the structured hits travel in
the artifact, but the model only ever sees the rendered block.
"""

from __future__ import annotations

import pytest

from deep_wiki_agent.semantic import (
    SEMANTIC_INGEST_TOOL_NAME,
    SEMANTIC_SEARCH_TOOL_NAME,
    SemanticConfig,
    create_semantic_tools,
    ingest_semantic_index,
)

CONCEPT_MD = """\
---
type: Concept
title: Operating margin
---

# Operating margin

Operating income divided by net revenue.
"""


@pytest.fixture
def bundle(tmp_path):
    """A minimal bundle with one page and one source."""
    root = tmp_path / "bundle"
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "raw").mkdir()
    (root / "wiki" / "concepts" / "operating-margin.md").write_text(
        CONCEPT_MD, encoding="utf-8"
    )
    (root / "raw" / "note.txt").write_text("A raw note.\n", encoding="utf-8")
    return root


@pytest.fixture
def semantic(embeddings, vector_store, bundle):
    """Tools bound to the bundle, with the timestamp fast path off."""
    return create_semantic_tools(
        embeddings,
        vector_store,
        wiki_path=bundle,
        config=SemanticConfig(trust_timestamps=False),
    )


def call(tool, **kwargs):
    """Invoke a tool through its schema and return `(content, artifact)`."""
    message = tool.invoke(
        {"name": tool.name, "args": kwargs, "id": "call-1", "type": "tool_call"}
    )
    return message.content, message.artifact


class TestTheToolsAgentsSee:
    def test_both_tools_are_named_for_what_they_do(self, semantic):
        assert semantic.ingest_tool.name == SEMANTIC_INGEST_TOOL_NAME
        assert semantic.search_tool.name == SEMANTIC_SEARCH_TOOL_NAME
        assert semantic.as_list() == [semantic.ingest_tool, semantic.search_tool]

    def test_the_model_cannot_choose_the_bundle_or_the_store(self, semantic):
        arguments = set(semantic.ingest_tool.args) | set(semantic.search_tool.args)

        assert not arguments & {"wiki_path", "backend", "vector_store", "embeddings"}

    def test_the_search_schema_offers_the_filters_the_prompt_mentions(self, semantic):
        assert set(semantic.search_tool.args) == {
            "query",
            "k",
            "area",
            "content_type",
            "path_contains",
            "section_contains",
        }

    def test_the_ingest_schema_takes_patterns_not_a_root(self, semantic):
        assert set(semantic.ingest_tool.args) == {
            "patterns",
            "tags",
            "only_modified",
        }


class TestIngestThroughTheTool:
    def test_it_reports_what_it_indexed(self, semantic):
        content, artifact = call(semantic.ingest_tool)

        assert "2 file(s)" in content
        assert artifact["files"] == 2
        assert artifact["errors"] == []

    def test_a_second_call_says_everything_was_up_to_date(self, semantic):
        call(semantic.ingest_tool)

        content, artifact = call(semantic.ingest_tool)

        assert "already up to date" in content
        assert artifact["skipped"] == 2

    def test_patterns_outside_the_bundle_index_nothing(self, semantic, tmp_path):
        content, artifact = call(semantic.ingest_tool, patterns=[str(tmp_path)])

        assert artifact["files"] == 0
        assert "Nothing was indexed" in content

    def test_tags_reach_the_chunks(self, semantic, vector_store):
        call(semantic.ingest_tool, tags=["q1"])

        assert all(
            "q1" in _metadata(entry)["tags"] for entry in vector_store.store.values()
        )


class TestSearchThroughTheTool:
    def test_hits_name_their_page_and_section(self, semantic):
        semantic.ingest()

        content, artifact = call(semantic.search_tool, query="operating margin")

        assert "/wiki/concepts/operating-margin.md" in content
        assert artifact[0]["file"] == "/wiki/concepts/operating-margin.md"

    def test_it_searches_the_wiki_and_not_the_sources_by_default(self, semantic):
        semantic.ingest()

        _, artifact = call(semantic.search_tool, query="note")

        assert all(hit["area"] == "wiki" for hit in artifact)

    def test_an_empty_result_says_what_to_try_next(self, semantic):
        content, artifact = call(semantic.search_tool, query="anything")

        assert artifact == []
        assert "No passage matched" in content

    def test_long_chunks_are_trimmed_for_the_model_but_not_in_the_artifact(
        self, embeddings, vector_store, bundle
    ):
        (bundle / "wiki" / "concepts" / "long.md").write_text(
            "# Long\n\n" + "margin " * 400, encoding="utf-8"
        )
        semantic = create_semantic_tools(
            embeddings,
            vector_store,
            wiki_path=bundle,
            config=SemanticConfig(trust_timestamps=False, snippet_chars=80),
        )
        semantic.ingest()

        content, artifact = call(
            semantic.search_tool, query="margin", path_contains="long.md"
        )

        assert "[…]" in content
        assert all(len(hit["text"]) > 80 for hit in artifact)

    def test_k_is_capped_by_the_schema(self, semantic):
        with pytest.raises(Exception, match="less than or equal to 25"):
            call(semantic.search_tool, query="margin", k=100)


class TestFactoryValidation:
    def test_it_needs_exactly_one_of_wiki_path_and_backend(
        self, embeddings, vector_store, bundle
    ):
        with pytest.raises(ValueError, match="exactly one of"):
            create_semantic_tools(embeddings, vector_store)
        with pytest.raises(ValueError, match="exactly one of"):
            create_semantic_tools(
                embeddings, vector_store, wiki_path=bundle, backend=object()
            )

    def test_a_missing_bundle_fails_where_the_agent_is_assembled(
        self, embeddings, vector_store, tmp_path
    ):
        with pytest.raises(NotADirectoryError):
            create_semantic_tools(
                embeddings, vector_store, wiki_path=tmp_path / "absent"
            )

    @pytest.mark.parametrize("search_k", [0, -1, 26])
    def test_search_k_must_be_within_the_schemas_range(
        self, embeddings, vector_store, bundle, search_k
    ):
        with pytest.raises(ValueError, match="search_k must be positive"):
            create_semantic_tools(
                embeddings, vector_store, wiki_path=bundle, search_k=search_k
            )

    def test_search_k_becomes_the_default_the_model_sees(
        self, embeddings, vector_store, bundle
    ):
        """Not only the function's default: the schema is what fills `k` in."""
        semantic = create_semantic_tools(
            embeddings, vector_store, wiki_path=bundle, search_k=3
        )

        assert semantic.search_tool.args["k"]["default"] == 3

    def test_it_accepts_a_prebuilt_backend(
        self, embeddings, vector_store, store_backend
    ):
        store_backend.write("/wiki/concepts/margin.md", CONCEPT_MD)

        semantic = create_semantic_tools(
            embeddings,
            vector_store,
            backend=store_backend,
            config=SemanticConfig(trust_timestamps=False),
        )

        assert "/wiki/concepts/margin.md" in semantic.ingest().matched_files


class TestIngestionWithoutAnAgent:
    def test_the_module_function_is_the_same_code_path(
        self, embeddings, vector_store, bundle
    ):
        report = ingest_semantic_index(
            embeddings,
            vector_store,
            wiki_path=bundle,
            config=SemanticConfig(trust_timestamps=False),
        )

        assert report.files == 2
        assert vector_store.store

    def test_it_takes_patterns_and_tags_like_the_tool(
        self, embeddings, vector_store, bundle
    ):
        report = ingest_semantic_index(
            embeddings,
            vector_store,
            wiki_path=bundle,
            patterns=["/wiki"],
            tags=["batch"],
            config=SemanticConfig(trust_timestamps=False),
        )

        assert report.matched_files == ["/wiki/concepts/operating-margin.md"]
        assert all(
            "batch" in _metadata(entry)["tags"] for entry in vector_store.store.values()
        )


def _metadata(entry):
    """Return the metadata of an `InMemoryVectorStore` record."""
    return entry.metadata if hasattr(entry, "metadata") else entry["metadata"]
