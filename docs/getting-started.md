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

## Ingest PDFs, docx, web pages

The library ships no document loaders: which formats your wiki ingests is
domain-specific, and loaders drag in heavy dependencies. Pass your own.

```python
from langchain_core.tools import tool
from pypdf import PdfReader


@tool
def read_pdf(path: str) -> str:
    """Extract the text of a PDF stored under /raw.

    Args:
        path: Path of the PDF relative to the bundle root.
    """
    reader = PdfReader(f"./my-wiki/{path.lstrip('/')}")
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
    tools=[read_pdf],
)
```

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

Pass `fix=True` to normalize malformed timestamps in place. For a bundle held
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
