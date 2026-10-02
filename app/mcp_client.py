"""MCP client: real tool discovery over streamable HTTP (no hard-coded tool wiring)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import settings

log = logging.getLogger("finsight.mcp")


@dataclass
class ToolRegistry:
    tools: dict[str, BaseTool] = field(default_factory=dict)
    server_of: dict[str, str] = field(default_factory=dict)
    servers: list[dict] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return sorted(self.tools)

    def describe(self) -> list[dict]:
        out = []
        for s in self.servers:
            s = dict(s)
            s["tools"] = [
                {"name": n, "description": (self.tools[n].description or "").strip(),
                 "args": list((self.tools[n].args or {}).keys())}
                for n in self.names if self.server_of[n] == s["name"]
            ]
            out.append(s)
        return out


def connections() -> dict[str, dict]:
    return {s.name: {"url": settings.mcp_url(s), "transport": "streamable_http"} for s in settings.mcp_servers}


async def discover() -> ToolRegistry:
    """Call ``tools/list`` on every configured MCP server and build the registry."""
    client = MultiServerMCPClient(connections())
    reg = ToolRegistry()
    for spec in settings.mcp_servers:
        info = {"name": spec.name, "title": spec.title, "url": settings.mcp_url(spec),
                "description": spec.description, "status": "online"}
        try:
            tools = await client.get_tools(server_name=spec.name)
        except Exception as exc:  # server down -> keep the rest working
            log.warning("MCP server %s unavailable: %s", spec.name, exc)
            info["status"] = "offline"
            info["error"] = str(exc)[:200]
            tools = []
        for t in tools:
            reg.tools[t.name] = t
            reg.server_of[t.name] = spec.name
        reg.servers.append(info)
    log.info("discovered %d MCP tools from %d servers", len(reg.tools), len(reg.servers))
    return reg
