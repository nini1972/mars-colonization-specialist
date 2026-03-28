"""Module entrypoint for running the MCP server runtime."""


if __name__ == "__main__":
    from mars_agent.mcp.server import mcp

    mcp.run(transport="stdio")
