"""Polaris MCP — entry point.

Imports the polaris package (which registers all @mcp.tool() decorators via
polaris/__init__.py → polaris/tools/__init__.py) and starts the MCP server.
"""

import polaris  # noqa: F401 — triggers tool registration

from polaris.config import MCP_TRANSPORT
from polaris.server import mcp

if __name__ == "__main__":
    mcp.run(transport=MCP_TRANSPORT)
