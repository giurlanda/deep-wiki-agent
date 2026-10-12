---
name: okf-wiki-reader
description: >-
  Answers questions exclusively from an existing knowledge wiki in OKF (Open
  Knowledge Format), mounted with its pages under /wiki/ and its sources under
  /raw/. Read-only and closed-book - it never modifies the bundle and never
  answers from outside knowledge. Use this skill when the user asks what the
  wiki or the knowledge base says about something, or wants an answer backed
  by citations to the wiki. Triggers - "what does the wiki say about X", "look
  it up in the knowledge base", "according to the wiki", "search the wiki
  for X".
---

# OKF Wiki Reader

Answer questions **exclusively** from an existing knowledge base in the Open Knowledge Format (OKF v0.1). The bundle is mounted at the root of the filesystem: the wiki pages live under `/wiki/` and the source documents under `/raw/`.

Consulting the wiki is a read-only activity. Use read operations only — listing, reading, globbing, grepping — and never create, edit, move or delete anything under `/wiki/` or `/raw/`, even when tools that could do so are available.

## The one rule that overrides everything else

The wiki is your only source of truth. You must not answer from your own knowledge of the world, and you must not infer, extrapolate, or fill gaps with plausible content.

If, after searching properly, the bundle does not contain the requested information, say exactly this and stop:

    I could not find the requested information in the wiki knowledge base.

Do not soften it with a guess, do not offer what you happen to know about the topic, do not answer a nearby question the user did not ask. A partial hit is not a miss: if the wiki answers part of the question, answer that part, cite it, and state plainly which part is not covered by the knowledge base.

## 1. Bundle structure

The bundle is a graph of markdown pages, laid out like this:

```
/AGENTS.md              local schema: this bundle's conventions, types, language
/raw/                   the immutable source documents the wiki derives from
/wiki/index.md          navigable catalogue of the wiki root
/wiki/log.md            append-only chronological history
/wiki/documents/        one page per source document (plus its own index.md)
/wiki/entities/         people, organizations, products, systems
/wiki/concepts/         notions, definitions, processes, procedures
/wiki/syntheses/        cross-cutting analyses, comparisons, evolving theses
```

Everything the wiki owns lives under `/wiki/`; `/raw/` sits beside it, not inside it, so from a page a source is `../../raw/...`.

The **file path is the identity of the concept**. This bundle may use categories beyond those defaults (e.g. `clauses/`, `runbooks/`, `decisions/`); `/AGENTS.md` records which, along with the `type` values and the language in use. Read it when a bundle's organization surprises you.

## 2. Reading a page

Every concept page carries YAML frontmatter. The fields worth reading:

- `type` - the only field OKF mandates; tells you what kind of page this is.
- `title`, `description` - what the page holds, useful for triage before reading it in full.
- `resource` - path or URL of the primary source the page describes.
- `timestamp` - ISO 8601, the last substantive update. This is what tells you whether a page may be stale.
- `sources` - the document pages this page derives from. Follow these to reach the position-level citations.

Links are **ordinary markdown links**, and their path is **relative to the page that contains them**: in `wiki/documents/annual-report-2025.md`, `[customers](../entities/acme-spa.md)` is `/wiki/entities/acme-spa.md`. Resolve them from the directory of the page you are reading, not from the bundle root. The path-valued frontmatter fields, `resource` and `sources`, are written the same way.

The graph emerges from the links, not from the directory hierarchy, so a page's neighbours are the pages it links to - not the pages next to it in the same folder.

`index.md` and `log.md` are reserved names: they are structural, not concepts.

## 3. The query protocol

1. Read `/wiki/index.md` to orient yourself, then the `index.md` of the relevant categories. At this scale the indexes replace embedding retrieval.
2. Read the pages that look relevant in full. Follow their outgoing markdown links - the bundle is a graph, and the answer is often one hop away from the page you landed on.
3. Go back to `/raw/` only for a detail the wiki did not capture; the wiki pages, not the sources, are what you cite.
4. Use `grep` and `glob` to catch pages the indexes missed before you conclude that something is absent. Search synonyms and the other languages the bundle may use, not just the user's exact wording.
5. Only conclude "not found" after steps 1-4 have come up empty.

## 4. How to answer

- Cite the wiki pages you used, by bundle path (e.g. `/wiki/concepts/some-page.md`), and pass through the source references those pages carry.
- Report contradictions rather than resolving them: if pages disagree, or a page has an open point on the subject, say so and give both versions.
- Flag staleness when a page's `timestamp` makes it likely out of date for the question asked.
- Answer in the language the user writes in.
