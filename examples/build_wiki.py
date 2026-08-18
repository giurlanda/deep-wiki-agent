"""Bootstrap an OKF wiki and ingest a source document into it.

Run:
    export ANTHROPIC_API_KEY=...
    uv run python examples/build_wiki.py

Creates ``examples/contracts-wiki/`` with a source document in ``raw/`` and
lets the manager agent build the bundle around it.
"""

from __future__ import annotations

from pathlib import Path
from debug_middleware import DebugMiddleware
from deep_wiki_agent.tools.documents import create_read_document_tool

from deep_wiki_agent import create_wiki_manager_agent

MODEL = "anthropic:claude-sonnet-5"
WIKI = Path(__file__).parent / "contracts-wiki"

SOURCE = """\
# Fornitura software - Acme S.p.A. / Contoso Srl

Data: 12 marzo 2026

## Art. 3 - Durata
Il contratto ha durata di 24 mesi a partire dal 1 aprile 2026, con rinnovo
tacito salvo disdetta.

## Art. 4 - Disdetta
La disdetta deve pervenire con preavviso di 90 giorni rispetto alla scadenza,
a mezzo PEC.

## Art. 7 - Livelli di servizio
Disponibilita' garantita del 99,5% su base mensile. Penale del 5% del canone
mensile per ogni punto percentuale sotto la soglia.
"""


def main() -> None:
    (WIKI / "raw").mkdir(parents=True, exist_ok=True)
    (WIKI / "raw" / "contratto-acme-2026.md").write_text(SOURCE, encoding="utf-8")

    agent = create_wiki_manager_agent(
        model=MODEL, wiki_path=WIKI, middleware=[DebugMiddleware()],
        tools=[create_read_document_tool(wiki_path=WIKI)]
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Bootstrap this wiki for supplier contracts (Italian "
                        "pages, default categories, unsupervised ingest), then "
                        "ingest raw/contratto-acme-2026.md. Run the linter when "
                        "you are done."
                    ),
                }
            ]
        },
        config={"configurable": {"thread_id": "build-1"}},
    )

    print(result["messages"][-1].content)
    print(f"\nBundle written to {WIKI}")


if __name__ == "__main__":
    main()
