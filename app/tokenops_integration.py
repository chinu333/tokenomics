"""TokenOps integration: price book, governance policy push, control-plane REST helpers.

Architecture
------------
* The **control plane** (``control-plane serve``) is a separate process that owns the
  ledger (spend, in-flight, halts), budgets, policy instances and run records.
* The **SDK** (``tokenops``) runs inside this app.  Each agent run is wrapped in
  ``tokenops_run(...)`` which registers the run with the plane and builds a Governor
  from the plane's governance config.  Every LLM call goes through ``wrap_complete``
  (pre-call checks + Chronicle crossing -> spend recorded on the plane).
"""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import yaml
from tokenops import ControlPlaneClient
from tokenops.control.core import Usage
from tokenops.control.pricing import Rate, build_price_book

from app import tracing
from app.config import settings

log = logging.getLogger("finsight.tokenops")
RUN_BUDGET_ID = "run_llm_cap"
RUN_TOTAL_BUDGET_ID = "__run_total__"  # plane ledger row holding each run's total LLM spend

# The plane derives steps/cost server-side and does not accept them on PATCH, so the app keeps
# its own per-run step/token metrics in a small sidecar file next to the plane DB.
_METRICS_FILE = Path(settings.control_plane_db).resolve().parent / "finsight_run_metrics.json"
_metrics_lock = threading.Lock()


def _load_metrics() -> dict[str, dict]:
    try:
        return json.loads(_METRICS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record_run_metrics(run_id: str, metrics: dict) -> None:
    with _metrics_lock:
        data = _load_metrics()
        data[run_id] = metrics
        _METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _METRICS_FILE.write_text(json.dumps(data), encoding="utf-8")


def update_run_metrics(run_id: str, patch: dict) -> None:
    """Merge fields (e.g. evaluation score) into an existing run's metrics; no-op if history was cleared."""
    with _metrics_lock:
        data = _load_metrics()
        if run_id not in data:
            return
        data[run_id].update(patch)
        _METRICS_FILE.write_text(json.dumps(data), encoding="utf-8")


def clear_history(tool_names: list[str]) -> dict:
    """Wipe run records + ledger on the plane, delete the local run-metrics file and traces, re-apply governance.

    The plane's clear-all also removes budgets/policies, so governance is re-pushed from the
    policy file (any budget edited in the UI returns to the file value).
    """
    plane("POST", "/v1/admin/clear-all")
    with _metrics_lock:
        _METRICS_FILE.unlink(missing_ok=True)
    tracing.clear_all()
    push_governance(tool_names)
    return {"status": "cleared"}


def run_spent_micros(run_id: str) -> int:
    """Actual LLM spend for a run, from the control-plane ledger."""
    try:
        body = plane("GET", "/v1/ledger/spent", params={"budget_id": RUN_TOTAL_BUDGET_ID,
                                                       "segment_key": f"run:{run_id}", "period": "lifetime"})
        return int(body.get("spent_micros", 0))
    except Exception:
        return 0


def enrich_run(r: dict, metrics: dict[str, dict] | None = None) -> dict:
    if not r.get("cost_micros"):
        r["cost_micros"] = run_spent_micros(r["run_id"])
    m = (metrics if metrics is not None else _load_metrics()).get(r["run_id"])
    if m:
        r.update({k: v for k, v in m.items() if k != "steps" or not r.get("steps")})
    return r


def _usd_to_micros_per_million(usd_per_million: float) -> int:
    return int(round(usd_per_million * 1_000_000))


def price_book():
    """Rates keyed by *Azure deployment name* (the SDK fails closed on unknown models)."""
    return build_price_book({
        settings.chat_deployment: Rate(input=_usd_to_micros_per_million(settings.price_chat_in),
                                       output=_usd_to_micros_per_million(settings.price_chat_out)),
        settings.mini_deployment: Rate(input=_usd_to_micros_per_million(settings.price_mini_in),
                                       output=_usd_to_micros_per_million(settings.price_mini_out)),
    })


def client() -> ControlPlaneClient:
    return ControlPlaneClient.from_env()


def _headers() -> dict[str, str]:
    key = settings.control_plane_api_key
    return {"Authorization": f"Bearer {key}"} if key else {}


_http = httpx.Client(limits=httpx.Limits(max_keepalive_connections=16, max_connections=32))


def plane(method: str, path: str, json: Any = None, timeout: float = 10.0,
          params: dict | None = None) -> Any:
    url = settings.control_plane_url.rstrip("/") + path
    r = _http.request(method, url, json=json, params=params, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json() if r.content else None


def plane_healthy() -> bool:
    try:
        plane("GET", "/health", timeout=2)
        return True
    except Exception:
        return False


def wait_for_plane(timeout_s: float = 30) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if plane_healthy():
            return True
        time.sleep(0.5)
    return False


def load_policy_file() -> dict:
    return yaml.safe_load(settings.governance_file.read_text(encoding="utf-8"))["governance"]


def build_governance(tool_names: list[str]) -> dict:
    """Policy file + runtime facts (discovered MCP tools, mini deployment for downgrade)."""
    gov = copy.deepcopy(load_policy_file())
    pol = gov.setdefault("policies", {})
    if "tool_fix" in pol:
        pol["tool_fix"]["registry"] = sorted(tool_names)
    if "cost_guard" in pol and not pol["cost_guard"].get("downgrade_to"):
        pol["cost_guard"]["downgrade_to"] = settings.mini_deployment
    return gov


def push_governance(tool_names: list[str]) -> dict:
    """Replace the plane's governance with our policy (POST /v1/admin/reseed-governance)."""
    gov = build_governance(tool_names)
    plane("POST", "/v1/admin/reseed-governance", json={"governance": gov})
    log.info("governance pushed: %d budgets, %d policies", len(gov.get("budgets", [])), len(gov.get("policies", {})))
    return gov


def set_run_budget(limit_usd: float) -> dict:
    return plane("PUT", "/v1/budgets", json={"id": RUN_BUDGET_ID, "limit_micros": int(round(limit_usd * 1_000_000)),
                                              "dimension": "run", "period": "lifetime"})


def run_budget_micros() -> int | None:
    try:
        return plane("GET", f"/v1/budgets/{RUN_BUDGET_ID}").get("limit_micros")
    except Exception:
        return None


# ------------------------------------------------------------ budget proof ---- #
_WORST_RE = re.compile(r"worst-case (\d+) >= remaining budget (-?\d+)")


def governance_params() -> dict:
    """Live params of the budget policies (plane first, policy file as fallback)."""
    params: dict[str, dict] = {}
    try:
        for p in plane("GET", "/v1/policies"):
            params.setdefault(p.get("template"), p.get("params") or {})
    except Exception:
        params = {k: v or {} for k, v in (load_policy_file().get("policies") or {}).items()}
    pre, guard = params.get("pre_call_worst_case") or {}, params.get("cost_guard") or {}
    return {"max_output": int(pre.get("default_max_output", 1024)),
            "guard_threshold": float(guard.get("threshold", 0.8)),
            "guard_mode": guard.get("mode", "downgrade")}


def proof_meta() -> dict:
    """Everything the Budget Proof panel needs to render the policy formulas."""
    return {
        **governance_params(),
        "chat_model": settings.chat_deployment, "mini_model": settings.mini_deployment,
        "rates": {settings.chat_deployment: [settings.price_chat_in, settings.price_chat_out],
                  settings.mini_deployment: [settings.price_mini_in, settings.price_mini_out]},
        "budget_usd": (b / 1e6) if (b := run_budget_micros()) else None,
    }


def precall_check(n: int, model: str, messages: list, spent_micros: int, budget_micros: int | None,
                  max_output: int) -> dict:
    """Re-derive the numbers pre_call_worst_case uses for one LLM call (same estimate + price book)."""
    est_in = max(1, len(str(messages)) // 4)  # identical to tokenops wrap_complete's estimate
    pb = price_book()
    try:
        in_m = pb("azure_openai", model, Usage(input=est_in))
        out_m = pb("azure_openai", model, Usage(output=max_output))
    except ValueError:
        in_m = out_m = None
    rate = {settings.chat_deployment: (settings.price_chat_in, settings.price_chat_out),
            settings.mini_deployment: (settings.price_mini_in, settings.price_mini_out)}.get(model, (None, None))
    left = (budget_micros - spent_micros) if budget_micros is not None else None
    return {"call": n, "model": model, "est_input": est_in, "max_output": max_output,
            "in_price": rate[0], "out_price": rate[1],
            "input_usd": in_m / 1e6 if in_m is not None else None,
            "output_usd": out_m / 1e6 if out_m is not None else None,
            "worst_usd": (in_m + out_m) / 1e6 if in_m is not None else None,
            "spent_usd": spent_micros / 1e6, "left_usd": left / 1e6 if left is not None else None,
            "decision": "allow"}


def finalize_check(chk: dict, reason: str | None) -> dict:
    """Mark a pre-call check as blocked when TokenOps' halt reason came from pre_call_worst_case."""
    if not reason:
        return chk
    m = _WORST_RE.search(reason)
    if m:  # use the SDK's own numbers so the panel shows exactly what was enforced
        chk.update(decision="block", worst_usd=int(m.group(1)) / 1e6, left_usd=int(m.group(2)) / 1e6)
    elif "worst-case" in reason or "failing closed" in reason:
        chk["decision"] = "block"
    elif "exhausted" in reason:
        chk["decision"] = "halt"
    chk["reason"] = reason
    return chk


def summary(limit: int = 100) -> dict:
    runs = plane("GET", f"/v1/run-records?limit={limit}")
    runs = [r for r in runs if (r.get("dims") or {}).get("app") == "finsight"]
    metrics = _load_metrics()
    with ThreadPoolExecutor(max_workers=16) as pool:
        runs = list(pool.map(lambda r: enrich_run(r, metrics), runs))
    budgets = plane("GET", "/v1/budgets")
    policies = plane("GET", "/v1/policies")
    total = sum(r.get("cost_micros", 0) for r in runs)
    halted = [r for r in runs if r.get("status") == "halted"]
    completed = [r for r in runs if r.get("status") == "completed"]
    run_budget = next((b for b in budgets if b["id"] == RUN_BUDGET_ID), None)
    events: dict[str, int] = {}
    for r in runs:
        for e in r.get("governance_events") or []:
            key = e.get("policy") if e.get("policy") not in (None, "—") else e.get("kind", "event")
            events[key] = events.get(key, 0) + 1
    by_persona: dict[str, dict] = {}
    for r in runs:
        p = (r.get("dims") or {}).get("persona", "unknown")
        agg = by_persona.setdefault(p, {"runs": 0, "cost_micros": 0})
        agg["runs"] += 1
        agg["cost_micros"] += r.get("cost_micros", 0)
    return {
        "plane_url": settings.control_plane_url,
        "runs": runs,
        "budgets": budgets,
        "policies": policies,
        "kpis": {
            "total_runs": len(runs),
            "completed": len(completed),
            "halted": len(halted),
            "total_cost_usd": total / 1e6,
            "avg_cost_usd": (total / len(runs) / 1e6) if runs else 0,
            "max_cost_usd": max((r.get("cost_micros", 0) for r in runs), default=0) / 1e6,
            "total_steps": sum(r.get("steps", 0) for r in runs),
            "run_budget_usd": (run_budget or {}).get("limit_micros", 0) / 1e6 if run_budget else None,
        },
        "events_by_policy": events,
        "cost_by_persona": by_persona,
    }
