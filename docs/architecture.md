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

`"write"` covers every mutating tool, not just `write_file` — `deepagents` maps
`edit_file` and `delete` onto the same operation, so the rule above needs no
amendment as that tool set grows.

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
| The answer's shape | prompt + schema (`structured_output`) | how a caller consumes an answer is an integration concern; a human-facing skill has no equivalent |

### Why the answer's shape is opt-in

`structured_output=True` is the one place where a contract is stated twice: as
`WikiAnswer`, which the model must satisfy, and as a prompt section explaining
how its fields relate to the not-found rule. The schema alone would be filled
inconsistently — nothing in `list[str]` says "wiki pages, not sources under
`raw/`", and nothing in `bool` says a partial hit is `found: true`.

It stays off by default because the two representations are not
interchangeable. Prose lets the reader answer as the question deserves; fields
let a caller branch without parsing. The library cannot pick for a deployment
it cannot see, so it exposes both and makes the choice one keyword wide.

## The lint tool

The manager's prompt tells it to validate the bundle before declaring a write
complete. A deep agent has file tools but no shell, so it cannot run a
validator itself.

`create_okf_lint_tool` closes the gap: it wraps
`deep_wiki_agent.okf_lint.lint` — ordinary imported code — as a LangChain tool
bound to one bundle. The same module keeps an `argparse` entry point, installed
as the `okf-lint` console script (and still reachable as `python -m
deep_wiki_agent.okf_lint`), so a human can run `okf-lint <bundle> [--fix]
[--json]` against the same implementation. It is stdlib-only, so a bundle stays
verifiable by anyone holding the directory.

`lint` itself walks the bundle through a small `list_pages`/`read`/`exists`/
`edit` interface rather than `Path` directly. A local directory is wrapped in
it automatically; `tools/lint.py` additionally adapts a deepagents
`BackendProtocol` (state, store, sandbox) to the same interface over its own
`glob`/`read`/`edit`. `okf_lint.py` itself never imports `deepagents`, so the
shell entry point still needs nothing beyond the standard library.

Two more design details:

- **The bundle (path or backend) is captured in the closure**, not exposed as
  a tool argument, so the model cannot aim the linter (and its `fix=True`
  writes) at an arbitrary location.
- **The report is capped** at 50 findings per section. A badly broken bundle
  would otherwise flood the context window; the summary counts stay exact.

Because the abstraction covers every backend, the factory attaches the tool
unconditionally whenever `enable_lint_tool=True` — there is no longer a
backend type the linter cannot reach.

## The document tool, and why it is optional

Not shipping loaders is the right default — which formats a wiki ingests is
domain-specific, and a mandatory PDF stack is dead weight for a bundle built
from plain text. But nearly every bundle's `raw/` directory holds PDFs, and
every user was writing the same adapter. `tools/documents.py` is that adapter,
behind the `documents` extra, so the cost is paid only by those who opt in.

The extra installs [markitdown](https://github.com/microsoft/markitdown), which
covers PDF, docx, pptx, xlsx, html and epub through one interface — hence
`read_document` rather than `read_pdf`. `markitdown` is imported *inside* the
converter, not at module scope, so importing `deep_wiki_agent` stays free for
everyone else and a missing extra surfaces as an `ImportError` carrying the
install command rather than as a crash at import time.

Three design details, two of them borrowed from the lint tool:

- **Bytes come through the backend**, via `download_files` rather than `read`.
  `read` returns decoded text, which a PDF does not survive; `download_files`
  returns raw bytes on every backend, so the tool reads the same tree the
  agent's file tools do — including a state, store, or sandbox-backed bundle —
  instead of reaching around it to the local filesystem.
- **The bundle is captured in the closure and reads are confined to `/raw`.**
  The model chooses which source to open and nothing else; path resolution is
  lexical (`..` is collapsed, then the result is checked against the root), so
  traversal cannot walk out of the source directory or into the wiki's pages.
- **Output is capped** at `max_chars`, with the truncation announced in the
  returned text so the model knows it is reading a prefix.

The tool is not attached by any factory: it is passed in `tools=`, like any
other loader, since its dependency is not part of a default install.

## The semantic index, and why it stays outside the bundle

The format's claim is that at moderate scale a hand-written index beats
embedding retrieval, and it holds — up to a point. Past a few hundred pages the
category indexes get long and a page linked from the wrong place stops being
reachable. The `semantic` extra adds a second entry point, under one
constraint: **a bundle must not acquire a database**.

So the vector store is the caller's, not the library's. Nothing is pinned, and
nothing about the index is authoritative: it holds no fact the bundle does not,
and deleting it costs a re-ingest and nothing else. What stays in the bundle is
one small JSON manifest under `.okf/` — invisible to `okf_lint`, which walks
`**/*.md` — recording each file's digest and the ids its chunks were stored
under. That is what makes a second ingest cheap and, more importantly,
*correct*: it is how a rewritten page updates instead of duplicating, and how
the chunks of a page that shrank or was deleted are actually removed. An index
that only ever grows eventually answers questions with pages that no longer
exist.

The rest follows the shape of the other two tools:

- **Everything is read through the backend.** Markdown through `read`, other
  formats through `download_files` and the same `markitdown` conversion
  `read_document` performs — reused by import, not reimplemented. The index
  therefore covers exactly the tree the agent's own file tools see.
- **The bundle, the store and the model are captured in the closure**;
  ingestion is confined to `ingest_roots` (`/wiki` and `/raw` by default). The
  model chooses what to index and what to search for, never where to read from
  or where to write to.
- **The two agents get different halves.** The manager, which changes the
  bundle, gets `semantic_ingest` and `semantic_search`; the reader gets search
  alone. This one is not a prompt convention: the filesystem permissions police
  the file tools, not a tool that talks to a vector store, so a read-only agent
  stays read-only by not being handed the tool that writes.
- **Both operations are plain Python too.** `ingest_semantic_index` is the same
  code path without an agent, because an index that only refreshes when a model
  decides to call a tool is not an index you can depend on.

Chunking lives in its own module and touches none of the above: header-aware
splitting, GFM tables lifted out as atomic chunks (a table cut by a
character-count splitter loses its header row, and with it the meaning of its
numbers), and the page path, section hierarchy and content type recorded in
each chunk's metadata. That metadata is what lets an answer cite a page rather
than an excerpt — if it is not attached at chunking time, no retrieval quality
puts it back later.

## Module map

| Module | Responsibility |
|---|---|
| `factory.py` | the two public factories; argument validation, assembly |
| `backends.py` | the permission sets |
| `prompts.py` | the two system prompt templates and the not-found default |
| `okf_lint.py` | the OKF conformance validator (backend-agnostic), plus its shell entry point |
| `tools/lint.py` | the `okf_lint` tool and its deepagents backend adapter |
| `tools/documents.py` | the optional `read_document` tool: markitdown conversion over backend bytes |
| `_paths.py` | virtual-path normalization and confinement, shared by the tools that read through a backend |
| `semantic/chunking.py` | header-aware markdown chunking, tables as atomic chunks, provenance metadata |
| `semantic/index.py` | ingestion, the incremental manifest, and hybrid-aware search |
| `semantic/tools.py` | the optional `semantic_ingest` / `semantic_search` tools and their factory |
| `skills/okf-wiki/` | the skill, at the repo root: not shipped, not mounted |
