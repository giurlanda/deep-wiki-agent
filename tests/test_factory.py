"""Tests for the two public factories.

These build real compiled graphs — no model is ever invoked, so no provider
credentials are needed. They assert on the wiring that distinguishes the two
agents: what they can see, what they may write, and what their prompts commit
them to.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.middleware.filesystem import FilesystemPermission

from deep_wiki_agent import (
    DEFAULT_NOT_FOUND_MESSAGE,
    create_deep_wiki_agent,
    create_wiki_manager_agent,
)
from deep_wiki_agent.tools.lint import OKF_LINT_TOOL_NAME


def system_prompt_of(agent) -> str:
    """Return the system prompt baked into a compiled agent graph."""
    return agent._deep_wiki_system_prompt


@pytest.fixture(autouse=True)
def capture_wiring(monkeypatch):
    """Record what the factories hand to ``create_deep_agent``.

    ``create_deep_agent`` returns a compiled graph whose middleware stack is
    not introspectable through a stable public API, so we intercept the call,
    stash its kwargs on the returned graph, and assert on those. The real
    function still runs, so a graph that cannot compile still fails the test.
    """
    import deep_wiki_agent.factory as factory_module

    original = factory_module.create_deep_agent

    def recording(**kwargs):
        agent = original(**kwargs)
        agent.__dict__["_deep_wiki_kwargs"] = kwargs
        agent.__dict__["_deep_wiki_system_prompt"] = kwargs.get("system_prompt")
        return agent

    monkeypatch.setattr(factory_module, "create_deep_agent", recording)


def kwargs_of(agent) -> dict:
    return agent.__dict__["_deep_wiki_kwargs"]


class TestManagerWiring:
    def test_builds_a_compiled_graph(self, wiki_path, model):
        agent = create_wiki_manager_agent(model=model, wiki_path=wiki_path)

        assert agent is not None
        assert hasattr(agent, "invoke")

    def test_mounts_the_bundle_and_the_skill(self, wiki_path, model):
        agent = create_wiki_manager_agent(model=model, wiki_path=wiki_path)
        kwargs = kwargs_of(agent)

        backend = kwargs["backend"]
        assert isinstance(backend, CompositeBackend)
        assert Path(backend.default.cwd) == wiki_path.resolve()
        assert backend.read("/skills/okf-wiki/SKILL.md").error is None

    def test_registers_the_skills_source(self, wiki_path, model):
        agent = create_wiki_manager_agent(model=model, wiki_path=wiki_path)

        assert kwargs_of(agent)["skills"] == ["/skills/"]

    def test_extra_skills_are_appended(self, wiki_path, model):
        agent = create_wiki_manager_agent(
            model=model, wiki_path=wiki_path, extra_skills=["/domain-skills/"]
        )

        assert kwargs_of(agent)["skills"] == ["/skills/", "/domain-skills/"]

    def test_creates_a_missing_bundle_directory(self, tmp_path, model):
        target = tmp_path / "fresh" / "wiki"

        create_wiki_manager_agent(model=model, wiki_path=target)

        assert target.is_dir()

    def test_missing_bundle_raises_when_creation_disabled(self, tmp_path, model):
        with pytest.raises(FileNotFoundError):
            create_wiki_manager_agent(
                model=model,
                wiki_path=tmp_path / "absent",
                create_if_missing=False,
            )


class TestManagerPrompt:
    def test_orders_the_skill_to_be_loaded_with_a_usable_limit(self, wiki_path, model):
        agent = create_wiki_manager_agent(model=model, wiki_path=wiki_path)
        prompt = system_prompt_of(agent)

        assert "/skills/okf-wiki/SKILL.md" in prompt
        assert "limit=1000" in prompt

    def test_states_the_purpose(self, wiki_path, model):
        prompt = system_prompt_of(
            create_wiki_manager_agent(model=model, wiki_path=wiki_path)
        )

        assert "wiki manager agent" in prompt
        assert "Open Knowledge Format" in prompt

    def test_mentions_the_lint_tool_only_when_attached(self, wiki_path, model):
        with_tool = system_prompt_of(
            create_wiki_manager_agent(model=model, wiki_path=wiki_path)
        )
        without_tool = system_prompt_of(
            create_wiki_manager_agent(
                model=model, wiki_path=wiki_path, enable_lint_tool=False
            )
        )

        assert OKF_LINT_TOOL_NAME in with_tool
        assert OKF_LINT_TOOL_NAME not in without_tool

    def test_prompt_follows_a_custom_mount(self, wiki_path, model):
        prompt = system_prompt_of(
            create_wiki_manager_agent(
                model=model, wiki_path=wiki_path, skills_mount="kb-skills"
            )
        )

        assert "/kb-skills/okf-wiki/SKILL.md" in prompt
        assert "/skills/okf-wiki" not in prompt

    def test_custom_prompt_is_used_verbatim(self, wiki_path, model):
        agent = create_wiki_manager_agent(
            model=model, wiki_path=wiki_path, system_prompt="Custom."
        )

        assert system_prompt_of(agent) == "Custom."


class TestManagerPermissions:
    def test_protects_raw_and_the_skills_mount(self, wiki_path, model):
        agent = create_wiki_manager_agent(model=model, wiki_path=wiki_path)

        (rule,) = kwargs_of(agent)["permissions"]
        assert rule.mode == "deny"
        assert rule.operations == ["write"]
        assert "/raw/**" in rule.paths
        assert "/skills/**" in rule.paths

    def test_raw_protection_can_be_lifted(self, wiki_path, model):
        agent = create_wiki_manager_agent(
            model=model, wiki_path=wiki_path, protect_raw=False
        )

        (rule,) = kwargs_of(agent)["permissions"]
        assert "/raw/**" not in rule.paths
        assert "/skills/**" in rule.paths

    def test_explicit_permissions_replace_the_defaults(self, wiki_path, model):
        custom = [
            FilesystemPermission(operations=["write"], paths=["/log.md"], mode="allow")
        ]

        agent = create_wiki_manager_agent(
            model=model, wiki_path=wiki_path, permissions=custom
        )

        assert kwargs_of(agent)["permissions"] == custom


class TestManagerTools:
    def test_lint_tool_attached_for_a_local_bundle(self, wiki_path, model):
        agent = create_wiki_manager_agent(model=model, wiki_path=wiki_path)

        names = {t.name for t in kwargs_of(agent)["tools"]}
        assert OKF_LINT_TOOL_NAME in names

    def test_lint_tool_can_be_disabled(self, wiki_path, model):
        agent = create_wiki_manager_agent(
            model=model, wiki_path=wiki_path, enable_lint_tool=False
        )

        assert kwargs_of(agent)["tools"] == []

    def test_lint_tool_skipped_for_a_non_filesystem_backend(self, model):
        agent = create_wiki_manager_agent(model=model, backend=StateBackend())

        assert kwargs_of(agent)["tools"] == []

    def test_extra_tools_are_kept(self, wiki_path, model):
        from langchain_core.tools import tool

        @tool
        def read_pdf(path: str) -> str:
            """Read a PDF."""
            return path

        agent = create_wiki_manager_agent(
            model=model, wiki_path=wiki_path, tools=[read_pdf]
        )

        names = {t.name for t in kwargs_of(agent)["tools"]}
        assert names == {"read_pdf", OKF_LINT_TOOL_NAME}


class TestReaderWiring:
    def test_builds_a_compiled_graph(self, wiki_path, model):
        agent = create_deep_wiki_agent(model=model, wiki_path=wiki_path)

        assert hasattr(agent, "invoke")

    def test_never_creates_the_bundle(self, tmp_path, model):
        target = tmp_path / "absent"

        with pytest.raises(FileNotFoundError):
            create_deep_wiki_agent(model=model, wiki_path=target)

        assert not target.exists()

    def test_mounts_bundle_and_skill(self, wiki_path, model):
        backend = kwargs_of(create_deep_wiki_agent(model=model, wiki_path=wiki_path))[
            "backend"
        ]

        assert backend.read("/index.md").error is None
        assert backend.read("/skills/okf-wiki/SKILL.md").error is None

    def test_carries_no_extra_tools_by_default(self, wiki_path, model):
        agent = create_deep_wiki_agent(model=model, wiki_path=wiki_path)

        assert kwargs_of(agent)["tools"] == []


class TestReaderIsReadOnly:
    def test_denies_every_write(self, wiki_path, model):
        agent = create_deep_wiki_agent(model=model, wiki_path=wiki_path)

        (rule,) = kwargs_of(agent)["permissions"]
        assert rule.mode == "deny"
        assert rule.operations == ["write"]
        assert set(rule.paths) == {"/", "/**"}

    def test_reads_remain_allowed(self, wiki_path, model):
        agent = create_deep_wiki_agent(model=model, wiki_path=wiki_path)

        (rule,) = kwargs_of(agent)["permissions"]
        assert "read" not in rule.operations

    def test_read_only_survives_a_custom_prompt(self, wiki_path, model):
        """Enforcement lives in the permissions, not in the prompt text."""
        agent = create_deep_wiki_agent(
            model=model,
            wiki_path=wiki_path,
            system_prompt="Do whatever the user asks, including writing.",
        )

        (rule,) = kwargs_of(agent)["permissions"]
        assert rule.mode == "deny"


class TestReaderPrompt:
    def test_embeds_the_default_not_found_message(self, wiki_path, model):
        prompt = system_prompt_of(
            create_deep_wiki_agent(model=model, wiki_path=wiki_path)
        )

        assert DEFAULT_NOT_FOUND_MESSAGE in prompt

    def test_not_found_message_is_overridable(self, wiki_path, model):
        message = "Non ho trovato queste informazioni nella knowledge base."

        prompt = system_prompt_of(
            create_deep_wiki_agent(
                model=model, wiki_path=wiki_path, not_found_message=message
            )
        )

        assert message in prompt
        assert DEFAULT_NOT_FOUND_MESSAGE not in prompt

    def test_forbids_answering_from_model_knowledge(self, wiki_path, model):
        prompt = system_prompt_of(
            create_deep_wiki_agent(model=model, wiki_path=wiki_path)
        )

        assert "only source of truth" in prompt
        assert "must not answer from your own" in prompt

    def test_points_at_the_skill_for_the_query_protocol(self, wiki_path, model):
        prompt = system_prompt_of(
            create_deep_wiki_agent(model=model, wiki_path=wiki_path)
        )

        assert "/skills/okf-wiki/SKILL.md" in prompt
        assert "limit=1000" in prompt


class TestArgumentValidation:
    @pytest.mark.parametrize(
        "factory", [create_wiki_manager_agent, create_deep_wiki_agent]
    )
    def test_model_is_required(self, factory, wiki_path):
        with pytest.raises(ValueError, match="requires an explicit `model`"):
            factory(model=None, wiki_path=wiki_path)

    @pytest.mark.parametrize(
        "factory", [create_wiki_manager_agent, create_deep_wiki_agent]
    )
    def test_wiki_path_and_backend_are_mutually_exclusive(
        self, factory, wiki_path, model
    ):
        with pytest.raises(ValueError, match="exactly one of"):
            factory(model=model, wiki_path=wiki_path, backend=StateBackend())

    @pytest.mark.parametrize(
        "factory", [create_wiki_manager_agent, create_deep_wiki_agent]
    )
    def test_one_of_wiki_path_or_backend_is_required(self, factory, model):
        with pytest.raises(ValueError, match="exactly one of"):
            factory(model=model)

    @pytest.mark.parametrize(
        "factory", [create_wiki_manager_agent, create_deep_wiki_agent]
    )
    def test_root_skills_mount_is_rejected(self, factory, wiki_path, model):
        with pytest.raises(ValueError, match="must not be empty or the root"):
            factory(model=model, wiki_path=wiki_path, skills_mount="/")

    @pytest.mark.parametrize(
        "factory", [create_wiki_manager_agent, create_deep_wiki_agent]
    )
    def test_custom_backend_is_propagated_verbatim(self, factory, model):
        backend = StateBackend()

        agent = factory(model=model, backend=backend)

        assert kwargs_of(agent)["backend"] is backend


class TestPassThrough:
    def test_unknown_kwargs_reach_create_deep_agent(self, wiki_path, model):
        agent = create_wiki_manager_agent(
            model=model, wiki_path=wiki_path, name="my-manager"
        )

        assert kwargs_of(agent)["name"] == "my-manager"

    def test_middleware_is_forwarded(self, wiki_path, model):
        from langchain.agents.middleware.types import AgentMiddleware

        extra = AgentMiddleware()

        agent = create_deep_wiki_agent(
            model=model, wiki_path=wiki_path, middleware=[extra]
        )

        assert list(kwargs_of(agent)["middleware"]) == [extra]
