"""MCP server: Wealth platform + market-data feed integration."""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings  # noqa: E402
from app.mcp_servers.common import db_path, load_json, make_server, pick, query  # noqa: E402

WEALTH = db_path("wealth", "wealth.db")
mcp = make_server(
    "wealth_markets",
    settings.mcp_wealth_markets_port,
    "Wealth management platform and end-of-day market data feed. Use for AUM, portfolio "
    "performance vs benchmark, holdings concentration and security price performance.",
)


@lru_cache(maxsize=1)
def _prices() -> dict[str, list[tuple[str, float]]]:
    series: dict[str, list[tuple[str, float]]] = defaultdict(list)
    with db_path("market_data", "daily_prices.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            series[row["symbol"]].append((row["date"], float(row["close"])))
    return series


def _last_price(symbol: str) -> float:
    return _prices()[symbol][-1][1]


def _market_values() -> dict[str, float]:
    mv: dict[str, float] = defaultdict(float)
    for h in query(WEALTH, "SELECT portfolio_id, symbol, quantity FROM holdings", limit=100000):
        mv[h["portfolio_id"]] += h["quantity"] * _last_price(h["symbol"])
    return mv


@mcp.tool()
def get_aum_summary(group_by: str = "strategy") -> list[dict]:
    """Assets under management (marked to latest market prices). group_by: strategy | risk_profile | advisor."""
    col = pick(group_by, {"strategy": "strategy", "risk_profile": "risk_profile", "advisor": "advisor"}, "strategy")
    mv = _market_values()
    agg: dict[str, dict] = {}
    for p in query(WEALTH, f"SELECT portfolio_id, {col} AS grp, ytd_return_pct, benchmark_ytd_pct, management_fee_bps FROM portfolios", limit=100000):
        a = agg.setdefault(p["grp"], {"group": p["grp"], "portfolios": 0, "aum": 0.0, "_ret": 0.0, "_bm": 0.0, "fee_revenue_est": 0.0})
        v = mv.get(p["portfolio_id"], 0.0)
        a["portfolios"] += 1
        a["aum"] += v
        a["_ret"] += p["ytd_return_pct"] * v
        a["_bm"] += p["benchmark_ytd_pct"] * v
        a["fee_revenue_est"] += v * p["management_fee_bps"] / 10000
    out = []
    for a in agg.values():
        aum = a["aum"] or 1
        out.append({"group": a["group"], "portfolios": a["portfolios"], "aum": round(a["aum"], 2),
                    "aum_weighted_ytd_return_pct": round(a["_ret"] / aum, 2),
                    "aum_weighted_benchmark_pct": round(a["_bm"] / aum, 2),
                    "annual_fee_revenue_est": round(a["fee_revenue_est"], 2)})
    return sorted(out, key=lambda r: r["aum"], reverse=True)[:25]


@mcp.tool()
def get_portfolio_details(portfolio_id: str = "", customer_id: str = "") -> list[dict]:
    """Holdings, weights and unrealised P&L for a portfolio_id, or all portfolios of a customer_id."""
    ports = query(WEALTH, "SELECT * FROM portfolios WHERE portfolio_id=? OR customer_id=?", (portfolio_id, customer_id), limit=5)
    out = []
    for p in ports:
        hs = query(WEALTH, "SELECT symbol, quantity, avg_cost FROM holdings WHERE portfolio_id=?", (p["portfolio_id"],))
        total = sum(h["quantity"] * _last_price(h["symbol"]) for h in hs) or 1
        p["market_value"] = round(total, 2)
        p["holdings"] = [{"symbol": h["symbol"], "market_value": round(h["quantity"] * _last_price(h["symbol"]), 2),
                          "weight_pct": round(100 * h["quantity"] * _last_price(h["symbol"]) / total, 2),
                          "unrealised_pnl_pct": round(100 * (_last_price(h["symbol"]) / h["avg_cost"] - 1), 2)} for h in hs]
        out.append(p)
    return out or [{"error": "no portfolio found"}]


@mcp.tool()
def get_underperforming_portfolios(threshold_pct: float = 5.0, limit: int = 15) -> dict:
    """Portfolios lagging their benchmark YTD by more than threshold_pct percentage points
    (suitability policy trigger)."""
    rows = query(WEALTH, """SELECT portfolio_id, customer_id, strategy, risk_profile, advisor, ytd_return_pct,
                               benchmark_ytd_pct, ROUND(ytd_return_pct-benchmark_ytd_pct,2) AS gap_pct
                            FROM portfolios WHERE benchmark_ytd_pct - ytd_return_pct > ? ORDER BY gap_pct""",
                 (float(threshold_pct),), limit=100000)
    mv = _market_values()
    for r in rows:
        r["market_value"] = round(mv.get(r["portfolio_id"], 0.0), 2)
    return {"count": len(rows), "aum_at_risk": round(sum(r["market_value"] for r in rows), 2),
            "portfolios": rows[: max(1, min(limit, 50))]}


@mcp.tool()
def get_market_performance(symbols: list[str] | None = None, period_days: int = 30) -> list[dict]:
    """Price return and volatility over the last period_days trading days for symbols
    (default: all securities in the security master)."""
    master = {s["symbol"]: s for s in load_json("market_data", "securities.json")}
    syms = [s for s in (symbols or list(master)) if s in master]
    n = max(2, min(int(period_days), 250))
    out = []
    for s in syms:
        series = _prices()[s][-n:]
        closes = [c for _, c in series]
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        mean = sum(rets) / len(rets)
        vol = (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5 * (252 ** 0.5)
        out.append({"symbol": s, "name": master[s]["name"], "asset_class": master[s]["asset_class"],
                    "last_close": closes[-1], "return_pct": round(100 * (closes[-1] / closes[0] - 1), 2),
                    "annualised_vol_pct": round(100 * vol, 2)})
    return sorted(out, key=lambda r: r["return_pct"], reverse=True)


@mcp.tool()
def get_concentration_risk(threshold_pct: float = 35.0, limit: int = 15) -> dict:
    """Portfolios where a single security exceeds threshold_pct of portfolio value."""
    mv = _market_values()
    flagged = []
    for h in query(WEALTH, "SELECT h.portfolio_id, h.symbol, h.quantity, p.customer_id, p.advisor FROM holdings h JOIN portfolios p USING(portfolio_id)", limit=100000):
        tot = mv.get(h["portfolio_id"], 0.0)
        if tot:
            w = 100 * h["quantity"] * _last_price(h["symbol"]) / tot
            if w > threshold_pct:
                flagged.append({"portfolio_id": h["portfolio_id"], "customer_id": h["customer_id"], "advisor": h["advisor"],
                                "symbol": h["symbol"], "weight_pct": round(w, 2), "portfolio_value": round(tot, 2)})
    flagged.sort(key=lambda r: r["weight_pct"], reverse=True)
    return {"count": len(flagged), "positions": flagged[: max(1, min(limit, 50))]}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
