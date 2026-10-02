"""MCP server: Risk & Compliance hub (AML, credit risk, findings, policy documents)."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings  # noqa: E402
from app.mcp_servers.common import db_path, make_server, pick, query  # noqa: E402

RISK = db_path("risk_compliance", "risk_compliance.db")
POLICY_DIR = db_path("risk_compliance", "policies")
mcp = make_server(
    "risk_compliance",
    settings.mcp_risk_compliance_port,
    "Risk & compliance hub. Use for AML alerts, credit risk (PD/LGD/EAD, watchlist), "
    "regulatory findings, and to look up internal policy thresholds.",
)


@mcp.tool()
def get_aml_alert_summary(min_risk_score: int = 0) -> dict:
    """AML alert pipeline: counts by status and scenario, SLA breaches, amount under review."""
    rows = query(RISK, "SELECT * FROM aml_alerts WHERE risk_score >= ?", (int(min_risk_score),), limit=100000)
    open_rows = [r for r in rows if not r["status"].startswith("Closed")]
    sla = [r for r in open_rows if (r["risk_score"] >= 80 and r["days_open"] > 1) or (50 <= r["risk_score"] < 80 and r["days_open"] > 7)]
    return {
        "total_alerts": len(rows),
        "open_alerts": len(open_rows),
        "by_status": dict(Counter(r["status"] for r in rows)),
        "open_by_scenario": dict(Counter(r["scenario"] for r in open_rows).most_common()),
        "open_high_risk": sum(1 for r in open_rows if r["risk_score"] >= 80),
        "sla_breaches": len(sla),
        "amount_under_review": round(sum(r["amount"] for r in open_rows), 2),
        "sars_filed": sum(1 for r in rows if "SAR" in r["status"]),
    }


@mcp.tool()
def list_high_risk_aml_alerts(min_risk_score: int = 80, limit: int = 15) -> list[dict]:
    """Open AML alerts at or above min_risk_score, oldest first."""
    return query(RISK, """SELECT alert_id, customer_id, alert_date, scenario, risk_score, amount, status, assigned_to, days_open
                          FROM aml_alerts WHERE risk_score >= ? AND status NOT LIKE 'Closed%'
                          ORDER BY days_open DESC""", (int(min_risk_score),), limit=max(1, min(limit, 50)))


@mcp.tool()
def get_credit_risk_summary(group_by: str = "internal_rating") -> list[dict]:
    """Credit-risk exposure: EAD, expected loss (PD x LGD x EAD), avg PD. group_by: internal_rating | watchlist."""
    col = pick(group_by, {"internal_rating": "internal_rating", "watchlist": "watchlist"}, "internal_rating")
    return query(RISK, f"""SELECT {col} AS grp, COUNT(*) AS borrowers, ROUND(SUM(ead),2) AS ead,
                                  ROUND(SUM(expected_loss),2) AS expected_loss, ROUND(AVG(pd)*100,2) AS avg_pd_pct,
                                  ROUND(AVG(lgd)*100,2) AS avg_lgd_pct
                           FROM credit_risk GROUP BY grp ORDER BY expected_loss DESC""")


@mcp.tool()
def get_watchlist(limit: int = 15) -> list[dict]:
    """Borrowers on the credit watchlist (PD > 8%), largest expected loss first."""
    return query(RISK, """SELECT customer_id, internal_rating, ROUND(pd*100,2) AS pd_pct, ead, expected_loss, last_review
                          FROM credit_risk WHERE watchlist=1 ORDER BY expected_loss DESC""", limit=max(1, min(limit, 50)))


@mcp.tool()
def get_compliance_findings(severity: str = "", status: str = "") -> dict:
    """Regulatory/audit findings. severity: Critical | High | Medium | Low. status: Open | In Remediation | Closed | Overdue."""
    rows = query(RISK, """SELECT * FROM compliance_findings WHERE (?='' OR severity=?) AND (?='' OR status=?)
                          ORDER BY CASE severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, due_date""",
                 (severity, severity, status, status), limit=1000)
    return {"count": len(rows), "by_area": dict(Counter(r["area"] for r in rows)),
            "by_status": dict(Counter(r["status"] for r in rows)), "findings": rows[:20]}


def _local_search(q: str, top_k: int) -> list[dict]:
    terms = [t for t in re.findall(r"[a-z0-9]+", q.lower()) if len(t) > 2]
    scored = []
    for f in POLICY_DIR.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        low = text.lower()
        score = sum(low.count(t) for t in terms)
        if score:
            scored.append({"document": f.name, "score": score, "content": text[:1800]})
    return sorted(scored, key=lambda r: r["score"], reverse=True)[:top_k]


def _azure_search(q: str, top_k: int) -> list[dict]:
    from azure.search.documents import SearchClient

    from app.azure_auth import credential

    client = SearchClient(settings.search_endpoint, settings.search_index, credential())
    return [{"document": r.get("title"), "score": r.get("@search.score"), "content": (r.get("content") or "")[:1800]}
            for r in client.search(search_text=q, top=top_k)]


@mcp.tool()
def search_policy_documents(search_query: str, top_k: int = 2) -> dict:
    """Search internal policy documents (AML thresholds, credit risk appetite, KYC, wealth
    suitability, AI cost governance). Uses Azure AI Search when configured, else local index."""
    top_k = max(1, min(int(top_k), 4))
    if settings.search_endpoint:
        try:
            return {"engine": "azure_ai_search", "results": _azure_search(search_query, top_k)}
        except Exception as exc:  # fall back so the agent still gets an answer
            return {"engine": "local_fallback", "azure_error": str(exc)[:200], "results": _local_search(search_query, top_k)}
    return {"engine": "local_keyword_index", "results": _local_search(search_query, top_k)}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
