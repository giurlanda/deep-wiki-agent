"""The guard that keeps `skills/okf-wiki/SKILL.md` a source of truth.

`SKILL.md` at the repository root is the canonical human-facing statement of
how an OKF wiki is maintained, and it is still usable in Claude Code and other
skill-aware harnesses. The agents in this library, however, no longer read it:
their instructions were written by hand into `prompts.py`, tracking `SKILL.md`
section by section.

Nothing in the code connects the two, so without this module "source of truth"
would be a comment rather than a guarantee. Two checks make it real:

- every section of `SKILL.md` is declared here as covered by the manager
  prompt, the reader prompt, both, or neither — and a section that appears in
  `SKILL.md` without being declared fails, forcing a conscious decision about
  where it belongs;
- a checksum of `SKILL.md`, which must be updated deliberately, so editing the
  skill without revisiting the prompts cannot pass silently.

When `SKILL.md` changes: revisit `prompts.py`, then update `SKILL_MD_SHA256`
below with the value the failure message prints.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from deep_wiki_agent.factory import WIKI_ROOT
from deep_wiki_agent.prompts import (
    LINT_TOOL_BLOCK,
    MANAGER_SYSTEM_PROMPT_TEMPLATE,
    READER_SYSTEM_PROMPT_TEMPLATE,
)

SKILL_MD = Path(__file__).parents[1] / "skills" / "okf-wiki" / "SKILL.md"

SKILL_MD_SHA256 = "0e7299c4dc4c15d44188ae9c44906d45b9800d66967593791b5b34be12a1053b"

MANAGER_PROMPT = MANAGER_SYSTEM_PROMPT_TEMPLATE.format(
    wiki_root=WIKI_ROOT, raw_dir="/raw", lint_block=LINT_TOOL_BLOCK
)
READER_PROMPT = READER_SYSTEM_PROMPT_TEMPLATE.format(
    wiki_root=WIKI_ROOT, raw_dir="/raw", not_found_message="Not found."
)

MANAGER = "manager"
READER = "reader"

# One row per heading in SKILL.md: which prompts must carry that section, and a
# phrase that proves each of them does. Phrases are deliberately distinctive —
# a paraphrase that loses them has lost the instruction too.
COVERAGE: dict[str, dict[str, str]] = {
    "1. Bundle structure": {
        MANAGER: "file path is the identity of the concept",
        READER: "file path is the identity of the concept",
    },
    "2. OKF conformance": {
        MANAGER: "The only field the spec mandates is `type`",
        READER: "the only field OKF mandates",
    },
    "3. Operations": {
        MANAGER: "## 3. Operations",
    },
    "Ingest - the main use case": {
        MANAGER: "Read the source in full",
    },
    "Query": {
        MANAGER: "propose archiving it",
        READER: "The query protocol",
    },
    "Lint": {
        MANAGER: "Orphan pages (no inbound links)",
    },
    "4. Bootstrap": {
        MANAGER: "Supervised or batch ingest",
    },
    "5. Log format": {
        MANAGER: "Entry types: `ingest`, `query`, `lint`, `refactor`",
    },
    # Background for a human maintaining the format, not operating
    # instructions: deliberately in neither prompt.
    "Resources": {},
}

PROMPTS = {MANAGER: MANAGER_PROMPT, READER: READER_PROMPT}

# Sections a read-only agent must not be given: it can never ingest, bootstrap
# or write a log entry, and a prompt that tells it otherwise wastes tokens and
# invites it to try.
READER_MUST_NOT_CARRY = (
    "Read the source in full",
    "Supervised or batch ingest",
    "Entry types: `ingest`, `query`, `lint`, `refactor`",
    "okf_lint",
)


def skill_headings() -> list[str]:
    """Return the `##`/`###` headings of SKILL.md, in document order.

    Fenced code blocks are stripped first: the log-format section shows a
    sample entry that starts with `## `, and that is an example, not a section.
    Em dashes are normalized to hyphens so a typographic edit alone does not
    fail the coverage check — the checksum catches that instead.
    """
    text = SKILL_MD.read_text(encoding="utf-8")
    prose = re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)
    found = re.findall(r"^#{2,3} (.+)$", prose, flags=re.MULTILINE)
    return [heading.strip().replace("—", "-") for heading in found]


class TestSkillMdIsIntact:
    def test_the_skill_still_ships_in_the_repository(self):
        assert SKILL_MD.is_file()

    def test_checksum_is_current(self):
        digest = hashlib.sha256(SKILL_MD.read_bytes()).hexdigest()

        assert digest == SKILL_MD_SHA256, (
            "SKILL.md changed. Revisit the prompts in prompts.py, then set "
            f"SKILL_MD_SHA256 = {digest!r} in this file."
        )


class TestEverySectionIsAccountedFor:
    def test_no_undeclared_section(self):
        """A new section must be consciously routed to a prompt, or to neither."""
        undeclared = [h for h in skill_headings() if h not in COVERAGE]

        assert not undeclared, (
            f"SKILL.md sections not declared in COVERAGE: {undeclared}. "
            "Decide which prompt carries each, then add a row."
        )

    def test_no_stale_declaration(self):
        headings = set(skill_headings())
        stale = [section for section in COVERAGE if section not in headings]

        assert not stale, f"COVERAGE rows with no matching SKILL.md section: {stale}"

    @pytest.mark.parametrize(
        ("section", "audience", "phrase"),
        [
            (section, audience, phrase)
            for section, targets in COVERAGE.items()
            for audience, phrase in targets.items()
        ],
    )
    def test_declared_section_is_present_in_its_prompt(self, section, audience, phrase):
        assert phrase in PROMPTS[audience], (
            f"the {audience} prompt no longer carries SKILL.md section {section!r} "
            f"(missing marker {phrase!r})"
        )


class TestReaderPromptStaysMinimal:
    @pytest.mark.parametrize("phrase", READER_MUST_NOT_CARRY)
    def test_write_side_sections_are_absent(self, phrase):
        assert phrase not in READER_PROMPT
