# deep-wiki-agent

LangChain [deepagents](https://github.com/langchain-ai/deepagents) that maintain and consult a wiki knowledge base in the [Open Knowledge Format (OKF)](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing).

An OKF bundle is a directory of markdown pages with YAML frontmatter, linked to each other into a graph. It is a *format*, not a platform: no database, no embeddings, no vendor lock-in. This library gives you two agents over such a bundle — one that writes it, one that reads it.

```python
from deep_wiki_agent import create_deep_wiki_agent, create_wiki_manager_agent

manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
)

reader = create_deep_wiki_agent(
    model="anthropic:claude-sonnet-5",
    wiki_path="./my-wiki",
)
```

## Two agents, opposite guarantees

| | `create_wiki_manager_agent` | `create_deep_wiki_agent` |
|---|---|---|
| Writes to the bundle | yes | **never** — denied at the tool boundary |
| `raw/` (source documents) | write-protected | unreachable for writing |
| Creates the bundle | yes, on demand | no, must already exist |
| Extra tools | `okf_lint` + whatever you pass | none by default |
| When the wiki is silent | asks the user, proposes sources | a fixed *not found* sentence |

The read-only guarantee is structural: it comes from `deepagents`' `FilesystemPermission` rules, applied before a tool runs. Replacing the reader's system prompt — even with one that tells it to write — does not lift it.

## Why a wiki instead of RAG

The `okf-wiki` skill that drives both agents states the premise:

> The wiki is not an index for RAG. It is an artifact that **grows**. Every ingested document is not merely summarized: it is *integrated*, updating existing pages, creating the links, and flagging contradictions. Knowledge is compiled once and then kept current, not re-derived at every question.

Practically, that means the reader agent does not embed and retrieve. It reads `index.md`, follows the link graph, and cites pages that themselves cite a position in a source document. At the scale a wiki is meant for — a hundred sources, a few hundred pages — navigation beats retrieval, and it stays auditable.

## Where to go next

- [Getting started](getting-started.md) — install, build a wiki, query it.
- [The OKF wiki format](okf.md) — bundle layout, frontmatter, the workflows the agents follow.
- [Architecture](architecture.md) — the backend mount, the permission model, why the prompts are thin.
- [API reference](api.md) — every parameter of both factories.
