"""Structured response schema for the reader agent.

The reader's contract — an answer, the pages it rests on, and an explicit
statement of what the bundle does not cover — is expressed in prose in
``READER_SYSTEM_PROMPT_TEMPLATE``. That reads well to a human and badly to a
caller: deciding whether the bundle covered the question means matching the
not-found sentence as a string, which breaks the moment
``not_found_message`` is translated or reworded.

:class:`WikiAnswer` is the same contract as fields. It is opt-in
(``create_deep_wiki_agent(..., structured_output=True)``) because the trade-off
is real: a schema costs the model the freedom to lay an answer out as the
question deserves — tables, staged reasoning, an aside about two pages that
disagree — and returns a record that a caller can branch on.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["WikiAnswer"]


class WikiAnswer(BaseModel):
    """A reader agent's answer, as data rather than prose.

    The field descriptions are part of the schema handed to the model, so they
    are written as instructions to it rather than as notes to the reader of
    this file.
    """

    answer: str = Field(
        description=(
            "The answer to the question, drawn exclusively from the wiki "
            "bundle. When `found` is false, this holds the not-found message "
            "and nothing else."
        )
    )
    citations: list[str] = Field(
        default_factory=list,
        description=(
            "Paths, from the bundle root, of the wiki pages the answer rests "
            "on - e.g. '/wiki/concepts/some-page.md'. One entry per page, no "
            "duplicates. Empty when `found` is false."
        ),
    )
    not_covered: str | None = Field(
        default=None,
        description=(
            "The part of the question the bundle does not answer, stated in "
            "the language of the question. Null when the bundle answers all "
            "of it."
        ),
    )
    found: bool = Field(
        description=(
            "Whether the bundle covered the question, in whole or in part. "
            "False only when the search came up empty; a partial answer is "
            "true with `not_covered` set."
        )
    )
