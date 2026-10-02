"""FinSight web server: leadership dashboard, analytics, governed AI agent (SSE) and TokenOps views."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from tokenops.control import Halt

from app import dashboard, tokenops_integration as tops, tracing
from app.agent import FinSightAgent
from app.config import ROOT_DIR, settings
from app.data_gen import ensure_data
from app.mcp_client import ToolRegistry, discover

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("finsight.server")
WEB = ROOT_DIR / "web"


async def _discover_with_retry(attempts: int = 20) -> ToolRegistry:
    reg = ToolRegistry()
    for _ in range(attempts):
        reg = await discover()
        if all(s["status"] == "online" for s in reg.servers):
            break
        await asyncio.sleep(1)
    return reg


async def _refresh(app: FastAPI, reseed: bool) -> None:
    app.state.registry = await _discover_with_retry()
    app.state.agent = FinSightAgent(app.state.registry, asyncio.get_running_loop())
    if reseed:
        try:
            await asyncio.to_thread(tops.push_governance, app.state.registry.names)
        except Exception as exc:
            log.error("could not push governance to control plane: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_data()
    if not await asyncio.to_thread(tops.wait_for_plane, 30):
        log.error("TokenOps control plane not reachable at %s - start it first (python run_demo.py)",
                  settings.control_plane_url)
    await _refresh(app, settings.reseed_on_start)
    yield


app = FastAPI(title="FinSight - TokenOps governed agentic analytics", lifespan=lifespan)


# ------------------------------------------------------------------ data ---- #
@app.get("/api/health")
async def health():
    reg: ToolRegistry = app.state.registry
    return {
        "app": "ok",
        "control_plane": await asyncio.to_thread(tops.plane_healthy),
        "aoai_configured": settings.aoai_configured,
        "langsmith": settings.langsmith_eval_enabled and settings.langsmith_configured,
        "mcp_servers": [{"name": s["name"], "status": s["status"]} for s in reg.servers],
        "tools": len(reg.tools),
    }


@app.get("/api/config")
async def config():
    return {
        "chat_deployment": settings.chat_deployment,
        "mini_deployment": settings.mini_deployment,
        "default_mode": settings.default_mode,
        "control_plane_url": settings.control_plane_url,
        "aoai_configured": settings.aoai_configured,
        "search_backend": "Azure AI Search" if settings.search_endpoint else "local keyword index",
        "langsmith_configured": settings.langsmith_eval_enabled and settings.langsmith_configured,
    }


@app.get("/api/evaluations")
async def evaluations():
    """Both evaluations of every traced run, side by side (Evaluation Lab)."""

    def collect() -> dict:
        runs = []
        for t in tracing.list_all():
            judge = dict(t.get("evaluation") or {})
            safety = judge.pop("safety", None)
            runs.append({
                "run_id": t.get("run_id"), "question": t.get("question"), "persona": t.get("persona"),
                "department": t.get("department"), "status": t.get("status"), "mode": t.get("mode"),
                "started_at": t.get("started_at"), "cost_usd": (t.get("totals") or {}).get("cost_usd"),
                "judge": judge, "safety": safety,
                "langsmith": t.get("evaluation_langsmith") or {"status": "skipped", "reason": "run predates LangSmith evaluation"},
            })
        return {
            "langsmith_configured": settings.langsmith_eval_enabled and settings.langsmith_configured,
            "langsmith_endpoint": settings.langsmith_endpoint, "project": settings.langsmith_project,
            "sampling_rate": settings.langsmith_sampling_rate,
            "judge_model": settings.eval_deployment, "runs": runs,
        }

    return await asyncio.to_thread(collect)


@app.get("/api/evaluations/langsmith-feedback")
async def langsmith_feedback():
    """Live feedback on the traced production runs, read back from LangSmith (includes UI online-evaluator rules)."""
    from app import langsmith_eval

    def pull() -> dict:
        ids = {}
        for t in tracing.list_all(limit=50):
            rid = (t.get("evaluation_langsmith") or {}).get("ls_run_id")
            if rid:
                ids[rid] = t.get("run_id")
        fb = langsmith_eval.sync_feedback(list(ids))
        return {"feedback": {ids[k]: v for k, v in fb.items() if k in ids}}

    try:
        return await asyncio.to_thread(pull)
    except Exception as exc:
        raise HTTPException(502, f"LangSmith error: {exc}") from exc


@app.get("/api/dashboard")
async def get_dashboard():
    return await asyncio.to_thread(dashboard.dashboard)


@app.get("/api/analytics")
async def get_analytics():
    return await asyncio.to_thread(dashboard.analytics)


@app.get("/api/datasources")
async def get_datasources():
    return await asyncio.to_thread(dashboard.datasources)


@app.get("/api/mcp/tools")
async def mcp_tools():
    return app.state.registry.describe()


@app.post("/api/mcp/refresh")
async def mcp_refresh():
    await _refresh(app, reseed=True)
    return app.state.registry.describe()


# ----------------------------------------------------------------- agent ---- #
class AgentRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    persona: Literal["CEO", "CFO", "CRO", "COO", "CMO", "Head of Wealth"] = "CEO"
    department: Literal["Executive Office", "Finance", "Risk", "Operations", "Marketing", "Wealth"] = "Executive Office"
    mode: Literal["enforce", "preview"] = "enforce"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@app.post("/api/agent/stream")
async def agent_stream(req: AgentRequest):
    if not settings.aoai_configured:
        raise HTTPException(400, "Azure OpenAI is not configured. Set AZURE_OPENAI_ENDPOINT in .env and restart.")
    if getattr(app.state, "proof_running", False):
        raise HTTPException(409, "A Budget Proof is running (it changes the per-run budget) - wait for it to finish.")
    agent: FinSightAgent = app.state.agent
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(ev: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    async def worker():
        app.state.active_runs = getattr(app.state, "active_runs", 0) + 1
        try:
            await asyncio.to_thread(agent.run, req.question, persona=req.persona, department=req.department,
                                    mode=req.mode, emit=emit)
        except BaseException as exc:  # tokenops Halt is a BaseException
            log.exception("agent run failed")
            emit({"type": "error", "message": f"{type(exc).__name__}: {str(exc)[:500]}"})
        finally:
            app.state.active_runs -= 1
            emit({"type": "done"})

    async def stream():
        task = asyncio.create_task(worker())
        try:
            while True:
                ev = await queue.get()
                yield _sse(ev)
                if ev.get("type") == "done":
                    break
        finally:
            if not task.done():
                await task

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# -------------------------------------------------------------- tokenops ---- #
class BudgetUpdate(BaseModel):
    limit_usd: float = Field(gt=0, le=100)


def _plane_call(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        raise HTTPException(502, f"control plane error: {exc}") from exc


@app.get("/api/tokenops/summary")
async def tokenops_summary():
    return await asyncio.to_thread(_plane_call, tops.summary)


@app.get("/api/tokenops/runs/{run_id}")
async def tokenops_run_detail(run_id: str):
    if not run_id.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "invalid run id")

    def detail() -> dict:
        r = tops.enrich_run(tops.plane("GET", f"/v1/run-records/{run_id}"))
        r["trace"] = tracing.load(run_id)
        return r

    return await asyncio.to_thread(_plane_call, detail)


@app.put("/api/tokenops/budget")
async def tokenops_budget(body: BudgetUpdate):
    if getattr(app.state, "proof_running", False):
        raise HTTPException(409, "A Budget Proof is running - the budget is restored when it finishes.")
    return await asyncio.to_thread(_plane_call, tops.set_run_budget, body.limit_usd)


# ---------------------------------------------------------- budget proof ---- #
class ProofRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    persona: Literal["CEO", "CFO", "CRO", "COO", "CMO", "Head of Wealth"] = "CFO"
    department: Literal["Executive Office", "Finance", "Risk", "Operations", "Marketing", "Wealth"] = "Finance"
    budgets: list[Annotated[float, Field(gt=0, le=100)]] = Field(
        default_factory=lambda: [0.005, 0.25, 1.0, 10.0], min_length=1, max_length=6)


_PROOF_EVENTS = {"run_start", "llm", "tool_start", "governance", "model_switch", "halt", "budget_check"}


@app.get("/api/tokenops/proof-meta")
async def proof_meta():
    return await asyncio.to_thread(_plane_call, tops.proof_meta)


@app.post("/api/tokenops/budget-proof")
async def budget_proof(req: ProofRequest):
    """Run the same question once per budget tier (sequentially, enforce mode), then restore the budget."""
    if not settings.aoai_configured:
        raise HTTPException(400, "Azure OpenAI is not configured. Set AZURE_OPENAI_ENDPOINT in .env and restart.")
    if getattr(app.state, "proof_running", False) or getattr(app.state, "active_runs", 0) > 0:
        raise HTTPException(409, "An agent run or Budget Proof is already in progress - wait for it to finish.")
    app.state.proof_running = True
    agent: FinSightAgent = app.state.agent
    tool_names = app.state.registry.names
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(ev: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    def run_tiers() -> None:
        original = tops.run_budget_micros()
        restored = None
        try:
            emit({"type": "proof_start", "budgets": req.budgets, **tops.proof_meta(),
                  "original_budget_usd": original / 1e6 if original else None})
            for i, budget in enumerate(req.budgets):
                tops.set_run_budget(budget)
                emit({"type": "tier_start", "tier": i, "budget_usd": budget})

                def tier_emit(ev: dict, i: int = i) -> None:
                    if ev.get("type") in _PROOF_EVENTS:
                        emit({**ev, "tier": i})

                try:
                    res = agent.run(req.question, persona=req.persona, department=req.department, mode="enforce",
                                    emit=tier_emit)
                    emit({**res, "type": "tier_done", "tier": i, "budget_usd": budget})
                except (Exception, Halt) as exc:
                    log.exception("budget proof tier %s failed", i)
                    emit({"type": "tier_error", "tier": i, "message": f"{type(exc).__name__}: {str(exc)[:500]}"})
        finally:
            try:
                if original:
                    tops.set_run_budget(original / 1e6)
                else:
                    tops.push_governance(tool_names)
                restored = tops.run_budget_micros()
            except Exception as exc:
                log.error("could not restore the per-run budget after the proof: %s", exc)
            emit({"type": "proof_done", "restored_usd": restored / 1e6 if restored else None})

    async def worker():
        app.state.active_runs = getattr(app.state, "active_runs", 0) + 1
        try:
            await asyncio.to_thread(run_tiers)
        except BaseException as exc:  # tokenops Halt is a BaseException
            log.exception("budget proof failed")
            emit({"type": "error", "message": f"{type(exc).__name__}: {str(exc)[:500]}"})
        finally:
            app.state.active_runs -= 1
            app.state.proof_running = False
            emit({"type": "done"})

    task = asyncio.create_task(worker())  # started now so the flags reset even if the stream is never read
    app.state.proof_task = task  # keep a strong reference while it runs

    async def stream():
        try:
            while True:
                ev = await queue.get()
                yield _sse(ev)
                if ev.get("type") == "done":
                    break
        finally:
            if not task.done():
                await task

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/tokenops/reseed")
async def tokenops_reseed():
    gov = await asyncio.to_thread(_plane_call, tops.push_governance, app.state.registry.names)
    return {"status": "reseeded", "governance": gov}


@app.post("/api/tokenops/clear-history")
async def tokenops_clear_history():
    if getattr(app.state, "active_runs", 0) > 0:
        raise HTTPException(409, "An agent run is in progress - wait for it to finish, then clear.")
    return await asyncio.to_thread(_plane_call, tops.clear_history, app.state.registry.names)


# ------------------------------------------------------------------- web ---- #
@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
