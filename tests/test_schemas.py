"""Tests for `WikiAnswer`, the reader's optional structured response.

The point of the schema is that a caller can branch on it without parsing
prose, so what matters here is the shape it guarantees: which fields a model
must supply, which default to something usable when it omits them, and that
every field carries a description — the descriptions travel to the model as
part of the schema, so an undescribed field is an unfilled one.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_wiki_agent import WikiAnswer


class TestTheContractAsFields:
    def test_a_full_answer_round_trips(self):
        answer = WikiAnswer(
            answer="Il margine operativo è il rapporto tra risultato e ricavi.",
            citations=["/wiki/concepts/margine-operativo.md"],
            found=True,
        )

        assert answer.found is True
        assert answer.citations == ["/wiki/concepts/margine-operativo.md"]
        assert answer.not_covered is None

    def test_a_miss_is_checkable_without_matching_the_message(self):
        """This is the whole reason the schema exists."""
        answer = WikiAnswer(answer="Not found.", found=False)

        assert answer.found is False
        assert answer.citations == []

    def test_a_partial_hit_names_what_is_missing(self):
        answer = WikiAnswer(
            answer="The wiki gives the 2026 threshold.",
            citations=["/wiki/documents/contratto-acme-2026.md"],
            not_covered="The 2025 threshold is not in the knowledge base.",
            found=True,
        )

        assert answer.found is True
        assert answer.not_covered

    @pytest.mark.parametrize("field", ["answer", "found"])
    def test_the_load_bearing_fields_are_required(self, field):
        payload = {"answer": "Something.", "found": True}
        del payload[field]

        with pytest.raises(ValidationError):
            WikiAnswer(**payload)

    @pytest.mark.parametrize("field", ["citations", "not_covered"])
    def test_the_optional_fields_default_to_something_usable(self, field):
        """A model that omits them must not make the caller handle absence."""
        answer = WikiAnswer(answer="Something.", found=True)

        assert getattr(answer, field) in ([], None)

    def test_citations_default_is_not_shared_between_instances(self):
        first = WikiAnswer(answer="A.", found=True)
        first.citations.append("/wiki/concepts/some-page.md")

        assert WikiAnswer(answer="B.", found=True).citations == []


class TestTheSchemaTheModelSees:
    @pytest.mark.parametrize("field", ["answer", "citations", "not_covered", "found"])
    def test_every_field_is_described(self, field):
        described = WikiAnswer.model_json_schema()["properties"][field]

        assert described.get("description")

    def test_the_field_names_are_the_documented_ones(self):
        assert set(WikiAnswer.model_fields) == {
            "answer",
            "citations",
            "not_covered",
            "found",
        }
