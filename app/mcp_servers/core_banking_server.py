"""MCP server: Core Banking + CRM integration (customers, accounts, transactions, loans)."""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings  # noqa: E402
from app.mcp_servers.common import db_path, load_json, make_server, pick, query  # noqa: E402

CORE = db_path("core_banking", "core_banking.db")
mcp = make_server(
    "core_banking",
    settings.mcp_core_banking_port,
    "Core banking system of record and CRM. Use for customers, deposits, accounts, "
    "transactions, loan book quality and customer sentiment/NPS.",
)


@mcp.tool()
def search_customers(segment: str = "", region: str = "", name_contains: str = "", limit: int = 15) -> list[dict]:
    """Find customers. segment: Retail | Affluent | High Net Worth | SME | Corporate.
    region: North America | EMEA | APAC | LATAM. Returns id, name, segment, region, total balance."""
    sql = """SELECT c.customer_id, c.full_name, c.segment, c.region, c.city, c.kyc_status,
                    ROUND(COALESCE(SUM(a.balance),0),2) AS total_balance
             FROM customers c LEFT JOIN accounts a ON a.customer_id=c.customer_id AND a.status='Active'
             WHERE (:seg='' OR c.segment=:seg) AND (:reg='' OR c.region=:reg)
               AND (:nm='' OR c.full_name LIKE '%'||:nm||'%')
             GROUP BY c.customer_id ORDER BY total_balance DESC"""
    return query(CORE, sql, {"seg": segment, "reg": region, "nm": name_contains}, limit=max(1, min(limit, 50)))


@mcp.tool()
def get_customer_profile(customer_id: str) -> dict:
    """360-degree view of one customer: profile, accounts, loans and latest CRM interactions."""
    prof = query(CORE, "SELECT * FROM customers WHERE customer_id=?", (customer_id,))
    if not prof:
        return {"error": f"customer {customer_id} not found"}
    crm = [r for r in load_json("crm", "crm_interactions.json")["records"] if r["customer_id"] == customer_id]
    crm.sort(key=lambda r: r["date"], reverse=True)
    return {
        "profile": prof[0],
        "accounts": query(CORE, "SELECT account_id, product, balance, currency, status FROM accounts WHERE customer_id=?", (customer_id,)),
        "loans": query(CORE, "SELECT loan_id, loan_type, outstanding, interest_rate, status, days_past_due, credit_score FROM loans WHERE customer_id=?", (customer_id,)),
        "recent_crm_interactions": crm[:5],
    }


@mcp.tool()
def get_deposit_summary(group_by: str = "segment") -> list[dict]:
    """Deposit balances (active checking/savings/treasury accounts). group_by: segment | region | product."""
    col = pick(group_by, {"segment": "c.segment", "region": "c.region", "product": "a.product"}, "segment")
    sql = f"""SELECT {col} AS grp, COUNT(DISTINCT a.customer_id) AS customers, COUNT(*) AS accounts,
                     ROUND(SUM(a.balance),2) AS total_balance, ROUND(AVG(a.balance),2) AS avg_balance
              FROM accounts a JOIN customers c ON c.customer_id=a.customer_id
              WHERE a.status='Active' AND a.balance > 0 GROUP BY grp ORDER BY total_balance DESC"""
    return query(CORE, sql)


@mcp.tool()
def get_transaction_trends(group_by: str = "month", months: int = 6) -> list[dict]:
    """Transaction volume and value over the last N months. group_by: month | channel | merchant_category | region."""
    col = pick(group_by, {"month": "substr(t.txn_date,1,7)", "channel": "t.channel",
                          "merchant_category": "t.merchant_category", "region": "c.region"}, "month")
    months = max(1, min(int(months), 12))
    sql = f"""SELECT {col} AS grp, COUNT(*) AS txn_count, ROUND(SUM(t.amount),2) AS total_value,
                     ROUND(AVG(t.amount),2) AS avg_value,
                     ROUND(SUM(CASE WHEN t.direction='credit' THEN t.amount ELSE 0 END),2) AS inflows,
                     ROUND(SUM(CASE WHEN t.direction='debit' THEN t.amount ELSE 0 END),2) AS outflows
              FROM transactions t JOIN customers c ON c.customer_id=t.customer_id
              WHERE t.txn_date >= date('2026-09-30', ?) GROUP BY grp ORDER BY grp"""
    return query(CORE, sql, (f"-{months} months",))


@mcp.tool()
def get_loan_book_summary(group_by: str = "loan_type") -> list[dict]:
    """Loan portfolio quality. group_by: loan_type | status | segment | region.
    Returns outstanding balance, NPL (90+ DPD + Default) balance and NPL ratio."""
    col = pick(group_by, {"loan_type": "l.loan_type", "status": "l.status", "segment": "c.segment", "region": "c.region"}, "loan_type")
    sql = f"""SELECT {col} AS grp, COUNT(*) AS loans, ROUND(SUM(l.outstanding),2) AS outstanding,
                     ROUND(SUM(CASE WHEN l.status IN ('90+ DPD','Default') THEN l.outstanding ELSE 0 END),2) AS npl_balance,
                     ROUND(100.0*SUM(CASE WHEN l.status IN ('90+ DPD','Default') THEN l.outstanding ELSE 0 END)/SUM(l.outstanding),2) AS npl_ratio_pct,
                     ROUND(AVG(l.interest_rate),2) AS avg_rate, ROUND(AVG(l.credit_score),0) AS avg_credit_score
              FROM loans l JOIN customers c ON c.customer_id=l.customer_id GROUP BY grp ORDER BY outstanding DESC"""
    return query(CORE, sql)


@mcp.tool()
def get_customer_sentiment(segment: str = "", region: str = "") -> dict:
    """CRM + survey analytics: NPS score, sentiment mix, top contact reasons, churn risk.
    Optional filters: segment, region."""
    seg_map = {r["customer_id"]: r for r in query(CORE, "SELECT customer_id, segment, region FROM customers", limit=100000)}

    def keep(cid: str) -> bool:
        c = seg_map.get(cid, {})
        return (not segment or c.get("segment") == segment) and (not region or c.get("region") == region)

    surveys = [s for s in load_json("crm", "nps_survey.json")["records"] if keep(s["customer_id"])]
    inter = [r for r in load_json("crm", "crm_interactions.json")["records"] if keep(r["customer_id"])]
    if not surveys:
        return {"error": "no survey data for filter"}
    prom = sum(1 for s in surveys if s["score"] >= 9)
    det = sum(1 for s in surveys if s["score"] <= 6)
    by_seg: dict[str, list[int]] = defaultdict(list)
    for s in surveys:
        by_seg[s["segment"]].append(s["score"])
    return {
        "responses": len(surveys),
        "nps": round(100 * (prom - det) / len(surveys), 1),
        "nps_by_segment": {k: round(100 * (sum(v >= 9 for v in vs) - sum(v <= 6 for v in vs)) / len(vs), 1) for k, vs in by_seg.items()},
        "interactions": len(inter),
        "sentiment_mix": dict(Counter(r["sentiment"] for r in inter)),
        "top_contact_reasons": Counter(r["reason"] for r in inter).most_common(5),
        "resolution_rate_pct": round(100 * sum(r["resolved"] for r in inter) / max(1, len(inter)), 1),
        "high_churn_risk_customers": len({r["customer_id"] for r in inter if r["churn_risk"] >= 0.5}),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
