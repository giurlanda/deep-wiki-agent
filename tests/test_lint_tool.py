"""Tests for the okf_lint tool wrapping the skill's validator script."""

from __future__ import annotations

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

        assert any("link rotto" in m for m in messages)

    def test_detects_missing_frontmatter(self, wiki_path):
        (wiki_path / "concepts" / "nuda.md").write_text(
            NO_FRONTMATTER_PAGE, encoding="utf-8"
        )

        messages = [e["msg"] for e in run_okf_lint(wiki_path)["errors"]]

        assert any("frontmatter" in m for m in messages)

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
        assert "link rotto" in report

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
