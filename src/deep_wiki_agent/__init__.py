"""Deep agents that maintain and consult an OKF wiki knowledge base.

Two factories, one knowledge base:

```python
from deep_wiki_agent import create_deep_wiki_agent, create_wiki_manager_agent

manager = create_wiki_manager_agent(
    model="anthropic:claude-sonnet-5", wiki_path="./my-wiki"
)
reader = create_deep_wiki_agent(
    model="anthropic:claude-sonnet-5", wiki_path="./my-wiki"
)
```

The bundle format is the Open Knowledge Format v0.1: markdown pages with YAML
frontmatter, linked into a graph. The domain rules live in the agents' system
prompts, in force from the first turn.
"""

from deep_wiki_agent.backends import (
    RAW_DIR,
    read_only_permissions,
    write_protect_permissions,
)
from deep_wiki_agent.factory import (
    WIKI_ROOT,
    create_deep_wiki_agent,
    create_wiki_manager_agent,
)
from deep_wiki_agent.prompts import (
    BUNDLE_SKELETON,
    DEFAULT_NOT_FOUND_MESSAGE,
    MANAGER_SYSTEM_PROMPT_TEMPLATE,
    READER_SYSTEM_PROMPT_TEMPLATE,
)
from deep_wiki_agent.schemas import WikiAnswer
from deep_wiki_agent.tools import create_okf_lint_tool, run_okf_lint

__version__ = "0.5.0"

__all__ = [
    "BUNDLE_SKELETON",
    "DEFAULT_NOT_FOUND_MESSAGE",
    "MANAGER_SYSTEM_PROMPT_TEMPLATE",
    "RAW_DIR",
    "READER_SYSTEM_PROMPT_TEMPLATE",
    "WIKI_ROOT",
    "WikiAnswer",
    "__version__",
    "create_deep_wiki_agent",
    "create_okf_lint_tool",
    "create_wiki_manager_agent",
    "read_only_permissions",
    "run_okf_lint",
    "write_protect_permissions",
]
