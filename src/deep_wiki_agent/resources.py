"""Locating the skills that ship inside the ``deep_wiki_agent`` package.

The library bundles the ``okf-wiki`` skill (SKILL.md + reference notes + the
``okf_lint.py`` validator) as package data so that both factories work out of
the box after a plain ``pip install deep-wiki-agent``, with no repository
checkout and no files copied into the user's wiki directory.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "OKF_WIKI_SKILL_NAME",
    "bundled_skills_dir",
    "okf_lint_script",
    "okf_wiki_skill_dir",
]

OKF_WIKI_SKILL_NAME = "okf-wiki"
"""Directory name of the bundled skill, also its ``name:`` in SKILL.md."""


def bundled_skills_dir() -> Path:
    """Return the directory holding the skills shipped with this package.

    This is the directory that *contains* skill directories (one per skill),
    i.e. the parent of ``okf-wiki/`` — it is what gets mounted at the agent's
    skills mount point by :func:`~deep_wiki_agent.backends.build_wiki_backend`.

    Returns:
        Absolute path to ``<package>/skills``.
    """
    return Path(__file__).parent / "skills"


def okf_wiki_skill_dir() -> Path:
    """Return the directory of the bundled ``okf-wiki`` skill itself.

    Returns:
        Absolute path to ``<package>/skills/okf-wiki``.

    Raises:
        FileNotFoundError: If the skill is missing from the installation,
            which means the package data was not included in the wheel.
    """
    path = bundled_skills_dir() / OKF_WIKI_SKILL_NAME
    if not (path / "SKILL.md").is_file():
        msg = (
            f"bundled skill not found at {path}/SKILL.md — the deep_wiki_agent "
            "installation is incomplete (package data missing from the wheel)"
        )
        raise FileNotFoundError(msg)
    return path


def okf_lint_script() -> Path:
    """Return the path to the bundled OKF bundle validator.

    Returns:
        Absolute path to ``<package>/skills/okf-wiki/scripts/okf_lint.py``.

    Raises:
        FileNotFoundError: If the script is missing from the installation.
    """
    path = okf_wiki_skill_dir() / "scripts" / "okf_lint.py"
    if not path.is_file():
        msg = f"bundled OKF linter not found at {path}"
        raise FileNotFoundError(msg)
    return path
