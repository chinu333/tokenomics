"""Mock data generator for FinSight AI.

Creates several *integration points* under ./data, each mimicking a different
enterprise source system (SQLite databases, CSV feeds, JSON exports, documents):

    data/
      core_banking/core_banking.db      customers, accounts, transactions, loans
      crm/crm_interactions.json         CRM export (Salesforce-style)
      crm/nps_survey.json               customer survey feed
      wealth/wealth.db                  portfolios, holdings
      market_data/securities.json       security master
      market_data/daily_prices.csv      end-of-day price feed
      risk_compliance/risk_compliance.db aml_alerts, credit_risk, compliance_findings
      risk_compliance/policies/*.md     policy documents (search corpus)
      warehouse/enterprise_dw.db        kpi_monthly, digital_engagement (history)
      catalog.json                      data-source catalog

Deterministic (seeded) so the demo is reproducible.  Run:  python -m app.data_gen
"""

from __future__ import annotations

import csv
import json
import math
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

RNG = random.Random(20260930)
TODAY = date(2026, 9, 30)

FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David",
         "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas",
         "Sarah", "Priya", "Arjun", "Mei", "Wei", "Hiroshi", "Yuki", "Carlos", "Sofia", "Lucas",
         "Emma", "Olivia", "Noah", "Liam", "Ava", "Fatima", "Omar", "Hans", "Ingrid", "Chinedu",
         "Amara", "Diego", "Valentina", "Raj", "Ananya", "Kenji", "Aiko", "Pierre", "Chloe"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Martinez",
        "Lopez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Lee", "Patel", "Shah",
        "Chen", "Wang", "Tanaka", "Sato", "Silva", "Rossi", "Muller", "Schmidt", "Dubois",
        "Okafor", "Kim", "Nguyen", "Kumar", "Chakraborty", "Haddad", "Novak", "Larsen", "Costa"]
CORP = ["Apex", "Northwind", "Contoso", "Fabrikam", "Tailspin", "Litware", "Adventure Works",
        "Proseware", "Wingtip", "Coho", "Woodgrove", "Alpine", "Blue Yonder", "Lucerne",
        "Trey Research", "Margie's", "Humongous", "Fourth Coffee", "Wide World", "Datum"]
CORP_SUFFIX = ["Holdings", "Logistics", "Manufacturing", "Capital", "Health", "Foods",
               "Technologies", "Energy", "Retail Group", "Partners"]

REGIONS = {
    "North America": ["New York", "Chicago", "Toronto", "San Francisco", "Dallas", "Boston"],
    "EMEA": ["London", "Frankfurt", "Paris", "Dubai", "Zurich", "Amsterdam"],
    "APAC": ["Singapore", "Hong Kong", "Tokyo", "Sydney", "Mumbai", "Bangalore"],
    "LATAM": ["Sao Paulo", "Mexico City", "Bogota", "Santiago", "Buenos Aires", "Lima"],
}
REGION_W = [0.42, 0.27, 0.21, 0.10]
SEGMENTS = ["Retail", "Affluent", "High Net Worth", "SME", "Corporate"]
SEGMENT_W = [0.55, 0.20, 0.07, 0.13, 0.05]
BUSINESS_LINES = ["Retail Banking", "Wealth Management", "Commercial Banking", "Corporate & Investment Banking"]
MCC = ["Groceries", "Travel", "Dining", "Utilities", "Healthcare", "Electronics", "Fuel",
       "Entertainment", "Luxury Goods", "Crypto Exchange", "Money Transfer", "Payroll",
       "Wholesale", "Professional Services", "Real Estate"]
CHANNELS = ["Mobile", "Online", "Branch", "ATM", "Card POS", "Wire", "ACH"]
HIGH_RISK_COUNTRIES = ["Country-X", "Country-Y", "Country-Z"]


def _d(days_back_max: int, days_back_min: int = 0) -> date:
    return TODAY - timedelta(days=RNG.randint(days_back_min, days_back_max))


def _month_starts(n: int) -> list[date]:
    out, y, m = [], TODAY.year, TODAY.month
    for _ in range(n):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    return sqlite3.connect(path)


# --------------------------------------------------------------------------- #
# Core banking                                                                 #
# --------------------------------------------------------------------------- #

def gen_core_banking(root: Path) -> list[dict]:
    db = _connect(root / "core_banking" / "core_banking.db")
    db.executescript(
        """
        CREATE TABLE customers (
          customer_id TEXT PRIMARY KEY, full_name TEXT, segment TEXT, region TEXT, city TEXT,
          onboarding_date TEXT, kyc_status TEXT, relationship_manager TEXT, annual_income REAL);
        CREATE TABLE accounts (
          account_id TEXT PRIMARY KEY, customer_id TEXT, product TEXT, balance REAL,
          currency TEXT, status TEXT, opened_date TEXT);
        CREATE TABLE transactions (
          txn_id TEXT PRIMARY KEY, account_id TEXT, customer_id TEXT, txn_date TEXT, amount REAL,
          direction TEXT, channel TEXT, merchant_category TEXT, counterparty_country TEXT);
        CREATE TABLE loans (
          loan_id TEXT PRIMARY KEY, customer_id TEXT, loan_type TEXT, principal REAL,
          outstanding REAL, interest_rate REAL, origination_date TEXT, maturity_date TEXT,
          status TEXT, days_past_due INTEGER, ltv REAL, credit_score INTEGER);
        CREATE INDEX ix_acc_cust ON accounts(customer_id);
        CREATE INDEX ix_txn_cust ON transactions(customer_id);
        CREATE INDEX ix_txn_date ON transactions(txn_date);
        CREATE INDEX ix_loan_cust ON loans(customer_id);
        """
    )
    rms = [f"{RNG.choice(FIRST)} {RNG.choice(LAST)}" for _ in range(24)]
    customers: list[dict] = []
    for i in range(1, 1501):
        seg = RNG.choices(SEGMENTS, SEGMENT_W)[0]
        region = RNG.choices(list(REGIONS), REGION_W)[0]
        if seg in ("SME", "Corporate"):
            name = f"{RNG.choice(CORP)} {RNG.choice(CORP_SUFFIX)}"
        else:
            name = f"{RNG.choice(FIRST)} {RNG.choice(LAST)}"
        income = {
            "Retail": RNG.uniform(35e3, 110e3), "Affluent": RNG.uniform(150e3, 400e3),
            "High Net Worth": RNG.uniform(600e3, 5e6), "SME": RNG.uniform(0.5e6, 12e6),
            "Corporate": RNG.uniform(20e6, 900e6),
        }[seg]
        c = {
            "customer_id": f"CUST-{i:05d}", "full_name": name, "segment": seg, "region": region,
            "city": RNG.choice(REGIONS[region]),
            "onboarding_date": _d(365 * 12, 10).isoformat(),
            "kyc_status": RNG.choices(["Verified", "Pending Review", "Expired"], [0.9, 0.06, 0.04])[0],
            "relationship_manager": RNG.choice(rms) if seg != "Retail" else None,
            "annual_income": round(income, 2),
        }
        customers.append(c)
    db.executemany(
        "INSERT INTO customers VALUES (:customer_id,:full_name,:segment,:region,:city,"
        ":onboarding_date,:kyc_status,:relationship_manager,:annual_income)", customers)

    products_by_seg = {
        "Retail": ["Checking", "Savings", "Credit Card"],
        "Affluent": ["Checking", "Savings", "Credit Card", "Brokerage"],
        "High Net Worth": ["Checking", "Savings", "Brokerage", "Private Banking"],
        "SME": ["Business Checking", "Business Savings", "Merchant Services"],
        "Corporate": ["Business Checking", "Treasury", "FX Account"],
    }
    bal_scale = {"Retail": 8e3, "Affluent": 60e3, "High Net Worth": 900e3, "SME": 250e3, "Corporate": 6e6}
    accounts, txns, loans = [], [], []
    acc_n = txn_n = loan_n = 0
    for c in customers:
        seg = c["segment"]
        prods = RNG.sample(products_by_seg[seg], k=RNG.randint(1, len(products_by_seg[seg])))
        for p in prods:
            acc_n += 1
            bal = abs(RNG.lognormvariate(math.log(bal_scale[seg]), 0.8))
            if p == "Credit Card":
                bal = -RNG.uniform(0, 9000)
            acc = {
                "account_id": f"ACC-{acc_n:06d}", "customer_id": c["customer_id"], "product": p,
                "balance": round(bal, 2),
                "currency": {"North America": "USD", "EMEA": RNG.choice(["EUR", "GBP", "USD"]),
                             "APAC": RNG.choice(["SGD", "JPY", "USD"]), "LATAM": RNG.choice(["BRL", "MXN", "USD"])}[c["region"]],
                "status": RNG.choices(["Active", "Dormant", "Closed"], [0.9, 0.07, 0.03])[0],
                "opened_date": max(date.fromisoformat(c["onboarding_date"]), _d(365 * 10)).isoformat(),
            }
            accounts.append(acc)
            n_tx = {"Retail": 14, "Affluent": 18, "High Net Worth": 16, "SME": 30, "Corporate": 36}[seg]
            for _ in range(RNG.randint(n_tx // 2, n_tx)):
                txn_n += 1
                direction = RNG.choices(["debit", "credit"], [0.62, 0.38])[0]
                amt = abs(RNG.lognormvariate(math.log(bal_scale[seg] / 40 + 20), 1.1))
                suspicious = RNG.random() < 0.012
                if suspicious:
                    amt *= RNG.uniform(8, 25)
                txns.append({
                    "txn_id": f"TXN-{txn_n:07d}", "account_id": acc["account_id"],
                    "customer_id": c["customer_id"], "txn_date": _d(364).isoformat(),
                    "amount": round(amt, 2), "direction": direction,
                    "channel": RNG.choice(CHANNELS if seg in ("SME", "Corporate") else CHANNELS[:5]),
                    "merchant_category": RNG.choice(["Crypto Exchange", "Money Transfer", "Luxury Goods"]) if suspicious else RNG.choice(MCC),
                    "counterparty_country": RNG.choice(HIGH_RISK_COUNTRIES) if suspicious and RNG.random() < 0.6 else c["region"],
                })
        # loans
        if RNG.random() < {"Retail": 0.35, "Affluent": 0.45, "High Net Worth": 0.3, "SME": 0.6, "Corporate": 0.7}[seg]:
            loan_n += 1
            ltype = {"Retail": RNG.choice(["Mortgage", "Auto Loan", "Personal Loan"]),
                     "Affluent": RNG.choice(["Mortgage", "HELOC", "Personal Loan"]),
                     "High Net Worth": RNG.choice(["Jumbo Mortgage", "Securities-Backed Line"]),
                     "SME": RNG.choice(["Term Loan", "Revolving Credit", "Equipment Finance"]),
                     "Corporate": RNG.choice(["Syndicated Loan", "Revolving Credit", "Trade Finance"])}[seg]
            principal = abs(RNG.lognormvariate(math.log(bal_scale[seg] * 6), 0.6))
            orig = _d(365 * 8, 30)
            term_y = RNG.choice([3, 5, 7, 10, 15, 30]) if "Mortgage" in ltype else RNG.choice([1, 3, 5, 7])
            score = int(min(850, max(480, RNG.gauss(725, 60))))
            dpd = 0
            status = "Current"
            r = RNG.random()
            stress = 0.10 if score < 640 else 0.03
            if r < stress * 0.35:
                status, dpd = "Default", RNG.randint(91, 240)
            elif r < stress * 0.6:
                status, dpd = "90+ DPD", RNG.randint(90, 120)
            elif r < stress:
                status, dpd = "30-89 DPD", RNG.randint(30, 89)
            loans.append({
                "loan_id": f"LN-{loan_n:05d}", "customer_id": c["customer_id"], "loan_type": ltype,
                "principal": round(principal, 2),
                "outstanding": round(principal * RNG.uniform(0.25, 0.98), 2),
                "interest_rate": round(RNG.uniform(3.1, 11.5), 3),
                "origination_date": orig.isoformat(),
                "maturity_date": date(orig.year + term_y, orig.month, min(orig.day, 28)).isoformat(),
                "status": status, "days_past_due": dpd,
                "ltv": round(RNG.uniform(0.35, 0.95), 3) if "Mortgage" in ltype or ltype == "HELOC" else None,
                "credit_score": score,
            })
    db.executemany("INSERT INTO accounts VALUES (:account_id,:customer_id,:product,:balance,:currency,:status,:opened_date)", accounts)
    db.executemany("INSERT INTO transactions VALUES (:txn_id,:account_id,:customer_id,:txn_date,:amount,:direction,:channel,:merchant_category,:counterparty_country)", txns)
    db.executemany("INSERT INTO loans VALUES (:loan_id,:customer_id,:loan_type,:principal,:outstanding,:interest_rate,:origination_date,:maturity_date,:status,:days_past_due,:ltv,:credit_score)", loans)
    db.commit()
    db.close()
    print(f"  core_banking: {len(customers)} customers, {len(accounts)} accounts, {len(txns)} txns, {len(loans)} loans")
    return customers, txns, loans


# --------------------------------------------------------------------------- #
# CRM                                                                          #
# --------------------------------------------------------------------------- #

def gen_crm(root: Path, customers: list[dict]) -> None:
    out = root / "crm"
    out.mkdir(parents=True, exist_ok=True)
    reasons = ["Fee dispute", "Mortgage inquiry", "Investment review", "Card fraud report",
               "Mobile app issue", "Account opening", "Rate negotiation", "Wealth planning",
               "Complaint - service delay", "Treasury onboarding", "Credit line increase"]
    interactions = []
    for n in range(1, 4201):
        c = RNG.choice(customers)
        interactions.append({
            "interaction_id": f"INT-{n:06d}", "customer_id": c["customer_id"],
            "date": _d(364).isoformat(),
            "channel": RNG.choice(["Phone", "Branch", "Email", "Chat", "RM Meeting"]),
            "reason": RNG.choice(reasons),
            "sentiment": RNG.choices(["Positive", "Neutral", "Negative"], [0.46, 0.36, 0.18])[0],
            "resolved": RNG.random() < 0.87,
            "churn_risk": round(min(1.0, max(0.0, RNG.gauss(0.22, 0.15))), 3),
            "next_best_action": RNG.choice(["Offer premium card", "Schedule portfolio review",
                                            "Refinance offer", "Cross-sell savings", "Retention call",
                                            "Digital onboarding nudge", "No action"]),
        })
    (out / "crm_interactions.json").write_text(json.dumps({
        "source": "CRM (Salesforce-style nightly export)", "exported_at": TODAY.isoformat(),
        "records": interactions}, indent=1), encoding="utf-8")

    surveys = []
    for n in range(1, 2601):
        c = RNG.choice(customers)
        base = {"Retail": 7.2, "Affluent": 7.9, "High Net Worth": 8.4, "SME": 7.0, "Corporate": 7.6}[c["segment"]]
        surveys.append({
            "survey_id": f"NPS-{n:05d}", "customer_id": c["customer_id"], "segment": c["segment"],
            "region": c["region"], "date": _d(364).isoformat(),
            "score": int(min(10, max(0, round(RNG.gauss(base, 1.8))))),
        })
    (out / "nps_survey.json").write_text(json.dumps({
        "source": "Customer survey platform feed", "records": surveys}, indent=1), encoding="utf-8")
    print(f"  crm: {len(interactions)} interactions, {len(surveys)} NPS responses")


# --------------------------------------------------------------------------- #
# Market data + wealth                                                         #
# --------------------------------------------------------------------------- #

SECURITIES = [
    ("MSFT", "Microsoft Corp", "Equity", "Technology", 430), ("AAPL", "Apple Inc", "Equity", "Technology", 225),
    ("NVDA", "NVIDIA Corp", "Equity", "Technology", 125), ("AMZN", "Amazon.com Inc", "Equity", "Consumer", 190),
    ("JPM", "JPMorgan Chase", "Equity", "Financials", 215), ("GS", "Goldman Sachs", "Equity", "Financials", 490),
    ("XOM", "Exxon Mobil", "Equity", "Energy", 115), ("JNJ", "Johnson & Johnson", "Equity", "Healthcare", 160),
    ("UNH", "UnitedHealth Group", "Equity", "Healthcare", 560), ("NESN", "Nestle SA", "Equity", "Consumer Staples", 95),
    ("ASML", "ASML Holding", "Equity", "Technology", 820), ("TSM", "Taiwan Semiconductor", "Equity", "Technology", 175),
    ("UST10", "US Treasury 10Y", "Fixed Income", "Government", 98), ("UST2", "US Treasury 2Y", "Fixed Income", "Government", 99.5),
    ("IGCORP", "IG Corporate Bond ETF", "Fixed Income", "Credit", 108), ("HYBOND", "High Yield Bond ETF", "Fixed Income", "Credit", 78),
    ("GOLD", "Gold ETF", "Commodity", "Precious Metals", 235), ("REIT", "Global REIT Index", "Real Estate", "REIT", 92),
    ("EMEQ", "Emerging Markets Equity ETF", "Equity", "Emerging Markets", 44), ("PE-FUND", "Private Equity Fund IV", "Alternatives", "Private Equity", 1000),
]


def gen_market_and_wealth(root: Path, customers: list[dict]) -> None:
    md = root / "market_data"
    md.mkdir(parents=True, exist_ok=True)
    (md / "securities.json").write_text(json.dumps([
        {"symbol": s, "name": n, "asset_class": ac, "sector": sec} for s, n, ac, sec, _ in SECURITIES
    ], indent=1), encoding="utf-8")

    days = []
    d = TODAY - timedelta(days=365)
    while d <= TODAY:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    last_close: dict[str, float] = {}
    vol = {"Equity": 0.018, "Fixed Income": 0.003, "Commodity": 0.01, "Real Estate": 0.012, "Alternatives": 0.004}
    drift = {"Technology": 0.0009, "Financials": 0.0005, "Energy": 0.0001, "Healthcare": 0.0003}
    with (md / "daily_prices.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "close", "volume"])
        for sym, _, ac, sec, start in SECURITIES:
            p = start * RNG.uniform(0.75, 0.95)
            for day in days:
                p *= math.exp(RNG.gauss(drift.get(sec, 0.0002), vol[ac]))
                w.writerow([day.isoformat(), sym, round(p, 2), int(RNG.uniform(0.4, 2.5) * 1e6) if ac == "Equity" else int(RNG.uniform(1e4, 3e5))])
            last_close[sym] = p

    db = _connect(root / "wealth" / "wealth.db")
    db.executescript(
        """
        CREATE TABLE portfolios (
          portfolio_id TEXT PRIMARY KEY, customer_id TEXT, strategy TEXT, risk_profile TEXT,
          inception_date TEXT, advisor TEXT, benchmark TEXT, ytd_return_pct REAL, benchmark_ytd_pct REAL,
          management_fee_bps INTEGER);
        CREATE TABLE holdings (
          portfolio_id TEXT, symbol TEXT, quantity REAL, avg_cost REAL,
          PRIMARY KEY (portfolio_id, symbol));
        """
    )
    strategies = {
        "Conservative Income": (["UST10", "UST2", "IGCORP", "JNJ", "NESN", "GOLD"], "Low"),
        "Balanced Growth": (["MSFT", "AAPL", "JPM", "IGCORP", "UST10", "REIT", "JNJ"], "Medium"),
        "Global Equity": (["MSFT", "NVDA", "AMZN", "ASML", "TSM", "EMEQ", "UNH"], "High"),
        "Tech Innovation": (["NVDA", "MSFT", "AAPL", "ASML", "TSM", "AMZN"], "Very High"),
        "Alternatives Plus": (["PE-FUND", "GOLD", "REIT", "HYBOND", "GS"], "High"),
    }
    advisors = [f"{RNG.choice(FIRST)} {RNG.choice(LAST)}, CFA" for _ in range(12)]
    wealthy = [c for c in customers if c["segment"] in ("Affluent", "High Net Worth")]
    pn = 0
    ports, holds = [], []
    for c in wealthy:
        for _ in range(RNG.choice([1, 1, 2])):
            pn += 1
            strat = RNG.choice(list(strategies))
            syms, rp = strategies[strat]
            target = abs(RNG.lognormvariate(math.log(3.5e6 if c["segment"] == "High Net Worth" else 4.5e5), 0.7))
            bench = round(RNG.uniform(4, 16), 2)
            pid = f"PF-{pn:05d}"
            ports.append((pid, c["customer_id"], strat, rp, _d(365 * 9, 60).isoformat(), RNG.choice(advisors),
                          "MSCI ACWI" if "Equity" in strat or "Tech" in strat else "60/40 Blend",
                          round(bench + RNG.gauss(0.4, 3.2), 2), bench, RNG.choice([45, 60, 75, 90, 110])))
            weights = [RNG.random() for _ in syms]
            tw = sum(weights)
            for s, wt in zip(syms, weights):
                px = last_close[s]
                qty = round(target * wt / tw / px, 3)
                holds.append((pid, s, qty, round(px * RNG.uniform(0.7, 1.15), 2)))
    db.executemany("INSERT INTO portfolios VALUES (?,?,?,?,?,?,?,?,?,?)", ports)
    db.executemany("INSERT INTO holdings VALUES (?,?,?,?)", holds)
    db.commit()
    db.close()
    print(f"  market_data: {len(SECURITIES)} securities x {len(days)} days; wealth: {len(ports)} portfolios, {len(holds)} holdings")


# --------------------------------------------------------------------------- #
# Risk & compliance                                                            #
# --------------------------------------------------------------------------- #

POLICY_DOCS = {
    "aml_transaction_monitoring_policy.md": """# AML Transaction Monitoring Policy (FIN-AML-004)
Owner: Chief Compliance Officer. Last review: 2026-06-30.

## Thresholds
- Cash or wire transactions above USD 10,000 must be reported (CTR) within 15 days.
- Structuring: three or more transactions between USD 8,000 and 9,999 within 7 days triggers an alert.
- Any transaction with a counterparty in a FATF high-risk jurisdiction (Country-X, Country-Y, Country-Z) triggers enhanced due diligence (EDD).
- Crypto exchange transfers above USD 25,000 per month require source-of-funds verification.

## Alert handling SLA
- High risk (score >= 80): triage within 24 hours, SAR decision within 30 days.
- Medium risk (score 50-79): triage within 5 business days.
- Low risk (< 50): batch review weekly.
Alerts open beyond SLA must be escalated to the MLRO and reported in the monthly risk committee pack.
""",
    "credit_risk_appetite_statement.md": """# Credit Risk Appetite Statement 2026 (FIN-CR-001)
Approved by the Board Risk Committee.

- Group non-performing loan (NPL) ratio (90+ DPD and Default) must stay below 2.5% of outstanding balances; amber trigger at 2.0%.
- Single-name concentration: no borrower above 10% of Tier 1 capital.
- Mortgage LTV above 90% limited to 5% of new origination.
- Watchlist names with probability of default (PD) above 8% require quarterly review by the Credit Committee.
- Commercial real-estate exposure capped at 18% of the loan book.
Expected loss = PD x LGD x EAD is reported monthly per segment.
""",
    "ai_usage_and_cost_governance_policy.md": """# Responsible AI Usage & Cost Governance Policy (FIN-AI-002)
- All generative AI agents must run under the TokenOps control plane with an enforced per-run budget.
- Default per-run LLM budget: USD 0.25; exceptions require CTO approval.
- Agents must downgrade to a lower-cost model when 80% of the run budget is consumed.
- Every run must be attributable to a user persona and department for chargeback.
- Customer PII must never be sent to models outside the approved Azure tenant; access uses Entra ID (RBAC), never shared keys.
- Runaway loops (more than 30 steps) must be halted automatically.
""",
    "kyc_refresh_procedure.md": """# KYC Refresh Procedure (FIN-KYC-003)
- High Net Worth and Corporate clients: KYC refresh every 12 months.
- Affluent and SME: every 24 months. Retail: every 36 months.
- Expired KYC blocks new product onboarding and outbound wires above USD 50,000.
- Pending Review status older than 30 days is escalated to the regional compliance head.
""",
    "wealth_suitability_guidelines.md": """# Wealth Suitability Guidelines (FIN-WM-007)
- Portfolio risk profile must match client risk tolerance recorded at onboarding.
- 'Very High' risk strategies (e.g. Tech Innovation) are limited to High Net Worth clients with signed acknowledgement.
- Single-security concentration above 35% of portfolio value requires advisor justification.
- Portfolios underperforming benchmark by more than 5 percentage points YTD trigger a mandatory client review.
""",
}


def gen_risk_compliance(root: Path, customers: list[dict], txns: list[dict], loans: list[dict]) -> None:
    db = _connect(root / "risk_compliance" / "risk_compliance.db")
    db.executescript(
        """
        CREATE TABLE aml_alerts (
          alert_id TEXT PRIMARY KEY, customer_id TEXT, txn_id TEXT, alert_date TEXT, scenario TEXT,
          risk_score INTEGER, amount REAL, status TEXT, assigned_to TEXT, days_open INTEGER);
        CREATE TABLE credit_risk (
          customer_id TEXT PRIMARY KEY, internal_rating TEXT, pd REAL, lgd REAL, ead REAL,
          expected_loss REAL, watchlist INTEGER, last_review TEXT);
        CREATE TABLE compliance_findings (
          finding_id TEXT PRIMARY KEY, area TEXT, regulation TEXT, severity TEXT, description TEXT,
          opened_date TEXT, due_date TEXT, status TEXT, owner TEXT);
        """
    )
    analysts = [f"{RNG.choice(FIRST)} {RNG.choice(LAST)}" for _ in range(10)]
    alerts = []
    an = 0
    for t in txns:
        flag = (t["counterparty_country"] in HIGH_RISK_COUNTRIES
                or t["merchant_category"] in ("Crypto Exchange",) and t["amount"] > 20000
                or (t["amount"] > 150000 and t["channel"] == "Wire"))
        if flag or RNG.random() < 0.0015:
            an += 1
            ad = date.fromisoformat(t["txn_date"]) + timedelta(days=RNG.randint(0, 3))
            ad = min(ad, TODAY)
            score = RNG.randint(70, 99) if t["counterparty_country"] in HIGH_RISK_COUNTRIES else RNG.randint(25, 85)
            status = RNG.choices(["Open", "Under Investigation", "Escalated - SAR Filed", "Closed - False Positive"],
                                 [0.3, 0.22, 0.08, 0.4])[0]
            alerts.append((f"AML-{an:05d}", t["customer_id"], t["txn_id"], ad.isoformat(),
                           "High-risk jurisdiction" if t["counterparty_country"] in HIGH_RISK_COUNTRIES else
                           RNG.choice(["Structuring", "Rapid movement of funds", "Crypto exposure", "Large wire", "Unusual velocity"]),
                           score, t["amount"], status, RNG.choice(analysts),
                           (TODAY - ad).days if not status.startswith("Closed") else 0))
    db.executemany("INSERT INTO aml_alerts VALUES (?,?,?,?,?,?,?,?,?,?)", alerts)

    loan_by_c = {}
    for ln in loans:
        loan_by_c.setdefault(ln["customer_id"], []).append(ln)
    rating_for = [(0.003, "AA"), (0.008, "A"), (0.02, "BBB"), (0.05, "BB"), (0.12, "B"), (1.0, "CCC")]
    cr = []
    for cid, lns in loan_by_c.items():
        ead = sum(x["outstanding"] for x in lns)
        worst = max(lns, key=lambda x: x["days_past_due"])
        pd = max(0.001, min(0.99, (850 - worst["credit_score"]) / 4000 + (0.5 if worst["status"] == "Default" else 0.15 if worst["days_past_due"] >= 30 else 0)))
        lgd = RNG.uniform(0.2, 0.55)
        rating = next(r for lim, r in rating_for if pd <= lim)
        cr.append((cid, rating, round(pd, 4), round(lgd, 3), round(ead, 2), round(pd * lgd * ead, 2),
                   1 if pd > 0.08 else 0, _d(200).isoformat()))
    db.executemany("INSERT INTO credit_risk VALUES (?,?,?,?,?,?,?,?)", cr)

    areas = [("AML", "BSA/AML", "SAR filing timeliness gaps in APAC"), ("KYC", "FinCEN CDD Rule", "Expired KYC on HNW clients"),
             ("Consumer Protection", "Reg Z / TILA", "APR disclosure errors in card statements"),
             ("Data Privacy", "GDPR", "Retention beyond policy for EMEA CRM data"),
             ("Model Risk", "SR 11-7", "Credit PD model validation overdue"),
             ("Operational Resilience", "DORA", "Third-party ICT register incomplete"),
             ("Market Conduct", "MiFID II", "Suitability assessment evidence missing"),
             ("AI Governance", "EU AI Act", "Generative AI usage inventory incomplete"),
             ("Capital", "Basel III", "RWA calculation data lineage gaps"),
             ("Sanctions", "OFAC", "Screening latency above SLA")]
    owners = ["Head of Compliance EMEA", "Head of Compliance NA", "CRO Office", "CDO Office", "CISO", "Head of Wealth Risk"]
    findings = []
    for n in range(1, 61):
        a, reg, desc = RNG.choice(areas)
        od = _d(420, 5)
        findings.append((f"FND-{n:04d}", a, reg, RNG.choices(["Critical", "High", "Medium", "Low"], [0.08, 0.25, 0.42, 0.25])[0],
                         desc, od.isoformat(), (od + timedelta(days=RNG.choice([30, 60, 90, 180]))).isoformat(),
                         RNG.choices(["Open", "In Remediation", "Closed", "Overdue"], [0.25, 0.3, 0.33, 0.12])[0],
                         RNG.choice(owners)))
    db.executemany("INSERT INTO compliance_findings VALUES (?,?,?,?,?,?,?,?,?)", findings)
    db.commit()
    db.close()

    pol = root / "risk_compliance" / "policies"
    pol.mkdir(parents=True, exist_ok=True)
    for name, text in POLICY_DOCS.items():
        (pol / name).write_text(text, encoding="utf-8")
    print(f"  risk_compliance: {len(alerts)} AML alerts, {len(cr)} credit-risk rows, {len(findings)} findings, {len(POLICY_DOCS)} policies")


# --------------------------------------------------------------------------- #
# Enterprise data warehouse (history)                                         #
# --------------------------------------------------------------------------- #

def gen_warehouse(root: Path) -> None:
    db = _connect(root / "warehouse" / "enterprise_dw.db")
    db.executescript(
        """
        CREATE TABLE kpi_monthly (
          month TEXT, business_line TEXT, region TEXT,
          revenue REAL, net_interest_income REAL, fee_income REAL, operating_expense REAL,
          credit_provisions REAL, net_income REAL, deposits REAL, loans_outstanding REAL,
          aum REAL, new_customers INTEGER, attrited_customers INTEGER, nps REAL,
          PRIMARY KEY (month, business_line, region));
        CREATE TABLE digital_engagement (
          month TEXT, channel TEXT, active_users INTEGER, transactions INTEGER,
          digital_sales INTEGER, avg_session_minutes REAL, PRIMARY KEY (month, channel));
        """
    )
    base_rev = {"Retail Banking": 410, "Wealth Management": 185, "Commercial Banking": 260, "Corporate & Investment Banking": 340}
    reg_share = dict(zip(REGIONS, REGION_W))
    months = _month_starts(36)
    rows = []
    for i, m in enumerate(months):
        season = 1 + 0.035 * math.sin(2 * math.pi * (m.month - 1) / 12)
        for bl, br in base_rev.items():
            growth = (1 + {"Retail Banking": 0.004, "Wealth Management": 0.011, "Commercial Banking": 0.006,
                           "Corporate & Investment Banking": 0.007}[bl]) ** i
            for rg, sh in reg_share.items():
                rev = br * sh * growth * season * RNG.uniform(0.95, 1.05) * 1e6
                nii_share = {"Retail Banking": 0.68, "Wealth Management": 0.25, "Commercial Banking": 0.62,
                             "Corporate & Investment Banking": 0.45}[bl]
                opex = rev * RNG.uniform(0.52, 0.61) * (1 - 0.002 * i)
                prov = rev * RNG.uniform(0.04, 0.09) * (1.35 if 14 <= i <= 18 else 1.0)
                rows.append((m.isoformat()[:7], bl, rg, round(rev, 2), round(rev * nii_share, 2),
                             round(rev * (1 - nii_share), 2), round(opex, 2), round(prov, 2),
                             round((rev - opex - prov) * 0.76, 2),
                             round(rev * RNG.uniform(38, 44), 2), round(rev * RNG.uniform(30, 36), 2),
                             round(rev * (95 if bl == "Wealth Management" else 4) * RNG.uniform(0.95, 1.05), 2),
                             int(sh * {"Retail Banking": 5200, "Wealth Management": 420, "Commercial Banking": 610,
                                       "Corporate & Investment Banking": 45}[bl] * growth * RNG.uniform(0.85, 1.15)),
                             int(sh * {"Retail Banking": 3900, "Wealth Management": 260, "Commercial Banking": 430,
                                       "Corporate & Investment Banking": 30}[bl] * RNG.uniform(0.85, 1.15)),
                             round(min(80, 32 + i * 0.35 + RNG.gauss(0, 2.5) + (8 if bl == "Wealth Management" else 0)), 1)))
    db.executemany("INSERT INTO kpi_monthly VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    drows = []
    for i, m in enumerate(months):
        for ch, base in [("Mobile", 2.1e6), ("Online", 1.3e6), ("Branch", 0.62e6), ("Contact Center", 0.35e6)]:
            trend = (1.025 ** i) if ch == "Mobile" else (1.006 ** i) if ch == "Online" else (0.99 ** i)
            users = int(base * trend * RNG.uniform(0.97, 1.03))
            drows.append((m.isoformat()[:7], ch, users, int(users * RNG.uniform(9, 16)),
                          int(users * RNG.uniform(0.004, 0.012)), round(RNG.uniform(3, 11), 1)))
    db.executemany("INSERT INTO digital_engagement VALUES (?,?,?,?,?,?)", drows)
    db.commit()
    db.close()
    print(f"  warehouse: {len(rows)} KPI rows ({len(months)} months), {len(drows)} digital rows")


def write_catalog(root: Path) -> None:
    catalog = {
        "generated_at": TODAY.isoformat(),
        "sources": [
            {"id": "core_banking", "name": "Core Banking System", "type": "SQLite (OLTP)", "path": "core_banking/core_banking.db",
             "tables": ["customers", "accounts", "transactions", "loans"], "refresh": "Real-time CDC", "mcp_server": "core_banking"},
            {"id": "crm", "name": "CRM Platform", "type": "JSON export", "path": "crm/",
             "tables": ["crm_interactions.json", "nps_survey.json"], "refresh": "Nightly batch", "mcp_server": "core_banking"},
            {"id": "wealth", "name": "Wealth Platform", "type": "SQLite", "path": "wealth/wealth.db",
             "tables": ["portfolios", "holdings"], "refresh": "Hourly", "mcp_server": "wealth_markets"},
            {"id": "market_data", "name": "Market Data Feed", "type": "CSV + JSON feed", "path": "market_data/",
             "tables": ["daily_prices.csv", "securities.json"], "refresh": "End of day", "mcp_server": "wealth_markets"},
            {"id": "risk_compliance", "name": "Risk & Compliance Hub", "type": "SQLite + documents", "path": "risk_compliance/",
             "tables": ["aml_alerts", "credit_risk", "compliance_findings", "policies/*.md"], "refresh": "Intra-day", "mcp_server": "risk_compliance"},
            {"id": "warehouse", "name": "Enterprise Data Warehouse", "type": "SQLite (OLAP)", "path": "warehouse/enterprise_dw.db",
             "tables": ["kpi_monthly", "digital_engagement"], "refresh": "Monthly close", "mcp_server": "analytics"},
            {"id": "tokenops", "name": "TokenOps Control Plane Ledger", "type": "SQLite (control plane)", "path": "tokenops/control_plane.db",
             "tables": ["runs", "budgets", "policy_instances", "ledger_spent"], "refresh": "Real-time", "mcp_server": None},
        ],
    }
    (root / "catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")


def main() -> None:
    root = settings.data_dir
    root.mkdir(parents=True, exist_ok=True)
    (root / "tokenops").mkdir(parents=True, exist_ok=True)
    print(f"Generating mock data in {root}")
    customers, txns, loans = gen_core_banking(root)
    gen_crm(root, customers)
    gen_market_and_wealth(root, customers)
    gen_risk_compliance(root, customers, txns, loans)
    gen_warehouse(root)
    write_catalog(root)
    print("Done.")


def ensure_data() -> None:
    """Generate data on first run only."""
    if not (settings.data_dir / "catalog.json").exists():
        main()


if __name__ == "__main__":
    main()
