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
# Software Supply Agreement - Acme S.p.A. / Contoso Srl

Date: March 12, 2026

## Art. 3 - Term
The agreement has a term of 24 months starting April 1, 2026, with automatic
renewal unless terminated.

## Art. 4 - Termination
Notice of termination must be given at least 90 days before the expiry date,
via certified email (PEC).

## Art. 7 - Service levels
Guaranteed availability of 99.5% on a monthly basis. Penalty of 5% of the
monthly fee for each percentage point below the threshold.
"""


def main() -> None:
    (WIKI / "raw").mkdir(parents=True, exist_ok=True)
    (WIKI / "raw" / "contract-acme-2026.md").write_text(SOURCE, encoding="utf-8")

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
                        "Bootstrap this wiki for supplier contracts (default categories, unsupervised ingest), then "
                        "ingest raw/contract-acme-2026.md. Run the linter when "
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
