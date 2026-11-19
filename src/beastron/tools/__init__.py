from .base import Tool, ToolError, ToolResult
from .files import make_file_tools
from .git import make_git_tool
from .sandbox import Workspace
from .shell import make_shell_tool


def make_tools(ws: Workspace, shell_prefix: list[str] | None = None) -> list[Tool]:
    """`shell_prefix` wraps every run_shell command (the eval runner uses it for the sandbox)."""
    return [*make_file_tools(ws), make_git_tool(ws), make_shell_tool(ws, shell_prefix)]


__all__ = ["Tool", "ToolError", "ToolResult", "Workspace", "make_tools"]
