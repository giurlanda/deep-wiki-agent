# deep-wiki-agent

LangChain [deepagents](https://github.com/langchain-ai/deepagents) that maintain and consult a wiki knowledge base in the [Open Knowledge Format (OKF)](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing).

An OKF bundle is a directory of markdown pages with YAML frontmatter, linked to each other into a graph. It is a *format*, not a platform: no database, no embeddings, no vendor. This library gives you two agents over such a bundle — one that writes it, one that reads it.

```python
from deep_wiki_agent import create_deep_wiki_agent, create_wiki_manager_agent

# Writes: ingests documents, keeps pages, links, indexes and log in sync.
manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
)

# Reads: answers strictly from the bundle, or says it found nothing.
reader = create_deep_wiki_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
)
```

## Why two agents

The two jobs pull in opposite directions, and the difference is enforced, not merely suggested:

| | `create_wiki_manager_agent` | `create_deep_wiki_agent` |
|---|---|---|
| Writes to the bundle | yes | **never** — every write is denied at the tool boundary |
| `raw/` (source documents) | write-protected | unreachable for writing |
| Creates the bundle | yes, on demand | no, must already exist |
| Extra tools | `okf_lint` + whatever you pass | none by default |
| Answer when the wiki is silent | ask the user, propose sources | a fixed *not found* sentence |

The reader's read-only guarantee comes from `deepagents`' `FilesystemPermission` rules, applied by `FilesystemMiddleware` before a tool runs. Replacing its system prompt — even with one that tells it to write — does not lift it.

## Installation

```bash
pip install deep-wiki-agent
# or
uv add deep-wiki-agent
```

Python 3.12+. You also need a provider package for the model you pass, e.g. `pip install langchain-anthropic`.

## The knowledge lives in the system prompt

The substantive rules — bundle layout, frontmatter conformance, the ingest workflow, the query protocol, the lint checklist, bootstrap, the log format — are in the agents' system prompts. They are in force from the first turn: nothing to load, no round trip spent reading a file, and no way for the model to skip them.

The content is split by audience. The manager carries all of it. The reader carries bundle structure, enough conformance to read frontmatter and follow links, and the query protocol — not ingest, bootstrap or the log format, which it can never act on.

`skills/okf-wiki/SKILL.md` stays in the repository as the canonical human-facing statement of the format, usable in Claude Code and other skill-aware harnesses. It is not shipped in the wheel and no agent reads it; `tests/test_prompt_drift.py` fails if it and the prompts drift apart.

To change what an agent follows, pass `system_prompt=`.

### The virtual filesystem the agent sees

```
/                          -> your OKF bundle (wiki_path)
├── AGENTS.md
├── index.md
├── log.md
├── raw/                   write-protected: sources are immutable
├── documents/
├── entities/
├── concepts/
└── syntheses/
```

One `FilesystemBackend`, rooted at your bundle, with `virtual_mode=True` so the agent cannot escape it via `../` or `~/`. Nothing is added to your wiki directory.

## Usage

### Building a wiki

```python
manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
)

config = {"configurable": {"thread_id": "session-1"}}
result = manager.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Bootstrap a wiki about our vendor contracts, "
                    "then ingest raw/contract-acme-2026.md"
                ),
            }
        ]
    },
    config=config,
)
print(result["messages"][-1].content)
```

The agent bootstraps the bundle structure (agreeing categories and types with you first), writes `AGENTS.md`, ingests the source, creates and cross-links the pages, updates the indexes and `log.md`, and runs `okf_lint`.

Put your source documents in `<wiki_path>/raw/` beforehand. They are write-protected, so the agent reads them and can never alter them.

### Consulting a wiki

```python
reader = create_deep_wiki_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
)

answer = reader.invoke(
    {"messages": [{"role": "user", "content": "What is the notice period in the Acme contract?"}]},
    config={"configurable": {"thread_id": "q-1"}},
)
```

If the bundle covers the question, you get an answer citing the wiki pages, which in turn cite the position in the source document. If it does not, you get exactly:

> I could not find the requested information in the wiki knowledge base.

Not a guess, not the model's general knowledge, not an answer to a nearby question. A *partial* hit is answered in part, with the uncovered part named explicitly.

Change the sentence — for another language or another product voice — with `not_found_message`:

```python
reader = create_deep_wiki_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    not_found_message="Non ho trovato queste informazioni nella knowledge base.",
)
```

### Ingesting PDFs, docx, web pages

The library ships no document loaders on purpose: which formats your wiki ingests is domain-specific, and loaders drag in heavy dependencies. Pass your own as tools:

```python
from langchain_core.tools import tool

@tool
def read_pdf(path: str) -> str:
    """Extract the text of a PDF stored under /raw."""
    ...

manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    tools=[read_pdf],
)
```

### Human approval before writes

Both factories forward every remaining `create_deep_agent` argument, so the usual deepagents controls apply:

```python
from langgraph.checkpoint.memory import MemorySaver

manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    interrupt_on={"write_file": True, "edit_file": True},
    checkpointer=MemorySaver(),  # interrupts require a checkpointer
)
```

### A bundle that is not on the local disk

Pass your own `backend` instead of `wiki_path`. It is used verbatim, and there is nothing else to mount — the agent's instructions travel in the prompt, so any backend serving the bundle at its root works as-is:

```python
from deepagents.backends import StoreBackend

reader = create_deep_wiki_agent(
    model="anthropic:claude-sonnet-5",
    backend=lambda rt: StoreBackend(rt),
)
```

The `okf_lint` tool walks a real directory, so it is attached only when the bundle resolves to one; for other backends the factory skips it silently and the prompt does not mention it.

## Linting

`okf_lint` validates the bundle against OKF v0.1 — frontmatter present, the mandatory `type` field, ISO 8601 timestamps, broken internal links, links written as absolute paths instead of relative to their page, orphan pages, stale or missing `index.md`, misused reserved names.

The manager agent runs it as a tool. You can run the same check yourself:

```python
from deep_wiki_agent import run_okf_lint

report = run_okf_lint("./my-wiki")
print(report["errors"], report["warnings"])
```

or from the shell — the validator is stdlib-only and exits `1` on errors, so it drops straight into CI:

```bash
python -m deep_wiki_agent.okf_lint ./my-wiki [--fix] [--json]
```

## Customizing the instructions

Start from the shipped template and extend it, rather than writing one from scratch:

```python
from deep_wiki_agent import MANAGER_SYSTEM_PROMPT_TEMPLATE
from deep_wiki_agent.prompts import LINT_TOOL_BLOCK

prompt = MANAGER_SYSTEM_PROMPT_TEMPLATE.format(
    wiki_root="/", raw_dir="/raw", lint_block=LINT_TOOL_BLOCK
) + "\n\nAlways write the pages in Italian."

manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    system_prompt=prompt,
)
```

Passing `system_prompt` replaces the built-in instructions wholesale, so restate whatever you still want in force. For the reader that includes the not-found contract and the query protocol — the read-only *enforcement*, by contrast, lives in the permissions and survives any prompt.

To add genuine skills alongside the agent, `create_deep_agent`'s own `skills=` parameter passes straight through.

## API

- `create_wiki_manager_agent(*, model, wiki_path=None, backend=None, ...)` — read/write agent over an OKF bundle.
- `create_deep_wiki_agent(*, model, wiki_path=None, backend=None, not_found_message=..., ...)` — read-only agent that answers only from the bundle.
- `create_okf_lint_tool(wiki_path)` / `run_okf_lint(wiki_path, *, fix=False)` — OKF conformance validation.
- `read_only_permissions()` / `write_protect_permissions(paths)` — the permission sets, reusable in your own agents.
- `MANAGER_SYSTEM_PROMPT_TEMPLATE` / `READER_SYSTEM_PROMPT_TEMPLATE` — the instructions each agent follows, as `str.format` templates.

Upgrading from 0.1.x? `build_wiki_backend`, `bundled_skills_dir`, `okf_wiki_skill_dir`, `normalize_mount`, and the `skills_mount` / `skills_dir` / `extra_skills` parameters are gone — see the [migration table](https://giurlanda.github.io/deep-wiki-agent/api/#migrating-from-01x).

Full reference: <https://giurlanda.github.io/deep-wiki-agent/>

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mkdocs serve
```

## License

MIT
