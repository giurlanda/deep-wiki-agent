"""Tests for the OKF validator and the tool that exposes it to the agent."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from deep_wiki_agent.okf_lint import _coerce_timestamp, _is_iso8601
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

Vedi [fantasma](fantasma.md).
"""

ABSOLUTE_LINK_PAGE = """\
---
type: Concept
title: Assoluta
description: Punta a una pagina esistente, ma dalla root del bundle.
timestamp: 2026-07-19T10:30:00Z
---

Vedi [isolata](/concepts/isolata.md).
"""

ISOLATED_PAGE = """\
---
type: Concept
title: Isolata
description: Raggiungibile solo dal link assoluto di assoluta.md.
timestamp: 2026-07-19T10:30:00Z
---

Corpo.
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

SLASHED_TIMESTAMP_PAGE = """\
---
type: Concept
title: Data con barre
description: Timestamp parsabile ma non ISO.
timestamp: 2026/07/19
---

Corpo.
"""


def write_absolute_link_pair(wiki_path) -> None:
    """Add a page whose only link is absolute, plus the page it points to."""
    concepts = wiki_path / "concepts"
    concepts.joinpath("assoluta.md").write_text(ABSOLUTE_LINK_PAGE, encoding="utf-8")
    concepts.joinpath("isolata.md").write_text(ISOLATED_PAGE, encoding="utf-8")


class TestRunOkfLint:
    def test_clean_bundle_has_no_errors(self, wiki_path):
        result = run_okf_lint(wiki_path)

        assert result["errors"] == []

    def test_detects_broken_link(self, wiki_path):
        (wiki_path / "concepts" / "rotta.md").write_text(BROKEN_PAGE, encoding="utf-8")

        messages = [e["msg"] for e in run_okf_lint(wiki_path)["errors"]]

        assert any("broken link" in m for m in messages)

    def test_detects_absolute_link(self, wiki_path):
        write_absolute_link_pair(wiki_path)

        messages = [e["msg"] for e in run_okf_lint(wiki_path)["errors"]]

        assert any("absolute link" in m for m in messages)

    def test_absolute_link_is_not_also_reported_as_broken(self, wiki_path):
        """Its target does exist: one defect, one finding."""
        write_absolute_link_pair(wiki_path)

        messages = [e["msg"] for e in run_okf_lint(wiki_path)["errors"]]

        assert not any("broken link" in m for m in messages)

    def test_absolute_link_still_counts_as_inbound(self, wiki_path):
        """A link written the wrong way still makes its target non-orphan."""
        write_absolute_link_pair(wiki_path)

        orphans = [
            w["file"]
            for w in run_okf_lint(wiki_path)["warnings"]
            if "orphan" in w["msg"]
        ]

        assert "concepts/isolata.md" not in orphans

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

    def test_fix_preserves_a_parseable_date(self, wiki_path):
        """The page's real last-update date survives normalization."""
        page = wiki_path / "concepts" / "data-barrata.md"
        page.write_text(SLASHED_TIMESTAMP_PAGE, encoding="utf-8")

        result = run_okf_lint(wiki_path, fix=True)

        assert "timestamp: 2026-07-19T00:00:00Z" in page.read_text(encoding="utf-8")
        assert any("2026/07/19 -> 2026-07-19" in f["msg"] for f in result["fixes"])

    def test_fix_reports_when_the_original_date_is_lost(self, wiki_path):
        """Falling back to now() destroys information, so it must be visible."""
        page = wiki_path / "concepts" / "data-storta.md"
        page.write_text(BAD_TIMESTAMP_PAGE, encoding="utf-8")

        messages = [f["msg"] for f in run_okf_lint(wiki_path, fix=True)["fixes"]]

        assert any("unparseable" in m and "original date lost" in m for m in messages)

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            run_okf_lint(tmp_path / "nope")


class TestCoerceTimestamp:
    """Normalization keeps the stated date; `now()` is only the last resort."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026/07/19", "2026-07-19T00:00:00Z"),
            ("2026/07/19 10:30", "2026-07-19T10:30:00Z"),
            ("2026/07/19 10:30:15", "2026-07-19T10:30:15Z"),
            ("19-07-2026", "2026-07-19T00:00:00Z"),
            ("19-07-2026 10:30", "2026-07-19T10:30:00Z"),
            ("19/07/2026", "2026-07-19T00:00:00Z"),
            ("19.07.2026", "2026-07-19T00:00:00Z"),
            ("20260719", "2026-07-19T00:00:00Z"),
            ("19 July 2026", "2026-07-19T00:00:00Z"),
            ("Jul 19, 2026", "2026-07-19T00:00:00Z"),
            ("  2026/07/19  ", "2026-07-19T00:00:00Z"),
        ],
    )
    def test_parses_common_spellings(self, raw, expected):
        assert _coerce_timestamp(raw) == expected

    @pytest.mark.parametrize("raw", ["ieri", "", "   ", "sometime in 2026", "19-07"])
    def test_returns_none_for_unparseable_values(self, raw):
        assert _coerce_timestamp(raw) is None

    def test_iso_values_never_reach_coercion(self):
        """`lint` only coerces what `_is_iso8601` rejects."""
        assert _is_iso8601("2026-07-19T10:30:00Z")
        assert not _is_iso8601("2026/07/19")


class TestRunOkfLintOnBackends:
    """A bundle held in a non-local backend is just as lintable.

    The linter walks it through `glob`/`read`/`edit` instead of `Path`, so the
    same checks apply regardless of where the bundle actually lives.
    """

    def test_clean_bundle_has_no_errors(self, store_backend):
        result = run_okf_lint(backend=store_backend)

        assert result["errors"] == []

    def test_detects_broken_link(self, store_backend):
        store_backend.write("/concepts/rotta.md", BROKEN_PAGE)

        messages = [e["msg"] for e in run_okf_lint(backend=store_backend)["errors"]]

        assert any("broken link" in m for m in messages)

    def test_detects_missing_required_field(self, store_backend):
        store_backend.write(
            "/concepts/senza-tipo.md", "---\ntitle: Senza tipo\n---\n\nCorpo.\n"
        )

        messages = [e["msg"] for e in run_okf_lint(backend=store_backend)["errors"]]

        assert any("required OKF field missing: `type`" in m for m in messages)

    def test_raw_directory_is_excluded_from_validation(self, store_backend):
        files = [e["file"] for e in run_okf_lint(backend=store_backend)["errors"]]

        assert not any(f.startswith("raw/") for f in files)

    def test_fix_normalizes_bad_timestamp(self, store_backend):
        store_backend.write("/concepts/data-storta.md", BAD_TIMESTAMP_PAGE)

        assert run_okf_lint(backend=store_backend)["errors"]

        result = run_okf_lint(backend=store_backend, fix=True)

        assert result["fixes"]
        content = store_backend.read("/concepts/data-storta.md").file_data["content"]
        assert "timestamp: ieri" not in content


class TestCreateOkfLintToolOnBackends:
    def test_reports_a_clean_bundle(self, store_backend):
        report = create_okf_lint_tool(backend=store_backend).invoke({})

        assert "0 error(s)" in report

    def test_reports_errors(self, store_backend):
        store_backend.write("/concepts/rotta.md", BROKEN_PAGE)

        report = create_okf_lint_tool(backend=store_backend).invoke({})

        assert "ERROR" in report
        assert "broken link" in report

    def test_fix_applies_through_the_tool(self, store_backend):
        store_backend.write("/concepts/data-storta.md", BAD_TIMESTAMP_PAGE)

        report = create_okf_lint_tool(backend=store_backend).invoke({"fix": True})

        assert "fix(es) applied" in report


class TestLintTargetArgumentValidation:
    def test_run_requires_exactly_one_target(self, wiki_path, store_backend):
        with pytest.raises(ValueError, match="exactly one of"):
            run_okf_lint(wiki_path, backend=store_backend)
        with pytest.raises(ValueError, match="exactly one of"):
            run_okf_lint()

    def test_create_tool_requires_exactly_one_target(self, wiki_path, store_backend):
        with pytest.raises(ValueError, match="exactly one of"):
            create_okf_lint_tool(wiki_path, backend=store_backend)
        with pytest.raises(ValueError, match="exactly one of"):
            create_okf_lint_tool()


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
        page = wiki_path / "concepts" / "data-barrata.md"
        page.write_text(SLASHED_TIMESTAMP_PAGE, encoding="utf-8")

        result = self.run(str(wiki_path), "--fix")

        assert "timestamp normalized" in result.stdout
        assert "timestamp: 2026-07-19T00:00:00Z" in page.read_text(encoding="utf-8")

    def test_missing_directory_exits_two(self, tmp_path):
        result = self.run(str(tmp_path / "nope"))

        assert result.returncode == 2
        assert "is not a directory" in result.stderr
