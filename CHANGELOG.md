# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/giurlanda/deep-wiki-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/giurlanda/deep-wiki-agent/releases/tag/v0.1.0
