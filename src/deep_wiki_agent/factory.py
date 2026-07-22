"""Factories for the two OKF wiki agents.

- :func:`create_wiki_manager_agent` — writes: ingests documents into an OKF
  bundle, keeps pages, links, indexes and log in sync, lints the result.
- :func:`create_deep_wiki_agent` — reads: answers questions strictly from an
  existing bundle, and says so when the bundle does not cover the question.

Both are thin wrappers over ``deepagents.create_deep_agent``. What they add is
the wiring that makes an OKF bundle usable by an agent: a composite backend
that mounts the bundle at the virtual root and the ``okf-wiki`` skill next to
it, a system prompt that delegates the domain rules to that skill, filesystem
permissions that enforce immutability where the format requires it, and (for
the manager) a runnable conformance linter.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import create_deep_agent

from deep_wiki_agent.backends import (
    DEFAULT_SKILLS_MOUNT,
    RAW_DIR,
    build_wiki_backend,
    normalize_mount,
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
from deep_wiki_agent.resources import OKF_WIKI_SKILL_NAME
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

WIKI_ROOT = "/"
"""Mount point of the OKF bundle on the agent's virtual filesystem."""


def _skill_paths(skills_mount: str) -> tuple[str, str, str]:
    """Return ``(mount, skill_dir, skill_file)`` for the bundled skill.

    Args:
        skills_mount: Mount point of the skills tree, in any accepted form.

    Returns:
        The normalized mount (``/skills/``), the skill directory
        (``/skills/okf-wiki``), and its SKILL.md (``/skills/okf-wiki/SKILL.md``).
    """
    mount = normalize_mount(skills_mount)
    skill_dir = f"{mount}{OKF_WIKI_SKILL_NAME}"
    return mount, skill_dir, f"{skill_dir}/SKILL.md"


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
    skills_mount: str = DEFAULT_SKILLS_MOUNT,
    skills_dir: str | Path | None = None,
    extra_skills: Sequence[str] = (),
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
    ``wiki_path`` and loads the bundled ``okf-wiki`` skill for its operating
    instructions: bundle structure, frontmatter conformance, and the ingest /
    query / lint / bootstrap workflows. The system prompt states the agent's
    purpose and directs it to read the skill before acting; it deliberately
    does not restate the skill's content, so the skill remains the single
    source of truth for how the wiki is maintained.

    Args:
        model: Model for the agent. Required — either a provider string
            (e.g. ``"anthropic:claude-sonnet-5"``) or a ``BaseChatModel``.
        wiki_path: Local directory of the OKF bundle, mounted at the virtual
            root. Required unless ``backend`` is given.
        backend: Pre-built backend, used verbatim instead of the one this
            factory would assemble. Use it for non-local storage (state,
            store, sandbox). You are then responsible for mounting the skills
            tree at ``skills_mount`` yourself — otherwise the agent has no
            instructions to load. Mutually exclusive with ``wiki_path``.
        skills_mount: Where the skills tree is mounted. The skill is then at
            ``<skills_mount>/okf-wiki/SKILL.md``, which the prompt references.
        skills_dir: Directory containing skill directories. Defaults to the
            skills bundled with this package. Pass your own to replace the
            ``okf-wiki`` instructions with a customized variant, or to add
            domain skills alongside it.
        extra_skills: Additional skill source paths, as seen on the agent's
            virtual filesystem, registered alongside ``skills_mount``.
        create_if_missing: When ``True`` (default), ``wiki_path`` is created
            if it does not exist, so the agent can bootstrap a new bundle into
            it. When ``False``, a missing directory raises.
        protect_raw: When ``True`` (default), writes under ``/raw`` are denied
            at the tool boundary. Source documents are immutable in the OKF
            wiki convention, and this makes that structural rather than a
            matter of the model's compliance. Writes to the skills mount are
            always denied.
        enable_lint_tool: When ``True`` (default) and the bundle resolves to a
            local directory, attach the ``okf_lint`` tool so the agent can run
            the conformance check the skill asks for (it has no shell to run
            ``scripts/okf_lint.py`` with). Silently skipped for backends that
            are not filesystem-backed.
        virtual_mode: Confine each filesystem backend to its root directory,
            blocking ``../`` and ``~/`` escapes. Leave enabled unless you have
            a specific reason not to.
        system_prompt: Override for the built-in manager prompt. Overriding it
            means you take responsibility for telling the agent to load the
            skill; the wiring is otherwise unchanged.
        tools: Extra tools, added alongside ``okf_lint`` and the built-in
            deepagents tools. This is where document loaders (PDF, docx, URL
            fetchers) belong — the library ships none, since which formats a
            wiki ingests is domain-specific.
        permissions: Override for the computed filesystem permissions. Passing
            this replaces the ``raw``/skills protection entirely.
        middleware: Extra middleware passed through to ``create_deep_agent``.
        **create_deep_agent_kwargs: Any remaining ``create_deep_agent``
            parameter (``subagents``, ``checkpointer``, ``store``,
            ``interrupt_on``, ``response_format``, ...), passed unchanged.

    Returns:
        The compiled deep agent graph, ready for ``invoke`` / ``astream``.

    Raises:
        ValueError: If ``model`` is missing, if neither or both of
            ``wiki_path`` and ``backend`` are given, or if ``skills_mount``
            is empty or the root path.
        FileNotFoundError: If ``wiki_path`` does not exist and
            ``create_if_missing`` is ``False``.
    """
    _require_model(model, "create_wiki_manager_agent")
    mount, skill_dir, skill_file = _skill_paths(skills_mount)

    effective_backend = _resolve_backend(
        wiki_path=wiki_path,
        backend=backend,
        skills_mount=mount,
        skills_dir=skills_dir,
        virtual_mode=virtual_mode,
        create_if_missing=create_if_missing,
    )

    # The linter walks a real directory, so it is only attachable when the
    # bundle is filesystem-backed. For other backends the skill's script has
    # to be run out of band by the caller.
    agent_tools: list[BaseTool] = list(tools)
    if enable_lint_tool:
        local_root = resolve_local_wiki_path(effective_backend)
        if local_root is not None:
            agent_tools.append(create_okf_lint_tool(local_root))
    lint_block = LINT_TOOL_BLOCK if len(agent_tools) > len(tools) else ""

    if system_prompt is None:
        system_prompt = MANAGER_SYSTEM_PROMPT_TEMPLATE.format(
            wiki_root=WIKI_ROOT,
            raw_dir=RAW_DIR,
            skill_name=OKF_WIKI_SKILL_NAME,
            skill_path=skill_dir,
            skill_file=skill_file,
            lint_block=lint_block,
        )

    if permissions is None:
        protected = [mount]
        if protect_raw:
            protected.append(RAW_DIR)
        permissions = write_protect_permissions(protected)

    return create_deep_agent(
        model=model,
        tools=agent_tools,
        system_prompt=system_prompt,
        backend=effective_backend,
        skills=[mount, *extra_skills],
        permissions=permissions,
        middleware=middleware,
        **create_deep_agent_kwargs,
    )


def create_deep_wiki_agent(
    *,
    model: str | BaseChatModel,
    wiki_path: str | Path | None = None,
    backend: BackendProtocol | None = None,
    skills_mount: str = DEFAULT_SKILLS_MOUNT,
    skills_dir: str | Path | None = None,
    extra_skills: Sequence[str] = (),
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
    indexes and the link graph as the ``okf-wiki`` skill's *Query* section
    prescribes, cites the pages it used, and — when the bundle does not cover
    the question — replies with ``not_found_message`` instead of falling back
    on the model's own knowledge.

    Args:
        model: Model for the agent. Required — either a provider string
            (e.g. ``"anthropic:claude-sonnet-5"``) or a ``BaseChatModel``.
        wiki_path: Local directory of the OKF bundle to consult, mounted at
            the virtual root. Required unless ``backend`` is given. Must
            already exist: this agent never creates a bundle.
        backend: Pre-built backend, used verbatim instead of the one this
            factory would assemble — for a bundle held in a store, a sandbox,
            or any non-local storage. You are then responsible for mounting
            the skills tree at ``skills_mount``. Mutually exclusive with
            ``wiki_path``.
        skills_mount: Where the skills tree is mounted. The skill is then at
            ``<skills_mount>/okf-wiki/SKILL.md``, which the prompt references.
        skills_dir: Directory containing skill directories. Defaults to the
            skills bundled with this package.
        extra_skills: Additional skill source paths, as seen on the agent's
            virtual filesystem, registered alongside ``skills_mount``.
        not_found_message: The exact sentence the agent must return when the
            bundle does not contain the requested information. Override it to
            match your product's voice or language; the surrounding contract
            (no guessing, no outside knowledge, partial answers allowed and
            labelled) is unchanged.
        virtual_mode: Confine each filesystem backend to its root directory,
            blocking ``../`` and ``~/`` escapes.
        system_prompt: Override for the built-in reader prompt. Note that the
            not-found contract lives in that prompt: if you replace it, you
            must restate the contract yourself. The read-only *enforcement*,
            by contrast, is in the permissions and survives any prompt.
        tools: Extra read-only tools. Do not pass tools that can write to the
            bundle: the filesystem permissions cannot police tools they do not
            mediate.
        permissions: Override for the read-only permission set. Passing this
            replaces the deny-all-writes rule — the agent is then only as
            read-only as your rules make it.
        middleware: Extra middleware passed through to ``create_deep_agent``.
        **create_deep_agent_kwargs: Any remaining ``create_deep_agent``
            parameter (``subagents``, ``checkpointer``, ``store``,
            ``response_format``, ...), passed unchanged.

    Returns:
        The compiled deep agent graph, ready for ``invoke`` / ``astream``.

    Raises:
        ValueError: If ``model`` is missing, if neither or both of
            ``wiki_path`` and ``backend`` are given, or if ``skills_mount``
            is empty or the root path.
        FileNotFoundError: If ``wiki_path`` does not exist.
    """
    _require_model(model, "create_deep_wiki_agent")
    mount, skill_dir, skill_file = _skill_paths(skills_mount)

    effective_backend = _resolve_backend(
        wiki_path=wiki_path,
        backend=backend,
        skills_mount=mount,
        skills_dir=skills_dir,
        virtual_mode=virtual_mode,
        create_if_missing=False,
    )

    if system_prompt is None:
        system_prompt = READER_SYSTEM_PROMPT_TEMPLATE.format(
            wiki_root=WIKI_ROOT,
            skill_name=OKF_WIKI_SKILL_NAME,
            skill_path=skill_dir,
            skill_file=skill_file,
            not_found_message=not_found_message,
        )

    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        backend=effective_backend,
        skills=[mount, *extra_skills],
        permissions=(read_only_permissions() if permissions is None else permissions),
        middleware=middleware,
        **create_deep_agent_kwargs,
    )


def _resolve_backend(
    *,
    wiki_path: str | Path | None,
    backend: BackendProtocol | None,
    skills_mount: str,
    skills_dir: str | Path | None,
    virtual_mode: bool,
    create_if_missing: bool,
) -> BackendProtocol:
    """Return the backend to hand to ``create_deep_agent``.

    Args:
        wiki_path: Local bundle directory, or ``None`` when ``backend`` is given.
        backend: Caller-supplied backend, propagated unchanged when present.
        skills_mount: Normalized skills mount point.
        skills_dir: Skills tree to mount, or ``None`` for the bundled one.
        virtual_mode: Whether to confine the filesystem backends to their roots.
        create_if_missing: Whether to create ``wiki_path`` when absent.

    Returns:
        Either the caller's backend or a freshly built composite backend.

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

    return build_wiki_backend(
        root,
        skills_mount=skills_mount,
        skills_dir=skills_dir,
        virtual_mode=virtual_mode,
    )
