"""Run the MCP server. Fleet connects to it as a remote MCP server (streamable HTTP at /mcp).

    cd mcp && uv run python -m sales_tools.server

Fleet stores tool references by server URL. Changing the URL breaks the agent,
so agree on the deployed URL once (Ayaan) and update fleet/*.json in one PR.
"""
from .app import mcp
from .tools import handoff, outreach, positioning, research, shared  # noqa: F401  (registers the tools)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
