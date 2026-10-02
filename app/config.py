"""Central configuration - every setting comes from the project-level .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env", override=False)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _path(name: str, default: str) -> Path:
    p = Path(_env(name, default))
    return p if p.is_absolute() else (ROOT_DIR / p).resolve()


def _bool(name: str, default: bool) -> bool:
    raw = _env(name, "")
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    title: str
    module: str
    port: int
    description: str


@dataclass(frozen=True)
class Settings:
    # Azure OpenAI
    aoai_endpoint: str = _env("AZURE_OPENAI_ENDPOINT")
    aoai_api_version: str = _env("AZURE_OPENAI_API_VERSION", "2024-10-21")
    chat_deployment: str = _env("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o")
    mini_deployment: str = _env("AZURE_OPENAI_MINI_DEPLOYMENT", "gpt-4o-mini")
    tenant_id: str = _env("AZURE_TENANT_ID")
    price_chat_in: float = float(_env("PRICE_CHAT_INPUT_PER_1M", "2.50"))
    price_chat_out: float = float(_env("PRICE_CHAT_OUTPUT_PER_1M", "10.00"))
    price_mini_in: float = float(_env("PRICE_MINI_INPUT_PER_1M", "0.15"))
    price_mini_out: float = float(_env("PRICE_MINI_OUTPUT_PER_1M", "0.60"))

    # Azure AI Search (optional)
    search_endpoint: str = _env("AZURE_SEARCH_ENDPOINT")
    search_index: str = _env("AZURE_SEARCH_INDEX", "finsight-policies")

    # TokenOps
    control_plane_host: str = _env("CONTROL_PLANE_HOST", "127.0.0.1")
    control_plane_port: int = int(_env("CONTROL_PLANE_PORT", "8800"))
    control_plane_url: str = _env("CONTROL_PLANE_URL", "http://127.0.0.1:8800")
    control_plane_db: Path = _path("CONTROL_PLANE_DB", "./data/tokenops/control_plane.db")
    control_plane_api_key: str = _env("CONTROL_PLANE_API_KEY")
    governance_file: Path = _path("TOKENOPS_GOVERNANCE_FILE", "./config/governance.yaml")
    reseed_on_start: bool = _bool("TOKENOPS_RESEED_ON_START", True)
    service: str = _env("TOKENOPS_SERVICE", "finsight-agent")
    default_mode: str = _env("TOKENOPS_MODE", "enforce")

    # MCP
    mcp_host: str = _env("MCP_HOST", "127.0.0.1")
    mcp_core_banking_port: int = int(_env("MCP_CORE_BANKING_PORT", "8101"))
    mcp_wealth_markets_port: int = int(_env("MCP_WEALTH_MARKETS_PORT", "8102"))
    mcp_risk_compliance_port: int = int(_env("MCP_RISK_COMPLIANCE_PORT", "8103"))
    mcp_analytics_port: int = int(_env("MCP_ANALYTICS_PORT", "8104"))

    # Agent
    agent_max_iterations: int = int(_env("AGENT_MAX_ITERATIONS", "12"))
    # Blank = model default (GPT-5 / o-series deployments only accept the default).
    agent_temperature: float | None = float(_env("AGENT_TEMPERATURE", "")) if _env("AGENT_TEMPERATURE", "") else None

    # Evaluation (LLM-as-judge on every completed run; blank deployment = the mini deployment)
    eval_enabled: bool = _bool("EVAL_ENABLED", True)
    eval_deployment: str = _env("EVAL_DEPLOYMENT") or _env("AZURE_OPENAI_MINI_DEPLOYMENT", "gpt-4o-mini")

    # LangSmith online evaluation: every run is traced to a LangSmith tracing project and the production
    # trace is scored by online evaluators (openevals LLM judges + code evaluators) as run feedback.
    langsmith_eval_enabled: bool = _bool("LANGSMITH_EVAL_ENABLED", True)
    langsmith_api_key: str = field(default=_env("LANGSMITH_API_KEY"), repr=False)
    langsmith_endpoint: str = _env("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    langsmith_workspace_id: str = _env("LANGSMITH_WORKSPACE_ID")
    langsmith_project: str = _env("LANGSMITH_PROJECT", "finsight-agent")
    langsmith_sampling_rate: float = max(0.0, min(1.0, float(_env("LANGSMITH_SAMPLING_RATE", "1.0") or 1.0)))

    # App
    app_host: str = _env("APP_HOST", "127.0.0.1")
    app_port: int = int(_env("APP_PORT", "8000"))
    data_dir: Path = _path("DATA_DIR", "./data")
    log_level: str = _env("LOG_LEVEL", "INFO")

    @property
    def aoai_configured(self) -> bool:
        return bool(self.aoai_endpoint) and "<" not in self.aoai_endpoint

    @property
    def langsmith_configured(self) -> bool:
        return bool(self.langsmith_api_key) and "<" not in self.langsmith_api_key

    @property
    def mcp_servers(self) -> list[McpServerSpec]:
        return [
            McpServerSpec(
                "core_banking", "Core Banking", "app.mcp_servers.core_banking_server",
                self.mcp_core_banking_port,
                "Customers, accounts, transactions and loan book (core banking SQLite + CRM JSON).",
            ),
            McpServerSpec(
                "wealth_markets", "Wealth & Markets", "app.mcp_servers.wealth_markets_server",
                self.mcp_wealth_markets_port,
                "Client portfolios, holdings and the market-data price feed (CSV).",
            ),
            McpServerSpec(
                "risk_compliance", "Risk & Compliance", "app.mcp_servers.risk_compliance_server",
                self.mcp_risk_compliance_port,
                "AML alerts, credit-risk ratings, compliance findings and policy documents.",
            ),
            McpServerSpec(
                "analytics", "Enterprise Analytics", "app.mcp_servers.analytics_server",
                self.mcp_analytics_port,
                "Data-warehouse KPI history by business line and region.",
            ),
        ]

    def mcp_url(self, spec: McpServerSpec) -> str:
        return f"http://{self.mcp_host}:{spec.port}/mcp"


settings = Settings()
