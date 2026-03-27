# Tool Definitions

This file is part of Bootstrap context and is authoritative for tool behavior.

## Python Calling Signatures

```python
from typing import Any


# All workspace tools return a ToolResult-shaped dict.
# IMPORTANT:
# - Pass arguments as keyword arguments.
# - Prefer these tools over Python file I/O for workspace files.
# - Check `result["is_error"]` before using `result["content"]`.
# - Text payloads live in `result["content"][i]["text"]`.
# - Image payloads use `result["content"][i]["mime_type"]` and `result["content"][i]["data_base64"]`.

# Shared return schema used by read/write/edit/grep/find/ls:
# {
#   "call_id": "string",
#   "tool_name": "string",
#   "content": [
#     {"type": "text", "text": "string"}
#     | {"type": "image", "mime_type": "string", "data_base64": "string"}
#   ],
#   "details": {
#     "truncation": {
#       "truncated": true,
#       "truncatedBy": "lines" | "bytes" | null,
#       "totalLines": int,
#       "totalBytes": int,
#       "outputLines": int,
#       "outputBytes": int,
#       "firstLineExceedsLimit": bool,
#       "maxLines": int,
#       "maxBytes": int
#     } | null,
#     "entryLimitReached": int | null,
#     "resultLimitReached": int | null,
#     "matchLimitReached": int | null,
#     "linesTruncated": bool | null,
#     "diff": "string" | null,
#     "firstChangedLine": int | null
#   } | null,
#   "is_error": bool,
#   "error": {"code": "string", "message": "string"} | null
# }


def read(path: str, offset: int = 1, limit: int = 2000) -> dict:
    """Read a file from the workspace.

    Supports UTF-8 text and common image files. For images, `content` contains:
    1. a text description item
    2. an image item with `mime_type` and `data_base64`

    Text output is windowed by `offset`/`limit`, then capped to 2000 lines / 50KB.
    If truncated, follow the continuation hint in the returned text and/or inspect
    `result["details"]["truncation"]`.
    """


def write(path: str, content: str) -> dict:
    """Write full UTF-8 text content to a workspace file.

    Creates parent directories when needed. On success, `content` contains one text
    item describing bytes written and target path.
    """


def edit(path: str, old_text: str, new_text: str) -> dict:
    """Replace one exact text span in a workspace file.

    `old_text` must be non-empty. On success, `details` may include `diff` and
    `firstChangedLine`.
    """


def grep(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    ignore_case: bool = False,
    literal: bool = False,
    context: int = 0,
    limit: int = 100,
) -> dict:
    """Search file contents under the workspace.

    Uses ripgrep semantics. Matches are returned as text items. If truncated by match
    limit, inspect `result["details"]["matchLimitReached"]`.
    """


def find(pattern: str, path: str = ".", limit: int = 1000) -> dict:
    """Find workspace files by glob pattern.

    Results are returned as text items. If truncated by result count, inspect
    `result["details"]["resultLimitReached"]`.
    """


def ls(path: str = ".", limit: int = 500) -> dict:
    """List one-level directory contents under the workspace.

    Directory entries include the native trailing separator. If truncated by entry
    count, inspect `result["details"]["entryLimitReached"]`.
    """


def final_answer(answer: Any) -> Any:
    """Return the final answer to the user.

    This is an agent built-in tool provided by CodeAgent/smolagents. It is not
    implemented in python_runtime/tools.py, but it is always available to the agent.
    """
```
