from .base import Tool, ToolError, ToolResult
from .files import make_file_tools
from .git import make_git_tool
from .sandbox import Workspace
from .shell import make_shell_tool


def make_tools(ws: Workspace) -> list[Tool]:
    return [*make_file_tools(ws), make_git_tool(ws), make_shell_tool(ws)]


__all__ = ["Tool", "ToolError", "ToolResult", "Workspace", "make_tools"]
