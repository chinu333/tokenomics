# AML Transaction Monitoring Policy (FIN-AML-004)
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
