# API reference

## Factories

::: deep_wiki_agent.factory.create_wiki_manager_agent

::: deep_wiki_agent.factory.create_deep_wiki_agent

## Structured responses

::: deep_wiki_agent.schemas.WikiAnswer

Opt in with `create_deep_wiki_agent(..., structured_output=True)`. The answer
then arrives as a `WikiAnswer` under `result["structured_response"]`, and the
reader's prompt gains a section explaining how the fields map onto the
not-found contract.

The value is that `found is False` replaces a string comparison against
`not_found_message`, which stops being a reliable test the moment the message
is reworded or translated. The cost is the model's freedom to shape a prose
answer to the question, which is why free text remains the default.

The flag sets `response_format` on the underlying deep agent, so it is mutually
exclusive with passing `response_format` yourself — a `ValueError` if you pass
both. Passing your own schema that way remains supported, and skips the prompt
section, since that section describes `WikiAnswer`'s fields specifically.

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
| `BUNDLE_SKELETON` | see below | the layout the manager bootstraps |
| `DEFAULT_NOT_FOUND_MESSAGE` | see below | the reader's not-found answer |

```python
BUNDLE_SKELETON = (
    "AGENTS.md",
    "raw/",
    "wiki/index.md",
    "wiki/log.md",
    "wiki/assets/",
    "wiki/documents/",
    "wiki/entities/",
    "wiki/concepts/",
    "wiki/syntheses/",
)

DEFAULT_NOT_FOUND_MESSAGE = (
    "I could not find the requested information in the wiki knowledge base."
)
```

`BUNDLE_SKELETON` is the same layout section 1 of both prompts draws, as
bundle-relative paths — directories carry a trailing slash, and each category
directory also holds its own `index.md`. Use it to pre-create a bundle, or to
check one you were handed; `tests/test_prompt_paths.py` uses it to verify that
every path the prompts cite exists in the layout they describe.

## Prompt templates

`MANAGER_SYSTEM_PROMPT_TEMPLATE` and `READER_SYSTEM_PROMPT_TEMPLATE` carry the
agents' full operating instructions — bundle layout, frontmatter conformance,
the workflows. They are exported so you can inspect or extend them rather than
rewriting from scratch. They are `str.format` templates:

| Template | Placeholders |
|---|---|
| `MANAGER_SYSTEM_PROMPT_TEMPLATE` | `wiki_root`, `raw_dir`, `lint_block` |
| `READER_SYSTEM_PROMPT_TEMPLATE` | `wiki_root`, `raw_dir`, `not_found_message`, `structured_output_block` |

`lint_block` is filled with `LINT_TOOL_BLOCK` when the `okf_lint` tool is
attached, and with an empty string otherwise. `structured_output_block` works
the same way: `STRUCTURED_OUTPUT_BLOCK_TEMPLATE` when `structured_output=True`,
an empty string otherwise. Both blocks are plain strings, so an omitted one
costs the agent nothing.

`STRUCTURED_OUTPUT_BLOCK_TEMPLATE` is itself a `str.format` template taking
`wiki_root` and `raw_dir` — render it before substituting it in, since
`str.format` does not recurse into the values it interpolates.

```python
from deep_wiki_agent import READER_SYSTEM_PROMPT_TEMPLATE

prompt = READER_SYSTEM_PROMPT_TEMPLATE.format(
    wiki_root="/",
    raw_dir="/raw",
    not_found_message="Nothing found in the knowledge base.",
    structured_output_block="",
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
| `scripts/okf_lint.py` inside the installed skill | the `okf-lint` console script, or `python -m deep_wiki_agent.okf_lint` |
