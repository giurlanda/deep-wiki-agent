# OKF v0.1 — note di riferimento

Sintesi della spec pubblicata da Google Cloud (giugno 2026) e delle scelte fatte da questa skill.
Spec e implementazioni di riferimento: https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf

## Cos'è OKF

Una specifica aperta e neutrale rispetto al vendor che formalizza il pattern LLM-wiki in un formato portabile. Non è un servizio, un SDK o un runtime: è un **formato**. Un bundle OKF è:

- **solo markdown** — leggibile in qualunque editor, renderizzabile su GitHub, indicizzabile da qualunque motore di ricerca
- **solo file** — spedibile come tarball, ospitabile in un repo git, montabile su qualunque filesystem
- **solo frontmatter YAML** — per il piccolo insieme di campi strutturati che devono essere interrogabili

Il valore del formato viene da quante parti lo parlano, non da chi lo possiede.

## Il modello

Un **bundle** è una directory di file markdown che rappresentano **concetti**. Un concetto è qualsiasi cosa si voglia catturare: una tabella, un dataset, una metrica, un runbook, un'API — e, per questa skill, un documento, un'entità, una nozione, una sintesi.

**Un concetto = un file. Il percorso del file è la sua identità.**

I concetti si collegano tra loro con normali link markdown. Il risultato è un **grafo** di relazioni più ricco della gerarchia padre/figlio implicata dal filesystem — ed è questo che distingue un bundle OKF da una cartella di appunti.

## I tre principi di design

1. **Minimamente prescrittivo.** OKF richiede esattamente una cosa da ogni concetto: il campo `type`. Quali tipi esistano, quali altri campi includere, che sezioni abbia il corpo — è tutto lasciato al produttore. La spec definisce la superficie di interoperabilità, non il modello di contenuto.
2. **Indipendenza produttore/consumatore.** Chi scrive la conoscenza è separato da chi la consuma. Un bundle scritto a mano può essere letto da un agente; uno generato da una pipeline può essere sfogliato in un visualizzatore; uno sintetizzato da un LLM può essere interrogato da un altro. Il formato è il contratto, gli strumenti alle due estremità sono sostituibili.
3. **Formato, non piattaforma.** Nessun legame con cloud, database, model provider o framework di agenti. Non richiederà mai un account proprietario per essere letto, scritto o servito.

## Campi del frontmatter

| Campo | Stato | Note |
|---|---|---|
| `type` | **obbligatorio** | unico requisito hard della spec |
| `title` | convenzionale | titolo leggibile |
| `description` | convenzionale | una frase |
| `resource` | convenzionale | URI/path della risorsa descritta |
| `tags` | convenzionale | lista |
| `timestamp` | convenzionale | ISO 8601 |

`sources` è un'estensione di questo bundle, non della spec: traccia le pagine documento da cui una pagina deriva. Le estensioni sono ammesse — restano leggibili da consumer che non le conoscono, che semplicemente le ignorano.

## Nomi riservati

- `index.md` — catalogo di una directory, per la *progressive disclosure*: un agente che naviga la gerarchia legge l'indice prima di scendere nelle pagine. A scala moderata (~100 fonti, qualche centinaio di pagine) questo sostituisce il retrieval per embedding.
- `log.md` — storico cronologico delle modifiche.

Entrambi sono opzionali per la spec. Questa skill li tratta come obbligatori: senza indice la wiki non è navigabile da un agente, senza log si perde la storia di come è cresciuta.

## Scelte specifiche di questa skill

Vincoli che questa skill aggiunge sopra OKF, per orientare il formato ai documenti:

- Categorie di default `documents/`, `entities/`, `concepts/`, `syntheses/` — adattabili al dominio in fase di bootstrap.
- `raw/` contiene le fonti immutabili e **non fa parte del bundle OKF**: va escluso da export e validazione.
- Ogni affermazione sostanziale in una pagina documento porta un riferimento alla posizione nella fonte (sezione, pagina, clausola). È ciò che rende la wiki verificabile invece che solo plausibile.
- Le contraddizioni non si risolvono sovrascrivendo: si registrano sotto `## Punti aperti` con fonte e data di entrambe le versioni.

## Ecosistema

Google ha pubblicato tre implementazioni di riferimento: un agente di arricchimento che genera concetti OKF da dataset BigQuery, un visualizzatore HTML statico che rende un bundle come grafo interattivo in un singolo file autocontenuto, e tre bundle di esempio navigabili (GA4 e-commerce, Stack Overflow, Bitcoin). Sono dichiaratamente proof of concept: niente nel formato richiede quello specifico agente o quella specifica UI.

v0.1 è un punto di partenza, versionato e progettato per crescere in modo retrocompatibile.
