"""Ask questions of an existing OKF wiki, including one it cannot answer.

Run ``examples/build_wiki.py`` first, then:
    export ANTHROPIC_API_KEY=...
    uv run python examples/query_wiki.py

The second question is deliberately outside the bundle: the agent should
return the not-found sentence rather than answering from model knowledge.
"""

from __future__ import annotations

from pathlib import Path
from debug_middleware import DebugMiddleware
from deep_wiki_agent import create_deep_wiki_agent

MODEL = "anthropic:claude-sonnet-5"
WIKI = Path(__file__).parent / "contracts-wiki"

QUESTIONS = [
    "What is the termination notice period under the Acme contract?",
    "What was Acme's revenue in 2025?",
]


def main() -> None:
    if not WIKI.is_dir():
        msg = f"{WIKI} does not exist — run examples/build_wiki.py first"
        raise SystemExit(msg)

    agent = create_deep_wiki_agent(
        model=MODEL, wiki_path=WIKI, middleware=[DebugMiddleware()]
    )

    for i, question in enumerate(QUESTIONS):
        result = agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"configurable": {"thread_id": f"query-{i}"}},
        )
        print(f"\n=== {question}\n")
        print(result["messages"][-1].content)


if __name__ == "__main__":
    main()
