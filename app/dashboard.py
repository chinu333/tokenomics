"""Leadership dashboard + analytics aggregates, computed live from the ./data sources."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.config import settings

D = settings.data_dir
CORE = D / "core_banking" / "core_banking.db"
WEALTH = D / "wealth" / "wealth.db"
RISK = D / "risk_compliance" / "risk_compliance.db"
DW = D / "warehouse" / "enterprise_dw.db"


def _q(db: Path, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def _one(db: Path, sql: str, params: tuple = ()) -> Any:
    rows = _q(db, sql, params)
    return next(iter(rows[0].values())) if rows else None


def _pct(cur: float, prev: float) -> float | None:
    return round((cur - prev) / prev * 100, 1) if prev else None


def dashboard() -> dict:
    months = [r["month"] for r in _q(DW, "SELECT DISTINCT month FROM kpi_monthly ORDER BY month")]
    last, ttm, prior = months[-1], months[-12:], months[-24:-12]
    ph = lambda xs: ",".join("?" * len(xs))  # noqa: E731

    def agg(ms: list[str]) -> dict:
        return _q(DW, f"""SELECT SUM(revenue) revenue, SUM(net_income) net_income, SUM(operating_expense) opex,
                            SUM(net_interest_income) nii, SUM(fee_income) fees, SUM(credit_provisions) provisions,
                            SUM(new_customers) new_customers, SUM(attrited_customers) attrited
                          FROM kpi_monthly WHERE month IN ({ph(ms)})""", tuple(ms))[0]

    cur, prv = agg(ttm), agg(prior)
    bal = lambda m: _q(DW, "SELECT SUM(deposits) deposits, SUM(loans_outstanding) loans, SUM(aum) aum, AVG(nps) nps "  # noqa: E731
                           "FROM kpi_monthly WHERE month=?", (m,))[0]
    b_now, b_ya = bal(last), bal(months[-13])

    trend = _q(DW, """SELECT month, SUM(revenue) revenue, SUM(net_income) net_income, SUM(operating_expense) opex
                      FROM kpi_monthly GROUP BY month ORDER BY month""")
    by_bl = _q(DW, f"""SELECT business_line, SUM(revenue) revenue, SUM(net_income) net_income
                       FROM kpi_monthly WHERE month IN ({ph(ttm)}) GROUP BY business_line ORDER BY revenue DESC""", tuple(ttm))
    by_region = _q(DW, f"""SELECT region, SUM(revenue) revenue, SUM(net_income) net_income
                           FROM kpi_monthly WHERE month IN ({ph(ttm)}) GROUP BY region ORDER BY revenue DESC""", tuple(ttm))

    loan_total = _one(CORE, "SELECT SUM(outstanding) FROM loans") or 0
    npl = _one(CORE, "SELECT SUM(outstanding) FROM loans WHERE status IN ('90+ DPD','Default')") or 0
    risk = {
        "aml_open": _one(RISK, "SELECT COUNT(*) FROM aml_alerts WHERE status NOT LIKE 'Closed%'"),
        "aml_high_open": _one(RISK, "SELECT COUNT(*) FROM aml_alerts WHERE status NOT LIKE 'Closed%' AND risk_score>=80"),
        "sar_filed": _one(RISK, "SELECT COUNT(*) FROM aml_alerts WHERE status LIKE 'Escalated%'"),
        "npl_ratio": round(npl / loan_total * 100, 2) if loan_total else 0,
        "expected_loss": _one(RISK, "SELECT SUM(expected_loss) FROM credit_risk"),
        "watchlist": _one(RISK, "SELECT COUNT(*) FROM credit_risk WHERE watchlist=1"),
        "findings_open": _one(RISK, "SELECT COUNT(*) FROM compliance_findings WHERE status!='Closed'"),
        "findings_overdue": _one(RISK, "SELECT COUNT(*) FROM compliance_findings WHERE status='Overdue'"),
        "findings_critical": _one(RISK, "SELECT COUNT(*) FROM compliance_findings WHERE severity='Critical' AND status!='Closed'"),
    }
    wealth = _q(WEALTH, """SELECT COUNT(*) portfolios, AVG(ytd_return_pct) avg_ytd, AVG(benchmark_ytd_pct) avg_bench,
                             SUM(CASE WHEN ytd_return_pct < benchmark_ytd_pct THEN 1 ELSE 0 END) underperforming
                           FROM portfolios""")[0]
    customers = _one(CORE, "SELECT COUNT(*) FROM customers")

    return {
        "as_of": last,
        "kpis": [
            {"id": "revenue", "label": "Revenue (TTM)", "value": cur["revenue"], "unit": "$", "delta": _pct(cur["revenue"], prv["revenue"])},
            {"id": "net_income", "label": "Net Income (TTM)", "value": cur["net_income"], "unit": "$", "delta": _pct(cur["net_income"], prv["net_income"])},
            {"id": "efficiency", "label": "Cost / Income", "value": round(cur["opex"] / cur["revenue"] * 100, 1), "unit": "%",
             "delta": round(cur["opex"] / cur["revenue"] * 100 - prv["opex"] / prv["revenue"] * 100, 1), "invert": True},
            {"id": "deposits", "label": "Deposits", "value": b_now["deposits"], "unit": "$", "delta": _pct(b_now["deposits"], b_ya["deposits"])},
            {"id": "loans", "label": "Loans Outstanding", "value": b_now["loans"], "unit": "$", "delta": _pct(b_now["loans"], b_ya["loans"])},
            {"id": "aum", "label": "Assets Under Mgmt", "value": b_now["aum"], "unit": "$", "delta": _pct(b_now["aum"], b_ya["aum"])},
            {"id": "nps", "label": "Net Promoter Score", "value": round(b_now["nps"], 1), "unit": "", "delta": round(b_now["nps"] - b_ya["nps"], 1)},
            {"id": "net_new", "label": "Net New Customers (TTM)", "value": cur["new_customers"] - cur["attrited"], "unit": "",
             "delta": _pct(cur["new_customers"] - cur["attrited"], prv["new_customers"] - prv["attrited"])},
        ],
        "trend": trend,
        "by_business_line": by_bl,
        "by_region": by_region,
        "income_mix": {"net_interest_income": cur["nii"], "fee_income": cur["fees"], "provisions": cur["provisions"]},
        "risk": risk,
        "wealth": wealth,
        "customers": customers,
    }


def analytics() -> dict:
    bl_trend = _q(DW, """SELECT month, business_line, SUM(revenue) revenue, SUM(net_income) net_income
                         FROM kpi_monthly GROUP BY month, business_line ORDER BY month""")
    series: dict[str, list] = defaultdict(list)
    for r in bl_trend:
        series[r["business_line"]].append(r["revenue"])
    months = sorted({r["month"] for r in bl_trend})

    digital = _q(DW, "SELECT month, channel, active_users, transactions, digital_sales FROM digital_engagement ORDER BY month")
    dig: dict[str, list] = defaultdict(list)
    for r in digital:
        dig[r["channel"]].append(r["active_users"])

    channel_mix = _q(CORE, """SELECT channel, COUNT(*) n, SUM(amount) amount FROM transactions
                              WHERE txn_date >= date((SELECT MAX(txn_date) FROM transactions), '-90 day')
                              GROUP BY channel ORDER BY amount DESC""")
    segments = _q(CORE, "SELECT segment, COUNT(*) n, AVG(annual_income) income FROM customers GROUP BY segment ORDER BY n DESC")
    loan_status = _q(CORE, "SELECT status, COUNT(*) n, SUM(outstanding) outstanding FROM loans GROUP BY status ORDER BY outstanding DESC")
    loan_types = _q(CORE, "SELECT loan_type, SUM(outstanding) outstanding, AVG(interest_rate) rate FROM loans GROUP BY loan_type ORDER BY outstanding DESC")
    aml = _q(RISK, "SELECT scenario, COUNT(*) n, AVG(risk_score) score FROM aml_alerts GROUP BY scenario ORDER BY n DESC")
    ratings = _q(RISK, """SELECT internal_rating, COUNT(*) n, SUM(ead) ead, SUM(expected_loss) el FROM credit_risk
                          GROUP BY internal_rating ORDER BY CASE internal_rating WHEN 'AA' THEN 1 WHEN 'A' THEN 2
                          WHEN 'BBB' THEN 3 WHEN 'BB' THEN 4 WHEN 'B' THEN 5 ELSE 6 END""")
    findings = _q(RISK, "SELECT severity, status, COUNT(*) n FROM compliance_findings GROUP BY severity, status")
    strategies = _q(WEALTH, """SELECT strategy, COUNT(*) n, AVG(ytd_return_pct) ytd, AVG(benchmark_ytd_pct) bench
                               FROM portfolios GROUP BY strategy ORDER BY n DESC""")

    first: dict[str, float] = {}
    lastp: dict[str, float] = {}
    with (D / "market_data" / "daily_prices.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            first.setdefault(row["symbol"], float(row["close"]))
            lastp[row["symbol"]] = float(row["close"])
    names = {s["symbol"]: s for s in json.loads((D / "market_data" / "securities.json").read_text(encoding="utf-8"))}
    market = sorted(({"symbol": s, "name": names.get(s, {}).get("name", s), "asset_class": names.get(s, {}).get("asset_class"),
                      "return_1y": round((lastp[s] / first[s] - 1) * 100, 1)} for s in lastp), key=lambda x: -x["return_1y"])

    crm = json.loads((D / "crm" / "crm_interactions.json").read_text(encoding="utf-8"))["records"]
    sentiment: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in crm:
        sentiment[r.get("channel", "Other")][r.get("sentiment", "Neutral")] += 1

    return {
        "months": months,
        "revenue_by_bl": series,
        "digital_months": sorted({r["month"] for r in digital}),
        "digital_users": dig,
        "channel_mix": channel_mix,
        "segments": segments,
        "loan_status": loan_status,
        "loan_types": loan_types,
        "aml_scenarios": aml,
        "ratings": ratings,
        "findings": findings,
        "strategies": strategies,
        "market": market,
        "crm_sentiment": {k: dict(v) for k, v in sentiment.items()},
    }


def datasources() -> list[dict]:
    cat = json.loads((D / "catalog.json").read_text(encoding="utf-8"))
    out = []
    for s in cat["sources"]:
        p = D / s["path"]
        s = dict(s)
        s["exists"] = p.exists()
        size = 0
        if p.is_file():
            size = p.stat().st_size
        elif p.is_dir():
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        s["size_kb"] = round(size / 1024, 1)
        counts = {}
        dbs = [p] if p.suffix == ".db" else (list(p.glob("*.db")) if p.is_dir() else [])
        for db in dbs:
            if not db.exists():
                continue
            for t in s["tables"]:
                try:
                    counts[t] = _one(db, f'SELECT COUNT(*) FROM "{t}"')  # table names come from our own catalog
                except sqlite3.Error:
                    pass
        for t in s["tables"]:
            if t.endswith(".json") and (p / t).exists():
                data = json.loads((p / t).read_text(encoding="utf-8"))
                counts[t] = len(data["records"]) if isinstance(data, dict) and "records" in data else len(data)
            elif t.endswith(".csv") and (p / t).exists():
                with (p / t).open(encoding="utf-8") as f:
                    counts[t] = sum(1 for _ in f) - 1
            elif t.endswith("*.md"):
                counts[t] = len(list((p / t.split("/")[0]).glob("*.md")))
        s["row_counts"] = counts
        out.append(s)
    return out
