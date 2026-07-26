"""Tests for the permission sets and local-root resolution."""

from __future__ import annotations

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from deep_wiki_agent.backends import read_only_permissions, write_protect_permissions


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
        (rule,) = write_protect_permissions(["/raw", "archive/"])

        assert rule.mode == "deny"
        assert rule.paths == ["/raw", "/raw/**", "/archive", "/archive/**"]

    def test_no_paths_yields_no_rules(self):
        """A rule with no patterns would restrict nothing while looking strict."""
        assert write_protect_permissions([]) == []


def _reader_write_tool(wiki_path) -> ToolNode:
    """A ``write_file`` tool wired exactly as the reader agent gets it.

    Mounts the bundle at the virtual root behind ``FilesystemMiddleware`` with
    the reader's permission set, so the tool goes through the same permission
    check the compiled reader agent applies at runtime.
    """
    backend = FilesystemBackend(root_dir=wiki_path, virtual_mode=True)
    middleware = FilesystemMiddleware(
        backend=backend, _permissions=read_only_permissions()
    )
    write_file = next(t for t in middleware.tools if t.name == "write_file")
    return ToolNode([write_file])


def _attempt_write(node: ToolNode, path: str):
    """Drive one ``write_file`` call through the middleware and return the result."""
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"file_path": path, "content": "prompt-injected"},
                "id": "call-1",
            }
        ],
    )
    result = node.invoke({"messages": [call]}, runtime=Runtime(context=None))
    return result["messages"][-1]


class TestReadOnlyEnforcement:
    """The read-only guarantee must hold at the tool boundary, not just in kwargs.

    These drive a real ``write_file`` call through ``FilesystemMiddleware``.
    Under the previous ``/wiki/`` rule, writes outside ``wiki/`` (e.g.
    ``/AGENTS.md``, ``/raw/x.md``) slipped through — this is the regression guard.
    """

    @pytest.mark.parametrize(
        "path",
        ["/AGENTS.md", "/raw/x.md", "/index.md", "/concepts/margine-operativo.md"],
    )
    def test_every_write_is_rejected_by_the_middleware(self, wiki_path, path):
        message = _attempt_write(_reader_write_tool(wiki_path), path)

        assert message.status == "error"
        assert "permission denied for write" in message.content

    def test_a_denied_write_never_touches_disk(self, wiki_path):
        _attempt_write(_reader_write_tool(wiki_path), "/AGENTS.md")

        assert not (wiki_path / "AGENTS.md").exists()
