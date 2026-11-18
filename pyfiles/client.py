"""Load expense tools from servers.json over Streamable HTTP."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx2
from langchain_core.tools import StructuredTool
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

SERVERS_PATH = Path(__file__).resolve().parent.parent / "servers.json"


def _servers() -> dict[str, dict[str, Any]]:
    data = json.loads(SERVERS_PATH.read_text(encoding="utf-8"))
    servers = data.get("mcpServers") or {}
    if not isinstance(servers, dict):
        raise RuntimeError("servers.json mcpServers must be an object")
    return servers


def _headers(spec: dict[str, Any]) -> dict[str, str]:
    env_name = spec.get("auth_env")
    if not env_name:
        return {}
    token = (os.getenv(str(env_name)) or "").strip()
    if not token:
        raise RuntimeError(f"{env_name} is not set")
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


def _result_text(result: object) -> str:
    if getattr(result, "isError", False):
        detail = _blocks(getattr(result, "content", None))
        raise RuntimeError(detail or "The expense tool returned an error")
    return _blocks(getattr(result, "content", None)) or str(result)


def _blocks(content: object) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


async def _call(url: str, headers: dict[str, str], tool_name: str, arguments: dict[str, Any]) -> str:
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return _result_text(result)


def _describe_expense_fields(schema: dict[str, Any]) -> dict[str, Any]:
    properties = dict(schema.get("properties") or {})
    if "date" in properties:
        properties["date"] = {
            **properties["date"],
            "description": "Expense date as YYYY-MM-DD. Use today's date if the user did not specify one.",
        }
    if "amount" in properties:
        properties["amount"] = {
            **properties["amount"],
            "description": "How much was spent, as a number.",
        }
    if "category" in properties:
        properties["category"] = {
            **properties["category"],
            "description": "Short category such as food, travel, salon, or bills.",
        }
    return {**schema, "properties": properties}


def _remote_tool(
    url: str,
    headers: dict[str, str],
    public_name: str,
    remote_name: str,
    description: str,
    schema: dict[str, Any],
) -> StructuredTool:
    async def runner(**kwargs: Any) -> str:
        return await _call(url, headers, remote_name, kwargs)

    return StructuredTool(
        name=public_name,
        description=description,
        args_schema=schema,
        coroutine=runner,
    )


async def load_tools() -> list[StructuredTool]:
    """Return LangChain tools for every server in servers.json."""
    tools: list[StructuredTool] = []
    for server_name, spec in _servers().items():
        url = str(spec["url"])
        headers = _headers(spec)
        async with httpx2.AsyncClient(headers=headers, timeout=30) as http_client:
            async with streamable_http_client(url, http_client=http_client) as streams:
                read_stream, write_stream = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
        for tool in listed.tools:
            raw_schema = getattr(tool, "input_schema", None)
            schema = raw_schema if isinstance(raw_schema, dict) else {"type": "object", "properties": {}}
            schema = _describe_expense_fields(schema)
            tools.append(
                _remote_tool(
                    url,
                    headers,
                    f"{server_name}_{tool.name}",
                    tool.name,
                    tool.description or tool.name,
                    schema,
                )
            )
    return tools
