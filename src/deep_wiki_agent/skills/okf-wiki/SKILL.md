---
name: okf-wiki
description: >-
  Costruisce e mantiene una wiki di conoscenza in formato OKF (Open Knowledge
  Format) a partire da documenti sorgente. Usa questa skill quando l'utente
  vuole ingerire documenti (PDF, docx, md, txt, pagine web, trascrizioni) in
  una knowledge base persistente, interrogare una wiki OKF esistente,
  verificarne la coerenza (lint), o convertire materiale documentale in un
  bundle OKF conforme. Trigger - "ingesta questo documento nella wiki",
  "aggiorna la knowledge base", "crea un bundle OKF", "cosa dice la wiki su X",
  "fai il lint della wiki".
---

# OKF Document Wiki

Mantieni una wiki persistente e compounding in **Open Knowledge Format v0.1**, alimentata da documenti.

Principio guida: la wiki non è un indice per RAG. È un artefatto che **cresce**. Ogni documento ingerito non viene solo riassunto: viene *integrato*, aggiornando le pagine esistenti, creando i collegamenti, e segnalando le contraddizioni. La conoscenza si compila una volta e poi si mantiene aggiornata, non si ri-deriva a ogni domanda.

L'utente cura le fonti e pone le domande. Tu fai tutto il resto: leggere, sintetizzare, collegare, archiviare, tenere in ordine.

---

## 1. Struttura del bundle

```
<bundle>/
├── AGENTS.md            # schema locale: convenzioni, tipi, workflow (co-evolve con l'utente)
├── index.md             # catalogo navigabile della root
├── log.md               # storico cronologico append-only
├── raw/                 # documenti sorgente — IMMUTABILI, mai modificare
│   └── assets/          # immagini estratte dai documenti
├── documents/           # una pagina per documento sorgente
│   ├── index.md
│   └── <slug>.md
├── entities/            # persone, organizzazioni, prodotti, sistemi
│   ├── index.md
│   └── <slug>.md
├── concepts/            # nozioni, definizioni, processi, procedure
│   ├── index.md
│   └── <slug>.md
└── syntheses/           # analisi trasversali, confronti, tesi in evoluzione
    ├── index.md
    └── <slug>.md
```

Il **percorso del file è l'identità del concetto**. Non rinominare file senza aggiornare tutti i link entranti.
`raw/` è l'unica directory che non appartiene alla wiki: sono le fonti, non toccarle mai.

Categorie diverse da `documents/entities/concepts/syntheses` sono legittime se il dominio lo richiede (es. `clauses/`, `runbooks/`, `decisions/`). Decidile con l'utente al bootstrap e registrale in `AGENTS.md`.

## 2. Conformità OKF

Ogni file concetto = un documento markdown con frontmatter YAML.

**L'unico campo obbligatorio da spec è `type`.** Il resto è convenzione di questo bundle, ma va rispettato per coerenza:

```yaml
---
type: Document | Entity | Concept | Synthesis | <tipo di dominio>
title: Titolo leggibile
description: Una frase, cosa contiene questa pagina.
resource: raw/relazione-annuale-2025.pdf   # path o URL della fonte primaria
tags: [bilancio, 2025]
timestamp: 2026-07-19T10:30:00Z            # ultimo aggiornamento, ISO 8601 UTC
sources: [documents/relazione-annuale-2025.md]   # pagine documento da cui deriva
---
```

Regole:
- I collegamenti sono **normali link markdown** con percorso relativo alla root del bundle: `[customers](/entities/acme-spa.md)`. Il grafo emerge dai link, non dalla gerarchia di cartelle.
- `timestamp` si aggiorna a ogni modifica sostanziale della pagina.
- `type` è libero ma **consistente**: elenca i tipi in uso in `AGENTS.md` e riusali, non inventarne uno nuovo per ogni pagina.
- Nessun campo proprietario obbligatorio. Il bundle deve restare leggibile da qualunque consumer OKF.
- `index.md` e `log.md` sono nomi riservati: non usarli per concetti.

Prima di dichiarare completa un'operazione, esegui `scripts/okf_lint.py` per verificare la conformità.

## 3. Operazioni

### Ingest — il caso d'uso principale

Quando l'utente aggiunge un documento in `raw/`:

1. **Leggi la fonte per intero.** Per PDF e docx consulta prima le skill `pdf-reading` / `docx`. Non lavorare su estratti parziali: se il documento è lungo, leggilo a sezioni ma coprilo tutto. Se contiene immagini o diagrammi rilevanti, aprili separatamente (il testo markdown da solo non li trasporta).
2. **Discuti i takeaway con l'utente** prima di scrivere, salvo che ti abbia chiesto un batch non supervisionato. Un giro di conferma qui evita di propagare un fraintendimento in 12 pagine.
3. **Crea `documents/<slug>.md`**: metadati del documento (autore, data, natura, provenienza), sintesi strutturata, affermazioni chiave con riferimento alla posizione nel documento (sezione, pagina, clausola). Le citazioni puntuali sono ciò che rende la wiki verificabile: senza di esse l'utente non può risalire alla fonte.
4. **Estrai entità e concetti.** Per ciascuno: se la pagina esiste, aggiornala integrando la nuova informazione; se non esiste e ha peso sufficiente, creala. Un'entità nominata di sfuggita una sola volta non merita una pagina — merita una menzione linkata dalla pagina documento.
5. **Aggiorna le pagine collegate.** È il passaggio che fa la differenza rispetto al RAG e anche quello che si è più tentati di saltare. Un documento sostanzioso tocca tipicamente 8-15 pagine.
6. **Segnala le contraddizioni.** Se la nuova fonte contraddice un'affermazione esistente, non sovrascrivere silenziosamente. Registra entrambe le versioni con la fonte e la data in una sezione `## Punti aperti` della pagina interessata, e portala all'attenzione dell'utente.
7. **Aggiorna gli `index.md`** delle categorie toccate e la root.
8. **Appendi a `log.md`.**

### Query

1. Leggi `index.md` (root, poi categoria) per orientarti. A scala moderata questo sostituisce il retrieval per embedding.
2. Leggi le pagine rilevanti; risali a `raw/` solo se serve un dettaglio che la wiki non ha catturato — e se serve spesso, è un segnale che la pagina va arricchita.
3. Rispondi **con citazioni ai file wiki** e, tramite quelli, al documento originale.
4. Se la risposta ha valore duraturo (un confronto, un'analisi, una connessione non ovvia), **proponi di archiviarla** in `syntheses/`. Le esplorazioni devono accumularsi come le fonti, non evaporare nella chat.

### Lint

Esegui `scripts/okf_lint.py`, poi aggiungi il giudizio che uno script non può dare:

- Contraddizioni tra pagine non ancora segnalate
- Affermazioni superate da fonti più recenti
- Pagine orfane (nessun link entrante) e link rotti
- Concetti citati ripetutamente ma senza pagina propria
- Frontmatter mancante o `type` incoerenti
- Lacune informative: cosa manca per rispondere alle domande che l'utente pone di più

Concludi proponendo fonti da cercare e domande da approfondire. Il lint è il momento in cui la wiki dice all'utente cosa le serve.

## 4. Bootstrap

Alla prima esecuzione, prima di creare qualunque cosa, definisci con l'utente:

- Dominio e scopo della wiki
- Quali categorie oltre a quelle di default
- Quali `type` useranno i documenti (elencali in `AGENTS.md`)
- Lingua delle pagine
- Ingest supervisionato o batch

Poi crea la struttura, scrivi `AGENTS.md` con queste decisioni, e inizializza `index.md` e `log.md` vuoti ma conformi.

## 5. Formato del log

Prefisso fisso, così il log resta interrogabile con `grep "^## \[" log.md | tail -5`:

```markdown
## [2026-07-19] ingest | Relazione annuale 2025
- Fonte: `raw/relazione-annuale-2025.pdf` (84 pp.)
- Creato: documents/relazione-annuale-2025.md, entities/acme-spa.md
- Aggiornato: concepts/margine-operativo.md, syntheses/andamento-2023-2025.md
- Aperto: il fatturato Q3 diverge da quello riportato nel bilancio semestrale
```

Tipi di entrata: `ingest`, `query`, `lint`, `refactor`.

## Risorse

- `scripts/okf_lint.py` — validatore di conformità: frontmatter, link rotti, pagine orfane, indici disallineati, timestamp malformati. Eseguilo `--fix` per correggere automaticamente indici e timestamp.
- `references/okf-spec-notes.md` — sintesi della spec OKF v0.1 e delle scelte di questo bundle. Consultalo in caso di dubbio sulla conformità.
