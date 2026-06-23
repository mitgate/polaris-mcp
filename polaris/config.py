"""Configuration — environment variables and constants for Polaris MCP."""

from __future__ import annotations

import logging
import os

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8016"))
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
DEFAULT_MODEL = os.getenv("BROWSER_USE_MODEL", "gpt-4o-mini")
HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() not in ("false", "0", "no")
SESSIONS_DIR = os.getenv("POLARIS_SESSIONS_DIR", "/tmp/polaris_sessions")

_AUTH_PATTERNS = {"login", "auth", "keycloak", "signin", "sso", "realms"}

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("polaris-mcp")
