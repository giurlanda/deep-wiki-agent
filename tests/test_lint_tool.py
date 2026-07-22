"""Tests for the OKF validator and the tool that exposes it to the agent."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from deep_wiki_agent.tools.lint import (
    OKF_LINT_TOOL_NAME,
    create_okf_lint_tool,
    run_okf_lint,
)

BROKEN_PAGE = """\
---
type: Concept
title: Rotta
description: Punta a una pagina inesistente.
timestamp: 2026-07-19T10:30:00Z
---

Vedi [fantasma](/concepts/fantasma.md).
"""

NO_FRONTMATTER_PAGE = "# Nuda\n\nNessun frontmatter qui.\n"

BAD_TIMESTAMP_PAGE = """\
---
type: Concept
title: Data storta
description: Timestamp non ISO.
timestamp: ieri
---

Corpo.
"""


class TestRunOkfLint:
    def test_clean_bundle_has_no_errors(self, wiki_path):
        result = run_okf_lint(wiki_path)

        assert result["errors"] == []

    def test_detects_broken_link(self, wiki_path):
        (wiki_path / "concepts" / "rotta.md").write_text(BROKEN_PAGE, encoding="utf-8")

        messages = [e["msg"] for e in run_okf_lint(wiki_path)["errors"]]

        assert any("broken link" in m for m in messages)

    def test_detects_missing_frontmatter(self, wiki_path):
        (wiki_path / "concepts" / "nuda.md").write_text(
            NO_FRONTMATTER_PAGE, encoding="utf-8"
        )

        messages = [e["msg"] for e in run_okf_lint(wiki_path)["errors"]]

        assert any("frontmatter" in m for m in messages)

    def test_detects_missing_required_field(self, wiki_path):
        (wiki_path / "concepts" / "senza-tipo.md").write_text(
            "---\ntitle: Senza tipo\n---\n\nCorpo.\n", encoding="utf-8"
        )

        messages = [e["msg"] for e in run_okf_lint(wiki_path)["errors"]]

        assert any("required OKF field missing: `type`" in m for m in messages)

    def test_raw_directory_is_excluded_from_validation(self, wiki_path):
        (wiki_path / "raw" / "note.md").write_text(
            NO_FRONTMATTER_PAGE, encoding="utf-8"
        )

        files = [e["file"] for e in run_okf_lint(wiki_path)["errors"]]

        assert not any(f.startswith("raw/") for f in files)

    def test_fix_normalizes_bad_timestamp(self, wiki_path):
        page = wiki_path / "concepts" / "data-storta.md"
        page.write_text(BAD_TIMESTAMP_PAGE, encoding="utf-8")

        assert run_okf_lint(wiki_path)["errors"]

        result = run_okf_lint(wiki_path, fix=True)

        assert result["fixes"]
        assert "timestamp: ieri" not in page.read_text(encoding="utf-8")

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            run_okf_lint(tmp_path / "nope")


class TestCreateOkfLintTool:
    def test_tool_metadata(self, wiki_path):
        tool = create_okf_lint_tool(wiki_path)

        assert tool.name == OKF_LINT_TOOL_NAME
        assert "OKF" in tool.description
        assert set(tool.args) == {"fix"}

    def test_path_is_not_a_tool_argument(self, wiki_path):
        """The bundle is bound in the closure so the model cannot redirect it."""
        tool = create_okf_lint_tool(wiki_path)

        assert "wiki_path" not in tool.args

    def test_reports_a_clean_bundle(self, wiki_path):
        report = create_okf_lint_tool(wiki_path).invoke({})

        assert "0 error(s)" in report

    def test_reports_errors(self, wiki_path):
        (wiki_path / "concepts" / "rotta.md").write_text(BROKEN_PAGE, encoding="utf-8")

        report = create_okf_lint_tool(wiki_path).invoke({})

        assert "ERROR" in report
        assert "broken link" in report

    def test_reports_error_string_instead_of_raising(self, tmp_path):
        tool = create_okf_lint_tool(tmp_path / "gone")

        assert tool.invoke({}).startswith("ERROR: okf_lint could not run")

    def test_truncates_a_flood_of_findings(self, wiki_path):
        for i in range(80):
            (wiki_path / "concepts" / f"nuda-{i}.md").write_text(
                NO_FRONTMATTER_PAGE, encoding="utf-8"
            )

        report = create_okf_lint_tool(wiki_path).invoke({})

        assert "not shown" in report
        assert report.count("ERROR ") <= 51
        assert "80 error(s)" in report


class TestShellEntryPoint:
    """`python -m deep_wiki_agent.okf_lint` must keep working from a shell.

    The agent's tool is the convenient path, not the only one: a bundle stays
    verifiable by anyone holding the directory.
    """

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-m", "deep_wiki_agent.okf_lint", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_clean_bundle_exits_zero(self, wiki_path):
        result = self.run(str(wiki_path))

        assert result.returncode == 0
        assert "0 errors" in result.stdout

    def test_errors_exit_one(self, wiki_path):
        (wiki_path / "concepts" / "rotta.md").write_text(BROKEN_PAGE, encoding="utf-8")

        result = self.run(str(wiki_path))

        assert result.returncode == 1
        assert "broken link" in result.stdout

    def test_json_output_is_machine_readable(self, wiki_path):
        result = self.run(str(wiki_path), "--json")

        payload = json.loads(result.stdout)

        assert set(payload) == {"errors", "warnings", "fixes"}

    def test_fix_normalizes_in_place(self, wiki_path):
        page = wiki_path / "concepts" / "data-storta.md"
        page.write_text(BAD_TIMESTAMP_PAGE, encoding="utf-8")

        result = self.run(str(wiki_path), "--fix")

        assert "timestamp normalized" in result.stdout
        assert "timestamp: ieri" not in page.read_text(encoding="utf-8")

    def test_missing_directory_exits_two(self, tmp_path):
        result = self.run(str(tmp_path / "nope"))

        assert result.returncode == 2
        assert "is not a directory" in result.stderr
