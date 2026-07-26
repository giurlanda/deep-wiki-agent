# Architecture

Both factories are thin wrappers over `deepagents.create_deep_agent`. What they
add is the wiring that makes an OKF bundle usable by an agent. This page
explains each piece and why it is shaped that way.

## The filesystem

The agent sees exactly one tree: your bundle, at the virtual root.

```python
FilesystemBackend(root_dir=wiki_path, virtual_mode=True)
```

```
/                          -> your OKF bundle
├── AGENTS.md
├── raw/
└── wiki/
    ├── index.md
    ├── log.md
    ├── assets/
    ├── documents/
    ├── entities/
    ├── concepts/
    └── syntheses/
```

Two consequences worth naming:

- **The bundle sits at the virtual root.** A tool call addresses a page by its
  bundle path, `/wiki/concepts/foo.md`, with no prefix to prepend and no
  translation layer. Links *inside* the pages are a different matter: they are
  written relative to the page holding them (`../concepts/foo.md`), so that the
  bundle stays navigable outside the agent too — on GitHub, in an editor, after
  a move.
- **`raw/` sits beside `wiki/`, not inside it.** Everything the wiki owns is
  under `wiki/`; the sources are not part of it. That is what makes a source
  two hops up from a category page (`../../raw/...`) and one hop up from
  `wiki/log.md` (`../raw/...`).
- **Nothing is added to your directory.** The agent's instructions are in its
  system prompt, not in files; `ls` on your wiki shows your pages and nothing
  else.

Because `virtual_mode=True`, the backend is confined to its root: the agent
cannot escape the bundle via `../` or `~/`.

### Bringing your own backend

Pass `backend=` instead of `wiki_path=` for a bundle held in a store, a
sandbox, or anywhere that is not a local directory. The factory then uses your
backend verbatim. There is nothing else to mount — the agent's instructions
travel in the prompt, so any backend that serves the bundle at its root works
as-is.

## The permission model

`deepagents` applies `FilesystemPermission` rules inside `FilesystemMiddleware`,
*before* a file tool executes. That makes them a property of the graph rather
than of the conversation: no prompt, system or user, can talk past them.

The manager agent:

```python
FilesystemPermission(operations=["write"], paths=["/raw", "/raw/**"], mode="deny")
```

`raw/` is where source documents live and the format declares them immutable —
better structural than dependent on the model's compliance. Set
`protect_raw=False` to lift it, which leaves the manager with no permission
rules at all.

The reader agent:

```python
FilesystemPermission(operations=["write"], paths=["/", "/**"], mode="deny")
```

Every write, everywhere. Reads are untouched, so `ls`, `read_file`, `glob` and
`grep` all work normally. This is the guarantee that makes it safe to expose the
reader to untrusted questions: a prompt-injected instruction to "update the
wiki" fails at the tool boundary, not at the model's discretion.

Passing your own `permissions` replaces these rules entirely — including, for
the reader, the read-only guarantee.

## Why the instructions are in the prompt

The prompts carry the substance: bundle layout, frontmatter conformance, the
ingest workflow, the query protocol, the lint checklist, bootstrap, the log
format. Earlier versions kept all of that in a mounted `okf-wiki` skill and had
the prompt tell the agent to read it first. That was traded away deliberately.

**A skill can be dropped silently.** When `SKILL.md`'s YAML frontmatter was once
invalid, `deepagents`' `SkillsMiddleware` skipped the skill *without raising*:
the agent started normally, with no instructions at all, and produced
plausible-looking nonconformant output. A system prompt is a string in the
process; it cannot be silently dropped.

**Progressive disclosure bought nothing here.** The instructions are not
optional — they apply to every request either agent handles. So the first tool
call of every session was a `read_file` whose result we already knew we wanted,
and whose omission the model could decide on.

**Removing the mount simplified everything downstream.** The backend collapsed
from a `CompositeBackend` over two `FilesystemBackend`s to a single one, and
`skills_mount` / `skills_dir` / `extra_skills` / `normalize_mount` /
`resources.py` all left the public surface. The `limit=1000` wart — both prompts
had to warn the agent that the default 100-line read truncates the file — went
with them.

What it costs: about 2k tokens on every turn rather than once per session,
largely amortized by prompt caching, and the reason the content is **split by
audience** rather than pasted into both prompts. The manager gets everything;
the reader gets bundle structure, enough conformance to read frontmatter and
follow links, and the query protocol — not ingest, bootstrap or the log format,
which it can never act on.

Customizing behavior now goes through `system_prompt=`, which was already the
documented override.

### `skills/okf-wiki/` still exists

The skill remains in the repository, at the root rather than inside the package,
and is no longer shipped in the wheel. It is the canonical human-facing
statement of the format and stays usable in Claude Code or any other
skill-aware harness.

`tests/test_prompt_drift.py` is what keeps "source of truth" from being merely a
comment: every section of `SKILL.md` must be declared as covered by the manager
prompt, the reader prompt, both, or neither, and a checksum of the file must be
updated deliberately. Editing the skill without revisiting the prompts fails
the suite.

What the prompts own beyond the skill's content:

| Contract | Where it lives | Why |
|---|---|---|
| The not-found sentence | prompt (`not_found_message`) | a per-deployment product decision, not a property of the format |
| No answering from model knowledge | prompt | the skill's query section assumes a cooperative reader; the reader agent needs it as a hard rule |
| Where the bundle root is | prompt (`WIKI_ROOT`) | a property of the mount, not of the format |

## The lint tool

The manager's prompt tells it to validate the bundle before declaring a write
complete. A deep agent has file tools but no shell, so it cannot run a
validator itself.

`create_okf_lint_tool` closes the gap: it wraps
`deep_wiki_agent.okf_lint.lint` — ordinary imported code — as a LangChain tool
bound to one bundle. The same module keeps an `argparse` entry point, so a human
can run `python -m deep_wiki_agent.okf_lint <bundle> [--fix] [--json]` against
the same implementation. It is stdlib-only, so a bundle stays verifiable by
anyone holding the directory.

Two design details:

- **The bundle path is captured in the closure**, not exposed as a tool
  argument, so the model cannot aim the linter (and its `fix=True` writes) at
  an arbitrary directory.
- **The report is capped** at 50 findings per section. A badly broken bundle
  would otherwise flood the context window; the summary counts stay exact.

The tool walks a real directory, so it is attached only when the bundle
resolves to one. For other backends the factory skips it silently *and* omits
its paragraph from the prompt — the agent is never told about a tool it does
not have. The lint *checklist* stays in the prompt either way: the judgment a
script cannot give does not depend on the script being there.

## Module map

| Module | Responsibility |
|---|---|
| `factory.py` | the two public factories; argument validation, assembly |
| `backends.py` | the permission sets and local-root resolution |
| `prompts.py` | the two system prompt templates and the not-found default |
| `okf_lint.py` | the OKF conformance validator, plus its shell entry point |
| `tools/lint.py` | the `okf_lint` tool over that validator |
| `skills/okf-wiki/` | the skill, at the repo root: not shipped, not mounted |
