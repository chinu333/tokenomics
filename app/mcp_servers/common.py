"""Shared helpers for the MCP servers (read-only data access + FastMCP factory)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app.config import settings  # noqa: E402

DATA = settings.data_dir
MAX_ROWS = 50


def db_path(*parts: str) -> Path:
    return DATA.joinpath(*parts)


def query(db: Path, sql: str, params: tuple | dict = (), limit: int = MAX_ROWS) -> list[dict[str, Any]]:
    """Run a parameterised, read-only query and return rows as dicts."""
    uri = f"file:{db.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchmany(limit)
    return [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in dict(r).items()} for r in rows]


def load_json(*parts: str) -> Any:
    return json.loads(db_path(*parts).read_text(encoding="utf-8"))


def pick(value: str, allowed: dict[str, str], default: str) -> str:
    """Map a user/LLM-supplied choice to a whitelisted SQL column (prevents SQL injection)."""
    return allowed.get((value or "").strip().lower(), allowed[default])


def make_server(name: str, port: int, instructions: str) -> FastMCP:
    return FastMCP(
        name,
        instructions=instructions,
        host=settings.mcp_host,
        port=port,
        stateless_http=True,
        json_response=True,
    )
