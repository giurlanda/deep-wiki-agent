"""Bootstrap an OKF wiki, ingest source documents, then query it.

Run:
    export ANTHROPIC_API_KEY=...
    uv run python examples/build_document_wiki.py

Copies every file from ``examples/assets/`` (``SOURCE``) into
``examples/documents-wiki/raw/``, lets the manager agent build the wiki
bundle around them, then asks a ``create_deep_wiki_agent`` reader a question
that can only be answered from the ingested documents.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from debug_middleware import DebugMiddleware

from deep_wiki_agent import create_deep_wiki_agent, create_wiki_manager_agent
from deep_wiki_agent.tools.documents import create_read_document_tool

MODEL = "anthropic:claude-sonnet-5"

WIKI = Path(__file__).parent / "documents-wiki"

SOURCE = Path(__file__).parent / "assets"

QUESTION = (
    "What are the six core capabilities of Deep Agents, and what four "
    "layers make up its execution environment?"
)


def main() -> None:
    (WIKI / "raw").mkdir(parents=True, exist_ok=True)

    for source_file in SOURCE.iterdir():
        if source_file.is_file():
            shutil.copy2(source_file, WIKI / "raw" / source_file.name)

    manager = create_wiki_manager_agent(
        model=MODEL, wiki_path=WIKI, middleware=[DebugMiddleware()],
        tools=[create_read_document_tool(wiki_path=WIKI)]
    )

    result = manager.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Bootstrap this wiki for technical documentation "
                        "(default categories, unsupervised "
                        "ingest), then ingest all file in raw/ . Run the "
                        "linter when you are done."
                    ),
                }
            ]
        },
        config={"configurable": {"thread_id": "build-1"}},
    )

    print(result["messages"][-1].content)
    print(f"\nBundle written to {WIKI}")

    reader = create_deep_wiki_agent(
        model=MODEL, wiki_path=WIKI, middleware=[DebugMiddleware()]
    )

    answer = reader.invoke(
        {"messages": [{"role": "user", "content": QUESTION}]},
        config={"configurable": {"thread_id": "query-1"}},
    )
    print(f"\n=== {QUESTION}\n")
    print(answer["messages"][-1].content)


if __name__ == "__main__":
    main()
