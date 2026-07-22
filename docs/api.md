# API reference

## Factories

::: deep_wiki_agent.factory.create_wiki_manager_agent

::: deep_wiki_agent.factory.create_deep_wiki_agent

## Backends and permissions

::: deep_wiki_agent.backends.build_wiki_backend

::: deep_wiki_agent.backends.read_only_permissions

::: deep_wiki_agent.backends.write_protect_permissions

::: deep_wiki_agent.backends.normalize_mount

## Linting

::: deep_wiki_agent.tools.lint.create_okf_lint_tool

::: deep_wiki_agent.tools.lint.run_okf_lint

## Locating the bundled skill

::: deep_wiki_agent.resources.bundled_skills_dir

::: deep_wiki_agent.resources.okf_wiki_skill_dir

::: deep_wiki_agent.resources.okf_lint_script

## Constants

| Name | Value | Meaning |
|---|---|---|
| `WIKI_ROOT` | `"/"` | mount point of the OKF bundle |
| `DEFAULT_SKILLS_MOUNT` | `"/skills"` | mount point of the skills tree |
| `RAW_DIR` | `"/raw"` | the immutable source-document directory |
| `OKF_WIKI_SKILL_NAME` | `"okf-wiki"` | directory name of the bundled skill |
| `DEFAULT_NOT_FOUND_MESSAGE` | see below | the reader's not-found answer |

```python
DEFAULT_NOT_FOUND_MESSAGE = (
    "I could not find the requested information in the wiki knowledge base."
)
```

## Prompt templates

`MANAGER_SYSTEM_PROMPT_TEMPLATE` and `READER_SYSTEM_PROMPT_TEMPLATE` are
exported so you can inspect or extend them rather than rewriting from scratch.
They are `str.format` templates:

| Template | Placeholders |
|---|---|
| `MANAGER_SYSTEM_PROMPT_TEMPLATE` | `wiki_root`, `raw_dir`, `skill_name`, `skill_path`, `skill_file`, `lint_block` |
| `READER_SYSTEM_PROMPT_TEMPLATE` | `wiki_root`, `skill_name`, `skill_path`, `skill_file`, `not_found_message` |

```python
from deep_wiki_agent import READER_SYSTEM_PROMPT_TEMPLATE

prompt = READER_SYSTEM_PROMPT_TEMPLATE.format(
    wiki_root="/",
    skill_name="okf-wiki",
    skill_path="/skills/okf-wiki",
    skill_file="/skills/okf-wiki/SKILL.md",
    not_found_message="Nothing found in the knowledge base.",
) + "\n\nAlways answer in Italian."
```

!!! warning
    Passing `system_prompt` to `create_deep_wiki_agent` replaces the not-found
    contract, so restate it. The read-only *enforcement* is separate — it lives
    in the filesystem permissions and survives any prompt.
