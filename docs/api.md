# API reference

## Factories

::: deep_wiki_agent.factory.create_wiki_manager_agent

::: deep_wiki_agent.factory.create_deep_wiki_agent

## Permissions

::: deep_wiki_agent.backends.read_only_permissions

::: deep_wiki_agent.backends.write_protect_permissions

## Linting

::: deep_wiki_agent.tools.lint.create_okf_lint_tool

::: deep_wiki_agent.tools.lint.run_okf_lint

::: deep_wiki_agent.okf_lint.lint

## Constants

| Name | Value | Meaning |
|---|---|---|
| `WIKI_ROOT` | `"/"` | mount point of the OKF bundle |
| `RAW_DIR` | `"/raw"` | the immutable source-document directory |
| `DEFAULT_NOT_FOUND_MESSAGE` | see below | the reader's not-found answer |

```python
DEFAULT_NOT_FOUND_MESSAGE = (
    "I could not find the requested information in the wiki knowledge base."
)
```

## Prompt templates

`MANAGER_SYSTEM_PROMPT_TEMPLATE` and `READER_SYSTEM_PROMPT_TEMPLATE` carry the
agents' full operating instructions — bundle layout, frontmatter conformance,
the workflows. They are exported so you can inspect or extend them rather than
rewriting from scratch. They are `str.format` templates:

| Template | Placeholders |
|---|---|
| `MANAGER_SYSTEM_PROMPT_TEMPLATE` | `wiki_root`, `raw_dir`, `lint_block` |
| `READER_SYSTEM_PROMPT_TEMPLATE` | `wiki_root`, `not_found_message` |

`lint_block` is filled with `LINT_TOOL_BLOCK` when the `okf_lint` tool is
attached, and with an empty string otherwise.

```python
from deep_wiki_agent import READER_SYSTEM_PROMPT_TEMPLATE

prompt = READER_SYSTEM_PROMPT_TEMPLATE.format(
    wiki_root="/",
    not_found_message="Nothing found in the knowledge base.",
) + "\n\nAlways answer in Italian."
```

!!! warning
    Passing `system_prompt` to either factory replaces the built-in
    instructions wholesale — for the reader, that includes the not-found
    contract and the query protocol, so restate what you still want in force.
    The read-only *enforcement* is separate: it lives in the filesystem
    permissions and survives any prompt.

## Migrating from 0.1.x

| Removed | Replacement |
|---|---|
| `build_wiki_backend` | `FilesystemBackend(root_dir=wiki_path, virtual_mode=True)` |
| `normalize_mount` | — |
| `bundled_skills_dir`, `okf_wiki_skill_dir`, `okf_lint_script` | — |
| `OKF_WIKI_SKILL_NAME`, `DEFAULT_SKILLS_MOUNT` | — |
| `skills_mount`, `skills_dir`, `extra_skills` | `system_prompt=` to change the instructions; `create_deep_agent`'s own `skills=` passthrough for genuinely extra skills |
| `scripts/okf_lint.py` inside the installed skill | `python -m deep_wiki_agent.okf_lint` |
