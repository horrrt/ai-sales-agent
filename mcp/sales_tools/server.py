"""Run the MCP server. Fleet connects to it as a remote MCP server (streamable HTTP at /mcp).

    cd mcp && uv run python -m sales_tools.server                  # local, http://localhost:8000/mcp
    HOST=0.0.0.0 PORT=8080 uv run python -m sales_tools.server     # what the container runs

With SALES_MCP_TOKEN set, every request must carry "Authorization: Bearer <token>" (in Fleet: the custom
MCP's static header). Without it the server is open, as before.

Fleet stores tool references by server URL. Changing the URL breaks the agent,
so agree on the deployed URL once (Ayaan) and update fleet/*.json in one PR.
"""
import hmac
import os

from mcp.server.transport_security import TransportSecuritySettings

from .app import mcp
from . import store, tracing
from .tools import deals, handoff, onboarding, outreach, positioning, research, shared  # noqa: F401  (registers the tools)

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


def require_token(app, token: str):
    """Wrap the ASGI app so HTTP requests without "Authorization: Bearer <token>" get a 401."""
    expected = f"Bearer {token}".encode()

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            given = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(given, expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)

    return guarded


if __name__ == "__main__":
    configure()
    persist_after_each_tool()
    tracing.trace_each_tool(mcp)  # LangSmith runs per tool call; a no-op unless LANGSMITH_TRACING and the key are set
    token = os.environ.get("SALES_MCP_TOKEN")
    if token:
        import uvicorn

        uvicorn.run(require_token(mcp.streamable_http_app(), token), host=mcp.settings.host, port=mcp.settings.port)
    else:
        mcp.run(transport="streamable-http")
