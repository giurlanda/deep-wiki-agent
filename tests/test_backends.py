"""Tests for backend assembly and the permission sets."""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

from deep_wiki_agent.backends import (
    build_wiki_backend,
    normalize_mount,
    read_only_permissions,
    resolve_local_wiki_path,
    write_protect_permissions,
)


def content_of(result) -> str:
    """Extract text from a backend ReadResult, failing loudly on read errors."""
    assert result.error is None, result.error
    return result.file_data["content"]


class TestNormalizeMount:
    @pytest.mark.parametrize(
        "raw", ["skills", "/skills", "skills/", "/skills/", "  /skills/  "]
    )
    def test_accepted_forms_normalize_identically(self, raw):
        assert normalize_mount(raw) == "/skills/"

    def test_nested_mount(self):
        assert normalize_mount("/agent/skills") == "/agent/skills/"

    @pytest.mark.parametrize("raw", ["", "/", "   ", "///"])
    def test_empty_or_root_rejected(self, raw):
        with pytest.raises(ValueError, match="must not be empty or the root"):
            normalize_mount(raw)


class TestBuildWikiBackend:
    def test_routes_skills_and_defaults_to_wiki(self, wiki_path):
        backend = build_wiki_backend(wiki_path)

        assert isinstance(backend, CompositeBackend)
        assert list(backend.routes) == ["/skills/"]
        assert Path(backend.default.cwd) == wiki_path.resolve()

    def test_wiki_pages_readable_at_bundle_paths(self, wiki_path):
        backend = build_wiki_backend(wiki_path)

        text = content_of(backend.read("/concepts/margine-operativo.md"))

        assert "type: Concept" in text

    def test_bundled_skill_readable_under_the_mount(self, wiki_path):
        backend = build_wiki_backend(wiki_path)

        text = content_of(backend.read("/skills/okf-wiki/SKILL.md"))

        assert "name: okf-wiki" in text

    def test_skill_tree_is_not_visible_inside_the_wiki(self, wiki_path):
        """The mount is virtual: nothing is copied into the user's bundle."""
        backend = build_wiki_backend(wiki_path)

        assert not (wiki_path / "skills").exists()
        assert backend.read("/okf-wiki/SKILL.md").error is not None

    def test_root_listing_shows_bundle_and_mount(self, wiki_path):
        backend = build_wiki_backend(wiki_path)

        paths = {entry["path"] for entry in backend.ls("/").entries}

        assert "/skills/" in paths
        assert any(p.endswith("index.md") for p in paths)

    def test_custom_mount_point(self, wiki_path):
        backend = build_wiki_backend(wiki_path, skills_mount="agent-skills")

        assert backend.read("/agent-skills/okf-wiki/SKILL.md").error is None

    def test_custom_skills_dir(self, wiki_path, tmp_path):
        custom = tmp_path / "custom-skills" / "my-skill"
        custom.mkdir(parents=True)
        (custom / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: x\n---\n", encoding="utf-8"
        )

        backend = build_wiki_backend(wiki_path, skills_dir=custom.parent)

        assert backend.read("/skills/my-skill/SKILL.md").error is None
        assert backend.read("/skills/okf-wiki/SKILL.md").error is not None

    def test_missing_wiki_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="wiki_path"):
            build_wiki_backend(tmp_path / "nope")

    def test_missing_skills_dir_raises(self, wiki_path, tmp_path):
        with pytest.raises(FileNotFoundError, match="skills directory"):
            build_wiki_backend(wiki_path, skills_dir=tmp_path / "nope")


class TestPermissions:
    def test_read_only_denies_writes_everywhere(self):
        (rule,) = read_only_permissions()

        assert rule.operations == ["write"]
        assert rule.mode == "deny"
        assert set(rule.paths) == {"/", "/**"}

    def test_read_only_leaves_reads_untouched(self):
        (rule,) = read_only_permissions()

        assert "read" not in rule.operations

    def test_write_protect_covers_dir_and_subtree(self):
        (rule,) = write_protect_permissions(["/raw", "skills/"])

        assert rule.mode == "deny"
        assert rule.paths == ["/raw", "/raw/**", "/skills", "/skills/**"]


class TestResolveLocalWikiPath:
    def test_unwraps_composite_to_the_default_backend(self, wiki_path):
        backend = build_wiki_backend(wiki_path)

        assert resolve_local_wiki_path(backend) == wiki_path.resolve()

    def test_plain_filesystem_backend(self, wiki_path):
        backend = FilesystemBackend(root_dir=wiki_path, virtual_mode=True)

        assert resolve_local_wiki_path(backend) == wiki_path.resolve()

    def test_non_filesystem_backend_yields_none(self):
        assert resolve_local_wiki_path(StateBackend()) is None
        assert resolve_local_wiki_path(None) is None
