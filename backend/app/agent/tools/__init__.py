"""The analyst agent's tools; every tool is bound to the conversation's tenant and dataset."""

from app.agent.tools import data, findings, python
from app.agent.tools.base import (
    AgentContext,
    Tool,
    ToolArgumentError,
    ToolResult,
    parse_arguments,
)

ALL_TOOLS: tuple[Tool, ...] = (*data.TOOLS, *python.TOOLS, *findings.TOOLS)
TOOLS_BY_NAME = {tool.name: tool for tool in ALL_TOOLS}

__all__ = [
    "ALL_TOOLS",
    "TOOLS_BY_NAME",
    "AgentContext",
    "Tool",
    "ToolArgumentError",
    "ToolResult",
    "parse_arguments",
]
