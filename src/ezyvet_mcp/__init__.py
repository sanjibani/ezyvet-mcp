"""ezyVet MCP — MCP server."""
from .client import EzyvetAPIError, EzyvetAuthError, EzyvetClient
from .server import main, mcp

__version__ = "0.1.0"
__all__ = [
    "EzyvetAPIError",
    "EzyvetAuthError",
    "EzyvetClient",
    "main",
    "mcp",
]