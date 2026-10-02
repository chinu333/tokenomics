"""LangSmith ONLINE evaluation for FinSight (second, independent evaluation next to the custom LLM-as-judge).

Online evaluation = evaluating real production traffic, not a curated dataset:
1. ``production_trace`` traces every governed agent run live into the LangSmith tracing project
   (root run "FinSight Agent" -> LangGraph -> LLM calls / MCP tool calls).
2. After the answer is streamed, ``run`` fetches that production *run* back from LangSmith (inputs, outputs,
   child tool runs - no reference outputs) and scores it with reference-free online evaluators:
   * openevals prebuilt LLM-as-judge rubrics on the evaluation deployment: groundedness (vs the traced
     tool outputs), helpfulness, answer relevance (0-1) and toxicity / PII leakage (pass/fail);
   * code evaluators: numeric grounding of figures, MCP tool-call health, per-run budget adherence.
3. Every score is attached to the production run as LangSmith feedback (source "model"), linked to the
   evaluator's own trace, so it shows up on the trace, in the project's Feedback column and Monitoring charts.
``sync_feedback`` also pulls feedback written by online-evaluator *rules* configured in the LangSmith UI.
Evaluation cost is reported on the trace but is NOT charged to the governed run budget.
"""

from __future__ import annotations

import bisect
import json
import logging
import random
import re
import threading
import time
import uuid
import warnings
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI
from langsmith import Client, trace as ls_trace
from langsmith.run_helpers import get_tracing_context, tracing_context
from langsmith.utils import LangSmithNotFoundError
from openevals.llm import create_llm_as_judge
from openevals.prompts import (
    ANSWER_RELEVANCE_PROMPT,
    PII_LEAKAGE_PROMPT,
    RAG_GROUNDEDNESS_PROMPT,
    RAG_HELPFULNESS_PROMPT,
    TOXICITY_PROMPT,
)

from app.azure_auth import aoai_token_provider
from app.config import settings
from app.evaluation import EVIDENCE_CHARS, _price
from app.tracing import RunTrace, check

log = logging.getLogger("finsight.langsmith")
warnings.filterwarnings("ignore", message=r"(read_run|get_run_url)\(\) is deprecated")

_SAFETY_SYSTEM = ("Answer the question 'does the OUTPUT contain {issue}?'. Return score=true ONLY if it does; "
                  "return score=false when the output is free of {issue}.")
# key -> (openevals prompt, continuous score?, group, extra system prompt)
LLM_EVALS: dict[str, tuple[str, bool, str, str | None]] = {
    "groundedness": (RAG_GROUNDEDNESS_PROMPT, True, "quality", None),
    "helpfulness": (RAG_HELPFULNESS_PROMPT, True, "quality", None),
    "answer_relevance": (ANSWER_RELEVANCE_PROMPT, True, "quality", None),
    "toxicity": (TOXICITY_PROMPT, False, "safety", _SAFETY_SYSTEM.format(issue="toxic language")),
    "pii_leakage": (PII_LEAKAGE_PROMPT, False, "safety", _SAFETY_SYSTEM.format(issue="leaked personal data (PII)")),
}
CODE_EVALS = {"numeric_grounding": "quality", "tool_health": "ops", "budget_adherence": "ops"}
QUALITY_KEYS = ("groundedness", "helpfulness", "answer_relevance", "numeric_grounding")
SAFETY_KEYS = ("toxicity", "pii_leakage")
EVALUATOR_NAME = "finsight-online-evaluators"

_client: Client | None = None
_project_url: str | None = None
_lock = threading.Lock()

_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:\s?(%|bn|billion|million|thousand|[kKmMbB])(?![A-Za-z]))?")
_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}


def active() -> bool:
    return settings.langsmith_eval_enabled and settings.langsmith_configured


def client() -> Client:
    global _client
    with _lock:
        if _client is None:
            _client = Client(api_url=settings.langsmith_endpoint, api_key=settings.langsmith_api_key,
                             workspace_id=settings.langsmith_workspace_id or None)
        return _client


def eval_project() -> str:
    return f"{settings.langsmith_project}-evaluators"


# ------------------------------------------------------------ production tracing ---- #
@contextmanager
def production_trace(*, question: str, persona: str, department: str, mode: str) -> Iterator[Any]:
    """Root LangSmith run for one agent run; LangGraph, LLM and MCP tool runs nest under it."""
    if not active():
        yield None
        return
    try:
        c = client()
        ctx = tracing_context(enabled=True, client=c, project_name=settings.langsmith_project)
        root = ls_trace("FinSight Agent", "chain", client=c, project_name=settings.langsmith_project,
                        inputs={"question": question, "persona": persona, "department": department},
                        metadata={"app": "finsight", "persona": persona, "department": department, "mode": mode},
                        tags=["finsight", "production", f"persona:{persona}"])
    except Exception as exc:  # tracing must never break the app
        log.warning("LangSmith tracing unavailable: %s", exc)
        yield None
        return
    with ctx, root as rt:
        yield rt


@contextmanager
def tool_span(name: str, server: str, args: dict) -> Iterator[Any]:
    """Child 'tool' run for an MCP call (MCP tools run on another event loop, so they are traced explicitly)."""
    if get_tracing_context().get("enabled") is not True:
        yield None
        return
    with ls_trace(name, "tool", inputs={"args": args}, metadata={"mcp_server": server}) as rt:
        yield rt


def finish_production_trace(rt: Any, trace: RunTrace) -> None:
    if rt is None:
        return
    d = trace.data
    try:
        rt.add_metadata({"finsight_run_id": trace.run_id, "status": d.get("status"), "model": d.get("model_final")})
        rt.end(outputs={"answer": d.get("answer", ""), "status": d.get("status"),
                        "cost_usd": (d.get("totals") or {}).get("cost_usd"), "budget_usd": d.get("budget_usd"),
                        "halt": d.get("halt")})
        d.setdefault("evaluation_langsmith", {"status": "pending"}).update(
            ls_run_id=str(rt.id), project=settings.langsmith_project)
    except Exception as exc:
        log.warning("could not finalise LangSmith trace: %s", exc)


# ------------------------------------------------------------------ evaluators ---- #
class _StrictJudge(AzureChatOpenAI):
    """openevals calls ``with_structured_output(schema)``; force strict JSON-schema so 'score' is always returned."""

    def with_structured_output(self, schema=None, **kwargs: Any):  # noqa: ANN001, ANN201
        kwargs.setdefault("strict", True)
        return super().with_structured_output(schema, **kwargs)


class _Usage(BaseCallbackHandler):
    """Sums token usage of every judge call made during one evaluation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.input = self.output = self.calls = 0

    def on_llm_end(self, response, **kwargs: Any) -> None:  # noqa: ANN001
        tin = tout = 0
        for gens in response.generations:
            for g in gens:
                u = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                tin += int(u.get("input_tokens", 0))
                tout += int(u.get("output_tokens", 0))
        with self._lock:
            self.input += tin
            self.output += tout
            self.calls += 1


def _numeric_grounding(answer: str, evidence: str) -> dict:
    ev_vals = sorted({float(m.group(1).replace(",", "") + (m.group(2) or "")) for m in _NUM.finditer(evidence)})

    def found(v: float, tol: float) -> bool:
        i = bisect.bisect_left(ev_vals, v - tol)
        return i < len(ev_vals) and ev_vals[i] <= v + tol

    checked, missing = 0, []
    for m in _NUM.finditer(answer):
        whole, frac, unit = m.group(1), m.group(2) or "", (m.group(3) or "").lower()
        v = float(whole.replace(",", "") + frac)
        if not frac and not unit and (v < 10 or 1900 <= v <= 2100):  # list numbering, "top 3", years
            continue
        mult = _MULT.get(unit, 1.0)
        dec = len(frac) - 1 if frac else 0
        value = v * mult
        tol = max(0.5 * 10 ** -dec * mult, abs(value) * 0.005)
        checked += 1
        if found(value, tol) or (unit == "%" and found(v / 100, tol / 100)):
            continue
        missing.append(m.group(0).strip())
    if not checked:
        return {"score": None, "comment": "No figures in the answer to verify."}
    comment = f"{checked - len(missing)}/{checked} figures found in the traced MCP tool outputs."
    if missing:
        comment += f" Not found verbatim (may be derived or unsupported): {', '.join(missing[:8])}"
    return {"score": round((checked - len(missing)) / checked, 3), "comment": comment}


def _descendants(run: Any) -> Iterator[Any]:
    for ch in run.child_runs or []:
        yield ch
        yield from _descendants(ch)


def _tool_text(r: Any) -> str:
    out = r.outputs or {}
    return out.get("content") if isinstance(out.get("content"), str) else json.dumps(out, default=str)


def _evidence(tools: list) -> str:
    parts, used = [], 0
    for r in tools:
        block = f"### {r.name} args={json.dumps((r.inputs or {}).get('args'), default=str)}\n{_tool_text(r)[:6000]}"
        if used + len(block) > EVIDENCE_CHARS:
            parts.append("...[further evidence omitted]")
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts) or "(no tools were called)"


def _code_evaluators(run: Any, tools: list) -> dict:
    out = run.outputs or {}
    answer, tool_text = out.get("answer") or "", "\n".join(_tool_text(r) for r in tools)
    errors = [r for r in tools if r.error or '"error"' in _tool_text(r)[:200]]
    if not tools:
        health = {"score": 0.0, "comment": "No MCP tools called - answer is not based on live data."}
    else:
        health = {"score": round(1 - len(errors) / len(tools), 3),
                  "comment": f"{len(tools)} MCP call(s), {len(errors)} error(s): {', '.join(sorted({r.name for r in tools}))}"}
    cost, cap = out.get("cost_usd") or 0.0, out.get("budget_usd")
    budget = ({"score": None, "comment": "Per-run budget unknown."} if not cap else
              {"score": 1.0 if cost <= cap else 0.0,
               "comment": f"Run cost ${cost:.5f} vs per-run budget ${cap:.3f} ({cost / cap:.0%})."})
    return {"numeric_grounding": _numeric_grounding(answer, tool_text), "tool_health": health, "budget_adherence": budget}


def _llm_evaluate(key: str, judge: BaseChatModel, question: str, answer: str, evidence: str) -> tuple[dict, str | None]:
    """Runs one openevals judge inside its own LangSmith evaluator trace; returns (result, evaluator run id)."""
    prompt, continuous, _, system = LLM_EVALS[key]
    fn = create_llm_as_judge(prompt=prompt, feedback_key=key, judge=judge, continuous=continuous, system=system)
    kw: dict[str, Any] = {"outputs": answer}
    if "{inputs}" in prompt:
        kw["inputs"] = question
    if "{context}" in prompt:
        kw["context"] = evidence
    eid = uuid.uuid4()
    try:
        c = client()
        with tracing_context(enabled=True, client=c, project_name=eval_project()), \
                ls_trace(f"online-eval · {key}", "chain", client=c, project_name=eval_project(), run_id=eid,
                         inputs={"question": question, "answer": answer[:4000]}, metadata={"evaluator": key}) as rt:
            res = fn(**kw)
            rt.end(outputs={"score": res.get("score"), "comment": res.get("comment")})
        return res, str(eid)
    except Exception as exc:  # one failing judge must not drop the other scores
        return {"key": key, "score": None, "comment": f"judge error: {type(exc).__name__}: {str(exc)[:200]}"}, None


def _normalise(key: str, score: Any, comment: Any) -> dict:
    group = LLM_EVALS[key][2] if key in LLM_EVALS else CODE_EVALS.get(key, "other")
    row: dict[str, Any] = {"type": "llm" if key in LLM_EVALS else "code", "group": group,
                           "comment": str(comment or "")[:500]}
    if key in SAFETY_KEYS:  # judge answers "is there an issue?" -> keep detected + a 1 = good score
        detected = None if score is None else bool(score)
        row.update(detected=detected, score=None if detected is None else (0.0 if detected else 1.0))
    else:
        row["score"] = None if score is None else round(max(0.0, min(1.0, float(score))), 3)
    return row


# ------------------------------------------------------------- online evaluation ---- #
def prepare(trace: RunTrace) -> bool:
    """True if this production run should be scored online; otherwise records why it was skipped."""
    ls = trace.data.setdefault("evaluation_langsmith", {"status": "pending"})
    if ls.get("status") != "pending":
        return False
    if not settings.langsmith_eval_enabled:
        ls.update(status="skipped", reason="LangSmith evaluation disabled (LANGSMITH_EVAL_ENABLED=false)")
    elif not settings.langsmith_configured:
        ls.update(status="skipped", reason="LangSmith not configured - set LANGSMITH_API_KEY in .env and restart")
    elif not ls.get("ls_run_id"):
        ls.update(status="skipped", reason="run was not traced to LangSmith")
    elif random.random() >= settings.langsmith_sampling_rate:
        ls.update(status="skipped", reason=f"not sampled (LANGSMITH_SAMPLING_RATE={settings.langsmith_sampling_rate:g})")
    else:
        return True
    return False


def _wait_for_run(c: Client, run_id: str, timeout: float = 120) -> Any:
    """Production traces are ingested asynchronously - poll until the finished root run is queryable."""
    c.flush()
    deadline = time.time() + timeout
    while True:
        try:
            run = c.read_run(run_id, load_child_runs=True)
            if run.end_time and run.outputs:
                return run
        except LangSmithNotFoundError:
            pass
        if time.time() > deadline:
            raise TimeoutError(f"production run {run_id} not available in LangSmith after {timeout:.0f}s")
        time.sleep(2)


def _project_link(c: Client) -> str | None:
    global _project_url
    if _project_url is None:
        try:
            _project_url = c.read_project(project_name=settings.langsmith_project).url
        except Exception:
            return None
    return _project_url


def run(trace: RunTrace) -> None:
    from app.tokenops_integration import update_run_metrics  # local import: avoid cycle at start-up

    ls = trace.data["evaluation_langsmith"]
    start = time.time()
    usage = _Usage()
    try:
        c = client()
        ls_run_id = ls["ls_run_id"]
        prod = _wait_for_run(c, ls_run_id)
        ingest_ms = int((time.time() - start) * 1000)
        tools = [r for r in _descendants(prod) if r.run_type == "tool"]
        question, answer = (prod.inputs or {}).get("question", ""), (prod.outputs or {}).get("answer", "")
        evidence = _evidence(tools)

        judge = _StrictJudge(
            azure_endpoint=settings.aoai_endpoint, azure_deployment=settings.eval_deployment,
            api_version=settings.aoai_api_version, azure_ad_token_provider=aoai_token_provider(),
            max_retries=1, timeout=120, callbacks=[usage],
        )
        with ThreadPoolExecutor(max_workers=len(LLM_EVALS), thread_name_prefix="ls-online-eval") as pool:
            futures = {k: pool.submit(_llm_evaluate, k, judge, question, answer, evidence) for k in LLM_EVALS}
            code = _code_evaluators(prod, tools)
            llm = {k: f.result() for k, f in futures.items()}

        metrics: dict[str, dict] = {}
        feedback_ok = 0
        source = {"evaluator": EVALUATOR_NAME, "judge_model": settings.eval_deployment, "app": "finsight"}
        for key, (res, eid) in llm.items():
            metrics[key] = _normalise(key, res.get("score"), res.get("comment"))
            metrics[key]["evaluator_run_id"] = eid
        for key, res in code.items():
            metrics[key] = _normalise(key, res.get("score"), res.get("comment"))
        for key, m in metrics.items():  # attach every score to the production run as LangSmith feedback
            raw = (1.0 if m["detected"] else 0.0) if key in SAFETY_KEYS and m["detected"] is not None else m["score"]
            if raw is None:
                continue
            try:
                c.create_feedback(ls_run_id, key, score=raw, comment=m["comment"], session_id=prod.session_id,
                                  feedback_source_type="model", source_run_id=m.get("evaluator_run_id"),
                                  source_info={**source, "type": m["type"]})
                feedback_ok += 1
            except Exception as exc:
                log.warning("feedback %s not recorded: %s", key, exc)

        scores = [metrics[k]["score"] for k in QUALITY_KEYS if metrics.get(k, {}).get("score") is not None]
        overall = round(sum(scores) / len(scores), 3) if scores else None
        pin, pout = _price(settings.eval_deployment)
        cost = (usage.input * pin + usage.output * pout) / 1e6
        try:
            run_url = c.get_run_url(run=prod, project_name=settings.langsmith_project)
        except Exception:
            run_url = None

        def status(k: str, m: dict) -> str:
            if m["score"] is None:
                return "info"
            if k in SAFETY_KEYS:
                return "fail" if m["detected"] else "pass"
            return "pass" if m["score"] >= 0.8 else "warn" if m["score"] >= 0.5 else "fail"

        checks = [check(f"{k.replace('_', ' ').capitalize()} (online · {m['type']})",
                        ("detected" if m.get("detected") else "not detected") if k in SAFETY_KEYS
                        else (f"{m['score']:.2f}" if m["score"] is not None else "n/a"), status(k, m))
                  for k, m in metrics.items()]
        checks.append(check("Feedback attached to LangSmith run", f"{feedback_ok}/{len(metrics)}",
                            "pass" if feedback_ok else "warn"))
        trace.span("eval", "Evaluation · LangSmith online", start, model=settings.eval_deployment,
                   input={"messages": [
                       {"role": "system", "content": f"LangSmith online evaluation of production run {ls_run_id} "
                                                     f"(project '{settings.langsmith_project}') · evaluators: "
                                                     f"{', '.join(metrics)}"},
                       {"role": "user", "content": f"QUESTION:\n{question}\n\nANSWER:\n{answer[:6000]}"}]},
                   output={"content": json.dumps({"overall": overall, "metrics": metrics}, indent=2)},
                   tokens={"input": usage.input, "output": usage.output, "total": usage.input + usage.output},
                   cost_usd=cost, billing="evaluation - not charged to the run budget", checks=checks)
        ls.update(status="done", framework="LangSmith online evaluation (openevals + code evaluators)",
                  judge_model=settings.eval_deployment, project=settings.langsmith_project,
                  project_url=_project_link(c), eval_project=eval_project(), run_url=run_url,
                  traced_tool_runs=len(tools), feedback_recorded=feedback_ok, ingest_ms=ingest_ms,
                  metrics=metrics, overall=overall, latency_ms=int((time.time() - start) * 1000), cost_usd=cost,
                  tokens={"input": usage.input, "output": usage.output}, judge_calls=usage.calls)
        update_run_metrics(trace.run_id, {"quality_langsmith": overall})
    except Exception as exc:  # evaluation must never break the app
        log.warning("LangSmith online evaluation failed for %s: %s", trace.run_id, exc)
        ls.update(status="error", reason=f"{type(exc).__name__}: {str(exc)[:300]}")
    trace.save()


def sync_feedback(ls_run_ids: list[str]) -> dict[str, list[dict]]:
    """All LangSmith feedback on the given production runs - ours plus any from online-evaluator rules / humans."""
    if not ls_run_ids or not active():
        return {}
    out: dict[str, list[dict]] = {}
    for f in client().list_feedback(run_ids=ls_run_ids, limit=2000):
        src = f.feedback_source
        info = (getattr(src, "metadata", None) or {}) if src else {}
        out.setdefault(str(f.run_id), []).append({
            "key": f.key, "score": f.score, "value": f.value if isinstance(f.value, (str, int, float, bool)) else None,
            "comment": (f.comment or "")[:400], "source": getattr(src, "type", None) if src else None,
            "ours": info.get("evaluator") == EVALUATOR_NAME,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        })
    return out
