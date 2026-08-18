"""Middleware di debug per gli agenti langchain creati con `create_db_agent`.

Stampa su stdout:
    - l'ultimo messaggio generato dal modello (incluse eventuali tool_calls richieste);
    - l'output di ogni esecuzione di tool.

Uso:
    from debug_middleware import DebugColor, DebugMiddleware

    agent = create_db_agent(
        ...,
        middleware=[DebugMiddleware(color=DebugColor.CYAN)],
    )
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain.agents.middleware.types import AgentState
    from langgraph.runtime import Runtime
    from langgraph.types import Command

_ANSI_RESET = "\033[0m"


class DebugColor(Enum):
    """Colori ANSI disponibili per l'output del DebugMiddleware."""

    WHITE = "\033[37m"
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


class DebugMiddleware(AgentMiddleware):
    """Middleware di solo logging: non modifica stato, messaggi o tool call."""

    def __init__(self, color: DebugColor = DebugColor.WHITE) -> None:
        super().__init__()
        self._color = color

    def _colorize(self, text: str, color: DebugColor | None = None) -> str:
        return f"{color.value if color else self._color.value}{text}{_ANSI_RESET}"

    def before_agent(
        self,
        state: AgentState[Any],
        runtime: Runtime[None],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        last = state["messages"][-1]
        print(
            self._colorize(
                f" ===\n[debug][before agent] {type(last).__name__}: {last.content!r}",
                DebugColor.YELLOW,
            )
        )

    def after_agent(
        self,
        state: AgentState[Any],
        runtime: Runtime[None],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        last = state["messages"][-1]
        print(
            self._colorize(
                f" ===\n[debug][after agent] {type(last).__name__}: {last.content!r}",
                DebugColor.GREEN,
            )
        )

    def after_model(
        self,
        state: dict[str, Any],
        runtime: Runtime,  # noqa: ARG002
    ) -> None:
        last = state["messages"][-1]
        print(
            self._colorize(
                f" ===\n[debug][model] {type(last).__name__}: {last.content!r}"
            )
        )
        for call in getattr(last, "tool_calls", None) or []:
            print(
                self._colorize(
                    " ===\n[debug][model] tool_call richiesta -> "
                    f"{call['name']}({call['args']})"
                )
            )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        content = result.content if isinstance(result, ToolMessage) else result
        print(
            self._colorize(
                f" ---\n[debug][tool] {request.tool_call['name']} -> {content!r}",
                DebugColor.CYAN,
            )
        )
        return result
