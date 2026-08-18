# Getting started

## Install

```bash
pip install deep-wiki-agent
# or
uv add deep-wiki-agent
```

Python 3.12+. You also need a provider package for the model you pass:

```bash
pip install langchain-anthropic
export ANTHROPIC_API_KEY=...
```

To ingest PDFs and other binary formats, add the optional `documents` extra —
see [Ingest PDFs, docx, web pages](#ingest-pdfs-docx-web-pages). For semantic
search over a large bundle add `semantic` instead, which includes `documents` —
see [Search a large bundle by meaning](#search-a-large-bundle-by-meaning). The
core install stays free of loader and retrieval dependencies.

## Build a wiki

Put your source documents in `raw/` first. They are write-protected: the agent
reads them and can never alter them.

```bash
mkdir -p my-wiki/raw
cp ~/Documents/contract-acme-2026.md my-wiki/raw/
```

```python
from deep_wiki_agent import create_wiki_manager_agent

manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
)

result = manager.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Bootstrap a wiki for our supplier contracts, then ingest "
                    "raw/contract-acme-2026.md."
                ),
            }
        ]
    },
    config={"configurable": {"thread_id": "session-1"}},
)
print(result["messages"][-1].content)
```

On a first run the agent bootstraps: it agrees the domain, the categories, the
page types and the language with you, writes them into `AGENTS.md`, and
initializes `wiki/index.md` and `wiki/log.md`. Then it ingests — reading the source in
full, creating the document page with position-level citations, extracting
entities and concepts, cross-linking them, updating the affected indexes and
the log, and finally running `okf_lint`.

Use one `thread_id` per working session so the agent keeps its context across
turns.

!!! tip "Supervise the first ingests"
    The agent discusses takeaways with you before writing, unless you ask for
    an unsupervised batch. One confirmation round here prevents a
    misunderstanding from propagating into a dozen pages.

## Query a wiki

```python
from deep_wiki_agent import create_deep_wiki_agent

reader = create_deep_wiki_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
)

answer = reader.invoke(
    {"messages": [{"role": "user", "content": "What is the notice period?"}]},
    config={"configurable": {"thread_id": "q-1"}},
)
print(answer["messages"][-1].content)
```

If the bundle covers the question you get an answer citing wiki pages, which in
turn cite the position in the source. If it does not, you get exactly:

> I could not find the requested information in the wiki knowledge base.

Change that sentence with `not_found_message`:

```python
reader = create_deep_wiki_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    not_found_message="Non ho trovato queste informazioni nella knowledge base.",
)
```

The surrounding contract does not change: no guessing, no outside knowledge,
and a partially covered question is answered in part with the gap named.

## Consume the answer from code

String-matching the not-found sentence to detect a miss breaks as soon as you
reword or translate it. `structured_output=True` returns a
[`WikiAnswer`](api.md#deep_wiki_agent.schemas.WikiAnswer) under
`result["structured_response"]` instead:

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
    raise LookupError(answer.answer)
print(answer.answer)
print("sources:", answer.citations)
if answer.not_covered:
    print("gap:", answer.not_covered)
```

The four fields are the prose contract made checkable: `found` is false only
when the bundle covers none of the question, `not_covered` names the gap in a
partial hit, and `citations` holds bundle paths such as
`/wiki/concepts/preavviso.md`.

!!! note "Why free text is still the default"
    A schema costs the model the freedom to lay an answer out as the question
    deserves — a table, a staged explanation, an aside about two pages that
    disagree. Turn it on when a program consumes the answer; leave it off when
    a person reads it.

    The flag also sets `response_format` on the underlying deep agent, so it
    cannot be combined with passing `response_format` yourself. Do that instead
    if `WikiAnswer` does not fit your caller — but note that only
    `structured_output=True` adds the prompt section that tells the model how
    the fields relate to the not-found contract.

## Ingest PDFs, docx, web pages

No loader is installed by default: which formats your wiki ingests is
domain-specific, and loaders drag in heavy dependencies. The common case ships
as an opt-in extra.

```bash
pip install "deep-wiki-agent[documents]"
# or
uv add "deep-wiki-agent[documents]"
```

That pulls in [markitdown](https://github.com/microsoft/markitdown) and enables
`create_read_document_tool` — a ready-made `read_document` tool covering PDF,
docx, pptx, xlsx, html, epub and the rest of markitdown's format list.

```python
from deep_wiki_agent import create_read_document_tool, create_wiki_manager_agent

manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    tools=[create_read_document_tool("./my-wiki")],
)
```

The agent calls `read_document("paper.pdf")` and gets markdown back. It picks
the document; it does not pick anything else:

- **Reads are confined to `/raw`.** `paper.pdf`, `raw/paper.pdf` and
  `/raw/paper.pdf` all name the same file, while anything resolving outside —
  including via `../` — comes back as an error. The tool cannot be aimed at the
  wiki's own pages, which the agent reads with its ordinary file tools anyway.
- **Output is capped** at `max_chars` (200 000 by default), with a note
  appended when it truncates, so one oversized source cannot swallow the
  context window.

Bytes are pulled through the backend's `download_files` rather than off the
local filesystem, so a bundle held in a state, store, or sandbox backend works
the same way:

```python
tool = create_read_document_tool(backend=my_backend)
```

To convert a source outside an agent, `read_document` is the same code path
without the truncation:

```python
from deep_wiki_agent import read_document

markdown = read_document("paper.pdf", wiki_path="./my-wiki")
```

!!! note
    Writing your own loader is still supported and sometimes the right call —
    a domain-specific parser, an OCR pass, a URL fetcher. Anything you pass in
    `tools=` is handed straight to the agent, alongside or instead of
    `read_document`.

## Search a large bundle by meaning

The indexes and the link graph are the primary route through a bundle, and at
moderate scale they replace embedding retrieval outright. Past a few hundred
pages that stops being true: the category indexes get long, and a page nobody
linked from the right place stops being findable. The `semantic` extra adds a
second entry point without changing the format — the index lives outside the
bundle and is rebuildable from it at any time.

```bash
pip install "deep-wiki-agent[semantic]"
```

Give either factory an embedding model and a vector store, and the tools appear
together with the prompt section explaining them:

```python
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode

from deep_wiki_agent import create_deep_wiki_agent, create_wiki_manager_agent

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
reader = create_deep_wiki_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    embeddings=embeddings,
    vector_store=store,
    search_k=8,
)
```

Any LangChain `VectorStore` works; the extra pins none. A store in hybrid mode
keeps its keyword half — the query is handed to it as text rather than as a
vector, which is what would otherwise turn BM25 off without saying so.

The manager gets `semantic_ingest` and `semantic_search`; the reader gets only
`semantic_search`. That asymmetry is structural, not a matter of prompting: the
ingestion tool writes, and the reader is read-only over the bundle by
construction.

!!! warning
    A hit is an entry point, not an answer. The reader's prompt section says so
    — open the page a hit points at, read it in full, and cite the page rather
    than the excerpt. A chunk can be current, superseded, or one half of a
    contradiction, and only the page tells you which.

Ingestion covers the wiki's pages and the sources under `raw/` (converted
through markitdown, so PDF, docx, csv and the rest are included), reading
through the backend rather than the local filesystem. Running it again is
cheap: unchanged files are skipped, a rewritten page updates its chunks instead
of duplicating them, and the chunks of a page that shrank or was deleted are
removed. A small manifest under `.okf/` records what was indexed.

To build or refresh the index without an agent — a cron job, a post-commit
hook, a deployment step:

```python
from deep_wiki_agent import ingest_semantic_index

report = ingest_semantic_index(embeddings, store, wiki_path="./my-wiki")
print(report.summary())
```

`SemanticConfig` carries everything the model does not get to choose: chunk
sizes, which directories may be indexed, batch sizes, an optional
`filter_builder` for your store's native server-side filters, and where the
manifest lives.

## Require approval before writes

```python
from langgraph.checkpoint.memory import MemorySaver

manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    interrupt_on={"write_file": True, "edit_file": True},
    checkpointer=MemorySaver(),
)
```

Interrupts require a checkpointer — without one the interrupt has nowhere to
suspend to.

## Lint a bundle

The manager agent runs `okf_lint` itself. To run it yourself:

```python
from deep_wiki_agent import run_okf_lint

report = run_okf_lint("./my-wiki")
for error in report["errors"]:
    print(error["file"], error["msg"])
```

Pass `fix=True` to normalize malformed timestamps in place. The date a
timestamp states is preserved whenever it can be parsed at all (`2026/07/19`,
`19-07-2026`, `Jul 19, 2026`, …); the current time is used only when nothing
parses, and the fix is then reported as `timestamp unparseable: ... (original
date lost)`. For a bundle held
in a non-local backend (state, store, sandbox), pass it instead of a path:
`run_okf_lint(backend=my_backend)`.

The same validator is stdlib-only and runnable from a shell:

```bash
python -m deep_wiki_agent.okf_lint ./my-wiki          # report
python -m deep_wiki_agent.okf_lint ./my-wiki --fix    # normalize timestamps
python -m deep_wiki_agent.okf_lint ./my-wiki --json   # machine-readable
```

It exits `1` when the bundle has errors, so it drops straight into CI or a
pre-commit hook.

## Maintain the wiki over time

Linting is not only a conformance check — it is the point where the wiki tells
you what it needs. Ask the manager agent periodically:

```python
manager.invoke(
    {"messages": [{"role": "user", "content": "Lint the wiki and tell me what is missing."}]},
    config={"configurable": {"thread_id": "maintenance-1"}},
)
```

Beyond the mechanical checks it reports contradictions between pages, claims
superseded by newer sources, orphan pages, concepts cited repeatedly without a
page of their own, and the gaps that block the questions you ask most.
