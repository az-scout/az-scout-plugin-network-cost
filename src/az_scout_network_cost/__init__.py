"""az-scout plugin for Azure VNet peering cost estimation.

Simulates VNet peering costs to help teams evaluate multi-region
architectures.  The key insight: global VNet peering cost is
predictable and typically NOT a blocker for multi-region deployments.
"""

from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from az_scout.plugin_api import ChatMode, TabDefinition, get_plugin_logger
from fastapi import APIRouter

logger = get_plugin_logger("network-cost")

_STATIC_DIR = Path(__file__).parent / "static"

try:
    __version__ = _pkg_version("az-scout-plugin-network-cost")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"


class NetworkCostPlugin:
    """Azure VNet peering cost estimation plugin."""

    name = "network-cost"
    version = __version__

    def get_router(self) -> APIRouter | None:
        """Return API routes for peering cost estimation."""
        from az_scout_network_cost.routes import router

        return router

    def get_mcp_tools(self) -> list[Callable[..., Any]] | None:
        """Return MCP tool functions for AI chat."""
        from az_scout_network_cost.tools import estimate_peering_cost

        return [estimate_peering_cost]

    def get_static_dir(self) -> Path | None:
        """Return path to static assets directory."""
        return _STATIC_DIR

    def get_tabs(self) -> list[TabDefinition] | None:
        """Return UI tab definitions."""
        return [
            TabDefinition(
                id="network-cost",
                label="Network Cost",
                icon="bi bi-diagram-3",
                js_entry="js/network-cost-tab.js",
                css_entry="css/network-cost.css",
            )
        ]

    def get_chat_modes(self) -> list[ChatMode] | None:
        """No custom chat modes."""
        return None

    def get_system_prompt_addendum(self) -> str | None:
        """Extra guidance for the default discussion chat mode."""
        return (
            "You have access to the estimate_peering_cost tool. Use it when "
            "the user asks about Azure VNet peering costs, multi-region "
            "networking costs, or data-transfer pricing between Azure regions. "
            "Emphasise that global VNet peering cost is predictable and "
            "typically not a blocker for multi-region architectures."
        )


# Module-level instance — referenced by the entry point
plugin = NetworkCostPlugin()
