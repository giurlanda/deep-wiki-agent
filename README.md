# deep-wiki-agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/deep-wiki-agent)](https://pypi.org/project/deep-wiki-agent/)
[![Version](https://img.shields.io/github/v/tag/giurlanda/deep-wiki-agent?sort=semver&label=version)](https://github.com/giurlanda/deep-search-agent/tags)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

LangChain [deepagents](https://github.com/langchain-ai/deepagents) that maintain and consult a wiki knowledge base in the [Open Knowledge Format (OKF)](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing).

Full docs: **https://giurlanda.github.io/deep-wiki-agent/**

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

To ingest PDFs and other binary formats, add the optional `documents` extra — `pip install "deep-wiki-agent[documents]"` — which enables [the ready-made `read_document` tool](#ingesting-pdfs-docx-web-pages). For [semantic search](#semantic-search-over-a-large-bundle) over a large bundle, add `semantic` instead: it includes `documents`. The core install stays free of loader and retrieval dependencies.

## The knowledge lives in the system prompt

The substantive rules — bundle layout, frontmatter conformance, the ingest workflow, the query protocol, the lint checklist, bootstrap, the log format — are in the agents' system prompts. They are in force from the first turn: nothing to load, no round trip spent reading a file, and no way for the model to skip them.

The content is split by audience. The manager carries all of it. The reader carries bundle structure, enough conformance to read frontmatter and follow links, and the query protocol — not ingest, bootstrap or the log format, which it can never act on.

`skills/okf-wiki/SKILL.md` stays in the repository as the canonical human-facing statement of the format, usable in Claude Code and other skill-aware harnesses. It is not shipped in the wheel and no agent reads it; `tests/test_prompt_drift.py` fails if it and the prompts drift apart.

To change what an agent follows, pass `system_prompt=`.

### The virtual filesystem the agent sees

```
/                          -> your OKF bundle (wiki_path)
├── AGENTS.md
├── raw/                   write-protected: sources are immutable
└── wiki/
    ├── index.md
    ├── log.md
    ├── assets/
    ├── documents/
    ├── entities/
    ├── concepts/
    └── syntheses/
```

Everything the wiki owns lives under `wiki/`; `raw/` sits beside it, not inside it, so a page in a category directory reaches its source two hops up (`../../raw/...`).

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

The agent bootstraps the bundle structure (agreeing categories and types with you first), writes `AGENTS.md`, ingests the source, creates and cross-links the pages, updates the indexes and `wiki/log.md`, and runs `okf_lint`.

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

### Getting the answer as data

Matching that sentence to detect a miss is brittle — it breaks the moment you translate it. Pass `structured_output=True` and the reader answers with a `WikiAnswer` instead:

```python
from deep_wiki_agent import WikiAnswer, create_deep_wiki_agent

reader = create_deep_wiki_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    structured_output=True,
)

result = reader.invoke(
    {"messages": [{"role": "user", "content": "What is the notice period?"}]},
    config={"configurable": {"thread_id": "q-1"}},
)

answer: WikiAnswer = result["structured_response"]
if not answer.found:
    ...                      # the bundle covers none of the question
elif answer.not_covered:
    ...                      # partial hit; `not_covered` names the gap
for path in answer.citations:
    ...                      # e.g. "/wiki/concepts/preavviso.md"
```

| Field | Type | Meaning |
|---|---|---|
| `answer` | `str` | the answer; the not-found message, verbatim, when `found` is false |
| `citations` | `list[str]` | bundle paths of the wiki pages the answer rests on |
| `not_covered` | `str \| None` | the part of the question the bundle does not answer |
| `found` | `bool` | false only when the bundle covers none of the question |

The trade-off, and why free text stays the default: a schema buys you a record you can branch on, and costs the model the freedom to shape an answer to the question — a table, a staged explanation, an aside about two pages that disagree. Take it when a program consumes the answer, leave it when a person reads it.

The flag is mutually exclusive with `create_deep_agent`'s `response_format` passthrough; pass your own schema that way instead if `WikiAnswer` does not fit, and note that only `structured_output=True` adds the prompt section telling the model how to fill the fields.

### Ingesting PDFs, docx, web pages

No loader is installed by default — which formats your wiki ingests is domain-specific, and loaders drag in heavy dependencies. The well-trodden path ships as an opt-in extra:

```bash
pip install "deep-wiki-agent[documents]"
# or
uv add "deep-wiki-agent[documents]"
```

That pulls in [markitdown](https://github.com/microsoft/markitdown) and enables `create_read_document_tool`, a ready-made `read_document` tool covering PDF, docx, pptx, xlsx, html, epub and the rest of markitdown's format list:

```python
from deep_wiki_agent import create_read_document_tool, create_wiki_manager_agent

manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    tools=[create_read_document_tool("./my-wiki")],
)
```

The agent then calls `read_document("paper.pdf")` and gets markdown back. Two things are not up to the model: reads are confined to `/raw`, so the tool cannot be aimed at the wiki's own pages or anywhere outside the bundle, and the result is truncated past `max_chars` (200 000 by default) with a note saying so, so one oversized source cannot swallow the context window.

Bytes are pulled through the backend's `download_files`, not off the local filesystem, so the tool reads the same tree the agent's file tools do — including a bundle held in a state, store, or sandbox backend:

```python
tool = create_read_document_tool(backend=my_backend)
```

To convert a source yourself, outside an agent, `read_document` is the same code path without the truncation:

```python
from deep_wiki_agent import read_document

markdown = read_document("paper.pdf", wiki_path="./my-wiki")
```

Writing your own loader instead remains supported — anything you pass in `tools=` is handed straight to the agent.

### Semantic search over a large bundle

The indexes and the link graph are how both agents navigate, and up to a few hundred pages that is enough — it is [the whole point of the format](#why-a-wiki-instead-of-rag). Past that it degrades: category indexes get long, and a page nobody linked from the right place stops being findable. The `semantic` extra adds the other way in, without changing what a bundle is.

```bash
pip install "deep-wiki-agent[semantic]"
# or
uv add "deep-wiki-agent[semantic]"
```

Hand both factories an embedding model and a vector store, and the tools appear along with the prompt section that says how to use them:

```python
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode

from deep_wiki_agent import create_wiki_manager_agent

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
store = QdrantVectorStore.from_existing_collection(
    collection_name="my-wiki",
    url="http://localhost:6333",
    embedding=embeddings,
    sparse_embedding=FastEmbedSparse(model_name="Qdrant/bm25"),
    retrieval_mode=RetrievalMode.HYBRID,   # dense + BM25 keyword
    vector_name="dense",
    sparse_vector_name="sparse",
)

manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    embeddings=embeddings,
    vector_store=store,
)
```

No vector store is pinned by the extra: any LangChain `VectorStore` works, and the one above is an example, not a requirement. A store configured for hybrid retrieval keeps its keyword half — the query reaches it as text rather than as a vector, which is what would otherwise disable BM25 silently.

The two agents get different halves, and this is not a matter of prompting:

| | `semantic_ingest` | `semantic_search` |
|---|---|---|
| `create_wiki_manager_agent` | yes | yes |
| `create_deep_wiki_agent` | **no** | yes |

The reader is read-only over the bundle, so it never gets the tool that writes. Its prompt section says the rest: a hit is an entry point, not an answer — open the page, read it in full, cite the page and not the excerpt, and never conclude "not found" from an empty search alone.

Ingestion reads the wiki's pages and the sources under `/raw` (converted through markitdown, so PDF, docx, csv and the rest are covered), through the backend rather than off the local filesystem. Re-indexing is cheap and safe to repeat: unchanged files are skipped, a rewritten page updates its chunks instead of duplicating them, and the chunks of a page that shrank or was deleted are removed. A small manifest under `.okf/` is what makes that possible; the index itself is derived data you can delete and rebuild at any time.

To build or refresh the index outside an agent — a cron job, a post-commit hook, a deploy step — the same code path is a function:

```python
from deep_wiki_agent import ingest_semantic_index

report = ingest_semantic_index(embeddings, store, wiki_path="./my-wiki")
print(report.summary())
```

`SemanticConfig` holds everything the model does not get to choose: chunk sizes, which directories may be indexed, batch sizes, an optional server-side `filter_builder` for your store's native filters, and where the manifest lives.

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

The `okf_lint` tool is attached regardless of backend: it walks the bundle through the backend's own `glob`/`read`/`edit` methods, so a state, store, or sandbox-backed bundle is just as self-validating as a local directory.

## Linting

`okf_lint` validates the bundle against OKF v0.1 — frontmatter present, the mandatory `type` field, ISO 8601 timestamps, broken internal links, links written as absolute paths instead of relative to their page, the same two defects in the path-valued frontmatter fields `resource` and `sources`, orphan pages, stale or missing `index.md`, `type` values not declared in `AGENTS.md`, the `log.md` entry format, the same concept created twice under different paths, misused reserved names.

`--fix` repairs what is mechanical and leaves the rest reported: malformed timestamps (keeping the date they state) and absolute links or frontmatter paths whose target exists, rewritten relative to their page. An absolute link whose target does not exist is left alone — rewriting it would only move a broken link around.

The manager agent runs it as a tool. You can run the same check yourself:

```python
from deep_wiki_agent import run_okf_lint

report = run_okf_lint("./my-wiki")
print(report["errors"], report["warnings"])
```

or from the shell — the validator is stdlib-only and exits `1` on errors, so it drops straight into CI:

```bash
okf-lint ./my-wiki [--fix] [--json]

# without the package installed on the PATH
uvx --from deep-wiki-agent okf-lint ./my-wiki
python -m deep_wiki_agent.okf_lint ./my-wiki
```

For a bundle held in a non-local backend, pass it instead of a path:

```python
report = run_okf_lint(backend=my_backend)
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
- `create_deep_wiki_agent(*, model, wiki_path=None, backend=None, not_found_message=..., structured_output=False, ...)` — read-only agent that answers only from the bundle.
- `WikiAnswer` — the reader's optional structured response: `answer`, `citations`, `not_covered`, `found`.
- `create_okf_lint_tool(wiki_path=None, *, backend=None)` / `run_okf_lint(wiki_path=None, *, backend=None, fix=False)` — OKF conformance validation, against a local directory or any deepagents backend.
- `create_read_document_tool(wiki_path=None, *, backend=None, root="/raw", max_chars=200_000)` / `read_document(path, *, wiki_path=None, backend=None, root="/raw")` — source-document loading via markitdown, confined to `/raw`. Requires the `documents` extra.
- `create_semantic_tools(embeddings, vector_store, *, wiki_path=None, backend=None, search_k=5, config=None)` / `ingest_semantic_index(...)` — the `semantic_ingest` and `semantic_search` tools, and the ingestion function behind them. Requires the `semantic` extra.
- `SemanticConfig` / `ChunkingConfig` / `IngestReport` — index configuration and what one ingest did.
- `read_only_permissions()` / `write_protect_permissions(paths)` — the permission sets, reusable in your own agents.
- `MANAGER_SYSTEM_PROMPT_TEMPLATE` / `READER_SYSTEM_PROMPT_TEMPLATE` — the instructions each agent follows, as `str.format` templates.
- `BUNDLE_SKELETON` — the bundle layout the manager bootstraps, as bundle-relative paths.

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
