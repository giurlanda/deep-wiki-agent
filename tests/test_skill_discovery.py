"""The integration point everything else rests on.

Both factories work only if deepagents' ``SkillsMiddleware`` can actually
discover ``okf-wiki`` through the composite mount. That requires the route
prefix, the mount normalization, the skill's directory name and its YAML
frontmatter all to agree — and when they do not, deepagents *skips the skill
silently*: the agent still runs, it just has no instructions. Nothing else in
the suite would notice.
"""

from __future__ import annotations

import yaml
from deepagents.middleware.skills import _list_skills, _list_skills_with_errors

from deep_wiki_agent import bundled_skills_dir, okf_wiki_skill_dir
from deep_wiki_agent.backends import build_wiki_backend


def discover(wiki_path, mount="/skills"):
    """Run deepagents' own skill discovery over the factories' mount."""
    backend = build_wiki_backend(wiki_path, skills_mount=mount)
    normalized = f"/{mount.strip('/')}/"
    return backend, _list_skills(backend, normalized)


class TestSkillIsDiscoverable:
    def test_middleware_finds_the_skill_through_the_mount(self, wiki_path):
        _, skills = discover(wiki_path)

        assert {s["name"] for s in skills} == {"okf-wiki"}

    def test_discovery_reports_no_parse_errors(self, wiki_path):
        """Invalid frontmatter makes deepagents skip the skill without raising."""
        backend = build_wiki_backend(wiki_path)

        skills, error = _list_skills_with_errors(backend, "/skills/")

        assert error is None
        assert skills

    def test_discovered_skill_carries_its_description(self, wiki_path):
        _, skills = discover(wiki_path)

        (skill,) = skills
        assert "OKF" in skill["description"]
        assert len(skill["description"]) > 100

    def test_discovered_path_is_the_one_the_prompt_tells_the_agent_to_read(
        self, wiki_path
    ):
        """A mismatch here sends the agent to read a path that does not exist."""
        backend, skills = discover(wiki_path)

        (skill,) = skills
        assert skill["path"] == "/skills/okf-wiki/SKILL.md"
        assert backend.read(skill["path"]).error is None

    def test_discovery_follows_a_custom_mount(self, wiki_path):
        _, skills = discover(wiki_path, mount="kb-skills")

        (skill,) = skills
        assert skill["path"] == "/kb-skills/okf-wiki/SKILL.md"

    def test_supporting_files_are_reachable_from_the_skill_dir(self, wiki_path):
        backend, _ = discover(wiki_path)

        assert (
            backend.read("/skills/okf-wiki/references/okf-spec-notes.md").error is None
        )
        assert backend.read("/skills/okf-wiki/scripts/okf_lint.py").error is None


class TestBundledSkillIsWellFormed:
    def test_skill_directory_layout(self):
        skill = okf_wiki_skill_dir()

        assert (skill / "SKILL.md").is_file()
        assert (skill / "references" / "okf-spec-notes.md").is_file()
        assert (skill / "scripts" / "okf_lint.py").is_file()

    def test_skill_dir_is_inside_the_skills_root(self):
        assert okf_wiki_skill_dir().parent == bundled_skills_dir()

    def test_frontmatter_is_valid_yaml_with_the_expected_name(self):
        text = (okf_wiki_skill_dir() / "SKILL.md").read_text(encoding="utf-8")

        assert text.startswith("---\n")
        front = yaml.safe_load(text.split("---\n", 2)[1])

        assert front["name"] == "okf-wiki"
        assert front["description"].strip()

    def test_skill_documents_the_workflows_the_prompts_delegate_to_it(self):
        text = (okf_wiki_skill_dir() / "SKILL.md").read_text(encoding="utf-8")

        for section in ("Ingest", "Query", "Lint", "Bootstrap"):
            assert section in text
