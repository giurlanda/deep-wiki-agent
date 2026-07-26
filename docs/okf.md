# The OKF wiki format

The [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
v0.1 is an open, vendor-neutral specification that formalizes the LLM-wiki
pattern into a portable format. Not a service, not an SDK, not a runtime — a
*format*. An OKF bundle is:

- **markdown only** — readable in any editor, rendered on GitHub, indexable by
  any search engine;
- **files only** — shippable as a tarball, hostable in a git repo, mountable on
  any filesystem;
- **YAML frontmatter only** — for the small set of structured fields that must
  be queryable.

A **concept** is anything you want to capture, and one concept is one file:
**the file path is its identity**. Concepts link to each other with ordinary
markdown links, and the resulting *graph* is richer than the parent/child
hierarchy the filesystem implies.

## Bundle layout

The layout the agents' prompts prescribe:

```
<bundle>/
├── AGENTS.md            local schema: conventions, types, workflow
├── index.md             navigable catalogue of the root
├── log.md               append-only chronological history
├── raw/                 source documents — IMMUTABLE, never modified
│   └── assets/          images extracted from the documents
├── documents/           one page per source document
├── entities/            people, organizations, products, systems
├── concepts/            notions, definitions, processes, procedures
└── syntheses/           cross-cutting analyses, comparisons, evolving theses
```

Categories other than these four are legitimate when the domain calls for them
(`clauses/`, `runbooks/`, `decisions/`). Decide them with the agent at
bootstrap; they get recorded in `AGENTS.md`.

`raw/` is the one directory that is *not* part of the bundle: it holds the
sources, excluded from export and validation. The manager agent write-protects
it by default (`protect_raw=True`).

## Frontmatter

Every concept page is a markdown document with YAML frontmatter. **The only
field the spec mandates is `type`**; the rest is this bundle's convention.
Below, a page living in `entities/` — every path in it is relative to that
page:

```yaml
---
type: Document | Entity | Concept | Synthesis | <domain type>
title: Human-readable title
description: One sentence — what this page contains.
resource: ../raw/annual-report-2025.pdf   # path or URL of the primary source
tags: [finance, 2025]
timestamp: 2026-07-19T10:30:00Z           # last update, ISO 8601 UTC
sources: [../documents/annual-report-2025.md]
---
```

| Field | Status | Notes |
|---|---|---|
| `type` | **required** | the spec's only hard requirement |
| `title` | conventional | human-readable title |
| `description` | conventional | one sentence |
| `resource` | conventional | URI/path of the described resource |
| `tags` | conventional | list |
| `timestamp` | conventional | ISO 8601, updated on every substantive edit |
| `sources` | *this bundle's extension* | document pages a page derives from |

Extensions like `sources` are allowed: a consumer that does not know them
simply ignores them.

Rules the agents enforce on top:

- Links are ordinary markdown links whose path is **relative to the page that
  contains them**, never absolute: from `index.md` a document page is
  `documents/annual-report-2025.md`, and from
  `documents/annual-report-2025.md` an entity is
  `[customers](../entities/acme-spa.md)`. A leading `/` is what the linter
  reports as an absolute link — relative paths are what keeps the bundle
  browsable once it is moved, rendered on GitHub or opened in an editor. The
  graph emerges from links, not from the directory hierarchy.
- The path-valued frontmatter fields, `resource` and `sources`, follow the same
  rule (URLs in `resource` are left as they are).
- `type` is free-form but **consistent**: list the types in use in `AGENTS.md`
  and reuse them rather than inventing one per page.
- `index.md` and `log.md` are reserved names and cannot be concept pages.

## Reserved names

`index.md` is a directory's catalogue, used for **progressive disclosure**: an
agent navigating the hierarchy reads the index before descending into pages. At
moderate scale (~100 sources, a few hundred pages) this replaces embedding
retrieval. `log.md` is the chronological history of changes.

Both are optional per the spec. This library treats them as mandatory:
without an index the wiki is not navigable by an agent, and without a log the
story of how it grew is lost.

## The workflows

Four operations define the wiki's lifecycle. They live in the agents' system
prompts, so they are in force from the first turn: the manager carries all
four, the reader only *Query*.

### Ingest

The main use case. When a document lands in `raw/`:

1. **Read the source in full.** Not extracts — long documents get read section
   by section, but covered entirely. Relevant images and diagrams are opened
   separately: markdown text alone does not carry them.
2. **Discuss the takeaways** before writing, unless you asked for an
   unsupervised batch.
3. **Create `documents/<slug>.md`**: document metadata, structured summary, key
   claims with a reference to their position in the source (section, page,
   clause). Those pinpoint citations are what make the wiki verifiable rather
   than merely plausible.
4. **Extract entities and concepts.** Existing pages are updated by integration;
   new ones are created only when the subject carries enough weight. An entity
   named once in passing gets a linked mention, not a page.
5. **Update the linked pages.** This is the step that distinguishes the wiki
   from RAG, and the one most tempting to skip. A substantial document
   typically touches 8–15 pages.
6. **Flag contradictions.** A new source that contradicts an existing claim
   does not silently overwrite it: both versions are recorded with source and
   date under `## Open points`, and raised with you.
7. **Update the `index.md`** of the touched categories and the root.
8. **Append to `log.md`.**

### Query

1. Read `index.md` (root, then category) to orient. At moderate scale this
   replaces embedding retrieval.
2. Read the relevant pages; go back to `raw/` only for a detail the wiki did
   not capture — and if that happens often, the page needs enriching.
3. Answer **with citations to the wiki files** and, through them, to the
   original document.
4. If the answer has lasting value (a comparison, an analysis, a non-obvious
   connection), propose archiving it under `syntheses/`. Explorations should
   accumulate like sources, not evaporate in the chat.

The read-only agent follows steps 1–3. It cannot perform step 4 — proposing an
archive is a write — which is the intended division of labour: consultation
never mutates the knowledge base.

### Lint

The mechanical checks (`okf_lint`) plus the judgment a script cannot give:
contradictions not yet flagged, claims superseded by newer sources, orphan
pages, broken and absolute links, concepts repeatedly cited without a page,
missing or inconsistent frontmatter, and the informational gaps that block the
questions you ask most.

### Bootstrap

Before creating anything, the agent settles with you: the domain and purpose,
the categories beyond the defaults, the `type` values in use, the language of
the pages, and whether ingest is supervised or batch. Those decisions go into
`AGENTS.md`, and `index.md` and `log.md` are initialized empty but conformant.

## Log format

A fixed prefix, so the log stays greppable with
`grep "^## \[" log.md | tail -5`:

```markdown
## [2026-07-19] ingest | Annual report 2025
- Source: `raw/annual-report-2025.pdf` (84 pp.)
- Created: documents/annual-report-2025.md, entities/acme-spa.md
- Updated: concepts/operating-margin.md, syntheses/trend-2023-2025.md
- Open: Q3 revenue diverges from the half-year statement
```

Entry types: `ingest`, `query`, `lint`, `refactor`.

## Reading the instructions yourself

These rules are not hidden in a file the agent has to load: they are its system
prompt, and you can print exactly what it will follow.

```python
from deep_wiki_agent import (
    MANAGER_SYSTEM_PROMPT_TEMPLATE,
    READER_SYSTEM_PROMPT_TEMPLATE,
)
from deep_wiki_agent.prompts import LINT_TOOL_BLOCK

print(
    MANAGER_SYSTEM_PROMPT_TEMPLATE.format(
        wiki_root="/", raw_dir="/raw", lint_block=LINT_TOOL_BLOCK
    )
)
print(
    READER_SYSTEM_PROMPT_TEMPLATE.format(
        wiki_root="/", raw_dir="/raw", not_found_message="..."
    )
)
```

The repository also keeps `skills/okf-wiki/SKILL.md` as the canonical
human-facing statement of the format — usable in Claude Code and other
skill-aware harnesses, and kept in step with the prompts by
`tests/test_prompt_drift.py`. It is not part of the installed package.
