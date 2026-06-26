"""ezyVet MCP — public surface.

NOTE: ``__version__`` is defined BEFORE any subpackage import. ``client.py``
imports ``from . import __version__`` (for the User-Agent header) so the
version must be resolvable at the moment ``client`` is loaded.
"""
__version__ = "0.2.0"

from .client import EzyvetClient
from .exceptions import (
    EzyvetAPIError,
    EzyvetAuthError,
    EzyvetConnectionError,
    EzyvetError,
    EzyvetNotFoundError,
    EzyvetRateLimitError,
)
from .server import main, mcp

__all__ = [
    "EzyvetAPIError",
    "EzyvetAuthError",
    "EzyvetClient",
    "EzyvetConnectionError",
    "EzyvetError",
    "EzyvetNotFoundError",
    "EzyvetRateLimitError",
    "main",
    "mcp",
]
