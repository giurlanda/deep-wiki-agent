"""Tests for the optional source-document loader and the tool exposing it."""

from __future__ import annotations

import pytest

from deep_wiki_agent.tools.documents import (
    READ_DOCUMENT_TOOL_NAME,
    _confine,
    create_read_document_tool,
    read_document,
)

PDF_TEXT = "Margine operativo lordo"
HTML_BYTES = b"<h1>Bilancio</h1><p>Ricavi netti in crescita.</p>"


def minimal_pdf(text: str) -> bytes:
    """Build the smallest PDF that still carries an extractable text run.

    Written out by hand rather than pulled from a fixture file so the binary
    payload the test asserts on is visible in the test itself — and so the
    suite needs no PDF writer at test time.
    """
    content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"

    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


@pytest.fixture
def wiki_with_pdf(wiki_path):
    """The shared conformant bundle, with a real PDF dropped into `raw/`."""
    (wiki_path / "raw" / "paper.pdf").write_bytes(minimal_pdf(PDF_TEXT))
    return wiki_path


@pytest.fixture
def store_with_pdf(store_backend):
    """A `StoreBackend` holding the same PDF, stored base64 as binary.

    The interesting half of the test matrix: the bytes only survive if the
    tool goes through `download_files` rather than the text-decoding `read`.
    """
    store_backend.upload_files([("/raw/paper.pdf", minimal_pdf(PDF_TEXT))])
    return store_backend


class TestConfine:
    """`/raw` is a boundary, not a suggestion."""

    @pytest.mark.parametrize(
        "path",
        ["paper.pdf", "raw/paper.pdf", "/raw/paper.pdf", "./paper.pdf"],
    )
    def test_accepts_the_spellings_a_model_produces(self, path):
        assert _confine(path, "/raw") == "/raw/paper.pdf"

    def test_keeps_subdirectories(self):
        assert _confine("2026/paper.pdf", "/raw") == "/raw/2026/paper.pdf"

    @pytest.mark.parametrize(
        "path",
        [
            "/concepts/margine-operativo.md",
            "/index.md",
            "../concepts/margine-operativo.md",
            "/raw/../log.md",
            "../../../etc/passwd",
        ],
    )
    def test_rejects_anything_outside_the_root(self, path):
        with pytest.raises(ValueError, match="outside /raw"):
            _confine(path, "/raw")

    def test_rejects_an_empty_path(self):
        with pytest.raises(ValueError, match="empty"):
            _confine("   ", "/raw")

    def test_rejects_the_root_itself(self):
        with pytest.raises(ValueError, match="outside /raw"):
            _confine("/raw", "/raw")


class TestReadDocument:
    def test_converts_a_pdf_from_a_local_bundle(self, wiki_with_pdf):
        markdown = read_document("paper.pdf", wiki_path=wiki_with_pdf)

        assert PDF_TEXT in markdown

    def test_converts_a_pdf_from_a_backend(self, store_with_pdf):
        markdown = read_document("paper.pdf", backend=store_with_pdf)

        assert PDF_TEXT in markdown

    def test_converts_a_non_pdf_format(self, wiki_path):
        (wiki_path / "raw" / "note.html").write_bytes(HTML_BYTES)

        markdown = read_document("note.html", wiki_path=wiki_path)

        assert "Bilancio" in markdown
        assert "Ricavi netti" in markdown

    def test_absolute_and_relative_paths_agree(self, wiki_with_pdf):
        relative = read_document("paper.pdf", wiki_path=wiki_with_pdf)
        absolute = read_document("/raw/paper.pdf", wiki_path=wiki_with_pdf)

        assert relative == absolute

    def test_reading_outside_raw_raises(self, wiki_with_pdf):
        with pytest.raises(ValueError, match="outside /raw"):
            read_document("/concepts/margine-operativo.md", wiki_path=wiki_with_pdf)

    def test_a_missing_document_raises(self, wiki_with_pdf):
        with pytest.raises(RuntimeError, match="could not read"):
            read_document("assente.pdf", wiki_path=wiki_with_pdf)

    def test_requires_exactly_one_target(self, wiki_with_pdf, store_with_pdf):
        with pytest.raises(ValueError, match="exactly one of"):
            read_document("paper.pdf", wiki_path=wiki_with_pdf, backend=store_with_pdf)
        with pytest.raises(ValueError, match="exactly one of"):
            read_document("paper.pdf")

    def test_a_missing_bundle_directory_raises(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            read_document("paper.pdf", wiki_path=tmp_path / "assente")

    def test_a_custom_root_moves_the_boundary(self, wiki_path):
        (wiki_path / "concepts" / "note.html").write_bytes(HTML_BYTES)

        markdown = read_document("note.html", wiki_path=wiki_path, root="/concepts")

        assert "Bilancio" in markdown


class TestCreateReadDocumentTool:
    def test_is_named_for_the_agent(self, wiki_with_pdf):
        tool = create_read_document_tool(wiki_with_pdf)

        assert tool.name == READ_DOCUMENT_TOOL_NAME
        assert "path" in tool.args

    def test_returns_the_converted_document(self, wiki_with_pdf):
        tool = create_read_document_tool(wiki_with_pdf)

        assert PDF_TEXT in tool.invoke({"path": "paper.pdf"})

    def test_works_against_a_backend(self, store_with_pdf):
        tool = create_read_document_tool(backend=store_with_pdf)

        assert PDF_TEXT in tool.invoke({"path": "paper.pdf"})

    def test_reports_a_failure_instead_of_raising(self, wiki_with_pdf):
        """The model gets a message it can act on, not a broken tool call."""
        tool = create_read_document_tool(wiki_with_pdf)

        result = tool.invoke({"path": "assente.pdf"})

        assert result.startswith("ERROR:")

    def test_refuses_to_leave_the_source_directory(self, wiki_with_pdf):
        tool = create_read_document_tool(wiki_with_pdf)

        result = tool.invoke({"path": "/concepts/margine-operativo.md"})

        assert result.startswith("ERROR:")
        assert "outside /raw" in result

    def test_truncates_an_oversized_document(self, wiki_path):
        (wiki_path / "raw" / "lungo.html").write_bytes(
            b"<p>" + b"parola " * 2000 + b"</p>"
        )
        tool = create_read_document_tool(wiki_path, max_chars=100)

        result = tool.invoke({"path": "lungo.html"})

        assert "[truncated:" in result
        assert "/raw/lungo.html" in result

    def test_a_document_under_the_cap_is_untouched(self, wiki_path):
        (wiki_path / "raw" / "note.html").write_bytes(HTML_BYTES)
        tool = create_read_document_tool(wiki_path, max_chars=10_000)

        assert "truncated" not in tool.invoke({"path": "note.html"})

    def test_requires_exactly_one_target(self, wiki_with_pdf, store_with_pdf):
        with pytest.raises(ValueError, match="exactly one of"):
            create_read_document_tool(wiki_with_pdf, backend=store_with_pdf)
        with pytest.raises(ValueError, match="exactly one of"):
            create_read_document_tool()

    def test_rejects_a_non_positive_cap(self, wiki_with_pdf):
        with pytest.raises(ValueError, match="max_chars must be positive"):
            create_read_document_tool(wiki_with_pdf, max_chars=0)

    def test_a_missing_bundle_fails_at_build_time(self, tmp_path):
        """Bad wiring surfaces where the agent is assembled, not mid-run."""
        with pytest.raises(NotADirectoryError):
            create_read_document_tool(tmp_path / "assente")


class TestDocumentsExtra:
    """The loader stack is opt-in; the core install must not require it."""

    def test_extra_is_declared(self):
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        extras = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
            "optional-dependencies"
        ]

        assert any(dep.startswith("markitdown") for dep in extras["documents"])

    def test_markitdown_is_not_a_core_dependency(self):
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        dependencies = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
            "dependencies"
        ]

        assert not any("markitdown" in dep for dep in dependencies)

    def test_a_missing_extra_names_the_install_command(
        self, monkeypatch, wiki_with_pdf
    ):
        """The error a user without the extra actually sees, simulated."""
        import builtins

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name.startswith("markitdown"):
                msg = f"No module named {name!r}"
                raise ImportError(msg)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)

        with pytest.raises(ImportError, match=r"deep-wiki-agent\[documents\]"):
            read_document("paper.pdf", wiki_path=wiki_with_pdf)

    def test_the_tool_reports_a_missing_extra_instead_of_raising(
        self, monkeypatch, wiki_with_pdf
    ):
        import builtins

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name.startswith("markitdown"):
                msg = f"No module named {name!r}"
                raise ImportError(msg)
            return real_import(name, *args, **kwargs)

        tool = create_read_document_tool(wiki_with_pdf)
        monkeypatch.setattr(builtins, "__import__", blocked)

        result = tool.invoke({"path": "paper.pdf"})

        assert result.startswith("ERROR:")
        assert "deep-wiki-agent[documents]" in result

    def test_importing_the_module_does_not_import_markitdown(self):
        """`markitdown` is imported inside the converter, not at module scope."""
        import ast
        from pathlib import Path

        from deep_wiki_agent.tools import documents

        tree = ast.parse(Path(documents.__file__).read_text(encoding="utf-8"))
        names = [
            alias.name if isinstance(node, ast.Import) else (node.module or "")
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]

        assert not any(name.startswith("markitdown") for name in names)


class TestAgentWiring:
    """The documented way to attach the tool actually assembles."""

    def test_manager_accepts_the_tool(self, wiki_with_pdf, model):
        from deep_wiki_agent import create_wiki_manager_agent

        agent = create_wiki_manager_agent(
            model=model,
            wiki_path=wiki_with_pdf,
            tools=[create_read_document_tool(wiki_with_pdf)],
        )

        assert agent is not None

    def test_exported_from_the_package_root(self):
        import deep_wiki_agent

        assert "create_read_document_tool" in deep_wiki_agent.__all__
        assert "read_document" in deep_wiki_agent.__all__
