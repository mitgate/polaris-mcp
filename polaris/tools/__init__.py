"""Tool registration — importing each module triggers @mcp.tool() decorators."""

from polaris.tools import (
    auth,
    execution,
    knowledge,
    utilities,
    verification,
)  # noqa: F401

__all__ = ["auth", "execution", "knowledge", "utilities", "verification"]
