# Architecture

Both factories are thin wrappers over `deepagents.create_deep_agent`. What they
add is the wiring that makes an OKF bundle usable by an agent. This page
explains each piece and why it is shaped that way.

## The mount

An agent needs to see two unrelated trees at once: the wiki bundle (which lives
wherever the user keeps it) and the `okf-wiki` skill (which lives inside the
installed package). `build_wiki_backend` joins them with a `CompositeBackend`:

```python
CompositeBackend(
    default=FilesystemBackend(root_dir=wiki_path, virtual_mode=True),
    routes={"/skills/": FilesystemBackend(root_dir=<pkg>/skills, virtual_mode=True)},
)
```

What the agent sees:

```
/                          -> your OKF bundle
├── index.md
├── log.md
├── raw/
└── concepts/ ...
/skills/                   -> the package's skills directory
└── okf-wiki/
    ├── SKILL.md
    ├── references/okf-spec-notes.md
    └── scripts/okf_lint.py
```

Two consequences worth naming:

- **The bundle sits at the virtual root.** The paths the agent reads and writes
  are exactly the bundle-relative paths OKF prescribes, so `/concepts/foo.md`
  in a tool call and `/concepts/foo.md` in a markdown link are the same string.
  No translation layer, nothing for the model to get wrong.
- **Nothing is copied into your directory.** The mount is virtual. `ls` on your
  wiki shows no `skills/` and no `.skills/`; the skill is served from the
  installed package and updates when you upgrade the library.

`CompositeBackend` strips the route prefix before delegating, so the skills
backend is rooted at the skills directory and a read of
`/skills/okf-wiki/SKILL.md` becomes a read of `okf-wiki/SKILL.md` there.

Because `virtual_mode=True`, each backend is confined to its own root: the
agent cannot escape the bundle via `../` or `~/`.

### Bringing your own backend

Pass `backend=` instead of `wiki_path=` for a bundle held in a store, a
sandbox, or anywhere that is not a local directory. The factory then uses your
backend verbatim and mounts nothing — including the skill, which you must place
at `skills_mount` yourself, or the agent has no instructions to load.

## The permission model

`deepagents` applies `FilesystemPermission` rules inside `FilesystemMiddleware`,
*before* a file tool executes. That makes them a property of the graph rather
than of the conversation: no prompt, system or user, can talk past them.

The manager agent:

```python
FilesystemPermission(
    operations=["write"],
    paths=["/skills", "/skills/**", "/raw", "/raw/**"],
    mode="deny",
)
```

`raw/` is where source documents live and the format declares them immutable —
better structural than dependent on the model's compliance. The skills mount is
denied so an agent cannot rewrite its own instructions. Set `protect_raw=False`
to lift the first; the skills protection is unconditional.

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

## Why the prompts are thin

The system prompts state purpose, point at the skill, and pin down the few
contracts the skill does not cover. They do not restate the bundle layout, the
frontmatter fields, or the ingest workflow.

That is deliberate. Those rules already exist in `SKILL.md`, and duplicating
them into a prompt creates two sources of truth that drift the first time
either changes. The manager prompt says as much to the agent: *if what you
remember and what the skill says disagree, the skill wins.*

The practical payoff is that customizing behavior does not require a code
change. Point `skills_dir` at your own tree — with an `okf-wiki/` directory in
it, since the prompts reference that path — and both agents follow your rules.

What the prompts *do* own:

| Contract | Where it lives | Why not the skill |
|---|---|---|
| Load `SKILL.md` with `limit=1000` | prompt | the default 100-line read truncates the file, and the agent cannot learn this from a file it has not read |
| The not-found sentence | prompt (`not_found_message`) | it is a per-deployment product decision, not a property of the format |
| No answering from model knowledge | prompt | the skill's query section assumes a cooperative reader; the reader agent needs it as a hard rule |
| Where the bundle root is | prompt | depends on the mount, not on the format |

## The lint tool

The skill instructs the agent to run `scripts/okf_lint.py` before declaring a
write complete. A deep agent has file tools but no shell, so it cannot execute
that script.

`create_okf_lint_tool` closes the gap: it loads the very same script as a Python
module and wraps its `lint()` function as a LangChain tool bound to one bundle.
The script stays the single implementation — self-contained, dependency-free,
and usable outside this library — while the tool cannot drift from it.

Two design details:

- **The bundle path is captured in the closure**, not exposed as a tool
  argument, so the model cannot aim the linter (and its `fix=True` writes) at
  an arbitrary directory.
- **The report is capped** at 50 findings per section. A badly broken bundle
  would otherwise flood the context window; the summary counts stay exact.

The tool walks a real directory, so it is attached only when the bundle
resolves to one. For other backends the factory skips it silently *and* omits
its paragraph from the prompt — the agent is never told about a tool it does
not have.

## Module map

| Module | Responsibility |
|---|---|
| `factory.py` | the two public factories; argument validation, assembly |
| `backends.py` | the composite mount, mount normalization, permission sets |
| `prompts.py` | the two system prompt templates and the not-found default |
| `resources.py` | locating the packaged skill |
| `tools/lint.py` | the `okf_lint` tool over the skill's validator script |
| `skills/okf-wiki/` | the skill: instructions, OKF reference notes, validator |
