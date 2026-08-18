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

from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

from deep_wiki_agent.backends import (
    RAW_DIR,
    read_only_permissions,
    write_protect_permissions,
)
from deep_wiki_agent.prompts import (
    DEFAULT_NOT_FOUND_MESSAGE,
    LINT_TOOL_BLOCK,
    MANAGER_SYSTEM_PROMPT_TEMPLATE,
    READER_SYSTEM_PROMPT_TEMPLATE,
    SEMANTIC_MANAGER_BLOCK,
    SEMANTIC_READER_BLOCK,
    STRUCTURED_OUTPUT_BLOCK_TEMPLATE,
)
from deep_wiki_agent.schemas import WikiAnswer
from deep_wiki_agent.tools import create_okf_lint_tool

if TYPE_CHECKING:
    from collections.abc import Sequence

    from deepagents.backends.protocol import BackendProtocol
    from deepagents.middleware.filesystem import FilesystemPermission
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import SystemMessage
    from langchain_core.tools import BaseTool
    from langchain_core.vectorstores import VectorStore
    from langgraph.graph.state import CompiledStateGraph

    from deep_wiki_agent.semantic import SemanticConfig, SemanticTools

__all__ = ["create_deep_wiki_agent", "create_wiki_manager_agent"]

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


def _build_semantic_tools(
    *,
    embeddings: Embeddings | None,
    vector_store: VectorStore | None,
    backend: BackendProtocol,
    search_k: int,
    semantic_config: SemanticConfig | None,
) -> SemanticTools | None:
    """Build the semantic tools, or nothing when semantic search is off.

    Args:
        embeddings: Caller-supplied embedding model, or ``None``.
        vector_store: Caller-supplied vector store, or ``None``.
        backend: The bundle's backend, captured by the tools so every read goes
            through the same tree the agent's file tools see.
        search_k: Default number of results a search returns.
        semantic_config: Index configuration, or ``None`` for the defaults.

    Returns:
        The tools, or ``None`` when neither an embedding model nor a store was
        given.

    Raises:
        ValueError: If exactly one of ``embeddings`` and ``vector_store`` is
            given — a half-configured index would fail on the first tool call
            instead of here.
        ImportError: If the optional ``semantic`` extra is not installed.
    """
    if embeddings is None and vector_store is None:
        return None
    if embeddings is None or vector_store is None:
        msg = (
            "semantic search needs both `embeddings` and `vector_store`; "
            f"got embeddings={embeddings is not None}, "
            f"vector_store={vector_store is not None}"
        )
        raise ValueError(msg)

    # Imported here, not at module scope: the extra is opt-in, and importing
    # this module must stay free for users who never enable semantic search.
    from deep_wiki_agent.semantic import create_semantic_tools  # noqa: PLC0415

    return create_semantic_tools(
        embeddings,
        vector_store,
        backend=backend,
        search_k=search_k,
        config=semantic_config,
    )


def create_wiki_manager_agent(
    *,
    model: str | BaseChatModel,
    wiki_path: str | Path | None = None,
    backend: BackendProtocol | None = None,
    create_if_missing: bool = True,
    protect_raw: bool = True,
    enable_lint_tool: bool = True,
    virtual_mode: bool = True,
    embeddings: Embeddings | None = None,
    vector_store: VectorStore | None = None,
    search_k: int = 5,
    semantic_config: SemanticConfig | None = None,
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
        enable_lint_tool: When ``True`` (default), attach the ``okf_lint`` tool
            so the agent can run the conformance check its prompt asks for (it
            has no shell). Works against any backend — local directory, state,
            store, or sandbox — since the linter walks it through the
            backend's own ``glob``/``read``/``edit`` methods.
        virtual_mode: Confine the filesystem backend to the bundle directory,
            blocking ``../`` and ``~/`` escapes. Leave enabled unless you have
            a specific reason not to.
        embeddings: Embedding model enabling semantic search. Given together
            with ``vector_store``, the agent gains ``semantic_ingest`` and
            ``semantic_search`` and a prompt section on keeping the index
            current. Requires the optional ``semantic`` extra.
        vector_store: Store the chunks are written to and searched in. A store
            configured for hybrid retrieval (dense + BM25) keeps its keyword
            half — the query reaches it as text rather than as a vector.
        search_k: Default number of passages a semantic search returns.
        semantic_config: Index configuration — chunking, which directories may
            be indexed, batch sizes, the manifest location. Defaults to
            :class:`~deep_wiki_agent.semantic.index.SemanticConfig`, which
            indexes the wiki's pages and the sources under ``/raw``.
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
        ValueError: If ``model`` is missing, if neither or both of
            ``wiki_path`` and ``backend`` are given, or if only one of
            ``embeddings`` and ``vector_store`` is given.
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

    agent_tools: list[BaseTool] = list(tools)
    if enable_lint_tool:
        agent_tools.append(create_okf_lint_tool(backend=effective_backend))

    semantic = _build_semantic_tools(
        embeddings=embeddings,
        vector_store=vector_store,
        backend=effective_backend,
        search_k=search_k,
        semantic_config=semantic_config,
    )
    if semantic is not None:
        # The manager is the agent that changes the bundle, so it is the one
        # that has to keep the index current: it gets both halves.
        agent_tools.extend(semantic.as_list())

    if system_prompt is None:
        system_prompt = MANAGER_SYSTEM_PROMPT_TEMPLATE.format(
            wiki_root=WIKI_ROOT,
            raw_dir=RAW_DIR,
            lint_block=LINT_TOOL_BLOCK if enable_lint_tool else "",
            semantic_block=SEMANTIC_MANAGER_BLOCK if semantic is not None else "",
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
    structured_output: bool = False,
    virtual_mode: bool = True,
    embeddings: Embeddings | None = None,
    vector_store: VectorStore | None = None,
    search_k: int = 5,
    semantic_config: SemanticConfig | None = None,
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
        structured_output: When ``True``, the agent answers with a
            :class:`~deep_wiki_agent.schemas.WikiAnswer` — ``answer``,
            ``citations``, ``not_covered``, ``found`` — found under
            ``result["structured_response"]``, and its prompt gains a section
            explaining how to fill those fields. This is what makes "the wiki
            does not cover this" testable as ``found is False`` instead of a
            string comparison against ``not_found_message``. The cost is the
            model's freedom to shape a prose answer to the question, which is
            why the free-text default stays the default. Mutually exclusive
            with passing your own ``response_format``.
        virtual_mode: Confine the filesystem backend to the bundle directory,
            blocking ``../`` and ``~/`` escapes.
        embeddings: Embedding model enabling semantic search. Given together
            with ``vector_store``, the agent gains ``semantic_search`` — and
            only that: the ingestion tool writes, and this agent is read-only
            by construction. Building and refreshing the index is the manager's
            job, or a job for
            :func:`~deep_wiki_agent.semantic.tools.ingest_semantic_index`.
            Requires the optional ``semantic`` extra.
        vector_store: Store holding the index to search. Must be the one the
            bundle was ingested into: this agent never writes to it.
        search_k: Default number of passages a semantic search returns.
        semantic_config: Index configuration. Only its search-side fields
            matter here (``over_fetch``, ``snippet_chars``, ``filter_builder``);
            the ingestion fields are unused, since this agent never ingests.
        system_prompt: Override for the built-in reader prompt. Note that the
            not-found contract, the query protocol and the field-filling
            instructions ``structured_output`` would add all live in that
            prompt: if you replace it, restate whichever you still want.
            The read-only *enforcement*, by contrast, is in the permissions and
            survives any prompt.
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
        ValueError: If ``model`` is missing, if neither or both of
            ``wiki_path`` and ``backend`` are given, if only one of
            ``embeddings`` and ``vector_store`` is given, or if
            ``structured_output`` is combined with an explicit
            ``response_format``.
        FileNotFoundError: If ``wiki_path`` does not exist.
    """
    _require_model(model, "create_deep_wiki_agent")

    if structured_output and "response_format" in create_deep_agent_kwargs:
        msg = (
            "pass either `structured_output=True` (the built-in WikiAnswer "
            "schema) or your own `response_format`, not both"
        )
        raise ValueError(msg)

    effective_backend = _resolve_backend(
        wiki_path=wiki_path,
        backend=backend,
        virtual_mode=virtual_mode,
        create_if_missing=False,
    )

    semantic = _build_semantic_tools(
        embeddings=embeddings,
        vector_store=vector_store,
        backend=effective_backend,
        search_k=search_k,
        semantic_config=semantic_config,
    )
    agent_tools: list[BaseTool] = list(tools)
    if semantic is not None:
        # Search only: the ingestion tool writes, and the read-only contract is
        # what this agent is for. The permissions would not stop it either —
        # they police the file tools, not a tool that talks to a vector store.
        agent_tools.append(semantic.search_tool)

    if system_prompt is None:
        system_prompt = READER_SYSTEM_PROMPT_TEMPLATE.format(
            wiki_root=WIKI_ROOT,
            raw_dir=RAW_DIR,
            not_found_message=not_found_message,
            semantic_block=SEMANTIC_READER_BLOCK if semantic is not None else "",
            structured_output_block=(
                STRUCTURED_OUTPUT_BLOCK_TEMPLATE.format(
                    wiki_root=WIKI_ROOT, raw_dir=RAW_DIR
                )
                if structured_output
                else ""
            ),
        )

    if structured_output:
        create_deep_agent_kwargs["response_format"] = WikiAnswer

    return create_deep_agent(
        model=model,
        tools=agent_tools,
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
