# Responsible AI Usage & Cost Governance Policy (FIN-AI-002)
- All generative AI agents must run under the TokenOps control plane with an enforced per-run budget.
- Default per-run LLM budget: USD 0.25; exceptions require CTO approval.
- Agents must downgrade to a lower-cost model when 80% of the run budget is consumed.
- Every run must be attributable to a user persona and department for chargeback.
- Customer PII must never be sent to models outside the approved Azure tenant; access uses Entra ID (RBAC), never shared keys.
- Runaway loops (more than 30 steps) must be halted automatically.
