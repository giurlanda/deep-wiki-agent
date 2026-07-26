"""Factories for the two OKF wiki agents.

- :func:`create_wiki_manager_agent` — writes: ingests documents into an OKF
  bundle, keeps pages, links, indexes and log in sync, lints the result.
- :func:`create_deep_wiki_agent` — reads: answers questions strictly from an
  existing bundle, and says so when the bundle does not cover the question.

Both are thin wrappers over ``deepagents.create_deep_agent``. What they add is
the wiring that makes an OKF bundle usable by an agent: a backend that puts the
bundle at the virtual root, a system prompt carrying the format's rules and
workflows, filesystem permissions that enforce immutability where the format
requires it, and (for the manager) a runnable conformance linter.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

from deep_wiki_agent.backends import (
    RAW_DIR,
    read_only_permissions,
    resolve_local_wiki_path,
    write_protect_permissions,
)
from deep_wiki_agent.prompts import (
    DEFAULT_NOT_FOUND_MESSAGE,
    LINT_TOOL_BLOCK,
    MANAGER_SYSTEM_PROMPT_TEMPLATE,
    READER_SYSTEM_PROMPT_TEMPLATE,
)
from deep_wiki_agent.tools import create_okf_lint_tool

if TYPE_CHECKING:
    from collections.abc import Sequence

    from deepagents.backends.protocol import BackendProtocol
    from deepagents.middleware.filesystem import FilesystemPermission
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import SystemMessage
    from langchain_core.tools import BaseTool
    from langgraph.graph.state import CompiledStateGraph

__all__ = ["create_deep_wiki_agent", "create_wiki_manager_agent"]

logger = logging.getLogger(__name__)

WIKI_ROOT = "/"
"""Mount point of the OKF bundle on the agent's virtual filesystem."""


def _require_model(model: str | BaseChatModel | None, factory: str) -> None:
    """Raise ``ValueError`` unless an explicit model was provided.

    Args:
        model: The caller-supplied model.
        factory: Name of the calling factory, for the error message.

    Raises:
        ValueError: If ``model`` is ``None``.
    """
    if model is None:
        msg = (
            f"{factory} requires an explicit `model`: deepagents' implicit "
            "default model is deprecated."
        )
        raise ValueError(msg)


def create_wiki_manager_agent(
    *,
    model: str | BaseChatModel,
    wiki_path: str | Path | None = None,
    backend: BackendProtocol | None = None,
    create_if_missing: bool = True,
    protect_raw: bool = True,
    enable_lint_tool: bool = True,
    virtual_mode: bool = True,
    system_prompt: str | SystemMessage | None = None,
    tools: Sequence[BaseTool] = (),
    permissions: list[FilesystemPermission] | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    **create_deep_agent_kwargs: Any,
) -> CompiledStateGraph:
    """Create an agent that builds and maintains an OKF wiki.

    The returned deep agent has read/write access to the bundle at
    ``wiki_path``. Its operating instructions — bundle structure, frontmatter
    conformance, and the ingest / query / lint / bootstrap workflows — are in
    its system prompt, so they are in force from the first turn with nothing to
    load and nothing that can be skipped.

    Args:
        model: Model for the agent. Required — either a provider string
            (e.g. ``"anthropic:claude-sonnet-5"``) or a ``BaseChatModel``.
        wiki_path: Local directory of the OKF bundle, placed at the virtual
            root. Required unless ``backend`` is given.
        backend: Pre-built backend, used verbatim instead of the one this
            factory would assemble. Use it for non-local storage (state,
            store, sandbox). Mutually exclusive with ``wiki_path``.
        create_if_missing: When ``True`` (default), ``wiki_path`` is created
            if it does not exist, so the agent can bootstrap a new bundle into
            it. When ``False``, a missing directory raises.
        protect_raw: When ``True`` (default), writes under ``/raw`` are denied
            at the tool boundary. Source documents are immutable in the OKF
            wiki convention, and this makes that structural rather than a
            matter of the model's compliance. When ``False``, no permission
            rules are applied at all.
        enable_lint_tool: When ``True`` (default) and the bundle resolves to a
            local directory, attach the ``okf_lint`` tool so the agent can run
            the conformance check its prompt asks for (it has no shell).
            Skipped for backends that are not filesystem-backed; the skip is
            reported on the ``deep_wiki_agent.factory`` logger at ``WARNING``.
        virtual_mode: Confine the filesystem backend to the bundle directory,
            blocking ``../`` and ``~/`` escapes. Leave enabled unless you have
            a specific reason not to.
        system_prompt: Override for the built-in manager prompt. Overriding it
            replaces the OKF instructions wholesale, so restate whatever of
            them you still want in force; the rest of the wiring is unchanged.
        tools: Extra tools, added alongside ``okf_lint`` and the built-in
            deepagents tools. This is where document loaders (PDF, docx, URL
            fetchers) belong — the library ships none, since which formats a
            wiki ingests is domain-specific.
        permissions: Override for the computed filesystem permissions. Passing
            this replaces the ``raw`` protection entirely.
        middleware: Extra middleware passed through to ``create_deep_agent``.
        **create_deep_agent_kwargs: Any remaining ``create_deep_agent``
            parameter (``subagents``, ``skills``, ``checkpointer``, ``store``,
            ``interrupt_on``, ``response_format``, ...), passed unchanged.

    Returns:
        The compiled deep agent graph, ready for ``invoke`` / ``astream``.

    Raises:
        ValueError: If ``model`` is missing, or if neither or both of
            ``wiki_path`` and ``backend`` are given.
        FileNotFoundError: If ``wiki_path`` does not exist and
            ``create_if_missing`` is ``False``.
    """
    _require_model(model, "create_wiki_manager_agent")

    effective_backend = _resolve_backend(
        wiki_path=wiki_path,
        backend=backend,
        virtual_mode=virtual_mode,
        create_if_missing=create_if_missing,
    )

    # The linter walks a real directory, so it is only attachable when the
    # bundle is filesystem-backed. For other backends the caller has to run
    # `python -m deep_wiki_agent.okf_lint` out of band.
    agent_tools: list[BaseTool] = list(tools)
    if enable_lint_tool:
        local_root = resolve_local_wiki_path(effective_backend)
        if local_root is not None:
            agent_tools.append(create_okf_lint_tool(local_root))
        else:
            # The caller asked for the linter and is not getting it. Saying so
            # is the only signal they have: the agent is built either way, and
            # a bundle that is never linted degrades silently.
            logger.warning(
                "enable_lint_tool=True but the backend %s is not rooted at a "
                "local directory, so the OKF linter was not attached. Run "
                "`python -m deep_wiki_agent.okf_lint <bundle>` out of band "
                "instead.",
                type(effective_backend).__name__,
            )
    lint_block = LINT_TOOL_BLOCK if len(agent_tools) > len(tools) else ""

    if system_prompt is None:
        system_prompt = MANAGER_SYSTEM_PROMPT_TEMPLATE.format(
            wiki_root=WIKI_ROOT,
            raw_dir=RAW_DIR,
            lint_block=lint_block,
        )

    if permissions is None:
        permissions = write_protect_permissions([RAW_DIR] if protect_raw else [])

    return create_deep_agent(
        model=model,
        tools=agent_tools,
        system_prompt=system_prompt,
        backend=effective_backend,
        permissions=permissions,
        middleware=middleware,
        **create_deep_agent_kwargs,
    )


def create_deep_wiki_agent(
    *,
    model: str | BaseChatModel,
    wiki_path: str | Path | None = None,
    backend: BackendProtocol | None = None,
    not_found_message: str = DEFAULT_NOT_FOUND_MESSAGE,
    virtual_mode: bool = True,
    system_prompt: str | SystemMessage | None = None,
    tools: Sequence[BaseTool] = (),
    permissions: list[FilesystemPermission] | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    **create_deep_agent_kwargs: Any,
) -> CompiledStateGraph:
    """Create an agent that answers questions from an existing OKF wiki.

    The returned deep agent is read-only over the bundle: every write
    operation is denied by ``FilesystemMiddleware`` at the tool boundary, not
    merely discouraged by the prompt, so a consultation session cannot corrupt
    the knowledge base regardless of what the model is asked to do.

    Its answering contract is closed-book over the bundle: it navigates the
    indexes and the link graph following the query protocol in its prompt,
    cites the pages it used, and — when the bundle does not cover the question
    — replies with ``not_found_message`` instead of falling back on the model's
    own knowledge.

    Args:
        model: Model for the agent. Required — either a provider string
            (e.g. ``"anthropic:claude-sonnet-5"``) or a ``BaseChatModel``.
        wiki_path: Local directory of the OKF bundle to consult, placed at the
            virtual root. Required unless ``backend`` is given. Must already
            exist: this agent never creates a bundle.
        backend: Pre-built backend, used verbatim instead of the one this
            factory would assemble — for a bundle held in a store, a sandbox,
            or any non-local storage. Mutually exclusive with ``wiki_path``.
        not_found_message: The exact sentence the agent must return when the
            bundle does not contain the requested information. Override it to
            match your product's voice or language; the surrounding contract
            (no guessing, no outside knowledge, partial answers allowed and
            labelled) is unchanged.
        virtual_mode: Confine the filesystem backend to the bundle directory,
            blocking ``../`` and ``~/`` escapes.
        system_prompt: Override for the built-in reader prompt. Note that the
            not-found contract and the query protocol live in that prompt: if
            you replace it, restate them yourself. The read-only *enforcement*,
            by contrast, is in the permissions and survives any prompt.
        tools: Extra read-only tools. Do not pass tools that can write to the
            bundle: the filesystem permissions cannot police tools they do not
            mediate.
        permissions: Override for the read-only permission set. Passing this
            replaces the deny-all-writes rule — the agent is then only as
            read-only as your rules make it.
        middleware: Extra middleware passed through to ``create_deep_agent``.
        **create_deep_agent_kwargs: Any remaining ``create_deep_agent``
            parameter (``subagents``, ``skills``, ``checkpointer``, ``store``,
            ``response_format``, ...), passed unchanged.

    Returns:
        The compiled deep agent graph, ready for ``invoke`` / ``astream``.

    Raises:
        ValueError: If ``model`` is missing, or if neither or both of
            ``wiki_path`` and ``backend`` are given.
        FileNotFoundError: If ``wiki_path`` does not exist.
    """
    _require_model(model, "create_deep_wiki_agent")

    effective_backend = _resolve_backend(
        wiki_path=wiki_path,
        backend=backend,
        virtual_mode=virtual_mode,
        create_if_missing=False,
    )

    if system_prompt is None:
        system_prompt = READER_SYSTEM_PROMPT_TEMPLATE.format(
            wiki_root=WIKI_ROOT,
            raw_dir=RAW_DIR,
            not_found_message=not_found_message,
        )

    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        backend=effective_backend,
        permissions=(read_only_permissions() if permissions is None else permissions),
        middleware=middleware,
        **create_deep_agent_kwargs,
    )


def _resolve_backend(
    *,
    wiki_path: str | Path | None,
    backend: BackendProtocol | None,
    virtual_mode: bool,
    create_if_missing: bool,
) -> BackendProtocol:
    """Return the backend to hand to ``create_deep_agent``.

    Args:
        wiki_path: Local bundle directory, or ``None`` when ``backend`` is given.
        backend: Caller-supplied backend, propagated unchanged when present.
        virtual_mode: Whether to confine the filesystem backend to its root.
        create_if_missing: Whether to create ``wiki_path`` when absent.

    Returns:
        Either the caller's backend or a ``FilesystemBackend`` rooted at the
        bundle.

    Raises:
        ValueError: If neither or both of ``wiki_path`` and ``backend`` are given.
        FileNotFoundError: If ``wiki_path`` is absent and must not be created.
    """
    if (wiki_path is None) == (backend is None):
        msg = (
            "provide exactly one of `wiki_path` (local OKF bundle, backend "
            "assembled for you) or `backend` (pre-built, used verbatim)"
        )
        raise ValueError(msg)

    if backend is not None:
        return backend

    assert wiki_path is not None  # noqa: S101 - guaranteed by the check above
    root = Path(wiki_path).expanduser().resolve()
    if not root.exists():
        if not create_if_missing:
            msg = f"wiki_path does not exist: {root}"
            raise FileNotFoundError(msg)
        root.mkdir(parents=True, exist_ok=True)

    return FilesystemBackend(root_dir=root, virtual_mode=virtual_mode)
