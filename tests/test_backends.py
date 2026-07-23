"""Tests for the permission sets and local-root resolution."""

from __future__ import annotations

from deepagents.backends import FilesystemBackend, StateBackend

from deep_wiki_agent.backends import (
    read_only_permissions,
    resolve_local_wiki_path,
    write_protect_permissions,
)


class TestPermissions:
    def test_read_only_denies_writes_everywhere(self):
        (rule,) = read_only_permissions()

        assert rule.operations == ["write"]
        assert rule.mode == "deny"
        assert set(rule.paths) == {"/wiki/", "/wiki/**"}

    def test_read_only_leaves_reads_untouched(self):
        (rule,) = read_only_permissions()

        assert "read" not in rule.operations

    def test_write_protect_covers_dir_and_subtree(self):
        (rule,) = write_protect_permissions(["/raw", "archive/"])

        assert rule.mode == "deny"
        assert rule.paths == ["/raw", "/raw/**", "/archive", "/archive/**"]

    def test_no_paths_yields_no_rules(self):
        """A rule with no patterns would restrict nothing while looking strict."""
        assert write_protect_permissions([]) == []


class TestResolveLocalWikiPath:
    def test_filesystem_backend(self, wiki_path):
        backend = FilesystemBackend(root_dir=wiki_path, virtual_mode=True)

        assert resolve_local_wiki_path(backend) == wiki_path.resolve()

    def test_non_filesystem_backend_yields_none(self):
        assert resolve_local_wiki_path(StateBackend()) is None
        assert resolve_local_wiki_path(None) is None
