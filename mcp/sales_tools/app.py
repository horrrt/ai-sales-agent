"""Shared FastMCP instance. Every tools module registers its tools on this."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Sales tools (sandbox)")
