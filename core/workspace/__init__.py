"""ULTRON Workspace 2.0 — one conversational workspace over MCP tools.

    web/            the interface (HTML/CSS/JS in a sandboxed QWebEngineView)
    bridge.py       the only door between that interface and Python
    orchestrator.py turns a request into tool calls, progress and result cards
    intent.py       Serbian/English understanding of what was asked
    geo.py, stays/  the real data providers behind the MCP tools
    mcp_server.py   those tools as an MCP server (stdio)
    mcp_registry.py every MCP server ULTRON can reach, with real health

Nothing in this package invents a distance, a price or a connection state:
a value the provider did not return is shown as missing, never estimated.
"""
