"""MCP server: Enterprise data warehouse (historical KPIs)."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings  # noqa: E402
from app.mcp_servers.common import db_path, make_server, pick, query  # noqa: E402

DW = db_path("warehouse", "enterprise_dw.db")
mcp = make_server(
    "analytics",
    settings.mcp_analytics_port,
    "Enterprise data warehouse with 36 months of monthly KPIs by business line and region, "
    "plus digital channel engagement. Use for trends, growth, profitability and efficiency.",
)

METRICS = {m: m for m in ["revenue", "net_interest_income", "fee_income", "operating_expense", "credit_provisions",
                          "net_income", "deposits", "loans_outstanding", "aum", "new_customers", "attrited_customers", "nps"]}


def _months_filter(months: int) -> str:
    months = max(1, min(int(months), 36))
    return f"month > (SELECT strftime('%Y-%m', date(MAX(month)||'-01', '-{months} months')) FROM kpi_monthly)"


@mcp.tool()
def get_kpi_trend(metric: str = "revenue", business_line: str = "", region: str = "", months: int = 12) -> list[dict]:
    """Monthly trend of one KPI. metric: revenue | net_interest_income | fee_income | operating_expense |
    credit_provisions | net_income | deposits | loans_outstanding | aum | new_customers | attrited_customers | nps.
    business_line: Retail Banking | Wealth Management | Commercial Banking | Corporate & Investment Banking.
    region: North America | EMEA | APAC | LATAM."""
    col = pick(metric, METRICS, "revenue")
    agg = "AVG" if col == "nps" else "SUM"
    return query(DW, f"""SELECT month, ROUND({agg}({col}),2) AS value FROM kpi_monthly
                         WHERE {_months_filter(months)} AND (?='' OR business_line=?) AND (?='' OR region=?)
                         GROUP BY month ORDER BY month""", (business_line, business_line, region, region), limit=36)


@mcp.tool()
def get_business_line_performance(months: int = 12) -> list[dict]:
    """P&L by business line over the trailing N months vs the prior N months (growth %),
    with cost-to-income ratio and net margin."""
    months = max(1, min(int(months), 18))
    sql = f"""WITH mx AS (SELECT MAX(month) m FROM kpi_monthly),
              cur AS (SELECT business_line, SUM(revenue) rev, SUM(net_income) ni, SUM(operating_expense) opex,
                             SUM(credit_provisions) prov FROM kpi_monthly, mx
                      WHERE month > strftime('%Y-%m', date(mx.m||'-01','-{months} months')) GROUP BY business_line),
              prv AS (SELECT business_line, SUM(revenue) rev, SUM(net_income) ni FROM kpi_monthly, mx
                      WHERE month > strftime('%Y-%m', date(mx.m||'-01','-{2 * months} months'))
                        AND month <= strftime('%Y-%m', date(mx.m||'-01','-{months} months')) GROUP BY business_line)
              SELECT cur.business_line, ROUND(cur.rev,2) AS revenue, ROUND(cur.ni,2) AS net_income,
                     ROUND(100*(cur.rev/prv.rev-1),2) AS revenue_growth_pct, ROUND(100*(cur.ni/prv.ni-1),2) AS net_income_growth_pct,
                     ROUND(100*cur.opex/cur.rev,2) AS cost_to_income_pct, ROUND(100*cur.ni/cur.rev,2) AS net_margin_pct,
                     ROUND(cur.prov,2) AS credit_provisions
              FROM cur JOIN prv USING(business_line) ORDER BY revenue DESC"""
    return query(DW, sql)


@mcp.tool()
def get_regional_performance(months: int = 12) -> list[dict]:
    """Revenue, net income, deposits, customer growth and NPS by region over trailing N months."""
    return query(DW, f"""SELECT region, ROUND(SUM(revenue),2) AS revenue, ROUND(SUM(net_income),2) AS net_income,
                                ROUND(100*SUM(operating_expense)/SUM(revenue),2) AS cost_to_income_pct,
                                SUM(new_customers) AS new_customers, SUM(attrited_customers) AS attrited_customers,
                                ROUND(AVG(nps),1) AS avg_nps
                         FROM kpi_monthly WHERE {_months_filter(months)} GROUP BY region ORDER BY revenue DESC""")


@mcp.tool()
def get_digital_engagement(months: int = 12) -> list[dict]:
    """Digital channel adoption: active users, transactions and digital sales by channel and month."""
    months = max(1, min(int(months), 36))
    return query(DW, f"""SELECT month, channel, active_users, transactions, digital_sales FROM digital_engagement
                         WHERE month > (SELECT strftime('%Y-%m', date(MAX(month)||'-01','-{months} months')) FROM digital_engagement)
                         ORDER BY month, channel""", limit=200)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
