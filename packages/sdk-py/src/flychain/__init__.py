"""FlyChain Python SDK.

Phase 0 scaffold; Phase 1 adds the real client once the gateway is live.
"""

from __future__ import annotations

__version__ = "0.0.0"


def gateway_base_url(default: str = "http://localhost:8080") -> str:
    """Return the configured FlyChain gateway base URL."""
    import os

    return os.environ.get("FLYCHAIN_GATEWAY_URL", default)


__all__ = ["__version__", "gateway_base_url"]
