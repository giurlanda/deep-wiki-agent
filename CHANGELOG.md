# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.1] - 2026-08-24

### Added

- **CI now deploys the built MkDocs site to GitHub Pages** on every push to
  `main`. The `docs` job still just builds (`mkdocs build --strict`) on PRs,
  as before; a new `deploy-docs` job, gated to `main` pushes, publishes the
  `site/` artifact via `actions/upload-pages-artifact` +
  `actions/deploy-pages`, with the corresponding `pages: write` /
  `id-token: write` permissions. README now links the published docs.

## [0.7.0] - 2026-08-09

### Added

- **Optional semantic + keyword search over a bundle, behind the new `semantic`
  extra.** The indexes and the link graph remain the primary route through a
  bundle, and up to a few hundred pages they replace embedding retrieval
  outright — past that they degrade, and this is the second entry point.
  `pip install "deep-wiki-agent[semantic]"` brings `langchain-text-splitters`
  and includes the `documents` extra, since ingestion has to read whatever sits
  in `raw/`. No vector store is pinned: pass any LangChain `VectorStore`.
- **`embeddings`, `vector_store`, `search_k` and `semantic_config` on both
  factories.** Given an embedding model and a store,
  `create_wiki_manager_agent` gains `semantic_ingest` and `semantic_search`,
  and `create_deep_wiki_agent` gains `semantic_search` alone — the ingestion
  tool writes, and the reader is read-only by construction, which the
  filesystem permissions cannot enforce for a tool that talks to a vector store
  rather than to files. Passing only one of `embeddings` and `vector_store`
  raises, rather than half-configuring an index that would fail on the model's
  first tool call.
- **`create_semantic_tools(embeddings, vector_store, *, wiki_path=None,
  backend=None, search_k=5, config=None)`** — the two tools and the
  `SemanticIndex` behind them, for wiring them yourself.
  **`ingest_semantic_index(...)`** is the same ingestion code path with no
  agent in it, for a cron job, a post-commit hook or a deploy step: an index
  that only refreshes when a model decides to call a tool is not one you can
  depend on.
- **Two new system-prompt sections, `SEMANTIC_MANAGER_BLOCK` and
  `SEMANTIC_READER_BLOCK`,** substituted into the templates' new
  `{semantic_block}` placeholder when the tools are attached, exactly as
  `lint_block` and `structured_output_block` already worked. The reader's block
  amends the query protocol rather than replacing it: a hit is an entry point,
  the page it points at is opened and read in full, citations name the page and
  never the excerpt, and an empty search is not on its own a "not found".
- **Incremental ingestion.** A small JSON manifest under `.okf/` — invisible to
  `okf_lint`, which walks `**/*.md` — records each file's digest and the ids
  its chunks were stored under. A second ingest skips unchanged files, updates
  a rewritten page's chunks instead of duplicating them, and deletes the chunks
  of a page that shrank or disappeared. A missing or corrupt manifest costs a
  full re-ingest, never an error.
- **Non-markdown sources are indexed too.** PDF, docx, csv, xlsx, pptx, html
  and the rest go through the same `markitdown` conversion `read_document`
  performs — reused by import, not reimplemented — so the sources under `raw/`
  are searchable alongside the wiki's own pages, distinguished by an `area`
  metadata field the search can filter on.
- `examples/semantic_wiki.py`, showing deterministic ingestion followed by a
  reader query against a hybrid Qdrant collection, and the `examples` extra
  gained `langchain-qdrant`, `qdrant-client` and `fastembed` to run it.

### Changed

- **`MANAGER_SYSTEM_PROMPT_TEMPLATE` and `READER_SYSTEM_PROMPT_TEMPLATE` take a
  new `semantic_block` placeholder.** Code that renders the templates by hand
  must pass it — an empty string reproduces the previous output exactly.
- `_normalize` and `_confine` moved out of `tools/documents.py` into a shared
  `_paths.py` (as `normalize`, `confine`, and a new non-raising `within`), so
  the semantic indexer confines its patterns with the same lexical resolution
  the document tool already used. `tools.documents._confine` still resolves to
  the same function.

## [0.6.1] - 2026-07-30

### Fixed

- **`.gitignore`'s VS Code launch-config entry had a typo.** It ignored
  `.vscode/launche.json`, a file nothing ever wrote, while `.vscode/launch.json`
  — the file VS Code actually creates — stayed trackable. Corrected to
  `.vscode/launch.json`.

### Added

- **A GitHub Actions workflow to publish releases to PyPI**,
  `.github/workflows/publish.yml`, triggered on `v*.*.*` tag pushes. It
  rebuilds the wheel, verifies the tag matches `__version__`, reruns the same
  check from the CI build job that keeps the `okf-wiki` skill out of the
  wheel, then publishes via `pypa/gh-action-pypi-publish` using PyPI Trusted
  Publishing (OIDC) — no API token stored in the repo.
- **A GitHub Actions CI pipeline**, `.github/workflows/ci.yml`, with four jobs:
  `ruff check` and `ruff format --check`; the test suite across Python 3.12 and
  3.13; a build that asserts the `okf-wiki` skill stays out of the wheel while
  `okf_lint.py` ships inside it, uploading the built `dist/`; and a strict
  `mkdocs build`.

### Changed

- **`examples/debug_middleware.py`'s type-only imports moved behind
  `TYPE_CHECKING`.** `AgentState`, `Runtime`, `Command` and `Callable` are used
  only in annotations, so importing them unconditionally pulled in `langgraph`
  and `langchain` typing surface the example has no runtime need for. The
  middleware hooks' unused `runtime` parameters are now `_runtime`, which
  satisfies ruff's unused-argument check without a `noqa`.
- `.gitignore` gained `examples/raw/` and an entry for the VS Code launch
  config (misspelled `launche.json`).
- `examples/debug_middleware.py` reformatted to the project's `ruff format`
  style; its `Callable` import moved from `typing` to `collections.abc`.

## [0.6.0] - 2026-07-29

### Added

- **A ready-made source-document tool, behind the optional `documents` extra.**
  `create_read_document_tool(wiki_path)` returns a `read_document` tool you pass
  in `tools=`; `pip install "deep-wiki-agent[documents]"` installs the
  [markitdown](https://github.com/microsoft/markitdown) stack it needs. Shipping
  no loaders was the right default, but nearly every bundle's `raw/` directory
  holds PDFs and every user was writing the same adapter. markitdown covers PDF,
  docx, pptx, xlsx, html and epub through one interface, hence `read_document`
  rather than a PDF-only tool. (#11)
- **`read_document(path, *, wiki_path=None, backend=None, root="/raw")`**, the
  same conversion outside an agent and without the tool's truncation. Both it
  and the tool are exported from the package root. (#11)

### Changed

- The core install is unchanged: `markitdown` is imported inside the converter
  rather than at module scope, so `deep_wiki_agent` still imports with no loader
  dependencies present, and a missing extra surfaces as an `ImportError`
  carrying the install command. (#11)

### Security

- `read_document` reads bytes through the backend's `download_files` rather than
  off the local filesystem, so it sees exactly the tree the agent's file tools
  do — a state, store, or sandbox-backed bundle included. Reads are confined to
  `/raw` with `..` segments collapsed before the check, so the model cannot aim
  the loader at the wiki's own pages or outside the bundle, and output is capped
  at `max_chars` (200 000 by default) so one oversized source cannot swallow the
  context window. (#11)

## [0.5.0] - 2026-07-29

### Added

- **An optional structured response for the reader.**
  `create_deep_wiki_agent(..., structured_output=True)` makes the agent answer
  with a `WikiAnswer` — `answer`, `citations`, `not_covered`, `found` — under
  `result["structured_response"]`. The reader's contract was expressible only as
  prose before, so a caller wanting to know whether the bundle covered the
  question had to string-match `not_found_message`: a test that stops working
  the moment that sentence is reworded or translated. `found is False` says the
  same thing structurally. Free text stays the default, since a schema costs the
  model the freedom to shape an answer to the question. (#12)
- **`WikiAnswer` is exported from the package root**, so callers can annotate
  what they read out of `structured_response`. Its field descriptions travel to
  the model as part of the schema. (#12)
- **`STRUCTURED_OUTPUT_BLOCK_TEMPLATE`**, the prompt section that tells the
  model how the four fields relate to the not-found contract — that a partial
  hit is `found: true` with `not_covered` set, and that `citations` holds wiki
  pages rather than sources under `raw/`. It is appended to the reader prompt
  only when `structured_output=True`, the way `LINT_TOOL_BLOCK` is appended to
  the manager's. (#12)

### Changed

- **`READER_SYSTEM_PROMPT_TEMPLATE` takes a fourth placeholder**,
  `structured_output_block`. Code that formats the template itself must pass it
  (an empty string reproduces the previous prompt exactly); the factory fills it
  for you. (#12)
- **`pydantic>=2.0,<3.0` is now a declared dependency.** It was already present
  through `langchain`; it is explicit now that a Pydantic model sits on this
  package's public API. (#12)

## [0.4.0] - 2026-07-26

### Added

- **`okf_lint` validates the path-valued frontmatter fields.** `resource` and
  `sources` are where a page records what it derives from, and until now only
  the markdown links in the body were checked, so a broken or absolute path
  there went unnoticed — exactly where the page → source traceability lives.
  They now get the same treatment as body links: broken paths and absolute
  paths are errors, URLs and prose `resource` values are left alone, and a page
  cited in another's `sources` counts as an inbound reference, so it is no
  longer flagged an orphan. (#10)
- **`--fix` makes absolute paths relative.** The conversion is mechanical — the
  page's own location is known — so absolute links and absolute frontmatter
  paths whose target exists are now rewritten relative to their page and
  reported as fixes instead of errors. One whose target does *not* exist is
  left untouched and still reported: rewriting it would only move a broken link
  around. (#10)
- **`type` values are checked against `AGENTS.md`.** The linter reads the
  vocabulary the bundle declares — the section whose heading mentions "types",
  taking the backticked names in it, or the lead token of each list item or
  table row when they are written plainly — and flags every `type` not in it,
  reporting a mere difference in capitalization as such. When `AGENTS.md`
  declares no types, the previous sprawl heuristic stays in charge. (#10)
- **The `log.md` entry format is linted.** The `## [YYYY-MM-DD] type | title`
  prefix exists so the history stays greppable; entries that drift from it, or
  that use a kind other than `ingest`/`query`/`lint`/`refactor`, are now
  warnings. (#10)
- **Duplicate slugs and titles are reported.** The file path is the identity of
  a concept, so the same slug under two categories — or two paths sharing a
  title, matched case-insensitively — is one concept with two identities, and
  is now a warning. (#10)
- **An `okf-lint` console script.** `[project.scripts]` now exposes the
  validator as `okf-lint <bundle> [--fix] [--json]`, usable with `uvx`/`pipx`
  and more convenient in CI than `python -m deep_wiki_agent.okf_lint`, which
  keeps working unchanged. (#10)

### Fixed

- **`AGENTS.md` is no longer flagged as a malformed concept page.** It was in
  neither the reserved names nor the skipped directories, so a perfectly
  legitimate bundle schema file was reported both for missing frontmatter and
  as an orphan. It is now structural, like `index.md` and `log.md`. (#10)

## [0.3.1] - 2026-07-26

### Fixed

- **`okf_lint --fix` no longer destroys the date a malformed timestamp
  states.** It previously replaced any non-ISO `timestamp` with the current
  time, silently losing the page's real last-update date. The fixer now first
  tries the common spellings (`2026/07/19`, `19-07-2026`, `19/07/2026`,
  `19.07.2026`, `20260719`, `19 July 2026`, `Jul 19, 2026`, with or without a
  time), normalizing them to ISO 8601 while preserving the value; naive values
  are read as UTC and date-only values anchored at midnight. `now()` is used
  only when nothing parses, and that case is now reported explicitly as
  `timestamp unparseable: ... (original date lost)` instead of being
  indistinguishable from a faithful normalization. (#9)

## [0.3.0] - 2026-07-26

### Added

- **`okf_lint` is now attachable to any backend, not just a local directory.**
  `deep_wiki_agent.okf_lint.lint` walks the bundle through a small
  `list_pages`/`read`/`exists`/`edit` interface instead of `Path` directly;
  local directories are still passed as a `Path` (wrapped automatically), and
  `run_okf_lint` / `create_okf_lint_tool` now also accept a `backend=`
  keyword — a deepagents `BackendProtocol` (state, store, sandbox) validated
  in place through its own `glob`/`read`/`edit` methods, `--fix` included. The
  `okf_lint.py` module itself still imports nothing beyond the standard
  library, so the shell entry point keeps working without installing
  `deepagents`. `create_wiki_manager_agent` now attaches the linter
  unconditionally when `enable_lint_tool=True`, regardless of which backend it
  was given. (#8)

### Removed

- `deep_wiki_agent.backends.resolve_local_wiki_path`, whose only purpose was
  gating the linter to filesystem-backed bundles — the gate this release
  removes. (#8)

## [0.2.2] - 2026-07-26

### Fixed

- **The linter is attached on an explicit backend type check, not on a
  `getattr(backend, "cwd", None)` probe.** `cwd` is a `FilesystemBackend`
  implementation detail rather than part of the backend protocol, so the old
  duck typing had two failure modes: if `deepagents` renamed the attribute the
  `okf_lint` tool would stop being attached with no signal at all, and any
  other backend that happened to expose a `cwd` would be mistaken for a local
  bundle. `resolve_local_wiki_path` now requires an actual `FilesystemBackend`.
  The `deepagents` dependency is capped at `<0.7` accordingly, until that
  surface is stable across a minor release. (#7)
- **A skipped `okf_lint` tool is no longer silent.** Building a manager with
  `enable_lint_tool=True` on a backend that is not filesystem-backed logs a
  warning on the `deep_wiki_agent.factory` logger naming the backend and
  pointing at `python -m deep_wiki_agent.okf_lint`, instead of returning an
  agent whose prompt tells it to run a linter it does not have. (#7)
- **The paths the prompts cite now match the `wiki/` layout they describe.**
  Section 1 of both prompts moved the bundle's pages under `wiki/` in 0.2.0,
  but several instructions kept addressing the flat layout: the manager was
  told to update "the `index.md` of the root" and append to `log.md`, to
  bootstrap `index.md` / `log.md`, and its log example cited a source as
  `raw/annual-report-2025.pdf` — a path that, resolved from `wiki/log.md`,
  points at `wiki/raw/`, which does not exist. The reader was sent back to
  `raw/` the same way. All of them are now written against the real layout,
  and both structure diagrams state explicitly that `raw/` sits beside
  `wiki/`, not inside it. (#5)
- The `READER_SYSTEM_PROMPT_TEMPLATE` examples in `docs/api.md` and
  `docs/okf.md` omitted the `raw_dir` placeholder, so copying them raised
  `KeyError: 'raw_dir'`.
- `deep_wiki_agent.__version__` was left at `0.2.0` by the 0.2.1 release; it
  tracks `pyproject.toml` again.
- **The prose documentation now draws the same `wiki/` layout as the prompts.**
  The bundle diagrams in `README.md`, `docs/architecture.md`, `docs/okf.md` and
  `skills/okf-wiki/SKILL.md` still showed the flat pre-0.2.0 tree, with
  `index.md`, `log.md` and the category directories at the bundle root, so a
  reader of the documentation and a reader of the prompts saw two different
  formats. The diagrams, the frontmatter and log examples written against them,
  and the surrounding path references now all describe the real layout —
  including `assets/`, which the docs placed under `raw/` while the manager
  bootstraps it at `wiki/assets/`. (#6)

### Added

- **`BUNDLE_SKELETON`**, the bundle layout the manager bootstraps, exported as
  a tuple of bundle-relative paths. It is the reference for the new
  `tests/test_prompt_paths.py`, which materializes the skeleton and checks that
  every path the prompts cite resolves inside it — root-relative paths against
  the bundle root, and the paths in the frontmatter and log examples against
  the page each example is written into. The drift this release fixes now fails
  the suite.
- **A layout guard over the documentation**, in `tests/test_prompt_drift.py`:
  the bundle diagram is parsed out of each document that draws one and held
  against `BUNDLE_SKELETON`, so a document that omits part of the layout, or
  places a page where the skeleton does not, fails the suite. The flat tree the
  docs carried until now is rejected by both checks. (#6)

## [0.2.1] - 2026-07-26

### Fixed

- **The reader agent (`create_deep_wiki_agent`) is now truly read-only across
  the whole bundle.** `read_only_permissions()` denied writes only under
  `/wiki/`, so with the bundle mounted at the virtual root the reader could
  still write `/AGENTS.md`, `/raw/**` and anything else outside `wiki/`. The
  rule is back to `paths=["/", "/**"]` (full deny), matching what `README.md`
  and `docs/architecture.md` already document and restoring the guarantee that
  makes it safe to expose the reader to untrusted questions. Added an
  end-to-end test that drives a real `write_file` call through
  `FilesystemMiddleware` and asserts the write is rejected at the tool
  boundary. (#4)

## [0.2.0] - 2026-07-22

**Breaking release.** The agents no longer load a skill: their instructions are
in their system prompts, and the OKF validator is ordinary package code. If you
only ever called `create_wiki_manager_agent` / `create_deep_wiki_agent` with
`model` and `wiki_path`, nothing changes for you. If you touched the skills
mount, read *Removed* below — this is a clean removal, with no deprecation
cycle, because the project is pre-1.0.

### Changed

- **Both agents' instructions moved from the bundled `okf-wiki` skill into
  their system prompts.** Bundle structure, OKF conformance, the ingest
  workflow, the query protocol, the lint checklist, bootstrap and the log
  format are now in force from the first turn. No agent reads a file before it
  can start, and no agent can silently end up with no instructions — which is
  what happened when invalid YAML frontmatter made `deepagents`'
  `SkillsMiddleware` skip the skill without raising. The content is split by
  audience: the reader does not carry the ingest, bootstrap or log sections it
  can never act on.
- **The default backend is a single `FilesystemBackend`** rooted at the bundle,
  where it used to be a `CompositeBackend` over the bundle and the skills tree.
  A caller supplying their own `backend` no longer has to mount a skill tree
  into it for the agent to work.
- **The manager's default permissions are now `/raw` only.** With no skills
  mount to guard, `protect_raw=False` yields *no* permission rules rather than
  one.
- **`skills/okf-wiki/` moved to the repository root and left the wheel.** It
  remains the canonical human-facing statement of the format and stays usable
  in Claude Code and other skill-aware harnesses; it is no longer package data
  and no longer mounted. `tests/test_prompt_drift.py` fails when it and the
  prompts drift apart.
- **The OKF linter's findings are reported in English** (`broken link`,
  `orphan page: no inbound links`, ...) instead of Italian, matching the
  prompts the agent reads them with. Code that string-matches on the old
  messages needs updating. `SKILL.md` and its reference notes were translated
  to English for the same reason.
- **Links between wiki pages are now written relative to the page that holds
  them**, never as absolute bundle paths: `documents/my-doc.md` from
  `index.md`, `../entities/acme-spa.md` from a document page. The path-valued
  frontmatter fields (`resource`, `sources`) follow the same rule. A bundle
  written this way stays navigable outside the agent — moved, rendered on
  GitHub, or opened in an editor. Both system prompts, `SKILL.md` and the docs
  state the rule; existing bundles full of absolute links keep resolving, but
  the linter now reports them.
- **The bundle now separates the writable wiki from the immutable sources at
  the root**: `wiki/` holds the OKF content (`index.md`, `log.md`,
  `documents/`, `entities/`, `concepts/`, `syntheses/`, `assets/`), `raw/`
  sits beside it rather than under it. Both system prompts and their
  linked-path examples were updated to the new layout (`../../raw/annual-
  report-2025.pdf` from an entity page, `wiki/index.md` as the entry point).
- **`read_only_permissions()` now denies writes under `/wiki/` instead of the
  whole bundle root**, matching the new layout.

### Added

- `deep_wiki_agent.okf_lint` — the conformance validator as a first-class,
  stdlib-only module, runnable as
  `python -m deep_wiki_agent.okf_lint <bundle> [--fix] [--json]`. It exits `1`
  when the bundle has errors, so it drops into CI or a pre-commit hook.
- **`okf_lint` reports absolute links as errors.** A link whose target exists
  is reported once, as an absolute link rather than also as a broken one, and
  still counts as an inbound link so its target is not flagged an orphan on top.
- `examples/debug_middleware.py` — a `DebugMiddleware` for `langchain` agents
  built with `create_deep_agent`, printing the model's last message and each
  tool call's result to stdout for local debugging. Ships behind a new
  `examples` extra (`langchain-openai`, `colorama`).

### Removed

| Symbol | Replacement |
|---|---|
| `build_wiki_backend` | `FilesystemBackend(root_dir=wiki_path, virtual_mode=True)` |
| `normalize_mount` | — |
| `bundled_skills_dir`, `okf_wiki_skill_dir`, `okf_lint_script` (`resources.py`) | — |
| `OKF_WIKI_SKILL_NAME`, `DEFAULT_SKILLS_MOUNT` | — |
| `skills_mount`, `skills_dir`, `extra_skills` on both factories | `system_prompt=` to change the instructions; `create_deep_agent`'s own `skills=` passthrough for genuinely extra skills |
| `scripts/okf_lint.py` inside the installed skill | `python -m deep_wiki_agent.okf_lint` |

Losing `skills_dir` is a real capability removal, called out here rather than
buried: it let you replace the agents' instructions at runtime by pointing at
your own directory, with no code change. That now goes through `system_prompt=`,
which was already the documented override.

Unchanged: `create_wiki_manager_agent`, `create_deep_wiki_agent`,
`create_okf_lint_tool`, `run_okf_lint`, `read_only_permissions`,
`write_protect_permissions`, `not_found_message`, `protect_raw`,
`enable_lint_tool`, `WIKI_ROOT`, `RAW_DIR`, and every `create_deep_agent`
passthrough.

## [0.1.0] - 2026-07-22

Initial release.

### Added

- `create_wiki_manager_agent` — deep agent that builds and maintains an OKF v0.1
  wiki bundle. Mounts the bundle at the virtual root, loads the bundled
  `okf-wiki` skill for its operating instructions, and write-protects `raw/`
  and the skills mount at the tool boundary.
- `create_deep_wiki_agent` — read-only deep agent that answers questions
  exclusively from an existing bundle, navigating indexes and the link graph as
  the skill's query protocol prescribes, and returning a fixed
  `not_found_message` when the bundle does not cover the question. Writes are
  denied by filesystem permissions, not merely by the prompt.
- `build_wiki_backend` — the `CompositeBackend` both factories assemble:
  the OKF bundle at `/`, the skills tree at `/skills/`.
- `okf_lint` tool (`create_okf_lint_tool`, `run_okf_lint`) — OKF conformance
  validation, wrapping the skill's standalone `scripts/okf_lint.py` so the
  manager agent can run the check the skill asks for without a shell.
- `read_only_permissions` / `write_protect_permissions` — the permission sets,
  reusable in caller-built agents.
- The `okf-wiki` skill, shipped as package data: bundle structure, OKF
  conformance rules, ingest / query / lint / bootstrap workflows, log format,
  OKF v0.1 reference notes, and the linter script.

[0.2.0]: https://github.com/giurlanda/deep-wiki-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/giurlanda/deep-wiki-agent/releases/tag/v0.1.0
