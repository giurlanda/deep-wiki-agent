# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Links between wiki pages are now written relative to the page that holds
  them**, never as absolute bundle paths: `documents/my-doc.md` from
  `index.md`, `../entities/acme-spa.md` from a document page. The path-valued
  frontmatter fields (`resource`, `sources`) follow the same rule. A bundle
  written this way stays navigable outside the agent — moved, rendered on
  GitHub, or opened in an editor. Both system prompts, `SKILL.md` and the docs
  state the rule; existing bundles full of absolute links keep resolving, but
  the linter now reports them.

### Added

- **`okf_lint` reports absolute links as errors.** A link whose target exists
  is reported once, as an absolute link rather than also as a broken one, and
  still counts as an inbound link so its target is not flagged an orphan on top.

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

### Added

- `deep_wiki_agent.okf_lint` — the conformance validator as a first-class,
  stdlib-only module, runnable as
  `python -m deep_wiki_agent.okf_lint <bundle> [--fix] [--json]`. It exits `1`
  when the bundle has errors, so it drops into CI or a pre-commit hook.

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

[Unreleased]: https://github.com/giurlanda/deep-wiki-agent/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/giurlanda/deep-wiki-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/giurlanda/deep-wiki-agent/releases/tag/v0.1.0
