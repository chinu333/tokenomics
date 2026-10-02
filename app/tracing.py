"""Per-run tracing for FinSight.

Every governed run produces one trace (JSON file next to the control-plane DB) holding a span for
each LLM request/response, MCP tool call, TokenOps governance action and the post-run evaluation,
with timing, token counts, cost, Azure content-safety results and per-span checks.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings

TRACE_DIR = Path(settings.control_plane_db).resolve().parent / "finsight_traces"
MAX_MSG_CHARS = 12_000
MAX_TOOL_CHARS = 20_000
SEVERITY = ["safe", "low", "medium", "high"]
HARM_CATEGORIES = ("hate", "sexual", "violence", "self_harm")

_lock = threading.Lock()
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
# Heuristic jailbreak / prompt-injection phrases (user input and tool outputs = indirect injection).
_INJECTION = re.compile(
    r"ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier) (?:instructions|prompts|rules)"
    r"|disregard (?:all |the )?(?:previous|prior|system|above)[\w ]{0,20}"
    r"|forget (?:all |your )?(?:previous |prior )?instructions"
    r"|you are now (?:an? )?[\w-]+|developer mode|\bDAN\b|do anything now|jailbreak"
    r"|reveal (?:your|the) (?:system )?(?:prompt|instructions)|print (?:your|the) system prompt"
    r"|act as (?:an? )?(?:unfiltered|unrestricted|uncensored)[\w ]{0,20}"
    r"|bypass (?:the |your |all )?(?:safety|guardrails|filters|content policy)"
    r"|<\s*/?\s*system\s*>|\[\s*system\s*\]",
    re.IGNORECASE,
)


def _path(run_id: str) -> Path:
    if not _RUN_ID.match(run_id or ""):
        raise ValueError("invalid run id")
    return TRACE_DIR / f"{run_id}.json"


def load(run_id: str) -> dict | None:
    try:
        with _lock:
            return json.loads(_path(run_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return None


def clear_all() -> None:
    with _lock:
        shutil.rmtree(TRACE_DIR, ignore_errors=True)


def list_all(limit: int = 200) -> list[dict]:
    """Most recent traces first."""
    with _lock:
        files = sorted(TRACE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        out = []
        for f in files:
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def clip(text: Any, limit: int) -> tuple[str, bool]:
    s = text if isinstance(text, str) else json.dumps(text, default=str)
    return (s, False) if len(s) <= limit else (s[:limit] + f"\n...[{len(s) - limit:,} more chars]", True)


def scan_injection(text: str) -> list[str]:
    hits: list[str] = []
    for m in _INJECTION.finditer(text or ""):
        h = m.group(0).strip()[:60]
        if h.lower() not in (x.lower() for x in hits):
            hits.append(h)
        if len(hits) >= 5:
            break
    return hits


def content_safety(results: Any) -> dict:
    """Normalise Azure OpenAI ``content_filter_results`` / ``prompt_filter_results``."""
    if isinstance(results, list):  # prompt_filter_results: one entry per prompt index
        merged = [content_safety((r or {}).get("content_filter_results")) for r in results]
        merged = [m for m in merged if m["available"]]
        if not merged:
            return {"available": False}
        out = merged[0]
        for m in merged[1:]:
            for cat, sev in m["categories"].items():
                if SEVERITY.index(sev) > SEVERITY.index(out["categories"].get(cat, "safe")):
                    out["categories"][cat] = sev
            for k in ("filtered", "jailbreak", "indirect_attack", "protected_material"):
                out[k] = bool(out.get(k)) or bool(m.get(k)) if (out.get(k) is not None or m.get(k) is not None) else None
        out["worst"] = max(out["categories"].values(), key=SEVERITY.index, default="safe")
        return out
    if not isinstance(results, dict) or not results:
        return {"available": False}
    cats: dict[str, str] = {}
    filtered = False
    for cat in HARM_CATEGORIES:
        r = results.get(cat)
        if isinstance(r, dict):
            sev = str(r.get("severity", "safe")).lower()
            cats[cat] = sev if sev in SEVERITY else "safe"
            filtered = filtered or bool(r.get("filtered"))

    def detected(*keys: str) -> bool | None:
        vals = [results[k].get("detected") for k in keys if isinstance(results.get(k), dict)]
        return any(bool(v) for v in vals) if vals else None

    return {
        "available": bool(cats) or any(k in results for k in ("jailbreak", "indirect_attack")),
        "categories": cats,
        "worst": max(cats.values(), key=SEVERITY.index, default="safe"),
        "filtered": filtered,
        "jailbreak": detected("jailbreak"),
        "indirect_attack": detected("indirect_attack"),
        "protected_material": detected("protected_material_text", "protected_material_code"),
    }


def serialize_messages(messages: list) -> list[dict]:
    out = []
    for m in messages:
        m = m if isinstance(m, dict) else {"role": getattr(m, "type", "?"), "content": getattr(m, "content", "")}
        content, cut = clip(m.get("content") or "", MAX_MSG_CHARS)
        row: dict[str, Any] = {"role": m.get("role", "?"), "content": content}
        if cut:
            row["truncated"] = True
        if m.get("tool_calls"):
            row["tool_calls"] = [
                {"id": t.get("id"), "name": (t.get("function") or {}).get("name", t.get("name")),
                 "args": _maybe_json((t.get("function") or {}).get("arguments", t.get("args")))}
                for t in m["tool_calls"]
            ]
        for k in ("tool_call_id", "name"):
            if m.get(k):
                row[k] = m[k]
        out.append(row)
    return out


def _maybe_json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def check(name: str, value: str, status: str) -> dict:
    """status: pass | warn | fail | info"""
    return {"name": name, "value": value, "status": status}


def safety_checks(safety: dict) -> list[dict]:
    checks = []
    for side, label in (("prompt", "Input"), ("completion", "Output")):
        s = safety.get(side) or {}
        if not s.get("available"):
            continue
        worst = s.get("worst", "safe")
        checks.append(check(f"{label} harmful content", f"{worst}" + (" (filtered)" if s.get("filtered") else ""),
                            "pass" if worst == "safe" else "warn" if worst == "low" else "fail"))
        if s.get("jailbreak") is not None:
            checks.append(check(f"{label} jailbreak (Prompt Shields)", "detected" if s["jailbreak"] else "not detected",
                                "fail" if s["jailbreak"] else "pass"))
        if s.get("indirect_attack") is not None:
            checks.append(check("Indirect attack (Prompt Shields)", "detected" if s["indirect_attack"] else "not detected",
                                "fail" if s["indirect_attack"] else "pass"))
        if s.get("protected_material") is not None:
            checks.append(check("Protected material", "detected" if s["protected_material"] else "not detected",
                                "warn" if s["protected_material"] else "pass"))
    if not checks:
        checks.append(check("Content safety", "not reported by the model endpoint", "info"))
    return checks


class RunTrace:
    """Collects spans for one run; written to disk on ``finish`` and after evaluation."""

    def __init__(self, run_id: str, *, question: str, persona: str, department: str, mode: str,
                 model: str, tools: list[str], system_prompt: str) -> None:
        self.t0 = time.time()
        self.data: dict[str, Any] = {
            "version": 1, "run_id": run_id, "question": question, "persona": persona, "department": department,
            "mode": mode, "model_initial": model, "system_prompt": system_prompt, "tools_available": sorted(tools),
            "started_at": self.t0, "status": "running", "spans": [],
            "evaluation": {"status": "pending"},
            "evaluation_langsmith": {"status": "pending"},
        }

    @property
    def run_id(self) -> str:
        return self.data["run_id"]

    @property
    def spans(self) -> list[dict]:
        return self.data["spans"]

    def now(self) -> float:
        return time.time()

    def span(self, kind: str, name: str, start: float, end: float | None = None, parent: str | None = None,
             **fields: Any) -> dict:
        end = time.time() if end is None else end
        s = {"id": f"s{len(self.spans) + 1}", "parent": parent, "kind": kind, "name": name,
             "start_ms": int((start - self.t0) * 1000), "end_ms": int((end - self.t0) * 1000),
             "latency_ms": int((end - start) * 1000), **fields}
        s.setdefault("status", "ok")
        s.setdefault("checks", [])
        self.spans.append(s)
        return s

    def finish(self, *, status: str, answer: str = "", cost_usd: float = 0.0, budget_usd: float | None = None,
               model_final: str | None = None, halt: dict | None = None, governance_events: list | None = None,
               error: str | None = None) -> None:
        ended = time.time()
        llm = [s for s in self.spans if s["kind"] == "llm"]
        tools = [s for s in self.spans if s["kind"] == "tool"]
        tok = lambda k: sum((s.get("tokens") or {}).get(k, 0) for s in llm)  # noqa: E731
        self.data.update({
            "status": status, "answer": answer, "error": error, "halt": halt, "ended_at": ended,
            "duration_ms": int((ended - self.t0) * 1000), "model_final": model_final or self.data["model_initial"],
            "budget_usd": budget_usd, "governance_events": governance_events or [],
            "totals": {
                "llm_calls": sum(1 for s in llm if s["status"] == "ok"),
                "llm_blocked": sum(1 for s in llm if s["status"] == "blocked"),
                "tool_calls": len(tools),
                "tool_errors": sum(1 for s in tools if s["status"] != "ok"),
                "input_tokens": tok("input"), "output_tokens": tok("output"),
                "cached_tokens": tok("cached"), "reasoning_tokens": tok("reasoning"),
                "cost_usd": cost_usd,
                "llm_latency_ms": sum(s.get("latency_ms", 0) for s in llm),
                "tool_latency_ms": sum(s.get("latency_ms", 0) for s in tools),
                "governance_actions": sum(1 for s in self.spans if s["kind"] == "governance"),
            },
        })
        self.data["evaluation"]["safety"] = self._safety_rollup()
        if status != "completed" or not answer:
            self.data["evaluation"].update(status="skipped", reason=f"run {status} - no final answer to evaluate")
            self.data["evaluation_langsmith"].update(status="skipped", reason=f"run {status} - no final answer to evaluate")
        self.save(create=True)

    def _safety_rollup(self) -> dict:
        cats: dict[str, str] = {}
        available = False
        shields: bool | None = None
        indirect: bool | None = None
        for s in self.spans:
            for side in ((s.get("safety") or {}).get("prompt"), (s.get("safety") or {}).get("completion")):
                if not side or not side.get("available"):
                    continue
                available = True
                for cat, sev in side.get("categories", {}).items():
                    if SEVERITY.index(sev) > SEVERITY.index(cats.get(cat, "safe")):
                        cats[cat] = sev
                    cats.setdefault(cat, sev)
                if side.get("jailbreak") is not None:
                    shields = bool(shields) or side["jailbreak"]
                if side.get("indirect_attack") is not None:
                    indirect = bool(indirect) or side["indirect_attack"]
        q_hits = scan_injection(self.data["question"])
        flagged = [{"span": s["id"], "tool": s["name"], "matches": s["injection"]}
                   for s in self.spans if s["kind"] == "tool" and s.get("injection")]
        return {
            "harmful_content": {"severity": max(cats.values(), key=SEVERITY.index, default="safe"),
                                "categories": cats, "source": "Azure OpenAI content filter" if available else "not reported"},
            "jailbreak": {"detected": bool(shields) or bool(q_hits), "prompt_shields": shields, "heuristic": q_hits},
            "prompt_injection": {"detected": bool(flagged) or bool(indirect), "flagged": flagged,
                                 "indirect_attack": indirect},
        }

    def save(self, create: bool = False) -> None:
        path = _path(self.run_id)
        with _lock:
            if not create and not path.exists():  # history was cleared meanwhile
                return
            TRACE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, default=str), encoding="utf-8")
            tmp.replace(path)
