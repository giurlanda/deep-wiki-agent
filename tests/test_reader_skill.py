"""The guard that keeps `skills/okf-wiki-reader/SKILL.md` in step with the reader.

The reader skill is the consultation half of the reader agent, packaged for a
skill-aware harness: a host that already runs an agent loads it to query a
bundle in-loop, instead of delegating to the agent `create_deep_wiki_agent`
builds. Its content is `READER_SYSTEM_PROMPT_TEMPLATE` rendered for the fixed
layout — pages under `/wiki/`, sources under `/raw/` — minus the semantic
search and structured-output blocks, which depend on tools and a schema a
harness does not have.

Nothing in the code derives one from the other, so this module ties them:

- a checksum of the reader template, which must be updated deliberately, so
  editing the reader's instructions without revisiting the skill fails;
- the distinctive phrases of each reader section, which the skill must carry;
- the phrases the skill must *not* carry — write-side instructions, and the
  optional blocks it deliberately leaves out.

The paths the skill cites are checked with the prompts' in
`tests/test_prompt_paths`.

When the reader template changes: revisit the skill, then update
`READER_TEMPLATE_SHA256` below with the value the failure message prints.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
import yaml

from deep_wiki_agent.prompts import (
    DEFAULT_NOT_FOUND_MESSAGE,
    READER_SYSTEM_PROMPT_TEMPLATE,
)

REPO_ROOT = Path(__file__).parents[1]

READER_SKILL_MD = REPO_ROOT / "skills" / "okf-wiki-reader" / "SKILL.md"

READER_TEMPLATE_SHA256 = (
    "4e3faaa809e05293306527ac821a5518efa95cdd655ef38afeddc7956a29498f"
)

# One marker per section of the reader prompt. Phrases are deliberately
# distinctive — a paraphrase that loses them has lost the instruction too.
MUST_CARRY = (
    "The one rule that overrides everything else",
    "you must not infer, extrapolate, or fill gaps",
    "A partial hit is not a miss",
    "file path is the identity of the concept",
    "the only field OKF mandates",
    "relative to the page that contains them",
    "The query protocol",
    "Search synonyms and the other languages the bundle",
    'Only conclude "not found" after steps 1-4',
    "Report contradictions rather than resolving them",
    "Flag staleness",
    "Answer in the language the user writes in",
)

MUST_NOT_CARRY = (
    # Write-side sections, as for the reader prompt in `test_prompt_drift`.
    "Read the source in full",
    "Supervised or batch ingest",
    "Entry types: `ingest`, `query`, `lint`, `refactor`",
    "okf_lint",
    # The optional reader blocks the skill leaves out.
    "semantic_search",
    "WikiAnswer",
    "structured_response",
    # Unrendered template placeholders.
    "{wiki_root}",
    "{raw_dir}",
    "{not_found_message}",
)

# Lowercase letters, digits and single hyphens, as skill-aware harnesses expect.
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_DESCRIPTION_CHARS = 1024


def skill_text() -> str:
    return READER_SKILL_MD.read_text(encoding="utf-8")


def frontmatter() -> dict:
    """Parse the YAML frontmatter, which must open the file.

    A harness that cannot parse it may drop the skill without raising, which
    is exactly the silent failure `docs/architecture.md` describes.
    """
    match = re.match(r"^---\n(.*?)\n---\n", skill_text(), flags=re.DOTALL)

    assert match, "SKILL.md does not open with a `---` frontmatter block"
    return yaml.safe_load(match[1])


class TestTheSkillIsLoadable:
    def test_the_skill_ships_in_the_repository(self):
        assert READER_SKILL_MD.is_file()

    def test_name_matches_its_directory(self):
        name = frontmatter()["name"]

        assert SKILL_NAME_RE.match(name)
        assert name == READER_SKILL_MD.parent.name

    def test_description_is_present_and_bounded(self):
        description = frontmatter()["description"]

        assert isinstance(description, str)
        assert 0 < len(description) <= MAX_DESCRIPTION_CHARS


class TestTheSkillTracksTheReader:
    def test_reader_template_checksum_is_current(self):
        digest = hashlib.sha256(READER_SYSTEM_PROMPT_TEMPLATE.encode()).hexdigest()

        assert digest == READER_TEMPLATE_SHA256, (
            "READER_SYSTEM_PROMPT_TEMPLATE changed. Revisit "
            "skills/okf-wiki-reader/SKILL.md, then set "
            f"READER_TEMPLATE_SHA256 = {digest!r} in this file."
        )

    @pytest.mark.parametrize("phrase", MUST_CARRY)
    def test_reader_section_is_carried(self, phrase):
        assert phrase in READER_SYSTEM_PROMPT_TEMPLATE, (
            f"marker {phrase!r} is no longer in the reader prompt; pick a new one"
        )
        assert phrase in skill_text(), f"the reader skill no longer carries {phrase!r}"

    def test_not_found_sentence_is_the_default_one(self):
        """The closed-book contract hinges on one exact, recognizable sentence."""
        assert f"    {DEFAULT_NOT_FOUND_MESSAGE}\n" in skill_text()

    @pytest.mark.parametrize("phrase", MUST_NOT_CARRY)
    def test_left_out_content_is_absent(self, phrase):
        assert phrase not in skill_text()
