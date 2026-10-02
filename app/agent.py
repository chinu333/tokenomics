"""FinSight LangGraph agent, governed end-to-end by TokenOps.

Graph:   START -> agent --(tool_calls?)--> tools -> agent ... -> END

* ``agent`` node: every LLM call goes through ``tokenops.control.wrap_complete``.
  The dispatch function calls Azure OpenAI (Entra ID auth) with the MCP tools bound,
  keeps the AIMessage (with tool_calls) and hands TokenOps a plain ModelResponse so
  the token usage is recorded on the control-plane ledger.
* ``tools`` node: executes MCP tool calls chosen by the model.  Each call is a Chronicle
  ``@boundary(kind="tool")`` crossing so step_cap / tool_fix / progress_guard /
  tool_output_cap policies see it.
* TokenOps actions: HALT stops the graph, MUTATE may cap output or downgrade the model
  (cost_guard), INJECT adds steer messages / replaces a bad tool result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from chronicle import boundary
from langchain_core.callbacks import BaseCallbackHandler, BaseCallbackManager
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, convert_to_openai_messages
from langchain_core.runnables.config import ensure_config
from langchain_openai import AzureChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from tokenops import tokenops_run
from tokenops.control import Halt, Throttled, governance_events_payload, halt_detector_from_events, wrap_complete
from tokenops.providers.types import ModelResponse

from app import evaluation, langsmith_eval, tracing
from app.azure_auth import aoai_token_provider
from app.config import settings
from app.mcp_client import ToolRegistry
from app.tokenops_integration import (client as plane_client, finalize_check, governance_params, precall_check,
                                      price_book, record_run_metrics, run_budget_micros, update_run_metrics)

log = logging.getLogger("finsight.agent")
PROVIDER = "azure_openai"
MAX_TOOL_CHARS = 14000

SYSTEM_PROMPT = """You are FinSight, an AI analyst for the senior leadership team of a global bank.
You answer using ONLY data returned by your tools (discovered from MCP servers that front the bank's
core banking, CRM, wealth, market data, risk & compliance and data-warehouse systems).
Rules:
- Plan briefly, call the minimum set of tools needed (call independent tools in parallel), then answer.
- Never invent numbers. Cite which system each figure came from.
- Money: format as $X.XM / $X.XB. Percentages with 1 decimal.
- Answer in concise executive markdown: a 1-line headline, key metrics as bullets,
  a small table when useful, then 'Risks' and 'Recommended actions'.
- When relevant, check internal policy thresholds with the policy search tool."""

_llms: dict[str, AzureChatOpenAI] = {}


def _llm(deployment: str) -> AzureChatOpenAI:
    if deployment not in _llms:
        extra = {} if settings.agent_temperature is None else {"temperature": settings.agent_temperature}
        _llms[deployment] = AzureChatOpenAI(
            azure_endpoint=settings.aoai_endpoint,
            azure_deployment=deployment,
            api_version=settings.aoai_api_version,
            azure_ad_token_provider=aoai_token_provider(),  # RBAC - no API key
            max_retries=2,
            timeout=120,
            **extra,
        )
    return _llms[deployment]


def _tool_content(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for b in result:
            if isinstance(b, dict) and "text" in b:
                parts.append(str(b["text"]))
            elif hasattr(b, "text"):
                parts.append(str(b.text))
            else:
                parts.append(json.dumps(b, default=str))
        return "\n".join(parts)
    return json.dumps(result, default=str)


class _AzureMeta(BaseCallbackHandler):
    """Captures Azure prompt_filter_results (Prompt Shields / input content filter) from llm_output."""

    def __init__(self) -> None:
        self.prompt_filter: Any = None

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        self.prompt_filter = (response.llm_output or {}).get("prompt_filter_results")


def _tool_status(content: str) -> tuple[str, str | None]:
    if content.lstrip().startswith("{"):
        try:
            body = json.loads(content)
            if isinstance(body, dict) and body.get("error"):
                return "error", str(body["error"])[:300]
        except ValueError:
            pass
    return "ok", None


class FinSightAgent:
    def __init__(self, registry: ToolRegistry, loop: asyncio.AbstractEventLoop) -> None:
        self.registry = registry
        self.loop = loop  # MCP tools are async; they run on the server's event loop

    # ------------------------------------------------------------------ #
    def run(self, question: str, *, persona: str, department: str, mode: str,
            emit: Callable[[dict], None]) -> dict:
        """Run one governed agent task (blocking - call from a worker thread), traced live to LangSmith."""
        with langsmith_eval.production_trace(question=question, persona=persona, department=department,
                                             mode=mode) as ls_root:
            return self._run(question, persona=persona, department=department, mode=mode, emit=emit,
                             ls_root=ls_root)

    def _run(self, question: str, *, persona: str, department: str, mode: str,
             emit: Callable[[dict], None], ls_root: Any = None) -> dict:
        if not settings.aoai_configured:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT is not configured in .env")

        started = time.time()
        tools = list(self.registry.tools.values())
        state: dict[str, Any] = {"model": settings.chat_deployment, "halt": None, "llm_calls": 0,
                                 "tool_calls": 0, "in_tokens": 0, "out_tokens": 0, "checks": []}
        cp = plane_client()
        budget_limit = run_budget_micros()
        gov_params = governance_params()

        with tokenops_run(
            client=cp,
            service=settings.service,
            intent="finsight.exec_insights",
            user_dims={"persona": persona, "department": department, "app": "finsight"},
            mode=mode,
            provider=PROVIDER,
            model=settings.chat_deployment,
            price=price_book(),
        ) as bound:
            run_id = bound.attr.run_id
            ledger, controls = bound.governor.ledger, bound.controls
            trace = tracing.RunTrace(run_id, question=question, persona=persona, department=department, mode=mode,
                                     model=state["model"], tools=self.registry.names, system_prompt=SYSTEM_PROMPT)
            state["prev_len"] = 0
            state["llm_span"] = None
            emit({"type": "run_start", "run_id": run_id, "mode": mode, "model": state["model"],
                  "tools_available": len(tools)})

            def cost_micros() -> int:
                try:
                    return int(ledger.cost_micros(run_id))
                except Exception:
                    return 0

            # ---------------- governed LLM dispatch ----------------
            holder: dict[str, AIMessage] = {}

            def dispatch(provider: str, model: str, messages: list, max_output_tokens: int | None = None,
                         **kw: Any) -> ModelResponse:
                llm = _llm(model).bind_tools(tools)
                params: dict[str, Any] = {}
                if max_output_tokens:
                    params["max_completion_tokens"] = int(max_output_tokens)
                for k in ("frequency_penalty", "presence_penalty"):
                    if k in kw:
                        params[k] = kw[k]
                meta = _AzureMeta()
                # keep the graph node's callbacks so the LLM call nests under it in LangSmith traces
                cfg = ensure_config()
                parent_cb = cfg.get("callbacks")
                if isinstance(parent_cb, BaseCallbackManager):
                    parent_cb = parent_cb.copy()
                    parent_cb.add_handler(meta, inherit=False)
                    cfg["callbacks"] = parent_cb
                else:
                    cfg["callbacks"] = [*(parent_cb or []), meta]
                t_llm = time.time()
                ai: AIMessage = llm.invoke(messages, config=cfg, **params)
                holder["ai"] = ai
                holder["call"] = {"sent": messages, "params": {"model": model, **params},
                                  "llm_ms": int((time.time() - t_llm) * 1000), "prompt_filter": meta.prompt_filter}
                usage = ai.usage_metadata or {}
                text = ai.content if isinstance(ai.content, str) else json.dumps(ai.content)
                if ai.tool_calls:
                    text = (text + " " + json.dumps([{"name": t["name"], "args": t["args"]} for t in ai.tool_calls]))[:2000]
                return ModelResponse(content=text, input_tokens=int(usage.get("input_tokens", 0)),
                                     output_tokens=int(usage.get("output_tokens", 0)))

            governed = wrap_complete(bound.governor, controls, bound.attr, provider=PROVIDER,
                                     model=settings.chat_deployment, dispatch=dispatch, service=settings.service)

            # ---------------- MCP tool crossing ----------------
            @boundary("mcp.tool_call", kind="tool")
            def call_mcp_tool(name: str, args: dict) -> str:
                tool = self.registry.tools.get(name)
                if tool is None:
                    return json.dumps({"error": f"unknown tool {name}", "available_tools": self.registry.names})
                with langsmith_eval.tool_span(name, self.registry.server_of.get(name, "?"), args) as ls_tool:
                    fut = asyncio.run_coroutine_threadsafe(tool.ainvoke(args), self.loop)
                    content = _tool_content(fut.result(timeout=90))
                    if ls_tool is not None:
                        ls_tool.end(outputs={"content": content[:tracing.MAX_TOOL_CHARS]})
                    return content

            # ---------------- graph nodes ----------------
            seen = {"n": 0}

            def flush_gov(parent: str | None = None) -> None:
                evs = governance_events_payload(controls)
                for ev in evs[seen["n"]:]:
                    emit({"type": "governance", **ev, "cost_usd": cost_micros() / 1e6})
                    now = time.time()
                    pol = ev.get("policy") if ev.get("policy") not in (None, "—") else "policy"
                    trace.span("governance", f"{str(ev.get('kind', '')).upper()} · {pol}", now, now, parent=parent,
                               status=str(ev.get("kind", "event")), output=ev,
                               checks=[tracing.check("TokenOps action", str(ev.get("kind", "")).upper(),
                                                     "fail" if ev.get("kind") == "halt" else "warn")])
                seen["n"] = len(evs)

            def llm_input(msgs: list, params: dict | None = None) -> dict:
                carried = state["prev_len"] + 1 if state["prev_len"] else 0
                return {"messages": tracing.serialize_messages(msgs), "carried": min(carried, len(msgs)),
                        "params": params or {"model": state["model"]}, "tools_bound": len(tools)}

            def agent_node(gs: MessagesState) -> dict:
                msgs = convert_to_openai_messages([SystemMessage(SYSTEM_PROMPT), *gs["messages"]])
                before = cost_micros()
                t0 = time.time()
                n = state["llm_calls"] + 1
                chk = precall_check(n, state["model"], msgs, before, budget_limit, gov_params["max_output"])
                try:
                    governed(PROVIDER, state["model"], msgs)
                except (Halt, Throttled) as h:
                    reason = h.action.reason if isinstance(h, Halt) else f"throttled: {h.action.reason}"
                    state["checks"].append(finalize_check(chk, reason))
                    emit({"type": "budget_check", **chk})
                    sp = trace.span("llm", f"LLM call #{n} · blocked", t0, model=state["model"], status="blocked",
                                    input=llm_input(msgs), error=reason,
                                    checks=[tracing.check("Pre-call governance", "blocked before dispatch", "fail")])
                    flush_gov(sp["id"])
                    state["halt"] = {"reason": reason, "stage": "llm"}
                    emit({"type": "halt", "reason": reason, "cost_usd": cost_micros() / 1e6})
                    return {"messages": []}
                except Exception as exc:
                    call = holder.pop("call", None) or {}
                    trace.span("llm", f"LLM call #{n} · error", t0, model=state["model"], status="error",
                               input=llm_input(call.get("sent") or msgs, call.get("params")),
                               error=f"{type(exc).__name__}: {str(exc)[:1500]}",
                               checks=[tracing.check("Request", "failed", "fail")])
                    raise
                state["checks"].append(chk)
                emit({"type": "budget_check", **chk})
                ai = holder.pop("ai")
                call = holder.pop("call")
                usage = ai.usage_metadata or {}
                state["llm_calls"] += 1
                state["in_tokens"] += int(usage.get("input_tokens", 0))
                state["out_tokens"] += int(usage.get("output_tokens", 0))
                after = cost_micros()
                latency = int((time.time() - t0) * 1000)
                emit({"type": "llm", "model": state["model"], "input_tokens": usage.get("input_tokens", 0),
                      "output_tokens": usage.get("output_tokens", 0), "call_cost_usd": (after - before) / 1e6,
                      "cost_usd": after / 1e6, "latency_ms": latency,
                      "tool_calls": [t["name"] for t in ai.tool_calls]})
                rm = ai.response_metadata or {}
                safety = {"prompt": tracing.content_safety(call["prompt_filter"] or rm.get("prompt_filter_results")),
                          "completion": tracing.content_safety(rm.get("content_filter_results"))}
                unknown = [t["name"] for t in ai.tool_calls if t["name"] not in self.registry.tools]
                checks = tracing.safety_checks(safety)
                if ai.tool_calls:
                    checks.append(tracing.check("Tool-call validity",
                                                f"{len(ai.tool_calls) - len(unknown)}/{len(ai.tool_calls)} discovered tools"
                                                + (f" · unknown: {', '.join(unknown)}" if unknown else ""),
                                                "fail" if unknown else "pass"))
                content = ai.content if isinstance(ai.content, str) else json.dumps(ai.content)
                in_det, out_det = usage.get("input_token_details") or {}, usage.get("output_token_details") or {}
                sp = trace.span(
                    "llm", f"LLM call #{n}" + (" · final answer" if not ai.tool_calls else f" · {len(ai.tool_calls)} tool call(s)"),
                    t0, model=call["params"]["model"], final=not ai.tool_calls,
                    input=llm_input(call["sent"], call["params"]),
                    output={"content": content, "finish_reason": rm.get("finish_reason"),
                            "tool_calls": [{"id": t["id"], "name": t["name"], "args": t["args"]} for t in ai.tool_calls],
                            "model_version": rm.get("model_name"), "system_fingerprint": rm.get("system_fingerprint")},
                    tokens={"input": int(usage.get("input_tokens", 0)), "output": int(usage.get("output_tokens", 0)),
                            "total": int(usage.get("total_tokens", 0)), "cached": int(in_det.get("cache_read", 0) or 0),
                            "reasoning": int(out_det.get("reasoning", 0) or 0)},
                    cost_usd=(after - before) / 1e6, llm_latency_ms=call["llm_ms"],
                    overhead_ms=max(latency - call["llm_ms"], 0), safety=safety, checks=checks)
                state["llm_span"] = sp["id"]
                state["prev_len"] = len(msgs)
                flush_gov(sp["id"])
                # cost_guard MUTATE(downgrade_to) is set during observe; make it sticky for the run.
                # Always route to the configured (priced + deployed) mini deployment, so a stale
                # downgrade_to on the control plane can never select an unpriced model.
                override = controls.call.model_override and settings.mini_deployment
                if override and override != state["model"]:
                    emit({"type": "model_switch", "from": state["model"], "to": override,
                          "reason": "cost_guard: budget pressure - switching to the cheaper deployment"})
                    now = time.time()
                    trace.span("governance", f"MODEL SWITCH · {state['model']} → {override}", now, now, parent=sp["id"],
                               status="mutate", output={"kind": "model_switch", "from": state["model"], "to": override,
                                                        "reason": "cost_guard: budget pressure"},
                               checks=[tracing.check("TokenOps action", "model downgraded", "warn")])
                    state["model"] = override
                if ai.tool_calls and state["llm_calls"] >= settings.agent_max_iterations:
                    state["halt"] = {"reason": "agent iteration limit reached", "stage": "agent"}
                return {"messages": [ai]}

            def tools_node(gs: MessagesState) -> dict:
                last = gs["messages"][-1]
                out = []
                for tc in last.tool_calls:
                    name, args = tc["name"], tc.get("args") or {}
                    server = self.registry.server_of.get(name, "?")
                    emit({"type": "tool_start", "tool": name, "server": server, "args": args, "id": tc["id"]})
                    t0 = time.time()
                    status, error = "ok", None
                    try:
                        content = call_mcp_tool(name, args)
                    except Halt as h:
                        state["halt"] = {"reason": h.action.reason, "stage": "tool"}
                        status, error = "halted", h.action.reason
                        content = json.dumps({"error": "halted by governance"})
                    except Exception as exc:
                        content = json.dumps({"error": str(exc)[:400]})
                    replaced = controls.take_tool_result()
                    if replaced:
                        content = replaced
                    if status == "ok":
                        status, error = _tool_status(content)
                    size = len(content)
                    stored, stored_cut = tracing.clip(content, tracing.MAX_TOOL_CHARS)
                    hits = tracing.scan_injection(content)
                    checks = [
                        tracing.check("Execution", status if not error else f"{status}: {error[:80]}",
                                      "pass" if status == "ok" else "fail"),
                        tracing.check("Prompt-injection scan", f"{len(hits)} suspicious pattern(s)" if hits else "clean",
                                      "fail" if hits else "pass"),
                        tracing.check("Output size", f"{size:,} chars" + (" · truncated for the model" if size > MAX_TOOL_CHARS else ""),
                                      "warn" if size > MAX_TOOL_CHARS else "pass"),
                    ]
                    if replaced:
                        checks.append(tracing.check("Governance", "result replaced by TokenOps tool_fix", "warn"))
                    sp = trace.span("tool", name, t0, parent=state["llm_span"], server=server, status=status, error=error,
                                    input={"tool_call_id": tc["id"], "args": args},
                                    output={"content": stored, "chars": size, "stored_truncated": stored_cut,
                                            "sent_to_model_truncated": size > MAX_TOOL_CHARS},
                                    replaced_by_governance=bool(replaced), injection=hits, checks=checks)
                    flush_gov(sp["id"])
                    if status == "halted":
                        emit({"type": "halt", "reason": error, "cost_usd": cost_micros() / 1e6})
                    if size > MAX_TOOL_CHARS:
                        content = content[:MAX_TOOL_CHARS] + "\n...[truncated]"
                    state["tool_calls"] += 1
                    emit({"type": "tool_end", "tool": name, "server": server, "id": tc["id"], "chars": size,
                          "latency_ms": int((time.time() - t0) * 1000), "preview": content[:600],
                          "replaced_by_governance": bool(replaced)})
                    out.append(ToolMessage(content=content, tool_call_id=tc["id"], name=name))
                    if state["halt"]:
                        break
                # every tool_call must be answered for a valid transcript
                answered = {m.tool_call_id for m in out}
                for tc in last.tool_calls:
                    if tc["id"] not in answered:
                        out.append(ToolMessage(content='{"error":"skipped"}', tool_call_id=tc["id"], name=tc["name"]))
                return {"messages": out}

            def route(gs: MessagesState) -> str:
                if state["halt"]:
                    return END
                last = gs["messages"][-1]
                return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

            g = StateGraph(MessagesState)
            g.add_node("agent", agent_node)
            g.add_node("tools", tools_node)
            g.add_edge(START, "agent")
            g.add_conditional_edges("agent", route, {"tools": "tools", END: END})
            g.add_conditional_edges("tools", lambda gs: END if state["halt"] else "agent", {"agent": "agent", END: END})
            graph = g.compile()

            try:
                final = graph.invoke({"messages": [HumanMessage(question)]},
                                     {"recursion_limit": settings.agent_max_iterations * 2 + 6})
            except Exception as exc:
                events = governance_events_payload(controls)
                try:
                    cp.update_run(run_id, status="error", halt_reason=str(exc)[:300], task=question[:300],
                                  governance_events=events, ended_at=time.time())
                except Exception:
                    pass
                trace.finish(status="error", cost_usd=cost_micros() / 1e6, model_final=state["model"],
                             governance_events=events, error=f"{type(exc).__name__}: {str(exc)[:1500]}")
                raise
            answer = ""
            for m in reversed(final["messages"]):
                if isinstance(m, AIMessage) and m.content and not m.tool_calls:
                    answer = m.content if isinstance(m.content, str) else json.dumps(m.content)
                    break

            events = governance_events_payload(controls)
            cost = cost_micros()
            status = "halted" if state["halt"] else "completed"
            if state["halt"] and state["halt"]["reason"].startswith("throttled"):
                status = "throttled"
            if state["halt"] and not answer:
                answer = (f"**Run halted by TokenOps governance** - {state['halt']['reason']}.\n\n"
                          f"The agent completed {state['tool_calls']} tool call(s) and {state['llm_calls']} "
                          f"LLM call(s) before the policy stopped it, protecting the per-run budget.")
            try:
                cp.update_run(run_id, status=status, halt_reason=(state["halt"] or {}).get("reason"),
                              task=question[:300],
                              detector=halt_detector_from_events(events) if state["halt"] else None,
                              governance_events=events, ended_at=time.time())
            except Exception as exc:
                log.warning("update_run failed: %s", exc)
            record_run_metrics(run_id, {"steps": state["llm_calls"] + state["tool_calls"],
                                        "llm_calls": state["llm_calls"], "tool_calls": state["tool_calls"],
                                        "input_tokens": state["in_tokens"], "output_tokens": state["out_tokens"],
                                        "model_final": state["model"]})

            trace.finish(status=status, answer=answer, cost_usd=cost / 1e6,
                         budget_usd=(budget_limit / 1e6) if budget_limit else None, model_final=state["model"],
                         halt=state["halt"], governance_events=events)
            safety = trace.data["evaluation"]["safety"]
            update_run_metrics(run_id, {"safety_flags": sum(1 for k in ("jailbreak", "prompt_injection") if safety[k]["detected"])
                                        + (safety["harmful_content"]["severity"] != "safe")})

        result = {
            "type": "final", "run_id": run_id, "status": status, "answer": answer, "mode": mode,
            "halt": state["halt"], "model_final": state["model"], "llm_calls": state["llm_calls"],
            "tool_calls": state["tool_calls"], "input_tokens": state["in_tokens"], "output_tokens": state["out_tokens"],
            "cost_usd": cost / 1e6, "budget_usd": (budget_limit / 1e6) if budget_limit else None,
            "governance_events": events, "elapsed_s": round(time.time() - started, 2),
            "budget_checks": state["checks"],
        }
        langsmith_eval.finish_production_trace(ls_root, trace)
        emit(result)
        evaluation.evaluate_async(trace)
        return result
