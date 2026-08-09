"""Tests for the chunker: what a page is cut into, and what each piece knows.

Chunking is where a retrieved passage either can or cannot be cited: if the
page, the section and the kind of content are not attached here, no amount of
retrieval quality puts them back later.
"""

from __future__ import annotations

import pytest

from deep_wiki_agent.semantic.chunking import (
    ChunkingConfig,
    chunk_markdown,
    extract_markdown_tables,
    split_table_markdown,
)

PAGE = """\
# Operating margin

Operating income divided by net revenue.

## Thresholds

Values considered healthy for the sector.

| Year | Margin |
|---|---|
| 2024 | 12% |
| 2025 | 14% |

### Notes

Figures are consolidated.
"""

FENCED_TABLE = """\
# Formats

Markdown tables look like this:

```markdown
| Year | Margin |
|---|---|
| 2024 | 12% |
```

That is all.
"""


def chunks_of(text, **kwargs):
    """Chunk a page with the defaults every test here shares."""
    kwargs.setdefault("path", "/wiki/concepts/operating-margin.md")
    kwargs.setdefault("file_hash", "deadbeef")
    return chunk_markdown(text, **kwargs)


class TestSectionSplitting:
    def test_a_chunk_never_straddles_two_sections(self):
        sections = {chunk.metadata["header_path"] for chunk in chunks_of(PAGE)}

        assert sections == {
            "Operating margin",
            "Operating margin > Thresholds",
            "Operating margin > Thresholds > Notes",
        }

    def test_hierarchy_is_recorded_not_flattened(self):
        deepest = next(
            chunk for chunk in chunks_of(PAGE) if chunk.metadata["section"] == "Notes"
        )

        assert deepest.metadata["parent_sections"] == [
            "Operating margin",
            "Thresholds",
        ]
        assert deepest.metadata["depth"] == 3

    def test_header_path_is_prepended_to_the_text(self):
        deepest = next(
            chunk for chunk in chunks_of(PAGE) if chunk.metadata["section"] == "Notes"
        )

        assert deepest.page_content.startswith("Operating margin > Thresholds > Notes")

    def test_prepending_can_be_turned_off(self):
        config = ChunkingConfig(prepend_header_path=False)

        deepest = next(
            chunk
            for chunk in chunks_of(PAGE, config=config)
            if chunk.metadata["section"] == "Notes"
        )

        assert deepest.page_content == "Figures are consolidated."


class TestTables:
    def test_a_table_becomes_one_atomic_chunk(self):
        tables = [
            chunk
            for chunk in chunks_of(PAGE)
            if chunk.metadata["content_type"] == "table"
        ]

        assert len(tables) == 1
        assert "| 2024 | 12% |" in tables[0].page_content
        assert "| 2025 | 14% |" in tables[0].page_content

    def test_the_table_chunk_carries_its_columns(self):
        table = next(
            chunk
            for chunk in chunks_of(PAGE)
            if chunk.metadata["content_type"] == "table"
        )

        assert table.metadata["table_columns"] == ["Year", "Margin"]
        assert table.metadata["table_rows"] == 2

    def test_the_prose_keeps_a_pointer_to_the_table(self):
        prose = next(
            chunk
            for chunk in chunks_of(PAGE)
            if chunk.metadata["section"] == "Thresholds"
            and chunk.metadata["content_type"] == "text"
        )

        assert "indexed separately" in prose.page_content

    def test_a_table_inside_a_fence_is_illustration_not_data(self):
        _, tables = extract_markdown_tables(FENCED_TABLE)

        assert tables == []

    def test_extraction_can_be_turned_off(self):
        config = ChunkingConfig(extract_tables=False)

        chunks = chunks_of(PAGE, config=config)

        kinds = {chunk.metadata["content_type"] for chunk in chunks}

        assert kinds == {"text"}

    def test_the_caption_above_a_table_is_kept(self):
        text = "Sector benchmarks\n\n| Year | Margin |\n|---|---|\n| 2024 | 12% |\n"

        _, tables = extract_markdown_tables(text)

        assert tables[0].caption == "Sector benchmarks"

    def test_an_oversized_table_repeats_its_header_in_every_part(self):
        _, tables = extract_markdown_tables(
            "| Year | Margin |\n|---|---|\n"
            + "".join(f"| {year} | {year % 100}% |\n" for year in range(2000, 2040))
        )

        parts = split_table_markdown(tables[0], max_chars=200)

        assert len(parts) > 1
        assert all(part.startswith("| Year | Margin |\n|---|---|") for part in parts)

    def test_a_table_that_fits_is_not_split(self):
        _, tables = extract_markdown_tables(
            "| Year | Margin |\n|---|---|\n| 2024 | 12% |\n"
        )

        assert len(split_table_markdown(tables[0], max_chars=2000)) == 1


class TestProvenanceMetadata:
    def test_every_chunk_names_the_page_it_came_from(self):
        for chunk in chunks_of(PAGE):
            assert chunk.metadata["source"] == "/wiki/concepts/operating-margin.md"
            relative = chunk.metadata["relative_path"]
            assert relative == "wiki/concepts/operating-margin.md"
            assert chunk.metadata["file_name"] == "operating-margin.md"

    def test_the_area_distinguishes_a_page_from_a_source(self):
        page = chunks_of(PAGE)[0]
        source = chunks_of(PAGE, path="/raw/report.pdf", area="raw")[0]

        assert page.metadata["area"] == "wiki"
        assert source.metadata["area"] == "raw"

    def test_chunks_are_numbered_within_the_page(self):
        chunks = chunks_of(PAGE)

        assert [chunk.metadata["chunk_index"] for chunk in chunks] == list(
            range(len(chunks))
        )
        assert {chunk.metadata["chunk_total"] for chunk in chunks} == {len(chunks)}

    def test_extra_metadata_reaches_every_chunk(self):
        chunks = chunks_of(PAGE, extra_metadata={"corpus": "internal", "tags": ["q1"]})

        assert all(chunk.metadata["corpus"] == "internal" for chunk in chunks)
        assert all(chunk.metadata["tags"] == ["q1"] for chunk in chunks)

    @pytest.mark.parametrize("text", ["", "   \n\n  "])
    def test_an_empty_page_produces_no_chunks(self, text):
        assert chunks_of(text) == []
