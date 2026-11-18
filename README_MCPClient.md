# Expense manager MCP client

This project connects to FastMCP Horizon and returns LangChain tools. The chatbot  client (`ChatOpenAI`) and passes these tools into the agent it already builds.

The expense server is `https://remote-expense-manger-server.fastmcp.app/mcp`.

## Setup

```bash
uv run python main.py
```

## Use the tools in the chatbot

Install this project into the chatbot environment, or add this directory to `PYTHONPATH`, then:

```python
from client import load_tools

tools = await load_tools()
agent = create_agent(existing_chat_openai, tools)
```

`existing_chat_openai` is the `ChatOpenAI` instance that already points at Gemini. `load_tools()` returns ordinary LangChain tools. They stay callable after discovery, and the chatbot's model decides when to call them.

## Add another server

Add a key under `mcpServers` in `servers.json`. Use `transport: "http"` (Streamable HTTP). Do not use `sse`; Horizon does not keep a GET stream open.


```json
{
  "mcpServers": {
    "expense-manager": {
      "url": "https://remote-expense-manger-server.fastmcp.app/mcp",
      "transport": "http",
      "auth_env": "EXPENSE_MCP_TOKEN"
    },
    "another-server": {
      "url": "https://another-server.fastmcp.app/mcp",
      "transport": "http",
      "auth_env": "ANOTHER_MCP_TOKEN"
    }
  }`
}
```

Set `ANOTHER_MCP_TOKEN` in `.env`. Tool names are prefixed with the server key, so two servers can expose the same tool name. Omit `auth_env` only for a server that needs no credential.


load_tools() in client.py reads servers.json, sends the FastMCP Gateway token from EXPENSE_MCP_TOKEN, and returns tools from https://remote-expense-manger-server.fastmcp.app/mcp. 