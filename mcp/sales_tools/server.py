"""Run the MCP server. Fleet connects to it as a remote MCP server (streamable HTTP at /mcp).

    cd mcp && uv run python -m sales_tools.server                  # local, http://localhost:8000/mcp
    HOST=0.0.0.0 PORT=8080 uv run python -m sales_tools.server     # what the container runs

Fleet stores tool references by server URL. Changing the URL breaks the agent,
so agree on the deployed URL once (Ayaan) and update fleet/*.json in one PR.
"""
import os

from mcp.server.transport_security import TransportSecuritySettings

from .app import mcp
from . import store
from .tools import deals, handoff, outreach, positioning, research, shared  # noqa: F401  (registers the tools)

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def configure() -> None:
    """Bind to HOST/PORT from the environment.

    FastMCP turns on DNS-rebinding protection for localhost, which only accepts Host: localhost.
    A public deployment is reached through its own hostname, so the protection is switched off there.
    Stateless HTTP lets any instance answer any request (no sticky sessions needed behind a proxy).
    """
    host = os.environ.get("HOST", "127.0.0.1")
    mcp.settings.host = host
    mcp.settings.port = int(os.environ.get("PORT", "8000"))
    if host not in LOCAL_HOSTS:
        mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        mcp.settings.stateless_http = True


def persist_after_each_tool() -> None:
    """Save the store snapshot after every tool call (only does anything when SALES_STATE is set)."""
    import functools

    for tool in mcp._tool_manager.list_tools():
        fn = tool.fn

        @functools.wraps(fn)
        def wrapped(*args, __fn=fn, **kwargs):
            try:
                return __fn(*args, **kwargs)
            finally:
                store.save()

        tool.fn = wrapped


if __name__ == "__main__":
    configure()
    persist_after_each_tool()
    mcp.run(transport="streamable-http")
