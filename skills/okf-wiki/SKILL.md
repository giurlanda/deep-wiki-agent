---
name: okf-wiki
description: >-
  Builds and maintains a knowledge wiki in OKF (Open Knowledge Format) from
  source documents. Use this skill when the user wants to ingest documents
  (PDF, docx, md, txt, web pages, transcripts) into a persistent knowledge
  base, query an existing OKF wiki, check its consistency (lint), or convert
  documentary material into a conformant OKF bundle. Triggers - "ingest this
  document into the wiki", "update the knowledge base", "create an OKF
  bundle", "what does the wiki say about X", "lint the wiki".
---

# OKF Document Wiki

Maintain a persistent, compounding wiki in **Open Knowledge Format v0.1**, fed by documents.

Guiding principle: the wiki is not an index for RAG. It is an artifact that **grows**. Every ingested document is not merely summarized: it is *integrated*, updating the existing pages, creating the links, and flagging contradictions. Knowledge is compiled once and then kept current, not re-derived at every question.

The user curates the sources and asks the questions. You do everything else: read, synthesize, link, archive, keep in order.

---

## 1. Bundle structure

```
<bundle>/
├── AGENTS.md            # local schema: conventions, types, workflow (co-evolves with the user)
├── index.md             # navigable catalogue of the root
├── log.md               # append-only chronological history
├── raw/                 # source documents — IMMUTABLE, never modify
│   └── assets/          # images extracted from the documents
├── documents/           # one page per source document
│   ├── index.md
│   └── <slug>.md
├── entities/            # people, organizations, products, systems
│   ├── index.md
│   └── <slug>.md
├── concepts/            # notions, definitions, processes, procedures
│   ├── index.md
│   └── <slug>.md
└── syntheses/           # cross-cutting analyses, comparisons, evolving theses
    ├── index.md
    └── <slug>.md
```

The **file path is the identity of the concept**. Do not rename files without updating every inbound link.
`raw/` is the one directory that does not belong to the wiki: those are the sources, never touch them.

Categories other than `documents/entities/concepts/syntheses` are legitimate when the domain calls for them (e.g. `clauses/`, `runbooks/`, `decisions/`). Decide them with the user at bootstrap and record them in `AGENTS.md`.

## 2. OKF conformance

Every concept file = one markdown document with YAML frontmatter.

**The only field the spec mandates is `type`.** The rest is this bundle's convention, but it must be respected for consistency:

```yaml
---
type: Document | Entity | Concept | Synthesis | <domain type>
title: Human-readable title
description: One sentence, what this page contains.
resource: raw/annual-report-2025.pdf        # path or URL of the primary source
tags: [finance, 2025]
timestamp: 2026-07-19T10:30:00Z             # last update, ISO 8601 UTC
sources: [documents/annual-report-2025.md]  # document pages it derives from
---
```

Rules:
- Links are **ordinary markdown links** with a path relative to the bundle root: `[customers](/entities/acme-spa.md)`. The graph emerges from the links, not from the directory hierarchy.
- `timestamp` is updated on every substantive change to the page.
- `type` is free-form but **consistent**: list the types in use in `AGENTS.md` and reuse them, do not invent a new one per page.
- No mandatory proprietary field. The bundle must stay readable by any OKF consumer.
- `index.md` and `log.md` are reserved names: do not use them for concepts.

Before declaring an operation complete, run the OKF linter to verify conformance.

## 3. Operations

### Ingest — the main use case

When the user adds a document to `raw/`:

1. **Read the source in full.** For PDF and docx consult the `pdf-reading` / `docx` skills first. Do not work on partial extracts: if the document is long, read it section by section but cover all of it. If it contains relevant images or diagrams, open them separately (the markdown text alone does not carry them).
2. **Discuss the takeaways with the user** before writing, unless they asked for an unsupervised batch. One confirmation round here prevents a misunderstanding from propagating into 12 pages.
3. **Create `documents/<slug>.md`**: document metadata (author, date, nature, provenance), structured summary, key claims with a reference to their position in the document (section, page, clause). Pinpoint citations are what makes the wiki verifiable: without them the user cannot trace back to the source.
4. **Extract entities and concepts.** For each: if the page exists, update it by integrating the new information; if it does not exist and carries enough weight, create it. An entity named once in passing does not deserve a page — it deserves a linked mention from the document page.
5. **Update the linked pages.** This is the step that makes the difference against RAG, and also the one you are most tempted to skip. A substantial document typically touches 8-15 pages.
6. **Flag contradictions.** If the new source contradicts an existing claim, do not silently overwrite. Record both versions with their source and date in an `## Open points` section of the affected page, and bring it to the user's attention.
7. **Update the `index.md`** of the touched categories and the root.
8. **Append to `log.md`.**

### Query

1. Read `index.md` (root, then category) to orient yourself. At moderate scale this replaces embedding retrieval.
2. Read the relevant pages; go back to `raw/` only if you need a detail the wiki did not capture — and if you need it often, that is a signal the page should be enriched.
3. Answer **with citations to the wiki files** and, through them, to the original document.
4. If the answer has lasting value (a comparison, an analysis, a non-obvious connection), **propose archiving it** under `syntheses/`. Explorations should accumulate like the sources, not evaporate in the chat.

### Lint

Run the OKF linter, then add the judgment a script cannot give:

- Contradictions between pages not yet flagged
- Claims superseded by more recent sources
- Orphan pages (no inbound links) and broken links
- Concepts cited repeatedly but without a page of their own
- Missing frontmatter or inconsistent `type` values
- Informational gaps: what is missing to answer the questions the user asks most

Conclude by proposing sources to look for and questions to dig into. The lint is the moment when the wiki tells the user what it needs.

## 4. Bootstrap

On the first run, before creating anything, settle with the user:

- Domain and purpose of the wiki
- Which categories beyond the defaults
- Which `type` values the documents will use (list them in `AGENTS.md`)
- Language of the pages
- Supervised or batch ingest

Then create the structure, write `AGENTS.md` with those decisions, and initialize `index.md` and `log.md` empty but conformant.

## 5. Log format

Fixed prefix, so the log stays queryable with `grep "^## \[" log.md | tail -5`:

```markdown
## [2026-07-19] ingest | Annual report 2025
- Source: `raw/annual-report-2025.pdf` (84 pp.)
- Created: documents/annual-report-2025.md, entities/acme-spa.md
- Updated: concepts/operating-margin.md, syntheses/trend-2023-2025.md
- Open: Q3 revenue diverges from the half-year statement
```

Entry types: `ingest`, `query`, `lint`, `refactor`.

## Resources

- `deep_wiki_agent.okf_lint` — conformance validator: frontmatter, broken links, orphan pages, stale indexes, malformed timestamps. Run it as `python -m deep_wiki_agent.okf_lint <bundle> [--fix] [--json]`; `--fix` normalizes malformed timestamps automatically. Agents built by `deep-wiki-agent` get the same validator as their `okf_lint` tool, already bound to their bundle.
- `references/okf-spec-notes.md` — summary of the OKF v0.1 spec and of this bundle's choices. Consult it whenever you are unsure about conformance.
