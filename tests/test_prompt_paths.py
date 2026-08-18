"""The guard that keeps the prompts' paths pointing at real bundle locations.

Section 1 of each prompt draws the bundle layout; the rest of the prompt then
writes paths against it — in the query protocol, in the frontmatter example, in
the log entry. Nothing ties the two together, so when the flat layout became
`wiki/` + `raw/` the diagram moved and several instructions did not, leaving the
agents told to read `/index.md`, a file that no longer exists.

`BUNDLE_SKELETON` is the layout as a data structure. Here it is materialized in
a temporary directory and every path the prompts cite is resolved against it:

- paths written from the bundle root must exist there;
- paths inside a fenced example must resolve from the page that example belongs
  to, which is what catches a source cited as `raw/...` from `wiki/log.md`;
- the diagram in section 1 must itself agree with the skeleton, so the two
  cannot drift apart in the other direction either.

Slugs are not real: `wiki/concepts/some-page.md` stands for a page the bundle
may or may not hold. A path whose leaf does not exist therefore passes as long
as its *directory* does, and is a directory the skeleton leaves open — the
mistake this module exists to catch is a wrong directory, never a wrong page
name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deep_wiki_agent.backends import RAW_DIR
from deep_wiki_agent.factory import WIKI_ROOT
from deep_wiki_agent.prompts import (
    BUNDLE_SKELETON,
    LINT_TOOL_BLOCK,
    MANAGER_SYSTEM_PROMPT_TEMPLATE,
    READER_SYSTEM_PROMPT_TEMPLATE,
    SEMANTIC_MANAGER_BLOCK,
    SEMANTIC_READER_BLOCK,
    STRUCTURED_OUTPUT_BLOCK_TEMPLATE,
)

MANAGER = "manager"
READER = "reader"

# The reader is rendered with its structured-output block included: that block
# cites a bundle path of its own, and this module is what proves it resolves.
PROMPTS = {
    MANAGER: MANAGER_SYSTEM_PROMPT_TEMPLATE.format(
        wiki_root=WIKI_ROOT,
        raw_dir=RAW_DIR,
        lint_block=LINT_TOOL_BLOCK,
        semantic_block=SEMANTIC_MANAGER_BLOCK,
    ),
    READER: READER_SYSTEM_PROMPT_TEMPLATE.format(
        wiki_root=WIKI_ROOT,
        raw_dir=RAW_DIR,
        not_found_message="Not found.",
        semantic_block=SEMANTIC_READER_BLOCK,
        structured_output_block=STRUCTURED_OUTPUT_BLOCK_TEMPLATE.format(
            wiki_root=WIKI_ROOT, raw_dir=RAW_DIR
        ),
    ),
}

# A path written from the bundle root, e.g. `/wiki/index.md`. The `.` in the
# lookbehind keeps the tail of a relative example (`../raw/...`) out: those are
# not root-relative, and are checked against their own page instead.
BUNDLE_PATH_RE = re.compile(r"(?<![\w:./])/(?:[\w.-]+/)*[\w.-]+")

# A path with at least one separator, as written inside a fenced example.
RELATIVE_PATH_RE = re.compile(r"(?:\.\./)*[\w.-]+(?:/[\w.-]+)+")

# Fenced blocks whose paths are relative to one specific page, and to which.
ANCHORED_EXAMPLES = {
    "yaml": "wiki/entities/acme-spa.md",  # the frontmatter example, section 2
    "markdown": "wiki/log.md",  # the log entry example, section 5
}

CLOSED_DIRS = frozenset({".", "wiki"})
"""Directories whose contents the skeleton enumerates in full.

The bundle root holds `AGENTS.md`, `raw/` and `wiki/`; `wiki/` holds its
indexes, its log and its categories. Everywhere else — the categories
themselves, `raw/`, `assets/` — fills up with pages and sources, so a path
landing there is an example slug rather than a mistake.
"""


def structure_block(prompt: str) -> str:
    """Return the layout diagram of the prompt's section 1.

    It is the first fenced block of both prompts; the frontmatter and log
    examples come later.
    """
    return prompt.split("```")[1]


def declared_paths(prompt: str) -> set[str]:
    """Return the bundle paths the layout diagram declares, without the mount.

    Each diagram line is `<path><padding><comment>`, so the first token is the
    path — kept verbatim, trailing slash included, since that is what tells a
    directory from a file.
    """
    return {
        line.split()[0].removeprefix(WIKI_ROOT)
        for line in structure_block(prompt).splitlines()
        if line.strip()
    }


def fenced_block(prompt: str, language: str) -> str:
    """Return the body of the first fenced block tagged with ``language``."""
    return prompt.split(f"```{language}", maxsplit=1)[1].split("```", maxsplit=1)[0]


@pytest.fixture(scope="module")
def skeleton(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Materialize :data:`BUNDLE_SKELETON` and return the bundle root."""
    root = tmp_path_factory.mktemp("bundle")
    for entry in BUNDLE_SKELETON:
        target = root / entry
        if entry.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
    return root


def resolves(skeleton: Path, path: Path) -> bool:
    """Return whether ``path`` names a location the skeleton can hold.

    True when the path exists, or when it is a file in an existing directory
    that the skeleton leaves open — that is an example slug, not a defect.
    Anything that escapes the bundle root is false, whatever it looks like.
    """
    resolved = path.resolve()
    if not resolved.is_relative_to(skeleton):
        return False
    if resolved.exists():
        return True
    parent = resolved.parent
    return (
        parent.is_dir() and parent.relative_to(skeleton).as_posix() not in CLOSED_DIRS
    )


class TestTheDiagramAgreesWithTheSkeleton:
    def test_manager_declares_the_whole_skeleton(self):
        """The manager bootstraps the bundle, so it must draw all of it."""
        assert declared_paths(PROMPTS[MANAGER]) == set(BUNDLE_SKELETON)

    def test_reader_declares_nothing_of_its_own(self):
        """The reader may omit what it never touches, but invent nothing."""
        undeclared = declared_paths(PROMPTS[READER]) - set(BUNDLE_SKELETON)

        assert not undeclared, (
            f"the reader's layout diagram shows {sorted(undeclared)}, which "
            "is not in BUNDLE_SKELETON"
        )


class TestEveryCitedPathExists:
    @pytest.mark.parametrize(
        ("audience", "cited"),
        [
            (audience, cited)
            for audience, prompt in PROMPTS.items()
            for cited in sorted(set(BUNDLE_PATH_RE.findall(prompt)))
        ],
    )
    def test_root_relative_path_is_in_the_bundle(self, audience, cited, skeleton):
        assert resolves(skeleton, skeleton / cited.lstrip("/")), (
            f"the {audience} prompt cites {cited!r}, which does not exist in "
            "the bundle skeleton"
        )


class TestExamplesResolveFromTheirOwnPage:
    @pytest.mark.parametrize(
        ("language", "anchor", "cited"),
        [
            (language, anchor, cited)
            for language, anchor in ANCHORED_EXAMPLES.items()
            for cited in RELATIVE_PATH_RE.findall(
                fenced_block(PROMPTS[MANAGER], language)
            )
        ],
    )
    def test_example_path_resolves(self, language, anchor, cited, skeleton):
        page = skeleton / anchor

        assert resolves(skeleton, page.parent / cited), (
            f"the {language} example is written into {anchor}, and {cited!r} "
            f"does not resolve from there ({(page.parent / cited).resolve()})"
        )
