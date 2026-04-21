from __future__ import annotations

from smolagents import CodeAgent, ToolCallingAgent
from smolagents.agents import populate_template


TOOLS_SECTION_MARKER = "Above examples were using notional tools that might not exist for you."
RULES_MARKER = "Here are the rules you should always follow to solve your task:"


def _render_context_prefixed_system_prompt(
    *,
    prompt_template: str,
    context_prefix: str,
    variables: dict[str, object],
    trim_tool_examples: bool,
) -> str:
    rendered = populate_template(prompt_template, variables=variables)
    if trim_tool_examples:
        tools_marker_index = rendered.find(TOOLS_SECTION_MARKER)
        rules_marker_index = rendered.find(RULES_MARKER)
        if (
            tools_marker_index != -1
            and rules_marker_index != -1
            and tools_marker_index < rules_marker_index
        ):
            rendered = (
                rendered[:tools_marker_index].rstrip()
                + "\n\n"
                + rendered[rules_marker_index:].lstrip()
            )

    return context_prefix + "\n\n---\n\n" + rendered


class NativeCodeAgent(CodeAgent):
    """CodeAgent variant whose system prompt starts from ContextBuilder output."""

    def __init__(self, *args, context_prefix: str, **kwargs):
        self._context_prefix = context_prefix
        kwargs.setdefault("add_base_tools", False)
        super().__init__(*args, **kwargs)

    def initialize_system_prompt(self) -> str:
        return _render_context_prefixed_system_prompt(
            prompt_template=self.prompt_templates["system_prompt"],
            context_prefix=self._context_prefix,
            variables={
                "tools": self.tools,
                "managed_agents": self.managed_agents,
                "authorized_imports": (
                    "You can import from any package you want."
                    if "*" in self.authorized_imports
                    else str(self.authorized_imports)
                ),
                "custom_instructions": self.instructions,
                "code_block_opening_tag": self.code_block_tags[0],
                "code_block_closing_tag": self.code_block_tags[1],
            },
            trim_tool_examples=True,
        )


class NativeToolCallingAgent(ToolCallingAgent):
    """ToolCallingAgent variant whose system prompt starts from ContextBuilder output."""

    def __init__(self, *args, context_prefix: str, **kwargs):
        self._context_prefix = context_prefix
        kwargs.setdefault("add_base_tools", False)
        super().__init__(*args, **kwargs)

    def initialize_system_prompt(self) -> str:
        return _render_context_prefixed_system_prompt(
            prompt_template=self.prompt_templates["system_prompt"],
            context_prefix=self._context_prefix,
            variables={
                "tools": self.tools,
                "managed_agents": self.managed_agents,
                "custom_instructions": self.instructions,
            },
            trim_tool_examples=False,
        )
